"""研究历史与清理确认的服务层适配（W-04）。

**位置说明**：本模块不在 ``services.agent_service`` 包内。该包的 ``__init__`` 会
初始化 Agent 运行时（DBOS / pydantic_ai），而 ``ui.app_layout`` /
``ui.viewmodels.research_observer_view_model`` 必须在 Agent 专用依赖缺失时仍可导入
（N01 契约）。因此本模块顶层只依赖 ``core``，清理确认命令在 ``confirm`` 内惰性导入。

清理准备（资源闭包冻结、preview digest 生成）不在本模块：UI 只能对**已准备**的
清理作业做「预览 → 显式确认 → 回执」。作业缺失即如实报未准备，不构造空集合
冒充可清理（R21），也不强行删除。准入仍由 ``CleanupService`` 逐次读权威事实
派生（未知交付/在途 writer 一律拒绝）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupJobRecord,
    CleanupPhase,
)

if TYPE_CHECKING:
    from services.agent_service.cleanup import CleanupConfirmCommand
    from services.agent_service.cleanup_execution import CleanupExecutionReceipt

__all__ = [
    "CleanupExecutionPort",
    "CleanupJobPreview",
    "CleanupJobStorePort",
    "CleanupOutcome",
    "HistoryCleanupGateway",
]


class CleanupJobStorePort(Protocol):
    """清理作业只读存储端口（生产接 ``DaoCleanupJobStore``）。"""

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None: ...


class CleanupExecutionPort(Protocol):
    """清理确认+执行端口（生产接 ``CleanupExecutionService``）。"""

    async def confirm_and_execute(self, command: CleanupConfirmCommand) -> CleanupExecutionReceipt: ...


@dataclass(frozen=True, slots=True)
class CleanupJobPreview:
    """清理作业预览：冻结集合计数 + 原预览摘要/修订（不含正文）。"""

    user_scope: str
    cleanup_job_id: str
    phase: CleanupPhase
    preview_digest: str
    expected_revision: int
    exact_ids: tuple[str, ...]
    session_ids: tuple[str, ...]
    asset_keys: tuple[tuple[str, str], ...]

    @property
    def verified(self) -> bool:
        return self.phase is CleanupPhase.VERIFIED

    @property
    def requires_confirmation(self) -> bool:
        """已 verified 的作业无需再确认（幂等回执）；其余必须先确认。"""

        return not self.verified


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """清理回执投影：阶段 + 是否重放 + 九域计数 + 应用域 tombstone 行数。"""

    cleanup_job_id: str
    phase: CleanupPhase
    replayed: bool
    verified_counts: tuple[tuple[str, int], ...]
    tombstoned: tuple[tuple[str, int], ...]

    @property
    def verified(self) -> bool:
        """只有 ``verified`` 才算清理完成；``retry_pending`` 不得当作已清除。"""

        return self.phase is CleanupPhase.VERIFIED


class HistoryCleanupGateway:
    """清理作业预览/确认门面：作业缺失显式失败，确认必须显式且复用原预览。"""

    def __init__(self, store: CleanupJobStorePort, execution: CleanupExecutionPort) -> None:
        self._store = store
        self._execution = execution

    async def preview(self, user_scope: str, cleanup_job_id: str) -> CleanupJobPreview:
        """读取已准备作业的冻结集合与版本；缺失即 ``CONFIG_UNAVAILABLE``。

        「查不到」不等于「已清理」：作业不存在时调用方必须按未准备阻断，
        不构造空作业、不以空集合执行删除。
        """

        record = await self._load(user_scope, cleanup_job_id)
        return _preview(record)

    async def confirm(
        self,
        user_scope: str,
        cleanup_job_id: str,
        *,
        command_id: str,
        confirmed: bool,
    ) -> CleanupOutcome:
        """显式确认后执行清理；准入未通过由 ``CleanupService`` 抛出，不吞错。"""

        # 惰性导入：UI 导入本模块不得初始化 Agent 运行时（N01 可选依赖契约）。
        from services.agent_service.cleanup import CleanupConfirmCommand

        if not confirmed:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "cleanup requires explicit confirmation",
                f"{user_scope}/{cleanup_job_id}",
            )
        record = await self._load(user_scope, cleanup_job_id)
        receipt = await self._execution.confirm_and_execute(
            CleanupConfirmCommand(
                user_scope=user_scope,
                cleanup_job_id=cleanup_job_id,
                command_id=command_id,
                preview_digest=record.preview_digest,
                expected_revision=record.expected_revision,
                confirmed=True,
            )
        )
        return CleanupOutcome(
            cleanup_job_id=receipt.cleanup_job_id,
            phase=receipt.phase,
            replayed=receipt.replayed,
            verified_counts=tuple(sorted(receipt.verified_counts.items())),
            tombstoned=tuple(sorted(receipt.tombstoned.items())),
        )

    async def _load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord:
        if not user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope is required", "history/cleanup")
        if not cleanup_job_id:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "cleanup_job_id is required", "history/cleanup")
        record = await self._store.load(user_scope, cleanup_job_id)
        if record is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "cleanup job not prepared",
                f"{user_scope}/{cleanup_job_id}",
            )
        return record


def _preview(record: CleanupJobRecord) -> CleanupJobPreview:
    return CleanupJobPreview(
        user_scope=record.user_scope,
        cleanup_job_id=record.cleanup_job_id,
        phase=record.phase,
        preview_digest=record.preview_digest,
        expected_revision=record.expected_revision,
        exact_ids=tuple(record.exact_ids),
        session_ids=tuple(record.session_ids),
        asset_keys=tuple(record.asset_keys),
    )
