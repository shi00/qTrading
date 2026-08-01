"""error_history_dialog — 错误历史通知中心构建函数 (Issue #448).

检视修订 (MAJOR-1/M6): 改为模块级纯函数 ``build_error_history_dialog()``,
在 ``app_layout`` 内用 ``ft.use_dialog`` 挂载 (对齐 task_center_view.py L499-546
的 details_dialog 模式)。状态订阅只在 app_layout 中发生, 消除重复订阅。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- 纯函数构建 ``ft.AlertDialog``, 不持有状态, 不订阅 Observable
- i18n/theme 由调用方在挂载上下文中订阅, 本函数直接调用 ``I18n.get`` / ``AppColors``
- ``ft.use_dialog(None)`` 不挂载模式对齐 task_center_view.py L539-540
"""

from collections.abc import Callable

import flet as ft

from ui.components.error_history_store import ErrorHistoryEntry, clear_history
from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

_DIALOG_WIDTH = 520
_DIALOG_HEIGHT = 420
_ENTRY_PADDING_ALL = 10
_ENTRY_BORDER_RADIUS = 6
_ENTRY_DETAILS_MAX_LINES = 5


def build_error_history_dialog(
    is_open: bool,
    on_close: Callable[[], None],
    errors: list[ErrorHistoryEntry],
) -> ft.AlertDialog | None:
    """构建错误历史 ``AlertDialog`` (纯函数, 由 ``app_layout`` 调用 ``ft.use_dialog`` 挂载).

    Args:
        is_open: 是否打开 (由 ``app_layout`` ``use_state`` 控制).
        on_close: 关闭回调 (由 ``app_layout`` 注入, 通常 ``lambda: set_open(False)``).
        errors: 错误历史列表 (由 ``app_layout`` 从 ``get_global_state().errors`` 读取).
            入参已由 ``record_error`` 内部强制 R9 脱敏 (v3 M1 修复).

    Returns:
        ``ft.AlertDialog`` 实例; ``is_open=False`` 时返回 ``None`` (``ft.use_dialog(None)``
        不挂载, 对齐 task_center_view.py L539-540).
    """
    if not is_open:
        return None  # ft.use_dialog(None) 不挂载

    if errors:
        error_items: list[ft.Control] = [_build_error_entry(entry) for entry in errors]
    else:
        error_items = [
            ft.Container(
                content=ft.Text(
                    I18n.get("error_history_empty"),
                    color=AppColors.TEXT_SECONDARY,
                    size=AppStyles.FONT_SIZE_BODY,
                    text_align=ft.TextAlign.CENTER,
                ),
                padding=AppStyles.SPACING_LG,
            )
        ]

    return ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Icon(
                    ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED,
                    color=AppColors.PRIMARY,
                ),
                ft.Text(
                    I18n.get("error_history_title"),
                    size=AppStyles.FONT_SIZE_TITLE,
                    weight=ft.FontWeight.BOLD,
                ),
            ],
            spacing=8,
        ),
        content=ft.Container(
            content=ft.Column(
                error_items,
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
            ),
            width=_DIALOG_WIDTH,
            height=_DIALOG_HEIGHT,
        ),
        actions=[
            ft.TextButton(
                content=I18n.get("error_history_clear"),
                icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                on_click=safe_on_click(lambda _e: clear_history()),
                style=ft.ButtonStyle(color=AppColors.ERROR),
            ),
            ft.TextButton(
                content=I18n.get("common_close"),
                on_click=safe_on_click(lambda _e: on_close()),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _build_error_entry(entry: ErrorHistoryEntry) -> ft.Container:
    """构建单条错误历史项 (含时间戳/来源/标题/消息/详情).

    纯函数 (非 @ft.component): 展开状态由 Dialog 整体重渲染处理, details 始终展示
    (``max_lines=_ENTRY_DETAILS_MAX_LINES`` 折叠 + ``selectable`` 方便复制).
    """
    time_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    # 来源标签 (i18n key 约定: error_source_<source>)
    source_label = I18n.get(f"error_source_{entry.source}", entry.source)

    column_controls: list[ft.Control] = [
        ft.Row(
            [
                ft.Icon(
                    ft.Icons.ERROR_OUTLINE,
                    color=AppColors.ERROR,
                    size=AppStyles.FONT_SIZE_LG,
                ),
                ft.Text(
                    entry.title,
                    size=AppStyles.FONT_SIZE_BODY,
                    weight=ft.FontWeight.W_600,
                    color=AppColors.TEXT_PRIMARY,
                    expand=True,
                ),
            ],
            spacing=8,
        ),
        ft.Text(
            entry.message,
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_SECONDARY,
        ),
        ft.Row(
            [
                ft.Text(
                    f"{source_label} · {time_str}",
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                    italic=True,
                ),
            ],
            spacing=4,
        ),
    ]

    # details (非空时展示, selectable 方便复制)
    if entry.details:
        column_controls.append(
            ft.Text(
                entry.details,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
                selectable=True,
                max_lines=_ENTRY_DETAILS_MAX_LINES,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        )

    return ft.Container(
        content=ft.Column(
            column_controls,
            spacing=4,
        ),
        padding=ft.Padding.all(_ENTRY_PADDING_ALL),
        border_radius=_ENTRY_BORDER_RADIUS,
        border=ft.Border.all(1, AppColors.BORDER),
        bgcolor=AppColors.SURFACE,
    )


__all__ = ["build_error_history_dialog"]
