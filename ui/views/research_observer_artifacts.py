"""研究观察面板·成果区（成果卡 / 正文按需读取 / 预览·复制·导出 / 比较入口占位）。

交付动作的真实副作用全部在服务层交付管线：本视图只负责采集目标路径、确认覆盖、
把回执三态（成功/失败/未知）如实呈现——未知态不渲染为成功（R21）。

复制走系统剪贴板：VM 只产出精确正文（或拒绝原因），视图在剪贴板写入成功后才
标记「已复制」，写入失败如实报错。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import PureWindowsPath

import flet as ft

from ui.i18n import I18n
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    ARTIFACT_EXPORT_FORMATS,
    ArtifactDeliveryReceiptView,
    ArtifactExportTargetView,
    ArtifactPreviewView,
    LoadedArtifactBody,
    ResearchObserverState,
    SessionArtifactView,
    SessionOriginView,
)
from ui.views.research_observer_widgets import hint_text, section_card, section_title

#: 服务端正文不可用原因的已知映射；未知原因按原始码展示（未知即未知，不猜语义）。
_ARTIFACT_UNAVAILABLE_KEYS = {
    "body_not_saved": "agent_research_artifact_body_not_saved",
    "body_cleaned": "agent_research_artifact_body_cleaned",
}

#: 格式展示名（格式词表是服务端冻结的技术词，不做本地化）。
_ARTIFACT_FORMAT_LABELS = {"md": "Markdown", "csv": "CSV", "xlsx": "XLSX"}


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``；未在渲染上下文（单元测试）时返回 None。"""

    try:
        return ft.context.page
    except RuntimeError:
        return None


def artifact_unavailable_text(reason: str | None) -> str:
    """成果正文不可用文案（已知原因映射 i18n；未知原因原样呈现错误码）。"""

    if reason is None:
        return I18n.get("agent_research_artifact_body_unloaded")
    key = _ARTIFACT_UNAVAILABLE_KEYS.get(reason)
    if key is not None:
        return I18n.get(key)
    return I18n.get("agent_research_artifact_unavailable_fmt").format(reason=reason)


def artifact_selection_reason(state: ResearchObserverState) -> str | None:
    """当前选择下预览/导出不可用的原因文案；可用返回 ``None``。"""

    if not state.artifacts:
        return I18n.get("agent_research_no_artifacts")
    if not state.artifact_selected_keys:
        return I18n.get("agent_research_artifact_selection_empty")
    if len(state.artifact_selected_keys) != 1:
        return I18n.get("agent_research_artifact_select_one")
    if not state.artifact_delivery_available:
        return I18n.get("agent_research_artifact_delivery_unavailable")
    return None


def artifact_overwrite_summary(target: ArtifactExportTargetView) -> str:
    """覆盖确认摘要：文件名 + 大小 + 摘要前缀（确认绑定该次目标事实）。"""

    file_name = PureWindowsPath(target.target_path).name or target.target_path
    size_text = str(target.size) if target.size is not None else I18n.get("agent_research_artifact_export_size_unknown")
    return I18n.get("agent_research_artifact_export_overwrite_fmt").format(
        name=file_name,
        size=size_text,
        digest=target.digest[:12],
    )


def artifact_receipt_lines(receipt: ArtifactDeliveryReceiptView) -> list[str]:
    """交付回执三态文案（``unknown`` 明示「未知，勿当成功」，并给出下一步）。"""

    if receipt.phase == "published_confirmed":
        lines = [I18n.get("agent_research_artifact_export_success")]
    elif receipt.phase == "unknown":
        lines = [I18n.get("agent_research_artifact_export_unknown")]
    elif receipt.phase == "cancelled_before_publish":
        lines = [I18n.get("agent_research_artifact_export_cancelled")]
    else:
        lines = [I18n.get("agent_research_artifact_export_failed_fmt").format(code=receipt.error_code or receipt.phase)]
    lines.append(I18n.get("agent_research_artifact_export_phase_fmt").format(phase=receipt.phase))
    if receipt.error_code:
        lines.append(I18n.get("agent_research_artifact_export_error_code_fmt").format(code=receipt.error_code))
    if receipt.next_action:
        lines.append(I18n.get("agent_research_artifact_export_next_action_fmt").format(action=receipt.next_action))
    if not receipt.staged_cleaned:
        lines.append(I18n.get("agent_research_artifact_export_staged_dirty"))
    return lines


