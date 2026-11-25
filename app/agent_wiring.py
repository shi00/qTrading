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
   - 迁移/运行凭据分离：迁移使用独立高权限连接（环境变量
     ``AGENT_RUNTIME_MIGRATION_DATABASE_URL`` 或 keyring），运行池只用运行凭据；
     未配置时外置模式拒绝启动（零派发），仅本机内置单用户模式显式告警共用；
   - 关闭前置经 ``AgentRuntime.prepare_close``（既有关闭链：冻结桥提交 → 关派发 →
     核对已 launch 宿主的 workflow 终态（原生事实）→ 保存结束/unknown → 关闭
     DBOS/Client/引擎），在释放共享引擎之前调用。

**launch 门闩**：needs-spike（HANDOFF §8.4：真 PG/真进程隔离实证）默认保持关闭——
宿主照常组装并执行启动隔离，但 launch 与派发显式推迟（零派发）。门闩由显式
配置项 ``AGENT_RUNTIME_LAUNCH_ALLOWED``（环境变量）派生，**默认关闭**；开启需
显式配置，代码不硬编码 True。

**运行链装配（R2-I01）**：

- 叶 runner 在组装期登记（``install_leaf_runtime``，含 GatewayModel 工厂：调用
  身份只由叶持久执行身份派生）；
- MCP 生命周期在组合根构造（``app.agent_mcp.build_mcp_lifecycle``）：真实
  FastMCP 传输 + ``app_state`` 配置/授权 + stdio 实现指纹；构造期零连接，
  未显式启连时工具工厂返回 ``None``（零工具、零外发）；
- 宿主就绪后构造 ``ResearchDispatcher``（受理回执 → 持久父研究 workflow），
  计划来源与投递/读回端口由组合根注入；
- 答复投递（P1-05）：已 launch 宿主复用同一 DBOS client 注入 ``AnswerSender``
  （按原答复命令身份投递，幂等键 = 客户端命令键）；未 launch 不注入，答复交付
  保持 fail-closed（保存成功但如实停留 not_delivered）；
- 答复消费（P1-05）：登记 ``AnswerConsumerRegistry``（同一用户域 + 共享 DAO），
  原生父 workflow 的 ``recv`` 步骤据此做幂等 CAS 消费（投递 → 消费闭环）；
- 派发门闩与关闭链共用同一 ``_ProcessDispatchGate``：关闭派发后新提交零派发。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final, Literal, cast

from dbos import WorkflowStatusString

from app.agent_mcp import build_mcp_lifecycle
from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    AnswerCommandFact,
    ArtifactDeliveryCommand,
    ArtifactReadQuery,
    CommandRef,
    ConsumeAnswerRequest,
    ConsumptionFact,
    DELIVERY_FORMAT_RULE_VERSION,
    DELIVERY_RENDER_COLUMNS,
    DeliveryField,
    DeliveryReceipt,
    DeliveryTarget,
    DispatchCloseRequest,
    aggregate_delivery_source_state,
    build_delivery_fields,
    canonical_delivery_source_state,
    derive_delivery_id,
    stable_id,
    validate_delivery_target_path,
)
from services.agent_service import (
    AgentRuntime,
    AgentRuntimeHandle,
    AgentRuntimeSettings,
    AgentService,
    CloseReadinessReceipt,
    LeafRuntimeWiring,
    ResearchDispatcher,
    ResearchWorkflowResult,
    StartupIsolationReceipt,
    install_leaf_runtime,
    launch_agent_runtime,
    start_agent_runtime,
)
from services.agent_service.capabilities import PlanProposalStore, PlanStoreBinding
from services.agent_service.cleanup import CleanupService, DaoCleanupJobStore
from services.agent_service.cleanup_execution import CleanupExecutionService
from services.agent_service.cleanup_native import NativeCleanupExecutor
from services.agent_service.cleanup_ports import DaoCleanupAdmissionFacts, DaoNativeVerification, DbosDeletionClient
from services.agent_service.delivery import WindowsManagedFileStore, _snapshot
from services.agent_service.delivery_render import render_delivery
from services.agent_service.delivery_service import DeliveryCommandService, DeliveryContext
from services.agent_service.dispatch import (
    PlanSpecProvider,
    RootWorkflowResultReader,
    RootWorkflowSubmitter,
    build_binding_identity_provider,
)
from services.agent_service.leaf_runtime import default_leaf_runtime
from services.agent_service.loop import AgentTurn, ResearchResultProducer
from services.agent_service.main_turn import (
    MainTurnIntake,
    MainTurnRunner,
    build_plan_spec_provider,
    build_role_capability_provider,
)
from services.agent_service.main_turn_model import build_governed_turn_factory
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle
from services.agent_service.recovery import (
    ISOLATION_PAGE_SIZE,
    STARTUP_ACTIVE_STATUSES,
    STARTUP_WORKFLOW_NAMES,
)
from services.agent_service.runtime import (
    RUNTIME_DB_NAME,
    CloseCommandIdentity,
    CloseFactOutcome,
    OwnerLoopBridge,
)
from services.agent_service.skills import ManagedSkillStore, SkillCatalogService
from services.agent_service.tool_runtime import build_tool_runtime_factory
from services.agent_service.units import UnitConversion
from services.agent_service.workflows import (
    ANSWER_MESSAGE_TOPIC,
    AnswerConsumerRegistry,
    install_terminal_state_dao,
    submit_research_workflow,
)
from utils.config.db import (
    AGENT_MIGRATION_DB_URL_ENV,
    DatabaseMode,
    get_agent_migration_db_url,
    resolve_database_mode,
)
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

#: launch 门闩配置项（环境变量，默认关闭；needs-spike 通过后显式开启）。
AGENT_RUNTIME_LAUNCH_ALLOWED_ENV: Final[str] = "AGENT_RUNTIME_LAUNCH_ALLOWED"

#: Agent 运行库逻辑库名（与业务库同 PG 实例、独立逻辑库；见 data.persistence.agent_migrator）。
AGENT_RUNTIME_DATABASE: Final[str] = "qtrading_agent_runtime"

#: 桌面单用户域（Agent 用户域唯一来源）。
_LOCAL_USER_SCOPE: Final[str] = "local-desktop"

#: 关闭前置预算（先于 watchdog/do_cleanup 执行，须有界以免拖死退出路径）。
AGENT_CLOSE_PRELUDE_TIMEOUT_S: Final[float] = 15.0

#: 已 launch 宿主的 workflow 终态核对预算（关闭前置与持久化事实各一次，有界）。
AGENT_LAUNCHED_WORKFLOW_VERIFY_TIMEOUT_S: Final[float] = 5.0

#: 未收口宿主关闭预算（本步先于 watchdog 执行，须有界：延迟的工作流执行不得拖死退出路径）。
AGENT_UNCLOSED_HANDLE_CLOSE_TIMEOUT_S: Final[float] = 10.0

#: 未收口宿主关闭的最大投递次数（一次关闭只投递一次；失效后如实报告，不作无界重投）。
AGENT_UNCLOSED_HANDLE_MAX_ATTEMPTS: Final[int] = 1

#: 原生终态（核到即视为该 workflow 已收敛；在途状态见 ``STARTUP_ACTIVE_STATUSES``）。
_LAUNCHED_WORKFLOW_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        WorkflowStatusString.SUCCESS.value,
        WorkflowStatusString.ERROR.value,
        WorkflowStatusString.CANCELLED.value,
        WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value,
    }
)


