"""BacktestConfigPanel 组件运行时测试 (Task 3.5).

覆盖:
1. 模块级纯函数: _make_date_picker (label/initial_value/on_change 配置正确性)
2. 组件运行时:
   - _on_run_click 调 _get_config_from_state + 触发 on_run_backtest
   - _on_stamp_duty_auto_change 切换时 stamp_duty_rate 显隐
   - DatePicker 选择日期后 start_date/end_date 状态更新
   - 9 个表单控件 on_change 触发 set_*
   - initial_capital 空/非数字容错
   - max_positions 边界 (0/负数/超大值)

tests/unit/ui/test_backtest_config_panel.py (顶层) 已覆盖:
- _get_config_from_state 纯函数 (类型转换/默认值/stamp_duty 分段)
- 契约守护 (无命令式模式/死代码删除/use_dialog 声明式)

本文件聚焦组件运行时行为 + _make_date_picker 模块级函数, 不重复纯函数测试。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
)
from ui.components.backtest import backtest_config_panel as panel_module
from ui.components.backtest.backtest_config_panel import (
    BacktestConfigPanel,
    _make_date_picker,
)

pytestmark = pytest.mark.unit


# ============================================================================
# Helper 函数
# ============================================================================


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归)。
    """
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_walk_controls(content))
    return result


def _find_text_field(root: Any, label: str) -> ft.TextField:
    """通过 label 查找 TextField 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"TextField with label={label} not found")


def _find_dropdown(root: Any, label: str) -> ft.Dropdown:
    """通过 label 查找 Dropdown 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"Dropdown with label={label} not found")


def _find_run_button(root: Any) -> ft.Button:
    """查找 run button (PLAY_ARROW icon)。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Button) and getattr(ctrl, "icon", None) == ft.Icons.PLAY_ARROW:
            return ctrl
    raise AssertionError("Run button not found")


def _find_date_buttons(root: Any) -> list[ft.OutlinedButton]:
    """查找 2 个 date button (CALENDAR_TODAY icon), 返回 [start_btn, end_btn]。"""
    btns = [
        ctrl
        for ctrl in _walk_controls(root)
        if isinstance(ctrl, ft.OutlinedButton) and getattr(ctrl, "icon", None) == ft.Icons.CALENDAR_TODAY
    ]
    assert len(btns) == 2, f"应有 2 个 date button, 实际 {len(btns)}"
    return btns


def _find_sliders(root: Any) -> dict[str, ft.Slider]:
    """查找 3 个 Slider, 返回 {name: slider}。

    commission_slider: max=10
    stamp_duty_slider: max=2
    slippage_slider: max=20
    """
    sliders = [c for c in _walk_controls(root) if isinstance(c, ft.Slider)]
    assert len(sliders) == 3, f"应有 3 个 Slider, 实际 {len(sliders)}"
    result: dict[str, ft.Slider] = {}
    for s in sliders:
        if s.max == 10:
            result["commission"] = s
        elif s.max == 2:
            result["stamp_duty"] = s
        elif s.max == 20:
            result["slippage"] = s
    return result


def _find_checkbox(root: Any) -> ft.Checkbox:
    """查找 stamp_duty_auto Checkbox。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Checkbox):
            return ctrl
    raise AssertionError("Checkbox not found")


