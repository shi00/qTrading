"""工具派发许可的生产适配器（I44）：连接 ``ToolGrantPort`` 与既有单胜者 CAS。

生产实现只复用 ``AgentDao.claim_tool_dispatch``（grant_revision CAS + operation
绑定 + owner/run epoch 复核 + 人答消费/有效期/授权摘要/MCP 停用门），不建第二
权限库。

**摘要比对（P1-10 补齐）**：决定表没有独立摘要列，但 ``save_tool_decision`` 已把
``schema_digest`` / ``audit_scope_digest`` / ``boundary_digest`` 并入
``question_payload`` 同事务持久化；本适配器把调用门新鲜重算的三项摘要与连接
身份稳定键随 claim 交给 DAO，在决定行锁内逐项比对，任一不符即 ``REVOKED``
（零派发）。授权摘要因此是**持久化来源认领**，重放只查回原操作、不扩大。
"""

from __future__ import annotations

import asyncio
from typing import Protocol

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
from services.agent_service.mcp_tools import ToolDispatchVerdict, ToolGrantPort

__all__ = ["DaoToolGrantPort"]

#: 可折叠为「撤销/不可派发」的拒绝码（其余异常原样传播，不把未知伪装成撤销）。
_REVOKING_CODES: frozenset[AgentErrorCode] = frozenset(
    {
        AgentErrorCode.REVISION_CONFLICT,
        AgentErrorCode.POLICY_DENIED,
        AgentErrorCode.INPUT_INVALID,
        AgentErrorCode.SOURCE_DISABLED,
    }
)


class _GrantScopeReader(Protocol):
    async def read_operation_grant_scope(self, *, operation_id: str) -> OperationGrantScope | None: ...


class _GrantClaimer(Protocol):
    async def claim_tool_dispatch(self, request: GrantRevisionRequest) -> ToolGrantFact: ...


class _GrantDao(_GrantScopeReader, _GrantClaimer, Protocol):
    """适配器所需的最小 DAO 面（生产为 ``AgentDao``）。"""


class DaoToolGrantPort(ToolGrantPort):
    """``ToolGrantPort`` 生产实现：operation → grant → decision → CAS claim。"""

    def __init__(self, dao: _GrantDao) -> None:
        self._dao = dao

    async def claim(
        self,
        *,
        identity: MCPServiceIdentity,
        tool_name: str,
        operation_id: str,
        schema_digest: str,
        arguments_digest: str,
        audit_scope_digest: str,
        boundary_digest: str,
    ) -> ToolDispatchVerdict:
        """认领派发权；任何无法证明授权的情形一律 ``REVOKED``（零派发）。

        P1-10：只有**本次答复已消费且为批准**的许可（``GRANTED``）可认领；
        ``PENDING``（未答/待消费）同样是零派发。三项授权摘要与连接身份稳定键
        交由 DAO 在决定行锁内与持久化值比对，并在同一事务内锁定停用门。
        """

        if (
            not identity.service_id
            or identity.config_version < 1
            or not tool_name
            or not schema_digest
            or not arguments_digest
            or not audit_scope_digest
            or not boundary_digest
        ):
            return ToolDispatchVerdict.REVOKED
        try:
            scope = await self._dao.read_operation_grant_scope(operation_id=operation_id)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentServiceError:
            return ToolDispatchVerdict.REVOKED
        if (
            scope is None
            or scope.decision_id is None
            or scope.grant_revision is None
            or scope.grant_state is not ToolGrantState.GRANTED
        ):
            return ToolDispatchVerdict.REVOKED
        if scope.parameter_digest is None or scope.parameter_digest != arguments_digest:
            return ToolDispatchVerdict.REVOKED
        try:
            fact = await self._dao.claim_tool_dispatch(
                GrantRevisionRequest(
                    user_scope=scope.user_scope,
                    decision_id=scope.decision_id,
                    expected_grant_revision=scope.grant_revision,
                    operation_id=operation_id,
                    schema_digest=schema_digest,
                    audit_scope_digest=audit_scope_digest,
                    boundary_digest=boundary_digest,
                    mcp_service_key=identity.stable_key(),
                )
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentServiceError as exc:
            if exc.code in _REVOKING_CODES:
                return ToolDispatchVerdict.REVOKED
            raise
        if fact.grant_state is ToolGrantState.CLAIMED:
            return ToolDispatchVerdict.CLAIMED
        return ToolDispatchVerdict.REVOKED