def agent_unit_converter() -> UnitConversion:
    """返回统一换算入口（``strategies.utils.threshold_in_data_unit``）。"""

    from strategies.utils import threshold_in_data_unit

    return threshold_in_data_unit


def agent_runtime_launch_allowed() -> bool:
    """launch 门闩取值（显式配置派生，默认关闭）。

    未配置 / 取值不识别时保持关闭（fail-closed）：无法证明已通过 needs-spike
    实证就不允许生产 launch 与派发。
    """

    return os.environ.get(AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AgentCleanupWiring:
    """清理链路的生产装配（I81）：作业存储 + 准入事实 + 原生执行器 + 编排。

    构造成功只表示端口已接线；是否允许清理仍由权威事实逐次判定（无事实即拒绝）。
    """

    store: DaoCleanupJobStore
    jobs: CleanupService
    executor: NativeCleanupExecutor
    execution: CleanupExecutionService


@dataclass(frozen=True)
class ClosePreludeReport:
    """未收口宿主关闭的可报告结果（不把在途/失效伪装成已关闭）。

    - ``idle``：无未收口宿主（启动阶段已收口或从未组装）；
    - ``pending``：隔离失败保留了句柄，尚未投递关闭；
    - ``in_flight``：本次关闭投递在途（其余入口共享该次结果，不重复投递）；
    - ``closed``：句柄已确认关闭并释放唯一执行锁；
    - ``failed``：本次投递失效（超时/异常/投递次数用尽），句柄保留、关闭门保持未就绪。
    """

    state: Literal["idle", "pending", "in_flight", "closed", "failed"]
    detail: str | None = None


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
    dispatcher: ResearchDispatcher | None = None
    cleanup: AgentCleanupWiring | None = None
    cleanup_failure: str | None = None
    #: MCP 生命周期门面（构造期零连接；关闭前置逐个断开已连服务）。
    mcp_lifecycle: MCPServiceLifecycle | None = None
    #: 首问 / 开始计划受理入口（组合根注入；未就绪时为 ``None``，UI 如实拒绝）。
    intake: MainTurnIntake | None = None
    #: 主回合生产构造（默认注入受治理模型回合工厂；构造期零外发）。
    main_turn: MainTurnRunner | None = None
    #: Agent 用户域（与受理/派发/回合身份同源）。
    user_scope: str = _LOCAL_USER_SCOPE
    #: 已 launch 宿主的 workflow 收敛事实（关闭前置与关闭门闩共用同一份核对结果）。
    launched_ledger: _LaunchedWorkflowLedger | None = None
    unclosed_handle: AgentRuntimeHandle | None = None
    instance_guard: Any | None = None
    #: 成果预览/导出端口（W-03；构造失败为 ``None``，UI 如实显示未接线）。
    delivery: AgentArtifactDelivery | None = None
    _close_command: CloseCommandIdentity | None = None
    _receipt: CloseReadinessReceipt | None = None
    #: MCP 关停未核事实（连接未确认断开）：进入关闭回执阻塞项，不得报「已安全关闭」。
    _mcp_close_blockers: tuple[str, ...] = ()
    #: 关闭前置单飞任务：多入口（窗口/断连/升级退出/切库）并发共享同一条关闭链。
    _prelude_task: asyncio.Task[CloseReadinessReceipt | None] | None = None
    #: 未收口宿主关闭的单次投递任务（单飞 + 有界；一次关闭只投递一次）。
    _unclosed_close_task: asyncio.Task[None] | None = None
    _unclosed_close_attempts: int = 0
    _unclosed_close_failure: str | None = None

    @property
    def ready(self) -> bool:
        """宿主就绪（组装成功且启动隔离就绪）；未就绪不得有任何派发/关闭链。"""

        return self.handle is not None and self.runtime is not None

    def dispatch_closed(self) -> bool:
        """当前是否零派发（未 launch 的宿主没有任何派发路径）。"""

        return not self.launched

    def close_ready(self) -> bool:
        """关闭门是否已给出「可释放共享引擎/账本/数据库」的证据。

        有宿主时只认 ``ready=True`` 的关闭回执；宿主从未组装（无 runtime 且无
        待收口 handle）时为可核事实——没有任何 Agent 派发入口/连接。未就绪即
        不得释放依赖（R2-I35）。MCP 连接未确认断开属未核事实，一律不 ready
        （含宿主未 launch/未组装时），不得把「仅告警」当已收尾。
        """

        if self._mcp_close_blockers:
            return False
        if self.runtime is not None:
            return self._receipt is not None and self._receipt.ready
        return self.unclosed_handle is None

    @property
    def close_report(self) -> ClosePreludeReport:
        """未收口宿主的关闭可报告结果（在途/失效/已关闭，不隐藏失败）。"""

        task = self._unclosed_close_task
        if task is not None and not task.done():
            return ClosePreludeReport(state="in_flight")
        if self.unclosed_handle is None:
            return ClosePreludeReport(state="closed" if self._unclosed_close_attempts else "idle")
        if self._unclosed_close_failure is not None:
            return ClosePreludeReport(state="failed", detail=self._unclosed_close_failure)
        return ClosePreludeReport(state="pending")

    async def prepare_close(self) -> CloseReadinessReceipt | None:
        """关闭前置（单次投递）：并发入口共享同一条关闭链，不重复投递。

        窗口关闭/断连/升级退出/配置切换可能同时在途调用本方法：同一时刻至多
        一条关闭链在投递中，其余入口复用其结果（``asyncio.shield`` 只撤等待者、
        不撤共享链），因此一次关闭只投递一次；关闭链结束后（就绪/未就绪）后续
        调用可重试。未收口宿主的关闭另受 :attr:`close_report` 的有界投递约束。

        其余语义（关闭顺序、未就绪不关 facade、取消传播）见
        :meth:`_run_close_prelude`。
        """

        task = self._prelude_task
        if task is None or task.done():
            task = asyncio.create_task(self._run_close_prelude())
            self._prelude_task = task
        return await asyncio.shield(task)

    async def _run_close_prelude(self) -> CloseReadinessReceipt | None:
        """关闭前置主体：先收敛/释放 Agent 宿主，再由调用方释放共享引擎。

        顺序：已 launch 宿主先按原生 workflow 终态核对收敛事实 → ``AgentRuntime.
        prepare_close``（既有关闭链，探针据同一份事实判定）→ facade ``aclose``。
        未就绪（宿主未组装/隔离失败）时为 no-op，不启动关闭链。不触碰他层资源
        （Recorder、业务引擎、内置 PG 由调用方按原协调顺序关闭）。

        **未就绪不关 facade**：关闭门未给出就绪证据（在途 IO/残留未核定/超时/
        异常）时保留服务可用面，调用方必须据 :meth:`close_ready` 阻断后续清理，
        不得先释放账本/引擎/数据库（R2-I35）。

        ``CancelledError`` 原样传播（R2）；超时/普通异常按「未就绪」返回，由调用方
        据 :meth:`close_ready` 阻断后续释放。
        """

        mcp_blockers = await self._disconnect_mcp_sessions()
        self._mcp_close_blockers = mcp_blockers
        if self.service is None:
            return None
        if self._close_command is None:
            self._close_command = CloseCommandIdentity(_LOCAL_USER_SCOPE, f"app-close-{uuid.uuid4().hex}")
        receipt = self._receipt
        if self.runtime is not None and (receipt is None or not receipt.ready):
            # 关闭门闩的残余探针是同步读面：先按真实原生事实刷新一次，再进关闭链。
            await self._verify_launched_workflows()
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
            # 隔离/launch 失败时未收口的宿主：单次有界投递关闭并释放唯一执行锁。
            await self._close_unclosed_handle()
            report = self.close_report
            if report.state == "failed":
                logger.error(
                    "[AgentWiring] unclosed host close failed (%s); close gate stays blocked",
                    report.detail,
                )
        if mcp_blockers and receipt is not None:
            # 未确认断开的 MCP 连接是关闭回执的未核事实：阻止「已安全关闭」结论
            # （含未 launch 的宿主），由调用方保留依赖并在重试成功后放行。
            receipt = replace(
                receipt,
                ready=False,
                blocking_reasons=(*receipt.blocking_reasons, *mcp_blockers),
            )
            self._receipt = receipt
            logger.warning(
                "[AgentWiring] mcp close incomplete: %s (host close stays unready)",
                ",".join(mcp_blockers),
            )
        if self.close_ready():
            if self.runtime is not None:
                # 唯一执行锁已由关闭链在就绪后释放：清引用避免重复 release。
                self.instance_guard = None
            await self.service.aclose()
        return receipt

    async def _verify_launched_workflows(self) -> None:
        """关闭前按原生事实刷新收敛快照（未 launch/无 ledger 时为 no-op）。

        核对失败由 ledger 如实记入残余项，关闭链随后继续阻断；这里不吞取消（R2），
        也不把核对失败当成功。
        """

        ledger = self.launched_ledger
        if ledger is None:
            return
        await ledger.verify()

    async def _disconnect_mcp_sessions(self) -> tuple[str, ...]:
        """关闭前置：断开本进程显式建立的 MCP 连接。

        在途/冻结使用者由共享连接管理器拒停；断开失败如实返回未核事实
        （``mcp_disconnect_failed:<identity>``），由 :meth:`prepare_close` 写入
        关闭回执阻塞项——不把「仅告警」当已收尾（R21），也不阻断其余关闭步骤。
        """

        lifecycle = self.mcp_lifecycle
        if lifecycle is None:
            return ()
        blockers: list[str] = []
        for identity in lifecycle.connected_identities():
            try:
                await lifecycle.disconnect(identity)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[AgentWiring] mcp disconnect failed: %s (%s)",
                    type(e).__name__,
                    identity.stable_key(),
                )
                blockers.append(f"mcp_disconnect_failed:{identity.stable_key()}")
        return tuple(blockers)

    async def _close_unclosed_handle(self) -> None:
        """投递一次有界关闭（单飞 + 有界重试），不阻断关闭链。

        - **单飞**：并发入口共享同一条关闭任务，一次关闭只投递一次（不再各关一次）；
        - **有界**：单次关闭受 ``AGENT_UNCLOSED_HANDLE_CLOSE_TIMEOUT_S`` 限制，延迟的
          工作流执行不得拖死退出路径（本步先于 watchdog 执行）；
        - **有界重试**：同一句柄至多投递 ``AGENT_UNCLOSED_HANDLE_MAX_ATTEMPTS`` 次，
          超限后如实报告失效（:attr:`close_report`），不作无界重投。
        """

        handle = self.unclosed_handle
        if handle is None:
            return
        task = self._unclosed_close_task
        if task is not None and not task.done():
            # 已有投递在途：共享该次结果（取消只撤等待者；CancelledError 原样传播，R2）
            await asyncio.shield(task)
            return
        if self._unclosed_close_attempts >= AGENT_UNCLOSED_HANDLE_MAX_ATTEMPTS:
            if self._unclosed_close_failure is None:
                self._unclosed_close_failure = "close_attempts_exhausted"
            logger.error(
                "[AgentWiring] unclosed agent runtime handle close not retried: attempts exhausted (%d)",
                self._unclosed_close_attempts,
            )
            return
        self._unclosed_close_attempts += 1
        task = asyncio.create_task(self._close_unclosed_handle_once(handle))
        self._unclosed_close_task = task
        await asyncio.shield(task)

    async def _close_unclosed_handle_once(self, handle: AgentRuntimeHandle) -> None:
        """执行一次有界关闭：成功即清句柄/释放唯一执行锁，超时或异常如实记为失效。"""

        from utils.thread_pool import TaskType, ThreadPoolManager

        try:
            await asyncio.wait_for(
                ThreadPoolManager().run_async(TaskType.IO, handle.close),
                timeout=AGENT_UNCLOSED_HANDLE_CLOSE_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._unclosed_close_failure = "close_timeout"
            logger.error(
                "[AgentWiring] unclosed agent runtime handle close timed out after %.1fs; "
                "host stays unclosed (close gate stays blocked)",
                AGENT_UNCLOSED_HANDLE_CLOSE_TIMEOUT_S,
            )
            return
        except Exception as e:
            self._unclosed_close_failure = type(e).__name__
            logger.error(
                "[AgentWiring] retry closing unclosed agent runtime handle failed: %s",
                type(e).__name__,
                exc_info=True,
            )
            return
        self._unclosed_close_failure = None
        self.unclosed_handle = None
        if self.instance_guard is not None:
            self.instance_guard.release()
            self.instance_guard = None
        logger.info("[AgentWiring] unclosed agent runtime handle closed; instance guard released")


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


class _InFlightPortIo:
    """组合根端口调用的在途计数（关闭门闩的残余 IO 证据）。

    只记录「已提交但尚未返回」的端口调用，不改变派发/读回语义；关闭门闩据
    ``residual_names`` 判定所有本地/外部在途是否收敛（R2-I35）。
    """

    def __init__(self) -> None:
        self._pending: dict[str, int] = {}

    def enter(self, name: str) -> None:
        self._pending[name] = self._pending.get(name, 0) + 1

    def exit(self, name: str) -> None:
        remaining = self._pending.get(name, 0) - 1
        if remaining > 0:
            self._pending[name] = remaining
        else:
            self._pending.pop(name, None)

    def residual_names(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, count in self._pending.items() if count > 0))


