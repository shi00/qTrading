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
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupAdmission,
    CleanupJobRecord,
    CleanupPhase,
    CleanupPrecondition,
    evaluate_cleanup_admission,
    normalize_cleanup_asset_keys,
    normalize_cleanup_ids,
    sha256_hex,
)


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
    phase: CleanupPhase
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
        if record.phase is CleanupPhase.VERIFIED:
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
            phase=record.phase if record.phase is CleanupPhase.RETRY_PENDING else CleanupPhase.PREPARED,
            fence_committed=True,
            session_ids=normalize_cleanup_ids(record.session_ids),
            asset_keys=normalize_cleanup_asset_keys(record.asset_keys),
            prior_error_category=record.prior_error_category,
        )
        await self._store.commit_fence(committed)
        return CleanupReceipt(
            command.cleanup_job_id,
            committed.phase,
            False,
            committed.exact_ids,
            admission,
        )


class CleanupJobDaoPort(Protocol):
    """``CleanupJobStorePort`` 的 DAO 侧最小面（生产接 ``AgentDao``）。"""

    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None: ...

    async def commit_cleanup_fence(self, record: CleanupJobRecord) -> None: ...


class DaoCleanupJobStore:
    """作业存储生产实现：直接委托 DAO 的清理作业事务（固定集合不可改写）。"""

    def __init__(self, dao: CleanupJobDaoPort) -> None:
        self._dao = dao

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        return await self._dao.load_cleanup_job(user_scope, cleanup_job_id)

    async def commit_fence(self, record: CleanupJobRecord) -> None:
        await self._dao.commit_cleanup_fence(record)
