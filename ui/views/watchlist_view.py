"""watchlist_view — 关注列表视图 (FR-UX-004, Task 4.2).

声明式组件，展示用户关注的股票列表，支持移除、点击查看详情。
- VM 通过 ``use_viewmodel(factory=lambda: WatchlistViewModel())`` 内部模式消费
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 异步操作通过 ``page.run_task`` 调度 (R16); CancelledError 必须 raise (R2)
"""

import asyncio
import logging
import typing

import flet as ft

from ui.components.confirm_dialog import ConfirmDialog
from ui.components.state_views import EmptyState
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.watchlist_view_model import WatchlistRow, WatchlistViewModel

logger = logging.getLogger(__name__)


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _safe_show_toast(page: ft.Page, msg: str, msg_type: str = "info") -> None:
    """page.show_toast 是 main.py 动态挂载的，ft.Page 类型存根未声明。"""
    show_toast = getattr(page, "show_toast", None)
    if show_toast is not None:
        show_toast(msg, msg_type)


def _build_watchlist_row(
    row: WatchlistRow,
    on_remove: typing.Callable[[str], None],
) -> ft.Container:
    """构建单行关注列表项 (股票名 + 代码 + 加入日期 + 备注 + 移除按钮)."""
    name = row.stock_name or row.ts_code
    sub_parts = [row.ts_code]
    if row.added_at:
        sub_parts.append(row.added_at)
    sub_text = " · ".join(sub_parts)

    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.STAR_OUTLINED, color=AppColors.PRIMARY, size=AppStyles.FONT_SIZE_LG),
                ft.Column(
                    [
                        ft.Text(
                            name,
                            size=AppStyles.FONT_SIZE_LG,
                            weight=ft.FontWeight.W_500,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        ft.Text(
                            sub_text,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        *(
                            [ft.Text(row.note, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY)]
                            if row.note
                            else []
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=AppColors.TEXT_SECONDARY,
                    tooltip=I18n.get("watchlist_remove"),
                    on_click=lambda _e: on_remove(row.ts_code),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
    )


@ft.component
def WatchlistView(
    active: bool = True,
) -> ft.Container:
    """关注列表视图 (声明式).

    Args:
        active: 是否为当前激活视图 (控制是否加载数据)
    """
    state, vm = use_viewmodel(factory=lambda: WatchlistViewModel())

    # i18n / theme 订阅
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # 删除确认对话框 state (复用 ConfirmDialog 组件, 消费方驱动 open_state, 防误删)
    confirm_open, set_confirm_open = ft.use_state(False)
    pending_remove_ts_code, set_pending_remove_ts_code = ft.use_state("")

    # --- 加载关注列表 (active 时) ---
    async def _load_effect() -> None:
        if not active:
            return
        await vm.load_watchlist()

    ft.use_effect(_load_effect, dependencies=[active])

    # --- 移除关注 ---
    async def _do_remove(ts_code: str) -> None:
        try:
            await vm.remove_from_watchlist(ts_code)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_removed"), "success")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[WatchlistView] Remove failed: %s", ex, exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_remove_failed"), "error")

    def _on_remove(ts_code: str) -> None:
        # 弹出 ConfirmDialog 二次确认 (防误删); 实际删除在 _do_confirm_remove
        # state setter 隐式依赖 page 上下文 (内部访问 context.page);
        # page 不可用时提前返回, 避免 RuntimeError (参考 home_view._refresh_clicked 的 page guard)
        if _get_page() is None:
            return
        set_pending_remove_ts_code(ts_code)
        set_confirm_open(True)

    def _do_confirm_remove() -> None:
        """ConfirmDialog on_confirm: 执行删除并关闭对话框。"""
        ts_code = pending_remove_ts_code
        set_confirm_open(False)
        set_pending_remove_ts_code("")
        page = _get_page()
        if page is not None:
            page.run_task(_do_remove, ts_code)

    def _do_cancel_remove() -> None:
        """ConfirmDialog on_cancel: 仅关闭对话框, 不执行删除。"""
        set_confirm_open(False)
        set_pending_remove_ts_code("")

    # --- 渲染 ---
    if state.is_loading:
        body = ft.Column(
            [ft.ProgressRing()],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
    elif not state.watchlist_rows:
        body = EmptyState(
            icon=ft.Icons.STAR_BORDER,
            title=I18n.get("watchlist_empty_title"),
            message=I18n.get("watchlist_empty_message"),
        )
    else:
        body = ft.Column(
            [_build_watchlist_row(row, _on_remove) for row in state.watchlist_rows],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=4,
        )

    # 错误提示
    error_banner = None
    if state.load_error is not None:
        error_banner = ft.Container(
            content=ft.Text(
                I18n.get(state.load_error.key, **state.load_error.params),
                color=AppColors.ERROR,
                size=AppStyles.FONT_SIZE_BODY_SM,
            ),
            padding=ft.Padding.all(8),
            bgcolor=AppColors.SURFACE_VARIANT,
            border_radius=8,
        )

    content_controls: list[ft.Control] = []
    if error_banner is not None:
        content_controls.append(error_banner)
    content_controls.append(body)

    # pending 行的显示名 (stock_name 优先, 空则用 ts_code, 与 _build_watchlist_row 一致)
    pending_name = pending_remove_ts_code
    for _r in state.watchlist_rows:
        if _r.ts_code == pending_remove_ts_code:
            pending_name = _r.stock_name or _r.ts_code
            break

    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("watchlist_title"),
                    size=AppStyles.FONT_SIZE_HEADLINE,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Divider(height=1, color=AppColors.DIVIDER),
                ft.Container(content=ft.Column(content_controls, expand=True, spacing=8), expand=True),
                ConfirmDialog(
                    open_state=confirm_open,
                    title=I18n.get("watchlist_confirm_remove_title"),
                    body=I18n.get("watchlist_confirm_remove_body", name=pending_name),
                    on_confirm=_do_confirm_remove,
                    on_cancel=_do_cancel_remove,
                    confirm_text=I18n.get("common_confirm"),
                    cancel_text=I18n.get("common_cancel"),
                ),
            ],
            expand=True,
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        **AppStyles.dashboard_card(padding=AppStyles.SPACING_LG),
        expand=True,
    )