class _NativeAnswerSender:
    """生产答复发送器（P1-05）：复用已 launch 宿主的 DBOS client 投递答复消息。

    - 只投递已保存答复（按原 ``workflow_id`` 目标 + ``client_command_id`` 幂等键），
      不新建答复、不改命令状态；
    - 派发门闩关闭（未 launch / 关闭链已关派发）→ 拒绝（``PERSISTENCE_UNAVAILABLE``），
      不伪造已投递；
    - 成功后仅代表「已交原生消息队列」，消费仍需原生 workflow 侧确认（INV-06）。
    """

    def __init__(self, *, client: Any, gate: _ProcessDispatchGate, port_io: _InFlightPortIo) -> None:
        self._client = client
        self._gate = gate
        self._port_io = port_io

    async def send_answer(self, fact: AnswerCommandFact) -> None:
        identity = f"answer/{fact.client_command_id}"
        if self._gate.dispatch_closed():
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "answer delivery closed: dispatch gate closed",
                identity,
            )
        payload = json.loads(fact.request_payload)
        if not isinstance(payload, dict):
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "answer payload is not a wire object",
                identity,
            )
        message = {
            "schema_version": 1,
            "decision_id": fact.decision_id,
            "execution_id": fact.execution_id,
            "round_id": fact.round_id,
            "client_command_id": fact.client_command_id,
            "answer": payload,
        }
        self._port_io.enter("answer_send")
        try:
            await self._client.send_async(
                fact.workflow_id,
                message,
                topic=ANSWER_MESSAGE_TOPIC,
                idempotency_key=fact.client_command_id,
            )
        finally:
            self._port_io.exit("answer_send")


