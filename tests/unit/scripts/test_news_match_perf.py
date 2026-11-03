"""T02 合成性能基线工具（scripts/probe/news_match_perf.py）的快速元测试。

只验证分位数口径、结构化输出契约与极小规模跑通；默认 CI 下耗时约毫秒级，
不执行 100 / 1000 / 5000 的完整基准（完整测量须手动运行脚本）。
"""

from __future__ import annotations

import pytest

from scripts.probe.news_match_perf import percentile, run_suite

pytestmark = [pytest.mark.unit, pytest.mark.meta]


def test_percentile_linear_interpolation() -> None:
    samples = [1.0, 2.0, 3.0, 4.0]
    assert percentile(samples, 0.0) == 1.0
    assert percentile(samples, 0.5) == 2.5
    assert percentile(samples, 1.0) == 4.0
    assert percentile([5.0], 0.95) == 5.0


def test_percentile_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        percentile([], 0.5)
    with pytest.raises(ValueError):
        percentile([1.0], 1.5)


def test_run_suite_tiny_is_structured_and_ordered() -> None:
    result = run_suite(scales=(20,), samples=2, warmup=0, candidate_pool=10)

    assert result["schema_version"] == 1
    assert result["measurement_scope"]["production_sla"] == "not_run"
    assert result["threshold_reference"]["name"] == "PerfThreshold.MEMORY_COMPUTE"
    assert [workload["name"] for workload in result["workloads"]] == [
        "dedupe_documents",
        "match_news_to_stock",
    ]
    assert result["not_run"]

    for workload in result["workloads"]:
        records = workload["records"]
        assert len(records) == 1
        record = records[0]
        assert record["n"] == 20
        assert len(record["samples_ms"]) == 2
        assert record["min_ms"] <= record["p50_ms"] <= record["p95_ms"] <= record["max_ms"]
        assert record["throughput_docs_per_s"] is not None
        assert record["throughput_docs_per_s"] > 0
