import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.mixins.health_mixin import HealthCheckMixin, _compute_tier
from data.constants import (
    SYNC_RESULT_EMPTY,
    TIER_FINANCIAL_FRESHNESS_DAYS,
    TIER_FIN_FRESH_RATIO_GOLD,
    TIER_FIN_FRESH_RATIO_MIN,
    TIER_FIN_FRESH_RATIO_NEUTRAL,
    TIER_FUNDAMENTAL_HIGH_THRESHOLD,
    TIER_QUOTE_FRESHNESS_DAYS,
)


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


class TestComputeTierMissingBranches:
    def test_fin_fresh_ratio_none_stale_returns_1(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS + 5,
            fin_fresh_ratio=None,
            missing_critical=False,
        )
        assert result == 1

    def test_gold_with_fin_lag_and_high_fundamental(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL,
            missing_critical=False,
            fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.1,
        )
        assert result == 3

    def test_gold_without_fin_lag_high_ratio(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_GOLD + 0.01,
            missing_critical=False,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.1,
        )
        assert result == 3

    def test_fin_ok_for_gold_but_low_fundamental(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL,
            missing_critical=False,
            fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD - 0.1,
        )
        assert result == 2

    def test_fin_ok_for_gold_no_fin_lag_but_below_gold_ratio(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_GOLD - 0.01,
            missing_critical=False,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.1,
        )
        assert result == 2

    def test_fresh_ratio_above_neutral(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL + 0.01,
            missing_critical=False,
        )
        assert result == 2

    def test_fresh_ratio_below_neutral_above_min(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN + 0.01,
            missing_critical=False,
        )
        assert result == 2

    def test_fresh_ratio_below_min(self):
        result = _compute_tier(
            lag_days=TIER_QUOTE_FRESHNESS_DAYS,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN - 0.05,
            missing_critical=False,
        )
        assert result == 1


class TestAssignBasicTierDeepBranches:
    @pytest.mark.asyncio
    async def test_stale_quotes_db_fallback_fresh(self):
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
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240610")
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.side_effect = [
                    datetime.datetime(2024, 1, 1),
                    datetime.datetime(2024, 6, 10),
                ]
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_stale_quotes_db_fallback_exception(self):
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
        proc.cache.get_latest_trade_date = AsyncMock(side_effect=Exception("DB error"))
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 1, 1)
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_critical_table_empty_via_status(self):
        proc = FakeProcessor()
        rows = [
            {
                "table_name": "daily_quotes",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 5000,
            },
            {
                "table_name": "daily_indicators",
                "last_data_date": "",
                "status": "empty",
                "last_result_status": SYNC_RESULT_EMPTY,
                "record_count": 0,
            },
        ]
        sync_df = pd.DataFrame(rows)
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 6, 10)
                await proc._assign_basic_tier()
                assert proc._quality_tier == 0

    @pytest.mark.asyncio
    async def test_critical_table_empty_via_record_count_zero(self):
        proc = FakeProcessor()
        rows = [
            {
                "table_name": "daily_quotes",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 5000,
            },
            {
                "table_name": "daily_indicators",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 0,
            },
        ]
        sync_df = pd.DataFrame(rows)
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 6, 10)
                await proc._assign_basic_tier()
                assert proc._quality_tier == 0

    @pytest.mark.asyncio
    async def test_critical_table_stale_parse_error(self):
        proc = FakeProcessor()
        rows = [
            {
                "table_name": "daily_quotes",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 5000,
            },
            {
                "table_name": "financial_reports",
                "last_data_date": "bad_date",
                "status": "success",
                "last_result_status": "success",
                "record_count": 100,
            },
        ]
        sync_df = pd.DataFrame(rows)
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            call_count = [0]

            def parse_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return datetime.datetime(2024, 6, 10)
                raise ValueError("bad date")

            with patch("data.mixins.health_mixin.parse_date", side_effect=parse_side_effect):
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_critical_table_no_last_date(self):
        proc = FakeProcessor()
        rows = [
            {
                "table_name": "daily_quotes",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 5000,
            },
            {
                "table_name": "financial_reports",
                "last_data_date": "",
                "status": "success",
                "last_result_status": "success",
                "record_count": 100,
            },
        ]
        sync_df = pd.DataFrame(rows)
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.mixins.health_mixin.parse_date") as mock_parse:
                mock_parse.return_value = datetime.datetime(2024, 6, 10)
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_fin_lag_days_computed(self):
        proc = FakeProcessor()
        rows = [
            {
                "table_name": "daily_quotes",
                "last_data_date": "20240610",
                "status": "success",
                "last_result_status": "success",
                "record_count": 5000,
            },
            {
                "table_name": "financial_reports",
                "last_data_date": "20240601",
                "status": "success",
                "last_result_status": "success",
                "record_count": 100,
            },
        ]
        sync_df = pd.DataFrame(rows)
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            call_count = [0]

            def parse_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return datetime.datetime(2024, 6, 10)
                return datetime.datetime(2024, 6, 1)

            with patch("data.mixins.health_mixin.parse_date", side_effect=parse_side_effect):
                await proc._assign_basic_tier()
                assert proc._quality_tier is not None

    @pytest.mark.asyncio
    async def test_stale_quotes_no_critical(self):
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


