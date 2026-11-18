"""watchlist_add_dialog — 关注列表「添加关注」声明式对话框组件 (issue #433).

提供搜索框 + 搜索结果列表 + 备注输入 + 确认/取消 的添加关注对话框：
- 受控组件：搜索数据状态 (``search_results``/``is_searching``/``search_error``)
  由父级 (WatchlistView) 从 VM state 传入；搜索触发经 ``on_search`` 回调
  (父级 ``page.run_task`` 调度 VM 命令, R16)
- 本地 UI 态：``keyword`` / ``note`` / 选中结果由 ``use_state`` 管理
- 必须从搜索结果中选中一只股票后才能确认添加 (避免无效 ts_code 入库)
- R2-I13：重复关注或修改备注前，先经 ``on_load_existing_note`` 按股票身份读取
  原备注（结果由父级从 VM state 经 ``existing_note`` 传入）；原备注非空且与新备注
  不同时，先展示「原备注 / 新备注 / 将替换」并要求二次确认，取消则不写入。
  读取中/读取失败/尚未读取均不得直接写入（避免静默覆盖原备注）。
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- ``@ft.component`` 函数组件，无 class 子类
- ``ft.use_dialog`` hook 自动挂载/卸载到 page overlay
- ``open`` prop 由消费方驱动，state 切换自动清理
"""

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
    safe_on_dismiss,
    safe_controls,
)
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.watchlist_view_model import ExistingNoteState, StockSearchRow

