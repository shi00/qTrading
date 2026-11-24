"""长稳 harness（scripts/probe/agent_soak.py）的口径与边界单测。

覆盖 ceil-rank 分位数、最小二乘斜率、配置非法值、计数回落判定、
unknown 指标不伪装为通过，以及 dry-run 循环的计数回落与 JSON 契约。
使用注入的假采样器 / 假时钟，不依赖 psutil、真实进程或数据库。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from scripts.probe.agent_soak import (
    ProcessMetrics,
    ResourceSample,
    SoakConfig,
    SyntheticSoakBackend,
    judge_count_recovery,
    judge_rss_growth,
    linear_slope,
    percentile_ceil,
    run_soak,
)

pytestmark = [pytest.mark.unit, pytest.mark.meta]


class _FakeSampler:
    """确定性进程采样器：按固定值/步长返回 RSS 与句柄数。"""

    def __init__(self, *, rss_mb: float | None = 100.0, handles: int | None = 40, rss_step: float = 0.0) -> None:
        self._rss_mb = rss_mb
        self._handles = handles
        self._rss_step = rss_step

    @property
    def backend(self) -> str:
        return "fake"

    def snapshot(self) -> ProcessMetrics:
        metrics = ProcessMetrics(rss_mb=self._rss_mb, open_handles=self._handles)
        if self._rss_mb is not None:
            self._rss_mb += self._rss_step
        return metrics


class _StepClock:
    """每次调用递增固定秒数的单调时钟。"""

    def __init__(self, step_s: float = 10.0) -> None:
        self._step_s = step_s
        self._now_s = 0.0

    def __call__(self) -> float:
        current = self._now_s
        self._now_s += self._step_s
        return current


def _sample(
    round_index: int,
    *,
    active_tasks: int = 0,
    elapsed_s: float = 0.0,
    rss_mb: float | None = 100.0,
    handles: int | None = 40,
    phase: str = "settled",
) -> ResourceSample:
    return ResourceSample(
        round_index=round_index,
        phase=phase,
        elapsed_s=elapsed_s,
        rss_mb=rss_mb,
        active_tasks=active_tasks,
        active_subscriptions=active_tasks,
        active_connections=active_tasks,
        managed_temp_files=0,
        open_handles=handles,
    )


def _count_samples(values: Sequence[int]) -> list[ResourceSample]:
    return [_sample(index + 1, active_tasks=value) for index, value in enumerate(values)]


def test_percentile_ceil_uses_ceiling_rank() -> None:
    assert percentile_ceil(list(range(1, 21)), 0.95) == 19.0
    assert percentile_ceil(list(range(1, 21)), 0.50) == 10.0
    assert percentile_ceil(list(range(1, 101)), 0.95) == 95.0
    assert percentile_ceil([7.5], 0.95) == 7.5
    assert percentile_ceil([3.0, 1.0, 2.0], 0.0) == 1.0
    assert percentile_ceil([3.0, 1.0, 2.0], 1.0) == 3.0


def test_percentile_ceil_rejects_empty_and_invalid_q() -> None:
    with pytest.raises(ValueError, match="samples 不能为空"):
        percentile_ceil([], 0.95)
    with pytest.raises(ValueError, match="q 必须在"):
        percentile_ceil([1.0], 1.5)


def test_linear_slope_exact_value() -> None:
    assert linear_slope([0.0, 1.0, 2.0], [0.0, 2.0, 4.0]) == 2.0


def test_linear_slope_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError, match="至少需要 2 个样本"):
        linear_slope([1.0], [1.0])
    with pytest.raises(ValueError, match="长度不一致"):
        linear_slope([1.0, 2.0], [1.0])
    with pytest.raises(ValueError, match="不能全部相同"):
        linear_slope([1.0, 1.0], [1.0, 2.0])


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"rounds": 0}, "rounds 必须 >= 1"),
        ({"warmup_rounds": -1}, "warmup_rounds 必须 >= 0"),
        ({"tail_rounds": 0}, "tail_rounds 必须 >= 1"),
        ({"rounds": 5, "warmup_rounds": 3, "tail_rounds": 3}, r"warmup_rounds \+ tail_rounds 不能超过 rounds"),
        ({"rss_budget_mb_per_min": 0.0}, "rss_budget_mb_per_min 必须 > 0.0"),
        ({"rss_budget_mb_per_min": -1.0}, "rss_budget_mb_per_min 必须 > 0.0"),
        ({"rss_budget_mb_per_min": float("nan")}, "rss_budget_mb_per_min 必须为有限数值"),
        ({"rss_budget_mb_per_min": float("inf")}, "rss_budget_mb_per_min 必须为有限数值"),
        ({"count_tolerance": -1}, "count_tolerance 必须 >= 0"),
        ({"handle_tolerance": -1}, "handle_tolerance 必须 >= 0"),
        ({"round_pause_s": -0.1}, "round_pause_s 必须 >= 0.0"),
        ({"rounds": True}, "rounds 必须为整数"),
    ],
)
def test_soak_config_rejects_invalid_values(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        SoakConfig(**kwargs)  # type: ignore[arg-type]  # 参数化用例故意传入非法类型


def test_judge_count_recovery_passes_when_tail_returns_to_baseline() -> None:
    samples = _count_samples([0] * 5 + [10] * 10 + [0] * 5)

    check = judge_count_recovery(samples, "active_tasks", warmup_rounds=5, tail_rounds=5, tolerance=0)

    assert check["status"] == "pass"
    assert check["baseline_min"] == 0
    assert check["baseline_max"] == 0
    assert check["tail_min"] == 0
    assert check["tail_max"] == 0
    assert check["max_rise"] == 0


def test_judge_count_recovery_fails_on_unbounded_growth() -> None:
    samples = _count_samples([0] * 5 + [1] * 15)

    check = judge_count_recovery(samples, "active_tasks", warmup_rounds=5, tail_rounds=5, tolerance=0)

    assert check["status"] == "fail"
    assert check["tail_max"] == 1
    assert check["max_rise"] == 1
    assert "未回落基线区间" in check["reason"]


def test_judge_count_recovery_tolerance_boundary() -> None:
    samples = _count_samples([0] * 5 + [2] * 5 + [2] * 10)

    assert (
        judge_count_recovery(samples, "active_tasks", warmup_rounds=5, tail_rounds=5, tolerance=2)["status"] == "pass"
    )
    assert (
        judge_count_recovery(samples, "active_tasks", warmup_rounds=5, tail_rounds=5, tolerance=1)["status"] == "fail"
    )


def test_judge_count_recovery_unknown_when_metric_missing() -> None:
    samples = [_sample(index + 1, handles=None) for index in range(10)]

    check = judge_count_recovery(samples, "open_handles", warmup_rounds=1, tail_rounds=3, tolerance=0)

    assert check["status"] == "unknown"
    assert "不按 0 处理" in check["reason"]


def test_judge_count_recovery_empty_samples_is_unknown() -> None:
    check = judge_count_recovery([], "active_tasks", warmup_rounds=1, tail_rounds=1, tolerance=0)

    assert check["status"] == "unknown"


def test_judge_count_recovery_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="未知的计数指标"):
        judge_count_recovery([], "not_a_metric", warmup_rounds=0, tail_rounds=1, tolerance=0)
    with pytest.raises(ValueError, match="tolerance 必须 >= 0"):
        judge_count_recovery([], "active_tasks", warmup_rounds=0, tail_rounds=1, tolerance=-1)


def test_judge_rss_growth_detects_slope_over_budget() -> None:
    growing = [_sample(index, elapsed_s=index * 10.0, rss_mb=100.0 + index) for index in range(1, 21)]

    check = judge_rss_growth(growing, budget_mb_per_min=5.0)

    assert check["status"] == "fail"
    assert check["slope_mb_per_min"] == pytest.approx(6.0, abs=1e-6)
    assert "超出预算" in check["reason"] or "MB/分钟" in check["reason"]


def test_judge_rss_growth_passes_for_flat_rss() -> None:
    flat = [_sample(index, elapsed_s=index * 10.0, rss_mb=100.0) for index in range(1, 21)]

    check = judge_rss_growth(flat, budget_mb_per_min=5.0)

    assert check["status"] == "pass"
    assert check["slope_mb_per_min"] == 0.0


def test_judge_rss_growth_unknown_for_short_window_or_missing_samples() -> None:
    short = [_sample(index, elapsed_s=index * 0.1, rss_mb=100.0 + index) for index in range(1, 21)]
    missing = [_sample(index, elapsed_s=index * 10.0, rss_mb=None) for index in range(1, 21)]

    assert judge_rss_growth(short, budget_mb_per_min=5.0)["status"] == "unknown"
    assert judge_rss_growth(missing, budget_mb_per_min=5.0)["status"] == "unknown"


def test_synthetic_backend_concurrent_subscriptions_return_to_baseline() -> None:
    backend = SyntheticSoakBackend()
    try:
        tokens = [backend.subscribe() for _ in range(3)]
        assert len(tokens) == 3
        assert backend.active_tasks == 3
        assert backend.active_subscriptions == 3
        assert backend.active_connections == 3
        assert backend.managed_temp_files == 3

        backend.complete()
        backend.fail()
        backend.recover()

        assert backend.active_tasks == 0
        assert backend.active_subscriptions == 0
        assert backend.active_connections == 0
        assert backend.managed_temp_files == 0
    finally:
        backend.close()


def test_run_soak_synthetic_backend_returns_to_baseline() -> None:
    report = run_soak(
        SoakConfig(rounds=10, warmup_rounds=2, tail_rounds=3),
        sampler=_FakeSampler(),
        clock=_StepClock(step_s=10.0),
    )

    assert report["mode"] == "dry_run"
    assert report["scope"]["database"] == "none"
    assert report["scope"]["network"] == "none"
    assert report["outcomes"] == {"complete": 4, "fail": 3, "recover": 3}
    assert report["judgement"]["verdict"] == "pass"

    peak = [sample for sample in report["samples"] if sample["phase"] == "peak"]
    settled = [sample for sample in report["samples"] if sample["phase"] == "settled"]
    assert len(peak) == 10
    assert len(settled) == 10
    assert {sample["active_tasks"] for sample in peak} == {1}
    assert {sample["active_subscriptions"] for sample in peak} == {1}
    assert {sample["active_connections"] for sample in peak} == {1}
    assert {sample["managed_temp_files"] for sample in peak} == {1}
    assert {sample["active_tasks"] for sample in settled} == {0}
    assert {sample["managed_temp_files"] for sample in settled} == {0}

    assert json.loads(json.dumps(report, ensure_ascii=False))["harness"] == "agent_soak"


def test_run_soak_unknown_metrics_make_verdict_incomplete() -> None:
    report = run_soak(
        SoakConfig(rounds=10, warmup_rounds=2, tail_rounds=3),
        sampler=_FakeSampler(rss_mb=None, handles=None),
        clock=_StepClock(step_s=10.0),
    )

    assert report["judgement"]["rss"]["status"] == "unknown"
    assert report["judgement"]["handles"]["status"] == "unknown"
    assert report["judgement"]["verdict"] == "incomplete"
