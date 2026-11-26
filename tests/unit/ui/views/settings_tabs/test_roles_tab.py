"""角色管理设置页签组件测试（Task 9）。

覆盖：
- 未接线（``catalog is None``）只渲染不可用说明且不构造 VM；
- 列表渲染预置 ``main`` / ``sub`` 与自定义角色（名称 / 版本 / 状态 / 模型继承）；
- 编辑保存提示词后出现升版本提示键，保存结果经 snackbar 呈现；
- 冲突错误键经 snackbar 呈现；
- 停用需二次确认，确认前不触达服务；
- 新 i18n 键在 View 中静态引用且在双语 locale 定义。

替身 ``_RecordingCatalog`` 复用真实 ``RoleCatalogService``（内存 ``RoleCatalogStore``），
仅记录调用并支持注入异常；断言精确调用列表，不使用 ``assert_called*``。
"""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.components.component import Component

from core.agent_types import AgentErrorCode, AgentServiceError, RoleDefinition
from services.agent_service.roles import RoleCatalogService, RoleCatalogStore
from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_render_effects,
)
from ui.viewmodels.roles_settings_view_model import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    RoleDraft,
    RoleRow,
    RolesSettingsState,
)
from ui.views.settings_tabs import roles_tab as mod

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 替身与测试基础设施
# ---------------------------------------------------------------------------


class _RecordingCatalog:
    """``RoleCatalogPort`` 替身：真实服务实现 + 调用记录 + 可注入异常。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.calls: list[tuple[object, ...]] = []
        self.raise_on: dict[str, BaseException] = {}
        self._service = RoleCatalogService(RoleCatalogStore(read=self._read, write_if=self._write_if, scan=self._scan))

    async def _read(self, key: str) -> str | None:
        return self._data.get(key)

    async def _write_if(self, key: str, expected: str | None, value: str) -> bool:
        if expected is None:
            if key in self._data:
                return False
            self._data[key] = value
            return True
        if self._data.get(key) != expected:
            return False
        self._data[key] = value
        return True

    async def _scan(self, prefix: str) -> dict[str, str]:
        return {key: value for key, value in self._data.items() if key.startswith(prefix)}

    async def seed(
        self,
        role_id: str,
        display_name: str = "示例角色",
        prompt_body: str = "示例正文",
        *,
        model_connection_id: str | None = None,
    ) -> None:
        await self._service.create_new(role_id, display_name, prompt_body, updated_by="seed")
        if model_connection_id is not None:
            await self._service.update(
                role_id,
                model_connection_id=model_connection_id,
                expected_version="r1",
                updated_by="seed",
            )

    async def seed_disabled(self, role_id: str, display_name: str, prompt_body: str) -> None:
        await self._service.create_new(role_id, display_name, prompt_body, updated_by="seed")
        await self._service.disable(role_id, expected_version="r1")

    def _maybe_raise(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    async def create_new(self, role_id: str, display_name: str, prompt_body: str, *, updated_by: str) -> RoleDefinition:
        self.calls.append(("create_new", role_id, display_name, prompt_body, updated_by))
        self._maybe_raise("create_new")
        return await self._service.create_new(role_id, display_name, prompt_body, updated_by=updated_by)

    async def update(
        self,
        role_id: str,
        *,
        display_name: str | None = None,
        prompt_body: str | None = None,
        model_connection_id: str | None = None,
        expected_version: str,
        updated_by: str,
    ) -> RoleDefinition:
        self.calls.append(
            ("update", role_id, display_name, prompt_body, model_connection_id, expected_version, updated_by)
        )
        self._maybe_raise("update")
        return await self._service.update(
            role_id,
            display_name=display_name,
            prompt_body=prompt_body,
            model_connection_id=model_connection_id,
            expected_version=expected_version,
            updated_by=updated_by,
        )

    async def disable(self, role_id: str, *, expected_version: str) -> None:
        self.calls.append(("disable", role_id, expected_version))
        self._maybe_raise("disable")
        await self._service.disable(role_id, expected_version=expected_version)

    async def enable(self, role_id: str, *, expected_version: str) -> None:
        self.calls.append(("enable", role_id, expected_version))
        self._maybe_raise("enable")
        await self._service.enable(role_id, expected_version=expected_version)

    async def list_roles(self) -> tuple[RoleDefinition, ...]:
        self.calls.append(("list_roles",))
        self._maybe_raise("list_roles")
        return await self._service.list_roles()

    async def resolve(self, role_id: str) -> RoleDefinition | None:
        self.calls.append(("resolve", role_id))
        self._maybe_raise("resolve")
        return await self._service.resolve(role_id)


class _TaskRecordingPage(FakePage):
    """记录 ``run_task`` 调度，供测试同步执行协程。"""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.tasks: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []

    def run_task(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.tasks.append((fn, args, kwargs))


class _FakeVm:
    """``_RolesList`` / ``_RoleEditor`` 消费的命令替身（记录精确调用）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def begin_edit(self, role_id: str) -> None:
        self.calls.append(("begin_edit", role_id))

    async def copy(self, role_id: str) -> None:
        self.calls.append(("copy", role_id))

    async def toggle_status(self, role_id: str) -> None:
        self.calls.append(("toggle_status", role_id))

    def set_role_id(self, value: str) -> None:
        self.calls.append(("set_role_id", value))

    def set_display_name(self, value: str) -> None:
        self.calls.append(("set_display_name", value))

    def set_prompt_body(self, value: str) -> None:
        self.calls.append(("set_prompt_body", value))

    def set_model_connection_id(self, value: str | None) -> None:
        self.calls.append(("set_model_connection_id", value))

    async def save(self) -> None:
        self.calls.append(("save",))

    def cancel_edit(self) -> None:
        self.calls.append(("cancel_edit",))


