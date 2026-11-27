"""逐调用审批闭环（D7-01）的真实 embedded PG 集成验证。

覆盖：PENDING 等待本次人答 → 批准后仅派发一次且重放不二次外发；拒绝/已授后
撤销/超时未答分别得到 ``permission_rejected`` / ``permission_revoked`` /
``approval_timeout``，不再混同为 ``source_disabled``。

工具调用门使用真实 :class:`MCPToolDispatcher` 与真实 :class:`AgentDao`
（``DaoToolGrantPort``）；仅 MCP 会话/管理器为本地替身（不发起真实网络 IO）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AcceptRequest,
    BindActualInputRequest,
    ConsumeAnswerRequest,
    GrantRevisionRequest,
    MCPBudgetProfile,
    MCPServiceIdentity,
    Mode,
    Purpose,
    SaveAnswerRequest,
    ToolGrantState,
    derive_round_id,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentDecision, AgentOperation
from services.agent_service.mcp import (
    MCPAuditEvidenceRecheck,
    MCPBudgetGuard,
    MCPToolDescriptor,
)
from services.agent_service.mcp_tools import (
    MCPToolDispatcher,
    ToolBoundaryAttestation,
    ToolCallState,
)
from services.agent_service.tool_grants import DaoToolGrantPort
from services.agent_service.tool_runtime import ToolApprovalCoordinator, ToolRunScope, ToolRuntime
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_IDENTITY = MCPServiceIdentity(service_id="svc-1", config_version=1)
_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
)
_BOUNDARY_DIGEST = "d" * 64


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=10_000,
        decoded_bytes=10_000,
        structure_depth=16,
        catalog_items=10,
        catalog_bytes=10_000,
        schema_bytes=10_000,
        single_result_bytes=10_000,
        retention_bytes=10_000,
    )


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        self.calls.append((name, arguments, deadline_s))
        return {"answer": 1}


class _Manager:
    """MCP 管理器替身：只提供调用门所需的内存取证，不建真实连接。"""

    def __init__(self, session: _Session) -> None:
        self._session = session
        self._catalog = (MCPToolDescriptor("search", _SCHEMA),)
        self._guard = MCPBudgetGuard(_budget(), identity=_IDENTITY.stable_key())

    async def recheck_audit_evidence(self, identity: MCPServiceIdentity) -> MCPAuditEvidenceRecheck:
        return MCPAuditEvidenceRecheck(admitted=True, reason=None, trust_limited=False)

    def frozen_catalog(self, identity: MCPServiceIdentity) -> tuple[MCPToolDescriptor, ...]:
        return self._catalog

    def connection_generation(self, identity: MCPServiceIdentity) -> int:
        return 1

    def audit_tool_scope(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ("search",)

    def session(self, identity: MCPServiceIdentity) -> _Session:
        return self._session

    def tool_deadline_s(self, identity: MCPServiceIdentity) -> int:
        return 5

    def budget_guard(self, identity: MCPServiceIdentity) -> MCPBudgetGuard:
        return self._guard

    def register_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        return None

    def release_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        return None

    def mark_in_flight_unknown(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        return None


class _Boundary:
    async def attest(self) -> ToolBoundaryAttestation:
        return ToolBoundaryAttestation(_BOUNDARY_DIGEST, None)


class _Validator:
    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None:
        return None


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
    from data.persistence.models import AgentSession

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
            )
        )


def _request(user_scope: str = "u1", session_id: str = "s1") -> AcceptRequest:
    return AcceptRequest(
        user_scope=user_scope,
        session_id=session_id,
        source_module="ui",
        business_request_id="req-1",
        client_command_id="client-1",
        expected_revision=0,
        node_id="root",
        workflow_id="wf-1",
        mode=Mode.DISCUSS,
        purpose=Purpose.CONCEPT,
        text="研究三家公司",
        manifest_payload=json_doc(
            {
                "schema_version": 1,
                "source_module": "ui",
                "business_request_id": "req-1",
                "mode": "discuss",
                "purpose": "concept",
                "text": "研究三家公司",
            }
        ),
        source_display_name="用户消息",
    )


async def _full_setup(real_embedded_pg: ConnectionInfo) -> tuple[AgentDao, str, str]:
    dao = await _setup(real_embedded_pg.url)
    await _seed_session(dao, "u1", "s1")
    receipt = await dao.accept_request(_request())
    bind_receipt = await dao.bind_actual_input(
        BindActualInputRequest(
            user_scope="u1",
            session_id="s1",
            execution_id=receipt.execution_id,
            node_id="root",
            workflow_id=receipt.workflow_id,
            workflow_code_version="wcv-1",
            run_epoch=1,
            expected_input_revision=1,
            frozen_manifest=json_doc(
                {
                    "payload_version": 2,
                    "execution_id": receipt.execution_id,
                    "round_id": derive_round_id(
                        user_scope="u1", execution_id=receipt.execution_id, workflow_id=receipt.workflow_id
                    ),
                    "binding_identity": {
                        "user_scope": "u1",
                        "session_id": "s1",
                        "workflow_code_version": "wcv-1",
                        "run_epoch": 1,
                        "expected_input_revision": 1,
                    },
                }
            ),
            actual_input=json_doc({"schema_version": 1, "kind": "actual_input"}),
        )
    )
    return dao, receipt.execution_id, bind_receipt.round_id


def _runtime(
    dao: AgentDao,
    session: _Session,
    *,
    execution_id: str,
    round_id: str,
    coordinator: ToolApprovalCoordinator,
) -> ToolRuntime:
    manager = _Manager(session)
    boundary = _Boundary()
    dispatcher = MCPToolDispatcher(
        manager=manager,  # type: ignore[arg-type]  # 仅本地会话替身
        validator=_Validator(),  # type: ignore[arg-type]
        grants=DaoToolGrantPort(dao),
        boundary=boundary,  # type: ignore[arg-type]
    )
    return ToolRuntime(
        tools=(),
        dispatcher=dispatcher,
        catalog=manager.frozen_catalog(_IDENTITY),
        connection_generation=manager.connection_generation(_IDENTITY),
        identity=_IDENTITY,
        scope=ToolRunScope(
            user_scope="u1",
            session_id="s1",
            execution_id=execution_id,
            round_id=round_id,
            node_id="root",
        ),
        role_tools=("search",),
        request_tools=("search",),
        permission_tools=("search",),
        service_tools=("search",),
        audit_scope=("search",),
        dao=dao,
        boundary=boundary,  # type: ignore[arg-type]
        approvals=coordinator,
        approval_timeout_s=2.0,
    )


async def _wait_decision(dao: AgentDao, *, execution_id: str, round_id: str) -> str:
    for _ in range(300):
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            row = (
                await conn.execute(
                    sa.select(AgentDecision.decision_id).where(
                        AgentDecision.user_scope == "u1",
                        AgentDecision.execution_id == execution_id,
                        AgentDecision.round_id == round_id,
                    )
                )
            ).first()
        if row is not None:
            return row.decision_id
        await asyncio.sleep(0.01)
    raise AssertionError("decision was not persisted")


async def _answer(
    dao: AgentDao,
    coordinator: ToolApprovalCoordinator,
    *,
    decision_id: str,
    execution_id: str,
    round_id: str,
    client_command_id: str,
    approve: bool,
) -> None:
    receipt = await dao.save_answer(
        SaveAnswerRequest(
            user_scope="u1",
            decision_id=decision_id,
            client_command_id=client_command_id,
            expected_decision_revision=1,
            expected_execution_id=execution_id,
            expected_round_id=round_id,
            answer_payload=json_doc({"schema_version": 1, "approve": approve}),
        )
    )
    await dao.consume_answer(ConsumeAnswerRequest(user_scope="u1", command_record_id=receipt.command_record_id))
    coordinator.notify_consumed(decision_id)


async def _operation(dao: AgentDao, operation_id: str) -> AgentOperation:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]
        return (
            await conn.execute(sa.select(AgentOperation).where(AgentOperation.operation_id == operation_id))
        ).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_timeout_denies_with_approval_timeout(real_embedded_pg: ConnectionInfo) -> None:
    dao, execution_id, round_id = await _full_setup(real_embedded_pg)
    session = _Session()
    runtime = _runtime(
        dao,
        session,
        execution_id=execution_id,
        round_id=round_id,
        coordinator=ToolApprovalCoordinator(),
    )
    runtime = replace(runtime, approval_timeout_s=0.02)

    outcome = await asyncio.wait_for(
        runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-timeout"),
        timeout=5,
    )

    assert outcome.state is ToolCallState.FAILED
    assert outcome.result == {"state": "denied", "reason": "approval_timeout"}
    assert session.calls == []
    operation = await _operation(dao, outcome.operation_id or "")
    assert operation.phase == "failed_confirmed"
    assert operation.effect_state == "not_dispatched"


@pytest.mark.asyncio(loop_scope="session")
async def test_rejection_denies_with_permission_rejected(real_embedded_pg: ConnectionInfo) -> None:
    dao, execution_id, round_id = await _full_setup(real_embedded_pg)
    session = _Session()
    coordinator = ToolApprovalCoordinator()
    runtime = _runtime(dao, session, execution_id=execution_id, round_id=round_id, coordinator=coordinator)

    task = asyncio.create_task(
        runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-reject")
    )
    decision_id = await _wait_decision(dao, execution_id=execution_id, round_id=round_id)
    await _answer(
        dao,
        coordinator,
        decision_id=decision_id,
        execution_id=execution_id,
        round_id=round_id,
        client_command_id="ans-reject",
        approve=False,
    )
    outcome = await asyncio.wait_for(task, timeout=5)

    assert outcome.state is ToolCallState.FAILED
    assert outcome.result == {"state": "denied", "reason": "permission_rejected"}
    assert session.calls == []


@pytest.mark.asyncio(loop_scope="session")
async def test_approval_dispatches_once_and_replay_is_not_resent(real_embedded_pg: ConnectionInfo) -> None:
    dao, execution_id, round_id = await _full_setup(real_embedded_pg)
    session = _Session()
    coordinator = ToolApprovalCoordinator()
    runtime = _runtime(dao, session, execution_id=execution_id, round_id=round_id, coordinator=coordinator)

    task = asyncio.create_task(
        runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-approve")
    )
    decision_id = await _wait_decision(dao, execution_id=execution_id, round_id=round_id)
    await _answer(
        dao,
        coordinator,
        decision_id=decision_id,
        execution_id=execution_id,
        round_id=round_id,
        client_command_id="ans-approve",
        approve=True,
    )
    first = await asyncio.wait_for(task, timeout=5)

    assert first.state is ToolCallState.COMPLETED
    assert len(session.calls) == 1
    operation = await _operation(dao, first.operation_id or "")
    assert operation.phase == "result_saved"

    replay = await asyncio.wait_for(
        runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-approve"),
        timeout=5,
    )

    assert replay.state is ToolCallState.UNKNOWN
    assert len(session.calls) == 1
    after = await _operation(dao, replay.operation_id or "")
    assert after.phase == "result_saved"


@pytest.mark.asyncio(loop_scope="session")
async def test_revocation_after_grant_denies_with_permission_revoked(real_embedded_pg: ConnectionInfo) -> None:
    dao, execution_id, round_id = await _full_setup(real_embedded_pg)
    session = _Session()
    coordinator = ToolApprovalCoordinator()
    runtime = _runtime(dao, session, execution_id=execution_id, round_id=round_id, coordinator=coordinator)

    task = asyncio.create_task(
        runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-revoke")
    )
    decision_id = await _wait_decision(dao, execution_id=execution_id, round_id=round_id)
    receipt = await dao.save_answer(
        SaveAnswerRequest(
            user_scope="u1",
            decision_id=decision_id,
            client_command_id="ans-revoke",
            expected_decision_revision=1,
            expected_execution_id=execution_id,
            expected_round_id=round_id,
            answer_payload=json_doc({"schema_version": 1, "approve": True}),
        )
    )
    consumed = await dao.consume_answer(
        ConsumeAnswerRequest(user_scope="u1", command_record_id=receipt.command_record_id)
    )
    assert consumed.consumption_state.value == "consumed"
    revoked = await dao.revoke_tool_grant(
        GrantRevisionRequest(
            user_scope="u1",
            decision_id=decision_id,
            expected_grant_revision=2,
            operation_id=None,
            mcp_service_key=_IDENTITY.stable_key(),
        )
    )
    assert revoked.grant_state is ToolGrantState.REVOKED
    coordinator.notify_consumed(decision_id)

    outcome = await asyncio.wait_for(task, timeout=5)

    assert outcome.state is ToolCallState.FAILED
    assert outcome.result == {"state": "denied", "reason": "permission_revoked"}
    assert session.calls == []
