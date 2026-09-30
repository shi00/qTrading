"""confirm_dialog — 声明式确认对话框组件 (P1-4 批次 2).

提供可复用的 ConfirmDialog 声明式组件，供消费方在执行破坏性操作或需要用户二次
确认时弹出。组件本身不持有业务状态，``open`` prop 由消费方驱动。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- ``@ft.component`` 函数组件，无 class 子类
- ``ft.use_dialog`` hook 自动挂载/卸载到 page overlay
- 防重入守护: ``open`` prop 由消费方管理；组件渲染期间 state 切换自动清理
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 颜色全部使用 ``AppColors`` 语义 token；按钮样式使用 ``AppStyles``
- ``destructive`` 控制确认按钮样式 (破坏性=danger, 默认=primary)；
  ``AlertDialog(scrollable=True)`` 承载超长正文
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
    destructive: bool = False,
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
        destructive: 是否为破坏性操作 (删除/重置/清空等不可逆动作)。True 时确认按钮
            使用危险色 (``AppStyles.danger_button()``); 默认 False 使用普通主按钮样式
            (``AppStyles.primary_button()``), 避免非破坏性确认 (AI 外发确认、"继续"等)
            被误标为危险操作。
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
    # 确认按钮样式随操作性质切换: 破坏性操作保留危险色, 其余用普通主按钮样式
    # (MINOR-05: 原实现固定 danger_button, 把 AI 外发确认/"继续"等非破坏性确认也标成红色)。
    confirm_style = AppStyles.danger_button() if destructive else AppStyles.primary_button()
    confirm_btn = ft.Button(
        content=confirm_text or "",
        on_click=safe_on_click(_on_confirm),
        style=confirm_style,
    )

    dialog = (
        ft.AlertDialog(
            modal=True,
            # 正文超长时由 Flet 把 title/content 包进可滚动容器, 其可用高度上限由对话框
            # 视口约束 (inset_padding 内) 派生 —— 长正文 (如 AI 外发数据预览) 在对话框内
            # 滚动而非撑破对话框 (MINOR-05)。
            scrollable=True,
            title=ft.Text(title, size=AppStyles.FONT_SIZE_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Text(body, size=AppStyles.FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY),
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
