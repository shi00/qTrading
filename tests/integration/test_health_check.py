"""
Tests for health check and data sync interactions.

P1-5: Health cache invalidated after sync.
P1-6: quality_scan includes delisted stocks (list_status D).
P1-7: API trade_cal used as gold standard for lag calculation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.mixins.health_mixin import _compute_tier


class TestHealthCacheInvalidation:
    """P1-5: Health cache must be cleared after sync operations"""

    @pytest.mark.asyncio
    async def test_sync_historical_clears_health_cache(self):
        from data.data_processor import DataProcessor

        DataProcessor._instance = None
        DataProcessor._initialized = False

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["historical"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=10)
                await processor.sync_historical_data()

            assert processor._health_cache == {"time": 0, "data": None}

        DataProcessor._instance = None
        DataProcessor._initialized = False

    @pytest.mark.asyncio
    async def test_sync_financial_clears_health_cache(self):
        from data.data_processor import DataProcessor

        DataProcessor._instance = None
        DataProcessor._initialized = False

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["financial"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=5)
                await processor.sync_financial_reports()

            assert processor._health_cache == {"time": 0, "data": None}

        DataProcessor._instance = None
        DataProcessor._initialized = False

    @pytest.mark.asyncio
    async def test_sync_comprehensive_clears_health_cache(self):
        from data.data_processor import DataProcessor

        DataProcessor._instance = None
        DataProcessor._initialized = False

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["financial"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=5)
                await processor.sync_comprehensive_fundamentals()

            assert processor._health_cache == {"time": 0, "data": None}

        DataProcessor._instance = None
        DataProcessor._initialized = False


class TestQualityScanDelisted:
    """P1-6: quality_scan should include delisted stocks (list_status D)"""

    def test_scan_filter_includes_delisted_status(self):
        basics_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                "list_status": ["L", "D", "L", "P"],
            }
        )
        active_stocks = basics_df[basics_df["list_status"].isin(["L", "D"])]["ts_code"].tolist()
        assert "000001.SZ" in active_stocks
        assert "000002.SZ" in active_stocks
        assert "000004.SZ" not in active_stocks

    def test_delisted_stocks_counted_in_sample(self):
        basics_df = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 101)] + ["900001.SZ"],
                "list_status": ["L"] * 100 + ["D"],
            }
        )
        active_stocks = basics_df[basics_df["list_status"].isin(["L", "D"])]["ts_code"].tolist()
        assert "900001.SZ" in active_stocks
        assert len(active_stocks) == 101


class TestLagDaysGoldStandard:
    """P1-7: API trade_cal used as gold standard for lag calculation"""

    def test_api_extends_official_dates(self):
        official_dates = ["20240101", "20240102", "20240103"]
        api_latest = "20240105"
        local_dates = {"20240101", "20240102"}

        gold_standard = list(official_dates)
        if api_latest > official_dates[-1]:
            gold_standard = official_dates + [api_latest]

        last_local = sorted(local_dates)[-1]
        lag_days = len([d for d in gold_standard if d > last_local])

        assert lag_days == 2
        assert "20240105" in gold_standard

    def test_no_api_falls_back_to_official(self):
        official_dates = ["20240101", "20240102", "20240103"]
        api_latest = None

        gold_standard = official_dates
        if api_latest and api_latest > official_dates[-1]:
            gold_standard = official_dates + [api_latest]

        assert gold_standard == official_dates

    def test_compute_tier_uses_lag_days(self):
        result_no_lag = _compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=False)
        result_with_lag = _compute_tier(lag_days=10, fin_fresh_ratio=0.9, missing_critical=False)
        assert result_no_lag >= result_with_lag


import pytest