# ============================================================================
# Mock fixture
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_panel_deps(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """Mock BacktestConfigPanel 的外部依赖 (I18n / AppColors)。

    mock_i18n_state / mock_app_colors_state 来自 tests/unit/ui/conftest.py,
    注入 ui.i18n._i18n_state 和 AppColors._state, 使 get_observable_state() 返回 mock state。

    本 fixture 进一步 mock panel_module.I18n (I18n.get 返回 f"i18n[{key}]")
    和 panel_module.AppColors (MagicMock, 所有属性访问不抛异常)。
    """
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    monkeypatch.setattr(panel_module, "I18n", mock_i18n)
    monkeypatch.setattr(panel_module, "AppColors", MagicMock())


def _render_panel(on_run_backtest: Any = None) -> tuple[Any, FakePage, Any, Any]:
    """渲染 BacktestConfigPanel, 返回 (on_run_backtest, page, result, component)。

    依赖 _mock_panel_deps autouse fixture 已 setup 的 mock。
    """
    if on_run_backtest is None:
        on_run_backtest = MagicMock()
    page = FakePage()
    component = make_component(BacktestConfigPanel, on_run_backtest=on_run_backtest)
    run_mount_effects(component, page=page)
    result = render_once(component)
    return on_run_backtest, page, result, component


def _rerender(component: Any) -> Any:
    """重新渲染组件 (声明式范式, on_change 触发 set_state 后需重新渲染)。"""
    return render_once(component)


# ============================================================================
# 模块级纯函数: _make_date_picker
# ============================================================================


class TestMakeDatePicker:
    """_make_date_picker: 创建 DatePicker (label/initial_value/on_change 配置正确性)。"""

    def test_returns_date_picker_instance(self) -> None:
        """返回 ft.DatePicker 实例。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert isinstance(picker, ft.DatePicker)

    def test_first_date_last_date_value_bound(self) -> None:
        """first_date/last_date/value 正确绑定。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.first_date == date(2020, 1, 1)
        assert picker.last_date == date(2025, 12, 31)
        assert picker.value == date(2023, 6, 15)

    def test_help_text_uses_i18n(self) -> None:
        """help_text 通过 I18n.get('date_picker_help') 获取。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.help_text == "i18n[date_picker_help]"

    def test_cancel_text_uses_i18n(self) -> None:
        """cancel_text 通过 I18n.get('common_cancel') 获取。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.cancel_text == "i18n[common_cancel]"

    def test_confirm_text_uses_i18n(self) -> None:
        """confirm_text 通过 I18n.get('common_ok') 获取。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.confirm_text == "i18n[common_ok]"

    def test_error_format_text_uses_i18n(self) -> None:
        """error_format_text 通过 I18n.get('date_picker_error_format') 获取。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.error_format_text == "i18n[date_picker_error_format]"

    def test_error_invalid_text_uses_i18n(self) -> None:
        """error_invalid_text 通过 I18n.get('date_picker_error_invalid') 获取。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.error_invalid_text == "i18n[date_picker_error_invalid]"

    def test_on_change_bound(self) -> None:
        """on_change 回调正确绑定。"""
        callback = MagicMock()
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
            on_change=callback,
        )
        assert picker.on_change is callback

    def test_on_dismiss_bound(self) -> None:
        """on_dismiss 回调正确绑定。"""
        callback = MagicMock()
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
            on_dismiss=callback,
        )
        assert picker.on_dismiss is callback

    def test_on_change_none_default(self) -> None:
        """on_change=None 默认值。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.on_change is None

    def test_on_dismiss_none_default(self) -> None:
        """on_dismiss=None 默认值。"""
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
        )
        assert picker.on_dismiss is None

    def test_on_change_invoked_with_event(self) -> None:
        """on_change 回调可被外部驱动 (模拟用户选择日期)。"""
        captured: list[date] = []
        callback = MagicMock(side_effect=lambda e: captured.append(e.control.value))
        picker = _make_date_picker(
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 31),
            value=date(2023, 6, 15),
            on_change=callback,
        )
        _invoke(picker.on_change, _make_event(date(2024, 1, 1)))
        callback.assert_called_once()
        assert captured == [date(2024, 1, 1)]


# ============================================================================
# 组件挂载/渲染基础测试
# ============================================================================


