"""业务库「唯一执行者」锁（PG 会话级 advisory lock）。

用途：``task_history`` 的 stale 回填（``RUNNING``/``QUEUED`` → ``INTERRUPTED``）只在
**本进程是业务库唯一存活执行者**时成立。第二个进程连到同一外置业务库时，首进程仍在
跑的任务行仍是活的；无 owner 条件的回填会把它们显示成「已中断」，却停不掉首进程的
实际执行（N02）。

会话级 advisory lock 精确表达这一所有权：锁随持有连接释放，持锁期间 ``pg_try_advisory_lock``
必失败（证明另一存活实例在用同一库）；进程正常退出或崩溃时 PG 自动释放（下一次启动
即可安全接管并回填）。锁建立在**独立单连接引擎**上，不占用业务连接池预算。

边界（明确不做）：

- 不判定任务业务终态、不改写业务事实；
- 不吞 ``asyncio.CancelledError``（R2）：取消继续传播，只先收口已建立的引擎/连接。

已知边界：锁随**持锁连接**存续。若持锁连接因网络分区/PG 重启在服务端被回收而本进程
未察觉，另一实例可取得锁并回填；此时首进程对该库的写入本身已失效（任务无法推进），
故不会出现「首进程继续写、第二进程改写其状态」的并发结果。重新初始化时会先探测持锁
连接是否存活，失活即视为未持有并重建。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

#: 业务库「任务回填唯一所有权」键（"ASTOCKTK" ASCII，64 位有符号范围内；不与迁移锁键复用）。
TASK_OWNERSHIP_LOCK_KEY: Final[int] = 0x4153544F434B544B

_TRY_LOCK_SQL = text("SELECT pg_try_advisory_lock(:lock_key)")
_PING_SQL = text("SELECT 1")

#: 独立锁引擎：单连接、无溢出、拿不到连接即失败（启动期有界等待）。
_LOCK_POOL_SIZE: Final[int] = 1
_LOCK_POOL_TIMEOUT_S: Final[int] = 3


class InstanceOwnershipConflictError(RuntimeError):
    """另一存活实例持有业务库唯一所有权锁：本进程不得改动其任务状态。"""

    def __init__(self) -> None:
        super().__init__(
            "另一个应用实例正在使用同一数据库（业务库唯一所有权锁已被持有）。"
            "本实例未回填任务状态，也未继续任务初始化；请关闭另一实例后重试。"
        )


class InstanceOwnershipLock:
    """业务库唯一所有权锁（独立单连接引擎持有会话级 advisory lock）。

    成功获取即同时证明「本进程取得唯一执行权」与「上一写者已退出/可安全接管」；
    失败（``pg_try_advisory_lock`` 返回假）即另一存活实例仍在用同一库。
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine | None = None
        self._connection: Any = None
        self._held = False

    async def acquire(self) -> bool:
        """尝试取得唯一所有权；已持有且持锁连接存活时幂等返回 ``True``。

        Raises:
            asyncio.CancelledError: 原样传播（R2），传播前先收口已建立的引擎/连接。
        """

        if self._held and await self._held_connection_alive():
            return True
        # 未持有或持锁连接已失活（会话锁已随连接释放）：旧状态不得冒充持有。
        self.force_release()
        engine = create_async_engine(
            self._database_url,
            pool_size=_LOCK_POOL_SIZE,
            max_overflow=0,
            pool_timeout=_LOCK_POOL_TIMEOUT_S,
        )
        try:
            connection = await engine.connect()
        except BaseException:
            # 建连失败或取消：先释放引擎再传播（取消不吞，R2）
            await _dispose_quietly(engine)
            raise
        try:
            granted = bool((await connection.execute(_TRY_LOCK_SQL, {"lock_key": TASK_OWNERSHIP_LOCK_KEY})).scalar())
        except BaseException:
            # 查询失败或取消：先关闭连接（会话锁随之释放）再传播，不吞取消（R2）
            await _close_quietly(connection)
            await _dispose_quietly(engine)
            raise
        if not granted:
            await _close_quietly(connection)
            await _dispose_quietly(engine)
            return False
        self._engine = engine
        self._connection = connection
        self._held = True
        logger.info("[InstanceLock] Business database ownership lock acquired (key=%#x).", TASK_OWNERSHIP_LOCK_KEY)
        return True

    async def _held_connection_alive(self) -> bool:
        """探测持锁连接是否仍可用；失活即不得冒充持有（会话锁已随连接释放）。"""

        connection = self._connection
        try:
            await connection.execute(_PING_SQL)
        except asyncio.CancelledError:
            raise
        except Exception:  # 连接失活/绑定其他事件循环：按未持有处理，不冒充所有权
            return False
        return True

    def force_release(self) -> None:
        """同步释放（单例重置/进程退出路径）：关闭底层连接即释放会话锁。

        只捕获 ``Exception``（``CancelledError`` 不是其子类），释放失败不得掩盖主流程
        异常；连接失效也不影响语义——PG 会在进程退出时回收会话锁。
        """

        connection, engine = self._connection, self._engine
        self._connection = None
        self._engine = None
        self._held = False
        if connection is not None:
            try:
                connection.sync_connection.invalidate()
            except Exception:  # 释放兜底：不掩盖启动/关闭主流程异常（R2_ALLOWED）
                pass
        if engine is not None:
            try:
                engine.sync_engine.dispose(close=True)
            except Exception:  # 同上
                pass


async def _close_quietly(connection: Any) -> None:
    """关闭连接；不掩盖原始异常/取消（`CancelledError` 不属 `Exception`，R2）。"""

    try:
        await connection.close()
    except Exception:  # 释放兜底（R2_ALLOWED）
        pass


async def _dispose_quietly(engine: AsyncEngine) -> None:
    """释放独立锁引擎；不掩盖原始异常/取消（`CancelledError` 不属 `Exception`，R2）。"""

    try:
        await engine.dispose()
    except Exception:  # 释放兜底（R2_ALLOWED）
        pass


__all__ = [
    "TASK_OWNERSHIP_LOCK_KEY",
    "InstanceOwnershipConflictError",
    "InstanceOwnershipLock",
]
