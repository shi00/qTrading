"""新闻匹配/去重纯逻辑合成性能基线（T02 / D12 可重复测量）。

用途：为 ``data/news_match.py`` 的 ``dedupe_documents`` 与 ``match_news_to_stock``
提供**脱机、可重复、带固定规模阶梯与采样次数**的 CPU 测量，输出规模 / 样本 /
p50 / p95 / max / 吞吐的结构化结果。测量只使用合成数据，不连接数据库、行情或网络。

口径（脚本自身即正本，勿把合成值当生产 SLA）：
- 规模阶梯默认 N ∈ {100, 1000, 5000}，每个工作负载每规模固定采样 7 次、预热 1 次；
- 采样前 ``gc.collect()``，测量期间 GC 保持开启（贴近正常运行态）；
- p50/p95 用线性插值；样本量 7 时 p95 接近最大值，尾部不可外推；
- 与 ``PerfThreshold.MEMORY_COMPUTE``（50ms，通用单操作内存计算红线）仅做
  **参考对照**，不构成门禁，也不代表生产数据库/行情规模 SLA；
- 脚本不接入 CI，超阈值不失败（退出码只反映脚本本身是否成功执行）。

运行（仓库根目录）：
    python scripts/probe/news_match_perf.py
    python scripts/probe/news_match_perf.py --json --scales 100,1000,5000 --samples 7
"""

from __future__ import annotations

import argparse
import datetime
import gc
import math
import platform
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.news_match import dedupe_documents, match_news_to_stock  # noqa: E402
from utils.log_decorators import PerfThreshold  # noqa: E402

DEFAULT_SCALES: tuple[int, ...] = (100, 1000, 5000)
DEFAULT_SAMPLES = 7
DEFAULT_WARMUP = 1
DEFAULT_CANDIDATE_POOL = 100

_SCHEMA_VERSION = 1
_THRESHOLD_MS = float(PerfThreshold.MEMORY_COMPUTE)
_NOISE_CV_WARNING = 0.15

_NOT_RUN: list[dict[str, str]] = [
    {
        "item": "production_database_scale_sla",
        "reason": "沙箱无真实 PostgreSQL / 行情库，未测真实数据规模下的端到端 SLA",
    },
    {
        "item": "real_news_corpus_representativeness",
        "reason": "合成文本未按真实新闻语料分布采样，绝对耗时不可外推到生产语料",
    },
    {
        "item": "memory_leak",
        "reason": "未做长时驻留 / 多轮内存增长测量，不能据此确认或排除内存泄漏",
    },
]


def percentile(samples_ms: Sequence[float], q: float) -> float:
    """线性插值分位数（q ∈ [0, 1]），空样本或非法 q 抛 ``ValueError``。"""
    if not samples_ms:
        raise ValueError("samples_ms 不能为空")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q 必须在 [0, 1] 内: {q}")
    ordered = sorted(samples_ms)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _make_documents(n: int) -> list[dict[str, Any]]:
    """生成 n 条互不重复的同源文档（最坏路径：全部保留、逐条全表比较）。"""
    base = datetime.datetime(2026, 9, 1, 10, 0, 0)
    return [
        {
            "source_kind": "news",
            "title": f"第{i:06d}条公司动态 行业观察 市场表现",
            "url": f"https://example.com/news/{i}",
            "content_hash": f"{i:016x}",
            "publish_time": base + datetime.timedelta(minutes=i),
        }
        for i in range(n)
    ]


def _make_candidates(pool: int) -> list[dict[str, str]]:
    """固定候选股池（默认 100 只），仅含 ``ts_code`` / ``name`` 两个匹配字段。"""
    return [{"ts_code": f"{600000 + i:06d}.SH", "name": f"测试公司{i:03d}"} for i in range(pool)]


def _make_news_texts(n: int, pool: int) -> list[str]:
    """生成 n 条含完整代码与唯一公司名的合成快讯（每轮均触发匹配循环）。"""
    return [f"【{i}】测试公司{i % pool:03d}发布公告，代码{600000 + i % pool:06d}.SH，第{i}条业务进展" for i in range(n)]


