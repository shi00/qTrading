"""MCP 服务设置页签渲染测试（R19：View 层分支与命令接线覆盖）。

覆盖点：
- 未接线 / 未加载 / 空列表 / 有行 四种列表状态；
- stdio 与 HTTP 两种传输表单分支（含 SSE/HTTP 风险开关）；
- 工具勾选区（空/已发现、已核准子集、busy 禁用）；
- 状态行按 level 取语义色 token；
- 表单/按钮事件接线：文本、预算（按索引）、布尔、传输、选服务、勾工具；
- 挂载 effect 只调度 ``vm.load``，渲染本身不建立连接。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.components.component import Component

import ui.views.settings_tabs.mcp_services_tab as tab
from core.i18n import Message
from tests.unit.ui.component_renderer import FakePage, attach_fake_page, make_component, render_once
from ui.i18n import I18n
from ui.theme import AppColors
from ui.viewmodels.mcp_services_view_model import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_WARNING,
    MCPConfigDraft,
    MCPServiceRow,
    MCPServicesState,
)

pytestmark = pytest.mark.unit

_CHILD_ATTRS = ("controls", "content", "action", "title", "subtitle", "cells", "rows")


class _TaskPage(FakePage):
    """记录 ``run_task`` 调度的协程工厂（不实际执行异步命令）。"""

    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[Any] = []

    def run_task(self, handler: Any, *args: Any, **kwargs: Any) -> None:
        self.tasks.append((handler, args, kwargs))


def _state(**overrides: Any) -> MCPServicesState:
    base: dict[str, Any] = {"available": True, "loaded": True, "draft": MCPConfigDraft()}
    base.update(overrides)
    return MCPServicesState(**base)


def _vm() -> MagicMock:
    return MagicMock()


def _materialize(node: Any, page: FakePage) -> Any:
    """把嵌套 ``@ft.component`` 占位递归渲染为真实控件树（单测无 Renderer 级联）。"""

    if isinstance(node, Component):
        child = make_component(node.fn, *node.args, **node.kwargs)
        attach_fake_page(child, page)
        return _materialize(render_once(child), page)
    if isinstance(node, ft.Control):
        for attr in _CHILD_ATTRS:
            value = getattr(node, attr, None)
            if isinstance(value, list):
                setattr(node, attr, [_materialize(item, page) for item in value])
            elif value is not None:
                setattr(node, attr, _materialize(value, page))
    return node


def _render(monkeypatch: pytest.MonkeyPatch, state: MCPServicesState) -> tuple[Any, MagicMock, _TaskPage]:
    vm = _vm()
    monkeypatch.setattr(tab, "use_viewmodel", lambda factory: (state, vm))  # noqa: ARG005
    component = make_component(tab.MCPServicesTab)
    page = _TaskPage()
    attach_fake_page(component, page)
    view = _materialize(render_once(component), page)
    component._run_mount_effects()
    return view, vm, page


def _walk(node: Any) -> list[Any]:
    """递归收集控件（覆盖 Column/Row/Container/DataTable/DashboardCard 等容器）。"""

    found: list[Any] = []
    if isinstance(node, ft.Control):
        found.append(node)
    if isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_walk(item))
        return found
    for attr in _CHILD_ATTRS:
        child = getattr(node, attr, None)
        if child is not None:
            found.extend(_walk(child))
    return found


def _of(view: Any, kind: type) -> list[Any]:
    return [control for control in _walk(view) if isinstance(control, kind)]


def _row(identity_key: str = "svc", *, connected: bool) -> MCPServiceRow:
    return MCPServiceRow(
        identity_key=identity_key,
        service_id=identity_key,
        config_version=1,
        display_name=f"{identity_key}-name",
        transport_kind="stdio",
        connected=connected,
    )


class TestListStates:
    def test_unavailable_renders_reason_without_actions(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(available=False))

        texts = [control.value for control in _of(view, ft.Text)]
        assert I18n.get("mcp_unavailable") in texts
        assert _of(view, ft.Button) == []
        assert _of(view, ft.OutlinedButton) == []

    def test_not_loaded_and_empty_list_are_distinct(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(loaded=False))
        assert I18n.get("mcp_not_loaded") in [control.value for control in _of(view, ft.Text)]

        view, _vm, _page = _render(monkeypatch, _state(loaded=True, rows=()))
        texts = [control.value for control in _of(view, ft.Text)]
        assert I18n.get("mcp_list_empty") in texts
        assert I18n.get("mcp_not_loaded") not in texts

    def test_rows_render_connection_state(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        rows = (_row("svc-a", connected=True), _row("svc-b", connected=False))
        view, _vm, _page = _render(monkeypatch, _state(rows=rows))

        table = _of(view, ft.DataTable)[0]
        assert len(table.rows) == 2
        status_texts = [
            cell.content.value for row in table.rows for cell in row.cells if isinstance(cell.content, ft.Text)
        ]
        assert I18n.get("mcp_state_connected") in status_texts
        assert I18n.get("mcp_state_disconnected") in status_texts
        assert len(_of(view, ft.TextButton)) == 2 + 3  # 选服务按钮 + 刷新/新建/重试关闭残留


class TestConfigForm:
    def test_stdio_form_shows_local_fields_only(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(draft=MCPConfigDraft(transport_kind="stdio")))

        labels = [control.label for control in _of(view, ft.TextField)]
        assert I18n.get("mcp_field_command") in labels
        assert I18n.get("mcp_field_cwd") in labels
        assert I18n.get("mcp_field_url") not in labels
        assert _of(view, ft.Switch) == []

    def test_http_form_shows_remote_fields_and_flags(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        draft = MCPConfigDraft(transport_kind="streamable_http", url="https://example.test/mcp")
        view, _vm, _page = _render(monkeypatch, _state(draft=draft))

        labels = [control.label for control in _of(view, ft.TextField)]
        assert I18n.get("mcp_field_url") in labels
        assert I18n.get("mcp_field_command") not in labels
        assert len(_of(view, ft.Switch)) == 2

    def test_budget_fields_follow_single_source_order(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        draft = MCPConfigDraft(budget_values=tuple(str(index) for index in range(8)))
        view, _vm, _page = _render(monkeypatch, _state(draft=draft))

        budget_labels = [I18n.get(key) for key in tab._BUDGET_LABEL_KEYS.values()]
        rendered = [control for control in _of(view, ft.TextField) if control.label in budget_labels]
        assert len(rendered) == len(tab._BUDGET_LABEL_KEYS)
        assert [control.value for control in rendered] == list(draft.budget_values)


class TestToolsAndActions:
    def test_tools_empty_hint(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(discovered_tools=()))
        assert I18n.get("mcp_tools_empty") in [control.value for control in _of(view, ft.Text)]
        assert _of(view, ft.Checkbox) == []

    def test_tools_checkbox_reflects_approved_subset(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        state = _state(discovered_tools=("read", "write"), approved_tools=("read",))
        view, _vm, _page = _render(monkeypatch, state)

        checkboxes = {box.label: box.value for box in _of(view, ft.Checkbox)}
        assert checkboxes == {"read": True, "write": False}

    def test_busy_disables_buttons_and_fields(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(busy=True, draft_saved=True))

        assert all(button.disabled for button in _of(view, ft.Button))
        assert all(button.disabled for button in _of(view, ft.OutlinedButton))
        assert all(field.disabled for field in _of(view, ft.TextField))
        assert all(switch.disabled for switch in _of(view, ft.Switch))

    def test_test_and_connect_require_saved_draft(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(draft_saved=False, busy=False))

        by_content = {str(button.content): button for button in _of(view, ft.Button) + _of(view, ft.OutlinedButton)}
        assert by_content[I18n.get("mcp_btn_test")].disabled is True
        assert by_content[I18n.get("mcp_btn_connect")].disabled is True
        assert by_content[I18n.get("mcp_btn_disconnect")].disabled is False

    def test_async_buttons_schedule_through_run_task(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, vm, page = _render(monkeypatch, _state(draft_saved=True))

        buttons = {
            str(control.content): control
            for control in _of(view, ft.Button) + _of(view, ft.OutlinedButton) + _of(view, ft.TextButton)
        }
        for label in ("mcp_btn_save", "mcp_btn_test", "mcp_btn_connect", "mcp_btn_disconnect", "mcp_btn_refresh"):
            buttons[I18n.get(label)].on_click(SimpleNamespace(control=None))

        scheduled = [handler for handler, _args, _kwargs in page.tasks]
        assert vm.load in scheduled
        assert vm.save_config in scheduled
        assert vm.connection_test in scheduled
        assert vm.connect in scheduled
        assert vm.disconnect in scheduled

        buttons[I18n.get("mcp_btn_new")].on_click(SimpleNamespace(control=None))
        vm.begin_new.assert_called_once_with()

    def test_status_absent_renders_placeholder_without_icon(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(status=None))

        icons = [icon for icon in _of(view, ft.Icon) if icon.icon == ft.Icons.INFO_OUTLINE]
        assert icons == []
        texts = [control.value for control in _of(view, ft.Text)]
        assert I18n.get("mcp_status_saved") not in texts

    def test_unknown_status_level_falls_back_to_secondary_token(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, _vm, _page = _render(monkeypatch, _state(status=Message("mcp_status_saved"), status_level="info"))

        icons = [icon for icon in _of(view, ft.Icon) if icon.icon == ft.Icons.INFO_OUTLINE]
        assert icons[0].color == AppColors.TEXT_SECONDARY

    def test_status_row_uses_level_token(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        for level, token in (
            (STATUS_SUCCESS, AppColors.SUCCESS),
            (STATUS_WARNING, AppColors.WARNING),
            (STATUS_ERROR, AppColors.ERROR),
        ):
            view, _vm, _page = _render(monkeypatch, _state(status=Message("mcp_status_saved"), status_level=level))
            icons = _of(view, ft.Icon)
            assert icons and icons[0].color == token


class TestFieldWiring:
    def test_text_field_change_updates_vm(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, vm, _page = _render(monkeypatch, _state())

        field = next(control for control in _of(view, ft.TextField) if control.label == I18n.get("mcp_field_command"))
        field.on_change(SimpleNamespace(control=ft.TextField(value="/usr/bin/mcp")))

        vm.set_draft_text.assert_called_with("command", "/usr/bin/mcp")

    def test_budget_field_change_uses_index(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, vm, _page = _render(monkeypatch, _state())

        budget_labels = [I18n.get(key) for key in tab._BUDGET_LABEL_KEYS.values()]
        fields = [control for control in _of(view, ft.TextField) if control.label in budget_labels]
        for field in fields:
            field.on_change(SimpleNamespace(control=ft.TextField(value="1024")))

        vm.set_budget_value.assert_any_call(0, "1024")
        vm.set_budget_value.assert_any_call(len(fields) - 1, "1024")
        assert vm.set_budget_value.call_count == len(fields)

    def test_string_number_field_change_uses_set_draft_text(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, vm, _page = _render(monkeypatch, _state())

        field = next(
            control for control in _of(view, ft.TextField) if control.label == I18n.get("mcp_field_config_version")
        )
        field.on_change(SimpleNamespace(control=ft.TextField(value="2")))

        vm.set_draft_text.assert_called_with("config_version", "2")

    def test_flag_and_transport_change_update_vm(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        view, vm, _page = _render(monkeypatch, _state(draft=MCPConfigDraft(transport_kind="streamable_http")))
        vm.reset_mock()

        switches = _of(view, ft.Switch)
        assert len(switches) == 2  # http 表单的 SSE / HTTP 风险开关
        for switch in switches:
            switch.on_change(SimpleNamespace(control=ft.Switch(value=True)))
        assert vm.set_draft_flag.call_count == 2

        dropdown = _of(view, ft.Dropdown)[0]
        dropdown.on_select(SimpleNamespace(control=ft.Dropdown(value="streamable_http")))
        vm.set_draft_text.assert_called_with("transport_kind", "streamable_http")

    def test_select_and_toggle_tool_call_vm(
        self, monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        state = _state(rows=(_row("svc-a", connected=False),), discovered_tools=("read",))
        view, vm, _page = _render(monkeypatch, state)
        vm.reset_mock()

        select_buttons = [
            button for button in _of(view, ft.TextButton) if str(button.content) == I18n.get("mcp_btn_select")
        ]
        select_buttons[0].on_click(SimpleNamespace(control=None))
        vm.select_service.assert_called_once_with("svc-a")

        _of(view, ft.Checkbox)[0].on_change(SimpleNamespace(control=ft.Checkbox(value=True)))
        vm.toggle_approved_tool.assert_called_once_with("read")


def test_mount_effect_schedules_single_load(
    monkeypatch: pytest.MonkeyPatch, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """挂载只调度读取，不触发任何连接动作。"""

    _view, vm, page = _render(monkeypatch, _state())

    scheduled = [handler for handler, _args, _kwargs in page.tasks]
    assert scheduled == [vm.load]
    vm.connect.assert_not_called()
    vm.connection_test.assert_not_called()