def artifact_preview_meta(preview: ArtifactPreviewView) -> str:
    """预览元信息：格式 / 字段数 / 字节数 / 摘要前缀（非成功承诺，仅事实）。"""

    return I18n.get("agent_research_artifact_preview_meta_fmt").format(
        format=_ARTIFACT_FORMAT_LABELS.get(preview.format, preview.format),
        field_count=preview.field_count,
        byte_count=preview.byte_count,
        digest=preview.digest[:12],
    )


#: 对象类别 → 原业务页（只定位，不重跑筛选/回测、不预填参数）。
_ORIGIN_REPORT_TAB_BY_KIND = {
    "backtest_result": "backtest",
    "screener_candidate": "screener",
    "news_item": "market",
}

#: 可按 ts_code 打开当前个股详情的对象类别（个股 / 选股候选）。
_STOCK_ORIGIN_KINDS = frozenset({"stock", "screener_candidate"})


def origin_report_navigate(origin: SessionOriginView | None) -> str | None:
    """研究对象的原业务页深链段；无来源/未知类别返回 None（不猜目标页）。"""

    if origin is None or origin.object_kind is None:
        return None
    return _ORIGIN_REPORT_TAB_BY_KIND.get(origin.object_kind)


def origin_stock_code(origin: SessionOriginView | None) -> str | None:
    """研究对象对应的个股代码（``object_id`` 归一为大写）；类别不符/缺失返回 None。"""

    if origin is None or origin.object_kind not in _STOCK_ORIGIN_KINDS:
        return None
    code = (origin.object_id or "").strip()
    return code.upper() if code else None


def artifact_brief_lines(origin: SessionOriginView | None, artifact: SessionArtifactView) -> list[str]:
    """复制摘要/条件文本：准确对象 + 来源 + 版本 + 时点 + 缺口。

    任何缺失事实都渲染为「未知」文案（R21：不留空、不以默认值冒充已知）；
    不包含用户身份/路径等无关信息，也不承诺分享或写库。
    """

    unknown = I18n.get("agent_research_artifact_brief_unknown")
    display_name = origin.display_name if origin is not None else None
    kind = origin.object_kind if origin is not None else None
    object_id = origin.object_id if origin is not None else None
    if kind and object_id:
        kind_text = f"{kind}:{object_id}"
    else:
        kind_text = kind or object_id or unknown
    source = (origin.source_module if origin is not None else None) or unknown
    version = origin.object_version[:12] if origin is not None and origin.object_version else unknown
    available_at = (origin.available_at if origin is not None else None) or unknown
    period = (origin.period if origin is not None else None) or unknown
    published_at = artifact.published_at or unknown
    gaps: list[str] = []
    if not artifact.summary:
        gaps.append(I18n.get("agent_research_artifact_brief_gap_missing_summary"))
    if not artifact.body_available:
        gaps.append(I18n.get("agent_research_artifact_brief_gap_body_unavailable"))
    gap = " / ".join(gaps) if gaps else unknown
    return [
        I18n.get("agent_research_artifact_brief_object").format(object=display_name or unknown),
        I18n.get("agent_research_artifact_brief_kind").format(kind=kind_text),
        I18n.get("agent_research_artifact_brief_source").format(source=source),
        I18n.get("agent_research_artifact_brief_version").format(version=version),
        I18n.get("agent_research_artifact_brief_available_at").format(time=available_at),
        I18n.get("agent_research_artifact_brief_period").format(period=period),
        I18n.get("agent_research_artifact_brief_published_at").format(time=published_at),
        I18n.get("agent_research_artifact_brief_gap").format(gap=gap),
    ]


