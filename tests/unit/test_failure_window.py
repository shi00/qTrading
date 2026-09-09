"""D1-3 滑动窗口失败率熔断判据单元测试。

修复点：``_FailureWindow`` 以并发友好的滑动窗口失败率取代 ``consecutive_failures``
整型计数，样本未满窗口不熔断，满窗口按失败率阈值判定。
"""

from data.sync.historical import _FailureWindow


class TestFailureWindow:
    def test_empty_window_never_trips(self):
        w = _FailureWindow(size=5)
        assert not w.should_trip
        assert w.failure_rate_pct == 0.0

    def test_insufficient_samples_never_trip(self):
        w = _FailureWindow(size=5)
        w.record(False)
        w.record(False)
        assert not w.should_trip  # 2 < 5，样本不足

    def test_full_window_all_fail_trips(self):
        w = _FailureWindow(size=3)
        w.record(False)
        w.record(False)
        w.record(False)
        assert w.should_trip  # 失败率 100% ≥ 80%

    def test_failure_rate_at_threshold_trips(self):
        w = _FailureWindow(size=5, threshold=0.8)
        w.record(False)
        w.record(False)
        w.record(False)
        w.record(False)
        w.record(True)
        assert w.should_trip  # 4/5 = 80%
        assert w.failure_rate_pct == 80.0

    def test_failure_rate_below_threshold_not_trip(self):
        w = _FailureWindow(size=5, threshold=0.8)
        w.record(False)
        w.record(False)
        w.record(False)
        w.record(True)
        w.record(True)
        assert not w.should_trip  # 3/5 = 60% < 80%

    def test_window_slides_after_successes(self):
        w = _FailureWindow(size=3)
        w.record(False)
        w.record(False)
        w.record(False)
        assert w.should_trip
        w.record(True)
        w.record(True)
        w.record(True)
        assert not w.should_trip  # 连续成功滑出旧失败样本
        w.record(False)
        assert not w.should_trip  # 2 成功 1 失败 = 33% < 80%
