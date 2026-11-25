"""MCP 服务设置页签（声明式组件，设置页第 3 个页签）。

边界（CLAUDE.md §3.2 MVVM + docs/flet/v1-api-constraints.md）：

- View 只读 ``MCPServicesState`` 并发出命令；业务状态全部在 ViewModel；
- 所有异步命令经 ``page.run_task`` 调度（R16：事件处理器不做同步阻塞 IO）；
- 异步出站只在用户点击「连接测试」「启连」时发生，本页渲染不建立任何连接；
- 未接线 / 未保存 / 未测试时如实提示原因（R21），不显示可用的假象；
- 颜色与字号一律消费 ``AppColors`` / ``AppStyles`` token（R23）。
"""

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.mcp_services_view_model import (
    DRAFT_FLAG_FIELDS,
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_WARNING,
    MCPConfigDraft,
    MCPServiceRow,
    MCPServicesState,
    MCPServicesViewModel,
)
from core.agent_types import MCP_BUDGET_FIELDS, MCPTransportKind

logger = logging.getLogger(__name__)

#: 预算字段 → 表单标签 i18n key（顺序即 ``MCP_BUDGET_FIELDS``，单一来源）。
_BUDGET_LABEL_KEYS: dict[str, str] = {
    "receive_bytes": "mcp_budget_receive_bytes",
    "decoded_bytes": "mcp_budget_decoded_bytes",
    "structure_depth": "mcp_budget_structure_depth",
    "catalog_items": "mcp_budget_catalog_items",
    "catalog_bytes": "mcp_budget_catalog_bytes",
    "schema_bytes": "mcp_budget_schema_bytes",
    "single_result_bytes": "mcp_budget_single_result_bytes",
    "retention_bytes": "mcp_budget_retention_bytes",
}