@pytest.fixture
def i18n_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """把 roles_tab 的 I18n 换成可控替身（返回 ``i18n[key]``）。"""

    mock = MagicMock()
    mock.get.side_effect = lambda key, *args, **kwargs: f"i18n[{key}]"
    monkeypatch.setattr(mod, "I18n", mock)
    return mock


def _drain(page: _TaskRecordingPage) -> None:
    """同步执行 page.run_task 调度的全部协程。"""

    while page.tasks:
        fn, args, kwargs = page.tasks.pop(0)
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            asyncio.run(result)


def _walk(root: Any) -> Iterator[Any]:
    """递归遍历 Control / Component 树（含组件 props 中的子组件）。

    说明：``Component._b`` 为 Flet 私有字段（记录已渲染体），本测试借助它访问
    子组件渲染结果；升级 Flet 时若该字段改名/移除，需同步调整此遍历器。
    """

    stack: list[Any] = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, Component):
            yield node
            stack.extend(node.args)
            stack.extend(node.kwargs.values())
            if node._b is not None:
                stack.append(node._b)
        elif isinstance(node, ft.Control):
            yield node
            for attr in ("content", "controls", "items", "tabs", "actions"):
                child = getattr(node, attr, None)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, (ft.Control, Component)):
                    stack.append(child)


def _texts(root: Any) -> set[str]:
    return {node.value for node in _walk(root) if isinstance(node, ft.Text) and isinstance(node.value, str)}


def _find_component(root: Any, fn: Any) -> Component:
    impl = getattr(fn, "__component_impl__", fn)
    for node in _walk(root):
        if isinstance(node, Component) and node.fn is impl:
            return node
    raise AssertionError(f"component {getattr(fn, '__name__', fn)} not found")


def _find_button(root: Any, label: str) -> ft.Button | ft.TextButton:
    for node in _walk(root):
        if isinstance(node, (ft.Button, ft.TextButton)) and getattr(node, "content", None) == label:
            return node
    raise AssertionError(f"button with content={label!r} not found")


def _find_textfield(root: Any, label: str) -> ft.TextField:
    for node in _walk(root):
        if isinstance(node, ft.TextField) and node.label == label:
            return node
    raise AssertionError(f"TextField with label={label!r} not found")


