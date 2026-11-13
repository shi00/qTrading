"""清理准备/确认的应用服务（E-06 / 详细设计 §5.3、§10.7、SQ-10）。

本轮只承载**不依赖真实数据库**的确认合同：
- 确认必须回传原 ``preview_digest`` 与 ``expected_revision``，并且 ``confirmed=true``；
- 准入谓词（准备/确认/重试共用）未通过时零副作用、不提交 fence；
- fence 提交前不得删除；重复确认幂等（不扩大集合、不重复 fence）；
- ``retry_pending`` 复用原不可变集合，不递归扩集合。

真实九类删除、逐域查 0 与 NOT NULL tombstone 由 DAO/编排分片实现（E-06-2/E-06-3）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupAdmission,
    CleanupPrecondition,
    evaluate_cleanup_admission,
    normalize_cleanup_ids,
    sha256_hex,
)


class CleanupJobState(Enum):
    """作业在存储中的阶段（与 :class:`CleanupPhase` 同名值）。"""

    PREPARED = "prepared"
    RETRY_PENDING = "retry_pending"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class CleanupJobRecord:
    """作业事实：固定集合 + 原预览摘要 + 期望修订 + fence 状态。"""

    user_scope: str
    cleanup_job_id: str
    owner_ref: str
    preview_digest: str
    expected_revision: int
    exact_ids: tuple[str, ...]
    phase: CleanupJobState
    fence_committed: bool


@dataclass(frozen=True, slots=True)
class CleanupConfirmCommand:
    """确认命令：身份 + 原预览/修订 + 显式确认。"""

    user_scope: str
    cleanup_job_id: str
    command_id: str
    preview_digest: str
    expected_revision: int
    confirmed: bool


@dataclass(frozen=True, slots=True)
class CleanupReceipt:
    """确认回执：阶段 + 是否重放 + 固定集合（不含正文）。"""

    cleanup_job_id: str
    phase: CleanupJobState
    replayed: bool
    exact_ids: tuple[str, ...]
    admission: CleanupAdmission


class CleanupJobStorePort(Protocol):
    """作业持久化端口（生产接 AgentDao；测试可注入替身）。"""

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None: ...

    async def commit_fence(self, record: CleanupJobRecord) -> None: ...


def preview_digest(preview_fields: tuple[str, ...], exact_ids: tuple[str, ...]) -> str:
    """预览摘要绑定字段 + 准确集合（集合先规范化，顺序无关）。"""

    payload = "|".join((*preview_fields, *normalize_cleanup_ids(exact_ids)))
    return sha256_hex(payload.encode("utf-8"))


class CleanupService:
    """准备/确认共用同一准入谓词；确认前不产生任何删除副作用。"""

    def __init__(self, store: CleanupJobStorePort) -> None:
        self._store = store

    async def confirm(
        self,
        command: CleanupConfirmCommand,
        precondition: CleanupPrecondition,
    ) -> CleanupReceipt:
        label = f"{command.user_scope}/{command.cleanup_job_id}"
        if not command.confirmed:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "cleanup requires explicit confirmation", label)
        if not command.command_id:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command_id is required", label)
        record = await self._store.load(command.user_scope, command.cleanup_job_id)
        if record is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "cleanup job not found", label)
        if command.preview_digest != record.preview_digest:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "preview digest does not match the frozen deletion set", label
            )
        if command.expected_revision != record.expected_revision:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "cleanup job revision is stale", label)
        if record.phase is CleanupJobState.VERIFIED:
            return CleanupReceipt(
                command.cleanup_job_id,
                record.phase,
                True,
                record.exact_ids,
                CleanupAdmission(False, "already_verified"),
            )
        admission = evaluate_cleanup_admission(precondition)
        if not admission.allowed:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, f"cleanup refused: {admission.reason}", label)
        if record.fence_committed:
            return CleanupReceipt(command.cleanup_job_id, record.phase, True, record.exact_ids, admission)
        committed = CleanupJobRecord(
            user_scope=record.user_scope,
            cleanup_job_id=record.cleanup_job_id,
            owner_ref=record.owner_ref,
            preview_digest=record.preview_digest,
            expected_revision=record.expected_revision,
            exact_ids=normalize_cleanup_ids(record.exact_ids),
            phase=record.phase if record.phase is CleanupJobState.RETRY_PENDING else CleanupJobState.PREPARED,
            fence_committed=True,
        )
        await self._store.commit_fence(committed)
        return CleanupReceipt(
            command.cleanup_job_id,
            CleanupJobState(committed.phase),
            False,
            committed.exact_ids,
            admission,
        )
