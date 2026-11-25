"""Agent 性能基准 harness：P50/P95 采样工具与三条测量入口。

**本文件是 harness 基线，不是达标证据**：

- 无 D10 真实数据（1000 会话 / 10 万消息量级），使用可控合成规模；
- 不依赖真实 Flet 渲染、真实 PostgreSQL 或网络；
- 统计口径 P95 = 排序后 ``ceil(0.95 * n)``（与验收判据一致）；
- 冷/热各要求 >= 100 个有效样本，样本不足显式抛出 ``InsufficientSamplesError``；
- 真实环境 P95 验收（首屏/搜索 <= 2s、保存到 UI <= 1s）须另行执行，本文件不声称达标。

测量入口：首屏、关键词搜索、保存→UI 呈现时差。后端可注入时钟（测试用
``FakeClock``，真实运行可换成 ``time.perf_counter``）与合成规模仓储。
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import pytest

from scripts.probe.agent_soak import percentile_ceil

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

MIN_SAMPLES = 100
SYNTHETIC_SESSION_COUNT = 1000
FIRST_SCREEN_ROWS = 50
COLD_BACKEND_CONSTRUCTION_MS = 150.0

# 验收判据参考值：首屏/搜索 P95 <= 2s，保存→UI P95 <= 1s。
# 仅用于报告对照，不构成本 harness 的达标声明。
REFERENCE_P95_MS: dict[str, float] = {
    "first_screen": 2000.0,
    "keyword_search": 2000.0,
    "save_to_presentation": 1000.0,
}


class InsufficientSamplesError(ValueError):
    """有效样本少于门槛；显式失败而不是静默降级。"""


@dataclass(frozen=True)
class LatencyBudget:
    """单条测量入口的参考 P95 预算（非法值在构造时失败）。"""

    entry: str
    reference_p95_ms: float

    def __post_init__(self) -> None:
        if isinstance(self.reference_p95_ms, bool) or not isinstance(self.reference_p95_ms, int | float):
            raise ValueError(f"{self.entry}: reference_p95_ms 必须为正有限数值: {self.reference_p95_ms!r}")
        if not math.isfinite(self.reference_p95_ms) or self.reference_p95_ms <= 0:
            raise ValueError(f"{self.entry}: reference_p95_ms 必须为正有限数值: {self.reference_p95_ms!r}")


class FakeClock:
    """可注入的单调时钟：测试通过 ``advance_ms`` 精确控制耗时。"""

    def __init__(self) -> None:
        self._now_s = 0.0

    def __call__(self) -> float:
        return self._now_s

    def advance_ms(self, milliseconds: float) -> None:
        if isinstance(milliseconds, bool) or not isinstance(milliseconds, int | float):
            raise ValueError(f"advance_ms 必须为数值: {milliseconds!r}")
        if not math.isfinite(milliseconds) or milliseconds < 0:
            raise ValueError(f"advance_ms 必须为非负有限值: {milliseconds!r}")
        self._now_s += milliseconds / 1000.0


@dataclass(frozen=True)
class SampleFailure:
    """单次采样失败记录（失败样本单列，不混入分位数）。"""

    attempt_index: int
    error_type: str
    message: str


@dataclass(frozen=True)
class LatencySeries:
    """一条测量入口的采样结果：成功样本 + 失败样本清单。"""

    label: str
    attempts: int
    samples_ms: list[float]
    failures: list[SampleFailure]


@dataclass
class FirstScreenBackend:
    """合成首屏后端：构造会话列表，``load_first_screen`` 返回可见行数。"""

    clock: FakeClock
    load_costs_ms: Sequence[float]
    cold: bool = False
    _rows: list[dict[str, int]] = field(init=False)
    _costs: Iterator[float] = field(init=False)

    def __post_init__(self) -> None:
        if not self.load_costs_ms:
            raise ValueError("load_costs_ms 不能为空")
        self._rows = [{"session_id": index} for index in range(SYNTHETIC_SESSION_COUNT)]
        self._costs = itertools.cycle(self.load_costs_ms)
        if self.cold:
            self.clock.advance_ms(COLD_BACKEND_CONSTRUCTION_MS)

    def load_first_screen(self) -> int:
        self.clock.advance_ms(next(self._costs))
        return min(len(self._rows), FIRST_SCREEN_ROWS)


@dataclass
class KeywordSearchBackend:
    """合成搜索后端：在固定规模标题集合上做关键词匹配。"""

    clock: FakeClock
    search_costs_ms: Sequence[float]
    cold: bool = False
    _titles: list[str] = field(init=False)
    _costs: Iterator[float] = field(init=False)

    def __post_init__(self) -> None:
        if not self.search_costs_ms:
            raise ValueError("search_costs_ms 不能为空")
        self._titles = [f"会话{index:04d} 关键词 研究记录" for index in range(SYNTHETIC_SESSION_COUNT)]
        self._costs = itertools.cycle(self.search_costs_ms)
        if self.cold:
            self.clock.advance_ms(COLD_BACKEND_CONSTRUCTION_MS)

    def search(self, keyword: str = "关键词") -> int:
        self.clock.advance_ms(next(self._costs))
        return sum(1 for title in self._titles if keyword in title)


@dataclass
class SaveToPresentationBackend:
    """合成保存→呈现后端：返回从点击保存到 UI 呈现的毫秒时差。"""

    clock: FakeClock
    save_costs_ms: Sequence[float]
    present_costs_ms: Sequence[float]
    _save_costs: Iterator[float] = field(init=False)
    _present_costs: Iterator[float] = field(init=False)

    def __post_init__(self) -> None:
        if not self.save_costs_ms:
            raise ValueError("save_costs_ms 不能为空")
        if not self.present_costs_ms:
            raise ValueError("present_costs_ms 不能为空")
        self._save_costs = itertools.cycle(self.save_costs_ms)
        self._present_costs = itertools.cycle(self.present_costs_ms)

    def save_and_present(self) -> float:
        started = self.clock()
        self.clock.advance_ms(next(self._save_costs))
        self.clock.advance_ms(next(self._present_costs))
        return (self.clock() - started) * 1000.0


def require_min_samples(samples: Sequence[float], *, label: str, minimum: int = MIN_SAMPLES) -> None:
    """有效样本少于 ``minimum`` 时显式失败，并说明实际数量。"""
    if len(samples) < minimum:
        raise InsufficientSamplesError(f"{label}: 有效样本不足，要求 >= {minimum}，实际 {len(samples)}；不静默降级")


def collect_latency(
    operation: Callable[[], Any],
    attempts: int,
    clock: Callable[[], float],
    *,
    label: str,
) -> LatencySeries:
    """执行 ``attempts`` 次采样；失败样本单列，不进入分位数计算。"""
    if attempts < 1:
        raise ValueError(f"attempts 必须 >= 1: {attempts}")
    samples_ms: list[float] = []
    failures: list[SampleFailure] = []
    for attempt_index in range(attempts):
        started = clock()
        try:
            operation()
        except Exception as exc:
            failures.append(SampleFailure(attempt_index=attempt_index, error_type=type(exc).__name__, message=str(exc)))
            continue
        samples_ms.append((clock() - started) * 1000.0)
    return LatencySeries(label=label, attempts=attempts, samples_ms=samples_ms, failures=failures)


def summarize(series: LatencySeries, budget: LatencyBudget) -> dict[str, Any]:
    """把一条测量入口的样本折算为 P50/P95/max 与失败清单。"""
    require_min_samples(series.samples_ms, label=series.label)
    ordered = sorted(series.samples_ms)
    p95_ms = percentile_ceil(ordered, 0.95)
    return {
        "label": series.label,
        "attempts": series.attempts,
        "samples": len(ordered),
        "failures": [asdict(failure) for failure in series.failures],
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(percentile_ceil(ordered, 0.50), 3),
        "p95_ms": round(p95_ms, 3),
        "max_ms": round(ordered[-1], 3),
        "reference_p95_ms": budget.reference_p95_ms,
        "meets_reference_target": p95_ms <= budget.reference_p95_ms,
    }


def build_entry_report(
    entry: str,
    cold: LatencySeries,
    hot: LatencySeries,
    budget: LatencyBudget,
) -> dict[str, Any]:
    """组装单条测量入口的 cold/hot 报告，并显式标注非达标证据。"""
    if entry not in REFERENCE_P95_MS:
        raise ValueError(f"未知的测量入口: {entry!r}")
    return {
        "entry": entry,
        "cold": summarize(cold, budget),
        "hot": summarize(hot, budget),
        "evidence_kind": "harness_baseline",
        "d10_real_data": False,
        "claims_target": False,
        "synthetic_scale": {"session_count": SYNTHETIC_SESSION_COUNT},
        "not_run": [
            {
                "item": "d10_real_data_p95",
                "reason": "无 1000 会话 / 10 万消息量级真实数据，合成规模不能外推生产 P95",
            },
            {
                "item": "real_flet_rendering",
                "reason": "未启动真实 Flet 渲染，首屏与保存→呈现时差为可注入时钟的合成测量",
            },
        ],
    }


def _costs(base_ms: float, spread_ms: float, count: int = MIN_SAMPLES) -> list[float]:
    """构造确定性的耗时序列，便于对 P50/P95 做精确断言。"""
    return [base_ms + (index % 10) * spread_ms for index in range(count)]


def _collect_first_screen(*, cold: bool) -> tuple[LatencySeries, list[float]]:
    clock = FakeClock()
    costs = _costs(120.0 if cold else 40.0, 1.0 if cold else 0.5)
    label = "first_screen/cold" if cold else "first_screen/hot"
    if cold:
        cold_costs = iter(costs)

        def operation() -> int:
            backend = FirstScreenBackend(clock=clock, load_costs_ms=[next(cold_costs)], cold=True)
            return backend.load_first_screen()

        expected = [COLD_BACKEND_CONSTRUCTION_MS + cost for cost in costs]
    else:
        backend = FirstScreenBackend(clock=clock, load_costs_ms=costs, cold=False)

        def operation() -> int:
            return backend.load_first_screen()

        expected = list(costs)
    return collect_latency(operation, MIN_SAMPLES, clock, label=label), expected


def _collect_keyword_search(*, cold: bool) -> tuple[LatencySeries, list[float]]:
    clock = FakeClock()
    costs = _costs(90.0 if cold else 30.0, 0.5)
    label = "keyword_search/cold" if cold else "keyword_search/hot"
    if cold:
        cold_costs = iter(costs)

        def operation() -> int:
            backend = KeywordSearchBackend(clock=clock, search_costs_ms=[next(cold_costs)], cold=True)
            return backend.search()

        expected = [COLD_BACKEND_CONSTRUCTION_MS + cost for cost in costs]
    else:
        backend = KeywordSearchBackend(clock=clock, search_costs_ms=costs, cold=False)

        def operation() -> int:
            return backend.search()

        expected = list(costs)
    return collect_latency(operation, MIN_SAMPLES, clock, label=label), expected


def _collect_save_to_presentation(*, cold: bool) -> tuple[LatencySeries, list[float]]:
    clock = FakeClock()
    save_costs = _costs(18.0 if cold else 8.0, 0.5)
    present_costs = _costs(26.0 if cold else 12.0, 0.5)
    backend = SaveToPresentationBackend(
        clock=clock,
        save_costs_ms=save_costs,
        present_costs_ms=present_costs,
    )
    label = "save_to_presentation/cold" if cold else "save_to_presentation/hot"
    return collect_latency(backend.save_and_present, MIN_SAMPLES, clock, label=label), [
        save + present for save, present in zip(save_costs, present_costs, strict=True)
    ]


def _assert_entry_report(report: dict[str, Any], cold_expected: Sequence[float], hot_expected: Sequence[float]) -> None:
    """断言 cold/hot 样本量、ceil-rank P50/P95 与 harness 标注。"""
    assert report["cold"]["samples"] == MIN_SAMPLES
    assert report["hot"]["samples"] == MIN_SAMPLES
    assert report["cold"]["p50_ms"] == round(percentile_ceil(cold_expected, 0.50), 3)
    assert report["cold"]["p95_ms"] == round(percentile_ceil(cold_expected, 0.95), 3)
    assert report["hot"]["p95_ms"] == round(percentile_ceil(hot_expected, 0.95), 3)
    assert report["cold"]["failures"] == []
    assert report["hot"]["failures"] == []
    assert report["evidence_kind"] == "harness_baseline"
    assert report["d10_real_data"] is False
    assert report["claims_target"] is False
    assert report["cold"]["meets_reference_target"] is True


def test_first_screen_cold_hot_samples_and_percentile() -> None:
    cold, cold_expected = _collect_first_screen(cold=True)
    hot, hot_expected = _collect_first_screen(cold=False)
    report = build_entry_report(
        "first_screen", cold, hot, LatencyBudget("first_screen", REFERENCE_P95_MS["first_screen"])
    )
    _assert_entry_report(report, cold_expected, hot_expected)


def test_keyword_search_cold_hot_samples_and_percentile() -> None:
    cold, cold_expected = _collect_keyword_search(cold=True)
    hot, hot_expected = _collect_keyword_search(cold=False)
    report = build_entry_report(
        "keyword_search", cold, hot, LatencyBudget("keyword_search", REFERENCE_P95_MS["keyword_search"])
    )
    _assert_entry_report(report, cold_expected, hot_expected)

    backend = KeywordSearchBackend(clock=FakeClock(), search_costs_ms=[1.0], cold=False)
    assert backend.search() == SYNTHETIC_SESSION_COUNT


def test_save_to_presentation_cold_hot_samples_and_percentile() -> None:
    cold, cold_expected = _collect_save_to_presentation(cold=True)
    hot, hot_expected = _collect_save_to_presentation(cold=False)
    report = build_entry_report(
        "save_to_presentation",
        cold,
        hot,
        LatencyBudget("save_to_presentation", REFERENCE_P95_MS["save_to_presentation"]),
    )
    _assert_entry_report(report, cold_expected, hot_expected)


def test_insufficient_samples_fail_explicitly() -> None:
    clock = FakeClock()
    operation = _raising_free_operation(clock, cost_ms=1.0)
    series = collect_latency(operation, MIN_SAMPLES - 1, clock, label="first_screen/cold")

    assert series.attempts == MIN_SAMPLES - 1
    assert len(series.samples_ms) == MIN_SAMPLES - 1
    with pytest.raises(InsufficientSamplesError, match="要求 >= 100"):
        summarize(series, LatencyBudget("first_screen", REFERENCE_P95_MS["first_screen"]))


def test_failed_samples_are_listed_separately() -> None:
    clock = FakeClock()

    def operation() -> None:
        clock.advance_ms(10.0)
        raise RuntimeError("synthetic render failure")

    series = collect_latency(operation, MIN_SAMPLES, clock, label="first_screen/cold")

    assert series.attempts == MIN_SAMPLES
    assert series.samples_ms == []
    assert len(series.failures) == MIN_SAMPLES
    assert series.failures[0].attempt_index == 0
    assert series.failures[0].error_type == "RuntimeError"
    assert series.failures[0].message == "synthetic render failure"
    with pytest.raises(InsufficientSamplesError, match="实际 0"):
        require_min_samples(series.samples_ms, label=series.label)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"), True])
def test_invalid_reference_budget_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="reference_p95_ms 必须为正有限数值"):
        LatencyBudget("first_screen", value)


def _raising_free_operation(clock: FakeClock, *, cost_ms: float) -> Callable[[], None]:
    """构造按固定耗时推进时钟、永不失败的采样操作。"""

    def operation() -> None:
        clock.advance_ms(cost_ms)

    return operation