def _make_event(value: Any = None) -> MagicMock:
    event = MagicMock()
    event.control.value = value
    return event


def _invoke(handler: Any, *args: Any) -> None:
    handler(*args)


def _mount_body(catalog: _RecordingCatalog, *, drain: bool = True) -> tuple[Component, _TaskRecordingPage, MagicMock]:
    show_snack = MagicMock()
    component = make_component(mod._RolesTabBody, show_snack=show_snack, catalog=catalog)
    page = _TaskRecordingPage()
    run_mount_effects(component, page=page)
    if drain:
        _drain(page)
    return component, page, show_snack


def _snack_messages(show_snack: MagicMock) -> list[Any]:
    return [call.args[0] if call.args else None for call in show_snack.call_args_list]


def _render_list(component: Component) -> tuple[Component, Any]:
    body_result = render_once(component)
    list_component = _find_component(body_result, mod._RolesList)
    return list_component, render_once(list_component)


# ---------------------------------------------------------------------------
# 未接线 / 接线委派
# ---------------------------------------------------------------------------


def test_unwired_renders_explanation_without_constructing_vm(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[Any] = []

    class _RecorderVm:
        def __init__(self, catalog: Any) -> None:
            constructed.append(catalog)

    monkeypatch.setattr(mod, "RolesSettingsViewModel", _RecorderVm)

    show_snack = MagicMock()
    component = make_component(mod.RolesTab, show_snack=show_snack)
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    assert "i18n[agent_roles_not_wired]" in _texts(result)
    assert constructed == []


def test_wired_renders_body_component(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    catalog = _RecordingCatalog()
    component = make_component(mod.RolesTab, show_snack=MagicMock(), catalog=catalog)
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    assert isinstance(result, Component)
    assert result.fn is mod._RolesTabBody.__component_impl__


# ---------------------------------------------------------------------------
# 列表渲染 (真实 RoleCatalogService + 内存存储)
# ---------------------------------------------------------------------------


def test_list_renders_presets_and_custom_roles(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    catalog = _RecordingCatalog()
    asyncio.run(catalog.seed("risk_reviewer", "风险复核", "只核对风险。", model_connection_id="conn-1"))
    asyncio.run(catalog.seed_disabled("news_editor", "新闻编辑", "只写摘要。"))

    body, _page, _snack = _mount_body(catalog)
    _list_component, list_view = _render_list(body)
    texts = _texts(list_view)

    assert "i18n[agent_roles_preset_main]" in texts
    assert "i18n[agent_roles_preset_sub]" in texts
    assert "i18n[agent_roles_preset_tag]" in texts
    assert "风险复核" in texts
    assert "新闻编辑" in texts
    assert "r1" in texts
    assert "conn-1" in texts
    assert "i18n[agent_roles_status_active]" in texts
    assert "i18n[agent_roles_status_disabled]" in texts
    assert "i18n[agent_roles_model_inherit]" in texts


def test_list_shows_empty_hint_when_no_custom_roles(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    catalog = _RecordingCatalog()
    body, _page, _snack = _mount_body(catalog)
    body_result = render_once(body)

    assert "i18n[agent_roles_empty]" in _texts(body_result)
    assert "i18n[agent_roles_loading]" not in _texts(body_result)


def test_first_frame_shows_loading_instead_of_empty(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    """首帧加载未完成时不得渲染成空列表（R21：不把未知伪装成空）。"""

    catalog = _RecordingCatalog()
    body, _page, _snack = _mount_body(catalog, drain=False)

    texts = _texts(render_once(body))
    assert "i18n[agent_roles_loading]" in texts
    assert "i18n[agent_roles_empty]" not in texts


def test_load_failure_shows_persistent_unavailable_instead_of_empty(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    """加载失败后列表区持久显示不可用，而不是空列表。"""

    catalog = _RecordingCatalog()
    catalog.raise_on["list_roles"] = AgentServiceError(
        AgentErrorCode.CONFIG_UNAVAILABLE, "role_catalog_unavailable", "role_catalog"
    )
    body, _page, _snack = _mount_body(catalog)

    texts = _texts(render_once(body))
    assert "i18n[agent_roles_unavailable]" in texts
    assert "i18n[agent_roles_empty]" not in texts
    assert "i18n[agent_roles_loading]" not in texts


# ---------------------------------------------------------------------------
# 停用二次确认
# ---------------------------------------------------------------------------


def test_disable_requires_confirmation_before_touching_service(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    state = RolesSettingsState(
        roles=(
            RoleRow("main", True, None, STATUS_ACTIVE, None, None),
            RoleRow("risk_reviewer", False, "风险复核", STATUS_ACTIVE, "r1", None),
        )
    )
    vm = _FakeVm()
    component = make_component(mod._RolesList, state=state, vm=vm)
    page = _TaskRecordingPage()
    run_mount_effects(component, page=page)
    result = render_once(component)

    _invoke(_find_button(result, "i18n[agent_roles_disable]").on_click, _make_event())

    assert page.tasks == []
    assert vm.calls == []

    rerendered = render_once(component)
    _find_button(rerendered, "i18n[agent_roles_disable]")
    dialogs = page._dialogs.controls
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert isinstance(dialog, ft.AlertDialog)
    assert "i18n[agent_roles_confirm_disable_title]" in _texts(dialog.title)
    assert "i18n[agent_roles_confirm_disable_body]" in _texts(dialog.content)

    confirm_buttons = [
        action
        for action in dialog.actions
        if isinstance(action, (ft.Button, ft.TextButton)) and action.content == "i18n[common_confirm]"
    ]
    assert len(confirm_buttons) == 1
    _invoke(confirm_buttons[0].on_click, _make_event())
    _drain(page)

    assert vm.calls == [("toggle_status", "risk_reviewer")]


def test_preset_row_only_exposes_copy_action(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    state = RolesSettingsState(
        roles=(
            RoleRow("main", True, None, STATUS_ACTIVE, None, None),
            RoleRow("sub", True, None, STATUS_ACTIVE, None, None),
        )
    )
    component = make_component(mod._RolesList, state=state, vm=_FakeVm())
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    edit_buttons = [
        node for node in _walk(result) if isinstance(node, ft.TextButton) and node.content == "i18n[agent_roles_edit]"
    ]
    toggle_buttons = [
        node
        for node in _walk(result)
        if isinstance(node, ft.TextButton) and node.content in ("i18n[agent_roles_disable]", "i18n[agent_roles_enable]")
    ]
    copy_buttons = [
        node for node in _walk(result) if isinstance(node, ft.TextButton) and node.content == "i18n[agent_roles_copy]"
    ]

    assert edit_buttons == []
    assert toggle_buttons == []
    assert len(copy_buttons) == 2


def test_unknown_status_is_not_disguised_as_active(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    state = RolesSettingsState(roles=(RoleRow("risk_reviewer", False, "风险复核", "archived", "r1", None),))
    component = make_component(mod._RolesList, state=state, vm=_FakeVm())
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    texts = _texts(result)
    assert "archived" in texts
    assert "i18n[agent_roles_status_active]" not in texts


def test_enable_requires_confirmation_and_calls_toggle(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    state = RolesSettingsState(roles=(RoleRow("news_editor", False, "新闻编辑", STATUS_DISABLED, "r1", None),))
    vm = _FakeVm()
    component = make_component(mod._RolesList, state=state, vm=vm)
    page = _TaskRecordingPage()
    run_mount_effects(component, page=page)
    result = render_once(component)

    _invoke(_find_button(result, "i18n[agent_roles_enable]").on_click, _make_event())
    assert page.tasks == []
    assert vm.calls == []

    rerendered = render_once(component)
    _find_button(rerendered, "i18n[agent_roles_enable]")
    dialogs = page._dialogs.controls
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert isinstance(dialog, ft.AlertDialog)
    assert "i18n[agent_roles_confirm_enable_title]" in _texts(dialog.title)
    assert "i18n[agent_roles_confirm_enable_body]" in _texts(dialog.content)

    confirm_buttons = [
        action
        for action in dialog.actions
        if isinstance(action, (ft.Button, ft.TextButton)) and action.content == "i18n[common_confirm]"
    ]
    assert len(confirm_buttons) == 1
    _invoke(confirm_buttons[0].on_click, _make_event())
    _drain(page)

    assert vm.calls == [("toggle_status", "news_editor")]


# ---------------------------------------------------------------------------
# 编辑器
# ---------------------------------------------------------------------------


def _existing_draft() -> RoleDraft:
    return RoleDraft(
        role_id="risk_reviewer",
        display_name="风险复核",
        prompt_body="旧正文",
        model_connection_id=None,
        role_version="r1",
        is_new=False,
        copy_from=None,
    )


def test_editor_role_id_is_readonly_for_existing_role(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    component = make_component(mod._RoleEditor, draft=_existing_draft(), vm=_FakeVm(), pending=False)
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    role_id_field = _find_textfield(result, "i18n[agent_roles_role_id_label]")
    assert role_id_field.disabled is True
    assert role_id_field.value == "risk_reviewer"


def test_editor_version_bump_hint_after_prompt_change(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    vm = _FakeVm()
    component = make_component(mod._RoleEditor, draft=_existing_draft(), vm=vm, pending=False)
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    assert "i18n[agent_roles_version_bump_hint]" not in _texts(result)

    prompt_field = _find_textfield(result, "i18n[agent_roles_prompt_label]")
    _invoke(prompt_field.on_change, _make_event("新正文"))
    assert vm.calls == [("set_prompt_body", "新正文")]

    component.kwargs["draft"] = replace(_existing_draft(), prompt_body="新正文")
    rerendered = render_once(component)

    assert "i18n[agent_roles_version_bump_hint]" in _texts(rerendered)


def test_editor_save_and_cancel_schedule_commands(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    vm = _FakeVm()
    component = make_component(mod._RoleEditor, draft=_existing_draft(), vm=vm, pending=False)
    page = _TaskRecordingPage()
    run_mount_effects(component, page=page)
    result = render_once(component)

    _invoke(_find_button(result, "i18n[agent_roles_save]").on_click, _make_event())
    _drain(page)
    assert ("save",) in vm.calls

    _invoke(_find_button(result, "i18n[agent_roles_cancel]").on_click, _make_event())
    assert ("cancel_edit",) in vm.calls


def test_editor_buttons_disabled_while_pending(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    """提交中禁用保存/取消，避免双击派发第二次 save（误导性冲突/非法提示）。"""

    component = make_component(mod._RoleEditor, draft=_existing_draft(), vm=_FakeVm(), pending=True)
    run_mount_effects(component, page=_TaskRecordingPage())
    result = render_once(component)

    assert _find_button(result, "i18n[agent_roles_save]").disabled is True
    assert _find_button(result, "i18n[agent_roles_cancel]").disabled is True


# ---------------------------------------------------------------------------
# 保存 / 冲突 snackbar（端到端：真实 VM + 真实服务 + 视图组件）
# ---------------------------------------------------------------------------


def _begin_edit_from_list(body: Component, page: _TaskRecordingPage) -> Any:
    _list_component, list_view = _render_list(body)
    _invoke(_find_button(list_view, "i18n[agent_roles_edit]").on_click, _make_event())
    _drain(page)
    body_result = render_once(body)
    editor_component = _find_component(body_result, mod._RoleEditor)
    return render_once(editor_component)


def test_save_prompt_change_surfaces_version_bumped_notice(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    catalog = _RecordingCatalog()
    asyncio.run(catalog.seed("risk_reviewer", "风险复核", "旧正文"))
    body, page, show_snack = _mount_body(catalog)

    editor_view = _begin_edit_from_list(body, page)
    prompt_field = _find_textfield(editor_view, "i18n[agent_roles_prompt_label]")
    _invoke(prompt_field.on_change, _make_event("新正文"))
    _invoke(_find_button(editor_view, "i18n[agent_roles_save]").on_click, _make_event())
    _drain(page)
    run_render_effects(body)

    assert _snack_messages(show_snack) == ["i18n[agent_roles_version_bumped]"]


def test_save_with_blank_model_inherits_and_passes_none(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    """模型连接留空保存时，服务调用参数为 ``None``（继承路由默认）。"""

    catalog = _RecordingCatalog()
    asyncio.run(catalog.seed("risk_reviewer", "风险复核", "旧正文", model_connection_id="conn-1"))
    body, page, _snack = _mount_body(catalog)

    editor_view = _begin_edit_from_list(body, page)
    model_field = _find_textfield(editor_view, "i18n[agent_roles_model_label]")
    _invoke(model_field.on_change, _make_event(""))
    _invoke(_find_button(editor_view, "i18n[agent_roles_save]").on_click, _make_event())
    _drain(page)

    updates = [call for call in catalog.calls if call[0] == "update"]
    assert updates, "应发生一次角色更新"
    # ("update", role_id, display_name, prompt_body, model_connection_id, expected_version, updated_by)
    assert updates[-1][4] is None


def test_save_conflict_surfaces_conflict_notice(
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    i18n_mock: MagicMock,
) -> None:
    catalog = _RecordingCatalog()
    asyncio.run(catalog.seed("risk_reviewer", "风险复核", "旧正文"))
    body, page, show_snack = _mount_body(catalog)

    editor_view = _begin_edit_from_list(body, page)
    catalog.raise_on["update"] = AgentServiceError(
        AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", "risk_reviewer"
    )
    _invoke(_find_button(editor_view, "i18n[agent_roles_save]").on_click, _make_event())
    _drain(page)
    run_render_effects(body)

    assert _snack_messages(show_snack) == ["i18n[agent_roles_conflict]"]


# ---------------------------------------------------------------------------
# i18n 键静态引用与双语存在性
# ---------------------------------------------------------------------------

_NEW_I18N_KEYS: frozenset[str] = frozenset(
    {
        "settings_tab_roles",
        "agent_roles_title",
        "agent_roles_new",
        "agent_roles_copy",
        "agent_roles_edit",
        "agent_roles_disable",
        "agent_roles_enable",
        "agent_roles_status_active",
        "agent_roles_status_disabled",
        "agent_roles_model_inherit",
        "agent_roles_version_label",
        "agent_roles_prompt_label",
        "agent_roles_role_id_label",
        "agent_roles_display_name_label",
        "agent_roles_model_label",
        "agent_roles_confirm_disable_title",
        "agent_roles_confirm_disable_body",
        "agent_roles_confirm_enable_title",
        "agent_roles_confirm_enable_body",
        "agent_roles_version_bump_hint",
        "agent_roles_not_wired",
        "agent_roles_empty",
        "agent_roles_preset_tag",
        "agent_roles_save",
        "agent_roles_cancel",
        "agent_roles_unavailable",
        "agent_roles_preset_readonly",
        "agent_roles_conflict",
        "agent_roles_invalid",
        "agent_roles_save_failed",
        "agent_roles_created",
        "agent_roles_saved",
        "agent_roles_version_bumped",
        "agent_roles_disabled",
        "agent_roles_enabled",
        "agent_roles_preset_main",
        "agent_roles_preset_sub",
        "agent_roles_list_title",
        "agent_roles_loading",
    }
)


def test_new_i18n_keys_are_referenced_in_view_source() -> None:
    """静态字面量引用：AST 死键门禁可检出（禁止只靠 ``I18n.get(state.error_key)``）。"""

    view_dir = Path(mod.__file__).resolve().parents[1]
    literals: set[Any] = set()
    for path in (Path(mod.__file__), view_dir / "settings_view.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals.update(
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    assert literals >= _NEW_I18N_KEYS


def test_new_i18n_keys_exist_in_both_locales() -> None:
    root = Path(__file__).resolve().parents[5]
    for locale in ("zh_CN", "en_US"):
        data = json.loads((root / "locales" / locale / "strings.json").read_text(encoding="utf-8"))
        missing = sorted(_NEW_I18N_KEYS - set(data))
        assert missing == [], f"{locale} missing keys: {missing}"
