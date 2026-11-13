"""九类原生目标真实执行器（E-06-2c / 详细设计 §10.7）。

删除顺序冻结为：公开系统删除（DBOS ``Client.delete_workflows``）→ Datasource
常量表参数化 DML → 九域逐域查回。全部为同步引擎调用，经受管线程提交（R16）。

明确不做：LIKE/前缀/递归扩集合/全表删除；系统表私有 DML（绕过 DBOS 公开 API）；
重试时把「查不到」当作已清除之外的新证据（预检的缺失由调用方按原 fence 处置）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    normalize_cleanup_ids,
)


class NativeStorePort(Protocol):
    """运行库原生目标存储端口（生产接 ``AgentRuntimeNativeStore``）。"""

    def counts(self, ids: list[str]) -> dict[str, int]: ...

    def missing_primary_ids(self, ids: list[str]) -> tuple[str, ...]: ...

    def delete_datasource_rows(self, ids: list[str]) -> int: ...


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
        run_blocking: BlockingRunner | None = None,
    ) -> None:
        self._store = store
        self._client = client
        self._run_blocking = run_blocking or _default_run_blocking

    async def preflight(self, ids: Sequence[str]) -> NativePreflight:
        """首次预检：九域计数 + 主键域缺失（禁止把缺失解释为空集合）。"""

        normalized = self._normalize(ids)
        if not normalized:
            return NativePreflight((), {}, ())
        counts, missing = await self._run_blocking(self._preflight_sync, list(normalized))
        return NativePreflight(normalized, counts, missing)

    async def delete(self, ids: Sequence[str]) -> NativeCleanupResult:
        """按准确集合删除并查回；空集合是唯一允许的「无需删除」情形。"""

        normalized = self._normalize(ids)
        if not normalized:
            return NativeCleanupResult((), {})
        counts = await self._run_blocking(self._delete_sync, list(normalized))
        return NativeCleanupResult(normalized, counts)

    def _preflight_sync(self, ids: list[str]) -> tuple[dict[str, int], tuple[str, ...]]:
        return self._store.counts(ids), self._store.missing_primary_ids(ids)

    def _delete_sync(self, ids: list[str]) -> dict[str, int]:
        # 显式不递归子工作流：集合只由用户确认的准确 ID 构成（§10.7）。
        self._client.delete_workflows(ids, delete_children=False)
        self._store.delete_datasource_rows(ids)
        return self._store.counts(ids)

    @staticmethod
    def _normalize(ids: Sequence[str]) -> tuple[str, ...]:
        try:
            return normalize_cleanup_ids(ids)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"invalid cleanup ids: {exc.code}", "cleanup"
            ) from exc
