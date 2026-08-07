"""ui/components/slider_input.py 单元测试 (UX 3.2).

测试 SliderInput 组件的行为：
1. 渲染契约：label 有值/None、expand、disabled
2. fmt 格式化：默认与自定义
3. divisions 自动计算
4. 事件交互：slider on_change (snap+clamp)、text on_change/blur/submit
5. value prop 变化时同步 TextField

测试范式参考 test_screener_view_spike.py (make_component + render_once).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects
from ui.components.slider_input import SliderInput

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_schedule_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """静默 ``Component._schedule_update``，避免 ``context.page`` RuntimeError。

    ``set_text_value`` 内部触发 ``_schedule_update``，后者调用
    ``context.page.session.schedule_update(self)``。``make_component + render_once``
    路径不绑定 FakePage（``_context_page`` 为 None），导致 RuntimeError。

    本 fixture 作用域限本文件，不影响 ``test_resizable_splitter_body.py`` 等
    依赖 ``page.session.scheduled_updates`` 断言的测试。
    """
    monkeypatch.setattr(Component, "_schedule_update", lambda self: None)


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
                return _find_control(children, ctrl_type)
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


def _setup(kwargs: dict) -> tuple[Any, Any]:
    """创建 SliderInput Component，注入 FakePage 并渲染，返回 (result, component)。

    用 run_mount_effects (内部 attach_fake_page + render_once + _run_mount_effects)
    保持 _context_page 在事件处理器调用时仍有效（参考 test_screener_view_runtime.py
    screener_view_env fixture 模式）。
    """
    component = make_component(SliderInput, **kwargs)
    run_mount_effects(component)
    result = render_once(component)
    return result, component


class TestSliderInputRender:
    """SliderInput 渲染契约测试。"""

    def test_renders_column_with_label(
        self,
        mock_app_colors_state,
    ) -> None:
        """label 有值 → Column 包含 Text + Row(Slider + TextField)。"""
        result, _ = _setup({"label": "止损率", "value": 5.0})

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
        result, _ = _setup({"label": None, "value": 5.0})

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
        result, _ = _setup({"value": 5.0, "expand": True})

        assert result.expand is True

    def test_expand_false_default(
        self,
        mock_app_colors_state,
    ) -> None:
        """expand 默认 False → Column.expand 未设置或 False。"""
        result, _ = _setup({"value": 5.0})

        assert not result.expand

    def test_disabled_propagates_to_slider_and_textfield(
        self,
        mock_app_colors_state,
    ) -> None:
        """disabled=True → Slider 与 TextField 同步禁用。"""
        result, _ = _setup({"value": 5.0, "disabled": True})

        slider = _find_control(result, ft.Slider)
        text_field = _find_control(result, ft.TextField)
        assert slider is not None  # noqa: weak-assertion null-check before asserting disabled property
        assert text_field is not None  # noqa: weak-assertion null-check before asserting disabled property
        assert slider.disabled is True
        assert text_field.disabled is True

    def test_disabled_false_default(
        self,
        mock_app_colors_state,
    ) -> None:
        """disabled 默认 False → Slider 与 TextField 均可编辑。"""
        result, _ = _setup({"value": 5.0})

        slider = _find_control(result, ft.Slider)
        text_field = _find_control(result, ft.TextField)
        assert slider.disabled is False
        assert text_field.disabled is False


class TestSliderInputFormat:
    """SliderInput fmt 格式化测试。"""

    def test_default_fmt_integer_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：整数值显示为 int。"""
        result, _ = _setup({"value": 5.0})

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "5"

    def test_default_fmt_float_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """默认 fmt：小数保留 1 位。"""
        result, _ = _setup({"value": 3.5})

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "3.5"

    def test_custom_fmt_formats_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """自定义 fmt：按 fmt 函数格式化。"""
        component = make_component(
            SliderInput,
            value=3.0,
            fmt=lambda v: f"{v:.1f}%",
        )
        result = render_once(component)

        text_field = _find_control(result, ft.TextField)
        assert text_field.value == "3.0%"


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
        result = render_once(component)

        slider = _find_control(result, ft.Slider)
        assert slider.divisions == 10  # (100-0)/10 = 10


