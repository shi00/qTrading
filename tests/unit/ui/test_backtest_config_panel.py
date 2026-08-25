"""BacktestConfigPanel 测试（声明式 V1）。

测试策略：
1. 纯函数 ``_get_config_from_state`` 单测（类型转换/默认值/stamp_duty 分段逻辑）
2. 契约守护测试（grep 命令式禁止模式 = 0）

声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化（参考 3.2.1-3.2.4 范式）。
"""

from datetime import date, timedelta

import pytest

from ui.components.backtest.backtest_config_panel import _get_config_from_state, _validate_backtest_inputs

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

    def test_invalid_initial_capital_raises_value_error(self, today: date, one_year_ago: date) -> None:
        """UX-05: 非法 initial_capital 静默兜底已删除 → _get_config_from_state 抛 ValueError。"""
        with pytest.raises(ValueError):  # noqa: weak-assertion trust-boundary 契约, 唯一行为即抛出, 无返回值可断言
            _get_config_from_state(
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

    def test_invalid_max_positions_raises_value_error(self, today: date, one_year_ago: date) -> None:
        """UX-05: 非法 max_positions 静默兜底已删除 → _get_config_from_state 抛 ValueError。"""
        with pytest.raises(ValueError):  # noqa: weak-assertion trust-boundary 契约, 唯一行为即抛出, 无返回值可断言
            _get_config_from_state(
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

    def test_empty_initial_capital_raises_value_error(self, today: date, one_year_ago: date) -> None:
        """UX-05: 空 initial_capital 静默兜底已删除 → 抛 ValueError（空串 int/float 失败）。"""
        with pytest.raises(ValueError):  # noqa: weak-assertion trust-boundary 契约, 唯一行为即抛出, 无返回值可断言
            _get_config_from_state(
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

    def test_empty_max_positions_raises_value_error(self, today: date, one_year_ago: date) -> None:
        """UX-05: 空 max_positions 静默兜底已删除 → 抛 ValueError。"""
        with pytest.raises(ValueError):  # noqa: weak-assertion trust-boundary 契约, 唯一行为即抛出, 无返回值可断言
            _get_config_from_state(
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


class TestValidateBacktestInputs:
    """UX-05 (P1-01): _validate_backtest_inputs 纯函数单测。

    规则与后端 strategies/backtest/config.py::validate 严格对齐：
    - initial_capital: 非空 / float 可解析且 isfinite / > 0
    - max_positions: 非空 / int 可解析（整数语义）/ >= 1
    - 日期: start < end（严格小于, 与后端 L69 一致）
    """

    @pytest.fixture
    def today(self) -> date:
        return date.today()

    @pytest.fixture
    def one_year_ago(self, today: date) -> date:
        return today - timedelta(days=365)

    def _call(
        self,
        start_date: date,
        end_date: date,
        initial_capital_str: str,
        max_positions_str: str,
    ) -> dict:
        return _validate_backtest_inputs(start_date, end_date, initial_capital_str, max_positions_str)

    def test_all_valid_returns_empty(self, today: date, one_year_ago: date) -> None:
        """默认值 (1000000 / 50 / start<end) → 空 dict。"""
        errors = self._call(one_year_ago, today, "1000000", "50")
        assert errors == {}

    def test_initial_capital_empty_required(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "", "50")
        assert errors.get("initial_capital") == "backtest_error_required"

    def test_initial_capital_non_numeric(self, today: date, one_year_ago: date) -> None:
        for bad in ("abc", "1,000,000", "1万"):
            errors = self._call(one_year_ago, today, bad, "50")
            assert errors.get("initial_capital") == "backtest_error_invalid_number", f"{bad!r} 应非法"

    def test_initial_capital_zero(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "0", "50")
        assert errors.get("initial_capital") == "backtest_error_capital_positive"

    def test_initial_capital_negative(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "-100", "50")
        assert errors.get("initial_capital") == "backtest_error_capital_positive"

    def test_initial_capital_decimal_accepted(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "1234567.89", "50")
        assert "initial_capital" not in errors

    def test_initial_capital_underscore_grouping_accepted(self, today: date, one_year_ago: date) -> None:
        """对抗检视 INFO: Python float() 容忍下划线数字分隔 ("1_000"=1000.0) — 良性千分位, 固化契约。"""
        errors = self._call(one_year_ago, today, "1_000_000", "50")
        assert "initial_capital" not in errors

    def test_initial_capital_non_finite_rejected(self, today: date, one_year_ago: date) -> None:
        """对抗检视 MAJOR: inf/nan/1e309 (溢出→inf) 必须拦截 (math.isfinite)。"""
        for bad in ("inf", "nan", "1e309"):
            errors = self._call(one_year_ago, today, bad, "50")
            assert errors.get("initial_capital") == "backtest_error_invalid_number", f"{bad!r} 应被 isfinite 拦截"

    def test_max_positions_empty_required(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "1000000", "")
        assert errors.get("max_positions") == "backtest_error_required"

    def test_max_positions_non_numeric(self, today: date, one_year_ago: date) -> None:
        """整数语义: "3.5"/"50.0" 均非法 (int() 拒绝), "abc" 非法。"""
        for bad in ("abc", "3.5", "50.0"):
            errors = self._call(one_year_ago, today, "1000000", bad)
            assert errors.get("max_positions") == "backtest_error_invalid_number", f"{bad!r} 应非法"

    def test_max_positions_zero(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "1000000", "0")
        assert errors.get("max_positions") == "backtest_error_positions_positive"

    def test_max_positions_negative(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "1000000", "-5")
        assert errors.get("max_positions") == "backtest_error_positions_positive"

    def test_max_positions_large_value_accepted(self, today: date, one_year_ago: date) -> None:
        """无上限契约: 999999 合法 (与后端仅 >0 约束一致)。"""
        errors = self._call(one_year_ago, today, "1000000", "999999")
        assert "max_positions" not in errors

    def test_start_after_end_date_range(self, today: date, one_year_ago: date) -> None:
        errors = self._call(today, one_year_ago, "1000000", "50")
        assert errors.get("date_range") == "backtest_error_date_range"

    def test_start_equal_end_date_range(self, today: date, one_year_ago: date) -> None:
        """对抗检视 MAJOR: start==end 非法 (后端 L69 严格小于)。"""
        errors = self._call(today, today, "1000000", "50")
        assert errors.get("date_range") == "backtest_error_date_range"

    def test_multiple_invalid_fields(self, today: date, one_year_ago: date) -> None:
        errors = self._call(one_year_ago, today, "", "-3")
        assert errors.get("initial_capital") == "backtest_error_required"
        assert errors.get("max_positions") == "backtest_error_positions_positive"
        assert len(errors) == 2

    def test_whitespace_only_invalid_number(self, today: date, one_year_ago: date) -> None:
        """全空格串 non-empty (truthy) → parse 失败 → invalid_number (非 required)。"""
        errors = self._call(one_year_ago, today, "   ", "50")
        assert errors.get("initial_capital") == "backtest_error_invalid_number"


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
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        assert "ft.use_state(get_observable_state)" in content

    def test_date_picker_uses_declarative_use_dialog(self) -> None:
        """DoD: DatePicker 必须通过 ft.use_dialog() 声明式管理（§10.1），禁止 use_ref + page.show_dialog 回归。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        # 正向守护：必须使用 ft.use_dialog
        assert "ft.use_dialog(" in content, "DatePicker 必须通过 ft.use_dialog() 声明式管理"
        # 反向守护：禁止命令式 dialog 管理 API 回归
        assert "page.show_dialog" not in content, "禁止 page.show_dialog 命令式 API"
        assert "page.pop_dialog" not in content, "禁止 page.pop_dialog 命令式 API"
        # 反向守护：DatePicker 不应与 use_ref 混用（命令式实例缓存）
        assert "use_ref" not in content, "禁止 use_ref 缓存 DatePicker 实例"
