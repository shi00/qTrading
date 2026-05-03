import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.mixins.health_mixin import HealthCheckMixin, _compute_tier
from data.constants import (
    TIER_QUOTE_FRESHNESS_DAYS,
    TIER_FINANCIAL_FRESHNESS_DAYS,
    TIER_FIN_FRESH_RATIO_GOLD,
    TIER_FIN_FRESH_RATIO_NEUTRAL,
    TIER_FIN_FRESH_RATIO_MIN,
    TIER_FUNDAMENTAL_HIGH_THRESHOLD,
    TIER_FUNDAMENTAL_LOW_THRESHOLD,
)


class TestComputeTier:
    def test_critical_missing_tables(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=1.0, missing_critical=True) == 0

    def test_bronze_stale_quotes(self):
        assert _compute_tier(lag_days=TIER_QUOTE_FRESHNESS_DAYS + 10, fin_fresh_ratio=1.0, missing_critical=False) == 1

    def test_silver_low_fundamental(self):
        assert (
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=0.8,
                missing_critical=False,
                avg_fundamental=TIER_FUNDAMENTAL_LOW_THRESHOLD - 0.1,
            )
            == 2
        )

    def test_gold_with_fin_lag(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL,
            missing_critical=False,
            fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.1,
        )
        assert result == 3

    def test_gold_without_fin_lag(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_GOLD + 0.01,
            missing_critical=False,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.1,
        )
        assert result == 3

    def test_silver_fresh_no_gold(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL + 0.01,
            missing_critical=False,
        )
        assert result == 2

    def test_silver_fresh_min_ratio(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN,
            missing_critical=False,
        )
        assert result == 2

    def test_bronze_low_ratio(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN - 0.1,
            missing_critical=False,
        )
        assert result == 1

    def test_fin_fresh_ratio_none_fresh(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS,
            fin_fresh_ratio=None,
            missing_critical=False,
        )
        assert result == 2

    def test_fin_fresh_ratio_none_stale(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS + 1,
            fin_fresh_ratio=None,
            missing_critical=False,
        )
        assert result == 1

    def test_gold_not_reached_without_avg_fundamental(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_GOLD + 0.01,
            missing_critical=False,
            fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
        )
        assert result < 3


class FakeProcessor(HealthCheckMixin):
    def __init__(self):
        self.cache = MagicMock()
        self._quality_tier = None
        self._health_cache = {"time": 0, "data": None}
        self.trade_calendar = MagicMock()

    def is_cancelled(self):
        return False

    def clear_cancel(self):
        pass

    async def get_latest_trade_date(self):
        return "20240614"

    async def get_trade_dates(self, start_date=None, end_date=None):
        return ["20240101", "20240614"]


class TestAssignBasicTier:
    @pytest.mark.asyncio
    async def test_no_sync_records(self):
        proc = FakeProcessor()
        proc.cache.get_sync_status = AsyncMock(return_value=None)
        await proc._assign_basic_tier()
        assert proc._quality_tier == 0

    @pytest.mark.asyncio
    async def test_empty_sync_records(self):
        proc = FakeProcessor()
        proc.cache.get_sync_status = AsyncMock(return_value=pd.DataFrame())
        await proc._assign_basic_tier()
        assert proc._quality_tier == 0

    @pytest.mark.asyncio
    async def test_fresh_quotes(self):
        proc = FakeProcessor()
        sync_df = pd.DataFrame(
            {
                "table_name": ["daily_quotes"],
                "last_data_date": ["20240610"],
                "status": ["success"],
                "last_result_status": ["success"],
                "record_count": [5000],
            }
        )
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 6, 10)
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_stale_quotes(self):
        proc = FakeProcessor()
        sync_df = pd.DataFrame(
            {
                "table_name": ["daily_quotes"],
                "last_data_date": ["20240101"],
                "status": ["success"],
                "last_result_status": ["success"],
                "record_count": [5000],
            }
        )
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 1, 1)
                await proc._assign_basic_tier()
                assert proc._quality_tier == 1

    @pytest.mark.asyncio
    async def test_exception_fallback(self):
        proc = FakeProcessor()
        proc.cache.get_sync_status = AsyncMock(side_effect=Exception("DB error"))
        await proc._assign_basic_tier()
        assert proc._quality_tier == 1


class TestCheckDataHealth:
    @pytest.mark.asyncio
    async def test_cached_result(self):
        proc = FakeProcessor()
        cached = {"status": "green", "msg": "OK"}
        proc._health_cache = {"time": 9999999999, "data": cached}
        result = await proc.check_data_health()
        assert result == cached

    @pytest.mark.asyncio
    async def test_no_trade_dates(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value=[])
        proc.cache.check_comprehensive_health = AsyncMock(return_value={"tables": {}})
        proc.cache.get_concept_count = AsyncMock(return_value=0)
        proc.cache.get_sync_status = AsyncMock(return_value=pd.DataFrame())
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return []

        proc.get_trade_dates = fake_get_trade_dates
        result = await proc.check_data_health()
        assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_green_status(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={
                "tables": {"financial_reports": {"ratio": 0.9}},
                "global_trade_days": 500,
            }
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20240610"],
                }
            )
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert "status" in result

    @pytest.mark.asyncio
    async def test_exception_returns_red(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(side_effect=Exception("DB error"))
        result = await proc.check_data_health()
        assert result["status"] == "red"


class TestRunQualityScan:
    @pytest.mark.asyncio
    async def test_empty_basics(self):
        proc = FakeProcessor()
        proc.cache.get_stock_basic = AsyncMock(return_value=None)
        result = await proc.run_quality_scan()
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_empty_active_stocks(self):
        proc = FakeProcessor()
        proc.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": [], "list_status": []}))
        result = await proc.run_quality_scan()
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_scan_with_data(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 60)],
                "list_status": ["L"] * 59,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={
                "tables": {"financial_reports": {"ratio": 0.8}},
                "global_trade_days": 500,
            }
        )
        proc.cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "close": [10.0],
                    "vol": [1000],
                }
            )
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20240610"],
                }
            )
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "cal_date": ["20240614"],
                    "is_open": [1],
                }
            )
        )

        result = await proc.run_quality_scan(sample_size=5)
        assert "score" in result
        assert "tier" in result

    @pytest.mark.asyncio
    async def test_scan_exception(self):
        proc = FakeProcessor()
        proc.cache.get_stock_basic = AsyncMock(side_effect=Exception("DB error"))
        result = await proc.run_quality_scan()
        assert result["score"] == 0
        assert "error" in result