def _build_answer_consumer(dao: Any, user_scope: str) -> Callable[[str, str], Awaitable[ConsumptionFact]]:
    """生产答复消费者（P1-05）：按保存时同一命令身份派生并走 DAO 幂等 CAS。

    命令身份派生与 ``AgentDao.save_answer`` 一致（``stable_id("command",
    [user_scope, decision_id, client_command_id])``），不新建答复、不改投递事实；
    已消费重放由 DAO 返回原事实（重复投递只消费一次）。
    """

    async def _consume(decision_id: str, client_command_id: str) -> ConsumptionFact:
        command_record_id = stable_id("command", [user_scope, decision_id, client_command_id])
        return await dao.consume_answer(
            ConsumeAnswerRequest(user_scope=user_scope, command_record_id=command_record_id)
        )

    return _consume


class _PendingCloseFactSink:
    """结束/unknown 事实端口（未 launch 宿主）。

    未 launch 的宿主没有派发路径、没有在途 workflow，结束/unknown 无事实可落，
    如实返回 ``saved=True``（可核事实，不是把未知伪装成已保存）。已 launch 宿主
    走 :class:`_LaunchedCloseFactSink`（按原生终态核对 + 持久派发关闭事实）。
    """

    def __init__(self, *, launched: bool) -> None:
        self._launched = launched

    async def save_end_or_unknown(self, command: CloseCommandIdentity) -> CloseFactOutcome:
        if self._launched:
            return CloseFactOutcome(saved=False)
        return CloseFactOutcome(saved=True)


class _LaunchedWorkflowLedger:
    """已 launch 宿主的 workflow 收敛事实（真实原生核对，非常量）。

    - ``track``：投递成功的根 workflow 身份（关闭时须逐个核到终态）；
    - ``verify``：按原生事实刷新——本应用/版本下仍在途的 workflow（含叶）与已
      跟踪 workflow 的原生状态；查询失败/超时如实记录，不冒充已收敛（R21）；
    - ``residual_names``：关闭门闩的**同步**探针读面（两次调用之间只读缓存事实）；
      核对之后才出现的 workflow 无终态证据，按 ``tracked - terminal - in_flight``
      计入未定项，故「关闭中再提交」不会被误放行。

    只读原生控制面（``DBOSClient``），不写业务事实；单次核对有界（不拖死退出）。
    """

    def __init__(self, *, client: Any, application_version: str, timeout_s: float) -> None:
        self._client = client
        self._application_version = application_version
        self._timeout_s = timeout_s
        self._tracked: set[str] = set()
        self._active_ids: frozenset[str] = frozenset()
        self._terminal_ids: frozenset[str] = frozenset()
        self._in_flight_ids: frozenset[str] = frozenset()
        self._verified = False
        self._query_failure: str | None = None

    @property
    def tracked_ids(self) -> tuple[str, ...]:
        """本进程投递成功的根 workflow 身份（稳定排序）。"""

        return tuple(sorted(self._tracked))

    def track(self, workflow_id: str) -> None:
        """登记一次成功投递的根 workflow（仅提交返回后调用）。"""

        if workflow_id:
            self._tracked.add(workflow_id)

    def residual_names(self) -> tuple[str, ...]:
        """残余项（精确原因 + 计数；无残余即空元组）。"""

        if self._query_failure is not None:
            return (f"launched_workflows_query_failed:{self._query_failure}",)
        if not self._verified:
            return ("launched_workflows_unverified",)
        residual: list[str] = []
        if self._in_flight_ids:
            residual.append(f"launched_workflows_in_flight:{len(self._in_flight_ids)}")
        unsettled = self._tracked - self._terminal_ids - self._in_flight_ids
        if unsettled:
            residual.append(f"launched_workflows_unsettled:{len(unsettled)}")
        return tuple(residual)

    def unresolved_ids(self) -> tuple[str, ...]:
        """未核到终态的身份（无法核对时即全部已跟踪身份，不缩小未知）。"""

        if self._query_failure is not None or not self._verified:
            return self.tracked_ids
        return tuple(sorted((self._tracked - self._terminal_ids) | self._active_ids))

    async def verify(self) -> None:
        """按原生事实刷新收敛快照；超时/异常如实记录，``CancelledError`` 传播。"""

        try:
            async with asyncio.timeout(self._timeout_s):
                active_ids = await self._list_active_workflow_ids()
                states = await self._list_tracked_states()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._query_failure = "TimeoutError"
            self._verified = False
            logger.warning(
                "[AgentWiring] launched workflow verification timed out after %.1fs; close stays blocked",
                self._timeout_s,
            )
            return
        except Exception as e:
            self._query_failure = type(e).__name__
            self._verified = False
            logger.warning(
                "[AgentWiring] launched workflow verification failed (%s); close stays blocked",
                type(e).__name__,
            )
            return
        self._active_ids = active_ids
        self._terminal_ids = frozenset(
            tid for tid in self._tracked if states.get(tid) in _LAUNCHED_WORKFLOW_TERMINAL_STATUSES
        )
        self._in_flight_ids = (
            frozenset(tid for tid, state in states.items() if state in STARTUP_ACTIVE_STATUSES) | active_ids
        )
        self._query_failure = None
        self._verified = True
        logger.info(
            "[AgentWiring] launched workflows verified (tracked=%d, in_flight=%d, terminal=%d)",
            len(self._tracked),
            len(self._in_flight_ids),
            len(self._terminal_ids),
        )

    async def _list_active_workflow_ids(self) -> frozenset[str]:
        """本应用/版本下仍在途的 workflow（含叶；分页读满后返回）。"""

        active: set[str] = set()
        offset = 0
        while True:
            page = await self._client.list_workflows_async(
                status=list(STARTUP_ACTIVE_STATUSES),
                name=list(STARTUP_WORKFLOW_NAMES),
                application_name=RUNTIME_DB_NAME,
                app_version=self._application_version,
                limit=ISOLATION_PAGE_SIZE,
                offset=offset,
                load_input=False,
                load_output=False,
            )
            for item in page:
                active.add(str(item.workflow_id))
            if len(page) < ISOLATION_PAGE_SIZE:
                return frozenset(active)
            offset += len(page)

    async def _list_tracked_states(self) -> dict[str, str]:
        """已跟踪 workflow 的原生状态（无行即不在结果中，不伪造终态）。"""

        states: dict[str, str] = {}
        tracked = sorted(self._tracked)
        for start in range(0, len(tracked), ISOLATION_PAGE_SIZE):
            chunk = tracked[start : start + ISOLATION_PAGE_SIZE]
            page = await self._client.list_workflows_async(
                workflow_ids=chunk,
                limit=len(chunk),
                load_input=False,
                load_output=False,
            )
            for item in page:
                states[str(item.workflow_id)] = str(item.status)
        return states


