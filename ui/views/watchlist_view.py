"""watchlist_view — 关注列表视图 (FR-UX-004, Task 4.2).

声明式组件，展示用户关注的股票列表，支持添加、移除、查看个股（UX-04: 深链跳
选股页并按代码过滤）。
- VM 通过 ``use_viewmodel(factory=lambda: WatchlistViewModel())`` 内部模式消费
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 异步操作通过 ``page.run_task`` 调度 (R16); CancelledError 必须 raise (R2)
"""

import asyncio
import logging
import typing

import flet as ft

from ui.components.confirm_dialog import ConfirmDialog
from ui.components.flet_type_helpers import safe_on_click
from ui.components.state_views import GITHUB_ISSUES_URL, EmptyState, ErrorState
from ui.components.watchlist_add_dialog import WatchlistAddDialog
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.viewmodels.watchlist_view_model import WatchlistRow, WatchlistViewModel
from utils.sanitizers import DataSanitizer

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
    on_view: typing.Callable[[str], None] | None = None,
) -> ft.Container:
    """构建单行关注列表项 (股票名 + 代码 + 加入日期 + 备注 + 查看/移除按钮).

    UX-04: ``on_view`` 传入时在删除按钮前渲染「查看个股」按钮 (SEARCH_OUTLINED,
    描边风格对齐 DELETE_OUTLINE); None 时不渲染 (位置参数兼容).
    """
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
                *(
                    [
                        ft.IconButton(
                            icon=ft.Icons.SEARCH_OUTLINED,
                            icon_color=AppColors.TEXT_SECONDARY,
                            tooltip=I18n.get("watchlist_view_stock"),
                            on_click=lambda _e: on_view(row.ts_code),
                        )
                    ]
                    if on_view is not None
                    else []
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

    # 「添加关注」对话框 state (issue #433)
    add_dialog_open, set_add_dialog_open = ft.use_state(False)

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
            logger.error("[WatchlistView] Remove failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_remove_failed"), "error")

    # --- 添加关注 (issue #433) ---
    async def _do_add(ts_code: str, stock_name: str, note: str) -> None:
        try:
            await vm.add_to_watchlist(ts_code, stock_name, note or None)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_added"), "success")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[WatchlistView] Add failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_add_failed"), "error")

    def _on_add(ts_code: str, stock_name: str, note: str) -> None:
        """WatchlistAddDialog on_add: 关闭对话框并调度 _do_add 执行添加。"""
        set_add_dialog_open(False)
        page = _get_page()
        if page is not None:
            page.run_task(_do_add, ts_code, stock_name, note)

    def _on_add_search(keyword: str) -> None:
        """WatchlistAddDialog on_search: run_task 调度 VM.search_stocks (R16)。"""
        page = _get_page()
        if page is not None:
            page.run_task(vm.search_stocks, keyword)

    def _on_add_dialog_close() -> None:
        """WatchlistAddDialog on_close: 关闭对话框并清空搜索状态。"""
        set_add_dialog_open(False)
        page = _get_page()
        if page is not None:
            page.run_task(vm.clear_search)

    def _on_open_add_dialog() -> None:
        """点击「添加关注」按钮：打开对话框。

        state setter 隐式依赖 page 上下文；page 不可用时提前返回 (参考 _on_remove).
        """
        if _get_page() is None:
            return
        set_add_dialog_open(True)

    def _on_remove(ts_code: str) -> None:
        # 弹出 ConfirmDialog 二次确认 (防误删); 实际删除在 _do_confirm_remove
        # state setter 隐式依赖 page 上下文 (内部访问 context.page);
        # page 不可用时提前返回, 避免 RuntimeError (参考 home_view._refresh_clicked 的 page guard)
        if _get_page() is None:
            return
        set_pending_remove_ts_code(ts_code)
        set_confirm_open(True)

    def _on_view_stock(ts_code: str) -> None:
        """UX-04: 行「查看」深链 — 携带 ts_code 跳选股页并填充代码过滤.

        ts_code 为 DB 主键正常非空; 空值时降级纯 tab 导航,
        避免 "screener:" 空段消息被协议解析吞掉 (与 home_view 同构防护).
        """
        page = _get_page()
        if page is None:
            return
        message = f"screener:{ts_code}" if ts_code else "screener"
        page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, message)

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

    # --- ErrorState 回调 (Task 11.2) ---
    def _on_retry() -> None:
        """ErrorState on_retry: 重新加载关注列表 (R16: page.run_task 调度 async)."""
        page = _get_page()
        if page is not None:
            page.run_task(vm.load_watchlist)

    def _on_cta() -> None:
        """ErrorState on_cta: 打开 GitHub Issues.

        page.launch_url 为 async (被 @deprecated 装饰器破坏 iscoroutinefunction 检测,
        须用 async wrapper 包裹后通过 page.run_task 调度, R16).
        """
        page = _get_page()
        if page is not None:

            async def _open_issues() -> None:
                await page.launch_url(GITHUB_ISSUES_URL)

            page.run_task(_open_issues)

    # --- 渲染 ---
    # Task 11.2: 完全失败 (rows 空 + load_error 非空) → ErrorState 替换 body;
    # 部分失败 (rows 非空 + load_error 非空) → 保留 error_banner (不丢失已加载列表)
    has_complete_failure = state.load_error is not None and not state.watchlist_rows

    if state.is_loading:
        body = ft.Column(
            [ft.ProgressRing()],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
    elif has_complete_failure:
        body = ErrorState(
            icon=ft.Icons.ERROR_OUTLINE,
            title=I18n.get("error_state_load_failed_title"),
            message=I18n.get("error_state_load_failed_message"),
            detail=state.load_error_detail,
            on_retry=_on_retry,
            retry_text=I18n.get("common_retry"),
            on_cta=_on_cta,
            cta_text=I18n.get("error_state_contact_support"),
            cta_icon=ft.Icons.FEEDBACK,  # UX-03 (P2-09): 反馈问题语义匹配
        )
    elif not state.watchlist_rows:
        body = EmptyState(
            icon=ft.Icons.STAR_BORDER,
            title=I18n.get("watchlist_empty_title"),
            message=I18n.get("watchlist_empty_message"),
        )
    else:
        body = ft.Column(
            [_build_watchlist_row(row, _on_remove, _on_view_stock) for row in state.watchlist_rows],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=4,
        )

    # 部分失败时保留 error_banner (rows 非空 + load_error 非空)
    error_banner = None
    if state.load_error is not None and state.watchlist_rows:
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
                ft.Row(
                    [
                        ft.Text(
                            I18n.get("watchlist_title"),
                            size=AppStyles.FONT_SIZE_XL,
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        ft.Container(expand=True),
                        ft.OutlinedButton(
                            content=I18n.get("watchlist_add"),
                            icon=ft.Icons.ADD,
                            on_click=safe_on_click(lambda _e: _on_open_add_dialog()),
                            style=AppStyles.outline_button(),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
                WatchlistAddDialog(
                    open_state=add_dialog_open,
                    search_results=state.search_results,
                    is_searching=state.is_searching,
                    search_error=state.search_error,
                    on_search=_on_add_search,
                    on_add=_on_add,
                    on_close=_on_add_dialog_close,
                ),
            ],
            expand=True,
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        **AppStyles.dashboard_card(padding=AppStyles.SPACING_LG),
        expand=True,
    )
