"""Agent 运行栈性能与长稳 harness（默认 dry-run，无外部副作用）。

用途：为「性能基准与 4 小时长稳」缺口提供第一版**可执行基线**，而不是达标证据：

- 可配置轮次（默认 100）的「切页 → 订阅 → 完成/失败/恢复」循环；
- 每轮采样 RSS、活跃任务 / 订阅 / 连接计数、受管临时文件数、进程句柄数；
- 输出 JSON 报告，并按「RSS 斜率预算」与「计数回落基线区间」给出判定。

判定口径（脚本自身即正本）：

- RSS 用最小二乘斜率（MB/分钟）与预算比较；运行时长不足 1 分钟时
  RSS 判定为 ``unknown``（短跑不能外推长稳结论），整体判定为 ``incomplete``；
- 计数以 warmup 轮的 settled 采样为基线窗口，末尾 tail 轮为回落窗口，
  要求回落窗口落在 ``[baseline_min - tolerance, baseline_max + tolerance]``；
- 采样后端不支持某项指标时值为 ``null`` 并判 ``unknown``，**不填 0**。

默认 backend 为纯内存合成实现：不连接数据库、不发网络请求、不启动 Flet。
真实 4 小时 / D10 规模（1000 会话 / 10 万消息量级）须在真实环境另行执行；
需要拉长驻留时间时可调大 ``--round-pause-s``（默认 0 仅做契约冒烟）。

运行（仓库根目录）：

    python scripts/probe/agent_soak.py
    python scripts/probe/agent_soak.py --rounds 200 --json-out soak.json

退出码：0 判定通过；1 判定失败；2 判定不完整（存在 unknown 指标）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

DEFAULT_ROUNDS = 100
DEFAULT_WARMUP_ROUNDS = 5
DEFAULT_TAIL_ROUNDS = 10
DEFAULT_RSS_BUDGET_MB_PER_MIN = 5.0
DEFAULT_COUNT_TOLERANCE = 0
DEFAULT_HANDLE_TOLERANCE = 8

# RSS 斜率在过短窗口内无统计意义（进程启动抖动会掩盖趋势），短于此值判 unknown。
MIN_RSS_WINDOW_MINUTES = 1.0

_SCHEMA_VERSION = 1
_OUTCOMES = ("complete", "fail", "recover")
_COUNT_METRICS = (
    "active_tasks",
    "active_subscriptions",
    "active_connections",
    "managed_temp_files",
)
_JUDGED_METRICS = (*_COUNT_METRICS, "open_handles")


@dataclass(frozen=True)
class ProcessMetrics:
    """单次进程级采样结果；``None`` 表示当前平台/后端无法采集。"""

    rss_mb: float | None
    open_handles: int | None


@dataclass(frozen=True)
class ResourceSample:
    """单轮单个阶段（peak / settled）的资源快照。"""

    round_index: int
    phase: str
    elapsed_s: float
    rss_mb: float | None
    active_tasks: int
    active_subscriptions: int
    active_connections: int
    managed_temp_files: int
    open_handles: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "phase": self.phase,
            "elapsed_s": round(self.elapsed_s, 6),
            "rss_mb": None if self.rss_mb is None else round(self.rss_mb, 3),
            "active_tasks": self.active_tasks,
            "active_subscriptions": self.active_subscriptions,
            "active_connections": self.active_connections,
            "managed_temp_files": self.managed_temp_files,
            "open_handles": self.open_handles,
        }


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须为整数: {value!r}")
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}: {value}")


def _require_finite_float(name: str, value: float, *, minimum: float, inclusive: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} 必须为数值: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须为有限数值: {value!r}")
    if value < minimum or (not inclusive and value == minimum):
        relation = ">=" if inclusive else ">"
        raise ValueError(f"{name} 必须 {relation} {minimum}: {value}")


@dataclass(frozen=True)
class SoakConfig:
    """长稳 harness 配置；非法值在构造时立即失败（不静默夹取）。"""

    rounds: int = DEFAULT_ROUNDS
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS
    tail_rounds: int = DEFAULT_TAIL_ROUNDS
    rss_budget_mb_per_min: float = DEFAULT_RSS_BUDGET_MB_PER_MIN
    count_tolerance: int = DEFAULT_COUNT_TOLERANCE
    handle_tolerance: int = DEFAULT_HANDLE_TOLERANCE
    round_pause_s: float = 0.0

    def __post_init__(self) -> None:
        _require_int("rounds", self.rounds, minimum=1)
        _require_int("warmup_rounds", self.warmup_rounds, minimum=0)
        _require_int("tail_rounds", self.tail_rounds, minimum=1)
        _require_int("count_tolerance", self.count_tolerance, minimum=0)
        _require_int("handle_tolerance", self.handle_tolerance, minimum=0)
        _require_finite_float(
            "rss_budget_mb_per_min",
            self.rss_budget_mb_per_min,
            minimum=0.0,
            inclusive=False,
        )
        _require_finite_float("round_pause_s", self.round_pause_s, minimum=0.0, inclusive=True)
        if self.warmup_rounds + self.tail_rounds > self.rounds:
            raise ValueError(
                "warmup_rounds + tail_rounds 不能超过 rounds: "
                f"{self.warmup_rounds} + {self.tail_rounds} > {self.rounds}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "warmup_rounds": self.warmup_rounds,
            "tail_rounds": self.tail_rounds,
            "rss_budget_mb_per_min": self.rss_budget_mb_per_min,
            "count_tolerance": self.count_tolerance,
            "handle_tolerance": self.handle_tolerance,
            "round_pause_s": self.round_pause_s,
        }


class ProcessSampler(Protocol):
    """进程级指标采样器（psutil 可选，缺失时回退到 stdlib）。"""

    @property
    def backend(self) -> str: ...

    def snapshot(self) -> ProcessMetrics: ...


class SoakBackend(Protocol):
    """长稳循环的后端接口；实现方负责保证每个 outcome 后计数回落。"""

    @property
    def name(self) -> str: ...

    @property
    def active_tasks(self) -> int: ...

    @property
    def active_subscriptions(self) -> int: ...

    @property
    def active_connections(self) -> int: ...

    @property
    def managed_temp_files(self) -> int: ...

    def switch_page(self) -> None: ...

    def subscribe(self) -> str: ...

    def complete(self) -> None: ...

    def fail(self) -> None: ...

    def recover(self) -> None: ...

    def close(self) -> None: ...


class SyntheticSoakBackend:
    """纯内存 + 受管临时目录的 dry-run 后端。

    每轮创建一条订阅与一个受管临时文件；随后按 complete / fail / recover
    之一结案并释放资源。不触达数据库、网络与 Flet。
    """

    def __init__(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="agent_soak_")
        self._root = Path(self._temp_dir.name)
        self._pending: dict[str, Path] = {}
        self._next_id = 0
        self._active_tasks = 0
        self._active_subscriptions = 0
        self._active_connections = 0
        self.page_switches = 0

    @property
    def name(self) -> str:
        return "synthetic"

    @property
    def active_tasks(self) -> int:
        return self._active_tasks

    @property
    def active_subscriptions(self) -> int:
        return self._active_subscriptions

    @property
    def active_connections(self) -> int:
        return self._active_connections

    @property
    def managed_temp_files(self) -> int:
        return len(self._pending)

    def switch_page(self) -> None:
        self.page_switches += 1

    def subscribe(self) -> str:
        self._next_id += 1
        token = f"sub-{self._next_id:04d}"
        path = self._root / f"{token}.json"
        path.write_text('{"state": "pending"}', encoding="utf-8")
        self._pending[token] = path
        self._active_tasks += 1
        self._active_subscriptions += 1
        self._active_connections += 1
        return token

    def _resolve_pending(self) -> None:
        if not self._pending:
            raise RuntimeError("没有待结案的订阅，循环状态已损坏")
        token = next(iter(self._pending))
        path = self._pending.pop(token)
        path.unlink(missing_ok=True)
        self._active_tasks -= 1
        self._active_subscriptions -= 1
        self._active_connections -= 1

    def complete(self) -> None:
        self._resolve_pending()

    def fail(self) -> None:
        self._resolve_pending()

    def recover(self) -> None:
        self._resolve_pending()

    def close(self) -> None:
        for path in self._pending.values():
            path.unlink(missing_ok=True)
        self._pending.clear()
        self._active_tasks = 0
        self._active_subscriptions = 0
        self._active_connections = 0
        self._temp_dir.cleanup()


def _psutil_reader() -> Callable[[], tuple[float | None, int | None]]:
    """psutil 采样（可选依赖）；句柄数按平台取 num_handles / num_fds。"""
    import psutil  # pyright: ignore[reportMissingModuleSource]  # 可选依赖：未安装时回退 stdlib 采样

    process = psutil.Process()

    def read() -> tuple[float | None, int | None]:
        with process.oneshot():
            rss_mb = process.memory_info().rss / (1024 * 1024)
            num_handles = getattr(process, "num_handles", None)
            if callable(num_handles):
                handles: int | None = int(cast(Callable[[], int], num_handles)())
            else:
                num_fds = getattr(process, "num_fds", None)
                handles = int(cast(Callable[[], int], num_fds)()) if callable(num_fds) else None
        return rss_mb, handles

    return read


def _windows_reader() -> Callable[[], tuple[float | None, int | None]]:
    """Windows stdlib 回退：GetProcessMemoryInfo + GetProcessHandleCount。"""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    get_memory_info = kernel32.K32GetProcessMemoryInfo
    get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    get_memory_info.restype = wintypes.BOOL
    get_handle_count = kernel32.GetProcessHandleCount
    get_handle_count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_handle_count.restype = wintypes.BOOL
    current_process = get_current_process()

    def read() -> tuple[float | None, int | None]:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        if not get_memory_info(current_process, ctypes.byref(counters), counters.cb):
            return None, None
        handle_count = wintypes.DWORD(0)
        handles_ok = get_handle_count(current_process, ctypes.byref(handle_count))
        rss_mb = counters.WorkingSetSize / (1024 * 1024)
        return rss_mb, int(handle_count.value) if handles_ok else None

    return read


def _procfs_reader() -> Callable[[], tuple[float | None, int | None]]:
    """Linux /proc 回退：/proc/self/statm 的 RSS 与 /proc/self/fd 的句柄数。"""
    from os import sysconf  # pyright: ignore[reportAttributeAccessIssue]  # 仅 Linux 分支调用，Windows 无此属性

    page_size = float(sysconf("SC_PAGE_SIZE"))

    def read() -> tuple[float | None, int | None]:
        rss_mb: float | None
        try:
            statm = Path("/proc/self/statm").read_text(encoding="ascii").split()
            rss_mb = int(statm[1]) * page_size / (1024 * 1024)
        except (OSError, IndexError, ValueError):
            rss_mb = None
        try:
            handles: int | None = len(list(Path("/proc/self/fd").iterdir()))
        except OSError:
            handles = None
        return rss_mb, handles

    return read


def _unavailable_reader() -> tuple[float | None, int | None]:
    return None, None


def _resolve_process_reader() -> tuple[str, Callable[[], tuple[float | None, int | None]]]:
    try:
        return "psutil", _psutil_reader()
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            return "windows_ctypes", _windows_reader()
        except (OSError, AttributeError):
            pass
    elif sys.platform.startswith("linux"):
        try:
            return "procfs", _procfs_reader()
        except (OSError, ValueError):
            pass
    return "unavailable", _unavailable_reader


class SystemProcessSampler:
    """系统进程采样器：psutil 优先，缺失时回退 stdlib（backend 名称写入报告）。"""

    def __init__(self) -> None:
        self._backend, self._read = _resolve_process_reader()

    @property
    def backend(self) -> str:
        return self._backend

    def snapshot(self) -> ProcessMetrics:
        rss_mb, handles = self._read()
        return ProcessMetrics(rss_mb=rss_mb, open_handles=handles)


def percentile_ceil(samples: Sequence[float], q: float) -> float:
    """ceil-rank 分位数：返回排序后第 ``ceil(q * n)`` 个值（1 基）。

    该口径与验收判据一致（P95 取排序后 ``ceil(0.95 * n)``）。空样本或非法 q
    抛 ``ValueError``，供调用方显式判失败而不是返回哨兵值。
    """
    if not samples:
        raise ValueError("samples 不能为空")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q 必须在 [0, 1] 内: {q}")
    ordered = sorted(samples)
    rank = math.ceil(q * len(ordered))
    index = min(max(rank, 1), len(ordered)) - 1
    return ordered[index]


def linear_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """最小二乘斜率；少于 2 个样本或 x 无变化时抛 ``ValueError``。"""
    if len(xs) != len(ys):
        raise ValueError(f"xs 与 ys 长度不一致: {len(xs)} != {len(ys)}")
    if len(xs) < 2:
        raise ValueError("线性斜率至少需要 2 个样本")
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        raise ValueError("xs 不能全部相同")
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return numerator / denominator


def _settled_samples(samples: Sequence[ResourceSample]) -> list[ResourceSample]:
    return [sample for sample in samples if sample.phase == "settled"]


def judge_rss_growth(samples: Sequence[ResourceSample], budget_mb_per_min: float) -> dict[str, Any]:
    """按 RSS 斜率判定是否存在单调无界增长；窗口过短或数据缺失判 unknown。"""
    usable = [sample for sample in samples if sample.rss_mb is not None]
    result: dict[str, Any] = {
        "check": "rss_slope",
        "budget_mb_per_min": budget_mb_per_min,
        "samples": len(usable),
    }
    if len(usable) < 2:
        result.update(status="unknown", reason="RSS 样本不足 2 个（采样后端不支持或运行过短）")
        return result

    elapsed_minutes = usable[-1].elapsed_s / 60.0
    result["elapsed_minutes"] = round(elapsed_minutes, 6)
    if elapsed_minutes < MIN_RSS_WINDOW_MINUTES:
        result.update(
            status="unknown",
            reason=f"运行时长 {elapsed_minutes:.4f} 分钟 < {MIN_RSS_WINDOW_MINUTES} 分钟，斜率不可判定",
        )
        return result

    xs = [sample.elapsed_s / 60.0 for sample in usable]
    ys = [cast(float, sample.rss_mb) for sample in usable]
    slope = linear_slope(xs, ys)
    projected_growth_mb = slope * elapsed_minutes
    passed = slope <= budget_mb_per_min
    result.update(
        slope_mb_per_min=round(slope, 6),
        projected_growth_mb=round(projected_growth_mb, 3),
        status="pass" if passed else "fail",
    )
    if not passed:
        result["reason"] = f"RSS 斜率 {slope:.6f} MB/分钟 > 预算 {budget_mb_per_min} MB/分钟"
    return result


def judge_count_recovery(
    samples: Sequence[ResourceSample],
    metric: str,
    *,
    warmup_rounds: int,
    tail_rounds: int,
    tolerance: int,
) -> dict[str, Any]:
    """判定计数是否回落到基线区间。

    基线窗口为前 ``max(warmup_rounds, 1)`` 个 settled 采样，回落窗口为末
    ``tail_rounds`` 个 settled 采样；两端任一出现 ``None`` 判 unknown。
    """
    if metric not in _JUDGED_METRICS:
        raise ValueError(f"未知的计数指标: {metric!r}")
    _require_int("warmup_rounds", warmup_rounds, minimum=0)
    _require_int("tail_rounds", tail_rounds, minimum=1)
    _require_int("tolerance", tolerance, minimum=0)

    settled = _settled_samples(samples)
    result: dict[str, Any] = {
        "check": metric,
        "tolerance": tolerance,
        "settled_samples": len(settled),
    }
    if len(settled) < max(warmup_rounds, 1) + tail_rounds:
        result.update(status="unknown", reason="settled 采样不足以同时构成基线窗口与回落窗口")
        return result

    baseline_window = settled[: max(warmup_rounds, 1)]
    tail_window = settled[-tail_rounds:]
    baseline_values = [getattr(sample, metric) for sample in baseline_window]
    tail_values = [getattr(sample, metric) for sample in tail_window]
    if any(value is None for value in baseline_values) or any(value is None for value in tail_values):
        result.update(status="unknown", reason=f"{metric} 采样存在 null（平台不支持），不按 0 处理")
        return result

    numeric_baseline = [int(value) for value in baseline_values]
    numeric_tail = [int(value) for value in tail_values]
    baseline_min = min(numeric_baseline)
    baseline_max = max(numeric_baseline)
    tail_min = min(numeric_tail)
    tail_max = max(numeric_tail)
    passed = tail_min >= baseline_min - tolerance and tail_max <= baseline_max + tolerance
    result.update(
        baseline_min=baseline_min,
        baseline_max=baseline_max,
        tail_min=tail_min,
        tail_max=tail_max,
        max_rise=tail_max - baseline_max,
        status="pass" if passed else "fail",
    )
    if not passed:
        result["reason"] = (
            f"{metric} 未回落基线区间: tail=[{tail_min}, {tail_max}], "
            f"baseline=[{baseline_min}, {baseline_max}], tolerance={tolerance}"
        )
    return result


def _make_sample(
    round_index: int,
    phase: str,
    elapsed_s: float,
    metrics: ProcessMetrics,
    backend: SoakBackend,
) -> ResourceSample:
    return ResourceSample(
        round_index=round_index,
        phase=phase,
        elapsed_s=elapsed_s,
        rss_mb=metrics.rss_mb,
        active_tasks=backend.active_tasks,
        active_subscriptions=backend.active_subscriptions,
        active_connections=backend.active_connections,
        managed_temp_files=backend.managed_temp_files,
        open_handles=metrics.open_handles,
    )


def _aggregate_verdict(checks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "fail"]
    unknown = [check for check in checks if check["status"] == "unknown"]
    verdict = "fail" if failed else ("incomplete" if unknown else "pass")
    reasons = [f"{check['check']}: {check.get('reason', '未回落 / 超出预算')}" for check in [*failed, *unknown]]
    return {"verdict": verdict, "reasons": reasons}


def run_soak(
    config: SoakConfig,
    *,
    backend: SoakBackend | None = None,
    sampler: ProcessSampler | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """执行长稳循环并返回可直接 JSON 序列化的报告。"""
    active_backend = backend if backend is not None else SyntheticSoakBackend()
    active_sampler = sampler if sampler is not None else SystemProcessSampler()
    now = clock if clock is not None else time.perf_counter

    started = now()
    samples: list[ResourceSample] = []
    outcomes: dict[str, int] = {outcome: 0 for outcome in _OUTCOMES}
    try:
        for round_index in range(1, config.rounds + 1):
            active_backend.switch_page()
            active_backend.subscribe()
            samples.append(
                _make_sample(round_index, "peak", now() - started, active_sampler.snapshot(), active_backend)
            )

            outcome = _OUTCOMES[(round_index - 1) % len(_OUTCOMES)]
            if outcome == "complete":
                active_backend.complete()
            elif outcome == "fail":
                active_backend.fail()
            else:
                active_backend.recover()
            outcomes[outcome] += 1

            samples.append(
                _make_sample(round_index, "settled", now() - started, active_sampler.snapshot(), active_backend)
            )
            if config.round_pause_s > 0:
                time.sleep(config.round_pause_s)
    finally:
        active_backend.close()

    elapsed_s = now() - started
    rss_check = judge_rss_growth(samples, config.rss_budget_mb_per_min)
    count_checks = {
        metric: judge_count_recovery(
            samples,
            metric,
            warmup_rounds=config.warmup_rounds,
            tail_rounds=config.tail_rounds,
            tolerance=config.count_tolerance,
        )
        for metric in _COUNT_METRICS
    }
    handle_check = judge_count_recovery(
        samples,
        "open_handles",
        warmup_rounds=config.warmup_rounds,
        tail_rounds=config.tail_rounds,
        tolerance=config.handle_tolerance,
    )
    verdict = _aggregate_verdict([rss_check, *count_checks.values(), handle_check])

    return {
        "schema_version": _SCHEMA_VERSION,
        "harness": "agent_soak",
        "mode": "dry_run",
        "scope": {
            "kind": "synthetic_in_process",
            "database": "none",
            "network": "none",
            "flet_rendering": "none",
            "production_soak": "not_run",
            "d10_real_scale": "not_run",
            "description": "合成后端 + 进程采样；真实 4 小时 / D10 规模须在真实环境另行执行",
        },
        "backend": active_backend.name,
        "sampler_backend": active_sampler.backend,
        "config": config.to_dict(),
        "outcomes": outcomes,
        "elapsed_s": round(elapsed_s, 6),
        "samples": [sample.to_dict() for sample in samples],
        "judgement": {
            "rss": rss_check,
            "counts": count_checks,
            "handles": handle_check,
            **verdict,
        },
        "not_run": [
            {
                "item": "four_hour_production_soak",
                "reason": "dry-run 只验证 harness 契约；4 小时真实驻留与泄漏结论未在本脚本执行",
            },
            {
                "item": "d10_real_data_scale",
                "reason": "无 1000 会话 / 10 万消息量级真实数据，合成规模不能外推生产结论",
            },
            {
                "item": "real_flet_ui_rendering",
                "reason": "未启动 Flet 渲染进程，切页耗时仅为合成后端计数",
            },
        ],
    }


def _print_human(report: dict[str, Any]) -> None:
    config = report["config"]
    judgement = report["judgement"]
    print("== Agent 长稳 harness（dry-run，非生产结论） ==")
    print(
        f"rounds={config['rounds']} warmup={config['warmup_rounds']} tail={config['tail_rounds']} "
        f"backend={report['backend']} sampler={report['sampler_backend']} elapsed={report['elapsed_s']:.3f}s"
    )
    print(f"outcomes={report['outcomes']}")
    rss = judgement["rss"]
    print(f"RSS: status={rss['status']} {rss.get('reason', '')}".rstrip())
    for metric, check in judgement["counts"].items():
        print(
            f"{metric}: status={check['status']} "
            f"baseline=[{check.get('baseline_min')}, {check.get('baseline_max')}] "
            f"tail=[{check.get('tail_min')}, {check.get('tail_max')}]"
        )
    handles = judgement["handles"]
    print(f"open_handles: status={handles['status']} {handles.get('reason', '')}".rstrip())
    print(f"verdict={judgement['verdict']}")
    for reason in judgement["reasons"]:
        print(f"- {reason}")
    print("not_run:")
    for item in report["not_run"]:
        print(f"- {item['item']}: {item['reason']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent 长稳 harness（默认 dry-run）")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="循环轮次（默认 100）")
    parser.add_argument("--warmup-rounds", type=int, default=DEFAULT_WARMUP_ROUNDS, help="基线窗口轮次")
    parser.add_argument("--tail-rounds", type=int, default=DEFAULT_TAIL_ROUNDS, help="回落窗口轮次")
    parser.add_argument(
        "--rss-budget-mb-per-min",
        type=float,
        default=DEFAULT_RSS_BUDGET_MB_PER_MIN,
        help="RSS 斜率预算（MB/分钟）",
    )
    parser.add_argument("--count-tolerance", type=int, default=DEFAULT_COUNT_TOLERANCE, help="计数容差")
    parser.add_argument("--handle-tolerance", type=int, default=DEFAULT_HANDLE_TOLERANCE, help="句柄容差")
    parser.add_argument("--round-pause-s", type=float, default=0.0, help="每轮休眠秒数（真实长稳可调大）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    parser.add_argument("--json-out", default=None, help="将 JSON 报告写入指定文件")
    args = parser.parse_args(argv)

    try:
        config = SoakConfig(
            rounds=args.rounds,
            warmup_rounds=args.warmup_rounds,
            tail_rounds=args.tail_rounds,
            rss_budget_mb_per_min=args.rss_budget_mb_per_min,
            count_tolerance=args.count_tolerance,
            handle_tolerance=args.handle_tolerance,
            round_pause_s=args.round_pause_s,
        )
    except ValueError as exc:
        parser.error(str(exc))

    report = run_soak(config)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return {"pass": 0, "fail": 1, "incomplete": 2}[report["judgement"]["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