class TestBacktestConfigPanelMount:
    """BacktestConfigPanel 挂载/渲染基础测试。"""

    def test_mount_returns_container_with_column_content(self) -> None:
        """挂载返回 ft.Container, content 为 Column。"""
        _, _, result, _ = _render_panel()
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_render_includes_initial_capital_text_field(self) -> None:
        """渲染含 initial_capital TextField。"""
        _, _, result, _ = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        assert isinstance(field, ft.TextField)

    def test_render_includes_max_positions_text_field(self) -> None:
        """渲染含 max_positions TextField。"""
        _, _, result, _ = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        assert isinstance(field, ft.TextField)

    def test_render_includes_rebalance_dropdown(self) -> None:
        """渲染含 rebalance_freq Dropdown。"""
        _, _, result, _ = _render_panel()
        dd = _find_dropdown(result, "i18n[backtest_rebalance_freq]")
        assert isinstance(dd, ft.Dropdown)

    def test_render_includes_three_sliders(self) -> None:
        """渲染含 3 个 Slider (commission/stamp_duty/slippage)。"""
        _, _, result, _ = _render_panel()
        sliders = _find_sliders(result)
        assert set(sliders.keys()) == {"commission", "stamp_duty", "slippage"}

    def test_render_includes_run_button(self) -> None:
        """渲染含 run button (ft.Button with PLAY_ARROW icon)。"""
        _, _, result, _ = _render_panel()
        run_btn = _find_run_button(result)
        assert isinstance(run_btn, ft.Button)

    def test_render_includes_two_date_buttons(self) -> None:
        """渲染含 2 个 OutlinedButton (start_date/end_date, CALENDAR_TODAY icon)。"""
        _, _, result, _ = _render_panel()
        date_btns = _find_date_buttons(result)
        assert len(date_btns) == 2

    def test_render_includes_stamp_duty_checkbox(self) -> None:
        """渲染含 stamp_duty_auto Checkbox。"""
        _, _, result, _ = _render_panel()
        checkbox = _find_checkbox(result)
        assert isinstance(checkbox, ft.Checkbox)

    def test_render_includes_rebalance_options(self) -> None:
        """rebalance_dropdown 含 4 个选项 (signal/daily/weekly/monthly)。"""
        _, _, result, _ = _render_panel()
        dd = _find_dropdown(result, "i18n[backtest_rebalance_freq]")
        keys = {o.key for o in dd.options}
        assert keys == {"signal", "daily", "weekly", "monthly"}

    def test_initial_capital_field_default_value(self) -> None:
        """initial_capital_input 默认值 = '1000000'。"""
        _, _, result, _ = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        assert field.value == "1000000"

    def test_max_positions_field_default_value(self) -> None:
        """max_position_input 默认值 = '50'。"""
        _, _, result, _ = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        assert field.value == "50"

    def test_rebalance_dropdown_default_value(self) -> None:
        """rebalance_dropdown 默认 value = 'signal'。"""
        _, _, result, _ = _render_panel()
        dd = _find_dropdown(result, "i18n[backtest_rebalance_freq]")
        assert dd.value == "signal"


# ============================================================================
# 组件运行时: _on_run_click
# ============================================================================


