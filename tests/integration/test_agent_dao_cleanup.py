"""E-06-2c 联合清理真实执行集成（真 sidecar PG，DD §10.7）。

验证：
- 首次预检：九类原生目标逐域命中，主键域都查不到的 ID 明确报缺失（查不到≠已清）；
- 真删除：DBOS 公开 ``Client.delete_workflows``（只删系统，级联清理事件/通知/流）
  → ``agent_ds.datasource_outputs`` 常量表参数化 DML → 逐域查回全 0；
- 集合外不变：同库集合外 workflow 的九域行保持原样；
- 重试幂等：已有可靠 fence 的重复删除不报错、仍查回 0；
- 应用域 NOT NULL tombstone：``agent_session.title`` / ``agent_asset_version``
  ``display_name``+``digest`` 换固定占位，可 NULL 的草稿/来源/元数据/正文引用清除，
  集合外会话/资产不变。

标记：``pytest.mark.integration`` + ``pytest.mark.embedded_real``。
SQL 合规（R4）：全部经静态 ``sa.text()`` 常量承载，身份经命名参数绑定。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from dbos import DBOSClient
from sqlalchemy import text

from core.agent_types import CLEANUP_TOMBSTONE, CLEANUP_TOMBSTONE_DIGEST
from data.persistence.agent_migrator import run_agent_runtime_migrations
from data.persistence.agent_runtime_store import AgentRuntimeNativeStore
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentAssetVersion, AgentSession
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
    setup_engine = create_test_engine(url)
    try:
        async with setup_engine.begin() as conn:
            for statement in _RESET_APP_SCHEMA:
                await conn.execute(statement)
    finally:
        await setup_engine.dispose()
    alembic_cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

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

    assert touched == {"agent_session": 1, "agent_asset_version": 1}
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
