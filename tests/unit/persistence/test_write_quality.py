"""WriteQuality 单测（DAT-03：表级日期 coerce 登记与降级判定）。"""

import threading

import pytest

import data.persistence.write_quality as write_quality_module
from data.persistence.write_quality import (
    WRITE_COERCE_DEGRADE_RATIO,
    WRITE_COERCE_MAX_AGE_SEC,
    WriteQuality,
)

pytestmark = pytest.mark.unit


class TestWriteQualityRecord:
    def test_record_and_get_ratio(self):
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=10)
        assert WriteQuality().get_coerce_ratio("daily_quotes") == pytest.approx(0.1)

    def test_no_write_returns_none(self):
        assert WriteQuality().get_coerce_ratio("daily_quotes") is None
        assert WriteQuality().is_degraded("daily_quotes") is False

    def test_zero_rows_ratio_none(self):
        WriteQuality().record_write("daily_quotes", rows=0, coerced_rows=5)
        # rows<=0 视为无有效写入依据，不提供比率也不降级
        assert WriteQuality().get_coerce_ratio("daily_quotes") is None
        assert WriteQuality().is_degraded("daily_quotes") is False

    def test_record_overwrite_latest(self):
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=10)
        # 后续一次正常写入（coerced=0）覆盖 ratio，使该表恢复可信
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=0)
        assert WriteQuality().get_coerce_ratio("daily_quotes") == pytest.approx(0.0)
        assert WriteQuality().is_degraded("daily_quotes") is False


class TestWriteQualityDegrade:
    def test_is_degraded_above_threshold(self):
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=10)
        assert WriteQuality().is_degraded("daily_quotes") is True

    def test_is_degraded_below_threshold(self):
        # ratio = 1/10000 = 1e-4 < WRITE_COERCE_DEGRADE_RATIO (1e-3)
        WriteQuality().record_write("daily_quotes", rows=10000, coerced_rows=1)
        assert WriteQuality().is_degraded("daily_quotes") is False

    def test_max_age_boundary_exactly_24h_still_valid(self, monkeypatch):
        """24h 过期边界：恰好等于 WRITE_COERCE_MAX_AGE_SEC 时仍有效（严格 ``>``）。

        monkeypatch 模块内 ``time.time``，避免依赖真实时钟/受执行耗时影响。
        """
        clock = {"now": 1_000_000.0}
        monkeypatch.setattr(write_quality_module.time, "time", lambda: clock["now"])
        quality = WriteQuality()
        quality.record_write("daily_quotes", rows=100, coerced_rows=10)

        clock["now"] += WRITE_COERCE_MAX_AGE_SEC
        assert quality.get_coerce_ratio("daily_quotes") == pytest.approx(0.1)
        assert quality.is_degraded("daily_quotes") is True

    def test_max_age_boundary_one_second_past_expires(self, monkeypatch):
        """超过 24h 一秒即失效：不再提供比率，也不再触发降级。"""
        clock = {"now": 1_000_000.0}
        monkeypatch.setattr(write_quality_module.time, "time", lambda: clock["now"])
        quality = WriteQuality()
        quality.record_write("daily_quotes", rows=100, coerced_rows=10)

        clock["now"] += WRITE_COERCE_MAX_AGE_SEC + 1
        assert quality.get_coerce_ratio("daily_quotes") is None
        assert quality.is_degraded("daily_quotes") is False

    def test_reset_singleton_clears_state_for_restart(self):
        """``_reset_singleton`` 后重启语义：新实例不继承旧登记（R7 隔离由 autouse fixture 兜底）。"""
        first = WriteQuality()
        first.record_write("daily_quotes", rows=100, coerced_rows=10)
        assert WriteQuality() is first
        assert first.get_coerce_ratio("daily_quotes") == pytest.approx(0.1)

        WriteQuality._reset_singleton()

        restarted = WriteQuality()
        assert restarted is not first
        assert restarted.get_coerce_ratio("daily_quotes") is None
        assert restarted.is_degraded("daily_quotes") is False

    def test_concurrent_record_and_get_no_corruption(self):
        """并发 record/get 冒烟：无异常、读到的比率只可能是写入过的合法值。

        说明：GIL 下单次 dict 读写本身难以必然暴露缺锁，本用例只能证明「并发调用
        不抛异常且状态不产生非法中间值」，不能单独证明锁的充分性。
        """
        quality = WriteQuality()
        quality.record_write("daily_quotes", rows=100, coerced_rows=10)

        workers = 4
        barrier = threading.Barrier(workers * 2)
        ratios_seen: list[float | None] = []
        degraded_seen: list[bool] = []
        errors: list[BaseException] = []
        seen_lock = threading.Lock()

        def writer() -> None:
            barrier.wait()
            try:
                for i in range(50):
                    quality.record_write("daily_quotes", rows=100, coerced_rows=10 if i % 2 else 0)
            except Exception as exc:  # 线程内异常需回传主线程断言
                errors.append(exc)

        def reader() -> None:
            barrier.wait()
            try:
                for _ in range(200):
                    ratio = quality.get_coerce_ratio("daily_quotes")
                    degraded = quality.is_degraded("daily_quotes")
                    with seen_lock:
                        ratios_seen.append(ratio)
                        degraded_seen.append(degraded)
            except Exception as exc:  # 线程内异常需回传主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(workers)]
        threads += [threading.Thread(target=reader) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors
        assert not any(thread.is_alive() for thread in threads)
        assert all(ratio in (0.0, pytest.approx(0.1)) for ratio in ratios_seen)
        assert all(degraded in (False, True) for degraded in degraded_seen)
        assert quality.get_coerce_ratio("daily_quotes") in (0.0, pytest.approx(0.1))

    def test_ratio_threshold_constant_is_small(self):
        assert 0 < WRITE_COERCE_DEGRADE_RATIO < 0.01

    def test_expired_ratio_returns_none(self):
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=10)
        # 传入极小的 max_age 使写入立即视为过期 → 无有效依据，不触发降级
        assert WriteQuality().get_coerce_ratio("daily_quotes", max_age_sec=0.0) is None
        assert WriteQuality().is_degraded("unknown_table") is False
