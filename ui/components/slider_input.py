"""slider_input — Slider + TextField 联动声明式组件 (UX 3.2).

提供 Slider 与 TextField 双向联动的可复用组件，解决鼠标拖动滑块难以
精确定位小数的问题（如止损率 0.035）。用户既可拖动 Slider 粗调，
也可在 TextField 中键入精确数值。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI, 修复报告 04 D1):
- ``@ft.component`` 声明式函数组件，返回 ``ft.Column``，无 class 子类
- 受控组件：``value`` prop 由父级驱动，``on_change`` 上抛新值
- 编辑中间态由 ``use_state`` 的 ``draft`` 承载：TextField 光标输入中不即时
  上抛（blur/submit 时 commit），避免每次按键触发 re-render 重建控件丢焦点；
  父级 ``value`` prop 变化经 ``use_effect([value])`` 同步到 draft
- 零命令式 ``.update()`` / ``.value = `` 赋值（全部由状态驱动）
- 颜色全部使用 ``AppColors`` 语义 token，订阅 ``AppColors.get_observable_state`` 自动重渲染
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


def _snap_to_step(value: float, min_val: float, max_val: float, step: float) -> float:
    """将 value snap 到最近的 step 网格点，并 clamp 到 ``[min_val, max_val]``。

    snap 之后再执行一次夹紧，端点优先于网格点：``step`` 不整除区间时，离端点
    最近的网格点可能落在区间外（如 min=0, max=1, step=0.6 的输入 1 → 网格点
    1.2），此时返回区间端点而非越界值。``step <= 0`` 时不做 snap，仅夹紧。
    """
    clamped = max(min_val, min(max_val, value))
    if step <= 0:
        return clamped
    steps = round((clamped - min_val) / step)
    snapped = min_val + steps * step
    # 浮点修正：避免 0.1+0.2=0.30000000000000004
    snapped = round(snapped, 10)
    # 端点优先于网格点：非整除区间的网格点可能越界，夹紧回区间后返回，
    # 保证 on_change 上抛值恒在 [min_val, max_val]（公开契约）。
    return max(min_val, min(max_val, snapped))


def _default_fmt(value: float) -> str:
    """默认格式化：整数显示为 int，小数去除浮点噪声与无意义尾随 0，保留完整精度。

    修复 UX-2.2 精度丢失：原实现固定保留 1 位小数，导致 0.035 → "0.0"。
    现按值实际精度输出（3.5 → "3.5"，0.035 → "0.035"），配合 _snap_to_step
    的 round(snapped, 10) 保证无浮点污染。
    """
    if value == int(value):
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def _parse_text_value(text: str, unit_suffix: str | None) -> float | None:
    """解析输入框文本为有限浮点数；空串/非法/非有限值返回 ``None``。

    输入框只承载裸数值，单位由 ``unit_suffix`` 独立展示。为兼容用户从旧显示
    或外部粘贴的带单位文本（如 ``4‱`` / ``7 bps``），解析前剥离本组件声明的
    已知单位后缀；不猜测任意 ``fmt`` 函数的逆运算。
    """
    stripped = text.strip()
    if not stripped:
        return None
    if unit_suffix:
        unit = unit_suffix.strip()
        if unit and stripped.endswith(unit):
            stripped = stripped[: -len(unit)].rstrip()
            if not stripped:
                return None
    try:
        parsed = float(stripped)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


@ft.component
def SliderInput(
    value: float = 0.0,
    min_val: float = 0.0,
    max_val: float = 100.0,
    step: float = 1.0,
    divisions: int | None = None,
    width: float | int | None = None,
    unit_suffix: str | None = None,
    on_change: Callable[[float], None] | None = None,
    disabled: bool = False,
    label: str | None = None,
    accessible_name: str | None = None,
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
        unit_suffix: 单位后缀文本（如 ``"‱"`` / ``" ‰"`` / ``" bps"``），仅作展示并
            在解析时剥离；None 时无单位。输入框始终只承载裸数值，单位不进入可编辑文本。
        on_change: 值变化回调（已 snap + clamp）；None 时无回调。
        disabled: 是否禁用（Slider 与 TextField 同步禁用）。
        label: 标签文案（已翻译字符串，由父级传入）；None 时不渲染顶部 Text（嵌入场景）。
        accessible_name: 无障碍语义名称（已翻译字符串，F08）。``label`` 为 None 的嵌入
            场景（标题在外部布局）由调用方明确提供，作为 TextField/Slider 的 tooltip
            语义名称（读屏朗读 + 悬停辅助，不改变静态布局）；None 时回退到 ``label``。
            参数较多时用于区分同页同名 SliderInput 实例。
        expand: 是否在父容器中扩展填充（``Column(expand=True)``）。
    """
    # i18n 变更自动重渲染（错误提示文案随 locale 切换）。
    ft.use_state(get_observable_state)
    # F08: 语义名称解析——嵌入场景（label=None）必须由调用方提供 accessible_name，
    # 否则 Tab/读屏聚焦时无法区分同页参数（accessibility-baseline.md §2.1）。
    semantic_name = accessible_name or label

    # 编辑中间态：TextField 光标输入中不即时上抛；父级 value 变化由 use_effect 同步。
    # use_state setter 有浅值比较，相同值不触发 re-render（不打断输入/拖动）。
    draft, set_draft = ft.use_state(lambda: _default_fmt(value))
    # 非法/越界输入的可见反馈；空串表示无错误（不静默还原用户输入）。
    error_text, set_error_text = ft.use_state(lambda: "")

    def _sync_draft_from_value(new_value: float) -> None:
        set_draft(_default_fmt(new_value))
        set_error_text("")

    ft.use_effect(lambda: _sync_draft_from_value(value), [value])

    if divisions is None:
        divisions = round((max_val - min_val) / step) if step > 0 else None  # RUF046: round 已返回 int，无需再 int()

    def _on_slider_change(e: ft.ControlEvent) -> None:
        raw = get_control_value(e.control, ft.Slider) if e and e.control else value
        try:
            new_val = _snap_to_step(float(raw), min_val, max_val, step)
        except (TypeError, ValueError):
            return
        set_draft(_default_fmt(new_val))
        set_error_text("")
        if on_change is not None:
            on_change(new_val)

    def _commit_text(raw: str | None) -> None:
        """解析 TextField 文本，clamp + snap 后上抛 on_change。

        空/非法/非有限输入保留用户文本并显示错误（不静默还原、不调用 on_change）；
        越界输入 clamp 到边界并显示范围提示后提交；合法输入清除错误。
        """
        text = (raw or "").strip()
        parsed = _parse_text_value(text, unit_suffix)
        if parsed is None:
            if raw is not None:
                set_draft(text)
            set_error_text(I18n.get("screener_param_invalid"))
            return
        new_val = _snap_to_step(parsed, min_val, max_val, step)
        if parsed < min_val or parsed > max_val:
            set_error_text(
                I18n.get(
                    "sys_err_out_of_range_fmt",
                    min=_default_fmt(min_val),
                    max=_default_fmt(max_val),
                )
            )
        else:
            set_error_text("")
        set_draft(_default_fmt(new_val))
        if new_val != value and on_change is not None:
            on_change(new_val)

    def _on_text_change(e: ft.ControlEvent) -> None:
        """输入中仅更新草稿（不 commit），避免父级重渲染覆盖编辑中文本。

        commit 延迟到 blur/submit，保证光标输入不被打断、不跳回。
        """
        if e and e.control:
            set_draft(get_control_value(e.control, ft.TextField))
            set_error_text("")

    def _on_text_submit(e: ft.ControlEvent) -> None:
        _commit_text(get_control_value(e.control, ft.TextField) if e and e.control else None)

    def _on_text_blur(e: ft.ControlEvent) -> None:
        _commit_text(get_control_value(e.control, ft.TextField) if e and e.control else None)

    unit_control = (
        ft.Text(unit_suffix, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY) if unit_suffix else None
    )
    text_field = ft.TextField(
        value=draft,
        keyboard_type=ft.KeyboardType.NUMBER,
        dense=True,
        tooltip=semantic_name,
        suffix=unit_control,
        error=error_text or None,
        border={
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.DIVIDER)),
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
        },
        text_size=AppStyles.FONT_SIZE_BODY,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        width=70,
        disabled=disabled,
        on_change=safe_on_change(_on_text_change),
        on_submit=safe_on_change(_on_text_submit),
        on_blur=safe_on_change(_on_text_blur),
    )

    slider_label = f"{{value}}{unit_suffix}" if unit_suffix else "{value}"
    slider = ft.Slider(
        min=min_val,
        max=max_val,
        value=value,
        divisions=divisions,
        label=slider_label,
        tooltip=semantic_name,
        active_color=AppColors.PRIMARY,
        expand=True,
        disabled=disabled,
        on_change=safe_on_change(_on_slider_change),
    )

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
    # 错误文案在输入框下方占额外高度，出错时临时增高，避免反馈被裁切。
    row_height = 60 if error_text else 38
    column_controls.append(
        ft.Container(
            content=ft.Row(
                [slider, text_field],
                spacing=8,
                alignment=ft.MainAxisAlignment.START,
            ),
            height=row_height,
        )
    )
    return ft.Column(
        column_controls,
        spacing=2,
        width=width,
        expand=expand,
    )


__all__ = ["SliderInput"]
