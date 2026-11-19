"""E-06-2c 联合清理真实执行集成（真 sidecar PG，DD §10.7）。

验证：
- 首次预检：九类原生目标逐域命中，主键域都查不到的 ID 明确报缺失（查不到≠已清）；
- 真删除：DBOS 公开 ``Client.delete_workflows``（只删系统，级联清理事件/通知/流）
  → ``agent_ds.datasource_outputs`` 常量表参数化 DML → 逐域查回全 0；
- 集合外不变：同库集合外 workflow 的九域行保持原样；
- 重试幂等：已有可靠 fence 的重复删除不报错、仍查回 0；
- 应用域 NOT NULL tombstone：``agent_session.title`` / ``agent_asset_version``
  ``display_name``+``digest`` 换固定占位，可 NULL 的草稿/来源/元数据/正文引用清除，
  集合外会话/资产不变；
- 结构回执与摘要（E-06-5b）：命令请求/回执、决定问题/schema、同意载荷、操作
  worker 引用、来源 locator、交付回执清空；可 NULL 摘要置 NULL、NOT NULL 摘要换
  tombstone；清理后读回报 ``result_cleaned`` / ``manifest_cleaned`` 而非「从未发生」。

标记：``pytest.mark.integration`` + ``pytest.mark.embedded_real``。
SQL 合规（R4）：全部经静态 ``sa.text()`` 常量承载，身份经命名参数绑定。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from dbos import DBOSClient
from sqlalchemy import text

from core.agent_types import (
    CLEANUP_TOMBSTONE,
    CLEANUP_TOMBSTONE_DIGEST,
    CleanupJobRecord,
    CleanupPhase,
    CleanupPrecondition,
    CommandRef,
    ArtifactDeliveryCommand,
    DeliveryManifest,
    DeliveryTarget,
    OperationReadQuery,
    derive_delivery_id,
)
from data.persistence.agent_migrator import run_agent_runtime_migrations
from data.persistence.agent_runtime_store import AgentRuntimeNativeStore
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentAssetVersion,
    AgentBodyPayload,
    AgentCommand,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentOperation,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)
from services.agent_service.cleanup import (
    CleanupConfirmCommand,
    CleanupService,
    DaoCleanupJobStore,
    preview_digest,
)
from services.agent_service.cleanup_execution import CleanupExecutionService
from services.agent_service.cleanup_native import NativeCleanupExecutor
from tests._helpers import create_test_engine, make_alembic_cfg
from tests.integration.test_agent_runtime_migrator import (
    _create_runtime_database,
    _drop_runtime_database,
    _ensure_runtime_role,
)

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER_SCOPE = "user-cleanup-2c"


def _new_workflow_ids() -> tuple[str, str]:
    """每个测试独立的集合内/集合外 workflow ID，避免顺序相关的残留数据。"""

    suffix = uuid.uuid4().hex[:8]
    return f"wf-in-{suffix}", f"wf-out-{suffix}"


_SEED_WORKFLOW_STATUS = text(
    "INSERT INTO dbos.workflow_status (workflow_uuid, created_at, updated_at, priority) VALUES (:workflow_id, 0, 0, 0)"
)
_SEED_WORKFLOW_INPUT = text(
    "INSERT INTO dbos.workflow_input (workflow_uuid, retention_timestamp) VALUES (:workflow_id, 0)"
)
_SEED_WORKFLOW_OUTPUT = text(
    "INSERT INTO dbos.workflow_output (workflow_uuid, retention_timestamp) VALUES (:workflow_id, 0)"
)
_SEED_OPERATION_OUTPUTS = text(
    "INSERT INTO dbos.operation_outputs (workflow_uuid, function_id, function_name, retention_timestamp) "
    "VALUES (:workflow_id, 1, 'step', 0)"
)
_SEED_WORKFLOW_EVENTS = text(
    "INSERT INTO dbos.workflow_events (workflow_uuid, key, value) VALUES (:workflow_id, 'k', 'v')"
)
_SEED_WORKFLOW_EVENTS_HISTORY = text(
    "INSERT INTO dbos.workflow_events_history (workflow_uuid, key, value, function_id) "
    "VALUES (:workflow_id, 'k', 'v', 1)"
)
# offset 是 PostgreSQL 保留字（R17）：常量 SQL 中必须加引号，身份仍参数化。
_SEED_STREAMS = text(
    'INSERT INTO dbos.streams (workflow_uuid, key, value, "offset", function_id) '
    "VALUES (:workflow_id, 'k', 'v', 0, 1)"
)
_SEED_NOTIFICATIONS = text("INSERT INTO dbos.notifications (destination_uuid, message) VALUES (:workflow_id, 'm')")
_SEED_DATASOURCE_OUTPUTS = text(
    "INSERT INTO agent_ds.datasource_outputs (workflow_id, step_id, created_at) VALUES (:workflow_id, 1, 0)"
)

_SEED_STATEMENTS = (
    _SEED_WORKFLOW_STATUS,
    _SEED_WORKFLOW_INPUT,
    _SEED_WORKFLOW_OUTPUT,
    _SEED_OPERATION_OUTPUTS,
    _SEED_WORKFLOW_EVENTS,
    _SEED_WORKFLOW_EVENTS_HISTORY,
    _SEED_STREAMS,
    _SEED_NOTIFICATIONS,
    _SEED_DATASOURCE_OUTPUTS,
)

_RESET_APP_SCHEMA = (
    text("DROP SCHEMA IF EXISTS public CASCADE"),
    text("CREATE SCHEMA public"),
)


async def _inline_runner(func, *args):
    """集成测试用同步执行端口（不依赖 UI 线程池，等价受管线程语义）。"""

    return await asyncio.to_thread(func, *args)


def _precondition() -> CleanupPrecondition:
    return CleanupPrecondition(
        scope_settled=True,
        writers_exited=True,
        closure_known=True,
        preview_valid=True,
        new_reference_conflict=False,
    )


async def _reset_and_migrate_app_schema(url: str) -> None:
    """布局用例前提：public schema 重建并迁移到 head（每个用例独立可重复）。"""

    setup_engine = create_test_engine(url)
    try:
        async with setup_engine.begin() as conn:
            for statement in _RESET_APP_SCHEMA:
                await conn.execute(statement)
    finally:
        await setup_engine.dispose()
    alembic_cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def runtime_sync_url(real_embedded_pg) -> AsyncGenerator[str]:
    """独立逻辑库 qtrading_agent_runtime 的同步 psycopg URL（schema 已迁移）。"""

    admin_url = real_embedded_pg.url
    await _create_runtime_database(admin_url)
    await _ensure_runtime_role(admin_url)
    sync_url = (
        sa.make_url(admin_url)
        .set(drivername="postgresql+psycopg", database="qtrading_agent_runtime")
        .render_as_string(hide_password=False)
    )
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS dbos CASCADE"))
            conn.execute(text("DROP SCHEMA IF EXISTS agent_ds CASCADE"))
    finally:
        engine.dispose()
    run_agent_runtime_migrations(sync_url)
    yield sync_url
    await _drop_runtime_database(admin_url)


def _seed_native_rows(sync_url: str, workflow_ids: list[str]) -> None:
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for statement in _SEED_STATEMENTS:
                for workflow_id in workflow_ids:
                    conn.execute(statement, {"workflow_id": workflow_id})
    finally:
        engine.dispose()


def _executor(sync_url: str, control_url: str | None = None) -> tuple[NativeCleanupExecutor, sa.Engine]:
    store_engine = sa.create_engine(sync_url)
    control_engine = sa.create_engine(control_url or sync_url)
    client = DBOSClient(
        system_database_engine=control_engine,
        dbos_system_schema="dbos",
        use_listen_notify=False,
        retry_connection_errors=False,
        application_name="qtrading_agent_runtime",
    )
    executor = NativeCleanupExecutor(
        store=AgentRuntimeNativeStore(store_engine),
        client=client,
        run_blocking=_inline_runner,
    )
    return executor, store_engine


@pytest.mark.asyncio(loop_scope="session")
async def test_preflight_finds_nine_domains_and_reports_missing(runtime_sync_url: str) -> None:
    """预检：九域各命中 1；主键域都查不到的 ID 报缺失，不当已清。"""

    in_set, _out_set = _new_workflow_ids()
    missing = f"wf-missing-{uuid.uuid4().hex[:8]}"
    _seed_native_rows(runtime_sync_url, [in_set])
    executor, engine = _executor(runtime_sync_url)
    try:
        result = await executor.preflight([in_set, missing])
    finally:
        engine.dispose()

    assert result.exact_ids == tuple(sorted((in_set, missing)))
    assert result.missing_ids == (missing,)
    assert len(result.counts) == 9
    assert all(count == 1 for count in result.counts.values())


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_exact_set_zeroes_nine_domains_and_keeps_outside(runtime_sync_url: str) -> None:
    """真删除：集合内九域全 0（重试仍 0），集合外九域保持 1。"""

    in_set, out_set = _new_workflow_ids()
    _seed_native_rows(runtime_sync_url, [in_set, out_set])
    executor, engine = _executor(runtime_sync_url)
    try:
        first = await executor.delete([in_set])
        retry = await executor.delete([in_set])
        outside = await executor.preflight([out_set])
    finally:
        engine.dispose()

    assert first.exact_ids == (in_set,)
    assert len(first.verified_counts) == 9
    assert set(first.verified_counts.values()) == {0}
    assert retry.exact_ids == (in_set,)
    assert set(retry.verified_counts.values()) == {0}
    assert outside.missing_ids == ()
    assert set(outside.counts.values()) == {1}


@pytest.mark.asyncio(loop_scope="session")
async def test_tombstone_not_null_text_and_digest_with_outside_unchanged(real_embedded_pg) -> None:
    """应用域：NOT NULL 文本/摘要换 tombstone，可 NULL 私人内容清除，集合外不变。"""

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)

    engine = create_test_engine(url)
    dao = AgentDao(engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "session_id": "session-in",
                        "title": "私人原句标题",
                        "draft_payload": {"text": "私人草稿"},
                        "origin_ref": "C:/private/session",
                        "branch_metadata": {"note": "私人分支"},
                    },
                    {
                        "user_scope": _USER_SCOPE,
                        "session_id": "session-out",
                        "title": "集合外标题",
                        "draft_payload": {"text": "集合外草稿"},
                        "origin_ref": None,
                        "branch_metadata": None,
                    },
                ],
            )
            await conn.execute(
                sa.insert(AgentAssetVersion),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "asset_id": "asset-in",
                        "asset_version": "1",
                        "asset_kind": "summary",
                        "display_name": "私人原句文件名.txt",
                        "digest": "a" * 64,
                        "origin_ref": "C:/private/asset",
                        "metadata_payload": {"title": "私人原句"},
                        "body_text": "私人原句正文",
                        "read_state": "ready",
                        "verification_state": "program_verified",
                    },
                    {
                        "user_scope": _USER_SCOPE,
                        "asset_id": "asset-out",
                        "asset_version": "1",
                        "asset_kind": "summary",
                        "display_name": "集合外名称",
                        "digest": "b" * 64,
                        "origin_ref": None,
                        "metadata_payload": None,
                        "body_text": None,
                        "read_state": "ready",
                        "verification_state": "program_verified",
                    },
                ],
            )

        touched = await dao.tombstone_cleanup_text(
            _USER_SCOPE, session_ids=["session-in"], asset_keys=[("asset-in", "1")]
        )

        async with engine.connect() as conn:
            session_columns = (
                AgentSession.title,
                AgentSession.draft_payload,
                AgentSession.origin_ref,
                AgentSession.branch_metadata,
                AgentSession.cleaned_at,
            )
            asset_columns = (
                AgentAssetVersion.display_name,
                AgentAssetVersion.digest,
                AgentAssetVersion.origin_ref,
                AgentAssetVersion.metadata_payload,
                AgentAssetVersion.body_text,
                AgentAssetVersion.body_ref,
                AgentAssetVersion.tombstoned,
                AgentAssetVersion.cleaned_at,
            )
            session_in = (
                await conn.execute(sa.select(*session_columns).where(AgentSession.session_id == "session-in"))
            ).one()
            session_out = (
                await conn.execute(sa.select(*session_columns).where(AgentSession.session_id == "session-out"))
            ).one()
            asset_in = (
                await conn.execute(sa.select(*asset_columns).where(AgentAssetVersion.asset_id == "asset-in"))
            ).one()
            asset_out = (
                await conn.execute(sa.select(*asset_columns).where(AgentAssetVersion.asset_id == "asset-out"))
            ).one()
    finally:
        await engine.dispose()

    assert touched["agent_session"] == 1
    assert touched["agent_asset_version"] == 1
    assert all(
        touched[key] == 0
        for key in (
            "agent_message",
            "agent_artifact",
            "agent_round",
            "agent_operation",
            "agent_body_payload",
        )
    )
    assert session_in.title == CLEANUP_TOMBSTONE
    assert session_in.draft_payload is None
    assert session_in.origin_ref is None
    assert session_in.branch_metadata is None
    assert session_in.cleaned_at is not None
    assert session_out.title == "集合外标题"
    assert session_out.draft_payload == {"text": "集合外草稿"}

    assert asset_in.display_name == CLEANUP_TOMBSTONE
    assert asset_in.digest == CLEANUP_TOMBSTONE_DIGEST
    assert asset_in.origin_ref is None
    assert asset_in.metadata_payload is None
    assert asset_in.body_text is None
    assert asset_in.body_ref is None
    assert asset_in.tombstoned is True
    assert asset_in.cleaned_at is not None
    assert asset_out.display_name == "集合外名称"
    assert asset_out.digest == "b" * 64


@pytest.mark.asyncio(loop_scope="session")
async def test_full_cleanup_execution_verifies_native_and_app_domains(real_embedded_pg, runtime_sync_url: str) -> None:
    """端到端：真 job 持久化 + 真九类删除 + 真 NOT NULL tombstone → verified。"""

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)
    in_set, out_set = _new_workflow_ids()
    _seed_native_rows(runtime_sync_url, [in_set, out_set])

    job_id = f"job-{uuid.uuid4().hex[:8]}"
    session_in = f"session-in-{uuid.uuid4().hex[:6]}"
    asset_in = f"asset-in-{uuid.uuid4().hex[:6]}"
    record = CleanupJobRecord(
        user_scope=_USER_SCOPE,
        cleanup_job_id=job_id,
        owner_ref="session-owner",
        preview_digest=preview_digest(("session-owner",), (in_set,)),
        expected_revision=1,
        exact_ids=(in_set,),
        phase=CleanupPhase.PREPARED,
        fence_committed=False,
        session_ids=(session_in,),
        asset_keys=((asset_in, "1"),),
    )

    app_engine = create_test_engine(url)
    try:
        dao = AgentDao(app_engine)
        assert await dao.save_cleanup_job(record) is True
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "session_id": session_in,
                        "title": "私人原句标题",
                        "draft_payload": {"text": "私人草稿"},
                        "origin_ref": "C:/private/session",
                        "branch_metadata": None,
                    }
                ],
            )
            await conn.execute(
                sa.insert(AgentAssetVersion),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "asset_id": asset_in,
                        "asset_version": "1",
                        "asset_kind": "summary",
                        "display_name": "私人原句文件名.txt",
                        "digest": "a" * 64,
                        "origin_ref": "C:/private/asset",
                        "metadata_payload": {"title": "私人原句"},
                        "body_text": "私人原句正文",
                        "read_state": "ready",
                        "verification_state": "program_verified",
                    }
                ],
            )

        executor, runtime_engine = _executor(runtime_sync_url)
        try:
            store = DaoCleanupJobStore(dao)
            orchestrator = CleanupExecutionService(jobs=CleanupService(store), store=store, dao=dao, executor=executor)
            command = CleanupConfirmCommand(
                user_scope=_USER_SCOPE,
                cleanup_job_id=job_id,
                command_id="cmd-e2e",
                preview_digest=record.preview_digest,
                expected_revision=record.expected_revision,
                confirmed=True,
            )
            receipt = await orchestrator.confirm_and_execute(command, _precondition())
        finally:
            runtime_engine.dispose()

        loaded = await dao.load_cleanup_job(_USER_SCOPE, job_id)
        async with app_engine.connect() as conn:
            session_row = (
                await conn.execute(
                    sa.select(AgentSession.title, AgentSession.draft_payload).where(
                        AgentSession.session_id == session_in
                    )
                )
            ).one()
            asset_row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.display_name,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.body_text,
                        AgentAssetVersion.tombstoned,
                    ).where(AgentAssetVersion.asset_id == asset_in)
                )
            ).one()
        outside = await executor.preflight([out_set])
    finally:
        await app_engine.dispose()

    assert receipt.phase is CleanupPhase.VERIFIED
    assert receipt.replayed is False
    assert len(receipt.verified_counts) == 9
    assert set(receipt.verified_counts.values()) == {0}
    assert receipt.tombstoned["agent_session"] == 1
    assert receipt.tombstoned["agent_asset_version"] == 1
    # R2-I21：清理路径为集合内会话同事务追加 cleanup_changed 事件消息（1 会话 → 1 行），
    # 该事件行同样在本次清理中被清空正文，故计数为 1 而非 0。
    assert receipt.tombstoned["agent_message"] == 1
    assert receipt.tombstoned["agent_body_payload"] == 0
    assert loaded is not None
    assert loaded.phase is CleanupPhase.VERIFIED
    assert loaded.fence_committed is True
    assert loaded.exact_ids == (in_set,)
    assert session_row.title == CLEANUP_TOMBSTONE
    assert session_row.draft_payload is None
    assert asset_row.display_name == CLEANUP_TOMBSTONE
    assert asset_row.digest == CLEANUP_TOMBSTONE_DIGEST
    assert asset_row.body_text is None
    assert asset_row.tombstoned is True
    assert outside.missing_ids == ()
    assert set(outside.counts.values()) == {1}


@pytest.mark.asyncio(loop_scope="session")
async def test_content_domains_cleared_and_body_payloads_deleted(real_embedded_pg) -> None:
    """E-06-5a：集合内正文承载域清空、受管正文行删除；集合外原样（§10.7 表第 1—3 行）。"""

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)

    suffix = uuid.uuid4().hex[:8]
    session_in, session_out = f"session-in-{suffix}", f"session-out-{suffix}"
    execution_in, execution_out = f"execution-in-{suffix}", f"execution-out-{suffix}"
    round_in, round_out = f"round-in-{suffix}", f"round-out-{suffix}"
    message_in, message_out = f"message-in-{suffix}", f"message-out-{suffix}"
    artifact_in, artifact_out = f"artifact-in-{suffix}", f"artifact-out-{suffix}"
    operation_in = f"operation-in-{suffix}"
    body_in, body_out = f"body-in-{suffix}", f"body-out-{suffix}"
    secret = "私人原句-清理后不应可查"
    now = datetime.now(UTC)

    def _execution(execution_id: str, session_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "execution_id": execution_id,
            "session_id": session_id,
            "execution_kind": "root",
            "source_module": f"module-{execution_id}",
            "business_request_id": f"request-{execution_id}",
            "intent_digest": "e" * 64,
            "node_id": "root",
            "display_state": "complete",
            "goal_coverage": "full",
        }

    def _round(round_id: str, execution_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "round_id": round_id,
            "execution_id": execution_id,
            "round_number": 1,
            "workflow_id": f"wf-{round_id}",
            "workflow_code_version": "1",
            "run_epoch": 1,
            "input_revision": 1,
            "plan_revision": 1,
            "frozen_manifest": {"goal": secret},
            "actual_input": {"input": secret},
            "model_binding": {"prompt": secret},
            "sdk_history_ref": f"C:/private/{suffix}-{round_id}.json",
        }

    def _message(message_id: str, session_id: str, execution_id: str, round_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "message_id": message_id,
            "session_id": session_id,
            "execution_id": execution_id,
            "round_id": round_id,
            "event_id": f"evt-{message_id}",
            "session_seq": 1,
            "execution_seq": 1,
            "event_kind": "public_message_saved",
            "payload_kind": "public_message",
            "subject_id": message_id,
            "subject_revision": 1,
            "role": "assistant",
            "body_text": None,
            "payload": {"text": secret},
            "occurred_at": now,
            "committed_at": now,
            "search_text": secret,
        }

    def _artifact(artifact_id: str, session_id: str, execution_id: str, round_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "artifact_id": artifact_id,
            "artifact_version": 1,
            "session_id": session_id,
            "execution_id": execution_id,
            "round_id": round_id,
            "artifact_kind": "explanation",
            "input_revision": 1,
            "actual_input_digest": "a" * 64,
            "actual_model_ref": "model-in",
            "summary": secret,
            "result_payload": {"text": secret},
            "result_digest": "b" * 64,
            "source_available_state": "available",
        }

    app_engine = create_test_engine(url)
    try:
        dao = AgentDao(app_engine)
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession),
                [
                    {"user_scope": _USER_SCOPE, "session_id": session_in, "title": secret},
                    {"user_scope": _USER_SCOPE, "session_id": session_out, "title": secret},
                ],
            )
            await conn.execute(
                sa.insert(AgentExecution),
                [_execution(execution_in, session_in), _execution(execution_out, session_out)],
            )
            await conn.execute(
                sa.insert(AgentRound),
                [_round(round_in, execution_in), _round(round_out, execution_out)],
            )
            message_rows = [
                _message(message_in, session_in, execution_in, round_in),
                _message(message_out, session_out, execution_out, round_out),
            ]
            message_rows[0]["body_text"] = secret
            await conn.execute(sa.insert(AgentMessage), message_rows)
            await conn.execute(
                sa.insert(AgentArtifact),
                [
                    _artifact(artifact_in, session_in, execution_in, round_in),
                    _artifact(artifact_out, session_out, execution_out, round_out),
                ],
            )
            await conn.execute(
                sa.insert(AgentOperation),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "operation_id": operation_in,
                        "session_id": session_in,
                        "execution_id": execution_in,
                        "owner_epoch": 1,
                        "run_epoch": 1,
                        "phase": "prepared",
                        "effect_state": "not_dispatched",
                        "io_state": "unknown",
                        "request_ref": f"C:/private/request-{suffix}.json",
                        "result_ref": f"C:/private/result-{suffix}.json",
                        "result_payload": {"text": secret},
                    }
                ],
            )
            await conn.execute(
                sa.insert(AgentBodyPayload),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "body_ref": body_in,
                        "owner_kind": "artifact",
                        "owner_id": artifact_in,
                        "owner_version": "1",
                        "artifact_id": artifact_in,
                        "artifact_version": 1,
                        "message_id": None,
                        "encoding": "utf-8",
                        "payload_bytes": secret.encode("utf-8"),
                        "byte_length": len(secret.encode("utf-8")),
                        "payload_digest": "c" * 64,
                    },
                    {
                        "user_scope": _USER_SCOPE,
                        "body_ref": body_out,
                        "owner_kind": "message",
                        "owner_id": message_out,
                        "owner_version": "1",
                        "message_id": message_out,
                        "artifact_id": None,
                        "artifact_version": None,
                        "encoding": "utf-8",
                        "payload_bytes": secret.encode("utf-8"),
                        "byte_length": len(secret.encode("utf-8")),
                        "payload_digest": "d" * 64,
                    },
                ],
            )
            await conn.execute(
                sa.update(AgentArtifact).where(AgentArtifact.artifact_id == artifact_in).values(body_ref=body_in)
            )
            await conn.execute(
                sa.update(AgentMessage).where(AgentMessage.message_id == message_out).values(body_ref=body_out)
            )

        touched = await dao.tombstone_cleanup_text(_USER_SCOPE, session_ids=[session_in])

        async with app_engine.connect() as conn:
            message_row = (
                await conn.execute(
                    sa.select(
                        AgentMessage.body_text,
                        AgentMessage.body_ref,
                        AgentMessage.payload,
                        AgentMessage.search_text,
                    ).where(AgentMessage.message_id == message_in)
                )
            ).one()
            artifact_row = (
                await conn.execute(
                    sa.select(
                        AgentArtifact.summary,
                        AgentArtifact.body_text,
                        AgentArtifact.body_ref,
                        AgentArtifact.result_payload,
                        AgentArtifact.result_digest,
                        AgentArtifact.cleaned_at,
                    ).where(AgentArtifact.artifact_id == artifact_in)
                )
            ).one()
            round_row = (
                await conn.execute(
                    sa.select(
                        AgentRound.frozen_manifest,
                        AgentRound.actual_input,
                        AgentRound.model_binding,
                        AgentRound.sdk_history_ref,
                    ).where(AgentRound.round_id == round_in)
                )
            ).one()
            operation_row = (
                await conn.execute(
                    sa.select(
                        AgentOperation.result_payload,
                        AgentOperation.request_ref,
                        AgentOperation.result_ref,
                    ).where(AgentOperation.operation_id == operation_in)
                )
            ).one()
            remaining_bodies = (
                (
                    await conn.execute(
                        sa.select(AgentBodyPayload.body_ref).where(
                            AgentBodyPayload.user_scope == _USER_SCOPE,
                            AgentBodyPayload.body_ref.in_([body_in, body_out]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            out_message_row = (
                await conn.execute(
                    sa.select(
                        AgentMessage.body_ref,
                        AgentMessage.payload,
                        AgentMessage.search_text,
                    ).where(AgentMessage.message_id == message_out)
                )
            ).one()
            out_artifact_row = (
                await conn.execute(
                    sa.select(
                        AgentArtifact.summary,
                        AgentArtifact.body_ref,
                        AgentArtifact.result_payload,
                        AgentArtifact.result_digest,
                    ).where(AgentArtifact.artifact_id == artifact_out)
                )
            ).one()
            out_round_row = (
                await conn.execute(
                    sa.select(
                        AgentRound.frozen_manifest,
                        AgentRound.actual_input,
                        AgentRound.model_binding,
                        AgentRound.sdk_history_ref,
                    ).where(AgentRound.round_id == round_out)
                )
            ).one()
            leaked = 0
            for model, condition in (
                (AgentSession, AgentSession.session_id == session_in),
                (AgentExecution, AgentExecution.session_id == session_in),
                (AgentRound, AgentRound.execution_id == execution_in),
                (AgentMessage, AgentMessage.session_id == session_in),
                (AgentArtifact, AgentArtifact.session_id == session_in),
                (AgentOperation, AgentOperation.session_id == session_in),
                (AgentBodyPayload, AgentBodyPayload.owner_id.in_([artifact_in, message_in])),
            ):
                rows = (await conn.execute(sa.select(*model.__table__.columns).where(condition))).all()
                for row in rows:
                    values = dict(row._mapping)
                    if secret in str(values):
                        leaked += 1
                    payload_bytes = values.get("payload_bytes")
                    if isinstance(payload_bytes, bytes) and secret.encode("utf-8") in payload_bytes:
                        leaked += 1
    finally:
        await app_engine.dispose()

    assert touched["agent_message"] == 1
    assert touched["agent_artifact"] == 1
    assert touched["agent_round"] == 1
    assert touched["agent_operation"] == 1
    assert touched["agent_body_payload"] == 1
    assert touched["agent_session"] == 1
    assert touched["agent_asset_version"] == 0

    assert message_row.body_text is None
    assert message_row.body_ref is None
    assert message_row.payload is None
    assert message_row.search_text is None
    assert artifact_row.summary is None
    assert artifact_row.body_text is None
    assert artifact_row.body_ref is None
    assert artifact_row.result_payload is None
    assert artifact_row.result_digest == CLEANUP_TOMBSTONE_DIGEST
    # 已清理信号必须可见：read_artifact 依 cleaned_at 区分 body_cleaned 与 body_not_saved（R21）
    assert artifact_row.cleaned_at is not None
    assert round_row.frozen_manifest is None
    assert round_row.actual_input is None
    assert round_row.model_binding is None
    assert round_row.sdk_history_ref is None
    assert operation_row.result_payload is None
    assert operation_row.request_ref is None
    assert operation_row.result_ref is None
    assert sorted(remaining_bodies) == [body_out]
    assert out_message_row.body_ref == body_out
    assert out_message_row.payload == {"text": secret}
    assert out_message_row.search_text == secret
    assert out_artifact_row.summary == secret
    assert out_artifact_row.result_payload == {"text": secret}
    assert out_artifact_row.result_digest == "b" * 64
    assert out_round_row.frozen_manifest == {"goal": secret}
    assert out_round_row.model_binding == {"prompt": secret}
    assert leaked == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_structural_receipts_and_content_digests_cleared(real_embedded_pg) -> None:
    """E-06-5b：结构回执/载荷清空、可 NULL 摘要置 NULL、NOT NULL 摘要换 tombstone。

    覆盖 §10.7 表第 2—4 行：命令请求/回执、决定问题与答案 schema、同意载荷、
    操作交付清单/worker 引用、来源 locator 清空；执行意图、轮次冻结/实际、参数、
    来源作用域摘要置 NULL；成果与命令的 NOT NULL 内容摘要换固定 tombstone。
    身份/grant/phase/unknown/计量（``attempt_refs``）保原值，集合外原样。
    """

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)

    suffix = uuid.uuid4().hex[:8]
    secret = "私人原句-结构回执清理后不应可查"
    session_in, session_out = f"s-in-{suffix}", f"s-out-{suffix}"
    execution_in, execution_out = f"e-in-{suffix}", f"e-out-{suffix}"
    round_in, round_out = f"r-in-{suffix}", f"r-out-{suffix}"
    artifact_in, artifact_out = f"a-in-{suffix}", f"a-out-{suffix}"
    command_in, command_out = f"c-in-{suffix}", f"c-out-{suffix}"
    decision_in, decision_out = f"d-in-{suffix}", f"d-out-{suffix}"
    link_in, link_out = f"l-in-{suffix}", f"l-out-{suffix}"
    asset_src = f"asset-src-{suffix}"

    def _execution(execution_id: str, session_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "execution_id": execution_id,
            "session_id": session_id,
            "execution_kind": "root",
            "source_module": f"module-{execution_id}",
            "business_request_id": f"request-{execution_id}",
            "intent_digest": "e" * 64,
            "node_id": "root",
            "display_state": "complete",
            "goal_coverage": "full",
        }

    def _round(round_id: str, execution_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "round_id": round_id,
            "execution_id": execution_id,
            "round_number": 1,
            "workflow_id": f"wf-{round_id}",
            "workflow_code_version": "1",
            "run_epoch": 1,
            "input_revision": 1,
            "plan_revision": 1,
            "frozen_digest": "f" * 64,
            "actual_digest": "g" * 64,
        }

    def _artifact(artifact_id: str, session_id: str, execution_id: str, round_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "artifact_id": artifact_id,
            "artifact_version": 1,
            "session_id": session_id,
            "execution_id": execution_id,
            "round_id": round_id,
            "artifact_kind": "explanation",
            "input_revision": 1,
            "actual_input_digest": "a" * 64,
            "actual_model_ref": "model-in",
            "result_digest": "b" * 64,
            "source_available_state": "available",
        }

    def _command(command_id: str, session_id: str, execution_id: str, round_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "command_record_id": command_id,
            "client_command_id": f"client-{command_id}",
            "target_id": session_id,
            "command_kind": "submit",
            "session_id": session_id,
            "execution_id": execution_id,
            "round_id": round_id,
            "expected_revision": 1,
            "input_revision": 1,
            "input_digest": "1" * 64,
            "request_payload": {"text": secret},
            "commit_state": "committed",
            "delivery_state": "delivered",
            "consumption_state": "consumed",
            "receipt_payload": {"text": secret},
        }

    def _decision(decision_id: str, session_id: str, execution_id: str, round_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "decision_id": decision_id,
            "session_id": session_id,
            "execution_id": execution_id,
            "round_id": round_id,
            "node_id": "root",
            "decision_kind": "tool_permission",
            "question_payload": {"text": secret},
            "answer_schema": {"text": secret},
            "parameter_digest": "2" * 64,
            "source_scope_digest": "3" * 64,
            "grant_id": f"grant-{decision_id}",
            "grant_revision": 4,
            "grant_state": "granted",
            "presentation_batch_seq": 1,
            "data_consent_payload": {"text": secret},
        }

    def _link(link_id: str, consumer_session: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "link_id": link_id,
            "source_id": asset_src,
            "source_version": "1",
            "consumer_kind": "draft",
            "consumer_id": consumer_session,
            "consumer_version": "1",
            "session_id": consumer_session,
            "locator_payload": {"path": f"C:/private/{secret}"},
            "claim_ref": f"claim-{link_id}",
            "relation_kind": "accepted_input",
        }

    app_engine = create_test_engine(url)
    try:
        dao = AgentDao(app_engine)
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession),
                [
                    {"user_scope": _USER_SCOPE, "session_id": session_in, "title": secret},
                    {"user_scope": _USER_SCOPE, "session_id": session_out, "title": secret},
                ],
            )
            await conn.execute(
                sa.insert(AgentAssetVersion),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "asset_id": asset_src,
                        "asset_version": "1",
                        "asset_kind": "summary",
                        "display_name": secret,
                        "digest": "9" * 64,
                        "read_state": "ready",
                        "verification_state": "program_verified",
                    }
                ],
            )
            await conn.execute(
                sa.insert(AgentExecution),
                [_execution(execution_in, session_in), _execution(execution_out, session_out)],
            )
            await conn.execute(
                sa.insert(AgentRound),
                [_round(round_in, execution_in), _round(round_out, execution_out)],
            )
            await conn.execute(
                sa.insert(AgentArtifact),
                [
                    _artifact(artifact_in, session_in, execution_in, round_in),
                    _artifact(artifact_out, session_out, execution_out, round_out),
                ],
            )
            await conn.execute(
                sa.insert(AgentCommand),
                [
                    _command(command_in, session_in, execution_in, round_in),
                    _command(command_out, session_out, execution_out, round_out),
                ],
            )
            await conn.execute(
                sa.insert(AgentDecision),
                [
                    _decision(decision_in, session_in, execution_in, round_in),
                    _decision(decision_out, session_out, execution_out, round_out),
                ],
            )
            await conn.execute(
                sa.insert(AgentSourceLink),
                [_link(link_in, session_in), _link(link_out, session_out)],
            )
            await conn.execute(
                sa.insert(AgentOperation),
                [
                    {
                        "user_scope": _USER_SCOPE,
                        "operation_id": f"op-in-{suffix}",
                        "session_id": session_in,
                        "execution_id": execution_in,
                        "owner_epoch": 1,
                        "run_epoch": 1,
                        "phase": "result_saved",
                        "effect_state": "confirmed",
                        "io_state": "known",
                        "parameter_digest": "4" * 64,
                        "worker_refs": {"worker": secret},
                        "data_consent_claims": {"claim": secret},
                        "attempt_refs": {"attempt": "attempt-1"},
                    }
                ],
            )

        touched = await dao.tombstone_cleanup_text(_USER_SCOPE, session_ids=[session_in])

        async with app_engine.connect() as conn:
            execution_row = (
                await conn.execute(
                    sa.select(AgentExecution.intent_digest).where(AgentExecution.execution_id == execution_in)
                )
            ).one()
            execution_out_row = (
                await conn.execute(
                    sa.select(AgentExecution.intent_digest).where(AgentExecution.execution_id == execution_out)
                )
            ).one()
            round_row = (
                await conn.execute(
                    sa.select(AgentRound.frozen_digest, AgentRound.actual_digest).where(AgentRound.round_id == round_in)
                )
            ).one()
            round_out_row = (
                await conn.execute(
                    sa.select(AgentRound.frozen_digest, AgentRound.actual_digest).where(
                        AgentRound.round_id == round_out
                    )
                )
            ).one()
            artifact_row = (
                await conn.execute(
                    sa.select(AgentArtifact.actual_input_digest, AgentArtifact.result_digest).where(
                        AgentArtifact.artifact_id == artifact_in
                    )
                )
            ).one()
            artifact_out_row = (
                await conn.execute(
                    sa.select(AgentArtifact.actual_input_digest, AgentArtifact.result_digest).where(
                        AgentArtifact.artifact_id == artifact_out
                    )
                )
            ).one()
            command_row = (
                await conn.execute(
                    sa.select(
                        AgentCommand.request_payload,
                        AgentCommand.receipt_payload,
                        AgentCommand.input_digest,
                        AgentCommand.commit_state,
                    ).where(AgentCommand.command_record_id == command_in)
                )
            ).one()
            command_out_row = (
                await conn.execute(
                    sa.select(
                        AgentCommand.request_payload,
                        AgentCommand.receipt_payload,
                        AgentCommand.input_digest,
                    ).where(AgentCommand.command_record_id == command_out)
                )
            ).one()
            decision_row = (
                await conn.execute(
                    sa.select(
                        AgentDecision.question_payload,
                        AgentDecision.answer_schema,
                        AgentDecision.parameter_digest,
                        AgentDecision.source_scope_digest,
                        AgentDecision.data_consent_payload,
                        AgentDecision.grant_id,
                        AgentDecision.grant_revision,
                        AgentDecision.grant_state,
                    ).where(AgentDecision.decision_id == decision_in)
                )
            ).one()
            decision_out_row = (
                await conn.execute(
                    sa.select(AgentDecision.question_payload, AgentDecision.answer_schema).where(
                        AgentDecision.decision_id == decision_out
                    )
                )
            ).one()
            operation_row = (
                await conn.execute(
                    sa.select(
                        AgentOperation.parameter_digest,
                        AgentOperation.worker_refs,
                        AgentOperation.data_consent_claims,
                        AgentOperation.attempt_refs,
                    ).where(AgentOperation.session_id == session_in)
                )
            ).one()
            link_row = (
                await conn.execute(
                    sa.select(
                        AgentSourceLink.locator_payload,
                        AgentSourceLink.claim_ref,
                        AgentSourceLink.relation_kind,
                    ).where(AgentSourceLink.link_id == link_in)
                )
            ).one()
            link_out_row = (
                await conn.execute(
                    sa.select(AgentSourceLink.locator_payload, AgentSourceLink.claim_ref).where(
                        AgentSourceLink.link_id == link_out
                    )
                )
            ).one()
            leaked = 0
            for model, condition in (
                (AgentExecution, AgentExecution.session_id == session_in),
                (AgentDecision, AgentDecision.session_id == session_in),
                (AgentCommand, AgentCommand.session_id == session_in),
                (AgentSourceLink, AgentSourceLink.session_id == session_in),
            ):
                rows = (await conn.execute(sa.select(*model.__table__.columns).where(condition))).all()
                for row in rows:
                    if secret in str(dict(row._mapping)):
                        leaked += 1
    finally:
        await app_engine.dispose()

    assert touched["agent_execution"] == 1
    assert touched["agent_decision"] == 1
    assert touched["agent_command"] == 1
    assert touched["agent_source_link"] == 1
    assert touched["agent_session"] == 1
    assert touched["agent_asset_version"] == 0
    assert touched["agent_body_payload"] == 0
    assert touched["agent_message"] == 0

    assert execution_row.intent_digest is None
    assert execution_out_row.intent_digest == "e" * 64
    assert round_row.frozen_digest is None
    assert round_row.actual_digest is None
    assert round_out_row.frozen_digest == "f" * 64
    assert round_out_row.actual_digest == "g" * 64

    assert artifact_row.actual_input_digest == CLEANUP_TOMBSTONE_DIGEST
    assert artifact_row.result_digest == CLEANUP_TOMBSTONE_DIGEST
    assert artifact_out_row.actual_input_digest == "a" * 64
    assert artifact_out_row.result_digest == "b" * 64

    assert command_row.request_payload is None
    assert command_row.receipt_payload is None
    assert command_row.input_digest == CLEANUP_TOMBSTONE_DIGEST
    assert command_row.commit_state == "committed"
    assert command_out_row.request_payload == {"text": secret}
    assert command_out_row.receipt_payload == {"text": secret}
    assert command_out_row.input_digest == "1" * 64

    assert decision_row.question_payload is None
    assert decision_row.answer_schema is None
    assert decision_row.parameter_digest is None
    assert decision_row.source_scope_digest is None
    assert decision_row.data_consent_payload is None
    assert decision_row.grant_id == f"grant-{decision_in}"
    assert decision_row.grant_revision == 4
    assert decision_row.grant_state == "granted"
    assert decision_out_row.question_payload == {"text": secret}
    assert decision_out_row.answer_schema == {"text": secret}

    assert operation_row.parameter_digest is None
    assert operation_row.worker_refs is None
    assert operation_row.data_consent_claims is None
    assert operation_row.attempt_refs == {"attempt": "attempt-1"}

    assert link_row.locator_payload is None
    assert link_row.claim_ref == f"claim-{link_in}"
    assert link_row.relation_kind == "accepted_input"
    assert link_out_row.locator_payload == {"path": f"C:/private/{secret}"}
    assert link_out_row.claim_ref == f"claim-{link_out}"
    assert leaked == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_clears_delivery_manifest_and_reports_cleaned(real_embedded_pg) -> None:
    """清理后回读语义：交付回执正文清除并报 ``manifest_cleaned``（不撒谎成「从未交付」），
    已清结果读回报 ``result_cleaned`` 而非坏行。"""

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)

    suffix = uuid.uuid4().hex[:8]
    session_id = f"s-del-{suffix}"
    execution_id = f"e-del-{suffix}"
    round_id = f"r-del-{suffix}"
    delivery_op = f"op-delivery-{suffix}"
    result_op = f"op-result-{suffix}"
    delivery_command = ArtifactDeliveryCommand(
        command=CommandRef(
            user_scope=_USER_SCOPE,
            source_module="module-delivery",
            business_request_id=f"req-{suffix}",
            command_id=f"cmd-{suffix}",
            expected_revision=0,
        ),
        artifact_id=f"artifact-{suffix}",
        artifact_version=1,
        format="md",
        format_rule_version=1,
        delivery_id="delivery-placeholder",
        target=DeliveryTarget(
            target_path="C:\\Users\\admin\\Desktop\\result.md",
            expected_existing=False,
            expected_existing_digest=None,
            overwrite_confirmed=False,
        ),
        expected_delivery_revision=0,
    )
    manifest = DeliveryManifest(
        delivery_id=derive_delivery_id(delivery_command),
        command=delivery_command.command,
        artifact_id=delivery_command.artifact_id,
        artifact_version=1,
        artifact_digest="d" * 64,
        format="md",
        format_rule_version=1,
        target=delivery_command.target,
        phase="manifest_saved",
        source_state="available",
        output_digest=None,
        output_bytes=None,
        staged_path=None,
        verified_at="2026-11-15T08:00:00.000000Z",
        error_code=None,
    )

    app_engine = create_test_engine(url)
    try:
        dao = AgentDao(app_engine)
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession).values(user_scope=_USER_SCOPE, session_id=session_id, title="交付会话")
            )
            await conn.execute(
                sa.insert(AgentExecution).values(
                    user_scope=_USER_SCOPE,
                    execution_id=execution_id,
                    session_id=session_id,
                    execution_kind="root",
                    source_module=f"module-{execution_id}",
                    business_request_id=f"request-{execution_id}",
                    intent_digest="e" * 64,
                    node_id="root",
                    display_state="complete",
                    goal_coverage="full",
                )
            )
            await conn.execute(
                sa.insert(AgentRound).values(
                    user_scope=_USER_SCOPE,
                    round_id=round_id,
                    execution_id=execution_id,
                    round_number=1,
                    workflow_id=f"wf-{round_id}",
                    workflow_code_version="1",
                    run_epoch=1,
                    input_revision=1,
                    plan_revision=1,
                )
            )
            # 交付写路径会把 manifest 的 artifact 身份写进 operation（FK 约束），先种产物行。
            await conn.execute(
                sa.insert(AgentArtifact).values(
                    user_scope=_USER_SCOPE,
                    artifact_id=delivery_command.artifact_id,
                    artifact_version=1,
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    artifact_kind="explanation",
                    input_revision=1,
                    actual_input_digest="a" * 64,
                    actual_model_ref="model-delivery",
                    result_digest="b" * 64,
                    source_available_state="available",
                )
            )
            await conn.execute(
                sa.insert(AgentOperation).values(
                    user_scope=_USER_SCOPE,
                    operation_id=delivery_op,
                    session_id=session_id,
                    execution_id=execution_id,
                    owner_epoch=1,
                    run_epoch=1,
                    phase="result_saved",
                    effect_state="confirmed",
                    io_state="known",
                )
            )
            await conn.execute(
                sa.insert(AgentOperation).values(
                    user_scope=_USER_SCOPE,
                    operation_id=result_op,
                    session_id=session_id,
                    execution_id=execution_id,
                    owner_epoch=1,
                    run_epoch=1,
                    phase="result_saved",
                    effect_state="confirmed",
                    io_state="returned",
                    result_payload={"rows": [1, 2]},
                )
            )

        # 交付事实经真实写路径落库（同时写 artifact 身份），清理后才可报「已清理」。
        await dao.save_delivery_phase(
            user_scope=_USER_SCOPE, operation_id=delivery_op, expected_revision=1, manifest=manifest
        )

        touched = await dao.tombstone_cleanup_text(_USER_SCOPE, session_ids=[session_id])

        delivery = await dao.read_delivery(user_scope=_USER_SCOPE, operation_id=delivery_op)
        read = await dao.read_operation(OperationReadQuery(operation_id=result_op), user_scope=_USER_SCOPE)
    finally:
        await app_engine.dispose()

    assert touched["agent_session"] == 1
    assert touched["agent_operation"] == 2
    assert delivery.manifest is None
    assert delivery.manifest_cleaned is True
    assert read.phase == "result_saved"
    assert read.result_payload is None
    assert read.result_text is None
    assert read.result_available is False
    assert read.unavailable_reason == "result_cleaned"


@pytest.mark.asyncio(loop_scope="session")
async def test_asset_scoped_source_links_clear_locator(real_embedded_pg) -> None:
    """资产闭包分支：非 draft 消费者（``session_id`` 为 NULL）只能靠源/派生资产键命中。

    仅以资产集合执行清理：源资产在集合内的边、派生资产在集合内的边都要清 locator；
    源与派生都不在集合内的边保持原样，且不误改会话域。
    """

    url = real_embedded_pg.url
    await _reset_and_migrate_app_schema(url)

    suffix = uuid.uuid4().hex[:8]
    secret = "私人原句-资产闭包清理后不应可查"
    session_id = f"s-link-{suffix}"
    execution_id = f"e-link-{suffix}"
    asset_in, asset_out = f"asset-in-{suffix}", f"asset-out-{suffix}"
    link_source, link_derived, link_out = (
        f"l-src-{suffix}",
        f"l-derived-{suffix}",
        f"l-out-{suffix}",
    )

    def _asset(asset_id: str) -> dict[str, object]:
        return {
            "user_scope": _USER_SCOPE,
            "asset_id": asset_id,
            "asset_version": "1",
            "asset_kind": "summary",
            "display_name": secret,
            "digest": "9" * 64,
            "read_state": "ready",
            "verification_state": "program_verified",
        }

    app_engine = create_test_engine(url)
    try:
        dao = AgentDao(app_engine)
        async with app_engine.begin() as conn:
            await conn.execute(
                sa.insert(AgentSession).values(user_scope=_USER_SCOPE, session_id=session_id, title=secret)
            )
            await conn.execute(
                sa.insert(AgentExecution).values(
                    user_scope=_USER_SCOPE,
                    execution_id=execution_id,
                    session_id=session_id,
                    execution_kind="root",
                    source_module=f"module-{execution_id}",
                    business_request_id=f"request-{execution_id}",
                    intent_digest="e" * 64,
                    node_id="root",
                    display_state="complete",
                    goal_coverage="full",
                )
            )
            await conn.execute(sa.insert(AgentAssetVersion), [_asset(asset_in), _asset(asset_out)])

            def _link(
                link_id: str,
                *,
                source_id: str,
                consumer_kind: str,
                consumer_id: str,
                consumer_version: str,
                execution: str | None = None,
                derived: str | None = None,
            ) -> dict[str, object]:
                return {
                    "user_scope": _USER_SCOPE,
                    "link_id": link_id,
                    "source_id": source_id,
                    "source_version": "1",
                    "consumer_kind": consumer_kind,
                    "consumer_id": consumer_id,
                    "consumer_version": consumer_version,
                    "session_id": None,
                    "execution_id": execution,
                    "round_id": None,
                    "message_id": None,
                    "artifact_id": None,
                    "artifact_version": None,
                    "derived_asset_id": derived,
                    "derived_asset_version": "1" if derived is not None else None,
                    "locator_payload": {"path": f"C:/private/{secret}"},
                    "relation_kind": "accepted_input",
                }

            await conn.execute(
                sa.insert(AgentSourceLink),
                [
                    _link(
                        link_source,
                        source_id=asset_in,
                        consumer_kind="execution",
                        consumer_id=execution_id,
                        consumer_version="1",
                        execution=execution_id,
                    ),
                    _link(
                        link_derived,
                        source_id=asset_out,
                        consumer_kind="asset",
                        consumer_id=asset_in,
                        consumer_version="1",
                        derived=asset_in,
                    ),
                    _link(
                        link_out,
                        source_id=asset_out,
                        consumer_kind="execution",
                        consumer_id=execution_id,
                        consumer_version="2",
                        execution=execution_id,
                    ),
                ],
            )

        touched = await dao.tombstone_cleanup_text(_USER_SCOPE, asset_keys=[(asset_in, "1")])

        async with app_engine.connect() as conn:
            rows = {
                row.link_id: row.locator_payload
                for row in (
                    await conn.execute(
                        sa.select(AgentSourceLink.link_id, AgentSourceLink.locator_payload).where(
                            AgentSourceLink.user_scope == _USER_SCOPE,
                            AgentSourceLink.link_id.in_([link_source, link_derived, link_out]),
                        )
                    )
                ).all()
            }
    finally:
        await app_engine.dispose()

    assert touched["agent_source_link"] == 2
    assert touched["agent_session"] == 0
    assert touched["agent_asset_version"] == 1
    assert rows[link_source] is None
    assert rows[link_derived] is None
    assert rows[link_out] == {"path": f"C:/private/{secret}"}
