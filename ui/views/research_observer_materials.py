"""研究观察面板·研究资料区（添加资料 / 已读未读 / 限额 / 三类时点 / 并入或转移）。

本模块只渲染 VM 给出的不可变快照并转发显式回调：不 import VM 反向依赖、不拼装
服务请求、不做文件 IO（文件选择由壳层回调完成）。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    MATERIAL_MAX_BYTES,
    MATERIAL_MAX_ITEMS,
    RejectedMaterialView,
    ResearchMaterialView,
    ResearchObserverState,
)
from ui.views.research_observer_widgets import hint_text, section_card, section_title


def _ignore_click() -> None:
    """缺省回调（未传入添加/转移回调时不绑定动作）。"""


def _ignore_select(_value: str | None) -> None:
    """缺省回调（未传入目标会话回调时不绑定动作）。"""


def _ignore_remove(_key: str) -> None:
    """缺省回调（未传入移除回调时不绑定动作）。"""


def _date_text(label_key: str, value: str | None) -> str:
    """时点文案：缺失一律渲染「未知」（不用当前时间填充，R21）。"""

    rendered = value if value else I18n.get("agent_research_materials_unknown")
    return I18n.get(label_key).format(value=rendered)


def _format_bytes(value: int | None) -> str:
    if value is None:
        return I18n.get("agent_research_materials_unknown")
    if value >= 1_000_000:
        return I18n.get("agent_research_materials_bytes_mb_fmt").format(value=f"{value / 1_000_000:.2f}")
    if value >= 1_000:
        return I18n.get("agent_research_materials_bytes_kb_fmt").format(value=f"{value / 1_000:.1f}")
    return I18n.get("agent_research_materials_bytes_b_fmt").format(value=value)


def _material_row(material: ResearchMaterialView, *, on_remove: Callable[[str], None]) -> ft.Control:
    read_text = I18n.get(material.read_key)
    if material.reason_key:
        read_text = I18n.get("agent_research_materials_read_with_reason_fmt").format(
            state=read_text, reason=I18n.get(material.reason_key)
        )
    counts = I18n.get("agent_research_materials_item_counts_fmt").format(
        codepoints=material.codepoints
        if material.codepoints is not None
        else I18n.get("agent_research_materials_unknown"),
        bytes=_format_bytes(material.byte_size),
    )
    row = ft.Row(
        spacing=8,
        controls=[
            ft.Text(
                material.display_name,
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.W_500,
                expand=True,
            ),
            ft.Text(I18n.get(material.kind_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
            ft.Text(
                read_text,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.SUCCESS if material.is_read else AppColors.WARNING,
            ),
            ft.TextButton(
                I18n.get("agent_research_materials_remove"),
                on_click=lambda _e, key=material.key: on_remove(key),
            ),
        ],
    )
    return ft.Container(
        padding=8,
        content=ft.Column(
            spacing=2,
            controls=[
                row,
                ft.Text(counts, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    _date_text("agent_research_materials_captured_fmt", material.captured_at),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Text(
                    _date_text("agent_research_materials_content_at_fmt", material.content_at),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
        ),
    )


def _rejected_row(rejected: RejectedMaterialView) -> ft.Text:
    return ft.Text(
        I18n.get("agent_research_materials_rejected_fmt").format(
            name=rejected.display_name,
            reason=I18n.get(rejected.reason_key),
        ),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.ERROR,
    )


def build_materials_section(
    state: ResearchObserverState,
    *,
    on_add_click: Callable[[], None] = _ignore_click,
    on_remove: Callable[[str], None] = _ignore_remove,
    on_select_target: Callable[[str | None], None] = _ignore_select,
    on_add_to_session: Callable[[], None] = _ignore_click,
    on_transfer: Callable[[], None] = _ignore_click,
) -> ft.Control:
    """渲染资料区：逐件已读/未读 + 限额 + 三类时点 + 目标会话动作（不可用给原因）。"""

    read_count, unread_count = state.material_counts()
    research_date = state.session_draft.research_date if state.session_draft is not None else None
    rows: list[ft.Control] = [
        section_title("agent_research_materials_title"),
        ft.Text(
            I18n.get("agent_research_materials_hint").format(
                items=MATERIAL_MAX_ITEMS, mb=MATERIAL_MAX_BYTES // 1_000_000
            ),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        ),
        ft.Row(
            spacing=8,
            controls=[
                ft.OutlinedButton(
                    I18n.get("agent_research_materials_add"),
                    icon=ft.Icons.ATTACH_FILE,
                    on_click=lambda _e: on_add_click(),
                ),
                ft.Text(
                    I18n.get("agent_research_materials_read_count_fmt").format(
                        read=read_count, unread=unread_count, total=len(state.materials)
                    ),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Text(
                    I18n.get("agent_research_materials_limit_used_fmt").format(
                        used=len(state.materials), limit=MATERIAL_MAX_ITEMS
                    ),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
        ),
        ft.Text(
            _date_text("agent_research_materials_research_date_fmt", research_date),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_SECONDARY,
        ),
    ]
    if not state.materials:
        rows.append(hint_text("agent_research_materials_empty"))
    rows.extend(_material_row(material, on_remove=on_remove) for material in state.materials)
    rows.extend(_rejected_row(rejected) for rejected in state.rejected_materials)
    if state.materials_error_key:
        rows.append(
            ft.Text(I18n.get(state.materials_error_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.ERROR)
        )
    if state.materials_notice_key:
        rows.append(
            ft.Text(I18n.get(state.materials_notice_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.SUCCESS)
        )
    rows.append(
        ft.Text(
            I18n.get("agent_research_materials_target_label"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.W_500,
        )
    )
    options = state.material_target_options()
    rows.append(
        ft.Dropdown(
            value=state.materials_target_session_id or "",
            options=[ft.DropdownOption(key=item.session_id, text=item.title or item.session_id) for item in options],
            on_select=lambda event: on_select_target(event.control.value or None),
            disabled=not options,
        )
    )
    if not options:
        rows.append(hint_text("agent_research_materials_target_empty"))
    pending = state.materials_transfer_pending
    rows.append(
        ft.Row(
            spacing=8,
            controls=[
                ft.FilledButton(
                    I18n.get("agent_research_materials_add_existing"),
                    on_click=lambda _e: on_add_to_session(),
                    disabled=pending or not state.materials_transfer_available,
                ),
                ft.OutlinedButton(
                    I18n.get("agent_research_materials_transfer"),
                    on_click=lambda _e: on_transfer(),
                    disabled=pending or not state.materials_transfer_available,
                ),
            ],
        )
    )
    if pending:
        rows.append(
            ft.Text(
                I18n.get("agent_research_materials_pending"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    if not state.materials_transfer_available:
        rows.append(
            ft.Text(
                I18n.get(state.materials_transfer_unavailable_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.WARNING,
            )
        )
    if state.materials_transfer_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.materials_transfer_error_key).format(code=state.materials_transfer_error_code or ""),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.materials_transfer_notice_key:
        params = dict(state.materials_transfer_notice_params)
        rows.append(
            ft.Text(
                I18n.get(state.materials_transfer_notice_key).format(
                    name=params.get("name", ""), revision=params.get("revision", "")
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.SUCCESS,
            )
        )
    return section_card(rows)