#: 传输类型 → 展示名 i18n key。
_TRANSPORT_LABEL_KEYS: dict[str, str] = {
    MCPTransportKind.STDIO.value: "mcp_transport_stdio",
    MCPTransportKind.STREAMABLE_HTTP.value: "mcp_transport_streamable_http",
    MCPTransportKind.SSE.value: "mcp_transport_sse",
}


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``，未在渲染上下文时返回 ``None``。"""

    try:
        return ft.context.page
    except RuntimeError:
        return None


def _input_style() -> dict[str, Any]:
    """输入框统一 token 样式（颜色/边框，R23）。"""

    return {
        "bgcolor": AppColors.INPUT_BG,
        "color": AppColors.INPUT_TEXT,
        "border": ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    }


def _status_color(level: str) -> str:
    """状态级别 → 语义色 token。"""

    if level == STATUS_SUCCESS:
        return AppColors.SUCCESS
    if level == STATUS_WARNING:
        return AppColors.WARNING
    if level == STATUS_ERROR:
        return AppColors.ERROR
    return AppColors.TEXT_SECONDARY


@ft.component
def MCPServicesTab(lifecycle: Any | None = None) -> ft.Container:
    """MCP 服务设置页。

    Args:
        lifecycle: 组合根接线的 ``MCPServiceLifecycle``（结构性满足本 VM 所需方法）；
            ``None`` 表示未接线，页面只显示不可用原因，不提供任何连接动作。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    state, vm = use_viewmodel(factory=lambda: MCPServicesViewModel(lifecycle))

    def _run(command: Callable[[], Any]) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(command)

    def _trigger_load() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(vm.load)

    ft.use_effect(_trigger_load, dependencies=[])

    # -- 表单事件（全部走 VM 命令；异步命令经 run_task） -------------------

    def _on_text_change(field: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            vm.set_draft_text(field, str(get_control_value(e.control, ft.TextField) or ""))

        return _handler

    def _on_flag_change(field: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            value = bool(get_control_value(e.control, ft.Switch))
            if field in DRAFT_FLAG_FIELDS:
                vm.set_draft_flag(field, value)

        return _handler

    def _on_budget_change(index: int) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            vm.set_budget_value(index, str(get_control_value(e.control, ft.TextField) or ""))

        return _handler

    def _on_transport_change(e: ft.ControlEvent) -> None:
        vm.set_draft_text("transport_kind", str(get_control_value(e.control, ft.Dropdown) or ""))

    def _on_select(identity_key: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            vm.select_service(identity_key)

        return _handler

    def _on_toggle_tool(name: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            vm.toggle_approved_tool(name)

        return _handler

    def _on_refresh(_e: ft.ControlEvent) -> None:
        _run(vm.load)

    def _on_new(_e: ft.ControlEvent) -> None:
        vm.begin_new()

    def _on_save(_e: ft.ControlEvent) -> None:
        _run(vm.save_config)

    def _on_test(_e: ft.ControlEvent) -> None:
        _run(vm.connection_test)

    def _on_connect(_e: ft.ControlEvent) -> None:
        _run(vm.connect)

    def _on_disconnect(_e: ft.ControlEvent) -> None:
        _run(vm.disconnect)

    def _on_retry_residue(_e: ft.ControlEvent) -> None:
        _run(vm.retry_test_residue_close)

    # -- 删除与角色分配事件 -------------------------------------------------

    def _on_request_delete(_e: ft.ControlEvent) -> None:
        vm.request_delete()

    def _on_cancel_delete(_e: ft.ControlEvent) -> None:
        vm.cancel_delete()

    def _on_confirm_delete(_e: ft.ControlEvent) -> None:
        _run(vm.confirm_delete)

    def _on_role_text(field: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            vm.set_role_text(field, str(get_control_value(e.control, ft.TextField) or ""))

        return _handler

    def _on_toggle_role_service(identity_key: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            vm.toggle_role_service(identity_key)

        return _handler

    def _on_role_default(e: ft.ControlEvent) -> None:
        vm.set_role_default(str(get_control_value(e.control, ft.Dropdown) or ""))

    def _on_role_tools(identity_key: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(e: ft.ControlEvent) -> None:
            vm.set_role_tools(identity_key, str(get_control_value(e.control, ft.TextField) or ""))

        return _handler

    def _on_select_role(role_id: str, role_version: str) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            vm.select_role_assignment(role_id, role_version)

        return _handler

    def _on_new_role(_e: ft.ControlEvent) -> None:
        vm.begin_role_new()

    def _on_save_role(_e: ft.ControlEvent) -> None:
        _run(vm.save_role_assignment)

    # -- 字段构造 -----------------------------------------------------------

    def _text_field(label_key: str, value: str, field: str, *, multiline: bool = False) -> ft.TextField:
        extra: dict[str, Any] = _input_style()
        if multiline:
            extra["min_lines"] = 2
            extra["max_lines"] = 6
        return ft.TextField(
            label=I18n.get(label_key),
            value=value,
            multiline=multiline,
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            on_change=safe_on_change(_on_text_change(field)),
            disabled=state.busy,
            **extra,
        )

    def _number_field(label_key: str, value: str, field_or_index: int | str) -> ft.TextField:
        on_change = (
            _on_budget_change(field_or_index) if isinstance(field_or_index, int) else _on_text_change(field_or_index)
        )
        return ft.TextField(
            label=I18n.get(label_key),
            value=value,
            width=AppStyles.CONTROL_WIDTH_SM,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=safe_on_change(on_change),
            disabled=state.busy,
            **_input_style(),
        )

    def _flag_switch(label_key: str, field: str, value: bool) -> ft.Switch:
        return ft.Switch(
            label=I18n.get(label_key),
            value=value,
            on_change=safe_on_change(_on_flag_change(field)),
            disabled=state.busy,
            active_color=AppColors.PRIMARY,
        )

    def _build_config_form(draft: MCPConfigDraft) -> ft.Column:
        is_stdio = draft.transport_kind == MCPTransportKind.STDIO.value
        transport_fields: list[ft.Control] = (
            [
                _text_field("mcp_field_command", draft.command, "command"),
                _text_field("mcp_field_args", draft.args_text, "args_text", multiline=True),
                ft.Text(
                    I18n.get("mcp_hint_args"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
                _text_field("mcp_field_env", draft.env_text, "env_text", multiline=True),
                ft.Text(
                    I18n.get("mcp_hint_env"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
                _text_field("mcp_field_cwd", draft.cwd, "cwd"),
            ]
            if is_stdio
            else [
                _text_field("mcp_field_url", draft.url, "url"),
                _text_field("mcp_field_headers", draft.headers_text, "headers_text", multiline=True),
                ft.Text(
                    I18n.get("mcp_hint_headers"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
                _flag_switch("mcp_field_sse_approved", "sse_compat_approved", draft.sse_compat_approved),
                _flag_switch("mcp_field_http_approved", "http_risk_approved", draft.http_risk_approved),
            ]
        )
        return ft.Column(
            [
                ft.ResponsiveRow(
                    [
                        ft.Container(
                            _text_field("mcp_field_service_id", draft.service_id, "service_id"), col=AppStyles.COL_HALF
                        ),
                        ft.Container(
                            _number_field("mcp_field_config_version", draft.config_version, "config_version"),
                            col=AppStyles.COL_HALF,
                        ),
                        ft.Container(
                            _text_field("mcp_field_display_name", draft.display_name, "display_name"),
                            col=AppStyles.COL_HALF,
                        ),
                        ft.Container(
                            ft.Dropdown(
                                label=I18n.get("mcp_field_transport"),
                                value=draft.transport_kind,
                                options=[
                                    ft.dropdown.Option(key=kind, text=I18n.get(label_key))
                                    for kind, label_key in _TRANSPORT_LABEL_KEYS.items()
                                ],
                                on_select=safe_on_change(_on_transport_change),
                                disabled=state.busy,
                                **_input_style(),
                            ),
                            col=AppStyles.COL_HALF,
                        ),
                    ],
                    run_spacing=AppStyles.SPACING_SM,
                ),
                *transport_fields,
                ft.ResponsiveRow(
                    [
                        ft.Container(
                            _number_field("mcp_field_connect_deadline", draft.connect_deadline_s, "connect_deadline_s"),
                            col=AppStyles.COL_HALF,
                        ),
                        ft.Container(
                            _number_field("mcp_field_tool_deadline", draft.tool_deadline_s, "tool_deadline_s"),
                            col=AppStyles.COL_HALF,
                        ),
                    ],
                    run_spacing=AppStyles.SPACING_SM,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        )

    def _build_budget_section(draft: MCPConfigDraft) -> ft.Column:
        return ft.Column(
            [
                ft.Text(I18n.get("mcp_budget_desc"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                ft.ResponsiveRow(
                    [
                        ft.Container(
                            _number_field(_BUDGET_LABEL_KEYS[name], draft.budget_values[index], index),
                            col=AppStyles.COL_THIRD,
                        )
                        for index, name in enumerate(MCP_BUDGET_FIELDS)
                    ],
                    run_spacing=AppStyles.SPACING_SM,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        )

    def _build_audit_section(draft: MCPConfigDraft) -> ft.Column:
        return ft.Column(
            [
                ft.Text(I18n.get("mcp_audit_desc"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                _text_field(
                    "mcp_field_audit_tool_scope", draft.audit_tool_scope_text, "audit_tool_scope_text", multiline=True
                ),
                _text_field(
                    "mcp_field_audit_fingerprint",
                    draft.audit_implementation_fingerprint,
                    "audit_implementation_fingerprint",
                ),
                ft.Text(
                    I18n.get("mcp_hint_audit_fingerprint"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
                _text_field("mcp_field_audit_evidence", draft.audit_evidence_source, "audit_evidence_source"),
                _text_field("mcp_field_audit_reviewed_at", draft.audit_reviewed_at, "audit_reviewed_at"),
                _text_field(
                    "mcp_field_audit_invalidation",
                    draft.audit_invalidation_text,
                    "audit_invalidation_text",
                    multiline=True,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        )

    def _row_status(row: MCPServiceRow) -> ft.Text:
        """行状态：失效原因优先（不把已知不可用渲染成「未连接」）。"""

        if row.problem_key is not None:
            return ft.Text(I18n.get(row.problem_key), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.WARNING)
        return ft.Text(
            I18n.get("mcp_state_connected" if row.connected else "mcp_state_disconnected"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.SUCCESS if row.connected else AppColors.TEXT_SECONDARY,
        )

    def _build_service_list(current: MCPServicesState) -> ft.Control:
        if not current.loaded:
            body: ft.Control = ft.Text(
                I18n.get("mcp_not_loaded"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
            )
        elif not current.rows:
            body = ft.Text(
                I18n.get("mcp_list_empty"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        else:
            rows = [
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(row.display_name, size=AppStyles.FONT_SIZE_BODY_SM)),
                        ft.DataCell(ft.Text(row.service_id, size=AppStyles.FONT_SIZE_BODY_SM)),
                        ft.DataCell(ft.Text(str(row.config_version), size=AppStyles.FONT_SIZE_BODY_SM)),
                        ft.DataCell(
                            ft.Text(
                                I18n.get(_TRANSPORT_LABEL_KEYS.get(row.transport_kind, "mcp_field_transport")),
                                size=AppStyles.FONT_SIZE_BODY_SM,
                            )
                        ),
                        ft.DataCell(_row_status(row)),
                        ft.DataCell(
                            ft.TextButton(
                                I18n.get("mcp_btn_select"),
                                on_click=safe_on_click(_on_select(row.identity_key)),
                                disabled=current.busy,
                            )
                        ),
                    ]
                )
                for row in current.rows
            ]
            body = ft.DataTable(
                columns=[
                    ft.DataColumn(label=ft.Text(I18n.get("mcp_col_service"))),
                    ft.DataColumn(label=ft.Text(I18n.get("mcp_col_service_id"))),
                    ft.DataColumn(label=ft.Text(I18n.get("mcp_col_version")), numeric=True),
                    ft.DataColumn(label=ft.Text(I18n.get("mcp_col_transport"))),
                    ft.DataColumn(label=ft.Text(I18n.get("mcp_col_status"))),
                    ft.DataColumn(label=ft.Text("")),
                ],
                rows=rows,
                heading_row_color=AppColors.TABLE_HEADER_BG,
                border=ft.Border.all(1, AppColors.DIVIDER),
                vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
            )
        if current.unreadable_count <= 0:
            return body
        return ft.Column(
            [
                body,
                ft.Text(
                    I18n.get("mcp_list_unreadable_hint", count=str(current.unreadable_count)),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.WARNING,
                ),
            ],
            spacing=AppStyles.SPACING_XS,
        )

    def _build_tools_section(current: MCPServicesState) -> ft.Column:
        if not current.discovered_tools:
            return ft.Column(
                [
                    ft.Text(
                        I18n.get("mcp_tools_empty"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        return ft.Column(
            [
                ft.Text(I18n.get("mcp_tools_desc"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                *[
                    ft.Checkbox(
                        label=name,
                        value=name in current.approved_tools,
                        on_change=safe_on_change(_on_toggle_tool(name)),
                        disabled=current.busy,
                        active_color=AppColors.PRIMARY,
                    )
                    for name in current.discovered_tools
                ],
            ],
            spacing=AppStyles.SPACING_XS,
        )

    # -- 角色分配 -----------------------------------------------------------

    def _service_label(identity_key: str) -> str:
        for row in state.rows:
            if row.identity_key == identity_key:
                return row.display_name or row.service_id
        return identity_key

    def _build_role_section(current: MCPServicesState) -> ft.Column:
        rows: list[ft.Control] = [
            ft.Text(I18n.get("mcp_role_desc"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT)
        ]
        if not current.roles_available:
            rows.append(
                ft.Text(
                    I18n.get("mcp_role_unavailable"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.WARNING,
                )
            )
            return ft.Column(rows, spacing=AppStyles.SPACING_XS)
        assignment_rows: list[ft.Control] = [
            ft.TextButton(
                f"{item.role_id}@{item.role_version}",
                icon=ft.Icons.EDIT,
                on_click=safe_on_click(_on_select_role(item.role_id, item.role_version)),
                disabled=current.busy,
            )
            for item in current.role_assignments
        ]
        if not assignment_rows:
            assignment_rows.append(
                ft.Text(
                    I18n.get("mcp_role_empty"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        rows.append(ft.Row(assignment_rows, spacing=AppStyles.SPACING_XS, wrap=True))
        draft = current.role_draft
        rows.append(
            ft.ResponsiveRow(
                [
                    ft.Container(
                        ft.TextField(
                            label=I18n.get("mcp_role_id"),
                            value=draft.role_id,
                            on_change=safe_on_change(_on_role_text("role_id")),
                            disabled=current.busy,
                            **_input_style(),
                        ),
                        col=AppStyles.COL_HALF,
                    ),
                    ft.Container(
                        ft.TextField(
                            label=I18n.get("mcp_role_version"),
                            value=draft.role_version,
                            keyboard_type=ft.KeyboardType.NUMBER,
                            on_change=safe_on_change(_on_role_text("role_version")),
                            disabled=current.busy,
                            **_input_style(),
                        ),
                        col=AppStyles.COL_HALF,
                    ),
                ],
                run_spacing=AppStyles.SPACING_SM,
            )
        )
        rows.append(
            ft.Text(I18n.get("mcp_role_available"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY)
        )
        rows.extend(
            ft.Checkbox(
                label=_service_label(row.identity_key),
                value=row.identity_key in draft.available_keys,
                on_change=safe_on_change(_on_toggle_role_service(row.identity_key)),
                disabled=current.busy,
                active_color=AppColors.PRIMARY,
            )
            for row in current.rows
        )
        default_options = [ft.dropdown.Option(key="", text=I18n.get("mcp_role_default_none"))]
        default_options.extend(ft.dropdown.Option(key=key, text=_service_label(key)) for key in draft.available_keys)
        rows.append(
            ft.Dropdown(
                label=I18n.get("mcp_role_default"),
                value=draft.default_key or "",
                options=default_options,
                on_select=safe_on_change(_on_role_default),
                disabled=current.busy,
                **_input_style(),
            )
        )
        tools_map = dict(draft.tools_text)
        for key in draft.available_keys:
            rows.append(
                ft.TextField(
                    label=I18n.get("mcp_role_tools_fmt").format(service=_service_label(key)),
                    value=tools_map.get(key, ""),
                    hint_text=I18n.get("mcp_role_tools_hint"),
                    multiline=True,
                    min_lines=2,
                    max_lines=6,
                    text_size=AppStyles.FONT_SIZE_BODY_SM,
                    on_change=safe_on_change(_on_role_tools(key)),
                    disabled=current.busy,
                    **_input_style(),
                )
            )
        rows.append(
            ft.Row(
                [
                    ft.TextButton(
                        I18n.get("mcp_role_new"),
                        icon=ft.Icons.ADD,
                        on_click=safe_on_click(_on_new_role),
                        disabled=current.busy,
                    ),
                    ft.Button(
                        content=I18n.get("mcp_role_save"),
                        icon=ft.Icons.SAVE,
                        on_click=safe_on_click(_on_save_role),
                        style=AppStyles.primary_button(),
                        disabled=current.busy,
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
        return ft.Column(rows, spacing=AppStyles.SPACING_XS)

    # -- 状态与按钮 ---------------------------------------------------------

    if not state.available:
        return ft.Container(
            content=ft.Column(
                [
                    DashboardCard(
                        content=ft.Column(
                            [
                                SectionHeader(I18n.get("mcp_panel_title"), title_key="mcp_panel_title"),
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
    # 列表刷新失败另列展示，不覆盖上面的操作回执。
    refresh_text = (
        I18n.get(state.refresh_status.key, **state.refresh_status.params) if state.refresh_status is not None else ""
    )
    refresh_row = (
        ft.Row(
            [
                ft.Icon(ft.Icons.WARNING_AMBER, size=AppStyles.FONT_SIZE_TITLE, color=AppColors.WARNING),
                ft.Text(refresh_text, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.WARNING),
            ],
            spacing=AppStyles.SPACING_XS,
        )
        if refresh_text
        else ft.Container()
    )

    btn_save = ft.Button(
        content=I18n.get("mcp_btn_save"),
        icon=ft.Icons.SAVE,
        on_click=safe_on_click(_on_save),
        style=AppStyles.primary_button(),
        disabled=state.busy,
    )
    btn_test = ft.OutlinedButton(
        content=I18n.get("mcp_btn_test"),
        icon=ft.Icons.NETWORK_CHECK,
        on_click=safe_on_click(_on_test),
        disabled=state.busy or not state.draft_saved,
    )
    btn_connect = ft.Button(
        content=I18n.get("mcp_btn_connect"),
        icon=ft.Icons.POWER,
        on_click=safe_on_click(_on_connect),
        disabled=state.busy or not state.draft_saved,
    )
    btn_disconnect = ft.OutlinedButton(
        content=I18n.get("mcp_btn_disconnect"),
        icon=ft.Icons.POWER_OFF,
        on_click=safe_on_click(_on_disconnect),
        disabled=state.busy,
    )
    btn_retry_close = ft.TextButton(
        content=I18n.get("mcp_btn_retry_close"),
        icon=ft.Icons.REFRESH,
        on_click=safe_on_click(_on_retry_residue),
        disabled=state.busy,
    )
    btn_delete = ft.OutlinedButton(
        content=I18n.get("mcp_btn_delete"),
        icon=ft.Icons.DELETE_OUTLINE,
        on_click=safe_on_click(_on_request_delete),
        disabled=state.busy or not state.draft_saved or state.delete_pending,
    )
    delete_confirm_row: ft.Control = (
        ft.Row(
            [
                ft.Text(
                    I18n.get("mcp_delete_confirm"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.WARNING,
                ),
                ft.Button(
                    content=I18n.get("mcp_delete_confirm_action"),
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=safe_on_click(_on_confirm_delete),
                    disabled=state.busy,
                ),
                ft.TextButton(
                    I18n.get("mcp_delete_cancel"),
                    on_click=safe_on_click(_on_cancel_delete),
                    disabled=state.busy,
                ),
            ],
            spacing=AppStyles.SPACING_XS,
            wrap=True,
        )
        if state.delete_pending
        else ft.Container()
    )

    return ft.Container(
        content=ft.Column(
            [
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_panel_title"), title_key="mcp_panel_title"),
                            ft.Text(
                                I18n.get("mcp_panel_desc"),
                                size=AppStyles.FONT_SIZE_BODY_SM,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                I18n.get("mcp_gate_hint"),
                                size=AppStyles.FONT_SIZE_CAPTION,
                                color=AppColors.TEXT_HINT,
                            ),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(
                                I18n.get("mcp_list_title"),
                                title_key="mcp_list_title",
                                action=ft.Row(
                                    [
                                        ft.TextButton(
                                            I18n.get("mcp_btn_refresh"),
                                            icon=ft.Icons.REFRESH,
                                            on_click=safe_on_click(_on_refresh),
                                            disabled=state.busy,
                                        ),
                                        ft.TextButton(
                                            I18n.get("mcp_btn_new"),
                                            icon=ft.Icons.ADD,
                                            on_click=safe_on_click(_on_new),
                                            disabled=state.busy,
                                        ),
                                    ],
                                    spacing=AppStyles.SPACING_XS,
                                ),
                            ),
                            _build_service_list(state),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_editor_title"), title_key="mcp_editor_title"),
                            _build_config_form(state.draft),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_budget_title"), title_key="mcp_budget_title"),
                            _build_budget_section(state.draft),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_audit_title"), title_key="mcp_audit_title"),
                            _build_audit_section(state.draft),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_role_title"), title_key="mcp_role_title"),
                            _build_role_section(state),
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("mcp_tools_title"), title_key="mcp_tools_title"),
                            _build_tools_section(state),
                            ft.Row(
                                [btn_save, btn_test, btn_connect, btn_disconnect, btn_retry_close, btn_delete],
                                spacing=AppStyles.SPACING_SM,
                                wrap=True,
                            ),
                            delete_confirm_row,
                            status_row,
                            refresh_row,
                        ],
                        spacing=AppStyles.SPACING_SM,
                    )
                ),
            ],
            spacing=AppStyles.SPACING_MD,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