class _LaunchedCloseFactSink:
    """已 launch 宿主的结束/unknown 持久化端口。

    关闭链只在残余探针为空时调用本端口；这里再按原生事实复核一次（关闭前重核），
    然后在有可跟踪 workflow 时落**持久派发关闭事实**（``mark_dispatch_closed``：
    按精确 workflow 身份幂等，与启动隔离同一事实；现有 DAO 无独立 execution-ended
    写入口，属 P1-08 范围）。任一 workflow 未核到终态即 ``saved=False`` 并把精确
    身份作为 unknown 关联上报（不伪装已结束，R21）；落库失败异常上抛，由关闭链
    记为 ``close_facts_save_failed`` 继续阻断。
    """

    def __init__(self, *, ledger: _LaunchedWorkflowLedger, dao: Any, user_scope: str) -> None:
        self._ledger = ledger
        self._dao = dao
        self._user_scope = user_scope

    async def save_end_or_unknown(self, command: CloseCommandIdentity) -> CloseFactOutcome:
        await self._ledger.verify()
        unresolved = self._ledger.unresolved_ids()
        if unresolved:
            logger.warning(
                "[AgentWiring] close facts not saved: %d workflow(s) without terminal evidence",
                len(unresolved),
            )
            return CloseFactOutcome(saved=False, unknown_associations=unresolved)
        tracked = self._ledger.tracked_ids
        if tracked:
            await self._dao.mark_dispatch_closed(
                DispatchCloseRequest(user_scope=self._user_scope, workflow_ids=tracked)
            )
            logger.info("[AgentWiring] close facts persisted (workflows=%d)", len(tracked))
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


def _application_version() -> str | None:
    """工作流代码版本（ADR-0017：pyproject version 唯一正本）。

    元数据不可得时返回 ``None``（调用方 fail-closed）：共享的 ``"dev"`` 无法区分
    不同源码/制品版本，会让版本谓词隔离失效，不得用作代码版本。
    """

    try:
        found = version("astock-screener")
    except Exception:  # 未安装发行版元数据（源码直跑/打包缺 dist-info）：真实代码版本不可得
        return None
    return found.strip() or None


BlockingRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


def _blocking_runner(blocking_runner: BlockingRunner | None) -> BlockingRunner:
    """受管线程执行器（未注入时用 ThreadPoolManager.IO，不堵 UI，R16）。"""

    if blocking_runner is not None:
        return blocking_runner

    async def _run(fn: Callable[[], Any]) -> Any:
        from utils.thread_pool import TaskType, ThreadPoolManager

        return await ThreadPoolManager().run_async(TaskType.IO, fn)

    return _run


def _default_workflow_submitter(
    blocking_runner: BlockingRunner | None,
) -> RootWorkflowSubmitter:
    """生产投递端口：同步 DBOS 启动经受管线程执行（SetWorkflowID 在宿主入口内）。"""

    runner = _blocking_runner(blocking_runner)

    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        await runner(lambda: submit_research_workflow(workflow_id, payload))

    return _submit


def _default_result_reader(handle: AgentRuntimeHandle) -> RootWorkflowResultReader:
    """生产结果读回端口：按稳定 workflow 身份读原生状态与成功输出（不等待）。"""

    async def _read(workflow_id: str) -> ResearchWorkflowResult:
        from dbos import WorkflowStatusString

        native = await handle.client.retrieve_workflow_async(workflow_id)
        status = await native.get_status()
        state = str(status.status)
        output = status.output if state == WorkflowStatusString.SUCCESS.value else None
        if output is not None and not isinstance(output, dict):
            raise RuntimeError("research workflow output is not a wire dict")
        return ResearchWorkflowResult(workflow_id=workflow_id, state=state, output=output)

    return _read


def _agent_runtime_sync_url(engine: Any) -> str:
    """由共享引擎 URL 派生 Agent 运行库同步 psycopg URL（同实例、独立逻辑库）。"""

    return engine.url.set(  # type: ignore[no-any-return]  # [reason: URL.set/渲染返回 str，Any 引擎使静态推断退化为 Any]
        drivername="postgresql+psycopg",
        database=AGENT_RUNTIME_DATABASE,
    ).render_as_string(hide_password=False)


def _embedded_single_user_mode() -> bool:
    """是否为内置 PostgreSQL 单用户模式（迁移/运行共用凭据的唯一允许场景）。

    判定失败按「非单用户」处理（fail-closed）：无法证明本机单用户时不得放宽
    迁移/运行角色分离要求。
    """
    try:
        return resolve_database_mode().mode is DatabaseMode.EMBEDDED
    except Exception as e:  # 判定不可用时按外置处理，不静默放宽
        logger.debug("[AgentWiring] database mode resolution failed: %s", DataSanitizer.sanitize_error(e))
        return False


def _migration_credentials(migration_url: str | None) -> tuple[str | None, bool]:
    """解析迁移凭据使用方式，返回 ``(迁移 URL, 是否显式允许共用)``。

    - 已配置独立迁移 URL：直接使用（运行池仍只用运行 URL，真正分离）；
    - 未配置且本机内置单用户模式：显式允许共用并记录告警（仅限本机单用户）；
    - 未配置且非单用户（外置/无法判定）：返回 ``(None, False)``，调用方拒绝启动。

    日志只提配置项名，不输出连接串（R9）。
    """
    if migration_url:
        return migration_url, False
    if not _embedded_single_user_mode():
        logger.warning(
            "[AgentWiring] agent runtime migration credentials missing (%s): external database "
            "mode requires a dedicated high-privilege agent_migrator role (zero dispatch)",
            AGENT_MIGRATION_DB_URL_ENV,
        )
        return None, False
    logger.warning(
        "[AgentWiring] agent runtime migrations use the runtime credentials (embedded single-user "
        "mode only); configure %s for dedicated agent_migrator credentials",
        AGENT_MIGRATION_DB_URL_ENV,
    )
    return None, True


def _build_cleanup_wiring(*, handle: AgentRuntimeHandle, dao: Any) -> tuple[AgentCleanupWiring | None, str | None]:
    """装配清理链路（I81）；失败如实返回原因，不伪造「清理可用」。

    只装配端口：准入/核验仍逐次读权威事实，任何不可得即拒绝。删除调用由
    ``NativeCleanupExecutor`` 提交受管线程（R16）。
    """

    try:
        from data.persistence.agent_runtime_store import AgentRuntimeNativeStore

        store = DaoCleanupJobStore(dao)
        facts = DaoCleanupAdmissionFacts(dao)
        verification = DaoNativeVerification(dao)
        native_store = AgentRuntimeNativeStore(handle.runtime_engine)
        executor = NativeCleanupExecutor(
            store=native_store,
            client=DbosDeletionClient(handle.client),
            verification=verification,
        )
        jobs = CleanupService(store, facts)
        execution = CleanupExecutionService(jobs=jobs, store=store, dao=dao, executor=executor)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[AgentWiring] cleanup wiring unavailable: %s (cleanup disabled, not claimed ready)",
            type(e).__name__,
        )
        return None, f"cleanup_wiring_failed:{type(e).__name__}"
    logger.info("[AgentWiring] cleanup ports wired (admission facts + native verification)")
    return AgentCleanupWiring(store=store, jobs=jobs, executor=executor, execution=execution), None


