"""BacktestConfigPanel 测试（声明式 V1）。

测试策略：
1. 纯函数 ``_get_config_from_state`` 单测（类型转换/默认值/stamp_duty 分段逻辑）
2. 契约守护测试（grep 命令式禁止模式 = 0）

声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化（参考 3.2.1-3.2.4 范式）。
"""

from datetime import date, timedelta

import pytest

from ui.components.backtest.backtest_config_panel import _get_config_from_state

pytestmark = pytest.mark.unit


class TestGetConfigFromState:
    """_get_config_from_state 纯函数单测。"""

    @pytest.fixture
    def today(self) -> date:
        return date.today()

    @pytest.fixture
    def one_year_ago(self, today: date) -> date:
        return today - timedelta(days=365)

    def test_default_values(self, today: date, one_year_ago: date) -> None:
        """默认值：initial_capital=1000000, max_positions=50, rebalance=signal, commission=3‱, slippage=5bps。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["start_date"] == one_year_ago
        assert config["end_date"] == today
        assert config["initial_capital"] == 1_000_000.0
        assert config["max_position_count"] == 50
        assert config["rebalance_freq"] == "signal"
        assert config["commission_rate"] == 3 / 10000
        assert config["stamp_duty_rate"] is None  # auto=True
        assert config["slippage_bps"] == 5.0

    def test_custom_values(self, today: date, one_year_ago: date) -> None:
        """自定义值。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="500000",
            rebalance_freq="weekly",
            max_positions_str="30",
            commission=5.0,
            stamp_duty_auto=False,
            stamp_duty_rate=2.0,
            slippage=10.0,
        )
        assert config["initial_capital"] == 500_000.0
        assert config["max_position_count"] == 30
        assert config["rebalance_freq"] == "weekly"
        assert config["commission_rate"] == 5 / 10000
        assert config["stamp_duty_rate"] == 2 / 1000  # ‰ → 小数
        assert config["slippage_bps"] == 10.0

    def test_invalid_initial_capital_falls_back(self, today: date, one_year_ago: date) -> None:
        """非法 initial_capital 兜底 1000000。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="invalid_number",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["initial_capital"] == 1_000_000.0

    def test_invalid_max_positions_falls_back(self, today: date, one_year_ago: date) -> None:
        """非法 max_positions 兜底 50。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="invalid_number",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["max_position_count"] == 50

    def test_empty_initial_capital_falls_back(self, today: date, one_year_ago: date) -> None:
        """空 initial_capital 兜底 1000000。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["initial_capital"] == 1_000_000.0

    def test_empty_max_positions_falls_back(self, today: date, one_year_ago: date) -> None:
        """空 max_positions 兜底 50。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["max_position_count"] == 50

    def test_slider_zero_values_respected(self, today: date, one_year_ago: date) -> None:
        """Slider 0 值被尊重（资金路径精度）：commission=0 → 0.0，slippage=0 → 0.0，stamp_duty_auto=False + slider=0 → 0.0。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=0,
            stamp_duty_auto=False,
            stamp_duty_rate=0,
            slippage=0,
        )
        assert config["commission_rate"] == 0.0  # 0 ‱ → 0.0（免佣金）
        assert config["stamp_duty_rate"] == 0.0  # 0 ‰ → 0.0（0 印花税）
        assert config["slippage_bps"] == 0.0  # 0 bps（无滑点）

    def test_none_rebalance_freq_falls_back(self, today: date, one_year_ago: date) -> None:
        """rebalance_freq=None 兜底 signal。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=0.5,
            slippage=5.0,
        )
        assert config["rebalance_freq"] == "signal"

    def test_stamp_duty_auto_true_returns_none(self, today: date, one_year_ago: date) -> None:
        """stamp_duty_auto=True → stamp_duty_rate=None。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=True,
            stamp_duty_rate=2.0,  # auto=True 时此值被忽略
            slippage=5.0,
        )
        assert config["stamp_duty_rate"] is None

    def test_stamp_duty_auto_false_slider_zero_returns_zero(self, today: date, one_year_ago: date) -> None:
        """stamp_duty_auto=False + slider=0 → stamp_duty_rate=0.0（0 值被尊重，资金路径精度）。"""
        config = _get_config_from_state(
            start_date=one_year_ago,
            end_date=today,
            initial_capital_str="1000000",
            rebalance_freq="signal",
            max_positions_str="50",
            commission=3.0,
            stamp_duty_auto=False,
            stamp_duty_rate=0,
            slippage=5.0,
        )
        assert config["stamp_duty_rate"] == 0.0


class TestBacktestConfigPanelContract:
    """契约守护测试：声明式组件禁止命令式模式。"""

    def test_no_imperative_patterns(self) -> None:
        """grep 命令式禁止模式 = 0（did_mount/will_unmount/refresh_locale/.update()/class X(ft.Container)）。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        forbidden_patterns = [
            "def did_mount",
            "def will_unmount",
            "def refresh_locale",
            "self.update()",
            "class BacktestConfigPanel(ft.Container)",
            "class BacktestConfigPanel(ft.UserControl)",
            "PageRefMixin",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"禁止命令式模式: {pattern}"

    def test_is_declarative_component(self) -> None:
        """验证是 @ft.component 声明式组件。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        assert "@ft.component" in content
        assert "def BacktestConfigPanel(" in content

    def test_no_strategy_key_dead_code(self) -> None:
        """验证 _strategy_key 死代码已删除（检查代码模式，允许 docstring 提及符号名）。"""
        import re
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        # 检查代码模式（属性访问/赋值、方法定义、方法调用），docstring 提及符号名不算违规
        assert not re.search(r"self\._strategy_key\b", content), "不应再有 self._strategy_key 属性访问"
        assert not re.search(r"def\s+set_strategy_key\s*\(", content), "不应再有 set_strategy_key 方法定义"
        assert not re.search(r"\.set_strategy_key\s*\(", content), "不应再有 set_strategy_key 方法调用"

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        assert "ft.use_state(I18n.get_observable_state)" in content
