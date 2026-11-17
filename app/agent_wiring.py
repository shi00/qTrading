"""app 层 Agent 组装注入点（E-01 / P01；设计 §7.4、§10.6）。

两件事：

1. **单位换算注入**：services/core/data 不得上行 import strategies（R1）；
   唯一合法注入点在 app 层。
2. **生产组合根接线**（P01）：
   - ``AgentService`` facade 基于共享 ``CacheManager`` 的引擎/账本构造
     （``cache_manager.agent_dao``）——Agent 与原业务共账本，不另起 engine/连接池；
   - ``OwnerLoopBridge`` 共享桥由本模块在建会话时创建并注入关闭编排；
   - 宿主组装与启动隔离经 ``start_agent_runtime``（唯一执行锁 → 组装 → 隔离 →
     就绪才 launch），隔离失败零派发；
   - 关闭前置经 ``AgentRuntime.prepare_close``（既有关闭链：冻结桥提交 → 关派发 →
     核 IO → 保存结束/unknown → 关闭 DBOS/Client/引擎），在释放共享引擎之前调用。

**launch 门闩**：needs-spike（HANDOFF §8.4：真 PG/真进程隔离实证）未通过前
``AGENT_RUNTIME_LAUNCH_ALLOWED`` 为 False——宿主照常组装并执行启动隔离，但
launch 被显式推迟（零派发）。门闩只允许在实证通过后改为 True。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Final, cast

from services.agent_service import (
    AgentRuntime,
    AgentRuntimeHandle,
    AgentRuntimeSettings,
    AgentService,
    CloseReadinessReceipt,
    StartupIsolationReceipt,
    launch_agent_runtime,
    start_agent_runtime,
)
from services.agent_service.runtime import CloseCommandIdentity, CloseFactOutcome, OwnerLoopBridge
from services.agent_service.units import UnitConversion

logger = logging.getLogger(__name__)

#: needs-spike 门闩（HANDOFF §8.4）：只有真 PG/真进程隔离实证通过后才允许生产 launch。
AGENT_RUNTIME_LAUNCH_ALLOWED: Final[bool] = False

#: Agent 运行库逻辑库名（与业务库同 PG 实例、独立逻辑库；见 data.persistence.agent_migrator）。
AGENT_RUNTIME_DATABASE: Final[str] = "qtrading_agent_runtime"

#: 桌面单用户域（Agent 用户域唯一来源）。
_LOCAL_USER_SCOPE: Final[str] = "local-desktop"

#: 关闭前置预算（先于 watchdog/do_cleanup 执行，须有界以免拖死退出路径）。
AGENT_CLOSE_PRELUDE_TIMEOUT_S: Final[float] = 15.0


def agent_unit_converter() -> UnitConversion:
    """返回统一换算入口（``strategies.utils.threshold_in_data_unit``）。"""

    from strategies.utils import threshold_in_data_unit

    return threshold_in_data_unit


@dataclass
class AgentWiring:
    """本会话的 Agent 接线（facade + 共享桥 + 宿主 + 关闭前置）。

    ``runtime`` 仅在宿主组装且启动隔离就绪后非空——隔离失败/组装失败时保持
    ``None``（零派发，且不启动任何关闭链/watchdog）。
    """

    service: AgentService | None
    bridge: OwnerLoopBridge
    isolation: StartupIsolationReceipt | None = None
    failure: str | None = None
    launched: bool = False
    handle: AgentRuntimeHandle | None = None
    runtime: AgentRuntime | None = None
    unclosed_handle: AgentRuntimeHandle | None = None
    instance_guard: Any | None = None
    _close_command: CloseCommandIdentity | None = None
    _receipt: CloseReadinessReceipt | None = None

    @property
    def ready(self) -> bool:
        """宿主就绪（组装成功且启动隔离就绪）；未就绪不得有任何派发/关闭链。"""

        return self.handle is not None and self.runtime is not None

    def dispatch_closed(self) -> bool:
        """当前是否零派发（未 launch 的宿主没有任何派发路径）。"""

        return not self.launched

    async def prepare_close(self) -> CloseReadinessReceipt | None:
        """关闭前置：先收敛/释放 Agent 宿主，再由调用方释放共享引擎。

        顺序：``AgentRuntime.prepare_close``（既有关闭链）→ facade ``aclose``。
        未就绪（宿主未组装/隔离失败）时为 no-op，不启动关闭链。不触碰他层资源
        （Recorder、业务引擎、内置 PG 由调用方按原协调顺序关闭）。

        ``CancelledError`` 原样传播（R2）；超时/普通异常按「未就绪」记录并返回，
        不掩盖调用方的正常关闭流程。
        """

        if self.service is None:
            return None
        if self._close_command is None:
            self._close_command = CloseCommandIdentity(_LOCAL_USER_SCOPE, f"app-close-{uuid.uuid4().hex}")
        receipt = self._receipt
        if self.runtime is not None and (receipt is None or not receipt.ready):
            try:
                receipt = await asyncio.wait_for(
                    self.runtime.prepare_close(self._close_command),
                    timeout=AGENT_CLOSE_PRELUDE_TIMEOUT_S,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                logger.error(
                    "[AgentWiring] close prelude timed out after %.1fs; host stays unready",
                    AGENT_CLOSE_PRELUDE_TIMEOUT_S,
                )
                receipt = None
            except Exception as e:  # 关闭前置失败：按未就绪记录，不吞调用方的关闭流程
                logger.warning(
                    "[AgentWiring] close prelude failed: %s",
                    type(e).__name__,
                    exc_info=True,
                )
                receipt = None
            self._receipt = receipt
            if receipt is not None and not receipt.ready:
                logger.warning(
                    "[AgentWiring] agent runtime close prelude not ready: %s",
                    ",".join(receipt.blocking_reasons),
                )
        elif receipt is None and self.unclosed_handle is not None:
            # 隔离/launch 失败时未收口的宿主：此处重试关闭并释放唯一执行锁。
            await self._close_unclosed_handle()
        await self.service.aclose()
        return receipt

    async def _close_unclosed_handle(self) -> None:
        """重试关闭未收口宿主（``AgentRuntimeHandle.close`` 按资源粒度幂等）。"""

        handle = self.unclosed_handle
        if handle is None:
            return
        from utils.thread_pool import TaskType, ThreadPoolManager

        try:
            await ThreadPoolManager().run_async(TaskType.IO, handle.close)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[AgentWiring] retry closing unclosed agent runtime handle failed: %s",
                type(e).__name__,
                exc_info=True,
            )
            return
        self.unclosed_handle = None
        if self.instance_guard is not None:
            self.instance_guard.release()
            self.instance_guard = None


class _ProcessDispatchGate:
    """进程内派发门闩。

    未 launch 的宿主没有任何派发路径，门闩恒为「已关闭」——这是可核事实
    （launch 门闩推迟派发），不是把未知伪装成已关闭。已 launch 时须先真正关闭
    派发才返回 ``True``。
    """

    def __init__(self, *, launched: bool) -> None:
        self._launched = launched
        self._closed = False

    def close_dispatch(self) -> bool:
        self._closed = True
        return True

    def dispatch_closed(self) -> bool:
        return self._closed or not self._launched


class _PendingCloseFactSink:
    """结束/unknown 事实端口。

    生产派发路径尚未接线（launch 门闩推迟），未 launch 的宿主没有在途 operation
    需要落结束/unknown，如实返回 ``saved=True``。若未来已 launch，结束/unknown 的
    持久化落点尚未接线，不得谎报已保存（R21）——返回 ``saved=False`` 让关闭前置
    如实报未就绪。
    """

    def __init__(self, *, launched: bool) -> None:
        self._launched = launched

    async def save_end_or_unknown(self, command: CloseCommandIdentity) -> CloseFactOutcome:
        if self._launched:
            return CloseFactOutcome(saved=False)
        return CloseFactOutcome(saved=True)


def _gated_launcher(*, launch_allowed: bool) -> Callable[[AgentRuntimeHandle], None]:
    """launch 门闩：needs-spike 未通过时只记录「推迟」，不调用 SDK launch。"""

    def _launch(handle: AgentRuntimeHandle) -> None:
        if not launch_allowed:
            logger.info(
                "[AgentWiring] launch deferred: needs-spike (HANDOFF §8.4) not confirmed; "
                "host assembled and startup isolation passed, runtime stays unlaunched (zero dispatch)"
            )
            return
        launch_agent_runtime(handle)

    return _launch


def _application_version() -> str:
    """工作流代码版本（ADR-0017：pyproject version 唯一正本）。"""

    try:
        return version("astock-screener")
    except Exception:  # 未安装发行版元数据（源码直跑）：保持真实代码版本不可得时显式落 "dev"
        return "dev"


def _agent_runtime_sync_url(engine: Any) -> str:
    """由共享引擎 URL 派生 Agent 运行库同步 psycopg URL（同实例、独立逻辑库）。"""

    return engine.url.set(  # type: ignore[no-any-return]  # [reason: URL.set/渲染返回 str，Any 引擎使静态推断退化为 Any]
        drivername="postgresql+psycopg",
        database=AGENT_RUNTIME_DATABASE,
    ).render_as_string(hide_password=False)


async def wire_agent_runtime(
    cache_manager: Any,
    *,
    user_scope: str = _LOCAL_USER_SCOPE,
    isolation_id: str | None = None,
    assemble_func: Callable[[AgentRuntimeSettings], Any] | None = None,
    launch_func: Callable[[AgentRuntimeHandle], None] | None = None,
    instance_guard: Any | None = None,
    blocking_runner: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None,
    launch_allowed: bool | None = None,
) -> AgentWiring:
    """生产组合根接线（P01）：facade + 共享桥 + 宿主/启动隔离 + 关闭前置。

    - facade 必须复用共享 ``cache_manager.agent_dao``（共账本，不另起引擎）；
    - 宿主经 ``start_agent_runtime``：唯一执行锁 → 组装 → 启动隔离 → **就绪才
      launch**；隔离失败/组装失败一律零派发且不构造关闭链；
    - launch 由 ``AGENT_RUNTIME_LAUNCH_ALLOWED``（needs-spike）门闩控制，
      未通过时宿主保持 unlaunched。

    接线失败降级为零派发（``failure`` 如实记录），不阻断既有业务启动。
    ``CancelledError`` 继续传播（R2）。
    """

    bridge = OwnerLoopBridge.for_running_loop()
    dao = getattr(cache_manager, "agent_dao", None)
    engine = getattr(cache_manager, "engine", None)
    if dao is None or engine is None:
        logger.warning("[AgentWiring] shared engine/ledger unavailable; agent runtime not wired (zero dispatch)")
        return AgentWiring(service=None, bridge=bridge, failure="shared_ledger_unavailable")

    service = AgentService(dao)
    try:
        runtime_url = _agent_runtime_sync_url(engine)
    except Exception as e:
        logger.warning("[AgentWiring] agent runtime url unresolved: %s (zero dispatch)", type(e).__name__)
        return AgentWiring(service=service, bridge=bridge, failure="runtime_url_unresolved")

    allow_launch = AGENT_RUNTIME_LAUNCH_ALLOWED if launch_allowed is None else launch_allowed
    settings = AgentRuntimeSettings(
        system_database_url=runtime_url,
        application_version=_application_version(),
    )
    try:
        outcome = await start_agent_runtime(
            settings,
            user_scope=user_scope,
            isolation_id=isolation_id or f"startup-{uuid.uuid4().hex}",
            registry=dao,
            assemble_func=assemble_func,
            launch_func=launch_func if launch_func is not None else _gated_launcher(launch_allowed=allow_launch),
            instance_guard=instance_guard,
            blocking_runner=blocking_runner,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("[AgentWiring] agent runtime startup failed: %s (zero dispatch)", type(e).__name__)
        return AgentWiring(service=service, bridge=bridge, failure=f"startup_failed:{type(e).__name__}")

    if outcome.handle is None:
        failure = outcome.failure
        if failure is None:
            reasons = ",".join(outcome.isolation.blocking_reasons) if outcome.isolation is not None else ""
            failure = f"isolation_not_ready:{reasons}" if reasons else "isolation_not_ready"
        logger.warning(
            "[AgentWiring] agent runtime not ready: %s (zero dispatch)",
            failure,
        )
        return AgentWiring(
            service=service,
            bridge=bridge,
            isolation=outcome.isolation,
            failure=failure,
            unclosed_handle=outcome.unclosed_handle,
            instance_guard=outcome.instance_guard,
        )

    handle = outcome.handle
    launched = bool(getattr(handle, "launched", False))
    runtime = AgentRuntime(
        handle=handle,
        bridge=bridge,
        dispatch_gate=_ProcessDispatchGate(launched=launched),
        fact_sink=_PendingCloseFactSink(launched=launched),
        blocking_runner=cast(Any, blocking_runner),
    )
    logger.info(
        "[AgentWiring] agent runtime host ready (launched=%s, isolation_ready=%s)",
        launched,
        outcome.isolation.ready if outcome.isolation is not None else None,
    )
    return AgentWiring(
        service=service,
        bridge=bridge,
        isolation=outcome.isolation,
        launched=launched,
        handle=handle,
        runtime=runtime,
        instance_guard=outcome.instance_guard,
    )