def _measure_ms(func: Callable[[], Any], *, warmup: int, samples: int) -> list[float]:
    """执行预热 + 固定次数计时采样，返回每次调用的毫秒耗时。"""
    if samples < 1:
        raise ValueError(f"samples 必须 >= 1: {samples}")
    if warmup < 0:
        raise ValueError(f"warmup 必须 >= 0: {warmup}")
    for _ in range(warmup):
        func()
    timings: list[float] = []
    for _ in range(samples):
        gc.collect()  # 排除采样前的残留循环垃圾，测量期间 GC 保持开启
        started = time.perf_counter()
        func()
        timings.append((time.perf_counter() - started) * 1000.0)
    return timings


def _make_result(n: int, timings_ms: list[float]) -> dict[str, Any]:
    """把样本序列折算为规模 / 样本 / p50 / p95 / max / 吞吐记录。"""
    mean_ms = statistics.fmean(timings_ms)
    stdev_ms = statistics.pstdev(timings_ms)
    p50_ms = percentile(timings_ms, 0.50)
    p95_ms = percentile(timings_ms, 0.95)
    throughput = n / (p50_ms / 1000.0) if p50_ms > 0 else float("inf")
    return {
        "n": n,
        "samples": len(timings_ms),
        "samples_ms": [round(v, 3) for v in timings_ms],
        "min_ms": round(min(timings_ms), 3),
        "p50_ms": round(p50_ms, 3),
        "p95_ms": round(p95_ms, 3),
        "max_ms": round(max(timings_ms), 3),
        "mean_ms": round(mean_ms, 3),
        "stdev_ms": round(stdev_ms, 3),
        "cv": round(stdev_ms / mean_ms, 4) if mean_ms > 0 else 0.0,
        "throughput_docs_per_s": round(throughput, 1) if math.isfinite(throughput) else None,
        "p95_vs_threshold_ratio": round(p95_ms / _THRESHOLD_MS, 3),
        "noise_warning": bool(mean_ms > 0 and stdev_ms / mean_ms > _NOISE_CV_WARNING),
    }


def _cpu_model() -> str:
    """取 CPU 型号：优先 /proc/cpuinfo，退化到 platform.processor()。"""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        # /proc/cpuinfo 不可用（非 Linux 或受限容器），退化为 platform 信息
        pass
    return platform.processor() or platform.machine() or "unknown"


def _environment() -> dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }


def run_suite(
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    samples: int = DEFAULT_SAMPLES,
    warmup: int = DEFAULT_WARMUP,
    candidate_pool: int = DEFAULT_CANDIDATE_POOL,
) -> dict[str, Any]:
    """运行固定规模阶梯的合成基准，返回可直接 JSON 序列化的结构化结果。"""
    if not scales or any(n < 1 for n in scales):
        raise ValueError(f"scales 必须为正整数序列: {scales}")
    if candidate_pool < 1:
        raise ValueError(f"candidate_pool 必须 >= 1: {candidate_pool}")

    candidates = _make_candidates(candidate_pool)
    dedupe_records: list[dict[str, Any]] = []
    match_records: list[dict[str, Any]] = []

    for n in scales:
        documents = _make_documents(n)
        dedupe_records.append(
            _make_result(
                n,
                _measure_ms(
                    lambda docs=documents: dedupe_documents(docs),
                    warmup=warmup,
                    samples=samples,
                ),
            )
        )

        texts = _make_news_texts(n, candidate_pool)
        match_records.append(
            _make_result(
                n,
                _measure_ms(
                    lambda batch=texts: [match_news_to_stock(text, candidates) for text in batch],
                    warmup=warmup,
                    samples=samples,
                ),
            )
        )

    return {
        "schema_version": _SCHEMA_VERSION,
        "measurement_scope": {
            "kind": "synthetic_cpu_only",
            "description": "合成数据、纯 CPU 纯逻辑测量；不依赖真实数据库 / 行情 / 网络",
            "production_sla": "not_run",
            "gate": "none（脚本不接入 CI，超阈值不失败）",
        },
        "environment": _environment(),
        "config": {
            "scales": list(scales),
            "samples": samples,
            "warmup": warmup,
            "candidate_pool": candidate_pool,
            "gc": "gc.collect() before each timed sample; GC enabled during measurement",
            "percentile": "linear interpolation",
            "throughput": "n / p50 seconds（吞吐按中位耗时折算）",
        },
        "threshold_reference": {
            "name": "PerfThreshold.MEMORY_COMPUTE",
            "ms": _THRESHOLD_MS,
            "usage": "仅参考对照，不是门禁；合成批次耗时不能直接等同于生产 SLA",
        },
        "workloads": [
            {
                "name": "dedupe_documents",
                "unit": "documents",
                "input": "同源、全部唯一（最坏路径：逐条全表比较后全部保留）",
                "records": dedupe_records,
            },
            {
                "name": "match_news_to_stock",
                "unit": "news_documents",
                "input": "每条快讯含完整代码与唯一公司名，候选池固定",
                "candidate_pool": candidate_pool,
                "records": match_records,
            },
        ],
        "not_run": [dict(item) for item in _NOT_RUN],
    }


