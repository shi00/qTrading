"""E-03-4b Skill 导入编排与角色分配服务单元测试（F12/F13）。

覆盖：新建/未变化/需确认/确认更新、缺失依赖记录、许可拒绝、登记失败补偿删除、
角色分配默认⊆启用前置拒绝与透传回执。
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    RoleSkillAssignment,
    RoleSkillAssignmentReceipt,
    SkillAssetReceipt,
    SkillAssetRegistration,
    SkillBudget,
    SkillVersionRef,
    sha256_hex,
)
from services.agent_service.skills import (
    ManagedSkillStore,
    SkillCatalogService,
    SkillImportStatus,
    SkillPackageImportRequest,
)

pytestmark = pytest.mark.asyncio


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _package(
    *,
    name: str = "demo-skill",
    description: str = "demo description",
    license_id: str = "MIT",
    dependencies: tuple[str, ...] = (),
    body: bytes = b"# Instructions",
) -> bytes:
    lines = ["---", f"name: {name}", f"description: {description}", f"license: {license_id}"]
    if dependencies:
        lines.append("dependencies:")
        lines.extend(f"  - {item}" for item in dependencies)
    lines.extend(["---", ""])
    return _zip_bytes({"SKILL.md": ("\n".join(lines) + "\n").encode() + body})


class FakeCatalogDao:
    def __init__(self) -> None:
        self.assets: dict[tuple[str, str, int], tuple[str, dict[str, object]]] = {}
        self.roles: dict[tuple[str, str, str], tuple[str, dict[str, object]]] = {}
        self.fail_register = False
        self.role_requests: list[RoleSkillAssignment] = []

    async def list_skill_versions(self, *, user_scope: str, skill_id: str) -> tuple[tuple[int, str], ...]:
        return tuple(
            sorted(
                (version, digest)
                for (scope, asset_id, version), (digest, _) in self.assets.items()
                if scope == user_scope and asset_id == skill_id
            )
        )

    async def has_skill(self, *, user_scope: str, skill_id: str) -> bool:
        return any(scope == user_scope and asset_id == skill_id for scope, asset_id, _ in self.assets)

    async def register_skill_asset(self, request: SkillAssetRegistration) -> SkillAssetReceipt:
        if self.fail_register:
            raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "forced failure", "test")
        key = (request.user_scope, request.skill_id, request.version)
        if key in self.assets:
            digest, _ = self.assets[key]
            if digest != request.content_digest:
                raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "differs", "test")
            return SkillAssetReceipt(
                user_scope=request.user_scope,
                skill_id=request.skill_id,
                version=request.version,
                content_digest=request.content_digest,
                replayed=True,
            )
        self.assets[key] = (
            request.content_digest,
            {"missing_dependencies": list(request.missing_dependencies)},
        )
        return SkillAssetReceipt(
            user_scope=request.user_scope,
            skill_id=request.skill_id,
            version=request.version,
            content_digest=request.content_digest,
            replayed=False,
        )

    async def save_role_skill_assignment(
        self,
        assignment: RoleSkillAssignment,
        *,
        user_scope: str,
        display_name: str,
    ) -> RoleSkillAssignmentReceipt:
        self.role_requests.append(assignment)
        refs = tuple(sorted({(ref.skill_id, ref.version) for ref in (*assignment.enabled, *assignment.defaults)}))
        return RoleSkillAssignmentReceipt(
            user_scope=user_scope,
            role_id=assignment.role_id,
            role_version=assignment.role_version,
            digest=sha256_hex(b"role"),
            linked_skill_versions=tuple(SkillVersionRef(skill_id=item[0], version=item[1]) for item in refs),
            replayed=False,
        )


def _service(tmp_path: Path) -> tuple[SkillCatalogService, FakeCatalogDao, ManagedSkillStore]:
    store = ManagedSkillStore(str(tmp_path / "skills"))
    dao = FakeCatalogDao()
    service = SkillCatalogService(
        store,
        dao,
        blocking_runner=lambda func, *args: asyncio.to_thread(func, *args),
    )
    return service, dao, store


async def _import(service: SkillCatalogService, data: bytes, **kwargs: object):
    return await service.import_package(
        SkillPackageImportRequest(user_scope="scope", data=data, directory_name="demo-skill", **kwargs)  # type: ignore[arg-type]  # 测试透传确认开关
    )


class TestImportLifecycle:
    async def test_new_import_publishes_and_registers(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        receipt = await _import(service, _package())
        assert receipt.status is SkillImportStatus.NEW
        assert receipt.version == 1
        assert receipt.license_id == "MIT"
        assert receipt.requires_confirmation is False
        assert store.version_dir(SkillVersionRef(receipt.skill_id, 1)).is_dir()
        assert len(dao.assets) == 1

    async def test_same_content_reimport_is_unchanged_without_new_facts(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        first = await _import(service, _package())
        second = await _import(service, _package())
        assert second.status is SkillImportStatus.UNCHANGED
        assert second.version == first.version
        assert len(dao.assets) == 1
        assert store.current_version(first.skill_id) == 1

    async def test_different_content_requires_confirmation_and_publishes_nothing(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        first = await _import(service, _package())
        second = await _import(service, _package(body=b"# Changed"))
        assert second.status is SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION
        assert second.requires_confirmation is True
        assert second.version == 2
        assert len(dao.assets) == 1
        assert store.current_version(first.skill_id) == 1

    async def test_confirmed_update_publishes_next_version_and_keeps_old(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        first = await _import(service, _package())
        second = await _import(service, _package(body=b"# Changed"), confirm_update=True)
        assert second.status is SkillImportStatus.UPDATE
        assert second.version == 2
        assert len(dao.assets) == 2
        assert store.read_file(SkillVersionRef(first.skill_id, 1), "SKILL.md") != store.read_file(
            SkillVersionRef(first.skill_id, 2), "SKILL.md"
        )

    async def test_missing_dependency_recorded_but_import_allowed(self, tmp_path: Path) -> None:
        service, dao, _ = _service(tmp_path)
        receipt = await _import(service, _package(dependencies=("base-skill",)))
        assert receipt.dependencies == ("base-skill",)
        assert receipt.missing_dependencies == ("base-skill",)
        stored = next(iter(dao.assets.values()))
        assert stored[1]["missing_dependencies"] == ["base-skill"]

    async def test_existing_dependency_is_not_reported_missing(self, tmp_path: Path) -> None:
        service, dao, _ = _service(tmp_path)
        base = await _import(service, _package(name="base-skill"))
        receipt = await _import(service, _package(dependencies=("base-skill",)))
        assert base.status is SkillImportStatus.NEW
        assert receipt.missing_dependencies == ()

    async def test_unknown_license_rejected_before_any_side_effect(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        with pytest.raises(AgentServiceError) as exc:
            await _import(service, _package(license_id="WTFPL"))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.assets == {}
        assert not any((tmp_path / "skills").rglob("SKILL.md"))

    async def test_registration_failure_removes_published_version(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        dao.fail_register = True
        with pytest.raises(AgentServiceError) as exc:
            await _import(service, _package())
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert not any((tmp_path / "skills").rglob("SKILL.md"))

    async def test_multiple_manifests_rejected(self, tmp_path: Path) -> None:
        service, dao, _ = _service(tmp_path)
        data = _zip_bytes({"SKILL.md": b"---\nname: demo\ndescription: x\nlicense: MIT\n---", "nested/SKILL.md": b"x"})
        with pytest.raises(AgentServiceError) as exc:
            await _import(service, data)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.assets == {}

    async def test_directory_source_imports_without_archive(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        source = tmp_path / "src"
        source.mkdir()
        (source / "SKILL.md").write_bytes(b"---\nname: demo-skill\ndescription: demo\nlicense: MIT\n---\n# body\n")
        (source / "empty").mkdir()
        receipt = await service.import_package(
            SkillPackageImportRequest(user_scope="scope", directory_name="demo-skill", directory=str(source))
        )
        assert receipt.status is SkillImportStatus.NEW
        assert receipt.source_kind == "directory"
        version_dir = store.version_dir(SkillVersionRef(receipt.skill_id, 1))
        assert version_dir.is_dir()
        assert (version_dir / "empty").is_dir()
        assert len(dao.assets) == 1

    async def test_reimport_heals_orphan_published_version(self, tmp_path: Path) -> None:
        service, dao, store = _service(tmp_path)
        first = await _import(service, _package())
        dao.assets.clear()  # 模拟「目录已发布、登记行丢失」的崩溃窗口
        second = await _import(service, _package())
        assert second.status is SkillImportStatus.NEW
        assert second.version == 1
        assert len(dao.assets) == 1
        assert store.current_version(first.skill_id) == 1

    async def test_staging_deadline_is_enforced_after_inspection(self, tmp_path: Path) -> None:
        store = ManagedSkillStore(str(tmp_path / "skills"))
        dao = FakeCatalogDao()
        calls: list[int] = []
        original = store.publish_package

        def spy(*args: object, **kwargs: object):
            calls.append(1)
            return original(*args, **kwargs)  # type: ignore[arg-type]  # 测试透传发布参数

        store.publish_package = spy  # type: ignore[method-assign]  # 测试注入发布探针
        ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 5.0])
        service = SkillCatalogService(
            store,
            dao,
            blocking_runner=lambda func, *args: asyncio.to_thread(func, *args),
            budget=SkillBudget(max_preflight_seconds=1),
            clock=lambda: next(ticks, 5.0),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _import(service, _package())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "skill_time_exceeded" in str(exc.value)
        assert calls == [1]
        assert dao.assets == {}
        assert not any((tmp_path / "skills").rglob("SKILL.md"))
        staging_root = tmp_path / "skills" / ".staging"
        if staging_root.exists():
            assert not list(staging_root.iterdir())


class TestRoleSkillAssignmentService:
    async def test_assignment_passes_validated_contract_to_dao(self, tmp_path: Path) -> None:
        service, dao, _ = _service(tmp_path)
        assignment = RoleSkillAssignment(
            role_id="role-main",
            role_version="2",
            enabled=(SkillVersionRef(skill_id="a", version=1), SkillVersionRef(skill_id="b", version=3)),
            defaults=(SkillVersionRef(skill_id="a", version=1),),
        )
        receipt = await service.assign_role_skills(assignment, user_scope="scope", display_name="主研究角色")
        assert receipt.role_id == "role-main"
        assert [ref.skill_id for ref in receipt.linked_skill_versions] == ["a", "b"]
        assert dao.role_requests == [assignment]

    async def test_default_not_enabled_rejected_before_dao(self, tmp_path: Path) -> None:
        service, dao, _ = _service(tmp_path)
        assignment = RoleSkillAssignment(
            role_id="role-main",
            role_version="2",
            enabled=(SkillVersionRef(skill_id="a", version=1),),
            defaults=(SkillVersionRef(skill_id="b", version=1),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await service.assign_role_skills(assignment, user_scope="scope", display_name="角色")
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.role_requests == []

    async def test_empty_assignment_is_valid(self, tmp_path: Path) -> None:
        service, _, _ = _service(tmp_path)
        assignment = RoleSkillAssignment(role_id="role-child", role_version="1", enabled=(), defaults=())
        receipt = await service.assign_role_skills(assignment, user_scope="scope", display_name="子角色")
        assert receipt.linked_skill_versions == ()
