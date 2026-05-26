import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from data.persistence.quality_gate import QualityTier
from strategies.ai_strategy import AISelectionStrategy


def _make_dp(tier=QualityTier.GOLD):
    dp = MagicMock()
    dp._quality_tier = tier
    return dp


class TestAISelectionStrategyInit:
    @patch("strategies.ai_strategy.ConfigHandler")
    def test_init(self, mock_ch):
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        assert s.limit == 10

    @patch("strategies.ai_strategy.ConfigHandler")
    def test_required_history_days(self, mock_ch):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_init_history_years.return_value = 3
        s = AISelectionStrategy()
        assert s.required_history_days == 750


class TestAISelectionStrategyFilter:
    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_none_context(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        result = await s.filter({"data_processor": _make_dp()})
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_dependencies_unready(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        s = AISelectionStrategy()
        context = {"data_processor": _make_dp()}
        result = await s.filter(context)
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_api_not_configured(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        mock_ai_instance = MagicMock()
        mock_ai_instance.is_cloud_available.return_value = False
        mock_ai_cls.return_value = mock_ai_instance
        s = AISelectionStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "pe_ttm": [15.0],
                "turnover_rate": [5.0],
                "list_status": ["L"],
            }
        )
        context = {
            "screening_data": df,
            "fundamental_screening_data": df,
            "data_processor": _make_dp(),
        }
        with pytest.raises(ValueError, match="API Key"):
            await s.filter(context)

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_empty_data(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        mock_ai_instance = MagicMock()
        mock_ai_instance.is_cloud_available.return_value = True
        mock_ai_cls.return_value = mock_ai_instance
        s = AISelectionStrategy()
        context = {
            "screening_data": pd.DataFrame(),
            "fundamental_screening_data": pd.DataFrame(),
            "data_processor": _make_dp(),
        }
        result = await s.filter(context)
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_no_candidates_after_filter(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        mock_ai_instance = MagicMock()
        mock_ai_instance.is_cloud_available.return_value = True
        mock_ai_cls.return_value = mock_ai_instance
        s = AISelectionStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "pe_ttm": [-5.0],
                "turnover_rate": [0.5],
                "list_status": ["L"],
            }
        )
        context = {
            "screening_data": df,
            "fundamental_screening_data": df,
            "data_processor": _make_dp(),
        }
        result = await s.filter(context)
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_with_candidates(self, mock_ch, mock_ai_cls, mock_mixin_ai):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        mock_ai_instance = MagicMock()
        mock_ai_instance.is_cloud_available.return_value = True
        mock_ai_cls.return_value = mock_ai_instance
        mock_mixin_ai.return_value.is_cloud_available.return_value = False
        s = AISelectionStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["测试"],
                "pe_ttm": [15.0],
                "turnover_rate": [5.0],
                "list_status": ["L"],
                "pct_chg": [2.0],
            }
        )
        context = {
            "screening_data": df,
            "fundamental_screening_data": df,
            "data_processor": _make_dp(),
        }
        result = await s.filter(context)
        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_legacy_data_key(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        mock_ai_instance = MagicMock()
        mock_ai_instance.is_cloud_available.return_value = True
        mock_ai_cls.return_value = mock_ai_instance
        s = AISelectionStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "pe_ttm": [-5.0],
                "turnover_rate": [0.5],
                "list_status": ["L"],
            }
        )
        context = {"data": df, "data_processor": _make_dp()}
        result = await s.filter(context)
        assert result.empty


class TestAISelectionStrategyGetAiContext:
    @patch("strategies.ai_strategy.ConfigHandler")
    def test_get_ai_context(self, mock_ch):
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        row = {"turnover_rate": 5.0, "pe_ttm": 15.0, "pct_chg": 2.0}
        result = s.get_ai_context(row)
        assert "5.0" in result
        assert "15.0" in result
