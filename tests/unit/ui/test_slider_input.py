"""ui/components/slider_input.py 单元测试 (UX 3.2, 报告 04 D1 重写).

测试 SliderInput 声明式受控组件（@ft.component）的行为：
1. 渲染契约：label 有值/None、expand、disabled
2. fmt 格式化：默认/自定义、精度（0.035 不丢精度）
3. divisions 自动计算
4. slider 事件：snap + clamp + draft 状态同步
5. textfield 事件：submit/blur commit、非法/空回滚、同值不上抛
6. 受控语义：父级 value 变化经 use_effect 同步 draft（D1 核心）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    render_once,
    run_mount_effects,
    run_render_effects,
)
from ui.components.slider_input import SliderInput, _default_fmt, _snap_to_step

pytestmark = pytest.mark.unit


def _find_control(root: Any, ctrl_type: type) -> Any | None:
    """在渲染树中查找第一个指定类型的控件。"""
    if isinstance(root, ctrl_type):
        return root
    if isinstance(root, ft.Control):
        for attr in ("controls", "content"):
            children = getattr(root, attr, None)
            if isinstance(children, list):
                for x in children:
                    found = _find_control(x, ctrl_type)
                    if found is not None:
                        return found
            elif children is not None:
                found = _find_control(children, ctrl_type)
                if found is not None:
                    return found
    return None


def _find_all(root: Any, ctrl_type: type) -> list:
    """在渲染树中查找所有指定类型的控件。"""
    found: list = []
    if isinstance(root, ctrl_type):
        found.append(root)
    if isinstance(root, ft.Control):
        for attr in ("controls", "content"):
            children = getattr(root, attr, None)
            if isinstance(children, list):
                for x in children:
                    found.extend(_find_all(x, ctrl_type))
            elif children is not None:
                found.extend(_find_all(children, ctrl_type))
    return found


def _mount(overrides: dict[str, Any] | None = None) -> tuple[Any, Any]:
    """创建并挂载 SliderInput 组件，返回 (component, 首次渲染控件树)。

    声明式范式下事件 handler 闭包捕获渲染时的 state；事件触发 set_draft 后
    必须重新 render_once 才能让新 draft 反映到控件树。
    """
    component = make_component(SliderInput, **({} if overrides is None else overrides))
    run_mount_effects(component)
    result = render_once(component)
    return component, result


def _rerender(component: Any) -> Any:
    """重新渲染组件，返回最新控件树（事件后断言的必经步骤）。"""
    return render_once(component)


def _make_event(value: Any) -> MagicMock:
    """构造带 ``e.control.value`` 的 ControlEvent mock（handler 经 get_control_value 读取）。"""
    e = MagicMock()
    e.control.value = value
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe)。

    Flet 控件事件属性为 Optional[Callable]，且 stub 声明 0 参但运行时注入
    ControlEvent；此 helper 用 Any 参数绕过 reportOptionalCall 与 reportCallIssue。
    """
    handler(*args)


class TestSliderInputRender:
    """SliderInput 渲染契约测试。"""

    def test_renders_column_with_label(
        self,
        mock_app_colors_state,
    ) -> None:
        """label 有值 → Column 包含 Text + Row(Slider + TextField)。"""
        _, result = _mount({"label": "止损率", "value": 5.0})

        assert isinstance(result, ft.Column)
        texts = _find_all(result, ft.Text)
        assert any(t.value == "止损率" for t in texts)
        assert len(_find_all(result, ft.Slider)) == 1
        assert len(_find_all(result, ft.TextField)) == 1

    def test_renders_column_without_label(
        self,
        mock_app_colors_state,
    ) -> None:
        """label=None → Column 仅包含 Row(Slider + TextField)，无 Text。"""
        _, result = _mount({"label": None, "value": 5.0})

        assert isinstance(result, ft.Column)
        texts = _find_all(result, ft.Text)
        assert len(texts) == 0
        assert len(_find_all(result, ft.Slider)) == 1
        assert len(_find_all(result, ft.TextField)) == 1

    def test_expand_true_sets_column_expand(
        self,
        mock_app_colors_state,
    ) -> None:
        """expand=True → Column.expand=True。"""
        _, result = _mount({"value": 5.0, "expand": True})

        assert result.expand is True

    def test_expand_false_default(
        self,
        mock_app_colors_state,
    ) -> None:
        """expand 默认 False → Column.expand 未设置或 False。"""
        _, result = _mount({"value": 5.0})

        assert not result.expand

    def test_disabled_propagates_to_slider_and_textfield(
        self,
        mock_app_colors_state,
    ) -> None:
        """disabled=True → Slider 与 TextField 同步禁用。"""
        _, result = _mount({"value": 5.0, "disabled": True})

        slider = _find_control(result, ft.Slider)
        text_field = _find_control(result, ft.TextField)
        assert slider.disabled is True
        assert text_field.disabled is True

    def test_disabled_false_default(
        self,
        mock_app_colors_state,
    ) -> None:
        """disabled 默认 False → Slider 与 TextField 均可编辑。"""
        _, result = _mount({"value": 5.0})

        slider = _find_control(result, ft.Slider)
        text_field = _find_control(result, ft.TextField)
        assert slider.disabled is False
        assert text_field.disabled is False

    def test_width_propagates_to_column(
        self,
        mock_app_colors_state,
    ) -> None:
        """width 显式传入 → Column.width。"""
        _, result = _mount({"value": 5.0, "width": 200})

        assert result.width == 200


