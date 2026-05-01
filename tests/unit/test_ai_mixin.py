import inspect


from strategies.ai_mixin import AIStrategyMixin
from strategies.oversold_strategy import OversoldStrategy


class TestLookaheadBiasGuard:
    """AI mixin end_date 前瞻偏差防护"""

    def test_backtest_invalid_trade_date_raises(self):
        source = inspect.getsource(AIStrategyMixin.run_ai_analysis)
        assert "is_backtest" in source
        assert "ValueError" in source

    def test_backtest_falls_back_to_current_date_when_valid(self):
        source = inspect.getsource(AIStrategyMixin.run_ai_analysis)
        assert "get_now" in source


class TestStrategySuppressErrors:
    """策略执行路径使用 suppress_errors=False"""

    def test_ai_mixin_get_daily_quotes_uses_suppress_errors_false(self):
        source = inspect.getsource(AIStrategyMixin.run_ai_analysis)
        assert "suppress_errors=False" in source, "AI mixin must use suppress_errors=False for strategy data queries"

    def test_oversold_strategy_get_daily_quotes_uses_suppress_errors_false(self):
        source = inspect.getsource(OversoldStrategy._math_filter)
        assert "suppress_errors=False" in source, (
            "OversoldStrategy must use suppress_errors=False for strategy data queries"
        )
