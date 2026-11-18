"""E-03-4b Skill 导入编排与角色分配服务单元测试（F12/F13）。

覆盖：新建/未变化/需确认/确认更新、缺失依赖记录、许可拒绝、登记失败补偿删除、
角色分配默认⊆启用前置拒绝与透传回执；I10 取消语义（waiter 超时/取消后确认退出或
标未知、发布前门零迟到发布、取消保旧、取消传播不被吞）。
"""

from __future__ import annotations

import asyncio
import io
import threading
import zipfile
from pathlib import Path

import pytest

import services.agent_service.skills as skills_module
from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
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
    SkillOperationState,
    SkillOperationTicket,
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


class _WorkerBlocker:
    """worker 阻塞哨兵：模拟「waiter 已放弃、worker 仍在跑」。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self._release = threading.Event()

    def block(self, timeout: float = 30.0) -> None:
        self.started.set()
        self._release.wait(timeout)

    def release(self) -> None:
        self._release.set()


class _BlockingRegisterDao(FakeCatalogDao):
    """可阻塞登记的 DAO：卡在「替换已完成、登记未完成」窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.block_registration = False
        self.register_entered = threading.Event()

    async def register_skill_asset(self, request: SkillAssetRegistration) -> SkillAssetReceipt:
        if self.block_registration:
            self.register_entered.set()
            await asyncio.sleep(60)
        return await super().register_skill_asset(request)


def _ticketed_service(
    tmp_path: Path,
    dao: FakeCatalogDao,
    tickets: list[SkillOperationTicket],
    *,
    budget: SkillBudget | None = None,
) -> tuple[SkillCatalogService, ManagedSkillStore]:
    store = ManagedSkillStore(str(tmp_path / "skills"))

    def factory(owner: str) -> SkillOperationTicket:
        ticket = SkillOperationTicket(owner)
        tickets.append(ticket)
        return ticket

    service = SkillCatalogService(
        store,
        dao,
        blocking_runner=lambda func, *args: asyncio.to_thread(func, *args),
        budget=budget if budget is not None else SkillBudget(),
        cancel_exit_timeout=0.05,
        operation_factory=factory,
    )
    return service, store


