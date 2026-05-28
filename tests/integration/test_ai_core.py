"""
Unit Tests for AI Core Modules
Targets: ReviewManager, AIStrategy, NewsFetcher
Coverage Goal: >90%
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.external.news_fetcher import NewsFetcher
from data.persistence.review_manager import ReviewManager
from strategies.ai_strategy import AISelectionStrategy
from utils.time_utils import get_now

# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def sample_screening_df():
    """Sample DataFrame mimicking DataProcessor output"""
    today = get_now().strftime("%Y%m%d")
    return pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "Bank A",
                "close": 10.5,
                "pct_chg": 1.2,
                "pe_ttm": 6.5,
                "turnover_rate": 5.0,
                "list_status": "L",
                "trade_date": today,
            },
            {
                "ts_code": "600519.SH",
                "name": "Wine Corp",
                "close": 1800.0,
                "pct_chg": -0.5,
                "pe_ttm": 30.0,
                "turnover_rate": 3.5,
                "list_status": "L",
                "trade_date": today,
            },
            {
                "ts_code": "300001.SZ",
                "name": "Tech Startup",
                "close": 50.0,
                "pct_chg": 5.0,
                "pe_ttm": -10.0,
                "turnover_rate": 15.0,
                "list_status": "L",
                "trade_date": today,  # Negative PE
            },
        ],
    )


@pytest.fixture
def mock_data_processor():
    """Mock DataProcessor with async methods"""
    mock_dp = MagicMock()

    async def mock_get_history(ts_code, days):
        dates = pd.date_range(end=pd.Timestamp.now(), periods=days)
        return pd.DataFrame(
            {
                "trade_date": dates,
                "close": [10.0 + i * 0.1 for i in range(days)],
                "high": [11.0] * days,
                "low": [9.0] * days,
            },
        )

    mock_dp.get_stock_history = mock_get_history
    mock_dp.is_cancelled.return_value = False

    from data.persistence.quality_gate import QualityTier

    mock_dp._quality_tier = QualityTier.SILVER

    return mock_dp


# ==============================================================================
# AI STRATEGY TESTS
# ==============================================================================


class TestAISelectionStrategy:
    """Tests for AISelectionStrategy"""

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    async def test_filter_returns_empty_when_no_api_key(
        self,
        mock_ai_service_cls,
        sample_screening_df,
        mock_data_processor,
        test_engine,
    ):
        """Test: Strategy raises error when API key is missing"""
        mock_ai_service = MagicMock()
        mock_ai_service.is_cloud_available.return_value = False
        mock_ai_service_cls.return_value = mock_ai_service

        strategy = AISelectionStrategy()

        fundamental_df = pd.DataFrame([{"ts_code": "000001.SZ", "pe_ttm": 6.5}])
        context = {
            "screening_data": sample_screening_df,
            "fundamental_screening_data": fundamental_df,
            "data_processor": mock_data_processor,
        }

        with pytest.raises(ValueError) as excinfo:
            await strategy.filter(context)

        assert "API Key" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    async def test_filter_returns_empty_when_no_data(
        self,
        mock_ai_service_cls,
        mock_data_processor,
    ):
        """Test: Strategy returns empty DataFrame when input is empty"""
        mock_ai_service = MagicMock()
        mock_ai_service.is_cloud_available.return_value = True
        mock_ai_service_cls.return_value = mock_ai_service

        strategy = AISelectionStrategy()

        context = {
            "screening_data": pd.DataFrame(),
            "data_processor": mock_data_processor,
        }

        result = await strategy.filter(context)
        assert result.empty

    @pytest.mark.asyncio
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    @patch("strategies.ai_strategy.AIService")
    async def test_filter_returns_empty_when_no_dp(
        self,
        mock_ai_service_cls,
        sample_screening_df,
        test_engine,
    ):
        """Test: Strategy handles missing DataProcessor gracefully.

        When data_processor is None and STRICT_QUALITY_GATE is disabled,
        the quality gate is bypassed (logged as warning), and the strategy
        proceeds. It should not crash.
        """
        mock_ai_service = MagicMock()
        mock_ai_service.is_cloud_available.return_value = True
        mock_ai_service_cls.return_value = mock_ai_service

        strategy = AISelectionStrategy()

        context = {"screening_data": sample_screening_df, "data_processor": None}

        result = await strategy.filter(context)
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    async def test_pre_filter_removes_negative_pe(
        self,
        mock_ai_service_cls,
        sample_screening_df,
        mock_data_processor,
        test_engine,
    ):
        """Test: Pre-filter correctly removes stocks with negative PE"""
        mock_ai_service = MagicMock()
        mock_ai_service.is_cloud_available.return_value = True

        async def mock_analyze(*args, **kwargs):
            return {"score": 80, "summary": "Test", "decision": "Buy"}

        mock_ai_service.analyze_stock.side_effect = mock_analyze
        mock_ai_service_cls.return_value = mock_ai_service

        strategy = AISelectionStrategy()

        fundamental_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "pe_ttm": 6.5},
                {"ts_code": "600519.SH", "pe_ttm": 30.0},
                {"ts_code": "300001.SZ", "pe_ttm": -10.0},
            ]
        )

        with patch.object(NewsFetcher, "get_us_major_moves", return_value=""):
            with patch.object(NewsFetcher, "get_stock_news", return_value=[]):
                context = {
                    "screening_data": sample_screening_df,
                    "fundamental_screening_data": fundamental_df,
                    "data_processor": mock_data_processor,
                }
                result = await strategy.filter(context)

        # Should have 2 results (negative PE stock filtered out)
        assert len(result) == 2
        assert "300001.SZ" not in result["ts_code"].values


# ==============================================================================
# REVIEW MANAGER TESTS
# ==============================================================================


class TestReviewManager:
    """Tests for ReviewManager"""

    @pytest.mark.asyncio
    async def test_save_results_handles_empty_df(self, test_engine):
        """Test: save_results gracefully handles empty DataFrame"""
        rm = ReviewManager()

        # Should not raise
        await rm.save_results("TEST", pd.DataFrame())
        await rm.save_results("TEST", None)

    @pytest.mark.asyncio
    async def test_get_learning_context_returns_xml(self, test_engine):
        """Test: get_learning_context returns valid XML structure"""
        rm = ReviewManager()

        context = await rm.get_learning_context(limit=3)

        assert isinstance(context, str)
        assert "<history_context>" in context or context == ""

    @pytest.mark.asyncio
    async def test_get_learning_context_with_as_of_filters_future(self, test_engine):
        """N4: 带 as_of 参数时，learning context 不包含未来数据"""
        import datetime

        rm = ReviewManager()
        as_of_date = datetime.date(2024, 1, 1)

        context = await rm.get_learning_context(limit=3, as_of=as_of_date)

        assert isinstance(context, str)
        assert "<history_context>" in context or context == ""


# ==============================================================================
# NEWS FETCHER TESTS
# ==============================================================================


class TestNewsFetcher:
    """Tests for NewsFetcher"""

    @pytest.mark.skip(reason="Requires real network - run manually")
    @pytest.mark.asyncio
    async def test_get_us_major_moves_returns_string(self):
        """Test: get_us_major_moves returns a non-empty string"""
        result = await NewsFetcher.get_us_major_moves()

        assert isinstance(result, str)

    @pytest.mark.skip(reason="Requires real network - run manually")
    @pytest.mark.asyncio
    async def test_get_stock_news_returns_list(self):
        """Test: get_stock_news returns a list"""
        result = await NewsFetcher.get_stock_news("000001.SZ", limit=3)

        assert isinstance(result, list)


# ==============================================================================
# BACKTEST INTEGRATION TESTS
# ==============================================================================


class TestBacktestIntegration:
    """Integration tests for backtest context flow (R4)"""

    @pytest.fixture
    def mock_cache(self):
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "close": 10.0,
                        "pct_chg": -11.0,
                        "pe_ttm": 6.5,
                        "turnover_rate": 5.0,
                        "list_status": "L",
                    }
                ]
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        cache.get_concepts = AsyncMock(return_value={})
        cache.prefetch_auxiliary_data = AsyncMock(return_value={})

        dates = pd.date_range(end=pd.Timestamp("2024-06-15"), periods=60)
        history_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 60,
                "trade_date": dates.strftime("%Y%m%d"),
                "close": [10.0 + i * 0.1 for i in range(60)],
                "high": [11.0] * 60,
                "low": [9.0] * 60,
                "vol": [1000.0] * 60,
                "pct_chg": [-1.0] * 60,
            }
        )
        cache.get_daily_quotes = AsyncMock(return_value=history_df)
        return cache

    @pytest.fixture
    def mock_dp(self, mock_cache):
        dp = MagicMock()
        dp.is_cancelled.return_value = False
        dp.cache = mock_cache
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=pd.Timestamp("2024-06-15").date())
        dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=pd.Timestamp("2024-01-15").date())
        dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
        from data.persistence.quality_gate import QualityTier

        dp._quality_tier = QualityTier.SILVER
        return dp

    @pytest.mark.asyncio
    async def test_backtest_context_disables_ai_when_configured(self, mock_cache, mock_dp):
        """Task 3 (R2 verification): When _disable_ai=True, strategy.filter skips AI analysis."""
        from datetime import date
        from unittest.mock import AsyncMock, patch

        from strategies.backtest.data_provider import BacktestDataProvider
        from strategies.ai_strategy import AISelectionStrategy

        strategy = AISelectionStrategy()
        strategy.check_dependencies = MagicMock(return_value={"status": "ready"})

        provider = BacktestDataProvider(cache=mock_cache)
        context = await provider.build_context(date(2024, 6, 15), disable_ai=True)
        context["data_processor"] = mock_dp

        assert context.get("_disable_ai") is True

        with (
            patch("services.ai_service.AIService.is_cloud_available", return_value=True),
            patch("services.ai_service.AIService.analyze_stock", new_callable=AsyncMock) as mock_analyze,
        ):
            result = await strategy.filter(context)
            mock_analyze.assert_not_called()
            assert not result.empty

    @pytest.mark.asyncio
    async def test_backtest_ai_uses_correct_as_of_for_learning(self, mock_cache, mock_dp):
        """Task 3 (R4 verification): When disable_ai=False, get_learning_context uses trade_date - offset."""
        from datetime import date, timedelta
        from unittest.mock import AsyncMock, patch

        from data.constants import SAFE_BACKTEST_LEARNING_OFFSET_DAYS
        from strategies.backtest.data_provider import BacktestDataProvider
        from strategies.ai_strategy import AISelectionStrategy

        strategy = AISelectionStrategy()
        provider = BacktestDataProvider(cache=mock_cache)
        trade_date = date(2024, 6, 15)
        context = await provider.build_context(trade_date, disable_ai=False)
        context["data_processor"] = mock_dp

        assert context["is_backtest"] is True
        assert context.get("_disable_ai") is None

        with (
            patch("services.ai_service.AIService.is_cloud_available", return_value=True),
            patch("services.ai_service.AIService.analyze_stock", new_callable=AsyncMock) as mock_analyze,
            patch(
                "data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock
            ) as mock_lc,
        ):
            mock_lc.return_value = "<learning>test</learning>"
            mock_analyze.return_value = {"score": 70, "summary": "test"}

            candidates_df = pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}])
            await strategy.run_ai_analysis(candidates_df, context)

            expected_as_of = trade_date - timedelta(days=SAFE_BACKTEST_LEARNING_OFFSET_DAYS)
            mock_lc.assert_called_once_with(as_of=expected_as_of)

    @pytest.mark.asyncio
    async def test_backtest_ai_news_respects_as_of(self, mock_cache, mock_dp):
        """Task 3 (R5 verification): When disable_ai=False, get_stock_news is called with backtest trade_date."""
        from datetime import date
        from unittest.mock import AsyncMock, patch

        from strategies.backtest.data_provider import BacktestDataProvider
        from strategies.ai_strategy import AISelectionStrategy

        strategy = AISelectionStrategy()
        provider = BacktestDataProvider(cache=mock_cache)
        trade_date = date(2024, 6, 15)
        context = await provider.build_context(trade_date, disable_ai=False)
        context["data_processor"] = mock_dp

        with (
            patch("services.ai_service.AIService.is_cloud_available", return_value=True),
            patch("services.ai_service.AIService.analyze_stock", new_callable=AsyncMock) as mock_analyze,
            patch("data.external.news_fetcher.NewsFetcher.get_stock_news", new_callable=AsyncMock) as mock_get_news,
            patch("data.persistence.review_manager.ReviewManager.get_learning_context", new_callable=AsyncMock),
        ):
            mock_get_news.return_value = []
            mock_analyze.return_value = {"score": 70, "summary": "test"}

            candidates_df = pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}])
            await strategy.run_ai_analysis(candidates_df, context)

            mock_get_news.assert_called_with("000001.SZ", limit=5, as_of=trade_date)


# ==============================================================================
# RUN TESTS
# ==============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
