"""清理执行编排（E-06-3b / 详细设计 §10.7）。

流程冻结：确认（原预览/修订 + 准入 + fence）→ 首次预检（查不到不是已清）→
落删除尝试阶段 → 公开系统删除 + Datasource DML + 九域查回 → 落 tombstone
待办阶段 → 应用域 NOT NULL tombstone → 九域全 0 才 ``verified``；任一步失败
保留原集合转 ``retry_pending``。

尝试阶段在副作用前落库（M14）：删除完成到 ``tombstone/verified`` 之间取消、
硬崩或服务异常时，下一次重试据此识别「已尝试删除」并沿原 job 收尾，不把
已删净的主键域误判为首次缺失。

不做：递归扩集合、绕过 fence 删除、把「查不到」当「已清除」、把缺失计数当 0。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

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

#: 删除副作用前落库的准确尝试阶段（持久化于既有 ``error_category`` 列，无 schema 变更）：
#: 「两主键域删除尝试已开始」→「原生两域已删净、待应用域 tombstone」。
_DELETE_STARTED_STAGE: Final = "cleanup_native_delete_started"
_TOMBSTONE_PENDING_STAGE: Final = "cleanup_native_deleted_tombstone_pending"

_RETRY_AFTER_DELETION_CATEGORIES: frozenset[str] = frozenset(
    {
        "cleanup_execution_failed",
        "native_rows_remaining",
        _DELETE_STARTED_STAGE,
        _TOMBSTONE_PENDING_STAGE,
    }
)
# 上次失败/中断发生在删除尝试之后 → §10.7 允许重试时原生行已缺失。


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

    async def save_cleanup_attempt_stage(
        self,
        user_scope: str,
        cleanup_job_id: str,
        *,
        stage: str,
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
            if preflight.missing_ids:
                # §10.7：只有「已有可靠 prepared 且 fence 持续」的**重试**允许原生行已缺失。
                # 判据是上次失败/中断发生在删除尝试之后（cleanup_execution_failed /
                # rows_remaining / 已落库的删除尝试阶段）；预检阶段缺失
                # （native_target_missing）与首次确认一律拒绝，不以「查不到」冒充「已清理」。
                retry_after_prepared = (
                    record.fence_committed
                    and bool(record.exact_ids)
                    and record.prior_error_category in _RETRY_AFTER_DELETION_CATEGORIES
                )
                if not retry_after_prepared:
                    await self._mark_retry(record.user_scope, record.cleanup_job_id, "native_target_missing")
                    raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "native cleanup target not found", label)
        except AgentServiceError:
            raise
        except Exception as exc:
            # 预检阶段尚未尝试删除：任何失败（含拒绝写入自身失败）都记
            # cleanup_preflight_failed，绝不并入 cleanup_execution_failed。
            await self._mark_retry(record.user_scope, record.cleanup_job_id, "cleanup_preflight_failed", exc)
            raise

        # 副作用前先落「删除尝试已开始」阶段（M14）：两主键域（公开系统 + Datasource）
        # 尚未删除，下一步是删除并逐域查回。此后取消/硬崩/服务异常，重试据既有
        # 阶段沿原 job 收尾，不把已删净的主键域误判为首次缺失。写入失败即中止：
        # 本次未删除任何行，保留原阶段与类别，不冒充「已尝试删除」。
        await self._dao.save_cleanup_attempt_stage(
            record.user_scope, record.cleanup_job_id, stage=_DELETE_STARTED_STAGE
        )

        try:
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
            # 原生两主键域已删净（九域查回全 0），下一步只剩应用域 tombstone 与
            # verified。先落该阶段：删除完成到 tombstone/verified 之间取消/崩溃后，
            # 重试仍能识别「删除已完成」并沿原 job 收尾。
            await self._dao.save_cleanup_attempt_stage(
                record.user_scope, record.cleanup_job_id, stage=_TOMBSTONE_PENDING_STAGE
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
        try:
            await self._dao.save_cleanup_phase(
                user_scope,
                cleanup_job_id,
                phase=CleanupPhase.RETRY_PENDING,
                error_category=error_category,
                sqlstate=self._sqlstate(exc),
            )
        except AgentServiceError:
            # 并发方已把作业推进到 verified：阶段不可回退，保留其完成态，也不让
            # 本次阶段写入失败掩盖原始失败原因。作业确实缺失/仍可重试时照常抛出。
            record = await self._store.load(user_scope, cleanup_job_id)
            if record is None or record.phase is not CleanupPhase.VERIFIED:
                raise

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
