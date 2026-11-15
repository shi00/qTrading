"""清理执行编排（E-06-3b / 详细设计 §10.7）。

流程冻结：确认（原预览/修订 + 准入 + fence）→ 首次预检（查不到不是已清）→
公开系统删除 + Datasource DML + 九域查回 → 应用域 NOT NULL tombstone →
九域全 0 才 ``verified``；任一步失败保留原集合转 ``retry_pending``。

不做：递归扩集合、绕过 fence 删除、把「查不到」当「已清除」、把缺失计数当 0。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupPhase,
    CleanupPrecondition,
    CleanupState,
)
from services.agent_service.cleanup import (
    CleanupConfirmCommand,
    CleanupJobStorePort,
    CleanupService,
)
from services.agent_service.cleanup_native import NativeCleanupResult, NativePreflight

_RETRY_AFTER_DELETION_CATEGORIES: frozenset[str] = frozenset({"cleanup_execution_failed", "native_rows_remaining"})
# 上次失败发生在删除尝试之后 → §10.7 允许重试时原生行已缺失。


class NativeCleanupPort(Protocol):
    """原生目标执行端口（生产接 ``NativeCleanupExecutor``）。"""

    async def preflight(self, ids: Sequence[str]) -> NativePreflight: ...

    async def delete(self, ids: Sequence[str]) -> NativeCleanupResult: ...


class CleanupExecutionDaoPort(Protocol):
    """编排所需的 DAO 面（生产接 ``AgentDao``）。"""

    async def save_cleanup_phase(
        self,
        user_scope: str,
        cleanup_job_id: str,
        *,
        phase: CleanupPhase,
        verified_counts: dict[str, int] | None = None,
        complete: bool = False,
        error_category: str | None = None,
        sqlstate: str | None = None,
    ) -> None: ...

    async def tombstone_cleanup_text(
        self,
        user_scope: str,
        *,
        session_ids: tuple[str, ...] = (),
        asset_keys: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True)
class CleanupExecutionReceipt:
    """执行回执：阶段 + 是否重放 + 九域计数 + 应用域 tombstone 行数。"""

    cleanup_job_id: str
    phase: CleanupPhase
    replayed: bool
    verified_counts: dict[str, int]
    tombstoned: dict[str, int]


class CleanupExecutionService:
    """确认并执行清理；失败保留原集合并落 ``retry_pending``，不静默成功。"""

    def __init__(
        self,
        *,
        jobs: CleanupService,
        store: CleanupJobStorePort,
        dao: CleanupExecutionDaoPort,
        executor: NativeCleanupPort,
    ) -> None:
        self._jobs = jobs
        self._store = store
        self._dao = dao
        self._executor = executor

    async def confirm_and_execute(
        self, command: CleanupConfirmCommand, precondition: CleanupPrecondition
    ) -> CleanupExecutionReceipt:
        label = f"{command.user_scope}/{command.cleanup_job_id}"
        confirmation = await self._jobs.confirm(command, precondition)
        record = await self._store.load(command.user_scope, command.cleanup_job_id)
        if record is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "cleanup job not found", label)
        if record.phase is CleanupPhase.VERIFIED:
            return CleanupExecutionReceipt(command.cleanup_job_id, CleanupPhase.VERIFIED, True, {}, {})

        try:
            preflight = await self._executor.preflight(record.exact_ids)
        except AgentServiceError:
            raise
        except Exception as exc:
            # 预检阶段尚未尝试删除：不得并入 cleanup_execution_failed，
            # 否则下次预检缺失时会被当作「删除已执行」而误放行。
            await self._mark_retry(record.user_scope, record.cleanup_job_id, "cleanup_preflight_failed", exc)
            raise

        try:
            if preflight.missing_ids:
                # §10.7：只有「已有可靠 prepared 且 fence 持续」的**重试**允许原生行已缺失。
                # 判据是上次失败发生在删除尝试之后（cleanup_execution_failed / rows_remaining）；
                # 预检阶段缺失（native_target_missing）与首次确认一律拒绝，不以「查不到」
                # 冒充「已清理」。
                retry_after_prepared = (
                    record.fence_committed
                    and bool(record.exact_ids)
                    and record.prior_error_category in _RETRY_AFTER_DELETION_CATEGORIES
                )
                if not retry_after_prepared:
                    await self._mark_retry(record.user_scope, record.cleanup_job_id, "native_target_missing")
                    raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "native cleanup target not found", label)
            deletion = await self._executor.delete(record.exact_ids)
            state = CleanupState(
                phase=CleanupPhase.PREPARED,
                fence_committed=record.fence_committed,
                exact_ids=record.exact_ids,
            )
            if not state.can_mark_verified(deletion.verified_counts):
                await self._dao.save_cleanup_phase(
                    record.user_scope,
                    record.cleanup_job_id,
                    phase=CleanupPhase.RETRY_PENDING,
                    verified_counts=deletion.verified_counts,
                    error_category="native_rows_remaining",
                )
                return CleanupExecutionReceipt(
                    command.cleanup_job_id,
                    CleanupPhase.RETRY_PENDING,
                    confirmation.replayed,
                    deletion.verified_counts,
                    {},
                )
            tombstoned = await self._dao.tombstone_cleanup_text(
                record.user_scope,
                session_ids=record.session_ids,
                asset_keys=record.asset_keys,
            )
            await self._dao.save_cleanup_phase(
                record.user_scope,
                record.cleanup_job_id,
                phase=CleanupPhase.VERIFIED,
                verified_counts=deletion.verified_counts,
                complete=True,
            )
        except AgentServiceError:
            raise
        except Exception as exc:
            await self._mark_retry(record.user_scope, record.cleanup_job_id, "cleanup_execution_failed", exc)
            raise

        return CleanupExecutionReceipt(
            command.cleanup_job_id,
            CleanupPhase.VERIFIED,
            confirmation.replayed,
            deletion.verified_counts,
            tombstoned,
        )

    async def _mark_retry(
        self,
        user_scope: str,
        cleanup_job_id: str,
        error_category: str,
        exc: BaseException | None = None,
    ) -> None:
        await self._dao.save_cleanup_phase(
            user_scope,
            cleanup_job_id,
            phase=CleanupPhase.RETRY_PENDING,
            error_category=error_category,
            sqlstate=self._sqlstate(exc),
        )

    @staticmethod
    def _sqlstate(exc: BaseException | None) -> str | None:
        """从异常链提取 SQLSTATE（仅用于脱敏诊断，不含语句/参数）。"""

        current: BaseException | None = exc
        while current is not None:
            for candidate in (current, getattr(current, "orig", None)):
                value = getattr(candidate, "sqlstate", None)
                if isinstance(value, str) and value:
                    return value
            current = current.__cause__ or current.__context__
        return None
