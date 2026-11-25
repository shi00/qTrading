"""研究资料：VM 已读/限额/三类时点 + 草稿并入/转移 + 面板渲染。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

from core.agent_types import DRAFT_PAYLOAD_SCHEMA_VERSION, DraftRead, Mode, Purpose
from ui.viewmodels.research_observer_view_model import (
    MATERIAL_READ_PARTIAL,
    MATERIAL_READ_READY,
    MATERIAL_READ_UNREAD,
    MaterialCandidate,
    RejectedMaterialView,
    ResearchMaterialView,
    ResearchObserverState,
    ResearchObserverViewModel,
    SessionListItemView,
)
from ui.views.research_observer_materials import build_materials_section

pytestmark = pytest.mark.unit

_CAPTURED = "2026-11-20T08:00:00.000000Z"
_CONTENT = "2026-11-18T00:00:00.000000Z"


def _draft_read(*, revision: int = 3, cleaned: bool = False) -> DraftRead:
    return DraftRead(
        session_id="session-target",
        draft_revision=revision,
        payload=None,
        is_empty=False,
        cleaned=cleaned,
        archived=False,
        transferred=False,
        read_at="2026-11-25T00:00:00.000000Z",
    )


class _MaterialsService:
    """最小 facade：读稿 + 登记来源 + 保存 + 转移 + 会话列表。"""

    def __init__(
        self,
        *,
        revision: int = 3,
        cleaned: bool = False,
        save_error: Exception | None = None,
        transfer_error: Exception | None = None,
    ) -> None:
        self.revision = revision
        self.cleaned = cleaned
        self.save_error = save_error
        self.transfer_error = transfer_error
        self.calls: list[str] = []
        self.registered: tuple = ()
        self.saved_commands: list = []
        self.transfer_commands: list = []
        self.search_calls = 0

    async def read_draft(self, query, *, user_scope: str):
        self.calls.append("read_draft")
        return _draft_read(revision=self.revision, cleaned=self.cleaned)

    async def register_draft_sources(self, *, user_scope, session_id, draft_revision, items):
        self.calls.append("register_draft_sources")
        self.registered = items
        return SimpleNamespace(registered=items, link_ids=(), replayed=False)

    async def save_draft(self, command):
        self.calls.append("save_draft")
        self.saved_commands.append(command)
        if self.save_error is not None:
            raise self.save_error
        return SimpleNamespace(session_id=command.session_id, draft_revision=command.expected_draft_revision + 1)

    async def transfer_draft(self, command):
        self.calls.append("transfer_draft")
        self.transfer_commands.append(command)
        if self.transfer_error is not None:
            raise self.transfer_error
        return SimpleNamespace(
            target_session_id=command.target_session_id,
            target_revision=command.expected_target_draft_revision + 1,
            transfer_id=command.transfer_id,
        )

    async def search(self, query, *, user_scope: str):
        self.search_calls += 1
        return SimpleNamespace(hits=())


class _ErrorWithCode(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _vm(service: _MaterialsService | None = None) -> ResearchObserverViewModel:
    vm = ResearchObserverViewModel(service)  # type: ignore[arg-type]  # 替身仅实现被测入口
    vm._set_state(available=True, sessions=(_target_session(),), materials_target_session_id="session-target")
    return vm


def _target_session() -> SessionListItemView:
    return SessionListItemView(
        session_id="session-target",
        title="目标研究",
        updated_at="2026-11-24T00:00:00.000000Z",
        revision=3,
        pinned=False,
        archived=False,
        cleaned=False,
    )


def _text_material(name: str = "notes.md", data: bytes | None = None) -> MaterialCandidate:
    return MaterialCandidate(
        kind="file",
        display_name=name,
        data=data if data is not None else b"alpha\nbeta\n",
        captured_at=_CAPTURED,
        content_at=_CONTENT,
    )


def test_attach_materials_marks_decodable_text_ready_and_keeps_three_dates():
    vm = _vm()

    vm.attach_materials([_text_material()])

    assert len(vm.state.materials) == 1
    material = vm.state.materials[0]
    assert material.read_state == MATERIAL_READ_READY
    assert material.is_read is True
    assert material.captured_at == _CAPTURED
    assert material.content_at == _CONTENT
    assert material.codepoints == 11
    assert material.reason_code is None
    assert vm.state.rejected_materials == ()


def test_attach_materials_marks_binary_unread_without_faking_read():
    vm = _vm()

    vm.attach_materials([MaterialCandidate(kind="file", display_name="scan.pdf", data=b"%PDF-1.7")])

    material = vm.state.materials[0]
    assert material.read_state == MATERIAL_READ_UNREAD
    assert material.reason_code == "material_extraction_not_wired"
    assert vm.state.material_counts() == (0, 1)


def test_attach_materials_marks_oversized_text_partial():
    vm = _vm()

    vm.attach_materials([_text_material(data=b"x" * 100_001)])

    material = vm.state.materials[0]
    assert material.read_state == MATERIAL_READ_PARTIAL
    assert material.reason_code == "material_truncated"
    assert vm.state.material_counts() == (0, 1)


def test_attach_materials_rejects_sixth_item_with_parseable_code():
    vm = _vm()
    candidates = [_text_material(name=f"doc-{index}.md", data=f"body-{index}".encode()) for index in range(6)]

    vm.attach_materials(candidates)

    assert len(vm.state.materials) == 5
    assert len(vm.state.rejected_materials) == 1
    rejected = vm.state.rejected_materials[0]
    assert rejected.reason_code == "material_count_exceeded"
    assert rejected.reason_key == "agent_research_materials_reason_count"


def test_attach_materials_rejects_duplicate_without_double_counting():
    vm = _vm()
    candidate = _text_material()

    vm.attach_materials([candidate, candidate])

    assert len(vm.state.materials) == 1
    assert vm.state.rejected_materials[0].reason_code == "material_duplicate"


def test_attach_materials_keeps_unknown_dates_as_none():
    vm = _vm()

    vm.attach_materials([MaterialCandidate(kind="fragment", display_name="摘录", text="只读片段")])

    material = vm.state.materials[0]
    assert material.captured_at is None
    assert material.content_at is None
    assert material.read_state == MATERIAL_READ_READY


def test_remove_material_and_select_target_only_accept_valid_ids():
    vm = _vm()
    vm.attach_materials([_text_material()])
    key = vm.state.materials[0].key

    vm.select_materials_target("session-target")
    assert vm.state.materials_target_session_id == "session-target"

    vm.select_materials_target("session-missing")
    assert vm.state.materials_target_session_id == "session-target"

    vm.remove_material(key)
    assert vm.state.materials == ()


@pytest.mark.asyncio
async def test_add_materials_to_existing_session_saves_draft_and_clears_materials():
    service = _MaterialsService()
    vm = _vm(service)
    vm.attach_materials([_text_material()])

    await vm.add_materials_to_existing_session()

    assert service.calls == ["read_draft", "register_draft_sources", "save_draft"]
    assert service.saved_commands[0].expected_draft_revision == 3
    assert vm.state.materials == ()
    assert vm.state.rejected_materials == ()
    assert vm.state.materials_transfer_pending is False
    assert vm.state.materials_transfer_error_code is None
    assert vm.state.materials_transfer_notice_key == "agent_research_materials_notice_added"
    assert dict(vm.state.materials_transfer_notice_params) == {"name": "目标研究", "revision": "4"}
    assert service.search_calls == 1


@pytest.mark.asyncio
async def test_transfer_draft_to_target_passes_idempotent_command_and_reports_notice():
    service = _MaterialsService()
    vm = _vm(service)
    vm.attach_materials([_text_material()])

    await vm.transfer_draft_to_target()

    command = service.transfer_commands[0]
    assert command.command.command_id.startswith("ui-materials-")
    assert command.command.expected_revision == 3
    assert command.expected_target_draft_revision == 3
    assert command.payload.schema_version == DRAFT_PAYLOAD_SCHEMA_VERSION
    assert command.payload.mode is Mode.DISCUSS
    assert command.payload.purpose is Purpose.END_OF_DAY_RESEARCH
    assert command.payload.research_date is None
    assert vm.state.materials_transfer_notice_key == "agent_research_materials_notice_transferred"


@pytest.mark.asyncio
async def test_materials_transfer_conflict_reports_code_without_clearing_materials():
    service = _MaterialsService(save_error=_ErrorWithCode("target_draft_conflict"))
    vm = _vm(service)
    vm.attach_materials([_text_material()])

    await vm.add_materials_to_existing_session()

    assert vm.state.materials_transfer_error_code == "target_draft_conflict"
    assert vm.state.materials_transfer_error_key == "agent_research_materials_error_target_conflict"
    assert len(vm.state.materials) == 1, "失败不得清空用户已选资料"
    assert vm.state.materials_transfer_notice_key is None


@pytest.mark.asyncio
async def test_materials_transfer_rejects_without_target_or_materials():
    service = _MaterialsService()
    vm = _vm(service)
    vm._set_state(materials_target_session_id=None)

    await vm.add_materials_to_existing_session()
    assert vm.state.materials_transfer_error_key == "agent_research_materials_unavailable_no_target"

    vm._set_state(materials_target_session_id="session-target")
    await vm.transfer_draft_to_target()
    assert vm.state.materials_transfer_error_key == "agent_research_materials_unavailable_empty"
    assert service.calls == []


@pytest.mark.asyncio
async def test_materials_transfer_unavailable_when_service_lacks_transfer():
    vm = _vm(_MaterialsService())
    vm._set_state(materials_transfer_available=False)
    vm._set_state(materials_transfer_unavailable_key="agent_research_materials_unavailable_not_wired")
    vm.attach_materials([_text_material()])

    await vm.transfer_draft_to_target()

    assert vm.state.materials_transfer_error_code == "not_wired"
    assert vm.state.materials_transfer_error_key == "agent_research_materials_unavailable_not_wired"


def test_materials_transfer_available_true_with_read_and_transfer_entrypoints():
    vm = ResearchObserverViewModel(_MaterialsService())  # type: ignore[arg-type]  # 替身仅实现被测入口

    assert vm.state.materials_transfer_available is True


def test_attach_materials_marks_unreadable_and_invalid_candidates_truthfully():
    vm = _vm()

    vm.attach_materials(
        [
            MaterialCandidate(kind="file", display_name="bad.md", data=b"\xff\xfe\xfa"),
            MaterialCandidate(kind="file", display_name="missing.md"),
            MaterialCandidate(kind="fragment", display_name="blank", text="   "),
            MaterialCandidate(kind="reference", display_name="no-version"),
            MaterialCandidate(kind="reference", display_name="ref.md", source_id="src-1", source_version="v1"),
            MaterialCandidate(kind="video", display_name="clip"),
        ]
    )

    assert [(item.display_name, item.read_state, item.reason_code) for item in vm.state.materials] == [
        ("bad.md", MATERIAL_READ_UNREAD, "material_decode_failed"),
        ("ref.md", MATERIAL_READ_UNREAD, "material_reference_not_read"),
    ]
    assert {item.reason_code for item in vm.state.rejected_materials} == {
        "material_content_missing",
        "material_empty",
        "material_reference_invalid",
        "material_kind_invalid",
    }
    assert vm.state.material_counts() == (0, 2)


def test_attach_materials_accepts_read_reference_with_codepoint_count():
    vm = _vm()

    vm.attach_materials(
        [
            MaterialCandidate(
                kind="reference",
                display_name="引用",
                source_id="src-2",
                source_version="v2",
                text="引用正文",
            )
        ]
    )

    material = vm.state.materials[0]
    assert material.read_state == MATERIAL_READ_READY
    assert material.codepoints == 4
    assert material.byte_size == len("引用正文".encode())


def test_select_materials_target_none_clears_selection():
    vm = _vm()
    vm.attach_materials([_text_material()])

    vm.select_materials_target(None)

    assert vm.state.materials_target_session_id is None


def _material_view(
    *,
    name: str = "notes.md",
    read_state: str = MATERIAL_READ_READY,
    reason_code: str | None = None,
    byte_size: int | None = 512,
    codepoints: int | None = 11,
    captured_at: str | None = _CAPTURED,
    content_at: str | None = _CONTENT,
) -> ResearchMaterialView:
    return ResearchMaterialView(
        key=f"file:{name}:v1",
        kind="file",
        display_name=name,
        source_id=f"file:{name}",
        source_version="v1",
        read_state=read_state,
        reason_code=reason_code,
        captured_at=captured_at,
        content_at=content_at,
        byte_size=byte_size,
        codepoints=codepoints,
    )


def _all_texts(control: ft.Control) -> list[str]:
    found: list[str] = []
    stack = [control]
    while stack:
        current = stack.pop()
        if isinstance(current, ft.Text):
            found.append(current.value or "")
        for attribute in ("controls", "content"):
            child = getattr(current, attribute, None)
            if isinstance(child, ft.Control):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend(item for item in child if isinstance(item, ft.Control))
    return found


def test_materials_section_renders_read_state_limits_and_unknown_dates():
    state = ResearchObserverState(
        available=True,
        materials=(
            _material_view(),
            _material_view(
                name="scan.pdf", read_state=MATERIAL_READ_UNREAD, reason_code="material_extraction_not_wired"
            ),
            _material_view(name="excerpt.txt", read_state=MATERIAL_READ_PARTIAL, reason_code="material_truncated"),
            _material_view(name="nodates.md", captured_at=None, content_at=None),
        ),
        materials_target_session_id="session-target",
        sessions=(_target_session(),),
        materials_transfer_available=False,
        materials_transfer_unavailable_key="agent_research_materials_unavailable_not_wired",
    )

    control = build_materials_section(state)
    texts = _all_texts(control)

    assert "已读 2 / 未读 2（共 4 件）" in texts
    assert "名额 4/5" in texts
    assert "研究发起时间：未知" in texts
    assert "资料采集时间：未知" in texts
    assert "资料内容时点：未知" in texts
    assert any("未读（该格式内容解析未接线，未读入研究）" in text for text in texts)
    assert any("草稿并入/转移未接线，按钮不可用。" in text for text in texts)


def test_materials_section_renders_rejections_errors_and_transfer_notice():
    state = ResearchObserverState(
        available=True,
        materials=(_material_view(),),
        rejected_materials=(RejectedMaterialView("doc-9.md", "file", "material_count_exceeded"),),
        materials_error_key="agent_research_materials_unavailable_empty",
        materials_transfer_available=True,
        materials_transfer_error_key="agent_research_materials_error_unknown",
        materials_transfer_error_code="Boom",
        materials_transfer_notice_key="agent_research_materials_notice_added",
        materials_transfer_notice_params=(("name", "目标研究"), ("revision", "4")),
        materials_transfer_pending=True,
        sessions=(_target_session(),),
        materials_target_session_id="session-target",
    )

    texts = _all_texts(build_materials_section(state))

    assert any("已拒绝添加「doc-9.md」：超过单条研究的资料件数上限" in text for text in texts)
    assert "请先添加资料。" in texts
    assert "操作失败（Boom）；未伪造成功。" in texts
    assert "资料已并入「目标研究」草稿（第 4 版），尚未发送。" in texts
    assert "提交中…" in texts


def _all_controls(control: ft.Control) -> list[ft.Control]:
    found: list[ft.Control] = []
    stack = [control]
    while stack:
        current = stack.pop()
        found.append(current)
        for attribute in ("controls", "content"):
            child = getattr(current, attribute, None)
            if isinstance(child, ft.Control):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend(item for item in child if isinstance(item, ft.Control))
    return found


def test_materials_section_wires_buttons_and_dropdown_callbacks():
    events: list[tuple[str, object]] = []
    state = ResearchObserverState(
        available=True,
        materials=(_material_view(),),
        materials_transfer_available=True,
        sessions=(_target_session(),),
        materials_target_session_id="session-target",
    )

    control = build_materials_section(
        state,
        on_add_click=lambda: events.append(("add", None)),
        on_remove=lambda key: events.append(("remove", key)),
        on_select_target=lambda session_id: events.append(("select", session_id)),
        on_add_to_session=lambda: events.append(("merge", None)),
        on_transfer=lambda: events.append(("transfer", None)),
    )
    controls = _all_controls(control)
    buttons = [item for item in controls if isinstance(item, (ft.OutlinedButton, ft.FilledButton, ft.TextButton))]
    dropdown = next(item for item in controls if isinstance(item, ft.Dropdown))

    for button in buttons:
        handler = button.on_click
        if handler is None:
            pytest.fail("materials button must bind a click callback")
        cast(Any, handler)(None)
    cast(Any, dropdown.on_select)(SimpleNamespace(control=SimpleNamespace(value="session-target")))

    assert set(events) == {
        ("add", None),
        ("remove", state.materials[0].key),
        ("merge", None),
        ("transfer", None),
        ("select", "session-target"),
    }


def test_materials_section_renders_empty_state_and_success_notice():
    state = ResearchObserverState(
        available=True,
        materials_notice_key="agent_research_materials_target_empty",
    )

    texts = _all_texts(build_materials_section(state))

    assert "尚未添加资料。" in texts
    assert "暂无可选的有效会话。" in texts


def test_materials_section_formats_byte_sizes_across_units():
    state = ResearchObserverState(
        available=True,
        materials=(
            _material_view(name="small.md", byte_size=512),
            _material_view(name="kilo.md", byte_size=2_500),
            _material_view(name="mega.md", byte_size=2_500_000),
            _material_view(name="unknown.md", byte_size=None, codepoints=None),
        ),
    )

    texts = _all_texts(build_materials_section(state))

    assert "字符 11 · 512 B" in texts
    assert "字符 11 · 2.5 KB" in texts
    assert "字符 11 · 2.50 MB" in texts
    assert "字符 未知 · 未知" in texts
