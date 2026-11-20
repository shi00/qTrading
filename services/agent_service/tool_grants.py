"""工具派发许可的生产适配器（I44）：连接 ``ToolGrantPort`` 与既有单胜者 CAS。

生产实现只复用 ``AgentDao.claim_tool_dispatch``（grant_revision CAS + operation
绑定 + owner/run epoch 复核），不建第二权限库。

**摘要比对边界（§7-Q1 决策 a）**：决定表目前只持久化 ``parameter_digest`` /
``tool_call_id``，没有 ``schema_digest`` / ``audit_scope_digest`` / ``boundary_digest``
列。因此本适配器只比对参数摘要（不符即 ``REVOKED``）；其余摘要仅供调用门自身比对与
日志追溯，暂无持久化落点——该缺口已在台账登记，补齐需新增列 + 迁移 + 授权写入路径，
不在本次装配范围。不得因此放宽参数比对。
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

        ``schema_digest`` / ``audit_scope_digest`` / ``boundary_digest`` 当前无持久化
        落点（见模块说明），仍做非空校验以避免空摘要被当作有效授权；研究边界事实的
        新鲜比对由调用门在 claim 前完成（I43）。
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
            or scope.grant_state is None
            or scope.grant_state is ToolGrantState.REVOKED
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
