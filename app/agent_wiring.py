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
     核 IO → 保存结束/unknown → 关闭 DBOS/Client/引擎），在释放共享引擎之前调用。

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
- 派发门闩与关闭链共用同一 ``_ProcessDispatchGate``：关闭派发后新提交零派发。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Final, cast

from app.agent_mcp import build_mcp_lifecycle
from core.agent_types import AgentValidationError
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
from services.agent_service.dispatch import (
    PlanSpecProvider,
    RootWorkflowResultReader,
    RootWorkflowSubmitter,
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
from services.agent_service.runtime import CloseCommandIdentity, CloseFactOutcome, OwnerLoopBridge
from services.agent_service.skills import ManagedSkillStore, SkillCatalogService
from services.agent_service.tool_runtime import build_tool_runtime_factory
from services.agent_service.units import UnitConversion
from services.agent_service.workflows import submit_research_workflow
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

    def close_ready(self) -> bool:
        """关闭门是否已给出「可释放共享引擎/账本/数据库」的证据。

        有宿主时只认 ``ready=True`` 的关闭回执；宿主从未组装（无 runtime 且无
        待收口 handle）时为可核事实——没有任何 Agent 派发入口/连接。未就绪即
        不得释放依赖（R2-I35）。
        """

        if self.runtime is not None:
            return self._receipt is not None and self._receipt.ready
        return self.unclosed_handle is None

    async def prepare_close(self) -> CloseReadinessReceipt | None:
        """关闭前置：先收敛/释放 Agent 宿主，再由调用方释放共享引擎。

        顺序：``AgentRuntime.prepare_close``（既有关闭链）→ facade ``aclose``。
        未就绪（宿主未组装/隔离失败）时为 no-op，不启动关闭链。不触碰他层资源
        （Recorder、业务引擎、内置 PG 由调用方按原协调顺序关闭）。

        **未就绪不关 facade**：关闭门未给出就绪证据（在途 IO/残留未核定/超时/
        异常）时保留服务可用面，调用方必须据 :meth:`close_ready` 阻断后续清理，
        不得先释放账本/引擎/数据库（R2-I35）。

        ``CancelledError`` 原样传播（R2）；超时/普通异常按「未就绪」返回，由调用方
        据 :meth:`close_ready` 阻断后续释放。
        """

        if self.mcp_lifecycle is not None:
            await self._disconnect_mcp_sessions()
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
        if self.close_ready():
            await self.service.aclose()
        return receipt

    async def _disconnect_mcp_sessions(self) -> None:
        """关闭前置：断开本进程显式建立的 MCP 连接。

        在途/冻结使用者由共享连接管理器拒停；断开失败如实记录并继续，
        不把「未确认断开」伪装成已断开（R21），也不阻断其余关闭步骤。
        """

        lifecycle = self.mcp_lifecycle
        if lifecycle is None:
            return
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
    except Exception as e:  # 叶运行链未装好即零派发（launch/派发都不进行）
        logger.warning("[AgentWiring] leaf runtime install failed: %s (zero dispatch)", type(e).__name__)
        return AgentWiring(
            service=service,
            bridge=bridge,
            failure=f"leaf_runtime_install_failed:{type(e).__name__}",
            mcp_lifecycle=lifecycle,
        )

    allow_launch = agent_runtime_launch_allowed() if launch_allowed is None else launch_allowed
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

    async def _tracked_submit(workflow_id: str, payload: dict[str, Any]) -> None:
        port_io.enter("research_submit")
        try:
            await submit_port(workflow_id, payload)
        finally:
            port_io.exit("research_submit")

    async def _tracked_read(workflow_id: str) -> ResearchWorkflowResult:
        port_io.enter("research_result_read")
        try:
            return await read_port(workflow_id)
        finally:
            port_io.exit("research_result_read")

    def _residual_io_probe() -> tuple[str, ...]:
        """关闭门闩的残余 IO 探针：端口在途 + 已 launch 但无法核定的宿主执行。"""
        residual = list(port_io.residual_names())
        if launched:
            # 已 launch 的 SDK 执行集没有可核对零在途的公开证据；在补齐真实探针前
            # 不得谎报已收敛（R2-I35/R21）——关闭门闩据此阻断释放依赖。
            residual.append("launched_workflows_unverified")
        return tuple(residual)

    dispatcher = ResearchDispatcher(
        gate=gate,
        submit=_tracked_submit,
        # 生产计划来源：冻结受理 manifest 的计划引用 → 已确认计划版 → 可执行节点表。
        plan_provider=plan_provider if plan_provider is not None else build_plan_spec_provider(dao),
        read_result=_tracked_read,
        # 生产角色能力来源：只取本次请求冻结的 Skill/MCP 显式手选集（未选择即零派发）。
        capability_provider=build_role_capability_provider(dao),
    )
    runtime = AgentRuntime(
        handle=handle,
        bridge=bridge,
        dispatch_gate=gate,
        fact_sink=_PendingCloseFactSink(launched=launched),
        blocking_runner=cast(Any, blocking_runner),
        residual_io_probe=_residual_io_probe,
    )
    logger.info(
        "[AgentWiring] agent runtime host ready (launched=%s, isolation_ready=%s, dispatch_wired=True)",
        launched,
        outcome.isolation.ready if outcome.isolation is not None else None,
    )
    cleanup, cleanup_failure = _build_cleanup_wiring(handle=handle, dao=dao)
    facade = AgentService(dao, usage_dao, dispatcher=dispatcher, skills=skills)
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
    intake = MainTurnIntake(facade, manifest_reader=dao, user_scope=user_scope, main_turn=main_turn)
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
        instance_guard=outcome.instance_guard,
    )
