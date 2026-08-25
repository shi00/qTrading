# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from data.persistence.quality_gate import QualityGateError, QualityTier
from strategies.ai_strategy import AISelectionStrategy

pytestmark = pytest.mark.unit


def _make_dp(tier=QualityTier.GOLD):
    dp = MagicMock()
    dp._quality_tier = tier
    return dp


class TestAISelectionStrategyQualityGate:
    """P1-001: 验证 @require_quality 装饰器替代手动 _check_tier 调用。"""

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_quality_gate_raises_when_tier_below_silver(self, mock_ch):
        """tier=BRONZE 低于 SILVER 要求时，@require_quality 抛 QualityGateError。"""
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        context = {"data_processor": _make_dp(tier=QualityTier.BRONZE)}
        with pytest.raises(QualityGateError, match="SILVER|too low"):
            await s.filter(context)

    @pytest.mark.asyncio
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_quality_gate_passes_when_tier_meets_silver(self, mock_ch):
        """tier=SILVER 满足要求时，@require_quality 放行（后续空 context 返回空 df）。"""
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        context = {"data_processor": _make_dp(tier=QualityTier.SILVER)}
        result = await s.filter(context)
        assert result.empty


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
    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_none_context(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        s = AISelectionStrategy()
        result = await s.filter({"data_processor": _make_dp()})
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_dependencies_unready(self, mock_ch, mock_ai_cls):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
        s = AISelectionStrategy()
        context = {"data_processor": _make_dp()}
        result = await s.filter(context)
        assert result.empty

    @pytest.mark.asyncio
    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_api_not_configured(self, mock_ch, mock_ai_cls):
        """Task 3.4: AI 不可用时不再 raise ValueError, 统一返回量化初筛结果.

        ai_mixin.run_ai_analysis 检查 is_cloud_available() 后返回原始 candidates,
        on_progress 提示 "ai_not_configured" (与 ai_mixin 路径统一).
        """
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
        on_progress = MagicMock()
        context = {
            "screening_data": df,
            "fundamental_screening_data": df,
            "data_processor": _make_dp(),
            "on_progress": on_progress,
        }
        result = await s.filter(context)
        # Task 3.4: 返回量化初筛结果 (不 raise), 与 ai_mixin 路径统一
        assert len(result) == 1
        assert result.iloc[0]["ts_code"] == "000001.SZ"
        # on_progress 提示 "ai_not_configured" (出现在任意一次调用中)
        # D7: progress_callback 透传 Message(key) 而非已翻译字符串, 断言 Message 对象
        from ui.viewmodels import Message

        expected_msg = Message("ai_not_configured")
        messages = [
            (c.args[2] if len(c.args) >= 3 else c.kwargs.get("message", "")) for c in on_progress.call_args_list
        ]
        assert expected_msg in messages, f"ai_not_configured not in on_progress calls: {messages}"

    @pytest.mark.asyncio
    @patch("strategies.ai_mixin.AIService")
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
    @patch("strategies.ai_mixin.AIService")
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
    @patch("strategies.ai_strategy.ConfigHandler")
    async def test_with_candidates(self, mock_ch, mock_mixin_ai):
        mock_ch.get_ai_max_candidates.return_value = 10
        mock_ch.get_strategy_min_turnover.return_value = 1.0
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
    @patch("strategies.ai_mixin.AIService")
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