def _build_skill_catalog(dao: Any, root: str | None) -> SkillCatalogService | None:
    """构造本地受管 Skill 目录服务；根目录不可用时降级为未接线（不阻断运行链）。"""

    if not root:
        return None
    try:
        return SkillCatalogService(ManagedSkillStore(root), dao)
    except (OSError, ValueError, AgentValidationError) as e:
        logger.warning("[AgentWiring] skill catalog unavailable: %s (skill lifecycle disabled)", type(e).__name__)
        return None


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
    leaf_runtime: LeafRuntimeWiring | None = None,
    mcp_lifecycle: MCPServiceLifecycle | None = None,
    plan_provider: PlanSpecProvider | None = None,
    workflow_submitter: RootWorkflowSubmitter | None = None,
    workflow_result_reader: RootWorkflowResultReader | None = None,
    skills_root: str | None = None,
    main_turn_turn: AgentTurn | None = None,
) -> AgentWiring:
    """生产组合根接线（P01/R2-I01）：facade + 共享桥 + 叶运行链 + 宿主/派发。

    - facade 必须复用共享 ``cache_manager.agent_dao``（共账本，不另起引擎）；
    - 叶 runner 在宿主组装前登记（``install_leaf_runtime``）：未安装时叶执行
      明确拒绝，不以占位执行器冒充；安装失败一律零派发；
    - 宿主经 ``start_agent_runtime``：唯一执行锁 → 组装 → 启动隔离 → **就绪才
      launch**；隔离失败/组装失败一律零派发且不构造关闭链；
    - launch 由显式配置 ``agent_runtime_launch_allowed()``（默认关闭）控制；
      未 launch 时派发门闩保持关闭（零派发）；
    - 宿主就绪后构造 ``ResearchDispatcher``：受理成功后把请求投递给持久父研究
      workflow，结果经原生状态/输出读回；计划来源不可得时零派发。
    - ``skills_root`` 给定时接线上本地受管 Skill 目录（用户级启停/角色版本选择）；
      目录不可用降级为未接线，不阻断既有启动。

    接线失败降级为零派发（``failure`` 如实记录），不阻断既有业务启动。
    ``CancelledError`` 继续传播（R2）。
    """

    bridge = OwnerLoopBridge.for_running_loop()
    dao = getattr(cache_manager, "agent_dao", None)
    engine = getattr(cache_manager, "engine", None)
    if dao is None or engine is None:
        logger.warning("[AgentWiring] shared engine/ledger unavailable; agent runtime not wired (zero dispatch)")
        return AgentWiring(service=None, bridge=bridge, failure="shared_ledger_unavailable")

    usage_dao = getattr(cache_manager, "ai_usage_dao", None)
    skills = _build_skill_catalog(dao, skills_root)
    service = AgentService(dao, usage_dao, skills=skills)
    #: 答复消费（P1-05）：原生 workflow 的 recv 消费步骤按同一用户域 + 共享账本
    #: 消费（未 launch 时无 workflow，安装无副作用；步骤缺少消费者时明确拒绝）。
    AnswerConsumerRegistry.install(_build_answer_consumer(dao, user_scope))
    try:
        runtime_url = _agent_runtime_sync_url(engine)
    except Exception as e:
        logger.warning("[AgentWiring] agent runtime url unresolved: %s (zero dispatch)", type(e).__name__)
        return AgentWiring(service=service, bridge=bridge, failure="runtime_url_unresolved")

    lifecycle = mcp_lifecycle
    if lifecycle is None:
        try:
            lifecycle = build_mcp_lifecycle(engine=engine, dao=dao)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 构造失败只降级为「无 MCP 工具」，不阻断既有启动；不伪造已接线。
            logger.warning(
                "[AgentWiring] mcp lifecycle unavailable: %s (tools stay disabled)",
                type(e).__name__,
            )
            lifecycle = None
    if leaf_runtime is None:
        tool_factory = None if lifecycle is None else build_tool_runtime_factory(dao=dao, lifecycle=lifecycle)
        leaf_runtime = default_leaf_runtime(dao, tool_runtime_factory=tool_factory)
    try:
        install_leaf_runtime(leaf_runtime)
        # 父流程收敛后落应用执行终态（P1-08）：同一共享账本写入，不新建引擎。
        install_terminal_state_dao(dao)
    except Exception as e:  # 叶运行链未装好即零派发（launch/派发都不进行）
        logger.warning("[AgentWiring] leaf runtime install failed: %s (zero dispatch)", type(e).__name__)
        return AgentWiring(
            service=service,
            bridge=bridge,
            failure=f"leaf_runtime_install_failed:{type(e).__name__}",
            mcp_lifecycle=lifecycle,
        )

    allow_launch = agent_runtime_launch_allowed() if launch_allowed is None else launch_allowed
    application_version = _application_version()
    if application_version is None:
        logger.warning(
            "[AgentWiring] application version unavailable (distribution metadata missing): "
            "agent runtime not wired (zero dispatch)"
        )
        return AgentWiring(
            service=service,
            bridge=bridge,
            failure="application_version_unavailable",
            mcp_lifecycle=lifecycle,
        )
    migration_url, share_migration_credentials = _migration_credentials(get_agent_migration_db_url())
    if migration_url is None and not share_migration_credentials:
        return AgentWiring(
            service=service,
            bridge=bridge,
            failure="migration_credentials_missing",
            mcp_lifecycle=lifecycle,
        )
    settings = AgentRuntimeSettings(
        system_database_url=runtime_url,
        migration_database_url=migration_url,
        allow_shared_migration_credentials=share_migration_credentials,
        application_version=application_version,
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
        return AgentWiring(
            service=service,
            bridge=bridge,
            failure=f"startup_failed:{type(e).__name__}",
            mcp_lifecycle=lifecycle,
        )

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
            mcp_lifecycle=lifecycle,
            unclosed_handle=outcome.unclosed_handle,
            instance_guard=outcome.instance_guard,
        )

    handle = outcome.handle
    launched = bool(getattr(handle, "launched", False))
    gate = _ProcessDispatchGate(launched=launched)
    port_io = _InFlightPortIo()
    submit_port = workflow_submitter if workflow_submitter is not None else _default_workflow_submitter(blocking_runner)
    read_port = workflow_result_reader if workflow_result_reader is not None else _default_result_reader(handle)
    #: 已 launch 宿主才需要真实终态核对；未 launch 无派发路径（零 workflow）。
    ledger = (
        _LaunchedWorkflowLedger(
            client=handle.client,
            application_version=settings.application_version,
            timeout_s=AGENT_LAUNCHED_WORKFLOW_VERIFY_TIMEOUT_S,
        )
        if launched
        else None
    )

    async def _tracked_submit(workflow_id: str, payload: dict[str, Any]) -> None:
        port_io.enter("research_submit")
        try:
            await submit_port(workflow_id, payload)
        finally:
            port_io.exit("research_submit")
        if ledger is not None:
            # 只在投递成功后登记：失败即无该 workflow，不得凭空要求终态。
            ledger.track(workflow_id)

    async def _tracked_read(workflow_id: str) -> ResearchWorkflowResult:
        port_io.enter("research_result_read")
        try:
            return await read_port(workflow_id)
        finally:
            port_io.exit("research_result_read")

    def _residual_io_probe() -> tuple[str, ...]:
        """关闭门闩的残余 IO 探针：端口在途 + 已 launch 宿主的 workflow 收敛事实。"""
        residual = list(port_io.residual_names())
        if ledger is not None:
            # 真实原生事实（终态/在途/未定项与计数），不再用常量一刀切（R2-I35/R21）。
            residual.extend(ledger.residual_names())
        return tuple(residual)

    dispatcher = ResearchDispatcher(
        gate=gate,
        submit=_tracked_submit,
        # 生产计划来源：冻结受理 manifest 的计划引用 → 已确认计划版 → 可执行节点表。
        plan_provider=plan_provider if plan_provider is not None else build_plan_spec_provider(dao),
        read_result=_tracked_read,
        # 生产角色能力来源：只取本次请求冻结的 Skill/MCP 显式手选集（未选择即零派发）。
        capability_provider=build_role_capability_provider(dao),
        # 生产应用轮次绑定来源：冻结会话/输入版/执行代次，父 workflow 据此建轮次。
        binding_identity_provider=build_binding_identity_provider(
            dao, application_version=settings.application_version
        ),
    )
    fact_sink = (
        _LaunchedCloseFactSink(ledger=ledger, dao=dao, user_scope=user_scope)
        if ledger is not None
        else _PendingCloseFactSink(launched=False)
    )
    runtime = AgentRuntime(
        handle=handle,
        bridge=bridge,
        dispatch_gate=gate,
        fact_sink=fact_sink,
        blocking_runner=cast(Any, blocking_runner),
        residual_io_probe=_residual_io_probe,
        # 正常关闭必须把唯一执行锁交给关闭链：就绪关闭完成后才释放（同进程切库可接管）。
        instance_guard=outcome.instance_guard,
    )
    logger.info(
        "[AgentWiring] agent runtime host ready (launched=%s, isolation_ready=%s, dispatch_wired=True)",
        launched,
        outcome.isolation.ready if outcome.isolation is not None else None,
    )
    cleanup, cleanup_failure = _build_cleanup_wiring(handle=handle, dao=dao)
    #: 答复投递（P1-05）：已 launch 宿主复用同一 DBOS client 投递答复消息；
    #: 未 launch（零 workflow）不注入发送器 → facade 交付 fail-closed 拒绝。
    answer_sender = _NativeAnswerSender(client=handle.client, gate=gate, port_io=port_io) if launched else None
    facade = AgentService(dao, usage_dao, dispatcher=dispatcher, skills=skills, answer_sender=answer_sender)
    # 主回合生产构造：注入受治理模型回合工厂（复用 agent_research 场景路由/模型桥）；
    # 构造期零外发，路由不可用/缺配置时回合 fail-closed 并如实报错。
    turn_factory = (lambda _request: main_turn_turn) if main_turn_turn is not None else build_governed_turn_factory()
    main_turn = MainTurnRunner(
        producer=ResearchResultProducer(dao),
        plan_store_factory=lambda identity: PlanProposalStore(
            dao,
            PlanStoreBinding(user_scope=identity.user_scope, execution_id=identity.execution_id),
        ),
        turn_factory=turn_factory,
    )
    intake = MainTurnIntake(
        facade,
        manifest_reader=dao,
        user_scope=user_scope,
        main_turn=main_turn,
        # 执行模式首问：受理后先产出计划，再由入口显式派发（同一门闩/计划准入）。
        dispatcher=dispatcher,
    )
    return AgentWiring(
        service=facade,
        bridge=bridge,
        isolation=outcome.isolation,
        launched=launched,
        handle=handle,
        runtime=runtime,
        dispatcher=dispatcher,
        cleanup=cleanup,
        cleanup_failure=cleanup_failure,
        mcp_lifecycle=lifecycle,
        intake=intake,
        main_turn=main_turn,
        user_scope=user_scope,
        launched_ledger=ledger,
        instance_guard=outcome.instance_guard,
        delivery=_build_artifact_delivery(dao=dao, user_scope=user_scope),
    )


