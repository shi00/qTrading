"""
Unit tests for ui/i18n.py.
Covers strategy name translation functionality.
"""

from unittest.mock import patch

from ui.i18n import translate_strategy_name


class TestTranslateStrategyName:
    """Tests for translate_strategy_name function."""

    def test_translate_none_returns_none(self):
        """Test translating None returns None."""
        result = translate_strategy_name(None)
        assert result is None

    def test_translate_empty_string_returns_empty(self):
        """Test translating empty string returns empty."""
        result = translate_strategy_name("")
        assert result == ""

    def test_translate_known_strategy_id(self):
        """Test translating a known strategy ID."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "AI Nightly Strategy"
            result = translate_strategy_name("AI_Auto_Nightly")
        assert result == "AI Nightly Strategy"

    def test_translate_known_strategy_name_chinese(self):
        """Test translating a known Chinese strategy name."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Value Investing"
            result = translate_strategy_name("价值投资")
        assert result == "Value Investing"

    def test_translate_known_strategy_name_english(self):
        """Test translating a known English strategy name."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Value Investing"
            result = translate_strategy_name("Value Investing")
        assert result == "Value Investing"

    def test_translate_unknown_strategy_returns_original(self):
        """Test translating unknown strategy returns original."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.side_effect = lambda x: x  # Return key as-is
            result = translate_strategy_name("Unknown Strategy")
        assert result == "Unknown Strategy"

    def test_translate_all_known_strategy_ids(self):
        """Test translating all known strategy IDs in the map."""
        test_cases = [
            ("AI_Auto_Nightly", "strategy_ai_nightly_name"),
            ("AI 深度精选 (Beta)", "strategy_ai_active_name"),
            ("AI Deep Dive (Beta)", "strategy_ai_active_name"),
            ("价值投资", "strategy_value_name"),
            ("Value Investing", "strategy_value_name"),
            ("高成长策略", "strategy_growth_name"),
            ("高股息策略", "strategy_dividend_name"),
            ("技术突破", "strategy_tech_breakout_name"),
            ("北向持股", "strategy_northbound_holding_name"),
            ("北向净流入", "strategy_northbound_flow_name"),
            ("超跌反弹", "strategy_oversold_name"),
            ("龙虎榜机构", "strategy_institutional_name"),
            ("筹码集中 (暂不可用)", "strategy_chips_name"),
            ("大宗交易", "strategy_block_trade_name"),
            ("现金流优质", "strategy_cashflow_name"),
            ("大盘低估", "strategy_large_pe_name"),
        ]

        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Translated"
            for strategy_name, _ in test_cases:
                result = translate_strategy_name(strategy_name)
                assert result == "Translated"

    def test_translate_returns_original_when_i18n_fails(self):
        """Test that original name is returned if I18n lookup fails."""
        # Test that non-mapped names pass through
        result = translate_strategy_name("Unknown Strategy Name")
        assert result == "Unknown Strategy Name"