def _print_human(result: dict[str, Any]) -> None:
    """打印便于粘贴到 PR / 报告的表格与 not_run 说明。"""
    env = result["environment"]
    print("== news_match 合成性能基线（T02 / D12） ==")
    print(
        f"环境: Python {env['python_version']} | {env['platform']} | CPU: {env['cpu_model']} | {env['timestamp_utc']}"
    )
    cfg = result["config"]
    print(
        f"口径: scales={cfg['scales']} samples={cfg['samples']} warmup={cfg['warmup']} "
        f"candidate_pool={cfg['candidate_pool']} | p95=线性插值 | GC 开启"
    )
    print(
        f"参考阈值: {result['threshold_reference']['name']}="
        f"{result['threshold_reference']['ms']:.0f}ms（仅对照，非门禁）"
    )
    header = f"{'workload':<22}{'N':>6}{'p50(ms)':>11}{'p95(ms)':>11}{'max(ms)':>11}{'docs/s':>12}{'CV':>8}"
    print(header)
    print("-" * len(header))
    for workload in result["workloads"]:
        for rec in workload["records"]:
            throughput = rec["throughput_docs_per_s"]
            throughput_text = f"{throughput:.1f}" if throughput is not None else "n/a"
            print(
                f"{workload['name']:<22}{rec['n']:>6}{rec['p50_ms']:>11.3f}{rec['p95_ms']:>11.3f}"
                f"{rec['max_ms']:>11.3f}{throughput_text:>12}{rec['cv']:>8.3f}"
                + ("  [noise]" if rec["noise_warning"] else "")
            )
    print("\nnot_run:")
    for item in result["not_run"]:
        print(f"- {item['item']}: {item['reason']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="news_match 合成性能基线（T02 / D12）")
    parser.add_argument("--scales", default=",".join(str(n) for n in DEFAULT_SCALES), help="规模阶梯，逗号分隔")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="每规模计时采样次数")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="每规模预热次数")
    parser.add_argument("--candidate-pool", type=int, default=DEFAULT_CANDIDATE_POOL, help="match 固定候选股池大小")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON（默认人类可读表格）")
    args = parser.parse_args(argv)

    scales: tuple[int, ...] = ()
    try:
        scales = tuple(int(part.strip()) for part in args.scales.split(",") if part.strip())
    except ValueError as exc:
        parser.error(f"--scales 解析失败: {exc}")
    if not scales:
        parser.error("--scales 不能为空")
    if args.samples < 1:
        parser.error("--samples 必须 >= 1")
    if args.warmup < 0:
        parser.error("--warmup 必须 >= 0")
    if args.candidate_pool < 1:
        parser.error("--candidate-pool 必须 >= 1")

    result = run_suite(
        scales=scales,
        samples=args.samples,
        warmup=args.warmup,
        candidate_pool=args.candidate_pool,
    )
    if args.json:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
