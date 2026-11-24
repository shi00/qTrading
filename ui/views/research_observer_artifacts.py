"""研究观察面板·成果区（成果卡 / 正文按需读取 / 比较入口占位）。

成果正文按需只读读取；比较入口仅为占位（禁用 + 明示后续版本提供），
不伪造已完成比较或结果。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import LoadedArtifactBody, ResearchObserverState, SessionArtifactView
from ui.views.research_observer_widgets import hint_text, section_card, section_title

#: 服务端正文不可用原因的已知映射；未知原因按原始码展示（未知即未知，不猜语义）。
_ARTIFACT_UNAVAILABLE_KEYS = {
    "body_not_saved": "agent_research_artifact_body_not_saved",
    "body_cleaned": "agent_research_artifact_body_cleaned",
}


def artifact_unavailable_text(reason: str | None) -> str:
    """成果正文不可用文案（已知原因映射 i18n；未知原因原样呈现错误码）。"""

    if reason is None:
        return I18n.get("agent_research_artifact_body_unloaded")
    key = _ARTIFACT_UNAVAILABLE_KEYS.get(reason)
    if key is not None:
        return I18n.get(key)
    return I18n.get("agent_research_artifact_unavailable_fmt").format(reason=reason)


def build_artifact_card(
    artifact: SessionArtifactView,
    *,
    loaded: LoadedArtifactBody | None,
    loading: bool,
    on_load_artifact: Callable[[str, int, str, str], None],
) -> ft.Control:
    """成果卡：摘要常驻；正文按需只读读取，未载入/不可用如实标注。"""

    header = ft.Row(
        [
            ft.Text(
                f"{artifact.artifact_kind} · v{artifact.artifact_version}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Text(
                artifact.execution_id,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            ),
        ],
        spacing=AppStyles.SPACING_SM,
    )
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
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def build_artifacts_section(
    state: ResearchObserverState,
    *,
    on_load_artifact: Callable[[str, int, str, str], None],
) -> list[ft.Control]:
    """成果区（摘要常驻；正文按需只读读取）+ 成果动作区 + 比较入口占位。"""

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
            )
        )
    if state.artifacts_has_more:
        rows.append(hint_text("agent_research_more_items"))
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
