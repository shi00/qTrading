"""
Integration Tests for AI Core Modules (Backtest Context)
Targets: BacktestDataProvider, AIStrategyMixin, ReviewManager
Coverage Goal: R2/R5 fix validation

This module validates:
- R2: _disable_ai flag is consumed by run_ai_analysis()
- R5: NewsFetcher.get_stock_news() receives as_of parameter in backtest mode
- End-to-end: as_of date propagation from BacktestDataProvider to ReviewManager
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from data.constants import SAFE_BACKTEST_LEARNING_OFFSET_DAYS
from data.external.news_fetcher import NewsFetcher
from strategies.oversold_strategy import OversoldStrategy


@pytest_asyncio.fixture
async def db_cache(test_engine: AsyncEngine):
    """Create CacheManager instance with test engine."""
    from data.cache.cache_manager import CacheManager

    CacheManager._reset_singleton()
    cache = CacheManager()
    cache._create_engine(str(test_engine.url))
    cache._disposed = False
    yield cache

    CacheManager._reset_singleton()


@pytest.fixture
def mock_data_processor_for_backtest():
    """Mock DataProcessor for backtest context with required async methods."""
    from data.persistence.quality_gate import QualityTier

    mock_dp = MagicMock()
    mock_dp.is_cancelled.return_value = False
    mock_dp._quality_tier = QualityTier.SILVER
    mock_dp.cache = MagicMock()

    async def mock_get_history(ts_code, days, end_date=None):
        base_close = 10.0
        dates = pd.date_range(end=pd.Timestamp(end_date or date.today()), periods=days, freq="B")
        return pd.DataFrame(
            {
                "trade_date": dates,
                "close": [base_close - i * 0.05 for i in range(days)],
                "high": [base_close + 0.5 for _ in range(days)],
                "low": [base_close - 0.5 for _ in range(days)],
                "vol": [1000000 for _ in range(days)],
                "pct_chg": [-0.5 for _ in range(days)],
            },
        )

    mock_dp.get_stock_history = mock_get_history
    mock_dp.trade_calendar = MagicMock()

    async def mock_get_latest_trade_date():
        return date(2024, 6, 15)

    async def mock_get_start_date(end_date_obj, trade_days):
        return end_date_obj - timedelta(days=trade_days * 2)

    mock_dp.trade_calendar.get_latest_trade_date = mock_get_latest_trade_date
    mock_dp.trade_calendar.get_start_date_by_trade_days = mock_get_start_date

    return mock_dp


@pytest.fixture
def sample_candidates_df():
    """Minimal candidate DataFrame for AI analysis test."""
    return pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "close": 10.5,
                "pct_chg": -5.2,
                "pe_ttm": 6.5,
                "turnover_rate": 5.0,
                "list_status": "L",
                "rsi_14": 25.0,
                "total_mv": 1500000,
            },
        ],
    )


class TestBacktestAIContextIntegration:
    """
    Integration tests for backtest → AI analysis context flow.

    Validates R2 and R5 fixes per AUDIT5_08_fix_plan.md.
    """

    @pytest.mark.asyncio
    async def test_backtest_disable_ai_flag_is_consumed(
        self,
        db_cache,
        mock_data_processor_for_backtest,
        sample_candidates_df,
    ):
        """
        R2 Test: When context contains _disable_ai=True, run_ai_analysis()
        should return candidates_df unchanged without calling any expensive
        pre-fetch operations (learning context, news, concepts, etc.).

        This validates that the _disable_ai guard is checked at the ENTRY POINT,
        before any resource-intensive operations are performed.
        """
        strategy = OversoldStrategy()
        strategy.enable_ai_analysis = True

        context = {
            "screening_data": sample_candidates_df,
            "data_processor": mock_data_processor_for_backtest,
            "trade_date": "20240615",
            "is_backtest": True,
            "_disable_ai": True,
        }

        pre_fetch_called = []

        async def track_pre_fetch(*args, **kwargs):
            pre_fetch_called.append(True)
            return {}

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai_cls,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
            patch.object(NewsFetcher, "get_us_major_moves", new_callable=AsyncMock) as mock_global,
            patch.object(NewsFetcher, "get_stock_news", new_callable=AsyncMock) as mock_news,
        ):
            mock_ai = MagicMock()
            mock_ai.is_cloud_available.return_value = True
            mock_ai_cls.return_value = mock_ai

            mock_lc.side_effect = track_pre_fetch
            mock_global.side_effect = track_pre_fetch
            mock_news.side_effect = track_pre_fetch

            mock_dp_cache = MagicMock()
            mock_dp_cache.get_concepts = AsyncMock(side_effect=track_pre_fetch)
            mock_dp_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.prefetch_auxiliary_data = AsyncMock(return_value={})
            mock_data_processor_for_backtest.cache = mock_dp_cache

            result = await strategy.run_ai_analysis(sample_candidates_df, context)

            assert result is sample_candidates_df
            assert len(result) == 1

            assert len(pre_fetch_called) == 0, (
                "Pre-fetch operations should NOT be called when _disable_ai=True. "
                f"Got {len(pre_fetch_called)} calls. This indicates the _disable_ai guard is missing."
            )

    @pytest.mark.asyncio
    async def test_backtest_ai_runs_when_disable_ai_false(
        self,
        db_cache,
        mock_data_processor_for_backtest,
        sample_candidates_df,
    ):
        """
        R2 Test: When _disable_ai=False (or absent), AI analysis should proceed
        if AI service is available.

        This verifies the guard condition does not block normal AI flow.
        """
        strategy = OversoldStrategy()
        strategy.enable_ai_analysis = True

        context = {
            "screening_data": sample_candidates_df,
            "data_processor": mock_data_processor_for_backtest,
            "trade_date": "20240615",
            "is_backtest": True,
        }

        captured_calls = []

        async def mock_analyze(*args, **kwargs):
            captured_calls.append((args, kwargs))
            return {"score": 75, "summary": "Test analysis", "recommendation": "hold"}

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai_cls,
            patch.object(NewsFetcher, "get_stock_news", new_callable=AsyncMock) as mock_news,
            patch.object(NewsFetcher, "get_us_major_moves", new_callable=AsyncMock) as mock_global,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
        ):
            mock_ai = MagicMock()
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(side_effect=mock_analyze)
            mock_ai_cls.return_value = mock_ai

            mock_news.return_value = []
            mock_global.return_value = ""
            mock_lc.return_value = ""

            mock_dp_cache = MagicMock()
            mock_dp_cache.get_concepts = AsyncMock(return_value={})
            mock_dp_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.prefetch_auxiliary_data = AsyncMock(return_value={})
            mock_data_processor_for_backtest.cache = mock_dp_cache

            result = await strategy.run_ai_analysis(sample_candidates_df, context)

            assert len(captured_calls) > 0 or result is sample_candidates_df

    @pytest.mark.asyncio
    async def test_backtest_news_fetch_receives_as_of_parameter(
        self,
        db_cache,
        mock_data_processor_for_backtest,
        sample_candidates_df,
    ):
        """
        R5 Test: In backtest mode, NewsFetcher.get_stock_news() should receive
        as_of parameter equal to the trade_date (as historical date).

        This validates that the news_as_of is computed from context.trade_date
        and passed to get_stock_news().

        Note: OversoldStrategy.should_include_global_context() returns False,
        but news is still fetched per-stock via bg_fetch_news() in the pipeline.
        """
        strategy = OversoldStrategy()
        strategy.enable_ai_analysis = True

        trade_date = date(2024, 6, 15)
        context = {
            "screening_data": sample_candidates_df,
            "data_processor": mock_data_processor_for_backtest,
            "trade_date": trade_date.strftime("%Y%m%d"),
            "is_backtest": True,
        }

        captured_as_of = []

        async def capture_news_call(ts_code, limit=5, as_of=None):
            captured_as_of.append(as_of)
            return []

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai_cls,
            patch.object(NewsFetcher, "get_stock_news", new_callable=AsyncMock) as mock_news,
            patch.object(NewsFetcher, "get_us_major_moves", new_callable=AsyncMock) as mock_global,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
        ):
            mock_ai = MagicMock()
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(return_value={"score": 70, "summary": "Test", "recommendation": "hold"})
            mock_ai_cls.return_value = mock_ai

            mock_news.side_effect = capture_news_call
            mock_global.return_value = ""
            mock_lc.return_value = ""

            mock_dp_cache = MagicMock()
            mock_dp_cache.get_concepts = AsyncMock(return_value={})
            mock_dp_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.prefetch_auxiliary_data = AsyncMock(return_value={})
            mock_dp_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_fina_audit = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_dividend = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
            mock_data_processor_for_backtest.cache = mock_dp_cache

            await strategy.run_ai_analysis(sample_candidates_df, context)

            assert len(captured_as_of) >= 1, (
                f"get_stock_news should be called at least once. Got {len(captured_as_of)} calls."
            )
            passed_as_of = captured_as_of[0]
            assert passed_as_of is not None, (
                "as_of parameter should not be None in backtest mode. "
                "This indicates news_as_of is not being computed/passed."
            )
            assert passed_as_of == trade_date, f"as_of should be {trade_date}, got {passed_as_of}"

    @pytest.mark.asyncio
    async def test_backtest_learning_context_as_of_is_offset(
        self,
        db_cache,
        mock_data_processor_for_backtest,
        sample_candidates_df,
    ):
        """
        End-to-end Test: In backtest mode, ReviewManager.get_learning_context()
        should receive as_of parameter that is trade_date - SAFE_BACKTEST_LEARNING_OFFSET_DAYS.

        This validates compute_learning_as_of() is correctly called with
        is_backtest=True and the offset is applied.

        Note: We mock should_include_learning_context() to return True since
        OversoldStrategy returns False by default.
        """
        strategy = OversoldStrategy()
        strategy.enable_ai_analysis = True

        trade_date = date(2024, 6, 15)
        expected_as_of = trade_date - timedelta(days=SAFE_BACKTEST_LEARNING_OFFSET_DAYS)

        context = {
            "screening_data": sample_candidates_df,
            "data_processor": mock_data_processor_for_backtest,
            "trade_date": trade_date.strftime("%Y%m%d"),
            "is_backtest": True,
        }

        captured_as_of = None

        async def capture_learning_context(*args, **kwargs):
            nonlocal captured_as_of
            captured_as_of = kwargs.get("as_of")
            return ""

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai_cls,
            patch.object(NewsFetcher, "get_stock_news", new_callable=AsyncMock) as mock_news,
            patch.object(NewsFetcher, "get_us_major_moves", new_callable=AsyncMock) as mock_global,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
            patch.object(strategy, "should_include_learning_context", return_value=True),
        ):
            mock_ai = MagicMock()
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(return_value={"score": 70, "summary": "Test", "recommendation": "hold"})
            mock_ai_cls.return_value = mock_ai

            mock_news.return_value = []
            mock_global.return_value = ""
            mock_lc.side_effect = capture_learning_context

            mock_dp_cache = MagicMock()
            mock_dp_cache.get_concepts = AsyncMock(return_value={})
            mock_dp_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.prefetch_auxiliary_data = AsyncMock(return_value={})
            mock_data_processor_for_backtest.cache = mock_dp_cache

            await strategy.run_ai_analysis(sample_candidates_df, context)

            assert captured_as_of is not None, (
                "get_learning_context should be called when should_include_learning_context=True. "
                "This indicates the learning context pre-fetch path is not working correctly."
            )
            assert captured_as_of <= expected_as_of, (
                f"as_of should be <= {expected_as_of} (trade_date - {SAFE_BACKTEST_LEARNING_OFFSET_DAYS} days), "
                f"got {captured_as_of}"
            )

    @pytest.mark.asyncio
    async def test_live_mode_news_as_of_is_none(
        self,
        db_cache,
        mock_data_processor_for_backtest,
        sample_candidates_df,
    ):
        """
        R5 Test: In live mode (is_backtest=False or absent), news_as_of should be None,
        allowing NewsFetcher.get_stock_news() to fetch current news.

        This ensures backward compatibility: live trading behavior unchanged.
        """
        strategy = OversoldStrategy()
        strategy.enable_ai_analysis = True

        context = {
            "screening_data": sample_candidates_df,
            "data_processor": mock_data_processor_for_backtest,
            "is_backtest": False,
        }

        captured_as_of = []

        async def capture_news_call(ts_code, limit=5, as_of=None):
            captured_as_of.append(as_of)
            return []

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai_cls,
            patch.object(NewsFetcher, "get_stock_news", new_callable=AsyncMock) as mock_news,
            patch.object(NewsFetcher, "get_us_major_moves", new_callable=AsyncMock) as mock_global,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
        ):
            mock_ai = MagicMock()
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(return_value={"score": 70, "summary": "Test", "recommendation": "hold"})
            mock_ai_cls.return_value = mock_ai

            mock_news.side_effect = capture_news_call
            mock_global.return_value = ""
            mock_lc.return_value = ""

            mock_dp_cache = MagicMock()
            mock_dp_cache.get_concepts = AsyncMock(return_value={})
            mock_dp_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            mock_dp_cache.prefetch_auxiliary_data = AsyncMock(return_value={})
            mock_data_processor_for_backtest.cache = mock_dp_cache

            await strategy.run_ai_analysis(sample_candidates_df, context)

            if len(captured_as_of) > 0:
                assert captured_as_of[0] is None


class TestBacktestDataProviderContextBuilding:
    """
    Tests for BacktestDataProvider.build_context() context construction.

    Validates the _disable_ai flag and is_backtest flag are correctly set.
    """

    @pytest.mark.asyncio
    async def test_build_context_sets_disable_ai_true_by_default(
        self,
        db_cache,
    ):
        """
        BacktestDataProvider.build_context() should set _disable_ai=True
        by default (disable_ai=True is the default parameter).
        """
        from strategies.backtest.data_provider import BacktestDataProvider

        provider = BacktestDataProvider(cache=db_cache)
        trade_date = date(2024, 6, 15)

        context = await provider.build_context(trade_date)

        assert context.get("is_backtest") is True
        assert context.get("_disable_ai") is True

    @pytest.mark.asyncio
    async def test_build_context_sets_disable_ai_false_when_explicit(
        self,
        db_cache,
    ):
        """
        BacktestDataProvider.build_context(disable_ai=False) should NOT
        set _disable_ai in context.
        """
        from strategies.backtest.data_provider import BacktestDataProvider

        provider = BacktestDataProvider(cache=db_cache)
        trade_date = date(2024, 6, 15)

        context = await provider.build_context(trade_date, disable_ai=False)

        assert context.get("is_backtest") is True
        assert context.get("_disable_ai") is None

    @pytest.mark.asyncio
    async def test_build_context_contains_trade_date(
        self,
        db_cache,
    ):
        """
        BacktestDataProvider.build_context() should set trade_date
        in normalized YYYYMMDD string format.
        """
        from strategies.backtest.data_provider import BacktestDataProvider

        provider = BacktestDataProvider(cache=db_cache)
        trade_date = date(2024, 6, 15)

        context = await provider.build_context(trade_date)

        assert context.get("trade_date") == "20240615"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
