"""清理准入/核验的生产事实提供器（I81）：DAO 权威事实 → 端口。

- 事实不可读/不可定位 → ``None`` / ``authoritative=False``，调用方必须拒绝清理；
- 不接受调用方自述「已静止/闭包已知」布尔（I81 契约）；闭包与预览有效性由存储面
  事实派生（P1-11）：会话/资产实际集合、全部执行与跨会话新引用都必须核查，缺失即拒；
- 只读应用域事实，不触达 DBOS 系统表（删除由 ``NativeCleanupExecutor`` 经公开
  的 ``Client.delete_workflows`` 执行，且已由执行器提交受管线程，R16）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from core.agent_types import AgentServiceError, CleanupJobRecord, CleanupNativeFacts, CleanupPrecondition
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError
from services.agent_service.cleanup import CleanupAdmissionFactsPort, preview_digest
from services.agent_service.cleanup_native import NativeVerification, NativeVerificationPort

__all__ = [
    "DaoCleanupAdmissionFacts",
    "DaoNativeVerification",
    "DbosDeletionClient",
]


class _CleanupFactsDao(Protocol):
    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None: ...

    async def read_cleanup_native_facts(
        self,
        *,
        user_scope: str,
        ids: tuple[str, ...],
        session_ids: tuple[str, ...] = (),
        asset_keys: tuple[tuple[str, str], ...] = (),
    ) -> CleanupNativeFacts: ...

    async def read_cleanup_content_manifest(self, user_scope: str, cleanup_job_id: str) -> dict[str, object] | None: ...


def preview_is_valid(record: CleanupJobRecord, manifest: dict[str, object] | None) -> bool:
    """原预览版有效（§10.7）：必须能用存储的预览内容清单重算出同一 preview digest。

    预览绑定字段不可得（清单缺失/不可解释）时返回 ``False``——不把「无从核对」
    当作「预览有效」（R21）。
    """

    if not isinstance(manifest, dict):
        return False
    fields = manifest.get("preview_fields")
    if not isinstance(fields, list) or not all(isinstance(field, str) and field for field in fields):
        return False
    return preview_digest(tuple(fields), record.exact_ids) == record.preview_digest


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
            facts = await self._dao.read_cleanup_native_facts(
                user_scope=user_scope,
                ids=record.exact_ids,
                session_ids=record.session_ids,
                asset_keys=record.asset_keys,
            )
            manifest = await self._dao.read_cleanup_content_manifest(user_scope, cleanup_job_id)
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
            # P1-11：闭包与预览均不得硬编码为真；事实未覆盖实际闭包即拒。
            closure_known=facts.closure_complete,
            preview_valid=preview_is_valid(record, manifest),
            new_reference_conflict=bool(facts.externally_referenced_ids) or facts.new_reference_conflict,
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
