"""watchlist_add_dialog — 关注列表「添加关注」声明式对话框组件 (issue #433).

提供搜索框 + 搜索结果列表 + 备注输入 + 确认/取消 的添加关注对话框：
- 受控组件：搜索数据状态 (``search_results``/``is_searching``/``search_error``)
  由父级 (WatchlistView) 从 VM state 传入；搜索触发经 ``on_search`` 回调
  (父级 ``page.run_task`` 调度 VM 命令, R16)
- 本地 UI 态：``keyword`` / ``note`` / 选中结果由 ``use_state`` 管理
- 必须从搜索结果中选中一只股票后才能确认添加 (避免无效 ts_code 入库)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- ``@ft.component`` 函数组件，无 class 子类
- ``ft.use_dialog`` hook 自动挂载/卸载到 page overlay
- ``open`` prop 由消费方驱动，state 切换自动清理
"""

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click, safe_controls
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.watchlist_view_model import StockSearchRow

# NOTE(lazy): 搜索结果区固定高度 + 内部滚动，限制 dialog 高度. ceiling: 最多展示
# ``search_stocks`` 默认 limit=10 条. upgrade: 搜索结果上限调高或改为分页时重估.
_RESULT_AREA_HEIGHT = 180


def _build_result_row(
    row: StockSearchRow,
    selected_ts_code: str,
    on_select: Callable[[StockSearchRow], None],
) -> ft.Container:
    """搜索结果行：点击选中，选中态高亮 (PRIMARY_CONTAINER + STAR icon)."""
    is_selected = selected_ts_code == row.ts_code
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(
                    ft.Icons.STAR if is_selected else ft.Icons.STAR_OUTLINE,
                    color=AppColors.PRIMARY,
                    size=AppStyles.FONT_SIZE_BODY,
                ),
                ft.Column(
                    [
                        ft.Text(
                            row.name or row.ts_code,
                            size=AppStyles.FONT_SIZE_BODY,
                            weight=ft.FontWeight.W_500 if is_selected else None,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        ft.Text(row.ts_code, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY),
                    ],
                    spacing=0,
                    expand=True,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        border_radius=6,
        bgcolor=AppColors.PRIMARY_DARK if is_selected else None,
        on_click=safe_on_click(lambda _e: on_select(row)),
    )


@ft.component
def WatchlistAddDialog(
    open_state: bool = False,
    search_results: tuple[StockSearchRow, ...] = (),
    is_searching: bool = False,
    search_error: Message | None = None,
    on_search: Callable[[str], None] | None = None,
    on_add: Callable[[str, str, str], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ft.Container:
    """「添加关注」对话框组件 (issue #433).

    Args:
        open_state: 是否打开 (由消费方驱动)。
        search_results: VM 搜索返回的股票候选列表。
        is_searching: 搜索请求进行中。
        search_error: 搜索失败信息 (i18n Message)。
        on_search: 触发搜索回调 (keyword)。
        on_add: 确认添加回调 (ts_code, stock_name, note)。
        on_close: 取消/关闭回调。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    keyword, set_keyword = ft.use_state("")
    note, set_note = ft.use_state("")
    selected_ts_code, set_selected_ts_code = ft.use_state("")
    selected_name, set_selected_name = ft.use_state("")

    def _reset_effect() -> None:
        """每次打开时重置本地输入状态 (避免上次残留)."""
        if open_state:
            set_keyword("")
            set_note("")
            set_selected_ts_code("")
            set_selected_name("")

    ft.use_effect(_reset_effect, dependencies=[open_state])

    def _on_keyword_change(e: ft.ControlEvent) -> None:
        """输入变化时更新 keyword 并清空选中 (旧选中可能不匹配新关键词)."""
        set_keyword(get_control_value(e.control, ft.TextField))
        set_selected_ts_code("")
        set_selected_name("")

    def _on_search(_e: ft.ControlEvent | None = None) -> None:
        """触发搜索 (TextField on_submit / 搜索按钮共用); keyword 为空时不发起."""
        if on_search is not None and keyword.strip():
            on_search(keyword)

    def _on_select(row: StockSearchRow) -> None:
        set_selected_ts_code(row.ts_code)
        set_selected_name(row.name)

    def _on_confirm(_e: ft.ControlEvent) -> None:
        """确认添加: 仅当已选中搜索结果时调用 on_add (按钮 disabled 双保险)."""
        if selected_ts_code and on_add is not None:
            on_add(selected_ts_code, selected_name, note.strip())

    def _on_cancel(_e: ft.ControlEvent) -> None:
        if on_close is not None:
            on_close()

    search_field = ft.TextField(
        value=keyword,
        hint_text=I18n.get("watchlist_add_search_hint"),
        dense=True,
        expand=True,
        on_change=safe_on_change(_on_keyword_change),
        on_submit=safe_on_click(_on_search),
    )

    # --- 搜索结果区 (条件渲染) ---
    if is_searching:
        result_area = ft.Row(
            [ft.ProgressRing(width=16, height=16), ft.Text(I18n.get("watchlist_add_searching"))],
            spacing=8,
        )
    elif search_error is not None:
        result_area = ft.Text(
            I18n.get(search_error.key, **search_error.params),
            color=AppColors.ERROR,
            size=AppStyles.FONT_SIZE_BODY_SM,
        )
    elif search_results:
        rows = [_build_result_row(row, selected_ts_code, _on_select) for row in search_results]
        result_area = ft.Container(
            content=ft.Column(safe_controls(rows), scroll=ft.ScrollMode.AUTO, spacing=2),
            height=_RESULT_AREA_HEIGHT,
            border=ft.Border.all(1, AppColors.DIVIDER),
            border_radius=8,
            padding=ft.Padding.all(4),
        )
    elif keyword.strip():
        result_area = ft.Text(
            I18n.get("watchlist_add_no_result"),
            color=AppColors.TEXT_SECONDARY,
            size=AppStyles.FONT_SIZE_BODY_SM,
        )
    else:
        result_area = ft.Text(
            I18n.get("watchlist_add_search_prompt"),
            color=AppColors.TEXT_SECONDARY,
            size=AppStyles.FONT_SIZE_BODY_SM,
        )

    note_field = ft.TextField(
        value=note,
        label=I18n.get("watchlist_add_note_label"),
        hint_text=I18n.get("watchlist_add_note_hint"),
        dense=True,
        on_change=safe_on_change(lambda e: set_note(get_control_value(e.control, ft.TextField))),
    )

    content = ft.Column(
        [search_field, result_area, note_field],
        spacing=8,
        width=380,
        tight=True,
    )

    cancel_btn = ft.TextButton(
        content=I18n.get("common_cancel"),
        on_click=safe_on_click(_on_cancel),
        style=ft.ButtonStyle(color=AppColors.PRIMARY),
    )
    confirm_btn = ft.Button(
        content=I18n.get("watchlist_add"),
        disabled=not selected_ts_code,
        on_click=safe_on_click(_on_confirm),
        style=AppStyles.primary_button(),
    )

    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(
                I18n.get("watchlist_add_dialog_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
            ),
            content=content,
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


__all__ = ["WatchlistAddDialog"]
