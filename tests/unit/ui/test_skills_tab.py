"""Skills 设置页签渲染测试（W-05：View 分支与命令接线 + i18n 双语 key）。

覆盖：未接线提示、已加载列表、预览区、角色区、按钮/输入事件只调度命令（不直接执行）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.components.component import Component

import ui.views.settings_tabs.skills_tab as tab
from tests.unit.ui.component_renderer import FakePage, attach_fake_page, make_component, render_once
from ui.viewmodels import Message
from ui.viewmodels.skills_settings_view_model import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_SUCCESS,
    STATUS_WARNING,
    SkillImportPreview,
    SkillRow,
    SkillVersionRow,
    SkillsSettingsState,
)

pytestmark = pytest.mark.unit

_CHILD_ATTRS = ("controls", "content", "action", "title", "subtitle", "cells", "rows")


class _TaskPage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[Any] = []

    def run_task(self, handler: Any, *args: Any, **kwargs: Any) -> None:
        self.tasks.append((handler, args, kwargs))


def _state(**overrides: Any) -> SkillsSettingsState:
    base: dict[str, Any] = {"available": True, "loaded": True}
    base.update(overrides)
    return SkillsSettingsState(**base)


def _materialize(node: Any, page: FakePage) -> Any:
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


def _render(monkeypatch: pytest.MonkeyPatch, state: SkillsSettingsState) -> tuple[Any, MagicMock, _TaskPage]:
    vm = MagicMock()
    monkeypatch.setattr(tab, "use_viewmodel", lambda factory: (state, vm))  # noqa: ARG005
    monkeypatch.setattr(tab, "SectionHeader", lambda title, **kwargs: ft.Text(str(title)))  # noqa: ARG005
    monkeypatch.setattr(tab, "DashboardCard", lambda content, **kwargs: content)  # noqa: ARG005
    component = make_component(tab.SkillsTab)
    page = _TaskPage()
    attach_fake_page(component, page)
    view = _materialize(render_once(component), page)
    component._run_mount_effects()
    return view, vm, page


def _walk(node: Any) -> list[Any]:
    result: list[Any] = []
    if node is None:
        return result
    result.append(node)
    for attr in _CHILD_ATTRS:
        value = getattr(node, attr, None)
        if isinstance(value, list):
            for item in value:
                result.extend(_walk(item))
        elif value is not None:
            result.extend(_walk(value))
    return result


def _texts(view: Any) -> list[str]:
    return [node.value for node in _walk(view) if isinstance(node, ft.Text) and isinstance(node.value, str)]


def _fire_events(view: Any) -> None:
    """触发渲染树上的全部输入/点击回调（值先行填充，模拟用户操作）。"""

    for node in _walk(view):
        if isinstance(node, (ft.TextField, ft.Dropdown)):
            if isinstance(node, ft.Dropdown):
                node.value = tab.IMPORT_KIND_DIRECTORY
            elif node.label == tab.I18n.get("skills_field_path"):
                node.value = "C:/skills/demo"
            elif node.label == tab.I18n.get("skills_field_role_id"):
                node.value = "role-main"
            elif node.label == tab.I18n.get("skills_field_role_version"):
                node.value = "1"
        for attr in ("on_click", "on_change", "on_select"):
            callback = getattr(node, attr, None)
            if callable(callback):
                cast(Any, callback)(SimpleNamespace(control=node))


def test_unavailable_state_shows_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    view, _, _ = _render(monkeypatch, _state(available=False))
    assert tab.I18n.get("skills_unavailable") in _texts(view)


def test_empty_and_not_loaded_states(monkeypatch: pytest.MonkeyPatch) -> None:
    empty, _, _ = _render(monkeypatch, _state(rows=()))
    assert tab.I18n.get("skills_list_empty") in _texts(empty)
    pending, _, _ = _render(monkeypatch, _state(loaded=False))
    assert tab.I18n.get("skills_not_loaded") in _texts(pending)


def test_loaded_rows_and_preview_are_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        SkillRow(
            skill_id="demo",
            display_name="Demo",
            versions=(
                SkillVersionRow(1, "ready", 3, 2048, ()),
                SkillVersionRow(2, "disabled", 4, 3 * 1024**3, ()),
                SkillVersionRow(3, "unavailable", 1, 512, ("dep",)),
                SkillVersionRow(4, "mystery", 1, 0, ()),
            ),
        ),
    )
    preview = SkillImportPreview(
        status="update_requires_confirmation",
        display_name="Demo",
        skill_id="demo",
        version=2,
        file_count=3,
        expanded_bytes=1024,
        license_id="MIT",
        dependencies=("base",),
        missing_dependencies=("absent",),
        requires_confirmation=True,
        source_kind="zip",
    )
    view, _, _ = _render(monkeypatch, _state(rows=rows, preview=preview))
    texts = _texts(view)
    assert "Demo" in texts
    assert tab.I18n.get("skills_preview_requires_confirm") in texts
    assert tab.I18n.get("skills_state_ready") in texts
    assert tab.I18n.get("skills_state_unknown") in texts


def test_mount_effect_schedules_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _, vm, page = _render(monkeypatch, _state())
    assert any(handler == vm.load for handler, _args, _kwargs in page.tasks)


def test_all_callbacks_schedule_viewmodel_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (SkillRow(skill_id="demo", display_name="Demo", versions=(SkillVersionRow(1, "ready", 3, 1024, ()),)),)
    preview = SkillImportPreview(
        status="new",
        display_name="Demo",
        skill_id="demo",
        version=2,
        file_count=1,
        expanded_bytes=1,
        license_id="MIT",
        dependencies=(),
        missing_dependencies=(),
        requires_confirmation=False,
        source_kind="directory",
    )
    view, vm, page = _render(monkeypatch, _state(rows=rows, preview=preview))
    _fire_events(view)
    scheduled = [handler for handler, _args, _kwargs in page.tasks]
    assert vm.load in scheduled
    assert vm.preview_import in scheduled
    assert len(scheduled) >= 7


@pytest.mark.parametrize(
    ("level", "status"),
    [
        (STATUS_INFO, Message("skills_status_role_saved")),
        (STATUS_SUCCESS, Message("skills_status_role_saved")),
        (STATUS_WARNING, Message("skills_status_role_saved")),
        (STATUS_ERROR, Message("skills_status_error", {"reason": "x", "code": "y"})),
    ],
)
def test_status_levels_render(monkeypatch: pytest.MonkeyPatch, level: str, status: Message) -> None:
    view, _, _ = _render(monkeypatch, _state(status=status, status_level=level))
    assert tab.I18n.get(status.key, **status.params) in _texts(view)


def test_role_and_import_fields_reflect_state(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (SkillRow(skill_id="demo", display_name="Demo", versions=(SkillVersionRow(1, "ready", 3, 1024, ()),)),)
    state = _state(
        rows=rows,
        role_id="role-main",
        role_version="1",
        role_enabled=("demo@1",),
        role_defaults=("demo@1",),
        role_effective=("demo@1",),
        import_path="C:/skills/demo",
        import_kind=tab.IMPORT_KIND_DIRECTORY,
    )
    view, _, _ = _render(monkeypatch, state)
    field_values = [node.value for node in _walk(view) if isinstance(node, (ft.TextField, ft.Dropdown)) and node.value]
    assert "role-main" in field_values
    assert "C:/skills/demo" in field_values
    assert tab.I18n.get("skills_role_effective", values="demo@1") in _texts(view)


def _locale_keys(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8")))


def test_skills_i18n_keys_exist_in_both_locales() -> None:
    root = Path(__file__).resolve().parents[3]
    zh = _locale_keys(root / "locales" / "zh_CN" / "strings.json")
    en = _locale_keys(root / "locales" / "en_US" / "strings.json")
    required = {
        "settings_tab_skills",
        "skills_panel_title",
        "skills_panel_desc",
        "skills_list_title",
        "skills_list_empty",
        "skills_not_loaded",
        "skills_col_name",
        "skills_col_id",
        "skills_col_version",
        "skills_col_state",
        "skills_col_files",
        "skills_col_size",
        "skills_state_ready",
        "skills_state_disabled",
        "skills_state_unavailable",
        "skills_state_unknown",
        "skills_btn_refresh",
        "skills_btn_enable",
        "skills_btn_disable",
        "skills_btn_uninstall",
        "skills_import_title",
        "skills_import_desc",
        "skills_field_path",
        "skills_field_kind",
        "skills_kind_zip",
        "skills_kind_directory",
        "skills_btn_preview",
        "skills_btn_confirm",
        "skills_preview_none",
        "skills_preview_summary",
        "skills_preview_dependencies",
        "skills_preview_missing_dependencies",
        "skills_preview_requires_confirm",
        "skills_role_title",
        "skills_role_desc",
        "skills_field_role_id",
        "skills_field_role_version",
        "skills_btn_load_role",
        "skills_btn_save_role",
        "skills_role_enabled_title",
        "skills_role_default_label",
        "skills_role_effective",
        "skills_unavailable",
        "skills_status_invalid_path",
        "skills_status_need_preview",
        "skills_status_imported",
        "skills_status_enabled",
        "skills_status_disabled",
        "skills_status_uninstalled",
        "skills_status_uninstalled_files_pending",
        "skills_status_uninstall_blocked",
        "skills_status_role_identity_required",
        "skills_status_role_unconfigured",
        "skills_status_role_saved",
        "skills_status_error",
    }
    assert required <= zh
    assert required <= en
