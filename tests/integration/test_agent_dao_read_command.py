"""AgentDao.read_command 集成测试（DD §4.2，F-01 前置）。

真 embedded PG 上验证命令原事实读取：

- 受理后读回保存/投递/消费三态分列（committed/not_delivered/not_consumed，INV-06）；
- 其他用户域 / 引用与调用上下文不一致 / 不存在 → INPUT_INVALID（不因知道键放行）；
- 同键跨 target 复用产生歧义 → INPUT_INVALID（不静默择一）；
- 存储错误码非冻结枚举 → OPERATION_UNKNOWN（fail-closed，R21）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import re

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryState,
    Mode,
    Purpose,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_WIRE_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_session(dao: AgentDao, user_scope: str, session_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
            )
        )


def _request(
    user_scope: str = "u1",
    session_id: str = "s1",
    client_command_id: str = "client-1",
    business_request_id: str = "req-1",
) -> AcceptRequest:
    manifest = json_doc(
        {
            "schema_version": 1,
            "source_module": "ui",
            "business_request_id": business_request_id,
            "mode": "discuss",
            "purpose": "concept",
            "text": "研究三家公司",
        }
    )
    return AcceptRequest(
        user_scope=user_scope,
        session_id=session_id,
        source_module="ui",
        business_request_id=business_request_id,
        client_command_id=client_command_id,
        expected_revision=0,
        node_id="root",
        workflow_id=f"wf-{session_id}-{client_command_id}",
        mode=Mode.DISCUSS,
        purpose=Purpose.CONCEPT,
        text="研究三家公司",
        manifest_payload=manifest,
        source_display_name="用户消息",
    )


def _ref(user_scope: str = "u1", client_command_id: str = "client-1") -> CommandRef:
    return CommandRef(
        user_scope=user_scope,
        source_module="ui",
        business_request_id="req-1",
        command_id=client_command_id,
        expected_revision=0,
    )


class TestReadCommandFacts:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_reads_initial_states_after_accept(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        accepted = await dao.accept_request(_request())

        receipt = await dao.read_command(_ref(), user_scope="u1")

        assert receipt.command == _ref()
        assert receipt.target_id == "s1"
        assert receipt.commit_state is CommitState.COMMITTED
        assert receipt.delivery_state is DeliveryState.NOT_DELIVERED
        assert receipt.consumption_state is ConsumptionState.NOT_CONSUMED
        assert receipt.error_code is None
        assert receipt.next_action is None
        assert _WIRE_TS_RE.match(receipt.updated_at)
        assert receipt.current_revision >= 1
        assert receipt.target_id == accepted.session_id
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_user_scope_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(), user_scope="u2")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_ref_scope_mismatch_rejected_before_lookup(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(user_scope="u1"), user_scope="u2")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "outside caller user scope" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_command_id_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(client_command_id="missing"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not found in user scope" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_key_across_targets_is_ambiguous(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u1", "s2")
        await dao.accept_request(_request(session_id="s1"))
        await dao.accept_request(_request(session_id="s2", business_request_id="req-2"))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "ambiguous" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unreadable_stored_error_code_is_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(sa.text("UPDATE agent_command SET error_code = 'BOGUS'"))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]