# ----------------------------------------------------------------------
# 成果预览/导出装配（W-03；组合根注入 UI，服务层交付管线不在此重写）
# ----------------------------------------------------------------------

#: 预览文本上限（字符）：仅限制 UI 显示，导出文件始终为完整内容（如实标注截断）。
_ARTIFACT_PREVIEW_CHAR_LIMIT: Final[int] = 20_000
#: 预览口径说明键（XLSX 字段表 / 截断；组合场景单独成键，不相互掩盖）。
_ARTIFACT_PREVIEW_FIELD_VIEW_KEY: Final[str] = "agent_research_artifact_preview_field_view"
_ARTIFACT_PREVIEW_TRUNCATED_KEY: Final[str] = "agent_research_artifact_preview_truncated"
_ARTIFACT_PREVIEW_FIELD_VIEW_TRUNCATED_KEY: Final[str] = "agent_research_artifact_preview_field_view_truncated"
#: 成果导出命令的来源模块（回执可溯源到 UI 入口）。
_ARTIFACT_DELIVERY_SOURCE_MODULE: Final[str] = "ui.research_observer"


@dataclass(frozen=True, slots=True)
class ArtifactPreviewResult:
    """预览结果：文本 + 完整输出摘要/字节数（预览不落库、不写文件）。"""

    format: str
    text: str
    digest: str
    byte_count: int
    field_count: int
    note_key: str | None


@dataclass(frozen=True, slots=True)
class DeliveryTargetProbe:
    """覆盖确认所需的目标事实（存在性 + 身份 + 摘要；重解析点单独标注）。"""

    target_path: str
    exists: bool
    size: int | None
    digest: str | None
    volume_serial: int | None
    file_id: int | None
    is_reparse_point: bool


@dataclass(frozen=True, slots=True)
class DeliveryExportResult:
    """一次导出的回执与本交付暂存清理事实（``unknown`` 原样透传，不转成功）。"""

    receipt: DeliveryReceipt
    staged_cleaned: bool
    target_path: str


def _xlsx_field_preview(fields: tuple[DeliveryField, ...]) -> str:
    """XLSX 预览以交付字段表呈现（导出文件仍是工作簿；预览不冒充二进制内容）。"""

    lines = ["\t".join(DELIVERY_RENDER_COLUMNS)]
    lines.extend(
        "\t".join(
            (
                field.path,
                field.value_type,
                str(field.chunk_index),
                str(field.chunk_count),
                field.value_text,
            )
        )
        for field in fields
    )
    return "\n".join(lines)