async def _write_clipboard_text(text: str) -> bool | None:
    """把文本写入系统剪贴板：无渲染上下文返回 None，成功 True，失败 False。

    CancelledError 必须传播（R2）；剪贴板 service 无论成败都从页面移除。
    """

    page = _get_page()
    if page is None:
        return None
    clipboard = ft.Clipboard()
    page.services.append(clipboard)
    try:
        await clipboard.set(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    finally:
        try:
            page.services.remove(clipboard)
        except ValueError:
            pass
    return True


async def _copy_brief_and_toast(text: str) -> None:
    """写入摘要文本并按结果给成功/失败 toast（不写库、不自动分享）。"""

    result = await _write_clipboard_text(text)
    if result is None:
        return
    page = _get_page()
    if page is None:
        return
    if result:
        page.toast.show(I18n.get("agent_research_artifact_brief_copied"), "success")  # type: ignore[attr-defined]  # [reason: page.toast 由 application.py 动态挂载, ft.Page 存根未声明]
    else:
        page.toast.show(I18n.get("agent_research_artifact_brief_copy_failed"), "error")  # type: ignore[attr-defined]  # [reason: page.toast 由 application.py 动态挂载, ft.Page 存根未声明]


def _queue_brief_copy(text: str) -> None:
    """事件处理器内只调度任务（R16：不在事件循环线程做同步阻塞操作）。"""

    page = _get_page()
    if page is not None:
        page.run_task(_copy_brief_and_toast, text)


def _publish_navigation(message: str) -> None:
    """发送业务页定位消息（仅定位：不重跑、不预填、不自动保存）。"""

    page = _get_page()
    if page is not None:
        page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, message)


def _brief_action_button(
    label_key: str, icon: str, tooltip_key: str, on_click: Callable[[ft.ControlEvent], None]
) -> ft.TextButton:
    """成果卡动作按钮（颜色/字号统一走 AppStyles/AppColors，R23）。"""

    return ft.TextButton(
        content=ft.Text(I18n.get(label_key), size=AppStyles.FONT_SIZE_BODY_SM),
        icon=icon,
        tooltip=I18n.get(tooltip_key),
        style=ft.ButtonStyle(color=AppColors.PRIMARY),
        on_click=on_click,
    )


def build_artifact_origin_actions(
    origin: SessionOriginView | None,
    artifact: SessionArtifactView,
) -> ft.Row:
    """成果卡动作行：复制摘要/条件 / 回原报告 / 查看当前个股 / 打开关注列表 / 打开选股方案。

    全部为只读定位或复制：不重跑筛选/回测、不预填参数、不自动关注/保存方案，
    也不显示未发生的成功提示。
    """

    buttons: list[ft.TextButton] = [
        _brief_action_button(
            "agent_research_artifact_brief_copy",
            ft.Icons.CONTENT_COPY,
            "agent_research_artifact_brief_copy_hint",
            lambda _e: _queue_brief_copy("\n".join(artifact_brief_lines(origin, artifact))),
        )
    ]
    report_tab = origin_report_navigate(origin)
    if report_tab is not None:
        buttons.append(
            _brief_action_button(
                "agent_research_artifact_open_report",
                ft.Icons.OPEN_IN_NEW,
                "agent_research_artifact_locate_only_hint",
                lambda _e, tab=report_tab: _publish_navigation(tab),
            )
        )
    stock_code = origin_stock_code(origin)
    if stock_code is not None:
        buttons.append(
            _brief_action_button(
                "agent_research_artifact_view_current_stock",
                ft.Icons.SEARCH,
                "agent_research_artifact_view_current_stock_note",
                lambda _e, code=stock_code: _publish_navigation(f"watchlist:{code}"),
            )
        )
    buttons.append(
        _brief_action_button(
            "agent_research_artifact_open_watchlist",
            ft.Icons.STAR_OUTLINE,
            "agent_research_artifact_locate_only_hint",
            lambda _e: _publish_navigation("watchlist"),
        )
    )
    buttons.append(
        _brief_action_button(
            "agent_research_artifact_open_plan",
            ft.Icons.FILTER_ALT_OUTLINED,
            "agent_research_artifact_locate_only_hint",
            lambda _e: _publish_navigation("screener"),
        )
    )
    return ft.Row(buttons, spacing=AppStyles.SPACING_XS, wrap=True)