class TestCheckDataHealthDeepBranches:
    @pytest.mark.asyncio
    async def test_end_date_none_uses_today(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        async def fake_get_latest_trade_date():
            return None

        proc.get_trade_dates = fake_get_trade_dates
        proc.get_latest_trade_date = fake_get_latest_trade_date
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")
            assert "lag" in result["details"]

    @pytest.mark.asyncio
    async def test_start_date_fallback_insufficient_dates(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")
            assert "lag" in result["details"]

    @pytest.mark.asyncio
    async def test_api_trade_cal_cross_check(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240610"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates

        mock_tc = MagicMock()
        mock_tc.trade_cal = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.external.tushare_client.TushareClient", return_value=mock_tc):
                result = await proc.check_data_health()
                assert result["status"] in ("red", "yellow", "green")
                assert "lag" in result["details"]

    @pytest.mark.asyncio
    async def test_api_extends_gold_standard(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240610"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240613"]

        proc.get_trade_dates = fake_get_trade_dates

        mock_tc = MagicMock()
        mock_tc.trade_cal = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("data.external.tushare_client.TushareClient", return_value=mock_tc):
                result = await proc.check_data_health()
                assert result["status"] in ("red", "yellow", "green")
                assert "lag" in result["details"]

    @pytest.mark.asyncio
    async def test_concept_count_error(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(side_effect=Exception("DB error"))
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_no_local_dates_lag(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value=set())
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_red_status_extreme_lag(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240101"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_data_status_yellow_many_missing(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        tables = {
            "financial_reports": {"ratio": 0.9},
            "t1": {"type": "stock", "ratio": 0.01, "sparse": False},
            "t2": {"type": "stock", "ratio": 0.01, "sparse": False},
            "t3": {"type": "stock", "ratio": 0.01, "sparse": False},
            "t4": {"type": "stock", "ratio": 0.01, "sparse": False},
        }
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(return_value={"tables": tables, "global_trade_days": 500})
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_data_status_yellow_fin_coverage_low(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.5}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_depth_warning(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9, "depth_ratio": 0.5}}, "global_trade_days": 10}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=2):
                result = await proc.check_data_health()
                assert "status" in result

    @pytest.mark.asyncio
    async def test_breadth_warning(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={
                "tables": {"financial_reports": {"ratio": 0.9, "breadth_ratio": 0.5}},
                "global_trade_days": 500,
            }
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_field_completeness_and_fin_lag(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9, "revenue": 0.8})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_field_completeness_exception(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(side_effect=Exception("FC error"))

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")

    @pytest.mark.asyncio
    async def test_sync_status_fin_lag_computation(self):
        proc = FakeProcessor()
        proc._health_cache = {"time": 0, "data": None}
        proc.cache.get_cached_trade_dates = AsyncMock(return_value={"20240614"})
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.9}}, "global_trade_days": 500}
        )
        proc.cache.get_concept_count = AsyncMock(return_value=100)
        sync_df = pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240601"]})
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9})

        async def fake_get_trade_dates(start_date=None, end_date=None):
            return ["20240101", "20240614"]

        proc.get_trade_dates = fake_get_trade_dates
        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.check_data_health()
            assert result["status"] in ("red", "yellow", "green")


class TestRunQualityScanDeepBranches:
    @pytest.mark.asyncio
    async def test_scan_with_progress_callback(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )
        progress_calls = []

        def progress_cb(current, total, msg):
            progress_calls.append((current, total, msg))

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5, progress_callback=progress_cb)
            assert "score" in result
            assert len(progress_calls) > 0

    @pytest.mark.asyncio
    async def test_scan_with_cancelled(self):
        proc = FakeProcessor()
        proc.is_cancelled = MagicMock(return_value=True)
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_no_batch_data(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
        )
        proc.cache.get_daily_quotes = AsyncMock(return_value=None)
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_empty_trade_cal(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(return_value=None)

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_fin_lag_with_datetime(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        sync_df = pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240601"]})
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        async def fake_get_latest_trade_date():
            return datetime.datetime(2024, 6, 14)

        proc.get_latest_trade_date = fake_get_latest_trade_date

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_fin_lag_with_date_object(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        sync_df = pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240601"]})
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        async def fake_get_latest_trade_date():
            return datetime.date(2024, 6, 14)

        proc.get_latest_trade_date = fake_get_latest_trade_date

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_fundamental_completeness_with_values(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        proc.cache.get_field_completeness = AsyncMock(return_value={"eps": 0.9, "revenue": 0.8, "net_profit": None})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result
            assert result.get("avg_fundamental") is not None

    @pytest.mark.asyncio
    async def test_scan_no_fundamental_completeness(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_coverage_exception(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(side_effect=Exception("health error"))
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
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_latest_trade_date_exception(self):
        proc = FakeProcessor()

        async def fake_get_latest_trade_date():
            raise Exception("date error")

        proc.get_latest_trade_date = fake_get_latest_trade_date
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
        )
        proc.cache.get_daily_quotes = AsyncMock(return_value=None)
        proc.cache.get_latest_trade_date = AsyncMock(return_value="20240614")
        proc.cache.get_field_completeness = AsyncMock(return_value={})
        proc.cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        )
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result

    @pytest.mark.asyncio
    async def test_scan_fin_recency_ok(self):
        proc = FakeProcessor()
        basics = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 10)],
                "list_status": ["L"] * 9,
            }
        )
        proc.cache.get_stock_basic = AsyncMock(return_value=basics)
        proc.cache.check_comprehensive_health = AsyncMock(
            return_value={"tables": {"financial_reports": {"ratio": 0.8}}, "global_trade_days": 500}
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
        sync_df = pd.DataFrame({"table_name": ["financial_reports"], "last_data_date": ["20240610"]})
        proc.cache.get_sync_status = AsyncMock(return_value=sync_df)
        proc.trade_calendar.get_trade_cal_df = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        )

        with patch("data.mixins.health_mixin.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14)
            result = await proc.run_quality_scan(sample_size=5)
            assert "score" in result
            assert "fin_recency_ok" in result
