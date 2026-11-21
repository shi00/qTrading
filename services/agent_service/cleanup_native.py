"""九类原生目标真实执行器（E-06-2c / 详细设计 §10.7）。

删除顺序冻结为：公开系统删除（DBOS ``Client.delete_workflows``）→ Datasource
常量表参数化 DML 与九域查零（**同一事务**，非零即回滚）。全部为同步引擎调用，
经受管线程提交（R16）。

明确不做：LIKE/前缀/递归扩集合/全表删除；系统表私有 DML（绕过 DBOS 公开 API）；
重试时把「查不到」当作已清除之外的新证据（预检的缺失由调用方按原 fence 处置）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from core.agent_types import (
    CLEANUP_NATIVE_TARGETS,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    normalize_cleanup_ids,
)


class NativeStorePort(Protocol):
    """运行库原生目标存储端口（生产接 ``AgentRuntimeNativeStore``）。"""

    def counts(self, ids: list[str]) -> dict[str, int]: ...

    def missing_primary_ids(self, ids: list[str]) -> tuple[str, ...]: ...

    def delete_datasource_rows_and_count(self, ids: list[str]) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True)
class NativeVerification:
    """逐 ID 权威核验结果（I81 §10.7）：归属/终态/集合外引用/writer/unknown 交付。

    ``authoritative`` 为假表示事实不可得（读取失败或未实现），必须拒绝清理；
    其余集合非空即对相应 ID 拒绝，不以「查不到」冒充「可清理」。
    """

    authoritative: bool
    foreign_ids: tuple[str, ...] = ()
    non_terminal_ids: tuple[str, ...] = ()
    externally_referenced_ids: tuple[str, ...] = ()
    writers_running_ids: tuple[str, ...] = ()
    unknown_delivery_ids: tuple[str, ...] = ()

    def refusal_reason(self) -> str | None:
        """返回首个脱敏拒绝原因；全空且权威时返回 None。"""

        if not self.authoritative:
            return "verification_unavailable"
        if self.foreign_ids:
            return "ownership_mismatch"
        if self.non_terminal_ids:
            return "non_terminal_target"
        if self.externally_referenced_ids:
            return "external_reference_present"
        if self.writers_running_ids:
            return "writers_still_running"
        if self.unknown_delivery_ids:
            return "unknown_delivery_pending"
        return None


class NativeVerificationPort(Protocol):
    """逐 ID 权威核验端口（生产接存储面/在途/交付事实；测试注入替身）。"""

    async def verify(self, ids: Sequence[str], *, user_scope: str) -> NativeVerification: ...


class WorkflowDeletionPort(Protocol):
    """DBOS 公开系统删除端口（生产接 ``DBOSClient.delete_workflows``）。"""

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None: ...


class BlockingRunner(Protocol):
    """同步工作提交端口（生产接 ``ThreadPoolManager.run_async``）。"""

    async def __call__[T](self, func: Callable[..., T], *args: object) -> T: ...


@dataclass(frozen=True, slots=True)
class NativePreflight:
    """首次预检结果：准确集合 + 九域计数 + 主键域缺失 ID。"""

    exact_ids: tuple[str, ...]
    counts: dict[str, int]
    missing_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeCleanupResult:
    """删除结果：准确集合 + 删除后九域逐域计数（全 0 才可置 verified）。"""

    exact_ids: tuple[str, ...]
    verified_counts: dict[str, int]


def _zero_counts() -> dict[str, int]:
    """九域可核零计数（空集合的显式verified 依据，非缺省缺失）。"""

    return {table: 0 for table, _key in CLEANUP_NATIVE_TARGETS}


async def _default_run_blocking[T](func: Callable[..., T], *args: object) -> T:
    """默认经受管 IO 线程池提交同步引擎工作（R16）。"""

    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, func, *args)


class NativeCleanupExecutor:
    """执行真删除并查回；不判断准入、不改 job 状态（编排由 E-06-3 负责）。"""

    def __init__(
        self,
        *,
        store: NativeStorePort,
        client: WorkflowDeletionPort,
        verification: NativeVerificationPort,
        run_blocking: BlockingRunner | None = None,
    ) -> None:
        self._store = store
        self._client = client
        self._verification = verification
        self._run_blocking = run_blocking or _default_run_blocking

    async def preflight(self, ids: Sequence[str], *, user_scope: str) -> NativePreflight:
        """首次预检：逐 ID 权威核验通过后，九域计数 + 主键域缺失（禁止把缺失解释为空集合）。

        核验事实不可得、归属不符、非终态、存在集合外引用/在途 writer/unknown
        交付时一律拒绝（零出站）；空集合只在「未启动计划」准入下出现：九域计数
        按可核零返回（不改库）。
        """

        normalized = self._normalize(ids)
        if not normalized:
            return NativePreflight((), _zero_counts(), ())
        if not isinstance(user_scope, str) or not user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid cleanup user scope", "cleanup")
        verification = await self._verification.verify(normalized, user_scope=user_scope)
        reason = verification.refusal_reason()
        if reason is not None:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, f"cleanup refused: {reason}", "cleanup")
        counts, missing = await self._run_blocking(self._preflight_sync, list(normalized))
        return NativePreflight(normalized, counts, missing)

    async def delete(self, ids: Sequence[str]) -> NativeCleanupResult:
        """按准确集合删除并在同一事务内查回；空集合是唯一允许的「无需删除」情形。

        非零计数表示删除已回滚（原行保持），由编排按既有非零分支转 ``retry_pending``，
        原 job/原 fence 不变即可重试。
        """

        normalized = self._normalize(ids)
        if not normalized:
            return NativeCleanupResult((), _zero_counts())
        counts = await self._run_blocking(self._delete_sync, list(normalized))
        return NativeCleanupResult(normalized, counts)

    def _preflight_sync(self, ids: list[str]) -> tuple[dict[str, int], tuple[str, ...]]:
        return self._store.counts(ids), self._store.missing_primary_ids(ids)

    def _delete_sync(self, ids: list[str]) -> dict[str, int]:
        # 显式不递归子工作流：集合只由用户确认的准确 ID 构成（§10.7）。
        self._client.delete_workflows(ids, delete_children=False)
        # 删除与查零同一事务：非零即回滚，绝不先提交删除再由另连接核零。
        return self._store.delete_datasource_rows_and_count(ids)

    @staticmethod
    def _normalize(ids: Sequence[str]) -> tuple[str, ...]:
        try:
            return normalize_cleanup_ids(ids)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"invalid cleanup ids: {exc.code}", "cleanup"
            ) from exc