# 旧备注默认状态（frozen dataclass，可安全共享）；避免在函数默认值内调用构造器 (B008)。
_EMPTY_NOTE_STATE = ExistingNoteState()

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
    is_submitting: bool = False,
    existing_note: ExistingNoteState = _EMPTY_NOTE_STATE,
    on_search: Callable[[str], None] | None = None,
    on_load_existing_note: Callable[[str], None] | None = None,
    on_add: Callable[[str, str, str], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ft.Container:
    """「添加关注」对话框组件 (issue #433).

    Args:
        open_state: 是否打开 (由消费方驱动)。
        search_results: VM 搜索返回的股票候选列表。
        is_searching: 搜索请求进行中。
        search_error: 搜索失败信息 (i18n Message)。
        is_submitting: 添加写入进行中 (F01: 禁用重复确认/输入变更/手工关闭)。
        existing_note: 当前选中股票的旧备注读取状态 (R2-I13，来自 VM state；
            ``ts_code`` 不匹配当前选中时视为未读取，重新读取而非猜测)。
        on_search: 触发搜索回调 (keyword)。
        on_load_existing_note: 按 ts_code 读取旧备注回调 (父级调度 VM 命令, R16)。
        on_add: 确认添加回调 (ts_code, stock_name, note)。
        on_close: 取消/关闭回调。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    keyword, set_keyword = ft.use_state("")
    note, set_note = ft.use_state("")
    selected_ts_code, set_selected_ts_code = ft.use_state("")
    selected_name, set_selected_name = ft.use_state("")
    # R2-I13: 是否已进入「替换原备注」二次确认态（本地 UI 态，非业务状态）。
    confirm_replace, set_confirm_replace = ft.use_state(False)
    # R2-I13: 同帧重复点击守卫——use_state 的值在本帧内是旧快照，必须用 ref 同步加锁。
    submit_lock = ft.use_ref(False)

    def _reset_effect() -> None:
        """每次打开时重置本地输入状态 (避免上次残留)."""
        if open_state:
            set_keyword("")
            set_note("")
            set_selected_ts_code("")
            set_selected_name("")
            set_confirm_replace(False)
            submit_lock.current = False

    ft.use_effect(_reset_effect, dependencies=[open_state])

    def _release_submit_lock() -> None:
        """写入结束（成功关闭 / 失败保留草稿）后释放同帧提交锁，允许失败后重试。"""
        if not is_submitting:
            submit_lock.current = False

    ft.use_effect(_release_submit_lock, dependencies=[is_submitting])

    # 仅接受属于当前选中股票的旧备注结果（切换选中后旧结果不得复用）。
    note_info = existing_note if selected_ts_code and existing_note.ts_code == selected_ts_code else None
    old_note = (note_info.note or "").strip() if note_info is not None and note_info.status == "loaded" else ""
    new_note = note.strip()
    # 只有「原备注非空且与新备注不同」才存在内容损失，才需要二次确认。
    shows_replace = confirm_replace and bool(old_note) and old_note != new_note

    def _on_keyword_change(e: ft.ControlEvent) -> None:
        """输入变化时更新 keyword 并清空选中 (旧选中可能不匹配新关键词)."""
        set_keyword(get_control_value(e.control, ft.TextField))
        set_selected_ts_code("")
        set_selected_name("")
        set_confirm_replace(False)

    def _on_note_change(e: ft.ControlEvent) -> None:
        """备注变化时退出替换确认态（确认内容已随之变化，需重新评估）。"""
        set_note(get_control_value(e.control, ft.TextField))
        if confirm_replace:
            set_confirm_replace(False)

    def _on_search(_e: ft.ControlEvent | None = None) -> None:
        """触发搜索 (TextField on_submit / 搜索按钮共用); keyword 为空时不发起."""
        if on_search is not None and keyword.strip():
            on_search(keyword)

    def _on_select(row: StockSearchRow) -> None:
        set_selected_ts_code(row.ts_code)
        set_selected_name(row.name)
        set_confirm_replace(False)
        # R2-I13: 选中即按股票身份读取原备注（写入前必须已知旧内容）。
        if on_load_existing_note is not None:
            on_load_existing_note(row.ts_code)

    def _submit() -> None:
        """真正提交写入（同帧重复点击只提交一次）。"""
        if on_add is None or submit_lock.current:
            return
        submit_lock.current = True
        on_add(selected_ts_code, selected_name, note.strip())

    def _on_confirm(_e: ft.ControlEvent) -> None:
        """确认添加：先确保已读取旧备注，必要时进入「替换原备注」二次确认。

        读取中 / 读取失败 / 尚未读取一律不写入（读取失败重试读取，绝不当作
        「无旧备注」直接覆盖）；写入由 ``_submit`` 的同帧锁 + ``is_submitting`` 双保险。
        """
        if is_submitting or not selected_ts_code or on_add is None:
            return
        if shows_replace:
            return  # 已在「替换原备注」确认面板，须经面板「仍要替换」显式确认
        if note_info is None or note_info.status in ("idle", "failed"):
            if on_load_existing_note is not None:
                on_load_existing_note(selected_ts_code)
            return
        if note_info.status == "loading":
            return
        if old_note and old_note != new_note:
            set_confirm_replace(True)
            return
        _submit()

    def _on_back_to_edit(_e: ft.ControlEvent) -> None:
        """返回修改：退出替换确认态，不写入（原备注保持不变）。"""
        if is_submitting:
            return
        set_confirm_replace(False)

    def _on_replace_confirm(_e: ft.ControlEvent) -> None:
        """「仍要替换」：用户已看到原备注与替换影响，执行写入。"""
        if is_submitting or not selected_ts_code:
            return
        _submit()

    def _on_cancel(_e: ft.ControlEvent) -> None:
        # F01: 写入执行中禁止手工关闭（避免已提交但未知结果被当成未写入）。
        if is_submitting:
            return
        if on_close is not None:
            on_close()

    search_field = ft.TextField(
        value=keyword,
        # F08: 持久 label 提供读屏语义名称（hint 仅作输入示例，不冒充字段名称，
        # 见 docs/flet/accessibility-baseline.md §2.1）。
        label=I18n.get("watchlist_add_search_label"),
        hint_text=I18n.get("watchlist_add_search_hint"),
        dense=True,
        expand=True,
        disabled=is_submitting,
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
        disabled=is_submitting,
        on_change=safe_on_change(_on_note_change),
    )

    # --- R2-I13：旧备注读取状态提示（读取中 / 读取失败 / 原备注与替换影响）---
    note_hints: list[ft.Control] = []
    if note_info is not None:
        if note_info.status == "loading":
            note_hints.append(
                ft.Text(
                    I18n.get("watchlist_note_checking"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                )
            )
        elif note_info.status == "failed":
            note_hints.append(
                ft.Text(
                    I18n.get("watchlist_note_check_failed"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.ERROR,
                )
            )
        elif old_note:
            note_hints.append(
                ft.Text(
                    I18n.get("watchlist_note_existing", note=old_note),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.WARNING,
                )
            )

    # --- R2-I13：替换原备注二次确认（展示旧内容与影响，允许取消）---
    replace_panel: ft.Control | None = None
    if shows_replace:
        new_note_text = (
            I18n.get("watchlist_note_replace_new", note=new_note)
            if new_note
            else I18n.get("watchlist_note_replace_new_cleared")
        )
        replace_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        I18n.get("watchlist_note_replace_title"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        weight=ft.FontWeight.W_500,
                        color=AppColors.WARNING,
                    ),
                    ft.Text(
                        I18n.get("watchlist_note_replace_old", note=old_note),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Text(
                        new_note_text,
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Row(
                        [
                            ft.TextButton(
                                content=I18n.get("watchlist_note_replace_cancel"),
                                disabled=is_submitting,
                                on_click=safe_on_click(_on_back_to_edit),
                                style=ft.ButtonStyle(color=AppColors.PRIMARY),
                            ),
                            ft.Button(
                                content=I18n.get("watchlist_note_replace_confirm"),
                                disabled=is_submitting,
                                on_click=safe_on_click(_on_replace_confirm),
                                style=AppStyles.danger_button(),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=8,
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding.all(8),
            bgcolor=AppColors.SURFACE_VARIANT,
            border_radius=8,
        )

    content_controls: list[ft.Control] = [search_field, result_area, note_field, *note_hints]
    if replace_panel is not None:
        content_controls.append(replace_panel)
    content = ft.Column(
        content_controls,
        spacing=8,
        width=380,
        tight=True,
    )

    cancel_btn = ft.TextButton(
        content=I18n.get("common_cancel"),
        disabled=is_submitting,
        on_click=safe_on_click(_on_cancel),
        style=ft.ButtonStyle(color=AppColors.PRIMARY),
    )
    confirm_btn = ft.Button(
        content=I18n.get("watchlist_add"),
        # R2-I13: 旧备注读取中禁用确认（读取失败仍可点击以重试读取）。
        disabled=(not selected_ts_code or is_submitting or (note_info is not None and note_info.status == "loading")),
        on_click=safe_on_click(_on_confirm),
        style=AppStyles.primary_button(),
    )

    dialog = (
        ft.AlertDialog(
            modal=True,
            # 内容（搜索结果 + 备注 + 替换确认面板）在窄窗口下超出视口时滚动，
            # 避免按钮区被挤出（同 ConfirmDialog 的 MINOR-05 处理）。
            scrollable=True,
            # 用户经 Esc / 关闭路径 dismiss 时按「取消」处理: 同步消费方 open_state
            # (提交中沿 _on_cancel 的 F01 守卫保持不关闭)。
            on_dismiss=safe_on_dismiss(_on_cancel),
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
