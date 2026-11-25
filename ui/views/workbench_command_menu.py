"""「＋指令与引用」菜单（W-18）。

职责边界：本模块**只**构造菜单并转发回调，不实现指令解析、资料引用、Skill/MCP
选择器——这些选择器已存在于研究侧 Composer 与设置页，本菜单仅提供统一入口与
回调接线（调用方决定插入 token 或打开既有选择器）。

被 ``ui/views/workbench_panel.py`` 的概览视角复用；不修改研究观察面板现有文件。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n


def _bind(handler: Callable[[], None]) -> Callable[[ft.ControlEvent], None]:
    """把无参回调适配为 ``on_click`` 事件处理器。"""

    def _on_click(_event: ft.ControlEvent) -> None:
        handler()

    return _on_click


def build_command_reference_menu(
    *,
    on_command: Callable[[], None],
    on_reference: Callable[[], None],
    on_skill: Callable[[], None],
    on_mcp: Callable[[], None],
) -> ft.PopupMenuButton:
    """构造「＋指令与引用」菜单（指令 / 资料引用 / Skill 引用 / MCP 引用）。

    Args:
        on_command: 指令入口回调（如插入 ``/``）。
        on_reference: 资料引用入口回调（如插入 ``@``）。
        on_skill: Skill 引用入口回调（如插入 ``@skill``）。
        on_mcp: MCP 引用入口回调（如插入 ``@mcp``）。

    Returns:
        带四个显式菜单项的 ``ft.PopupMenuButton``；每项点击只调对应回调。
    """
    return ft.PopupMenuButton(
        icon=ft.Icons.ADD,
        tooltip=I18n.get("agent_composer_menu_open"),
        items=[
            ft.PopupMenuItem(
                content=I18n.get("agent_composer_menu_command"),
                icon=ft.Icons.TERMINAL,
                on_click=safe_on_click(_bind(on_command)),
            ),
            ft.PopupMenuItem(
                content=I18n.get("agent_composer_menu_reference"),
                icon=ft.Icons.ATTACH_FILE,
                on_click=safe_on_click(_bind(on_reference)),
            ),
            ft.PopupMenuItem(
                content=I18n.get("agent_composer_menu_skill"),
                icon=ft.Icons.EXTENSION,
                on_click=safe_on_click(_bind(on_skill)),
            ),
            ft.PopupMenuItem(
                content=I18n.get("agent_composer_menu_mcp"),
                icon=ft.Icons.HUB,
                on_click=safe_on_click(_bind(on_mcp)),
            ),
        ],
    )
