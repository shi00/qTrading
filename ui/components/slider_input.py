"""slider_input — Slider + TextField 联动声明式组件 (UX 3.2).

提供 Slider 与 TextField 双向联动的可复用组件，解决鼠标拖动滑块难以
精确定位小数的问题（如止损率 0.035）。用户既可拖动 Slider 粗调，
也可在 TextField 中键入精确数值。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- 普通工厂函数（非 ``@ft.component``），返回 ``ft.Column``，无 class 子类
- 受控组件：``value`` prop 由父级驱动，``on_change`` 上抛新值
- 局部 TextField 光标输入中文本不即时上抛（blur/submit 时 _commit_text 提交），
  避免每次按键触发父级 re-render；父级 ``value`` prop 变化依赖 View 整体
  re-render（工厂函数重建）时同步 TextField 文本
- 颜色全部使用 ``AppColors`` 语义 token，订阅 ``AppColors.get_observable_state`` 自动重渲染
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


def _snap_to_step(value: float, min_val: float, max_val: float, step: float) -> float:
    """将 value snap 到最近的 step 网格点，并 clamp 到 ``[min_val, max_val]``。"""
    clamped = max(min_val, min(max_val, value))
    if step <= 0:
        return clamped
    steps = round((clamped - min_val) / step)
    snapped = min_val + steps * step
    # 浮点修正：避免 0.1+0.2=0.30000000000000004
    return round(snapped, 10)


def _default_fmt(value: float) -> str:
    """默认格式化：整数显示为 int，小数去除浮点噪声与无意义尾随 0，保留完整精度。

    修复 UX-2.2 精度丢失：原实现固定保留 1 位小数，导致 0.035 → "0.0"。
    现按值实际精度输出（3.5 → "3.5"，0.035 → "0.035"），配合 _snap_to_step
    的 round(snapped, 10) 保证无浮点污染。
    """
    if value == int(value):
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def SliderInput(
    value: float = 0.0,
    min_val: float = 0.0,
    max_val: float = 100.0,
    step: float = 1.0,
    divisions: int | None = None,
    width: float | int | None = None,
    fmt: Callable[[float], str] | None = None,
    on_change: Callable[[float], None] | None = None,
    disabled: bool = False,
    label: str | None = None,
    expand: bool = False,
) -> ft.Column:
    """Slider + TextField 联动组件 (UX 3.2)。

    Args:
        value: 当前值（受控 prop，由父级驱动）。
        min_val: 最小值。
        max_val: 最大值。
        step: 步长（用于 snap 与 divisions 计算）。
        divisions: Slider 离散分段数；None 时由 ``(max-min)/step`` 自动计算。
        width: 组件总宽度；None 时不约束（配合 ``expand=True`` 填充父容器）。
        fmt: 值显示格式化函数（如 ``lambda v: f"{v:.1f}%"``）；None 用默认。
        on_change: 值变化回调（已 snap + clamp）；None 时无回调。
        disabled: 是否禁用（Slider 与 TextField 同步禁用）。
        label: 标签文案（已翻译字符串，由父级传入）；None 时不渲染顶部 Text（嵌入场景）。
        expand: 是否在父容器中扩展填充（``Column(expand=True)``）。
    """
    formatter = fmt or _default_fmt
    text_val = formatter(value)

    if divisions is None:
        divisions = int(round((max_val - min_val) / step)) if step > 0 else None

    text_field = ft.TextField(
        value=text_val,
        keyboard_type=ft.KeyboardType.NUMBER,
        dense=True,
        border_color=AppColors.DIVIDER,
        focused_border_color=AppColors.PRIMARY,
        text_size=AppStyles.FONT_SIZE_BODY,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        width=70,
        disabled=disabled,
    )

    slider = ft.Slider(
        min=min_val,
        max=max_val,
        value=value,
        divisions=divisions,
        label="{value}",
        active_color=AppColors.PRIMARY,
        expand=True,
        disabled=disabled,
    )

    def _on_slider_change(e: ft.ControlEvent) -> None:
        raw = get_control_value(e.control, ft.Slider) if e and e.control else value
        try:
            new_val = _snap_to_step(float(raw), min_val, max_val, step)
        except (TypeError, ValueError):
            return
        formatted = formatter(new_val)
        if text_field.value != formatted:
            text_field.value = formatted
            if text_field.page:
                text_field.update()
        if on_change is not None:
            on_change(new_val)

    def _commit_text() -> None:
        """解析 TextField 文本，clamp + snap 后上抛 on_change。

        空输入或非法输入时恢复为当前 ``value`` prop 的格式化值（不调用 on_change）。
        """
        raw = (text_field.value or "").strip()
        if not raw:
            text_field.value = formatter(value)
            if text_field.page:
                text_field.update()
            return
        try:
            parsed = float(raw)
        except ValueError:
            text_field.value = formatter(value)
            if text_field.page:
                text_field.update()
            return
        new_val = _snap_to_step(parsed, min_val, max_val, step)
        formatted = formatter(new_val)
        text_field.value = formatted
        if slider.value != new_val:
            slider.value = new_val
            if slider.page:
                slider.update()
        if text_field.page:
            text_field.update()
        if new_val != value and on_change is not None:
            on_change(new_val)

    def _on_text_submit(_e: ft.ControlEvent) -> None:
        _commit_text()

    def _on_text_blur(_e: ft.ControlEvent) -> None:
        _commit_text()

    slider.on_change = safe_on_change(_on_slider_change)
    text_field.on_submit = safe_on_change(_on_text_submit)
    text_field.on_blur = safe_on_change(_on_text_blur)

    column_controls: list = []
    if label is not None:
        column_controls.append(
            ft.Text(
                label,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    # 根因防范: 将包含 Slider(expand=True) 的水平 Row 包裹在固定 height=38
    # 的 Container 约束中，在 SliderInput 内部提供明确的垂直高度界限。
    column_controls.append(
        ft.Container(
            content=ft.Row(
                [slider, text_field],
                spacing=8,
                alignment=ft.MainAxisAlignment.START,
            ),
            height=38,
        )
    )
    return ft.Column(
        column_controls,
        spacing=2,
        width=width,
        expand=expand,
    )


__all__ = ["SliderInput"]
