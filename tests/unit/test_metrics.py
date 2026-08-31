"""review05-E19: 进程内运行时指标聚合 MetricsRegistry 单元测试。"""

import threading

import pytest

from utils.metrics import MetricsRegistry, OperationStats

pytestmark = pytest.mark.unit


class TestOperationStats:
    def test_record_elapsed(self):
        stats = OperationStats()
        stats.record_elapsed(10.0)
        stats.record_elapsed(20.0)
        assert stats.count == 2
        assert stats.total_ms == 30.0
        assert stats.max_ms == 20.0
        assert stats.error_count == 0

    def test_record_error_updates_distribution(self):
        stats = OperationStats()
        stats.record_error("timeout")
        stats.record_elapsed(5.0)
        stats.record_error("timeout")
        stats.record_error("network")
        assert stats.error_count == 3
        assert stats.error_codes == {"timeout": 2, "network": 1}
        # record_error 不改变耗时计数
        assert stats.count == 1


class TestMetricsRegistry:
    def test_record_success_and_error(self):
        reg = MetricsRegistry()
        reg.record("op_a", 10.0)
        reg.record("op_a", 20.0, error_code="timeout")
        snap = reg.snapshot()
        assert set(snap) == {"op_a"}
        stats = snap["op_a"]
        assert stats.count == 2
        assert stats.total_ms == 30.0
        assert stats.max_ms == 20.0
        assert stats.error_count == 1
        assert stats.error_codes == {"timeout": 1}

    def test_error_code_distribution(self):
        reg = MetricsRegistry()
        reg.record("op", 1.0, error_code="timeout")
        reg.record("op", 2.0, error_code="network")
        reg.record("op", 3.0, error_code="timeout")
        stats = reg.snapshot()["op"]
        assert stats.error_codes == {"timeout": 2, "network": 1}

    def test_record_error_event(self):
        reg = MetricsRegistry()
        reg.record_error("op", "timeout")
        reg.record_error("op", "network")
        stats = reg.snapshot()["op"]
        assert stats.error_count == 2
        # 纯错误事件不改变耗时计数
        assert stats.count == 0

    def test_reset(self):
        reg = MetricsRegistry()
        reg.record("op", 1.0)
        reg.reset()
        assert reg.snapshot() == {}

    def test_snapshot_is_defensive_copy(self):
        reg = MetricsRegistry()
        reg.record("op", 5.0)
        snap = reg.snapshot()
        snap["op"].record_error("x")
        # 修改外部快照不应影响内部聚合状态
        assert reg.snapshot()["op"].error_codes == {}

    def test_thread_safety_concurrent_records(self):
        reg = MetricsRegistry()

        def worker():
            for _ in range(1000):
                reg.record("op", 1.0)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stats = reg.snapshot()["op"]
        assert stats.count == 8000
        assert stats.total_ms == 8000.0