class AgentArtifactDelivery:
    """成果预览/目标探测/导出适配（W-03）。

    交付顺序、覆盖绑定与 ``unknown`` 语义全部由 ``DeliveryCommandService`` 冻结；
    本类只补两个 UI 缺口：① 预览（只读渲染，不落库、不写文件）；② 目标事实探测
    （覆盖确认摘要）。预览要求正文跨游标取全（截断只影响 UI 显示并如实标注）。
    """

    def __init__(
        self,
        *,
        dao: Any,
        user_scope: str,
        store: Any | None = None,
    ) -> None:
        self._dao = dao
        self._user_scope = user_scope
        self._store = store if store is not None else WindowsManagedFileStore()
        self._service = DeliveryCommandService(artifacts=dao, dao=dao, store=self._store)

    async def preview(
        self,
        *,
        artifact_id: str,
        artifact_version: int,
        execution_id: str,
        round_id: str,
        format_name: str,
    ) -> ArtifactPreviewResult:
        """按服务端交付字段渲染预览（内容与导出同源；不产生任何持久事实）。"""

        read, body_text = await self._read_complete(
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            execution_id=execution_id,
            round_id=round_id,
        )
        fresh = await self._dao.read_artifact(
            ArtifactReadQuery(
                artifact_id=artifact_id,
                artifact_version=artifact_version,
                execution_id=execution_id,
                round_id=round_id,
                limit=1,
            ),
            user_scope=self._user_scope,
        )
        states = tuple(fresh.source_ref_states)
        if states:
            aggregate = aggregate_delivery_source_state(states)
        else:
            # 旧成果无逐项来源引用：保存时的 available 不能证明本次可交付（R21）。
            saved_state = canonical_delivery_source_state(fresh.source_available_state)
            aggregate = "unknown" if saved_state == "available" else saved_state
        fields = await self._run_blocking(
            build_delivery_fields,
            read,
            body_text=body_text,
            current_source_state=aggregate,
            current_source_states=states,
        )
        rendered = await self._run_blocking(render_delivery, fields, format_name)
        if format_name == "xlsx":
            text = _xlsx_field_preview(fields)
            note_key: str | None = _ARTIFACT_PREVIEW_FIELD_VIEW_KEY
        else:
            text = rendered.content.decode("utf-8", errors="replace")
            if format_name == "csv":
                text = text.lstrip("\ufeff")
            note_key = None
        if len(text) > _ARTIFACT_PREVIEW_CHAR_LIMIT:
            text = text[:_ARTIFACT_PREVIEW_CHAR_LIMIT]
            note_key = (
                _ARTIFACT_PREVIEW_FIELD_VIEW_TRUNCATED_KEY if note_key is not None else _ARTIFACT_PREVIEW_TRUNCATED_KEY
            )
        return ArtifactPreviewResult(
            format=format_name,
            text=text,
            digest=rendered.digest,
            byte_count=len(rendered.content),
            field_count=len(fields),
            note_key=note_key,
        )

    async def inspect_target(self, *, target_path: str) -> DeliveryTargetProbe:
        """探测目标事实（stat + 摘要，提交线程池，不堵 UI；不创建/截断目标）。"""

        path = Path(validate_delivery_target_path(target_path))
        try:
            snapshot = await self._run_blocking(_snapshot, path)
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "delivery target is not readable",
                "artifact-delivery-target",
            ) from exc
        return DeliveryTargetProbe(
            target_path=str(path),
            exists=snapshot.exists,
            size=snapshot.size,
            digest=snapshot.digest,
            volume_serial=snapshot.volume_serial,
            file_id=snapshot.file_id,
            is_reparse_point=snapshot.is_reparse_point,
        )

    async def export(
        self,
        *,
        artifact_id: str,
        artifact_version: int,
        execution_id: str,
        round_id: str,
        format_name: str,
        target_path: str,
        expected_existing: bool,
        expected_digest: str | None,
        expected_volume_serial: int | None,
        expected_file_id: int | None,
    ) -> DeliveryExportResult:
        """按冻结顺序导出（覆盖确认事实随命令下发；目标已变化即由交付层拒绝）。"""

        path_text = validate_delivery_target_path(target_path)
        # 每次用户导出是独立命令：命令键含随机盐，避免与历史交付共用同一个
        # operation（已完成/unknown 的 operation 不接受新阶段）。崩溃后重试需重新
        # 走覆盖确认，不会静默覆盖。
        command_id = stable_id(
            "ui-export",
            [
                self._user_scope,
                artifact_id,
                str(artifact_version),
                format_name,
                path_text,
                uuid.uuid4().hex,
            ],
        )
        operation_id = stable_id("delivery-operation", [self._user_scope, command_id])
        node_id = stable_id("delivery-node", [self._user_scope, artifact_id, str(artifact_version)])
        await self._dao.ensure_model_operation(
            user_scope=self._user_scope,
            operation_id=operation_id,
            execution_id=execution_id,
            round_id=round_id,
            node_id=node_id,
        )
        draft = ArtifactDeliveryCommand(
            command=CommandRef(
                user_scope=self._user_scope,
                source_module=_ARTIFACT_DELIVERY_SOURCE_MODULE,
                business_request_id=operation_id,
                command_id=command_id,
                expected_revision=0,
            ),
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            format=format_name,
            format_rule_version=DELIVERY_FORMAT_RULE_VERSION,
            delivery_id="",
            target=DeliveryTarget(
                target_path=path_text,
                expected_existing=expected_existing,
                expected_existing_digest=expected_digest,
                overwrite_confirmed=expected_existing,
                expected_volume_serial=expected_volume_serial,
                expected_file_id=expected_file_id,
            ),
            expected_delivery_revision=0,
        )
        command = replace(draft, delivery_id=derive_delivery_id(draft))
        outcome = await self._service.deliver(
            command,
            DeliveryContext(
                operation_id=operation_id,
                command_record_id=operation_id,
                execution_id=execution_id,
                round_id=round_id,
            ),
        )
        return DeliveryExportResult(
            receipt=outcome.receipt,
            staged_cleaned=outcome.staged_cleaned,
            target_path=path_text,
        )

    async def _read_complete(
        self,
        *,
        artifact_id: str,
        artifact_version: int,
        execution_id: str,
        round_id: str,
    ) -> tuple[Any, str | None]:
        """跨游标取完整正文（分页未取全即报 OPERATION_UNKNOWN，不静默截断）。"""

        cursor: str | None = None
        parts: list[str] = []
        first: Any = None
        while True:
            read = await self._dao.read_artifact(
                ArtifactReadQuery(
                    artifact_id=artifact_id,
                    artifact_version=artifact_version,
                    execution_id=execution_id,
                    round_id=round_id,
                    body_cursor=cursor,
                ),
                user_scope=self._user_scope,
            )
            if first is None:
                first = read
                if read.artifact_id != artifact_id or read.artifact_version != artifact_version:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "artifact identity mismatch",
                        "artifact-delivery-preview",
                    )
                if read.execution_id != execution_id or read.round_id != round_id:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "artifact execution/round mismatch",
                        "artifact-delivery-preview",
                    )
            elif (read.artifact_id, read.artifact_version) != (first.artifact_id, first.artifact_version):
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "artifact identity changed between pages",
                    "artifact-delivery-preview",
                )
            if read.next_cursor is not None and read.next_cursor == cursor:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "artifact body cursor did not advance",
                    "artifact-delivery-preview",
                )
            if read.body_text is not None:
                parts.append(read.body_text)
            if read.next_cursor is None:
                break
            cursor = read.next_cursor
        body_text = "".join(parts) if first.body_available and parts else None
        return first, body_text

    async def _run_blocking(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """同步工作提交线程池（R16：渲染/摘要不在事件循环上执行）。"""

        from utils.thread_pool import TaskType, ThreadPoolManager

        task_type = TaskType.CPU if func is render_delivery or func is build_delivery_fields else TaskType.IO
        return await ThreadPoolManager().run_async(task_type, func, *args, **kwargs)


def _build_artifact_delivery(*, dao: Any, user_scope: str) -> AgentArtifactDelivery | None:
    """构造成果交付适配（失败不阻断宿主组装；UI 如实显示导出未接线）。"""

    try:
        return AgentArtifactDelivery(dao=dao, user_scope=user_scope)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "[AgentWiring] artifact delivery wiring unavailable: %s (UI export disabled)",
            type(exc).__name__,
        )
        return None