class TestImportCancellation:
    """I10：waiter 超时/取消须确认 worker 退出或显式标未知；发布前门保证零迟到发布。"""

    async def test_normal_import_records_published_owner_and_exit(self, tmp_path: Path) -> None:
        tickets: list[SkillOperationTicket] = []
        service, store = _ticketed_service(tmp_path, FakeCatalogDao(), tickets)
        receipt = await _import(service, _package())
        assert receipt.status is SkillImportStatus.NEW
        assert store.version_dir(SkillVersionRef(receipt.skill_id, 1)).is_dir()
        ticket = tickets[0]
        assert ticket.owner.startswith("scope/skill/demo-skill#")
        assert ticket.claimed == SkillVersionRef(receipt.skill_id, 1)
        assert ticket.state is SkillOperationState.PUBLISHED
        assert ticket.exited

    async def test_waiter_timeout_marks_unknown_without_publish(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tickets: list[SkillOperationTicket] = []
        dao = FakeCatalogDao()
        service, _ = _ticketed_service(tmp_path, dao, tickets, budget=SkillBudget(max_preflight_seconds=0.3))
        blocker = _WorkerBlocker()
        original_parse = skills_module.parse_skill_manifest

        def slow_parse(data: bytes, *, directory_name: str):
            blocker.block()
            return original_parse(data, directory_name=directory_name)

        monkeypatch.setattr(skills_module, "parse_skill_manifest", slow_parse)
        try:
            with pytest.raises(AgentServiceError) as excinfo:
                await _import(service, _package())
            # 释放前断言：worker 仍在跑，只能标「未知」，不得假装已停或已发布。
            ticket = tickets[0]
            assert ticket.state is SkillOperationState.UNKNOWN
            assert not ticket.exited
        finally:
            blocker.release()
        assert "skill_time_exceeded" in str(excinfo.value)
        assert ticket.published_destination is None
        assert dao.assets == {}
        assert not any((tmp_path / "skills").rglob("SKILL.md"))

    async def test_cancel_during_preflight_keeps_old_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tickets: list[SkillOperationTicket] = []
        service, store = _ticketed_service(tmp_path, FakeCatalogDao(), tickets)
        first = await _import(service, _package())
        blocker = _WorkerBlocker()
        original_inspect = skills_module.inspect_skill_package

        def slow_inspect(data: bytes, **kwargs: object):
            blocker.block()
            return original_inspect(data, **kwargs)  # type: ignore[arg-type]  # 测试透传预检参数

        monkeypatch.setattr(skills_module, "inspect_skill_package", slow_inspect)
        task = asyncio.create_task(_import(service, _package(body=b"# Changed"), confirm_update=True))
        assert await asyncio.to_thread(blocker.started.wait, 10.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancel_info:
            await task
        assert isinstance(cancel_info.value, asyncio.CancelledError)
        assert task.cancelled()  # R2：取消未被吞
        blocker.release()
        ticket = tickets[-1]
        assert await asyncio.to_thread(ticket.wait_exit, 10.0)
        assert ticket.published_destination is None
        assert ticket.state is SkillOperationState.ABORTED
        assert store.current_version(first.skill_id) == 1
        assert not (store.root / first.skill_id / "2").exists()

    async def test_cancel_before_replace_publishes_nothing(self, tmp_path: Path) -> None:
        tickets: list[SkillOperationTicket] = []
        dao = FakeCatalogDao()
        service, store = _ticketed_service(tmp_path, dao, tickets)
        blocker = _WorkerBlocker()
        original_publish = store.publish_package

        def slow_publish(*args: object, **kwargs: object):
            blocker.block()
            return original_publish(*args, **kwargs)  # type: ignore[arg-type]  # 测试透传发布参数

        store.publish_package = slow_publish  # type: ignore[method-assign]  # 测试注入发布延迟
        task = asyncio.create_task(_import(service, _package()))
        assert await asyncio.to_thread(blocker.started.wait, 10.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancel_info:
            await task
        assert isinstance(cancel_info.value, asyncio.CancelledError)
        assert task.cancelled()
        blocker.release()
        ticket = tickets[0]
        assert await asyncio.to_thread(ticket.wait_exit, 10.0)
        assert ticket.published_destination is None  # 发布前门拦下，零替换
        assert ticket.state is SkillOperationState.ABORTED
        assert dao.assets == {}
        assert not any((tmp_path / "skills").rglob("SKILL.md"))

    async def test_publish_gate_refuses_replace_after_cancel(self, tmp_path: Path) -> None:
        ticket = SkillOperationTicket("scope/skill/demo#unit")
        calls: list[int] = []

        def replace() -> Path:
            calls.append(1)
            return tmp_path

        ticket.cancel()
        with pytest.raises(AgentValidationError) as gate_error:
            ticket.publish(replace, destination=tmp_path)
        assert gate_error.value.code == "skill_cancelled"
        assert calls == []
        assert ticket.state is SkillOperationState.ABORTED

    async def test_cancel_after_publish_before_register_removes_new_version(self, tmp_path: Path) -> None:
        tickets: list[SkillOperationTicket] = []
        dao = _BlockingRegisterDao()
        service, store = _ticketed_service(tmp_path, dao, tickets)
        first = await _import(service, _package())
        dao.block_registration = True
        task = asyncio.create_task(_import(service, _package(body=b"# Changed"), confirm_update=True))
        assert await asyncio.to_thread(dao.register_entered.wait, 10.0)
        ticket = tickets[-1]
        assert ticket.published_destination is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancel_info:
            await task
        assert isinstance(cancel_info.value, asyncio.CancelledError)
        assert task.cancelled()
        assert ticket.exited
        assert ticket.published_destination is None
        assert ticket.state is SkillOperationState.ABORTED
        assert store.current_version(first.skill_id) == 1  # 取消保旧
        assert not (store.root / first.skill_id / "2").exists()
        assert len(dao.assets) == 1


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
