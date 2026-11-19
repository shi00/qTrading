"""E-06-3a 清理作业持久化集成（真 sidecar PG，§9.11/§10.7）。

验证：
- 创建/读取往返：不可变身份集合（原生 ID / 会话 / 资产版本）与预览摘要、
  修订、阶段、fence 逐字段成立；
- 同身份重放幂等，异摘要/异集合明确 ``INPUT_CONFLICT``，不静默覆盖；
- fence 只在原摘要+修订匹配时提交；摘要漂移 → ``INPUT_INVALID`` 且行不变；
- ``verified`` 阶段必须已提交 fence；verified_counts/verified_at 落库；
- ``retry_pending`` 复用原集合（不扩缩）；
- ``DaoCleanupJobStore`` + ``CleanupService.confirm`` 在真库上端到端：首次提交
  fence、二次确认 replayed、已 verified 返回 already_verified。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.agent_types import (
    CLEANUP_NATIVE_TARGETS,
    AgentErrorCode,
    AgentServiceError,
    CleanupJobRecord,
    CleanupPhase,
    CleanupPrecondition,
    EventKind,
    EventReadQuery,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentSession
from services.agent_service.cleanup import (
    CleanupConfirmCommand,
    CleanupService,
    DaoCleanupJobStore,
    preview_digest,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER_SCOPE = "user-cleanup-job"
_EXACT_IDS = ("wf-b", "wf-a")
_SESSION_IDS = ("session-2", "session-1")
_ASSET_KEYS = (("asset-b", "2"), ("asset-a", "1"))

_RESET_APP_SCHEMA = (
    text("DROP SCHEMA IF EXISTS public CASCADE"),
    text("CREATE SCHEMA public"),
)


def _zeros() -> dict[str, int]:
    """九域齐备零计数（含应用 Datasource 输出正文域）：verified 写入的完整依据。"""

    return {table: 0 for table, _key in CLEANUP_NATIVE_TARGETS}


def _precondition(**overrides: bool) -> CleanupPrecondition:
    values: dict[str, bool] = {
        "scope_settled": True,
        "writers_exited": True,
        "closure_known": True,
        "preview_valid": True,
        "new_reference_conflict": False,
        "unknown_deliveries": False,
    }
    values.update(overrides)
    return CleanupPrecondition(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class _AuthoritativeFacts:
    """集成测试用权威事实替身（生产由存储面/在途/交付事实实现）。"""

    async def derive_precondition(self, user_scope: str, cleanup_job_id: str) -> CleanupPrecondition | None:
        return _precondition()


def _record(job_id: str, **overrides: object) -> CleanupJobRecord:
    values: dict[str, object] = {
        "user_scope": _USER_SCOPE,
        "cleanup_job_id": job_id,
        "owner_ref": "session-owner",
        "preview_digest": preview_digest(("session-owner",), _EXACT_IDS),
        "expected_revision": 7,
        "exact_ids": _EXACT_IDS,
        "phase": CleanupPhase.PREPARED,
        "fence_committed": False,
        "session_ids": _SESSION_IDS,
        "asset_keys": _ASSET_KEYS,
    }
    values.update(overrides)
    return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖


def _job_id() -> str:
    return f"job-{uuid.uuid4().hex[:10]}"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def app_url(real_embedded_pg) -> AsyncGenerator[str]:
    """真 sidecar 默认库：public schema 重建并迁移到 head（隔离运行）。"""

    url = real_embedded_pg.url
    setup_engine = create_test_engine(url)
    try:
        async with setup_engine.begin() as conn:
            for statement in _RESET_APP_SCHEMA:
                await conn.execute(statement)
    finally:
        await setup_engine.dispose()
    alembic_cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    yield url


@pytest_asyncio.fixture(loop_scope="session")
async def dao(app_url: str) -> AsyncGenerator[AgentDao]:
    engine = create_test_engine(app_url)
    try:
        yield AgentDao(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_paths_append_change_events_once(dao: AgentDao) -> None:
    """登记/fence/阶段各同事务追加 cleanup_changed；重写不重复（R2-I21）。"""

    job_id = _job_id()
    record = _record(job_id)
    async with dao.engine.begin() as conn:
        for session_id in _SESSION_IDS:
            await conn.execute(
                pg_insert(AgentSession)
                .values(
                    user_scope=_USER_SCOPE,
                    session_id=session_id,
                    title=f"cleanup {session_id}",
                )
                .on_conflict_do_nothing()
            )
    assert await dao.save_cleanup_job(record) is True
    assert await dao.save_cleanup_job(record) is False
    await dao.commit_cleanup_fence(record)
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.RETRY_PENDING, error_category="transient")
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.RETRY_PENDING, error_category="transient")

    page = await dao.read_events(EventReadQuery(user_scope=_USER_SCOPE, session_id=_SESSION_IDS[0], after_seq=0))
    events = [event for event in page.events if event.event_kind is EventKind.CLEANUP_CHANGED]
    assert [event.payload_kind for event in events] == ["cleanup_job", "cleanup_fence", "cleanup_phase"]
    assert all(event.subject_id == job_id for event in events)


@pytest.mark.asyncio(loop_scope="session")
async def test_create_and_load_roundtrip_preserves_frozen_identity(dao: AgentDao) -> None:
    job_id = _job_id()
    record = _record(job_id)

    assert await dao.save_cleanup_job(record) is True

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.cleanup_job_id == job_id
    assert loaded.owner_ref == "session-owner"
    assert loaded.preview_digest == record.preview_digest
    assert loaded.expected_revision == 7
    assert loaded.exact_ids == ("wf-a", "wf-b")
    assert loaded.session_ids == ("session-1", "session-2")
    assert loaded.asset_keys == (("asset-a", "1"), ("asset-b", "2"))
    assert loaded.phase is CleanupPhase.PREPARED
    assert loaded.fence_committed is False


@pytest.mark.asyncio(loop_scope="session")
async def test_same_identity_replay_is_idempotent_and_conflict_is_rejected(dao: AgentDao) -> None:
    job_id = _job_id()
    record = _record(job_id)
    await dao.save_cleanup_job(record)

    assert await dao.save_cleanup_job(_record(job_id)) is False

    with pytest.raises(AgentServiceError) as digest_exc:
        await dao.save_cleanup_job(_record(job_id, preview_digest="c" * 64))
    assert digest_exc.value.code is AgentErrorCode.INPUT_CONFLICT

    with pytest.raises(AgentServiceError) as set_exc:
        await dao.save_cleanup_job(_record(job_id, exact_ids=("wf-c",)))
    assert set_exc.value.code is AgentErrorCode.INPUT_CONFLICT

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.exact_ids == ("wf-a", "wf-b")


@pytest.mark.asyncio(loop_scope="session")
async def test_fence_requires_original_digest_and_revision(dao: AgentDao) -> None:
    job_id = _job_id()
    record = _record(job_id)
    await dao.save_cleanup_job(record)

    with pytest.raises(AgentServiceError) as stale_exc:
        await dao.commit_cleanup_fence(_record(job_id, preview_digest="d" * 64))
    assert stale_exc.value.code is AgentErrorCode.INPUT_INVALID
    before = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert before is not None
    assert before.fence_committed is False

    await dao.commit_cleanup_fence(record)
    after = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert after is not None
    assert after.fence_committed is True
    assert after.exact_ids == ("wf-a", "wf-b")

    # 重复提交幂等（条件更新仍命中，行不变）。
    await dao.commit_cleanup_fence(record)
    still = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert still is not None
    assert still.fence_committed is True


@pytest.mark.asyncio(loop_scope="session")
async def test_verified_requires_fence_and_persists_counts(dao: AgentDao) -> None:
    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))

    with pytest.raises(AgentServiceError) as exc:
        await dao.save_cleanup_phase(
            _USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=_zeros(), complete=True
        )
    assert exc.value.code is AgentErrorCode.INPUT_INVALID

    await dao.commit_cleanup_fence(_record(job_id))
    zeros = _zeros()
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=zeros, complete=True)

    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 集成测试直读核验
        row = (
            await conn.execute(
                text(
                    "SELECT phase, complete, fence_committed, verified_counts, verified_at "
                    "FROM agent_cleanup_job WHERE user_scope = :scope AND cleanup_job_id = :job"
                ),
                {"scope": _USER_SCOPE, "job": job_id},
            )
        ).one()
    assert row[0] == "verified"
    assert row[1] is True
    assert row[2] is True
    assert row[3] == zeros
    assert row[4] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_retry_pending_reuses_original_set(dao: AgentDao) -> None:
    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))
    await dao.commit_cleanup_fence(_record(job_id))

    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.RETRY_PENDING, error_category="transient")

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.phase is CleanupPhase.RETRY_PENDING
    assert loaded.exact_ids == ("wf-a", "wf-b")
    assert loaded.session_ids == ("session-1", "session-2")
    assert loaded.asset_keys == (("asset-a", "1"), ("asset-b", "2"))
    assert loaded.prior_error_category == "transient"


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_service_confirm_commits_fence_once_on_real_store(dao: AgentDao) -> None:
    job_id = _job_id()
    record = _record(job_id)
    await dao.save_cleanup_job(record)
    service = CleanupService(DaoCleanupJobStore(dao), _AuthoritativeFacts())
    command = CleanupConfirmCommand(
        user_scope=_USER_SCOPE,
        cleanup_job_id=job_id,
        command_id="cmd-1",
        preview_digest=record.preview_digest,
        expected_revision=record.expected_revision,
        confirmed=True,
    )

    first = await service.confirm(command)
    assert first.replayed is False
    assert first.exact_ids == ("wf-a", "wf-b")
    persisted = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert persisted is not None
    assert persisted.fence_committed is True

    second = await service.confirm(command)
    assert second.replayed is True
    assert second.exact_ids == ("wf-a", "wf-b")

    await dao.save_cleanup_phase(
        _USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=_zeros(), complete=True
    )
    verified = await service.confirm(command)
    assert verified.replayed is True
    assert verified.phase is CleanupPhase.VERIFIED
    assert verified.admission.reason == "already_verified"

    denied = await service.confirm(command)
    assert denied.admission.reason == "already_verified"


@pytest.mark.asyncio(loop_scope="session")
async def test_verified_phase_is_terminal_and_cannot_regress(dao: AgentDao) -> None:
    """阶段单调：已 verified 的作业拒绝 retry_pending 覆盖（R22 语义），行与时间戳不变。"""

    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))
    await dao.commit_cleanup_fence(_record(job_id))
    zeros = _zeros()
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=zeros, complete=True)

    with pytest.raises(AgentServiceError) as exc:
        await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.RETRY_PENDING, error_category="transient")
    assert exc.value.code is AgentErrorCode.INPUT_INVALID
    assert "already verified" in str(exc.value)

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.phase is CleanupPhase.VERIFIED
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 集成测试直读核验
        row = (
            await conn.execute(
                text(
                    "SELECT complete, verified_at, error_category FROM agent_cleanup_job "
                    "WHERE user_scope = :scope AND cleanup_job_id = :job"
                ),
                {"scope": _USER_SCOPE, "job": job_id},
            )
        ).one()
    assert row[0] is True
    assert row[1] is not None
    assert row[2] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_fence_write_rejected_after_verified(dao: AgentDao) -> None:
    """并发确认：后到者的 fence 写入不得把已 verified 阶段改回未完成态。"""

    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))
    await dao.commit_cleanup_fence(_record(job_id))
    zeros = _zeros()
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=zeros, complete=True)

    with pytest.raises(AgentServiceError) as exc:
        await dao.commit_cleanup_fence(_record(job_id))
    assert exc.value.code is AgentErrorCode.INPUT_INVALID
    assert "already verified" in str(exc.value)

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.phase is CleanupPhase.VERIFIED
    assert loaded.fence_committed is True


@pytest.mark.asyncio(loop_scope="session")
async def test_content_manifest_is_part_of_cleanup_identity(dao: AgentDao) -> None:
    """固定集合：content_manifest 同身份重放须逐值一致，异值不得被静默忽略。"""

    job_id = _job_id()
    assert await dao.save_cleanup_job(_record(job_id), content_manifest={"body_refs": ["body-a"]}) is True
    assert await dao.save_cleanup_job(_record(job_id), content_manifest={"body_refs": ["body-a"]}) is False
    # tuple/list 与键序差异属同一身份（规范 JSON 比较），不得误判冲突。
    assert await dao.save_cleanup_job(_record(job_id), content_manifest={"body_refs": ("body-a",)}) is False

    with pytest.raises(AgentServiceError) as exc:
        await dao.save_cleanup_job(_record(job_id), content_manifest={"body_refs": ["body-b"]})
    assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
    assert "content manifest" in str(exc.value)

    with pytest.raises(AgentServiceError) as none_exc:
        await dao.save_cleanup_job(_record(job_id))
    assert none_exc.value.code is AgentErrorCode.INPUT_CONFLICT

    # 入口即校验规范 JSON：不可承载类型不得先入库成功、重放时才炸。
    with pytest.raises(AgentServiceError) as bad_exc:
        await dao.save_cleanup_job(_record(_job_id()), content_manifest={"ratio": 1.5})
    assert bad_exc.value.code is AgentErrorCode.INPUT_INVALID
    assert "canonical JSON" in str(bad_exc.value)


@pytest.mark.asyncio(loop_scope="session")
async def test_decimal_content_manifest_is_stored_via_canonical_json(dao: AgentDao) -> None:
    """规范 JSON 可承载的 Decimal 不得被 JSONB 编码器拒绝后以 StatementError 逃逸。"""

    job_id = _job_id()
    assert await dao.save_cleanup_job(_record(job_id), content_manifest={"ratio": Decimal("1.5")}) is True
    # 重放核对与落库同一规范形态：Decimal("1.5") 与字符串 "1.5" 同身份。
    assert await dao.save_cleanup_job(_record(job_id), content_manifest={"ratio": "1.5"}) is False

    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 集成测试直读核验
        stored = (
            await conn.execute(
                text(
                    "SELECT content_manifest FROM agent_cleanup_job WHERE user_scope = :scope AND cleanup_job_id = :job"
                ),
                {"scope": _USER_SCOPE, "job": job_id},
            )
        ).scalar_one()
    assert stored == {"ratio": "1.5"}


@pytest.mark.asyncio(loop_scope="session")
async def test_fence_commit_does_not_regress_retry_pending_phase(dao: AgentDao) -> None:
    """fence 提交只写 fence 位：携带旧 ``prepared`` 记录的并发确认不得回退阶段（R22）。"""

    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))
    await dao.commit_cleanup_fence(_record(job_id))
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.RETRY_PENDING, error_category="transient")

    await dao.commit_cleanup_fence(_record(job_id))

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.phase is CleanupPhase.RETRY_PENDING
    assert loaded.fence_committed is True
    assert loaded.prior_error_category == "transient"


@pytest.mark.asyncio(loop_scope="session")
async def test_fence_rejects_verified_record(dao: AgentDao) -> None:
    """DAO 面硬化：verified 记录不得经 fence 路径写入（否则出现无 verified_at 的坏态）。"""

    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id))

    with pytest.raises(AgentServiceError) as exc:
        await dao.commit_cleanup_fence(_record(job_id, phase=CleanupPhase.VERIFIED))
    assert exc.value.code is AgentErrorCode.INPUT_INVALID
    assert "cannot rewrite fence" in str(exc.value)

    loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
    assert loaded is not None
    assert loaded.phase is CleanupPhase.PREPARED
    assert loaded.fence_committed is False


@pytest.mark.asyncio(loop_scope="session")
async def test_verified_keeps_content_manifest(dao: AgentDao) -> None:
    """固定集合留存：verified 阶段不得清空 content_manifest（§9.11 / §10.7 第 5 行）。"""

    job_id = _job_id()
    await dao.save_cleanup_job(_record(job_id), content_manifest={"body_refs": ["body-keep"]})
    await dao.commit_cleanup_fence(_record(job_id))
    zeros = _zeros()
    await dao.save_cleanup_phase(_USER_SCOPE, job_id, phase=CleanupPhase.VERIFIED, verified_counts=zeros, complete=True)

    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 集成测试直读核验
        manifest = (
            await conn.execute(
                text(
                    "SELECT content_manifest FROM agent_cleanup_job WHERE user_scope = :scope AND cleanup_job_id = :job"
                ),
                {"scope": _USER_SCOPE, "job": job_id},
            )
        ).scalar_one()
    assert manifest == {"body_refs": ["body-keep"]}