def build_artifact_card(
    artifact: SessionArtifactView,
    *,
    loaded: LoadedArtifactBody | None,
    loading: bool,
    on_load_artifact: Callable[[str, int, str, str], None],
    selected: bool = False,
    on_toggle_selection: Callable[[str], None] | None = None,
    origin: SessionOriginView | None = None,
) -> ft.Control:
    """成果卡：摘要常驻；正文按需只读读取，未载入/不可用如实标注。

    ``origin`` 为会话级对象来源（同一会话所有成果共用）；为 None 时动作行只保留
    「打开关注列表/打开选股方案」，复制文案只含成果自身字段（不编造对象）。
    """

    selection_key = f"{artifact.artifact_id}:{artifact.artifact_version}"
    header_controls: list[ft.Control] = []
    if on_toggle_selection is not None:
        header_controls.append(
            ft.Checkbox(
                value=selected,
                label=f"{artifact.artifact_kind} · v{artifact.artifact_version}",
                label_style=ft.TextStyle(
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                on_change=lambda _e, key=selection_key: on_toggle_selection(key),
                key=f"agent_research_artifact_select_{selection_key}",
            )
        )
    else:
        header_controls.append(
            ft.Text(
                f"{artifact.artifact_kind} · v{artifact.artifact_version}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    header_controls.append(
        ft.Text(
            artifact.execution_id,
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        )
    )
    header = ft.Row(header_controls, spacing=AppStyles.SPACING_SM)
    rows: list[ft.Control] = [header]
    if artifact.summary:
        rows.append(
            ft.Text(
                artifact.summary,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    if loaded is not None:
        if loaded.body_available:
            if loaded.body_text:
                rows.append(
                    ft.Text(
                        loaded.body_text,
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_PRIMARY,
                    )
                )
            if loaded.truncated:
                rows.append(
                    ft.Text(
                        I18n.get("agent_research_artifact_truncated"),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_HINT,
                    )
                )
        else:
            rows.append(
                ft.Text(
                    artifact_unavailable_text(loaded.unavailable_reason),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
    elif artifact.body_available:
        # 首屏已携正文：直接展示，不再给「读取正文」入口（避免把已有正文标成未载入）
        rows.append(
            ft.Text(
                artifact.body_text or "",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    else:
        rows.append(
            ft.Text(
                I18n.get("agent_research_artifact_body_unloaded"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
        if loading:
            rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
        else:
            rows.append(
                ft.IconButton(
                    icon=ft.Icons.ARTICLE_OUTLINED,
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("agent_research_artifact_load"),
                    on_click=lambda _e, item=artifact: on_load_artifact(
                        item.artifact_id,
                        item.artifact_version,
                        item.execution_id,
                        item.round_id,
                    ),
                )
            )
    rows.append(build_artifact_origin_actions(origin, artifact))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def build_artifact_delivery_actions(
    state: ResearchObserverState,
    *,
    on_format_change: Callable[[str], None],
    on_target_path_change: Callable[[str], None],
    on_preview: Callable[[], Awaitable[None]],
    on_copy: Callable[[], Awaitable[None]],
    on_export: Callable[[], Awaitable[None]],
) -> ft.Control:
    """成果动作区：格式 / 目标路径 / 预览 / 复制 / 导出（空选择禁用并给原因）。"""

    reason = artifact_selection_reason(state)
    single_ready = reason is None and not state.artifact_preview_pending
    selected_count = len(state.artifact_selected_keys)
    action_rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Dropdown(
                    label=I18n.get("agent_research_artifact_format_label"),
                    value=state.artifact_export_format,
                    width=130,
                    options=[
                        ft.DropdownOption(
                            key=name,
                            text=_ARTIFACT_FORMAT_LABELS.get(name, name),
                        )
                        for name in ARTIFACT_EXPORT_FORMATS
                    ],
                    on_select=lambda e: on_format_change(str(e.control.value or "")),
                ),
                ft.Text(
                    I18n.get("agent_research_artifact_selected_count_fmt").format(count=selected_count),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        ),
        ft.TextField(
            label=I18n.get("agent_research_artifact_target_label"),
            value=state.artifact_export_target_path,
            hint_text=I18n.get("agent_research_artifact_target_hint"),
            dense=True,
            on_change=lambda e: on_target_path_change(str(e.control.value or "")),
        ),
        ft.Row(
            [
                ft.FilledButton(
                    content=ft.Text(I18n.get("agent_research_artifact_preview"), size=AppStyles.FONT_SIZE_BODY_SM),
                    icon=ft.Icons.VISIBILITY,
                    disabled=not single_ready,
                    tooltip=reason,
                    on_click=on_preview,
                ),
                ft.OutlinedButton(
                    content=ft.Text(I18n.get("agent_research_artifact_copy"), size=AppStyles.FONT_SIZE_BODY_SM),
                    icon=ft.Icons.CONTENT_COPY,
                    disabled=not state.artifact_selected_keys,
                    tooltip=I18n.get("agent_research_artifact_selection_empty")
                    if not state.artifact_selected_keys
                    else None,
                    on_click=on_copy,
                ),
                ft.OutlinedButton(
                    content=ft.Text(I18n.get("agent_research_artifact_export"), size=AppStyles.FONT_SIZE_BODY_SM),
                    icon=ft.Icons.SAVE_ALT,
                    disabled=not single_ready or state.artifact_export_pending,
                    tooltip=reason,
                    on_click=on_export,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        ),
    ]
    if state.artifact_copy_error_key:
        action_rows.append(hint_text(state.artifact_copy_error_key))
    if state.artifact_copy_notice_key:
        action_rows.append(
            ft.Text(
                I18n.get(state.artifact_copy_notice_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if not state.artifact_delivery_available:
        action_rows.append(hint_text("agent_research_artifact_delivery_unavailable"))
    return section_card(action_rows)


def build_artifact_preview_panel(state: ResearchObserverState) -> ft.Control | None:
    """预览面板：等待 / 错误（含错误码）/ 预览正文与口径说明（XLSX 为字段表）。"""

    if state.artifact_preview_pending:
        return section_card(
            [
                section_title("agent_research_artifact_preview"),
                ft.ProgressRing(width=18, height=18, stroke_width=2),
            ]
        )
    if state.artifact_preview_error_key:
        error_rows: list[ft.Control] = [
            section_title("agent_research_artifact_preview"),
            hint_text(state.artifact_preview_error_key),
        ]
        if state.artifact_preview_error_code:
            error_rows.append(
                ft.Text(
                    I18n.get("agent_research_artifact_preview_failed_fmt").format(
                        code=state.artifact_preview_error_code
                    ),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        return section_card(error_rows)
    preview = state.artifact_preview
    if preview is None:
        return None
    rows: list[ft.Control] = [
        section_title("agent_research_artifact_preview"),
        ft.Text(
            artifact_preview_meta(preview),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        ),
    ]
    if preview.note_key:
        rows.append(hint_text(preview.note_key))
    rows.append(
        ft.TextField(
            value=preview.text,
            read_only=True,
            multiline=True,
            min_lines=4,
            max_lines=12,
            text_size=AppStyles.FONT_SIZE_CAPTION,
            tooltip=I18n.get("agent_research_artifact_preview"),
        )
    )
    return section_card(rows)


def build_artifact_export_panels(state: ResearchObserverState) -> list[ft.Control]:
    """导出确认 / 回执 / 错误面板（覆盖确认绑定目标事实；未知态不按成功呈现）。"""

    rows: list[ft.Control] = []
    confirmation = state.artifact_export_confirmation
    if confirmation is not None:
        rows.append(
            section_card(
                [
                    section_title("agent_research_artifact_export"),
                    ft.Text(
                        artifact_overwrite_summary(confirmation),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Text(
                        confirmation.target_path,
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_HINT,
                    ),
                ]
            )
        )
    if state.artifact_export_notice_key:
        rows.append(
            ft.Text(
                I18n.get(state.artifact_export_notice_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.artifact_export_receipt is not None:
        receipt = state.artifact_export_receipt
        receipt_rows: list[ft.Control] = [section_title("agent_research_artifact_export")]
        receipt_rows.extend(
            ft.Text(
                line,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_PRIMARY if receipt.phase == "unknown" else AppColors.TEXT_HINT,
            )
            for line in artifact_receipt_lines(receipt)
        )
        rows.append(section_card(receipt_rows))
    if state.artifact_export_error_key:
        rows.append(hint_text(state.artifact_export_error_key))
    if state.artifact_export_error_code:
        rows.append(
            ft.Text(
                I18n.get("agent_research_artifact_export_error_code_fmt").format(code=state.artifact_export_error_code),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return rows


def build_artifacts_section(
    state: ResearchObserverState,
    *,
    on_load_artifact: Callable[[str, int, str, str], None],
    on_toggle_artifact_selection: Callable[[str], None] | None = None,
    on_select_all_artifacts: Callable[[], None] | None = None,
    on_clear_artifact_selection: Callable[[], None] | None = None,
    on_artifact_format_change: Callable[[str], None] | None = None,
    on_artifact_target_path_change: Callable[[str], None] | None = None,
    on_preview_artifact: Callable[[], Awaitable[None]] | None = None,
    on_copy_artifacts: Callable[[], str | None] | None = None,
    on_artifact_copy_done: Callable[[], None] | None = None,
    on_artifact_copy_failed: Callable[[], None] | None = None,
    on_request_export: Callable[[], Awaitable[None]] | None = None,
    on_confirm_export: Callable[[], Awaitable[None]] | None = None,
    on_cancel_export: Callable[[], None] | None = None,
) -> list[ft.Control]:
    """成果区（摘要常驻；正文按需只读读取）+ 预览/复制/导出接线 + 比较入口占位。"""

    rows: list[ft.Control] = [section_title("agent_research_artifacts")]
    if not state.artifacts:
        rows.append(hint_text("agent_research_no_artifacts"))
    for artifact in state.artifacts:
        key = f"{artifact.artifact_id}:{artifact.artifact_version}"
        rows.append(
            build_artifact_card(
                artifact,
                loaded=state.artifact_body_for(artifact.artifact_id, artifact.artifact_version),
                loading=key in state.artifact_body_pending_keys,
                on_load_artifact=on_load_artifact,
                selected=key in state.artifact_selected_keys,
                on_toggle_selection=on_toggle_artifact_selection,
                origin=state.session_origin,
            )
        )
    if state.artifacts_has_more:
        rows.append(hint_text("agent_research_more_items"))
    if state.artifacts and on_toggle_artifact_selection is not None:
        selection_buttons: list[ft.Control] = []
        if on_select_all_artifacts is not None:
            selection_buttons.append(
                ft.OutlinedButton(
                    content=ft.Text(
                        I18n.get("agent_research_artifact_select_all"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                    ),
                    icon=ft.Icons.SELECT_ALL,
                    on_click=lambda _e: on_select_all_artifacts(),
                )
            )
        if on_clear_artifact_selection is not None:
            selection_buttons.append(
                ft.OutlinedButton(
                    content=ft.Text(
                        I18n.get("agent_research_artifact_clear_selection"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                    ),
                    icon=ft.Icons.DESELECT,
                    disabled=not state.artifact_selected_keys,
                    on_click=lambda _e: on_clear_artifact_selection(),
                )
            )
        if selection_buttons:
            rows.append(ft.Row(selection_buttons, spacing=AppStyles.SPACING_SM))

    async def _copy_to_clipboard(_e: ft.ControlEvent | None = None) -> None:
        if on_copy_artifacts is None:
            return
        text = on_copy_artifacts()
        if text is None:
            return  # VM 已给出拒绝原因（空选择/未载入/已清理/分批未读完）
        page = _get_page()
        if page is None:
            # 无渲染上下文（单元测试）不冒充「已复制」：只保留 VM 的正文就绪事实。
            return
        clipboard = ft.Clipboard()
        page.services.append(clipboard)
        try:
            await clipboard.set(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            if on_artifact_copy_failed is not None:
                on_artifact_copy_failed()
            return
        finally:
            try:
                page.services.remove(clipboard)
            except ValueError:
                pass
        if on_artifact_copy_done is not None:
            on_artifact_copy_done()

    async def _preview(_e: ft.ControlEvent | None = None) -> None:
        if on_preview_artifact is not None:
            await on_preview_artifact()

    async def _export(_e: ft.ControlEvent | None = None) -> None:
        if on_request_export is not None:
            await on_request_export()

    async def _confirm(_e: ft.ControlEvent | None = None) -> None:
        if on_confirm_export is not None:
            await on_confirm_export()

    if (
        on_artifact_format_change is not None
        and on_artifact_target_path_change is not None
        and on_preview_artifact is not None
        and on_request_export is not None
    ):
        rows.append(
            build_artifact_delivery_actions(
                state,
                on_format_change=on_artifact_format_change,
                on_target_path_change=on_artifact_target_path_change,
                on_preview=_preview,
                on_copy=_copy_to_clipboard,
                on_export=_export,
            )
        )
        preview_panel = build_artifact_preview_panel(state)
        if preview_panel is not None:
            rows.append(preview_panel)
        rows.extend(build_artifact_export_panels(state))
        if state.artifact_export_confirmation is not None and on_confirm_export is not None:
            rows.append(
                ft.Row(
                    [
                        ft.FilledButton(
                            content=ft.Text(
                                I18n.get("agent_research_artifact_export_overwrite_confirm"),
                                size=AppStyles.FONT_SIZE_BODY_SM,
                            ),
                            icon=ft.Icons.SAVE_ALT,
                            disabled=state.artifact_export_pending,
                            on_click=_confirm,
                        ),
                        ft.OutlinedButton(
                            content=ft.Text(
                                I18n.get("agent_research_artifact_export_overwrite_cancel"),
                                size=AppStyles.FONT_SIZE_BODY_SM,
                            ),
                            disabled=state.artifact_export_pending,
                            on_click=lambda _e: on_cancel_export() if on_cancel_export is not None else None,
                        ),
                    ],
                    spacing=AppStyles.SPACING_SM,
                )
            )
    if state.artifacts:
        rows.append(build_comparison_entry())
    return rows


def build_comparison_entry() -> ft.Control:
    """比较入口占位：入口可见但禁用，并明示由后续版本提供（不伪造比较结果）。"""

    return section_card(
        [
            section_title("agent_research_comparison"),
            hint_text("agent_research_comparison_placeholder"),
            ft.Row(
                [
                    ft.OutlinedButton(
                        content=ft.Text(
                            I18n.get("agent_research_comparison_open"),
                            size=AppStyles.FONT_SIZE_BODY_SM,
                        ),
                        icon=ft.Icons.COMPARE_ARROWS,
                        disabled=True,
                        tooltip=I18n.get("agent_research_comparison_pending"),
                    )
                ]
            ),
        ]
    )
