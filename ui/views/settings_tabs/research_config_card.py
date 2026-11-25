"""研究配置统一入口卡片（W-18）。

把研究相关配置集中到一处：模型（只读，指向「模型连接」单一来源）、角色、预算
（只读，指向「模型使用」单一来源）、摘要策略（启用 / 保留条数 / 摘要 Prompt）。

- 默认值不在本页重复声明：角色与摘要默认来自 ``utils/config/llm.py``，界面只渲染
  ``AIBrainSettingsState`` 的字段；
- 摘要 Prompt 默认资产来自 ``core.prompt_base.RESEARCH_SUMMARY_PROMPT_DEFAULT``，
  可编辑、可恢复默认，恢复后按「未自定义」落盘以跟随默认资产升级；
- 预算与模型值由宿主（AIBrainTab）从既有 VM/配置来源传入，避免第二份读数。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.ai_brain_settings_view_model import (
    ROLE_PROMPT_NAME_KEYS,
    SAVE_ERROR,
    SAVE_SAVING,
    SAVE_SUCCESS,
    AIBrainSettingsState,
    AIBrainSettingsViewModel,
)
from utils.config.llm import RESEARCH_ROLE_KEYS, RESEARCH_SUMMARY_KEEP_MESSAGES_MAX, RESEARCH_SUMMARY_KEEP_MESSAGES_MIN

_INPUT_WIDTH_SMALL = 120


def _build_budget_and_model_rows(model_label: str, budget_text: str) -> ft.Column:
    """模型与预算只读行：数值来自各自单一来源，本页不复制默认值。"""

    def _row(label_key: str, value: str, hint_key: str) -> ft.Row:
        return ft.Row(
            [
                ft.Text(I18n.get(label_key), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    value or I18n.get("research_config_value_unset"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_PRIMARY if value else AppColors.TEXT_HINT,
                ),
                ft.Text(I18n.get(hint_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
            ],
            spacing=AppStyles.SPACING_SM,
            wrap=True,
        )

    return ft.Column(
        [
            _row("research_config_model_label", model_label, "research_config_model_hint"),
            _row("research_config_budget_label", budget_text, "research_config_budget_hint"),
        ],
        spacing=AppStyles.SPACING_XS,
    )


@ft.component
def ResearchConfigCard(
    state: AIBrainSettingsState,
    vm: AIBrainSettingsViewModel,
    *,
    budget_text: str = "",
    on_save: Callable[[], None] = lambda: None,
    on_request_reset_prompt: Callable[[], None] = lambda: None,
) -> ft.Control:
    """研究配置统一入口（角色 / 模型 / 预算 / 摘要策略集中一处）。"""
    role_options = [
        ft.dropdown.Option(key=role_key, text=I18n.get(ROLE_PROMPT_NAME_KEYS[role_key]))
        for role_key in RESEARCH_ROLE_KEYS
    ]
    is_saving = state.research_save_state == SAVE_SAVING
    status_key = ""
    if state.research_save_state == SAVE_SUCCESS:
        status_key = "research_config_saved"
    elif state.research_save_state == SAVE_ERROR:
        status_key = state.research_warning_key or "research_config_save_failed"

    return DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(I18n.get("research_config_title"), title_key="research_config_title"),
                        ft.Icon(ft.Icons.TUNE, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("research_config_desc"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
                _build_budget_and_model_rows(state.research_model_label, budget_text),
                ft.Divider(height=20, color=AppColors.BORDER),
                ft.Dropdown(
                    label=I18n.get("research_config_role_label"),
                    value=state.research_role,
                    options=role_options,
                    width=220,
                    on_select=safe_on_select(
                        lambda e: vm.set_research_role(str(get_control_value(e.control, ft.Dropdown)))
                    ),
                ),
                ft.Row(
                    [
                        ft.Switch(
                            label=I18n.get("research_config_summary_enabled"),
                            value=state.research_summary_enabled,
                            on_change=safe_on_change(
                                lambda e: vm.set_research_summary_enabled(bool(get_control_value(e.control, ft.Switch)))
                            ),
                        ),
                        ft.TextField(
                            label=I18n.get("research_config_summary_keep_label"),
                            value=state.research_summary_keep_messages_value,
                            width=_INPUT_WIDTH_SMALL,
                            keyboard_type=ft.KeyboardType.NUMBER,
                            hint_text=I18n.get("research_config_summary_keep_hint").format(
                                min=RESEARCH_SUMMARY_KEEP_MESSAGES_MIN,
                                max=RESEARCH_SUMMARY_KEEP_MESSAGES_MAX,
                            ),
                            on_change=safe_on_change(
                                lambda e: vm.set_research_summary_keep_messages(
                                    str(get_control_value(e.control, ft.TextField))
                                )
                            ),
                        ),
                    ],
                    spacing=AppStyles.SPACING_MD,
                    wrap=True,
                ),
                ft.Row(
                    [
                        SectionHeader(
                            I18n.get("research_config_summary_prompt_label"),
                            title_key="research_config_summary_prompt_label",
                        ),
                        ft.Text(
                            I18n.get(
                                "research_config_prompt_source_default"
                                if state.research_summary_prompt_is_default
                                else "research_config_prompt_source_custom"
                            ),
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=AppColors.TEXT_HINT,
                        ),
                        ft.TextButton(
                            I18n.get("research_config_prompt_reset"),
                            icon=ft.Icons.RESTORE,
                            on_click=safe_on_click(lambda _e: on_request_reset_prompt()),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                ),
                ft.TextField(
                    value=state.research_summary_prompt_value,
                    multiline=True,
                    min_lines=4,
                    max_lines=12,
                    text_size=AppStyles.FONT_SIZE_BODY_SM,
                    hint_text=I18n.get("research_config_summary_prompt_hint"),
                    bgcolor=AppColors.INPUT_BG,
                    color=AppColors.INPUT_TEXT,
                    border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                    on_change=safe_on_change(
                        lambda e: vm.set_research_summary_prompt(str(get_control_value(e.control, ft.TextField)))
                    ),
                ),
                ft.Row(
                    [
                        ft.Button(
                            content=I18n.get("research_config_save"),
                            icon=ft.Icons.SAVE,
                            on_click=safe_on_click(lambda _e: on_save()),
                            style=AppStyles.primary_button(),
                            height=40,
                            disabled=is_saving,
                        ),
                        ft.ProgressRing(visible=is_saving, width=20, height=20, stroke_width=2),
                        ft.Text(
                            I18n.get(status_key) if status_key else "",
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=(
                                AppColors.SUCCESS
                                if state.research_save_state == SAVE_SUCCESS
                                else AppColors.ERROR
                                if state.research_save_state == SAVE_ERROR
                                else AppColors.TEXT_HINT
                            ),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=AppStyles.SPACING_SM,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        ),
    )
