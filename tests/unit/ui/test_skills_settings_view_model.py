"""Skills 设置 VM 单测（W-05：预览/导入/启停/卸载/角色选择边界）。

覆盖：未接线如实不可用、列表分组、预览拒绝与确认前校验、版本更新确认标志、
引用阻删如实报错、启用停用委托、角色保存前一致性校验与解析回读。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    RoleSkillAssignment,
    SkillVersionRef,
    SkillVersionView,
)
from services.agent_service.skills import SkillImportReceipt, SkillImportStatus, SkillPackageImportRequest
from ui.viewmodels.skills_settings_view_model import (
    IMPORT_KIND_DIRECTORY,
    STATUS_ERROR,
    STATUS_SUCCESS,
    SkillsSettingsViewModel,
)

pytestmark = pytest.mark.unit


def _view(skill_id: str = "demo", version: int = 1, *, read_state: str = "ready") -> SkillVersionView:
    return SkillVersionView(
        skill_id=skill_id,
        display_name="Demo Skill",
        version=version,
        read_state=read_state,
        content_digest="d" * 64,
        file_count=3,
        expanded_bytes=1024,
        license_id="MIT",
    )


def _receipt(
    status: SkillImportStatus = SkillImportStatus.NEW, *, requires_confirmation: bool = False
) -> SkillImportReceipt:
    return SkillImportReceipt(
        status=status,
        user_scope="scope",
        skill_id="demo",
        version=2,
        display_name="Demo Skill",
        content_digest="d" * 64,
        license_id="MIT",
        file_count=3,
        expanded_bytes=1024,
        dependencies=("base",),
        missing_dependencies=(),
        requires_confirmation=requires_confirmation,
    )


class _FakeService:
    def __init__(self) -> None:
        self.views: tuple[SkillVersionView, ...] = (_view(),)
        self.preview_error: Exception | None = None
        self.import_error: Exception | None = None
        self.enable_error: Exception | None = None
        self.uninstall_error: Exception | None = None
        self.validate_error: Exception | None = None
        self.list_error: Exception | None = None
        self.read_error: Exception | None = None
        self.resolve_error: Exception | None = None
        self.read_assignment: RoleSkillAssignment | None = None
        self.preview_requests: list[SkillPackageImportRequest] = []
        self.import_requests: list[SkillPackageImportRequest] = []
        self.enabled_calls: list[tuple[SkillVersionRef, bool]] = []
        self.uninstalled: list[SkillVersionRef] = []
        self.assignments: list[RoleSkillAssignment] = []
        self.validated: list[RoleSkillAssignment] = []
        self.files_removed = True

    async def list_skills(self, *, user_scope: str) -> tuple[SkillVersionView, ...]:
        if self.list_error is not None:
            raise self.list_error
        return self.views

    async def preview_skill_package(self, request: SkillPackageImportRequest) -> SkillImportReceipt:
        self.preview_requests.append(request)
        if self.preview_error is not None:
            raise self.preview_error
        return _receipt(SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION, requires_confirmation=True)

    async def import_skill_package(self, request: SkillPackageImportRequest) -> SkillImportReceipt:
        self.import_requests.append(request)
        if self.import_error is not None:
            raise self.import_error
        return _receipt(SkillImportStatus.UPDATE)

    async def set_skill_enabled(self, ref: SkillVersionRef, *, user_scope: str, enabled: bool) -> Any:
        self.enabled_calls.append((ref, enabled))
        if self.enable_error is not None:
            raise self.enable_error
        return SimpleNamespace(version=ref.version)

    async def uninstall_skill(self, ref: SkillVersionRef, *, user_scope: str) -> Any:
        self.uninstalled.append(ref)
        if self.uninstall_error is not None:
            raise self.uninstall_error
        return SimpleNamespace(skill_id=ref.skill_id, version=ref.version, files_removed=self.files_removed)

    async def read_role_skill_assignment(
        self, *, user_scope: str, role_id: str, role_version: str
    ) -> RoleSkillAssignment | None:
        if self.read_error is not None:
            raise self.read_error
        return self.read_assignment

    async def validate_role_skill_assignment(
        self, assignment: RoleSkillAssignment, *, user_scope: str
    ) -> tuple[SkillVersionRef, ...]:
        self.validated.append(assignment)
        if self.validate_error is not None:
            raise self.validate_error
        return assignment.defaults

    async def assign_role_skills(self, assignment: RoleSkillAssignment, *, user_scope: str, display_name: str) -> Any:
        self.assignments.append(assignment)
        return SimpleNamespace()

    async def resolve_role_skill_selection(
        self, *, user_scope: str, role_id: str, role_version: str, hand_picked: Any = (), is_child: bool = False
    ) -> tuple[SkillVersionRef, ...]:
        if self.resolve_error is not None:
            raise self.resolve_error
        return (SkillVersionRef("demo", 1),)


def _vm(service: _FakeService | None) -> SkillsSettingsViewModel:
    return SkillsSettingsViewModel(cast(Any, service), user_scope="scope")


@pytest.mark.asyncio
async def test_unwired_service_reports_unavailable() -> None:
    vm = _vm(None)
    await vm.load()
    state = vm.state
    assert state.available is False
    assert state.status is not None and state.status.key == "skills_unavailable"


@pytest.mark.asyncio
async def test_load_groups_versions_by_skill() -> None:
    service = _FakeService()
    service.views = (_view(version=1), _view(version=2, read_state="disabled"))
    vm = _vm(service)
    await vm.load()
    assert vm.state.loaded is True
    assert len(vm.state.rows) == 1
    assert [row.version for row in vm.state.rows[0].versions] == [1, 2]
    assert vm.state.rows[0].latest_version == 2


@pytest.mark.asyncio
async def test_preview_error_is_reported_without_preview(tmp_path: Path) -> None:
    service = _FakeService()
    service.preview_error = AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid_skill_package", "scope")
    vm = _vm(service)
    vm.set_import_kind(IMPORT_KIND_DIRECTORY)
    vm.set_import_path(str(tmp_path / "demo"))
    await vm.preview_import()
    assert vm.state.preview is None
    assert vm.state.status_level == STATUS_ERROR
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"


@pytest.mark.asyncio
async def test_preview_then_confirm_sends_confirmed_update(tmp_path: Path) -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_import_kind(IMPORT_KIND_DIRECTORY)
    vm.set_import_path(str(tmp_path / "demo"))
    await vm.preview_import()
    assert vm.state.preview is not None and vm.state.preview.requires_confirmation is True
    assert service.preview_requests[0].confirm_update is False
    await vm.confirm_import()
    assert service.import_requests[0].confirm_update is True
    assert vm.state.status is not None and vm.state.status.key == "skills_status_imported"


@pytest.mark.asyncio
async def test_confirm_requires_preview_first(tmp_path: Path) -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_import_kind(IMPORT_KIND_DIRECTORY)
    vm.set_import_path(str(tmp_path / "demo"))
    await vm.confirm_import()
    assert service.import_requests == []
    assert vm.state.status is not None and vm.state.status.key == "skills_status_need_preview"


@pytest.mark.asyncio
async def test_empty_import_path_is_reported() -> None:
    service = _FakeService()
    vm = _vm(service)
    await vm.preview_import()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_invalid_path"
    assert service.preview_requests == []


@pytest.mark.asyncio
async def test_relative_directory_path_is_rejected_as_invalid() -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_import_kind(IMPORT_KIND_DIRECTORY)
    vm.set_import_path("relative/demo")
    await vm.preview_import()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_invalid_path"
    assert service.preview_requests == []


@pytest.mark.asyncio
async def test_enable_and_disable_delegate_to_service() -> None:
    service = _FakeService()
    vm = _vm(service)
    await vm.set_enabled("demo@1", enabled=False)
    await vm.set_enabled("demo@1", enabled=True)
    assert service.enabled_calls == [(SkillVersionRef("demo", 1), False), (SkillVersionRef("demo", 1), True)]
    assert vm.state.status is not None and vm.state.status.key == "skills_status_enabled"


@pytest.mark.asyncio
async def test_uninstall_reference_blocked_is_surfaced_verbatim() -> None:
    service = _FakeService()
    service.uninstall_error = AgentServiceError(
        AgentErrorCode.POLICY_DENIED,
        "skill version is referenced by role: role-main@1",
        "scope",
    )
    vm = _vm(service)
    await vm.uninstall("demo@1")
    assert vm.state.status is not None
    assert vm.state.status.key == "skills_status_uninstall_blocked"
    assert "role-main@1" in str(vm.state.status.params["reason"])


@pytest.mark.asyncio
async def test_uninstall_reports_residual_files_honestly() -> None:
    service = _FakeService()
    service.files_removed = False
    vm = _vm(service)
    await vm.uninstall("demo@1")
    key = vm.state.status.key if vm.state.status is not None else None
    assert key == "skills_status_uninstalled_files_pending"


@pytest.mark.asyncio
async def test_role_save_validates_before_persisting() -> None:
    service = _FakeService()
    service.validate_error = AgentServiceError(
        AgentErrorCode.POLICY_DENIED, "skill version not enabled: demo@1", "scope/role-main@1"
    )
    vm = _vm(service)
    vm.set_role_identity("role_id", "role-main")
    vm.set_role_identity("role_version", "1")
    vm.toggle_role_enabled("demo@1")
    await vm.save_role_assignment()
    assert service.assignments == []
    assert vm.state.status_level == STATUS_ERROR


@pytest.mark.asyncio
async def test_role_save_persists_and_reads_back_effective_selection() -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_role_identity("role_id", "role-main")
    vm.set_role_identity("role_version", "1")
    vm.toggle_role_enabled("demo@1")
    vm.toggle_role_default("demo@1")
    await vm.save_role_assignment()
    assert service.validated[0].defaults == (SkillVersionRef("demo", 1),)
    assert service.assignments[0].enabled == (SkillVersionRef("demo", 1),)
    assert vm.state.role_effective == ("demo@1",)
    assert vm.state.status_level == STATUS_SUCCESS


@pytest.mark.asyncio
async def test_disabling_enabled_role_version_also_drops_default() -> None:
    vm = _vm(_FakeService())
    vm.toggle_role_enabled("demo@1")
    vm.toggle_role_default("demo@1")
    vm.toggle_role_enabled("demo@1")
    assert vm.state.role_enabled == ()
    assert vm.state.role_defaults == ()


@pytest.mark.asyncio
async def test_load_failure_is_reported() -> None:
    service = _FakeService()
    service.list_error = AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "db down", "scope")
    vm = _vm(service)
    await vm.load()
    assert vm.state.loaded is False
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"


@pytest.mark.asyncio
async def test_zip_preview_reads_archive_and_sends_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "demo.zip"
    archive.write_bytes(b"PK-not-a-real-zip")
    service = _FakeService()
    vm = _vm(service)
    vm.set_import_kind("zip")
    vm.set_import_path(str(archive))
    await vm.preview_import()
    assert service.preview_requests[0].data == b"PK-not-a-real-zip"
    assert service.preview_requests[0].directory is None


@pytest.mark.asyncio
async def test_zip_preview_missing_file_is_invalid_path(tmp_path: Path) -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_import_kind("zip")
    vm.set_import_path(str(tmp_path / "absent.zip"))
    await vm.preview_import()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_invalid_path"
    assert service.preview_requests == []


@pytest.mark.asyncio
async def test_invalid_import_kind_is_ignored() -> None:
    vm = _vm(_FakeService())
    vm.set_import_kind("ftp")
    assert vm.state.import_kind == "zip"


@pytest.mark.asyncio
async def test_confirm_import_failure_is_reported(tmp_path: Path) -> None:
    service = _FakeService()
    service.import_error = AgentServiceError(AgentErrorCode.INPUT_INVALID, "bad package", "scope")
    vm = _vm(service)
    vm.set_import_kind(IMPORT_KIND_DIRECTORY)
    vm.set_import_path(str(tmp_path / "demo"))
    await vm.preview_import()
    await vm.confirm_import()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"


@pytest.mark.asyncio
async def test_enable_failure_is_reported() -> None:
    service = _FakeService()
    service.enable_error = AgentServiceError(AgentErrorCode.POLICY_DENIED, "bound to role", "scope")
    vm = _vm(service)
    await vm.set_enabled("demo@1", enabled=False)
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"


@pytest.mark.asyncio
async def test_invalid_ref_key_is_ignored() -> None:
    service = _FakeService()
    vm = _vm(service)
    await vm.set_enabled("not-a-ref", enabled=True)
    await vm.uninstall("demo@0")
    assert service.enabled_calls == []
    assert service.uninstalled == []


@pytest.mark.asyncio
async def test_load_role_requires_identity() -> None:
    vm = _vm(_FakeService())
    await vm.load_role_assignment()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_role_identity_required"


@pytest.mark.asyncio
async def test_load_role_unconfigured_and_configured() -> None:
    service = _FakeService()
    vm = _vm(service)
    vm.set_role_identity("role_id", "role-main")
    vm.set_role_identity("role_version", "1")
    await vm.load_role_assignment()
    assert vm.state.role_loaded is True
    assert vm.state.status is not None and vm.state.status.key == "skills_status_role_unconfigured"

    service.read_assignment = RoleSkillAssignment(
        role_id="role-main",
        role_version="1",
        enabled=(SkillVersionRef("demo", 1),),
        defaults=(SkillVersionRef("demo", 1),),
    )
    await vm.load_role_assignment()
    assert vm.state.role_enabled == ("demo@1",)
    assert vm.state.role_defaults == ("demo@1",)


@pytest.mark.asyncio
async def test_load_role_failure_is_reported() -> None:
    service = _FakeService()
    service.read_error = AgentServiceError(AgentErrorCode.INPUT_INVALID, "bad role", "scope")
    vm = _vm(service)
    vm.set_role_identity("role_id", "role-main")
    vm.set_role_identity("role_version", "1")
    await vm.load_role_assignment()
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"


@pytest.mark.asyncio
async def test_role_default_toggle_requires_enabled_version() -> None:
    vm = _vm(_FakeService())
    vm.toggle_role_default("demo@1")
    assert vm.state.role_defaults == ()


@pytest.mark.asyncio
async def test_save_role_requires_identity() -> None:
    service = _FakeService()
    vm = _vm(service)
    await vm.save_role_assignment()
    assert service.assignments == []
    assert vm.state.status is not None and vm.state.status.key == "skills_status_role_identity_required"


@pytest.mark.asyncio
async def test_save_role_resolution_failure_after_persist_is_reported() -> None:
    service = _FakeService()
    service.resolve_error = AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "resolve failed", "scope")
    vm = _vm(service)
    vm.set_role_identity("role_id", "role-main")
    vm.set_role_identity("role_version", "1")
    vm.toggle_role_enabled("demo@1")
    await vm.save_role_assignment()
    assert len(service.assignments) == 1
    assert vm.state.status is not None and vm.state.status.key == "skills_status_error"