class TestSliderInputOnChange:
    """SliderInput on_change 回调测试。"""

    def test_slider_on_change_triggers_callback(
        self,
        mock_app_colors_state,
    ) -> None:
        """slider 拖动 → on_change 被调用（snap 后的值）。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        # 模拟 slider 拖动到 55
        e = MagicMock()
        e.control.value = 55.0
        slider.on_change(e)

        callback.assert_called_once_with(60.0)  # snap 到最近的 10 的倍数

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
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        e = MagicMock()
        e.control.value = 150.0
        slider.on_change(e)

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
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        e = MagicMock()
        e.control.value = -5.0
        slider.on_change(e)

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
        result = render_once(component)
        slider = _find_control(result, ft.Slider)

        e = MagicMock()
        e.control.value = 55.0
        # 不应抛异常
        slider.on_change(e)


class TestSliderInputTextInput:
    """SliderInput TextField 输入测试。"""

    def test_text_submit_triggers_on_change(
        self,
        mock_app_colors_state,
    ) -> None:
        """textfield submit → 解析 + snap + on_change。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=0.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        # 模拟用户输入 "55"：on_change 调用 set_text_value("55")
        e_change = MagicMock()
        e_change.control.value = "55"
        text_field.on_change(e_change)

        # 声明式范式：set_text_value 后需 re-render 让闭包 text_value 更新为新值，
        # 否则 _commit_text 读到的 text_value 仍是旧值 "0"，导致 new_val == value 不触发回调
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        # submit 触发 _commit_text：从已更新的 text_value="55" 解析 → snap 到 60
        e_submit = MagicMock()
        text_field.on_submit(e_submit)

        callback.assert_called_once_with(60.0)  # snap 到 60

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
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_change = MagicMock()
        e_change.control.value = "26"
        text_field.on_change(e_change)

        # re-render 让闭包 text_value 更新为 "26"（同上）
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        # blur 触发 _commit_text：从 text_value="26" 解析 → snap 到 30
        # (注: 不用 25 是因 Python round() 用 banker's rounding, round(2.5)=2 → snap 到 20)
        e_blur = MagicMock()
        text_field.on_blur(e_blur)

        callback.assert_called_once_with(30.0)  # snap 到 30

    def test_invalid_text_input_restores_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """非法输入 → 不触发 on_change，恢复为当前 value。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        # 输入非法值 "abc"
        e_change = MagicMock()
        e_change.control.value = "abc"
        text_field.on_change(e_change)

        # re-render 让闭包 text_value 更新为 "abc"
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_blur = MagicMock()
        text_field.on_blur(e_blur)

        callback.assert_not_called()

    def test_empty_text_input_restores_value(
        self,
        mock_app_colors_state,
    ) -> None:
        """空输入 → 不触发 on_change，恢复为当前 value。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_change = MagicMock()
        e_change.control.value = ""
        text_field.on_change(e_change)

        # re-render 让闭包 text_value 更新为 ""
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_blur = MagicMock()
        text_field.on_blur(e_blur)

        callback.assert_not_called()

    def test_text_input_same_value_no_callback(
        self,
        mock_app_colors_state,
    ) -> None:
        """输入与当前 value 相同 → 不触发 on_change（避免冗余回调）。"""
        callback = MagicMock()
        component = make_component(
            SliderInput,
            value=50.0,
            min_val=0,
            max_val=100,
            step=10.0,
            on_change=callback,
        )
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_change = MagicMock()
        e_change.control.value = "50"
        text_field.on_change(e_change)

        # re-render 让闭包 text_value 更新为 "50"
        result = render_once(component)
        text_field = _find_control(result, ft.TextField)

        e_blur = MagicMock()
        text_field.on_blur(e_blur)

        callback.assert_not_called()  # 值未变化，不回调


class TestSnapToStepEdgeCases:
    """_snap_to_step 边界分支补强（L32 step<=0 防御分支）。"""

    def test_step_zero_returns_clamped_value(self):
        """step<=0 → 返回 clamp 后的值（不做 snap）。"""
        from ui.components.slider_input import _snap_to_step

        # step=0 → 不 snap，仅 clamp
        assert _snap_to_step(55, 0, 100, 0) == 55
        # step 负数 → 同样不 snap
        assert _snap_to_step(55, 0, 100, -1) == 55
        # clamp 仍然生效
        assert _snap_to_step(150, 0, 100, 0) == 100
        assert _snap_to_step(-10, 0, 100, 0) == 0
