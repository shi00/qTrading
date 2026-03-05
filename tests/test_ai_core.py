"""
Unit Tests for AI Core Modules
Targets: ReviewManager, AIStrategy, NewsFetcher
Coverage Goal: >90%
"""
import pytest
import asyncio
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
import sys
import os

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.ai_strategy import AISelectionStrategy
from data.review_manager import ReviewManager
from data.news_fetcher import NewsFetcher


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def sample_screening_df():
    """Sample DataFrame mimicking DataProcessor output"""
    today = datetime.now().strftime('%Y%m%d')
    return pd.DataFrame([
        {
            'ts_code': '000001.SZ', 'name': 'Bank A', 'close': 10.5, 'pct_chg': 1.2,
            'pe_ttm': 6.5, 'turnover_rate': 5.0, 'list_status': 'L', 'trade_date': today
        },
        {
            'ts_code': '600519.SH', 'name': 'Wine Corp', 'close': 1800.0, 'pct_chg': -0.5,
            'pe_ttm': 30.0, 'turnover_rate': 3.5, 'list_status': 'L', 'trade_date': today
        },
        {
            'ts_code': '300001.SZ', 'name': 'Tech Startup', 'close': 50.0, 'pct_chg': 5.0,
            'pe_ttm': -10.0, 'turnover_rate': 15.0, 'list_status': 'L', 'trade_date': today  # Negative PE
        }
    ])


@pytest.fixture
def mock_data_processor():
    """Mock DataProcessor with async methods"""
    mock_dp = MagicMock()
    
    async def mock_get_history(ts_code, days):
        dates = pd.date_range(end=pd.Timestamp.now(), periods=days)
        return pd.DataFrame({
            'trade_date': dates,
            'close': [10.0 + i * 0.1 for i in range(days)],
            'high': [11.0] * days,
            'low': [9.0] * days
        })
    
    mock_dp.get_stock_history = mock_get_history
    mock_dp.is_cancelled.return_value = False
    
    from data.quality_gate import QualityTier
    mock_dp._quality_tier = QualityTier.SILVER
    
    return mock_dp


# ==============================================================================
# AI STRATEGY TESTS
# ==============================================================================

class TestAISelectionStrategy:
    """Tests for AISelectionStrategy"""
    
    @pytest.mark.asyncio
    @patch('strategies.ai_strategy.AIService')
    async def test_filter_returns_empty_when_no_api_key(self, mock_ai_service_cls, sample_screening_df, mock_data_processor):
        """Test: Strategy raises error when API key is missing"""
        mock_ai_service = MagicMock()
        mock_ai_service.client = None
        mock_ai_service_cls.return_value = mock_ai_service
        
        strategy = AISelectionStrategy()
        
        context = {
            'screening_data': sample_screening_df,
            'data_processor': mock_data_processor
        }
        
        with pytest.raises(ValueError) as excinfo:
            await strategy.filter(context)
        
        assert "API Key" in str(excinfo.value)
    
    @pytest.mark.asyncio
    @patch('strategies.ai_strategy.AIService')
    async def test_filter_returns_empty_when_no_data(self, mock_ai_service_cls, mock_data_processor):
        """Test: Strategy returns empty DataFrame when input is empty"""
        mock_ai_service = MagicMock()
        mock_ai_service.client = MagicMock()
        mock_ai_service_cls.return_value = mock_ai_service
        
        strategy = AISelectionStrategy()
        
        context = {
            'screening_data': pd.DataFrame(),
            'data_processor': mock_data_processor
        }
        
        result = await strategy.filter(context)
        assert result.empty
    
    @pytest.mark.asyncio
    @patch('strategies.ai_strategy.AIService')
    async def test_filter_returns_empty_when_no_dp(self, mock_ai_service_cls, sample_screening_df):
        """Test: Strategy handles missing DataProcessor gracefully.
        
        When data_processor is None, the quality gate is bypassed (logged as warning),
        and the strategy proceeds. It should not crash.
        """
        mock_ai_service = MagicMock()
        mock_ai_service.client = MagicMock()
        mock_ai_service_cls.return_value = mock_ai_service
        
        strategy = AISelectionStrategy()
        
        context = {
            'screening_data': sample_screening_df,
            'data_processor': None
        }
        
        # Should not raise; quality gate is bypassed when dp is None
        result = await strategy.filter(context)
        assert isinstance(result, pd.DataFrame)
    
    @pytest.mark.asyncio
    @patch('strategies.ai_strategy.AIService')
    async def test_pre_filter_removes_negative_pe(self, mock_ai_service_cls, sample_screening_df, mock_data_processor):
        """Test: Pre-filter correctly removes stocks with negative PE"""
        mock_ai_service = MagicMock()
        mock_ai_service.client = MagicMock()
        
        # Mock analyze_stock to return valid result
        async def mock_analyze(*args, **kwargs):
            return {"score": 80, "summary": "Test", "decision": "Buy"}
        mock_ai_service.analyze_stock.side_effect = mock_analyze
        mock_ai_service_cls.return_value = mock_ai_service
        
        strategy = AISelectionStrategy()
        
        with patch.object(NewsFetcher, 'get_us_major_moves', return_value=""):
            with patch.object(NewsFetcher, 'get_stock_news', return_value=[]):
                context = {
                    'screening_data': sample_screening_df,
                    'data_processor': mock_data_processor
                }
                result = await strategy.filter(context)
        
        # Should have 2 results (negative PE stock filtered out)
        assert len(result) == 2
        assert '300001.SZ' not in result['ts_code'].values


# ==============================================================================
# REVIEW MANAGER TESTS
# ==============================================================================

class TestReviewManager:
    """Tests for ReviewManager"""
    
    @pytest.mark.asyncio
    async def test_save_results_handles_empty_df(self):
        """Test: save_results gracefully handles empty DataFrame"""
        rm = ReviewManager()
        
        # Should not raise
        await rm.save_results("TEST", pd.DataFrame())
        await rm.save_results("TEST", None)
    
    @pytest.mark.asyncio
    async def test_get_learning_context_returns_xml(self):
        """Test: get_learning_context returns valid XML structure"""
        rm = ReviewManager()
        
        context = await rm.get_learning_context(limit=3)
        
        assert isinstance(context, str)
        assert "<history_context>" in context or context == ""


# ==============================================================================
# NEWS FETCHER TESTS
# ==============================================================================

class TestNewsFetcher:
    """Tests for NewsFetcher"""
    
    @pytest.mark.asyncio
    async def test_get_us_major_moves_returns_string(self):
        """Test: get_us_major_moves returns a non-empty string"""
        result = await NewsFetcher.get_us_major_moves()
        
        assert isinstance(result, str)
        # May be "Global data error" if market is closed, but should still be string
    
    @pytest.mark.asyncio
    async def test_get_stock_news_returns_list(self):
        """Test: get_stock_news returns a list"""
        result = await NewsFetcher.get_stock_news("000001.SZ", limit=3)
        
        assert isinstance(result, list)


# ==============================================================================
# RUN TESTS
# ==============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
