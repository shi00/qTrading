"""角色管理设置页签（声明式组件，Task 9）。

边界（CLAUDE.md §3.2 MVVM + docs/flet/v1-api-constraints.md）：

- View 只读 ``RolesSettingsState`` 并发出命令，业务状态全部在 ViewModel；
- 组合根未接线（``catalog is None``）时只渲染不可用说明，不构造 VM、不伪造可用（R21）；
- 异步命令经 ``page.run_task`` 调度（R16）；
- VM 产出的错误/提示键以模块级字面量元组静态登记（i18n 死键门禁可 AST 检出），
  非空时经 ``ft.use_effect`` 只弹一次 snackbar（不调用 VM 私有方法）；
- 首帧/加载失败不得伪装成「空列表」：未完成加载显示加载提示，加载失败持久显示不可用（R21）；
- 停用/启用须经二次确认对话框，确认后才触达服务；
- 颜色/字号只用 ``AppColors`` / ``AppStyles``（R23）；渲染顶层无 logger/print 副作用（UIX-10）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.roles_settings_view_model import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    RoleDraft,
    RoleRow,
    RolesSettingsState,
    RolesSettingsViewModel,
)

#: 角色状态 → 展示文案键（列表状态列）。
_STATUS_LABEL_KEYS: dict[str, str] = {
    STATUS_ACTIVE: "agent_roles_status_active",
    STATUS_DISABLED: "agent_roles_status_disabled",
}

#: 预置角色 → 展示名键（目录中无预置定义，名称由 i18n 承载）。
_PRESET_LABEL_KEYS: dict[str, str] = {
    "main": "agent_roles_preset_main",
    "sub": "agent_roles_preset_sub",
}

#: VM 产出并在 snackbar 展示的错误/提示键（模块级字面量，供 i18n 死键门禁 AST 检出）。
#: 与 VM 实际产出键的集合一致性由 ``test_vm_message_keys_cover_every_vm_produced_key``
#: 守护；运行时直接 ``I18n.get(key)``，未知键原样展示，不静默吞掉提示。
_VM_MESSAGE_KEYS: tuple[str, ...] = (
    "agent_roles_unavailable",
    "agent_roles_preset_readonly",
    "agent_roles_conflict",
    "agent_roles_invalid",
    "agent_roles_model_unavailable",
    "agent_roles_save_failed",
    "agent_roles_created",
    "agent_roles_saved",
    "agent_roles_version_bumped",
    "agent_roles_disabled",
    "agent_roles_enabled",
)


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


def _role_name(row: RoleRow) -> str:
    """行展示名：预置行按 i18n，自定义行用名称（缺失时回退 role_id，不伪装空名）。"""

    if row.is_preset:
        preset_key = _PRESET_LABEL_KEYS.get(row.role_id)
        if preset_key is not None:
            return I18n.get(preset_key)
    return row.display_name or row.role_id


def _status_text(status: str) -> str:
    key = _STATUS_LABEL_KEYS.get(status)
    if key is None:
        # 未知状态原样展示，不回退成「启用中」（不把未知伪装成业务上的合法值，R21）。
        return status
    return I18n.get(key)


def _message_text(key: str | None) -> str | None:
    """把 VM 键解析为展示文案；空键不产生提示。"""

    if not key:
        return None
    return I18n.get(key)


def _build_unavailable_view() -> ft.Control:
    """组合根未接线：只说明不可用原因，不提供任何写动作。"""

    return ft.Container(
        content=ft.Column(
            [
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("agent_roles_title"), title_key="agent_roles_title"),
                            ft.Text(
                                I18n.get("agent_roles_not_wired"),
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


def _build_role_row(
    row: RoleRow,
    pending: bool,
    on_edit: Callable[[], None],
    on_copy: Callable[[], None],
    on_toggle: Callable[[], None],
) -> ft.Control:
    """单行角色：预置行只读（仅复制），自定义行可编辑/复制/启停。"""

    name_controls: list[ft.Control] = [
        ft.Text(_role_name(row), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_PRIMARY)
    ]
    if row.is_preset:
        name_controls.append(
            ft.Container(
                content=ft.Text(
                    I18n.get("agent_roles_preset_tag"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_ON_PRIMARY,
                ),
                bgcolor=AppColors.PRIMARY,
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                border_radius=4,
            )
        )

    actions: list[ft.Control] = []
    if not row.is_preset:
        actions.append(
            ft.TextButton(
                I18n.get("agent_roles_edit"),
                on_click=safe_on_click(lambda _e: on_edit()),
                disabled=pending,
            )
        )
    actions.append(
        ft.TextButton(
            I18n.get("agent_roles_copy"),
            on_click=safe_on_click(lambda _e: on_copy()),
            disabled=pending,
        )
    )
    if not row.is_preset:
        actions.append(
            ft.TextButton(
                I18n.get("agent_roles_enable" if row.status == STATUS_DISABLED else "agent_roles_disable"),
                on_click=safe_on_click(lambda _e: on_toggle()),
                disabled=pending or row.role_version is None,
            )
        )

    return ft.Container(
        content=ft.Row(
            [
                ft.Column(name_controls, spacing=AppStyles.SPACING_XS),
                ft.Text(row.role_id, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                ft.Text(row.role_version or "-", size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(_status_text(row.status), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    row.model_connection_id or I18n.get("agent_roles_model_inherit"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Row(actions, spacing=AppStyles.SPACING_XS),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(vertical=AppStyles.SPACING_XS),
    )


@ft.component
def _RolesList(
    state: RolesSettingsState,
    vm: RolesSettingsViewModel,
) -> ft.Control:
    """角色列表：预置行 + 自定义行，停用/启用经二次确认。"""

    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)
    confirm_role_id, set_confirm_role_id = ft.use_state("")
    confirm_disabling, set_confirm_disabling = ft.use_state(False)

    def _run(command: Callable[[], Any]) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(command)

    def _ask_toggle(row: RoleRow) -> None:
        set_confirm_role_id(row.role_id)
        set_confirm_disabling(row.status == STATUS_ACTIVE)

    def _confirm_toggle() -> None:
        role_id = confirm_role_id
        set_confirm_role_id("")
        if not role_id:
            return
        _run(lambda: vm.toggle_status(role_id))

    def _cancel_toggle() -> None:
        set_confirm_role_id("")

    rows: list[ft.Control] = []
    for row in state.roles:
        rows.append(
            _build_role_row(
                row=row,
                pending=state.pending,
                on_edit=lambda role_id=row.role_id: _run(lambda: vm.begin_edit(role_id)),
                on_copy=lambda role_id=row.role_id: _run(lambda: vm.copy(role_id)),
                on_toggle=lambda current=row: _ask_toggle(current),
            )
        )

    disabling = confirm_disabling
    if confirm_role_id:
        title_key = "agent_roles_confirm_disable_title" if disabling else "agent_roles_confirm_enable_title"
        body_key = "agent_roles_confirm_disable_body" if disabling else "agent_roles_confirm_enable_body"
    else:
        title_key = "agent_roles_confirm_disable_title"
        body_key = "agent_roles_confirm_disable_body"
    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get(title_key)),
            content=ft.Text(I18n.get(body_key), color=AppColors.TEXT_SECONDARY),
            actions=[
                ft.TextButton(
                    I18n.get("common_cancel"),
                    on_click=safe_on_click(lambda _e: _cancel_toggle()),
                ),
                ft.Button(
                    I18n.get("common_confirm"),
                    on_click=safe_on_click(lambda _e: _confirm_toggle()),
                    style=AppStyles.danger_button() if disabling else AppStyles.primary_button(),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if confirm_role_id
        else None
    )
    ft.use_dialog(dialog)

    return ft.Column(rows, spacing=AppStyles.SPACING_XS)


@ft.component
def _RoleEditor(draft: RoleDraft, vm: RolesSettingsViewModel, pending: bool) -> ft.Control:
    """编辑态草稿：role_id 新建/复制可编辑，编辑既有角色只读；提交中禁用按钮。"""

    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # 基线取编辑器首次挂载时（即加载完成）的正文；切换编辑对象时重置，避免误报升版本。
    baseline_ref = ft.use_ref((draft.role_id, draft.prompt_body))
    if baseline_ref.current is None or baseline_ref.current[0] != draft.role_id:
        baseline_ref.current = (draft.role_id, draft.prompt_body)
    baseline_prompt = baseline_ref.current[1]

    def _run(command: Callable[[], Any]) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(command)

    def _on_role_id(e: ft.ControlEvent) -> None:
        vm.set_role_id(str(get_control_value(e.control, ft.TextField) or ""))

    def _on_display_name(e: ft.ControlEvent) -> None:
        vm.set_display_name(str(get_control_value(e.control, ft.TextField) or ""))

    def _on_prompt_body(e: ft.ControlEvent) -> None:
        vm.set_prompt_body(str(get_control_value(e.control, ft.TextField) or ""))

    def _on_model_connection(e: ft.ControlEvent) -> None:
        value = str(get_control_value(e.control, ft.TextField) or "").strip()
        vm.set_model_connection_id(value or None)

    controls: list[ft.Control] = [
        ft.ResponsiveRow(
            [
                ft.Container(
                    ft.TextField(
                        label=I18n.get("agent_roles_role_id_label"),
                        value=draft.role_id,
                        on_change=safe_on_change(_on_role_id),
                        disabled=not draft.is_new,
                        text_size=AppStyles.FONT_SIZE_BODY_SM,
                        **_input_style(),
                    ),
                    col=AppStyles.COL_HALF,
                ),
                ft.Container(
                    ft.TextField(
                        label=I18n.get("agent_roles_display_name_label"),
                        value=draft.display_name,
                        on_change=safe_on_change(_on_display_name),
                        text_size=AppStyles.FONT_SIZE_BODY_SM,
                        **_input_style(),
                    ),
                    col=AppStyles.COL_HALF,
                ),
            ],
            run_spacing=AppStyles.SPACING_SM,
        ),
        ft.TextField(
            label=I18n.get("agent_roles_prompt_label"),
            value=draft.prompt_body,
            on_change=safe_on_change(_on_prompt_body),
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            **_input_style(),
        ),
        ft.TextField(
            label=I18n.get("agent_roles_model_label"),
            hint_text=I18n.get("agent_roles_model_inherit"),
            value=draft.model_connection_id or "",
            on_change=safe_on_change(_on_model_connection),
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            **_input_style(),
        ),
    ]
    if not draft.is_new:
        controls.insert(
            1,
            ft.Text(
                f"{I18n.get('agent_roles_version_label')}: {draft.role_version or '-'}",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
        )
    if not draft.is_new and draft.prompt_body != baseline_prompt:
        controls.append(
            ft.Text(
                I18n.get("agent_roles_version_bump_hint"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.WARNING,
            )
        )
    controls.append(
        ft.Row(
            [
                ft.Button(
                    content=I18n.get("agent_roles_save"),
                    icon=ft.Icons.SAVE,
                    on_click=safe_on_click(lambda _e: _run(vm.save)),
                    style=AppStyles.primary_button(),
                    disabled=pending,
                ),
                ft.TextButton(
                    I18n.get("agent_roles_cancel"),
                    on_click=safe_on_click(lambda _e: vm.cancel_edit()),
                    disabled=pending,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        )
    )
    return ft.Column(controls, spacing=AppStyles.SPACING_SM)


@ft.component
def _RolesTabBody(show_snack: Callable[[str], None], catalog: Any) -> ft.Control:
    """已接线内容：构造 VM、加载目录、渲染列表与编辑器。"""

    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    state, vm = use_viewmodel(factory=lambda: RolesSettingsViewModel(catalog))

    def _trigger_load() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(vm.load)

    ft.use_effect(_trigger_load, dependencies=[])

    def _announce() -> None:
        for key in (state.error_key, state.notice_key):
            text = _message_text(key)
            if text:
                show_snack(text)

    # 依赖键变化才弹一次；中间态（pending 置位清空键）不产生提示，重复失败可再次提示。
    ft.use_effect(_announce, dependencies=[(state.error_key, state.notice_key)])

    if not state.loaded:
        list_content: ft.Control = ft.Text(
            I18n.get("agent_roles_loading"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_HINT,
        )
    elif state.load_failed:
        # 加载失败持久展示不可用，而不是把「未知」渲染成空列表（R21）。
        list_content = ft.Text(
            I18n.get("agent_roles_unavailable"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.ERROR,
        )
    else:
        list_controls: list[ft.Control] = [_RolesList(state=state, vm=vm)]
        if not any(not row.is_preset for row in state.roles):
            list_controls.append(
                ft.Text(
                    I18n.get("agent_roles_empty"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        list_content = ft.Column(list_controls, spacing=AppStyles.SPACING_XS)

    sections: list[ft.Control] = [
        DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("agent_roles_title"), title_key="agent_roles_title"),
                    ft.Button(
                        content=I18n.get("agent_roles_new"),
                        icon=ft.Icons.ADD,
                        on_click=safe_on_click(lambda _e: vm.begin_create()),
                        style=AppStyles.primary_button(),
                        disabled=state.pending,
                    ),
                ],
                spacing=AppStyles.SPACING_SM,
            )
        ),
        DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("agent_roles_list_title"), title_key="agent_roles_list_title"),
                    list_content,
                ],
                spacing=AppStyles.SPACING_SM,
            )
        ),
    ]
    if state.editing is not None:
        editor_title_key = "agent_roles_new" if state.editing.is_new else "agent_roles_edit"
        sections.append(
            DashboardCard(
                content=ft.Column(
                    [
                        SectionHeader(I18n.get(editor_title_key), title_key=editor_title_key),
                        _RoleEditor(draft=state.editing, vm=vm, pending=state.pending),
                    ],
                    spacing=AppStyles.SPACING_SM,
                )
            )
        )
    return ft.Container(
        content=ft.Column(
            sections,
            spacing=AppStyles.SPACING_MD,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )


@ft.component
def RolesTab(show_snack: Callable[[str], None], catalog: Any | None = None) -> ft.Control:
    """设置页「角色管理」tab。

    Args:
        show_snack: snackbar 触发函数。
        catalog: 组合根接线的角色目录服务（结构性满足 ``RoleCatalogPort``）；
            ``None`` 表示未接线，只显示不可用说明，不构造 VM。
    """

    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    if catalog is None:
        return _build_unavailable_view()
    return _RolesTabBody(show_snack, catalog)


__all__ = ["RolesTab"]