class TestOnRunClick:
    """_on_run_click: 调 _get_config_from_state + 触发 on_run_backtest。"""

    def test_run_click_triggers_on_run_backtest_with_config(self) -> None:
        """run button on_click → on_run_backtest(config dict)。"""
        on_run, _, result, _ = _render_panel()
        run_btn = _find_run_button(result)
        _invoke(run_btn.on_click, _make_event())
        on_run.assert_called_once()
        config = on_run.call_args.args[0]
        assert isinstance(config, dict)

    def test_run_click_config_has_required_keys(self) -> None:
        """config dict 含 8 个必需 key。"""
        on_run, _, result, _ = _render_panel()
        run_btn = _find_run_button(result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        expected_keys = {
            "start_date",
            "end_date",
            "initial_capital",
            "rebalance_freq",
            "max_position_count",
            "commission_rate",
            "stamp_duty_rate",
            "slippage_bps",
        }
        assert set(config.keys()) == expected_keys

    def test_run_click_with_default_state(self) -> None:
        """默认 state: initial_capital=1000000, rebalance=signal, max_positions=50。"""
        on_run, _, result, _ = _render_panel()
        run_btn = _find_run_button(result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 1_000_000.0
        assert config["rebalance_freq"] == "signal"
        assert config["max_position_count"] == 50
        assert config["commission_rate"] == 3 / 10000  # 3.0 ‱
        assert config["stamp_duty_rate"] is None  # auto=True
        assert config["slippage_bps"] == 5.0

    def test_run_click_none_callback_no_error(self) -> None:
        """on_run_backtest=None 时 _on_run_click 不抛异常 (早返回)。"""
        _, _, result, _ = _render_panel(on_run_backtest=None)
        run_btn = _find_run_button(result)
        # 不应抛异常
        _invoke(run_btn.on_click, _make_event())

    def test_run_click_reflects_updated_state(self) -> None:
        """_on_run_click 读取最新 state (set_initial_capital 后 _on_run_click 看到新值)。"""
        on_run, _, result, component = _render_panel()
        capital_field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(capital_field.on_change, _make_event("500000"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 500_000.0

    def test_run_click_passes_start_end_date(self) -> None:
        """_on_run_click 把当前 start_date/end_date 传给 config。"""
        on_run, _, result, _ = _render_panel()
        run_btn = _find_run_button(result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        today = date.today()
        one_year_ago = today - timedelta(days=365)
        assert config["start_date"] == one_year_ago
        assert config["end_date"] == today


# ============================================================================
# 组件运行时: _on_stamp_duty_auto_change
# ============================================================================


class TestOnStampDutyAutoChange:
    """_on_stamp_duty_auto_change: 切换 stamp_duty_auto 时 stamp_duty_rate 显隐。"""

    def test_checkbox_uncheck_sets_auto_false(self) -> None:
        """Checkbox 取消勾选 (value=False) → set_stamp_duty_auto(False), slider 可用。"""
        on_run, _, result, component = _render_panel()
        checkbox = _find_checkbox(result)
        _invoke(checkbox.on_change, _make_event(False))
        new_result = _rerender(component)
        sliders = _find_sliders(new_result)
        assert sliders["stamp_duty"].disabled is False
        # run 验证 stamp_duty_rate 非 None (auto=False)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["stamp_duty_rate"] is not None

    def test_checkbox_check_sets_auto_true(self) -> None:
        """Checkbox 勾选 (value=True) → set_stamp_duty_auto(True), slider disabled。"""
        on_run, _, result, component = _render_panel()
        checkbox = _find_checkbox(result)
        # 先取消再勾选 (默认已是 True, 需先 False 再 True 才能看到变化)
        _invoke(checkbox.on_change, _make_event(False))
        _rerender(component)
        # 重新渲染拿 checkbox
        mid_result = _rerender(component)
        checkbox = _find_checkbox(mid_result)
        _invoke(checkbox.on_change, _make_event(True))
        new_result = _rerender(component)
        sliders = _find_sliders(new_result)
        assert sliders["stamp_duty"].disabled is True
        # run 验证 stamp_duty_rate=None
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["stamp_duty_rate"] is None

    def test_stamp_duty_slider_disabled_default_true(self) -> None:
        """默认 auto=True 时 stamp_duty_slider disabled=True。"""
        _, _, result, _ = _render_panel()
        sliders = _find_sliders(result)
        assert sliders["stamp_duty"].disabled is True

    def test_stamp_duty_text_shows_auto_when_enabled(self) -> None:
        """auto=True 时 stamp_duty_text 显示 'i18n[backtest_stamp_duty_auto]'。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        texts = [c for c in ctrls if isinstance(c, ft.Text)]
        auto_texts = [t for t in texts if t.value == "i18n[backtest_stamp_duty_auto]"]
        # stamp_duty_text 应显示 auto 文案 (还有一个是 checkbox label, 同 key)
        assert len(auto_texts) >= 1

    def test_stamp_duty_text_shows_rate_when_disabled(self) -> None:
        """auto=False 时 stamp_duty_text 显示 '0.5‰' 格式。"""
        _, _, result, component = _render_panel()
        checkbox = _find_checkbox(result)
        _invoke(checkbox.on_change, _make_event(False))
        new_result = _rerender(component)
        ctrls = _walk_controls(new_result)
        texts = [c for c in ctrls if isinstance(c, ft.Text)]
        # stamp_duty_rate 默认 0.5, 应显示 "0.5‰"
        rate_texts = [t for t in texts if t.value == "0.5‰"]
        assert len(rate_texts) == 1

    def test_stamp_duty_auto_change_reflected_in_config(self) -> None:
        """切换 stamp_duty_auto 后 _on_run_click 产生的 config 反映新值。"""
        on_run, _, result, component = _render_panel()
        # 切换到 auto=False
        checkbox = _find_checkbox(result)
        _invoke(checkbox.on_change, _make_event(False))
        # 修改 stamp_duty_rate slider
        mid_result = _rerender(component)
        sliders = _find_sliders(mid_result)
        _invoke(sliders["stamp_duty"].on_change, _make_event(1.5))
        # run 验证
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["stamp_duty_rate"] == 1.5 / 1000  # ‰ → 小数


# ============================================================================
# 组件运行时: DatePicker 选择日期后状态更新
# ============================================================================


class TestDatePickerStateUpdate:
    """DatePicker 选择日期后 start_date/end_date 状态更新。

    组件内部 _on_start_change / _on_end_change 通过 ft.use_dialog 注册到 DatePicker.on_change。
    测试策略:
    1. 触发 date_btn.on_click → set_show_xxx_picker(True)
    2. re-render → use_dialog 注册 DatePicker 到 page._dialogs.controls
    3. 拿到 DatePicker 实例, 调用其 on_change → _on_start_change/_on_end_change
    4. 验证 start_date_btn/end_date_btn.content 显示新日期
    """

    def test_start_date_button_click_registers_date_picker(self) -> None:
        """start_date_btn.on_click → set_show_start_picker(True), re-render 后 DatePicker 注册。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        # use_dialog 应注册 DatePicker 到 page._dialogs.controls
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1, "DatePicker 未注册到 page._dialogs.controls"

    def test_end_date_button_click_registers_date_picker(self) -> None:
        """end_date_btn.on_click → set_show_end_picker(True), re-render 后 DatePicker 注册。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[1].on_click, _make_event())
        _rerender(component)
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1, "DatePicker 未注册到 page._dialogs.controls"

    def test_start_date_picker_on_change_updates_start_date(self) -> None:
        """DatePicker 选择新日期 → _on_start_change → set_start_date → start_date_btn 更新。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        # 打开 start_date picker
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        # 拿到 DatePicker 实例
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1
        picker = date_pickers[0]
        # 模拟选择新日期
        new_date = date(2024, 3, 15)
        _invoke(picker.on_change, _make_event(new_date))
        # re-render 验证 start_date_btn.content 显示新日期
        new_result = _rerender(component)
        new_date_btns = _find_date_buttons(new_result)
        assert new_date_btns[0].content == new_date.strftime("%Y-%m-%d")

    def test_end_date_picker_on_change_updates_end_date(self) -> None:
        """DatePicker 选择新日期 → _on_end_change → set_end_date → end_date_btn 更新。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        # 打开 end_date picker
        _invoke(date_btns[1].on_click, _make_event())
        _rerender(component)
        # 拿到 DatePicker 实例
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1
        picker = date_pickers[0]
        # 模拟选择新日期
        new_date = date(2024, 12, 31)
        _invoke(picker.on_change, _make_event(new_date))
        # re-render 验证 end_date_btn.content 显示新日期
        new_result = _rerender(component)
        new_date_btns = _find_date_buttons(new_result)
        assert new_date_btns[1].content == new_date.strftime("%Y-%m-%d")

    def test_start_date_picker_none_value_skips_update(self) -> None:
        """_on_start_change: value=None 时不更新 start_date。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        original_content = date_btns[0].content
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1
        picker = date_pickers[0]
        _invoke(picker.on_change, _make_event(None))
        new_result = _rerender(component)
        new_date_btns = _find_date_buttons(new_result)
        # value=None 时 start_date 不变, content 仍为原值
        assert new_date_btns[0].content == original_content

    def test_start_date_picker_on_dismiss_no_new_picker(self) -> None:
        """_on_start_dismiss: dismiss → set_show_start_picker(False), 再次渲染 use_dialog 收到 None, 不注册新 picker。

        FakePage._dialogs.controls 是 append-only 容器, 无法验证 dialog 卸载;
        改为验证 dismiss 后再次渲染时 picker 数量保持不变 (use_dialog 收到 None, 未 append 新 picker)。
        """
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        pickers_before_dismiss = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(pickers_before_dismiss) >= 1
        picker = pickers_before_dismiss[0]
        # dismiss → set_show_start_picker(False)
        _invoke(picker.on_dismiss, _make_event())
        _rerender(component)
        # show_start_picker=False, use_dialog 收到 None, 不注册新 picker (数量不变)
        pickers_after_dismiss = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(pickers_after_dismiss) == len(pickers_before_dismiss)

    def test_start_date_update_reflected_in_config(self) -> None:
        """DatePicker 更新 start_date 后 _on_run_click 产生 config 反映新日期。"""
        on_run, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1
        picker = date_pickers[0]
        new_date = date(2023, 1, 1)
        _invoke(picker.on_change, _make_event(new_date))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["start_date"] == new_date


# ============================================================================
# 组件运行时: 9 个表单控件 on_change 触发 set_*
# ============================================================================


class TestFormControlsOnChange:
    """9 个表单控件 on_change/on_select/on_click 触发 set_* 更新 state。

    控件清单:
    1. initial_capital_input.on_change → set_initial_capital
    2. rebalance_dropdown.on_select → set_rebalance_freq
    3. max_position_input.on_change → set_max_positions
    4. commission_slider.on_change → set_commission
    5. stamp_duty_auto_checkbox.on_change → set_stamp_duty_auto
    6. stamp_duty_slider.on_change → set_stamp_duty_rate
    7. slippage_slider.on_change → set_slippage
    8. start_date_btn.on_click → set_show_start_picker(True)
    9. end_date_btn.on_click → set_show_end_picker(True)

    验证方式: on_change 触发 set_state 后, 重新渲染, 通过控件 value 或 _on_run_click
    产生的 config 验证 state 已更新。
    """

    def test_initial_capital_on_change_updates_state(self) -> None:
        """initial_capital_input.on_change → set_initial_capital。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(field.on_change, _make_event("999999"))
        new_result = _rerender(component)
        # 验证: field.value 已更新
        new_field = _find_text_field(new_result, "i18n[backtest_initial_capital]")
        assert new_field.value == "999999"
        # 验证: _on_run_click 产生的 config 反映新值
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 999_999.0

    def test_rebalance_dropdown_on_select_updates_state(self) -> None:
        """rebalance_dropdown.on_select → set_rebalance_freq。"""
        on_run, _, result, component = _render_panel()
        dd = _find_dropdown(result, "i18n[backtest_rebalance_freq]")
        _invoke(dd.on_select, _make_event("weekly"))
        new_result = _rerender(component)
        new_dd = _find_dropdown(new_result, "i18n[backtest_rebalance_freq]")
        assert new_dd.value == "weekly"
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["rebalance_freq"] == "weekly"

    def test_max_positions_on_change_updates_state(self) -> None:
        """max_position_input.on_change → set_max_positions。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event("20"))
        new_result = _rerender(component)
        new_field = _find_text_field(new_result, "i18n[backtest_max_positions]")
        assert new_field.value == "20"
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == 20

    def test_commission_slider_on_change_updates_state(self) -> None:
        """commission_slider.on_change → set_commission。"""
        on_run, _, result, component = _render_panel()
        sliders = _find_sliders(result)
        _invoke(sliders["commission"].on_change, _make_event(7.0))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["commission"].value == 7.0
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["commission_rate"] == 7 / 10000

    def test_stamp_duty_slider_on_change_updates_state(self) -> None:
        """stamp_duty_slider.on_change → set_stamp_duty_rate (需先 auto=False 解锁)。"""
        on_run, _, result, component = _render_panel()
        # 先解除 disabled
        checkbox = _find_checkbox(result)
        _invoke(checkbox.on_change, _make_event(False))
        _rerender(component)
        # 再调 stamp_duty_slider
        mid_result = _rerender(component)
        sliders = _find_sliders(mid_result)
        _invoke(sliders["stamp_duty"].on_change, _make_event(1.5))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["stamp_duty"].value == 1.5
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["stamp_duty_rate"] == 1.5 / 1000

    def test_slippage_slider_on_change_updates_state(self) -> None:
        """slippage_slider.on_change → set_slippage。"""
        on_run, _, result, component = _render_panel()
        sliders = _find_sliders(result)
        _invoke(sliders["slippage"].on_change, _make_event(12.0))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["slippage"].value == 12.0
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["slippage_bps"] == 12.0

    def test_start_date_button_on_click_triggers_picker(self) -> None:
        """start_date_btn.on_click → set_show_start_picker(True) (打开 date picker)。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[0].on_click, _make_event())
        _rerender(component)
        # 验证 DatePicker 已注册
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1

    def test_end_date_button_on_click_triggers_picker(self) -> None:
        """end_date_btn.on_click → set_show_end_picker(True)。"""
        _, page, result, component = _render_panel()
        date_btns = _find_date_buttons(result)
        _invoke(date_btns[1].on_click, _make_event())
        _rerender(component)
        date_pickers = [c for c in page._dialogs.controls if isinstance(c, ft.DatePicker)]
        assert len(date_pickers) >= 1

    def test_commission_slider_none_value_falls_back(self) -> None:
        """commission_slider.on_change value=None → set_commission(3.0) (默认兜底)。"""
        _, _, result, component = _render_panel()
        sliders = _find_sliders(result)
        _invoke(sliders["commission"].on_change, _make_event(None))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["commission"].value == 3.0

    def test_slippage_slider_none_value_falls_back(self) -> None:
        """slippage_slider.on_change value=None → set_slippage(5.0) (默认兜底)。"""
        _, _, result, component = _render_panel()
        sliders = _find_sliders(result)
        _invoke(sliders["slippage"].on_change, _make_event(None))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["slippage"].value == 5.0

    def test_stamp_duty_slider_none_value_falls_back(self) -> None:
        """stamp_duty_slider.on_change value=None → set_stamp_duty_rate(0.5) (默认兜底)。"""
        _, _, result, component = _render_panel()
        # 先解除 disabled
        checkbox = _find_checkbox(result)
        _invoke(checkbox.on_change, _make_event(False))
        _rerender(component)
        mid_result = _rerender(component)
        sliders = _find_sliders(mid_result)
        _invoke(sliders["stamp_duty"].on_change, _make_event(None))
        new_result = _rerender(component)
        new_sliders = _find_sliders(new_result)
        assert new_sliders["stamp_duty"].value == 0.5

    def test_rebalance_dropdown_empty_value_falls_back(self) -> None:
        """rebalance_dropdown.on_select value='' → set_rebalance_freq('signal') (兜底)。"""
        _, _, result, component = _render_panel()
        dd = _find_dropdown(result, "i18n[backtest_rebalance_freq]")
        _invoke(dd.on_select, _make_event(""))
        new_result = _rerender(component)
        new_dd = _find_dropdown(new_result, "i18n[backtest_rebalance_freq]")
        assert new_dd.value == "signal"


# ============================================================================
# 组件运行时: initial_capital 容错
# ============================================================================


class TestInitialCapitalEdgeCases:
    """initial_capital 空/非数字容错 (组件层 _on_run_click 触发 _get_config_from_state)。"""

    def test_empty_initial_capital_falls_back_in_run_click(self) -> None:
        """空 initial_capital → _on_run_click 触发 config.initial_capital=1000000。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(field.on_change, _make_event(""))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 1_000_000.0

    def test_non_numeric_initial_capital_falls_back_in_run_click(self) -> None:
        """非数字 initial_capital → config.initial_capital=1000000。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(field.on_change, _make_event("abc"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 1_000_000.0

    def test_decimal_initial_capital_accepted(self) -> None:
        """decimal initial_capital (1234567.89) → 接受为 float。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(field.on_change, _make_event("1234567.89"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["initial_capital"] == 1_234_567.89

    def test_initial_capital_field_value_empty_string(self) -> None:
        """set_initial_capital('') 后 field.value 显示空字符串。"""
        _, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_initial_capital]")
        _invoke(field.on_change, _make_event(""))
        new_result = _rerender(component)
        new_field = _find_text_field(new_result, "i18n[backtest_initial_capital]")
        assert new_field.value == ""


# ============================================================================
# 组件运行时: max_positions 边界
# ============================================================================


class TestMaxPositionsEdgeCases:
    """max_positions 边界 (0/负数/超大值)。

    组件层不做边界校验 (由后续 BacktestConfig 处理), _get_config_from_state
    仅做 int 转换, 这些边界值会原样传入 config。
    """

    def test_max_positions_zero(self) -> None:
        """max_positions=0 → config.max_position_count=0 (组件层不限制)。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event("0"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == 0

    def test_max_positions_negative(self) -> None:
        """max_positions=-5 → config.max_position_count=-5 (组件层不限制)。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event("-5"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == -5

    def test_max_positions_large_value(self) -> None:
        """max_positions=999999 → config.max_position_count=999999 (组件层不限制)。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event("999999"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == 999_999

    def test_max_positions_empty_falls_back(self) -> None:
        """max_positions='' → config.max_position_count=50 (空兜底)。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event(""))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == 50

    def test_max_positions_non_numeric_falls_back(self) -> None:
        """max_positions='abc' → config.max_position_count=50 (ValueError 兜底)。"""
        on_run, _, result, component = _render_panel()
        field = _find_text_field(result, "i18n[backtest_max_positions]")
        _invoke(field.on_change, _make_event("abc"))
        new_result = _rerender(component)
        run_btn = _find_run_button(new_result)
        _invoke(run_btn.on_click, _make_event())
        config = on_run.call_args.args[0]
        assert config["max_position_count"] == 50


# ============================================================================
# 测试隔离守卫 (R7: 单例未污染)
# ============================================================================


class TestBacktestConfigPanelIsolation:
    """R7 守卫: 测试间无单例状态污染 (由 conftest _reset_all_singletons autouse 保证)。"""

    def test_no_state_leakage_between_renders(self) -> None:
        """连续渲染两个 panel, 第二个不受第一个影响 (state 独立)。"""
        on_run1, _, result1, _ = _render_panel()
        on_run2, _, result2, _ = _render_panel()
        # 两个 on_run 应是独立 MagicMock 实例
        assert on_run1 is not on_run2
        # 触发第一个 panel 的 run, 第二个不应被调用
        run_btn1 = _find_run_button(result1)
        _invoke(run_btn1.on_click, _make_event())
        on_run1.assert_called_once()
        on_run2.assert_not_called()

    def test_two_panels_have_independent_state(self) -> None:
        """两个 panel 实例 state 独立: 修改 panel1 不影响 panel2。"""
        on_run1, _, result1, component1 = _render_panel()
        on_run2, _, result2, _ = _render_panel()
        # 修改 panel1 的 initial_capital
        field1 = _find_text_field(result1, "i18n[backtest_initial_capital]")
        _invoke(field1.on_change, _make_event("888888"))
        new_result1 = _rerender(component1)
        # panel1 config 反映新值
        run_btn1 = _find_run_button(new_result1)
        _invoke(run_btn1.on_click, _make_event())
        config1 = on_run1.call_args.args[0]
        assert config1["initial_capital"] == 888_888.0
        # panel2 config 仍是默认值
        run_btn2 = _find_run_button(result2)
        _invoke(run_btn2.on_click, _make_event())
        config2 = on_run2.call_args.args[0]
        assert config2["initial_capital"] == 1_000_000.0