class TestSliderInputFormat:
    """SliderInput fmt 格式化测试。"""

    def test_default_fmt_integer_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：整数值显示为 int。"""
        _, result = _mount({"value": 5.0})

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "5"

    def test_default_fmt_float_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：小数去除尾随 0。"""
        _, result = _mount({"value": 3.5})

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "3.5"

    def test_custom_fmt_formats_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """自定义 fmt：按 fmt 函数格式化。"""
        component = make_component(SliderInput, value=3.0, fmt=lambda v: f"{v:.1f}%")
        run_mount_effects(component)
        result = render_once(component)

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "3.0%"

    def test_default_fmt_small_decimal_precision(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：小数值保留完整精度（0.035 → "0.035"，修复 UX-2.2 精度丢失）。"""
        _, result = _mount({"value": 0.035})

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "0.035"

    def test_default_fmt_strips_trailing_zeros(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：去除无意义尾随 0（契约：值已由 _snap_to_step round 到 10 位）。"""
        assert _default_fmt(3.5) == "3.5"
        assert _default_fmt(0.0350) == "0.035"
        assert _default_fmt(5.0) == "5"


class TestSliderInputDivisions:
    """SliderInput divisions 自动计算测试。"""

    def test_divisions_explicit(
        self,
        mock_app_colors_state,
    ) -> None:
        """divisions 显式传入 → 直接使用。"""
        component = make_component(
            SliderInput,
            value=5.0,
            min_val=0,
            max_val=100,
            step=1.0,
            divisions=10,
        )
        run_mount_effects(component)
        result = render_once(component)

        slider = _find_control(result, ft.Slider)
        assert slider.divisions == 10

    def test_divisions_auto_calculated(
        self,
        mock_app_colors_state,
    ) -> None:
        """divisions=None → 由 (max-min)/step 自动计算。"""
        component = make_component(
            SliderInput,
            value=5.0,
            min_val=0,
            max_val=100,
            step=10.0,
            divisions=None,
        )
        run_mount_effects(component)
        result = render_once(component)

        slider = _find_control(result, ft.Slider)
        assert slider.divisions == 10


class TestSliderInputOnChange:
    """SliderInput slider 事件测试（snap + clamp + draft 同步）。"""

    def test_slider_on_change_triggers_callback(
        self,
        mock_app_colors_state,
    ) -> None:
        """slider 拖动 → on_change 被调用（snap 后的值），draft 同步更新。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        _invoke(slider.on_change, _make_event(55.0))

        callback.assert_called_once_with(60.0)
        assert _find_control(_rerender(component), ft.TextField).value == "60"

    def test_slider_on_change_clamped_to_max(
        self,
        mock_app_colors_state,
    ) -> None:
        """slider 值超过 max → clamp 到 max。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        _invoke(slider.on_change, _make_event(150.0))

        callback.assert_called_once_with(100.0)

    def test_slider_on_change_clamped_to_min(
        self,
        mock_app_colors_state,
    ) -> None:
        """slider 值低于 min → clamp 到 min。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=10,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        _invoke(slider.on_change, _make_event(-5.0))

        callback.assert_called_once_with(10.0)

    def test_slider_on_change_no_callback_when_none(
        self,
        mock_app_colors_state,
    ) -> None:
        """on_change=None → 不抛异常。"""
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=None,
        )
        run_mount_effects(component)
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        _invoke(slider.on_change, _make_event(55.0))


class TestSliderInputTextInput:
    """SliderInput TextField 输入测试。"""

    def test_text_submit_triggers_on_change(
        self,
        mock_app_colors_state,
    ) -> None:
        """textfield submit → 解析 + snap + on_change，draft 同步。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_submit, _make_event("55"))

        callback.assert_called_once_with(60.0)
        assert _find_control(_rerender(component), ft.TextField).value == "60"

    def test_text_blur_triggers_on_change(
        self,
        mock_app_colors_state,
    ) -> None:
        """textfield blur → 解析 + snap + on_change。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_blur, _make_event("26"))

        callback.assert_called_once_with(30.0)

    def test_invalid_text_input_restores_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """非法输入 → 不触发 on_change，draft 恢复为当前 value。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_blur, _make_event("abc"))

        callback.assert_not_called()
        assert _find_control(_rerender(component), ft.TextField).value == "50"

    def test_empty_text_input_restores_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """空输入 → 不触发 on_change，draft 恢复为当前 value。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_blur, _make_event(""))

        callback.assert_not_called()
        assert _find_control(_rerender(component), ft.TextField).value == "50"

    def test_text_input_same_value_no_callback(
        self,
        mock_app_colors_state,
    ) -> None:
        """输入与当前 value 相同 → 不触发 on_change。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_blur, _make_event("50"))

        callback.assert_not_called()

    def test_text_input_negative_below_min_clamped(
        self,
        mock_app_colors_state,
    ) -> None:
        """输入超出 [min, max] → 解析后 clamp + snap，上抛边界值。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=10,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        run_mount_effects(component)
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        _invoke(text_field.on_blur, _make_event("5"))

        callback.assert_called_once_with(10.0)


class TestSliderInputControlledSync:
    """D1 受控语义：父级 value 驱动 draft（use_effect deps 同步）。"""

    def test_parent_value_change_syncs_draft(
        self,
        mock_app_colors_state,
    ) -> None:
        """父级 value 变化 → use_effect 检测到 deps 变化 → draft 同步为受控值。"""
        component = make_component(SliderInput, value=5.0)
        run_mount_effects(component)
        result = render_once(component)
        assert _find_control(result, ft.TextField).value == "5"

        # 模拟父级以新 value 重渲染：use_effect deps 检测 → set_draft(格式化新值)
        component.kwargs["value"] = 10.0  # type: ignore[index]  [reason: 测试模拟父级 props 变化, 组件 kwargs 为 dict[str, Any] 但 make_component 泛型未暴露 mutable 更新接口]
        run_render_effects(component)
        result = render_once(component)

        assert _find_control(result, ft.TextField).value == "10"
        assert _find_control(result, ft.Slider).value == 10.0

    def test_parent_value_unchanged_no_draft_reset(
        self,
        mock_app_colors_state,
    ) -> None:
        """value 未变时 use_effect 不重跑：编辑中的 draft 不被覆盖。"""
        component = make_component(SliderInput, value=5.0)
        run_mount_effects(component)

        # 用户在 TextField 中输入中间值（未 commit），draft 变为 "7"
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)
        _invoke(text_field.on_change, _make_event("7"))

        # 父级用相同 value 重渲染 → deps 未变 → 不 set_draft，编辑中草稿保留
        run_render_effects(component)
        result = render_once(component)
        assert _find_control(result, ft.TextField).value == "7"


class TestSnapToStepEdgeCases:
    """_snap_to_step 边界分支补强（L32 step<=0 防御分支）。"""

    def test_step_zero_returns_clamped_value(self):
        """step<=0 → 返回 clamp 后的值（不做 snap）。"""

        # step=0 → 不 snap，仅 clamp
        assert _snap_to_step(55, 0, 100, 0) == 55
        # step 负数 → 同样不 snap
        assert _snap_to_step(55, 0, 100, -1) == 55
        # clamp 仍然生效
        assert _snap_to_step(150, 0, 100, 0) == 100
        assert _snap_to_step(-10, 0, 100, 0) == 0
