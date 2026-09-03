"""WriteQuality 单测（DAT-03：表级日期 coerce 登记与降级判定）。"""

import pytest

from data.persistence.write_quality import (
    WRITE_COERCE_DEGRADE_RATIO,
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

    def test_ratio_threshold_constant_is_small(self):
        assert 0 < WRITE_COERCE_DEGRADE_RATIO < 0.01

    def test_expired_ratio_returns_none(self):
        WriteQuality().record_write("daily_quotes", rows=100, coerced_rows=10)
        # 传入极小的 max_age 使写入立即视为过期 → 无有效依据，不触发降级
        assert WriteQuality().get_coerce_ratio("daily_quotes", max_age_sec=0.0) is None
        assert WriteQuality().is_degraded("unknown_table") is False
