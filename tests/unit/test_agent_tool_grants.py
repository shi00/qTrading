"""I44 生产许可适配器：operation→grant→decision→CAS claim 的拒绝语义。"""

from __future__ import annotations

import asyncio

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    GrantRevisionRequest,
    MCPServiceIdentity,
    OperationGrantScope,
    ToolGrantFact,
    ToolGrantState,
)
from data.persistence.daos.base_dao import EngineDisposedError
from services.agent_service.mcp_tools import ToolDispatchVerdict
from services.agent_service.tool_grants import DaoToolGrantPort

_IDENTITY = MCPServiceIdentity(service_id="svc-1", config_version=1)


def _scope(**overrides: object) -> OperationGrantScope:
    values: dict[str, object] = {
        "user_scope": "u1",
        "operation_id": "op-1",
        "execution_id": "ex-1",
        "round_id": "r-1",
        "grant_id": "grant-1",
        "decision_id": "dec-1",
        "grant_revision": 3,
        "grant_state": ToolGrantState.GRANTED,
        "tool_call_id": "call-1",
        "parameter_digest": "a" * 64,
    }
    values.update(overrides)
    return OperationGrantScope(**values)  # type: ignore[arg-type]  # [reason: 测试构造以 kwargs 覆盖可选字段，取值已在用例内约束]


class _Dao:
    def __init__(self, scope: OperationGrantScope | None = None, *, claim_error: Exception | None = None) -> None:
        self.scope = scope
        self.claim_error = claim_error
        self.claim_requests: list[GrantRevisionRequest] = []

    async def read_operation_grant_scope(self, *, operation_id: str) -> OperationGrantScope | None:
        return self.scope

    async def claim_tool_dispatch(self, request: GrantRevisionRequest) -> ToolGrantFact:
        self.claim_requests.append(request)
        if self.claim_error is not None:
            raise self.claim_error
        return ToolGrantFact(
            decision_id=request.decision_id,
            grant_id="grant-1",
            grant_revision=request.expected_grant_revision + 1,
            grant_state=ToolGrantState.CLAIMED,
        )


async def _claim(
    dao: _Dao, *, arguments_digest: str = "a" * 64, boundary_digest: str = "e" * 64
) -> ToolDispatchVerdict:
    return await DaoToolGrantPort(dao).claim(
        identity=_IDENTITY,
        tool_name="search",
        operation_id="op-1",
        schema_digest="b" * 64,
        arguments_digest=arguments_digest,
        audit_scope_digest="c" * 64,
        boundary_digest=boundary_digest,
    )


class TestDaoToolGrantPort:
    async def test_claims_granted_scope_with_bound_revision(self) -> None:
        dao = _Dao(_scope())
        assert await _claim(dao) is ToolDispatchVerdict.CLAIMED
        assert len(dao.claim_requests) == 1
        request = dao.claim_requests[0]
        assert (request.user_scope, request.decision_id, request.expected_grant_revision) == ("u1", "dec-1", 3)
        assert request.operation_id == "op-1"

    async def test_argument_digest_mismatch_revokes_without_claim(self) -> None:
        dao = _Dao(_scope())
        assert await _claim(dao, arguments_digest="d" * 64) is ToolDispatchVerdict.REVOKED
        assert dao.claim_requests == []

    async def test_missing_parameter_digest_revokes(self) -> None:
        dao = _Dao(_scope(parameter_digest=None))
        assert await _claim(dao) is ToolDispatchVerdict.REVOKED
        assert dao.claim_requests == []

    async def test_missing_boundary_digest_revokes_without_claim(self) -> None:
        """I43：边界摘要同样是授权证据，空摘要不得当作有效授权。"""

        dao = _Dao(_scope())
        assert await _claim(dao, boundary_digest="") is ToolDispatchVerdict.REVOKED
        assert dao.claim_requests == []

    async def test_missing_operation_revokes(self) -> None:
        dao = _Dao(None)
        assert await _claim(dao) is ToolDispatchVerdict.REVOKED

    async def test_operation_without_grant_revokes(self) -> None:
        dao = _Dao(_scope(grant_id=None, decision_id=None, grant_revision=None, grant_state=None))
        assert await _claim(dao) is ToolDispatchVerdict.REVOKED
        assert dao.claim_requests == []

    async def test_revoked_state_revokes(self) -> None:
        dao = _Dao(_scope(grant_state=ToolGrantState.REVOKED))
        assert await _claim(dao) is ToolDispatchVerdict.REVOKED
        assert dao.claim_requests == []

    async def test_revision_conflict_collapses_to_revoked(self) -> None:
        dao = _Dao(_scope(), claim_error=AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "lost", "u1/dec-1"))
        assert await _claim(dao) is ToolDispatchVerdict.REVOKED

    async def test_unexpected_service_error_propagates(self) -> None:
        dao = _Dao(
            _scope(), claim_error=AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "db down", "u1/dec-1")
        )
        with pytest.raises(AgentServiceError) as exc:
            await _claim(dao)
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_disposed_engine_propagates(self) -> None:
        dao = _Dao(_scope(), claim_error=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError) as exc:
            await _claim(dao)
        assert "disposed" in str(exc.value)

    async def test_cancellation_propagates(self) -> None:
        dao = _Dao(_scope(), claim_error=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError) as exc:
            await _claim(dao)
        assert type(exc.value) is asyncio.CancelledError
