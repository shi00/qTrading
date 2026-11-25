"""Skills 设置页签（声明式组件，设置页第 8 个页签）。

边界（CLAUDE.md §3.2 MVVM + docs/flet/v1-api-constraints.md）：

- View 只读 ``SkillsSettingsState`` 并发出命令；业务状态全部在 ViewModel；
- 所有异步命令经 ``page.run_task`` 调度（R16：事件处理器不做同步阻塞 IO）；
- 导入必须先预览，确认后才发布；预览如实展示文件数/展开字节/许可/依赖；
- 未接线 / 未加载 / 无 Skill 时如实提示，不显示可用的假象（R21）；
- 颜色与字号一律消费 ``AppColors`` / ``AppStyles`` token（R23）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.skills_settings_view_model import (
    IMPORT_KIND_DIRECTORY,
    IMPORT_KIND_ZIP,
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_WARNING,
    SkillVersionRow,
    SkillsSettingsState,
    SkillsSettingsViewModel,
)

logger = logging.getLogger(__name__)

_KIND_LABEL_KEYS: dict[str, str] = {
    IMPORT_KIND_ZIP: "skills_kind_zip",
    IMPORT_KIND_DIRECTORY: "skills_kind_directory",
}

_STATE_LABEL_KEYS: dict[str, str] = {
    "ready": "skills_state_ready",
    "disabled": "skills_state_disabled",
    "unavailable": "skills_state_unavailable",
}


def _get_page() -> ft.Page | None:
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _input_style() -> dict[str, Any]:
    return {
        "bgcolor": AppColors.INPUT_BG,
        "color": AppColors.INPUT_TEXT,
        "border": ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    }


def _status_color(level: str) -> str:
    if level == STATUS_SUCCESS:
        return AppColors.SUCCESS
    if level == STATUS_WARNING:
        return AppColors.WARNING
    if level == STATUS_ERROR:
        return AppColors.ERROR
    return AppColors.TEXT_SECONDARY


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


@ft.component
def SkillsTab(service: Any | None = None, user_scope: str = "local-desktop") -> ft.Container:
    """Skills 管理页。

    Args:
        service: 组合根接线的 ``AgentService``（结构性满足 Skill 目录方法）；
            ``None`` 表示未接线，页面只显示不可用原因，不提供任何写动作。
        user_scope: 与受理/派发同源的用户域。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    state, vm = use_viewmodel(factory=lambda: SkillsSettingsViewModel(service, user_scope=user_scope))

    def _run(command: Callable[[], Any]) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(command)

    def _trigger_load() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(vm.load)

    ft.use_effect(_trigger_load, dependencies=[])

    # -- 事件 ---------------------------------------------------------------

    def _on_import_path(e: ft.ControlEvent) -> None:
        vm.set_import_path(str(get_control_value(e.control, ft.TextField) or ""))

    def _on_import_kind(e: ft.ControlEvent) -> None:
        vm.set_import_kind(str(get_control_value(e.control, ft.Dropdown) or ""))

    def _on_role_field(field: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            vm.set_role_identity(field, str(get_control_value(e.control, ft.TextField) or ""))

        return _handler

    def _on_toggle_role(key: str, *, default: bool) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            if default:
                vm.toggle_role_default(key)
            else:
                vm.toggle_role_enabled(key)

        return _handler

    def _on_enable(key: str, enabled: bool) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            _run(lambda: vm.set_enabled(key, enabled=enabled))

        return _handler

    def _on_uninstall(key: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            _run(lambda: vm.uninstall(key))

        return _handler

    def _on_refresh(_e: ft.ControlEvent) -> None:
        _run(vm.load)

    def _on_preview(_e: ft.ControlEvent) -> None:
        _run(vm.preview_import)

    def _on_confirm(_e: ft.ControlEvent) -> None:
        _run(vm.confirm_import)

    def _on_load_role(_e: ft.ControlEvent) -> None:
        _run(vm.load_role_assignment)

    def _on_save_role(_e: ft.ControlEvent) -> None:
        _run(vm.save_role_assignment)

    # -- 展示 ---------------------------------------------------------------

    def _version_state_text(row: SkillVersionRow) -> str:
        return I18n.get(_STATE_LABEL_KEYS.get(row.read_state, "skills_state_unknown"))

    def _build_skill_list(current: SkillsSettingsState) -> ft.Control:
        if not current.loaded:
            return ft.Text(I18n.get("skills_not_loaded"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.WARNING)
        if not current.rows:
            return ft.Text(
                I18n.get("skills_list_empty"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY
            )
        rows: list[ft.DataRow] = []
        for skill in current.rows:
            for version in skill.versions:
                key = f"{skill.skill_id}@{version.version}"
                rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(skill.display_name, size=AppStyles.FONT_SIZE_BODY_SM)),
                            ft.DataCell(ft.Text(skill.skill_id, size=AppStyles.FONT_SIZE_CAPTION)),
                            ft.DataCell(ft.Text(str(version.version), size=AppStyles.FONT_SIZE_BODY_SM)),
                            ft.DataCell(
                                ft.Text(
                                    _version_state_text(version),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.SUCCESS if version.enabled else AppColors.TEXT_SECONDARY,
                                )
                            ),
                            ft.DataCell(ft.Text(str(version.file_count), size=AppStyles.FONT_SIZE_CAPTION)),
                            ft.DataCell(
                                ft.Text(_format_bytes(version.expanded_bytes), size=AppStyles.FONT_SIZE_CAPTION)
                            ),
                            ft.DataCell(
                                ft.Row(
                                    [
                                        ft.TextButton(
                                            I18n.get("skills_btn_disable" if version.enabled else "skills_btn_enable"),
                                            on_click=safe_on_click(_on_enable(key, not version.enabled)),
                                            disabled=current.busy,
                                        ),
                                        ft.TextButton(
                                            I18n.get("skills_btn_uninstall"),
                                            on_click=safe_on_click(_on_uninstall(key)),
                                            disabled=current.busy,
                                        ),
                                    ],
                                    spacing=AppStyles.SPACING_XS,
                                )
                            ),
                        ]
                    )
                )
        return ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_name"))),
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_id"))),
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_version")), numeric=True),
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_state"))),
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_files")), numeric=True),
                ft.DataColumn(label=ft.Text(I18n.get("skills_col_size"))),
                ft.DataColumn(label=ft.Text("")),
            ],
            rows=rows,
            heading_row_color=AppColors.TABLE_HEADER_BG,
            border=ft.Border.all(1, AppColors.DIVIDER),
            vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
        )

    def _build_preview(current: SkillsSettingsState) -> ft.Control:
        preview = current.preview
        if preview is None:
            return ft.Text(I18n.get("skills_preview_none"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT)
        summary = I18n.get(
            "skills_preview_summary",
            name=preview.display_name,
            version=preview.version,
            status=preview.status,
            files=preview.file_count,
            size=_format_bytes(preview.expanded_bytes),
            license=preview.license_id,
        )
        details: list[ft.Control] = [
            ft.Text(summary, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_PRIMARY),
            ft.Text(
                I18n.get("skills_preview_dependencies", values=", ".join(preview.dependencies) or "-"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
            ft.Text(
                I18n.get(
                    "skills_preview_missing_dependencies",
                    values=", ".join(preview.missing_dependencies) or "-",
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.WARNING if preview.missing_dependencies else AppColors.TEXT_SECONDARY,
            ),
        ]
        if preview.requires_confirmation:
            details.append(
                ft.Text(
                    I18n.get("skills_preview_requires_confirm"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.WARNING,
                )
            )
        return ft.Column(details, spacing=AppStyles.SPACING_XS)

    def _build_role_section(current: SkillsSettingsState) -> ft.Control:
        role_fields = ft.ResponsiveRow(
            [
                ft.Container(
                    ft.TextField(
                        label=I18n.get("skills_field_role_id"),
                        value=current.role_id,
                        on_change=safe_on_change(_on_role_field("role_id")),
                        disabled=current.busy,
                        text_size=AppStyles.FONT_SIZE_BODY_SM,
                        **_input_style(),
                    ),
                    col=AppStyles.COL_HALF,
                ),
                ft.Container(
                    ft.TextField(
                        label=I18n.get("skills_field_role_version"),
                        value=current.role_version,
                        on_change=safe_on_change(_on_role_field("role_version")),
                        disabled=current.busy,
                        text_size=AppStyles.FONT_SIZE_BODY_SM,
                        **_input_style(),
                    ),
                    col=AppStyles.COL_HALF,
                ),
            ],
            run_spacing=AppStyles.SPACING_SM,
        )
        selections: list[ft.Control] = []
        for skill in current.rows:
            for version in skill.versions:
                key = f"{skill.skill_id}@{version.version}"
                selections.append(
                    ft.Row(
                        [
                            ft.Checkbox(
                                label=f"{skill.display_name} v{version.version}",
                                value=key in current.role_enabled,
                                on_change=safe_on_change(_on_toggle_role(key, default=False)),
                                disabled=current.busy,
                                active_color=AppColors.PRIMARY,
                            ),
                            ft.Checkbox(
                                label=I18n.get("skills_role_default_label"),
                                value=key in current.role_defaults,
                                on_change=safe_on_change(_on_toggle_role(key, default=True)),
                                disabled=current.busy or key not in current.role_enabled,
                                active_color=AppColors.ACCENT,
                            ),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                )
        effective_text = I18n.get("skills_role_effective", values=", ".join(current.role_effective) or "-")
        return ft.Column(
            [
                role_fields,
                ft.Row(
                    [
                        ft.TextButton(
                            I18n.get("skills_btn_load_role"),
                            icon=ft.Icons.DOWNLOAD,
                            on_click=safe_on_click(_on_load_role),
                            disabled=current.busy,
                        ),
                        ft.Button(
                            content=I18n.get("skills_btn_save_role"),
                            icon=ft.Icons.SAVE,
                            on_click=safe_on_click(_on_save_role),
                            style=AppStyles.primary_button(),
                            disabled=current.busy,
                        ),
                    ],
                    spacing=AppStyles.SPACING_SM,
                ),
                ft.Text(
                    I18n.get("skills_role_enabled_title"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT
                ),
                *selections,
                ft.Text(effective_text, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY),
            ],
            spacing=AppStyles.SPACING_XS,
        )

    if not state.available:
        return ft.Container(
            content=ft.Column(
                [
                    DashboardCard(
                        content=ft.Column(
                            [
                                SectionHeader(I18n.get("skills_panel_title"), title_key="skills_panel_title"),
                                ft.Text(
                                    I18n.get(state.unavailable_reason_key),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.WARNING,
                                ),
                            ],
                            spacing=AppStyles.SPACING_SM,
                        )
                    )
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            expand=True,
        )

    status_text = I18n.get(state.status.key, **state.status.params) if state.status is not None else ""
    status_row = (
        ft.Row(
            [
                ft.Icon(ft.Icons.INFO_OUTLINE, size=AppStyles.FONT_SIZE_TITLE, color=_status_color(state.status_level)),
                ft.Text(status_text, size=AppStyles.FONT_SIZE_BODY_SM, color=_status_color(state.status_level)),
            ],
            spacing=AppStyles.SPACING_XS,
        )
        if status_text
        else ft.Container()
    )

    import_controls = ft.Column(
        [
            ft.ResponsiveRow(
                [
                    ft.Container(
                        ft.TextField(
                            label=I18n.get("skills_field_path"),
                            value=state.import_path,
                            on_change=safe_on_change(_on_import_path),
                            disabled=state.busy,
                            text_size=AppStyles.FONT_SIZE_BODY_SM,
                            **_input_style(),
                        ),
                        col=AppStyles.COL_TWO_THIRDS,
                    ),
                    ft.Container(
                        ft.Dropdown(
                            label=I18n.get("skills_field_kind"),
                            value=state.import_kind,
                            options=[
                                ft.dropdown.Option(key=kind, text=I18n.get(label_key))
                                for kind, label_key in _KIND_LABEL_KEYS.items()
                            ],
                            on_select=safe_on_change(_on_import_kind),
                            disabled=state.busy,
                            **_input_style(),
                        ),
                        col=AppStyles.COL_THIRD,
                    ),
                ],
                run_spacing=AppStyles.SPACING_SM,
            ),
            ft.Row(
                [
                    ft.OutlinedButton(
                        content=I18n.get("skills_btn_preview"),
                        icon=ft.Icons.PREVIEW,
                        on_click=safe_on_click(_on_preview),
                        disabled=state.busy,
                    ),
                    ft.Button(
                        content=I18n.get("skills_btn_confirm"),
                        icon=ft.Icons.UPLOAD,
                        on_click=safe_on_click(_on_confirm),
                        style=AppStyles.primary_button(),
                        disabled=state.busy or state.preview is None,
                    ),
                ],
                spacing=AppStyles.SPACING_SM,
            ),
            _build_preview(state),
        ],
        spacing=AppStyles.SPACING_SM,
    )

    return ft.Container(
        content=ft.Column(
            [
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("skills_panel_title"), title_key="skills_panel_title"),
                            ft.Text(
                                I18n.get("skills_panel_desc"),
                                size=AppStyles.FONT_SIZE_BODY_SM,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(
                                I18n.get("skills_list_title"),
                                title_key="skills_list_title",
                                action=ft.TextButton(
                                    I18n.get("skills_btn_refresh"),
                                    icon=ft.Icons.REFRESH,
                                    on_click=safe_on_click(_on_refresh),
                                    disabled=state.busy,
                                ),
                            ),
                            _build_skill_list(state),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("skills_import_title"), title_key="skills_import_title"),
                            ft.Text(
                                I18n.get("skills_import_desc"),
                                size=AppStyles.FONT_SIZE_CAPTION,
                                color=AppColors.TEXT_HINT,
                            ),
                            import_controls,
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("skills_role_title"), title_key="skills_role_title"),
                            ft.Text(
                                I18n.get("skills_role_desc"),
                                size=AppStyles.FONT_SIZE_CAPTION,
                                color=AppColors.TEXT_HINT,
                            ),
                            _build_role_section(state),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                status_row,
            ],
            spacing=AppStyles.SPACING_MD,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
