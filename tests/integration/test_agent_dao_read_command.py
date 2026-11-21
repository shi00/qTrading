"""AgentDao.read_command 集成测试（DD §4.2，F-01 前置）。

真 embedded PG 上验证命令原事实读取：

- 受理后读回保存/投递/消费三态分列（committed/not_delivered/not_consumed，INV-06）；
- 其他用户域 / 引用与调用上下文不一致 / 不存在 → INPUT_INVALID（不因知道键放行）；
- 同一命令键跨 target 合法并存时按身份（意图键/预期版/准确 target）查回原记录，
  不做「先按命令键多行即拒」（检视 D20）；按 ref 全部可用身份过滤后仍多行才算真
  歧义 → INPUT_INVALID（不静默择一）；丢回执/重启后仍可查回；
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
from data.persistence.models import AgentCommand, AgentSession
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


def _ref(
    user_scope: str = "u1",
    client_command_id: str = "client-1",
    source_module: str = "ui",
    business_request_id: str = "req-1",
    expected_revision: int = 0,
) -> CommandRef:
    return CommandRef(
        user_scope=user_scope,
        source_module=source_module,
        business_request_id=business_request_id,
        command_id=client_command_id,
        expected_revision=expected_revision,
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
    async def test_source_module_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        """回执必须与传入引用同源：同 command_id 但 source_module 不符 → 拒绝回显。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(source_module="other"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "does not match stored command" in excinfo.value.sanitized_message
        assert "source_module" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_business_request_id_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(business_request_id="req-9"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "does not match stored command" in excinfo.value.sanitized_message
        assert "business_request_id" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_expected_revision_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        """预期版不符同样拒绝：不以「命令存在」掩盖调用方引用与存行不一致。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(expected_revision=5), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "does not match stored command" in excinfo.value.sanitized_message
        assert "expected_revision" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_command_without_intent_key_verifies_only_expected_revision(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """无根意图键的存行（如链到叶执行）不比对来源/业务请求：无权威事实不猜拒。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope="u1",
                    command_record_id="cmd-leaf",
                    client_command_id="client-leaf",
                    target_id="s1",
                    command_kind="answer",
                    expected_revision=2,
                    input_digest="0" * 64,
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                )
            )
        ref = CommandRef(
            user_scope="u1",
            source_module="api",
            business_request_id="other-request",
            command_id="client-leaf",
            expected_revision=2,
        )

        receipt = await dao.read_command(ref, user_scope="u1")

        assert receipt.command == ref
        assert receipt.current_revision == 1
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_key_across_targets_resolved_by_intent_identity(self, real_embedded_pg: ConnectionInfo) -> None:
        """同一命令键在不同合法 target 下并存：按意图键各自查回，不误判歧义（D20）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u1", "s2")
        await dao.accept_request(_request(session_id="s1"))
        await dao.accept_request(_request(session_id="s2", business_request_id="req-2"))

        first = await dao.read_command(_ref(business_request_id="req-1"), user_scope="u1")
        second = await dao.read_command(_ref(business_request_id="req-2"), user_scope="u1")

        assert first.target_id == "s1"
        assert second.target_id == "s2"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_key_across_targets_reads_back_after_restart(self, real_embedded_pg: ConnectionInfo) -> None:
        """丢回执/重启：新 DAO/新连接仍按身份查回原记录，不因历史多行残留变永久歧义。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u1", "s2")
        await dao.accept_request(_request(session_id="s1"))
        await dao.accept_request(_request(session_id="s2", business_request_id="req-2"))
        await dao.engine.dispose()  # type: ignore[union-attr]

        restarted = AgentDao(create_test_engine(real_embedded_pg.url))
        first = await restarted.read_command(_ref(business_request_id="req-1"), user_scope="u1")
        second = await restarted.read_command(_ref(business_request_id="req-2"), user_scope="u1")

        assert first.target_id == "s1"
        assert second.target_id == "s2"
        await restarted.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_target_repeat_submission_reads_back_once(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 target 同键重传只留原记录：丢回执后再查仍唯一命中。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        accepted = await dao.accept_request(_request())
        await dao.accept_request(_request())

        receipt = await dao.read_command(_ref(), user_scope="u1")

        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            stored = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentCommand)
                    .where(
                        AgentCommand.user_scope == "u1",
                        AgentCommand.client_command_id == "client-1",
                    )
                )
            ).scalar_one()
        assert stored == 1
        assert receipt.target_id == accepted.session_id
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_identical_identity_across_targets_is_ambiguous(self, real_embedded_pg: ConnectionInfo) -> None:
        """按 ref 全部可用身份过滤后仍多行 → 真歧义，拒绝静默择一。"""

        dao = await _setup(real_embedded_pg.url)
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            for record_id, target in (("cmd-dup-1", "s1"), ("cmd-dup-2", "s2")):
                await conn.execute(
                    sa.insert(AgentCommand).values(
                        user_scope="u1",
                        command_record_id=record_id,
                        client_command_id="client-dup",
                        target_id=target,
                        command_kind="answer",
                        expected_revision=2,
                        input_digest="0" * 64,
                        commit_state=CommitState.COMMITTED.value,
                        delivery_state=DeliveryState.NOT_DELIVERED.value,
                        consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    )
                )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_command(_ref(client_command_id="client-dup", expected_revision=2), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "ambiguous" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_exact_target_resolves_residual_ambiguity(self, real_embedded_pg: ConnectionInfo) -> None:
        """已知准确 target 时按该 target 下的命令记录定位（存储 PK 维度）。"""

        dao = await _setup(real_embedded_pg.url)
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            for record_id, target in (("cmd-dup-1", "s1"), ("cmd-dup-2", "s2")):
                await conn.execute(
                    sa.insert(AgentCommand).values(
                        user_scope="u1",
                        command_record_id=record_id,
                        client_command_id="client-dup",
                        target_id=target,
                        command_kind="answer",
                        expected_revision=2,
                        input_digest="0" * 64,
                        commit_state=CommitState.COMMITTED.value,
                        delivery_state=DeliveryState.NOT_DELIVERED.value,
                        consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    )
                )

        receipt = await dao.read_command(
            _ref(client_command_id="client-dup", expected_revision=2), user_scope="u1", target_id="s2"
        )

        assert receipt.target_id == "s2"
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
