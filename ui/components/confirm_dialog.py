"""confirm_dialog — 声明式确认对话框组件 (P1-4 批次 2).

提供可复用的 ConfirmDialog 声明式组件，供 ai_brain_tab 等消费方在执行破坏性
操作前弹出确认。组件本身不持有业务状态，``open`` prop 由消费方驱动。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- ``@ft.component`` 函数组件，无 class 子类
- ``ft.use_dialog`` hook 自动挂载/卸载到 page overlay
- 防重入守护: ``open`` prop 由消费方管理；组件渲染期间 state 切换自动清理
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 颜色全部使用 ``AppColors`` 语义 token
"""

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import get_observable_state
from ui.theme import AppColors, AppStyles


@ft.component
def ConfirmDialog(
    open_state: bool = False,
    title: str = "",
    body: str = "",
    on_confirm: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
    confirm_text: str | None = None,
    cancel_text: str | None = None,
) -> ft.Container:
    """确认对话框组件 (P1-4).

    Args:
        open_state: 是否打开 (由消费方驱动，state 切换自动挂载/卸载)。
        title: 标题文案 (已翻译字符串)。
        body: 正文文案 (已翻译字符串)。
        on_confirm: 确认回调 (可选); None 时仅关闭对话框。
        on_cancel: 取消回调 (可选); None 时仅关闭对话框。
        confirm_text: 确认按钮文案 (已翻译字符串); 缺省由消费方传入。
        cancel_text: 取消按钮文案 (已翻译字符串); 缺省由消费方传入。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    def _on_confirm(_e: ft.ControlEvent) -> None:
        if on_confirm is not None:
            on_confirm()

    def _on_cancel(_e: ft.ControlEvent) -> None:
        if on_cancel is not None:
            on_cancel()

    cancel_btn = ft.TextButton(
        content=cancel_text or "",
        on_click=safe_on_click(_on_cancel),
        style=ft.ButtonStyle(color=AppColors.PRIMARY),
    )
    confirm_btn = ft.Button(
        content=confirm_text or "",
        on_click=safe_on_click(_on_confirm),
        style=AppStyles.danger_button(),
    )

    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
            content=ft.Text(body, size=13, color=AppColors.TEXT_SECONDARY),
            actions=[cancel_btn, confirm_btn],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=8),
        )
        if open_state
        else None
    )
    ft.use_dialog(dialog)

    # 宿主容器（不可见，仅承载 use_dialog hook）
    return ft.Container(width=0, height=0)


__all__ = ["ConfirmDialog"]
