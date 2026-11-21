"""清理准入/核验的生产事实提供器（I81）：DAO 权威事实 → 端口。

- 事实不可读/不可定位 → ``None`` / ``authoritative=False``，调用方必须拒绝清理；
- 不接受调用方自述「已静止/闭包已知」布尔（I81 契约）；
- 只读应用域事实，不触达 DBOS 系统表（删除由 ``NativeCleanupExecutor`` 经公开
  的 ``Client.delete_workflows`` 执行，且已由执行器提交受管线程，R16）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from core.agent_types import AgentServiceError, CleanupJobRecord, CleanupNativeFacts, CleanupPrecondition
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError
from services.agent_service.cleanup import CleanupAdmissionFactsPort
from services.agent_service.cleanup_native import NativeVerification, NativeVerificationPort

__all__ = [
    "DaoCleanupAdmissionFacts",
    "DaoNativeVerification",
    "DbosDeletionClient",
]


class _CleanupFactsDao(Protocol):
    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None: ...

    async def read_cleanup_native_facts(self, *, user_scope: str, ids: tuple[str, ...]) -> CleanupNativeFacts: ...


class _DeletionClient(Protocol):
    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None: ...


class DaoCleanupAdmissionFacts(CleanupAdmissionFactsPort):
    """由作业记录 + 权威事实派生准入谓词；任一事实不可得返回 ``None``。"""

    def __init__(self, dao: _CleanupFactsDao) -> None:
        self._dao = dao

    async def derive_precondition(self, user_scope: str, cleanup_job_id: str) -> CleanupPrecondition | None:
        try:
            record = await self._dao.load_cleanup_job(user_scope, cleanup_job_id)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except (DatabaseQueryError, AgentServiceError):
            return None
        if record is None:
            return None
        try:
            facts = await self._dao.read_cleanup_native_facts(user_scope=user_scope, ids=record.exact_ids)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except (DatabaseQueryError, AgentServiceError):
            return None
        if len(facts.resolvable_ids) != len(tuple(dict.fromkeys(record.exact_ids))):
            # 有 ID 无法在本用户域定位：事实不可得，拒绝清理（不当作已静止/已清空）。
            return None
        return CleanupPrecondition(
            scope_settled=not facts.foreign_ids and not facts.non_terminal_ids,
            writers_exited=not facts.writers_running_ids,
            closure_known=True,
            preview_valid=True,
            new_reference_conflict=bool(facts.externally_referenced_ids),
            unknown_deliveries=bool(facts.unknown_delivery_ids),
        )


class DaoNativeVerification(NativeVerificationPort):
    """逐 ID 权威核验：归属/终态/集合外引用/writer/unknown 交付。"""

    def __init__(self, dao: _CleanupFactsDao) -> None:
        self._dao = dao

    async def verify(self, ids: Sequence[str], *, user_scope: str) -> NativeVerification:
        requested = tuple(dict.fromkeys(str(item) for item in ids if item))
        if not requested:
            return NativeVerification(authoritative=True)
        try:
            facts = await self._dao.read_cleanup_native_facts(user_scope=user_scope, ids=requested)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except (DatabaseQueryError, AgentServiceError):
            return NativeVerification(authoritative=False)
        known = set(facts.resolvable_ids) | set(facts.foreign_ids)
        if not set(requested) <= known:
            return NativeVerification(authoritative=False)
        return NativeVerification(
            authoritative=True,
            foreign_ids=facts.foreign_ids,
            non_terminal_ids=facts.non_terminal_ids,
            externally_referenced_ids=facts.externally_referenced_ids,
            writers_running_ids=facts.writers_running_ids,
            unknown_delivery_ids=facts.unknown_delivery_ids,
        )


class DbosDeletionClient:
    """``WorkflowDeletionPort`` 生产适配器（DBOS 公开删除 API）。

    ``delete_workflows`` 为同步调用；执行器已将其提交受管 IO 线程（R16）。
    """

    def __init__(self, client: _DeletionClient) -> None:
        if not callable(getattr(client, "delete_workflows", None)):
            raise TypeError("DBOS client does not expose delete_workflows (cleanup unavailable)")
        self._client = client

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        self._client.delete_workflows(workflow_ids, delete_children=delete_children)
