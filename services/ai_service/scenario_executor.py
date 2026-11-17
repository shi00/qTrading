"""场景执行器（S3，ADR-0014 §6.1，模型统一.md §6.2，方案 D7）。

职责：按快照路由把一次推理请求编排为「许可 → 派发 → 适配器调用 → 校验 →
落账收敛」的候选遍历；不做配置读写（快照冻结语义由 §3.3 保证）、不直接
触账本（一律经 UsageRecorder）。

关键契约（方案 §6.2 固定执行流程）：
- **执行序**：父请求建立（F16，先于候选遍历——全候选被拒 / deadline 耗尽等
  零尝试请求同样在请求页可查）→ 冷却检查 → 连接并发许可（排队计入 deadline）→
  逐实际尝试原子预算预留（F16，D3 月锁内 try_reserve；F19：费用型金额按连接
  计价冻结快照 + 请求 payload 估算，dispatch 不伪造 0 金额）→ mark_dispatched 绑定
  派发身份 → 逐次 authorize_attempt（每次尝试单独授权，不预发下一次重试的
  许可）→ begin_attempt 建立实际尝试记录 → 适配器执行；不在安全许可后再排队
  （§4.3）；F26 连接资格门控（禁用 / 已确认不支持任务，按冻结快照证据）先于
  冷却检查——派发前淘汰不合格候选（决策记录 rejected + 原因）；
- **重试**（F18）：同候选默认 1 次、上限 2 次（构造可调），退避 1/2s 与适配器
  Retry-After 取大，并 clamp 至剩余 deadline；**每次真实尝试独立身份**——
  独立 attempt / reservation 与递增 retry_index（外部重试 = 新的真实调用 =
  新账目事实；持久化重放同一次写账才沿用原身份，由 DAO 幂等承载）；每次
  失败尝试即时提交 failed 终态（结算该次预留 + 记失败事实，R21 不静默）；
- **D5 降级**：有限额费用型在预留被拒（余额不足 / owner 失权）或账本不可用
  （try_reserve / mark_dispatched persisted=False）时零出站；usage_only 预留
  或落账不可用时允许进程内一次性派发身份；失败候选收敛错误提交终态
  （§6.2 步骤 5，F16：终态携带预留 ID 经 F22 转换 / F23 背书收敛预留）；
- **R2**：CancelledError 始终传播，不包装为失败；取消传播边界先经受限清理
  （F19）：尽力提交 cancelled 终态或幂等重提交在途终态（短超时，提交失败仅记
  日志）后 re-raise——attempt 不再永久停留 dispatched；外发已开始的取消按
  收费未知收敛（不结算预留，经 F23 租约到期自愈 held_unconfirmed）。
- **F27 异常分类**：适配器异常先分类后处置（终止判定先于重试判定，禁止先重试
  再发现），四类判据见 :func:`_classify_adapter_failure`——① 终止类（内容安全
  拒绝 ``CONTENT_REFUSED``）：提交策略终态并中断整个候选遍历（halt，禁止重试
  与换候选绕过）；② 瞬态类：退避后同候选重试（消耗路由 ``transient_retries``
  预算）；③ 输出非法类（validate_output 失败）：不重试，允许切换备用候选（与
  内容安全拒绝严格区分）；④ 不重试可切换类（认证 / 配额 / 未知等
  ``should_retry=False`` 判据）：不重试，允许切换候选。判据复用
  ``classify_error(context="llm")`` 的结构化 ``should_retry`` / ``code``，
  不散落 if-else；
- **F27 路由策略传递**：路由冻结快照（ExecutionSnapshot.routes 条目）携带的
  ``transient_retries``（int 0..2，零重试）与 ``fallback_enabled``（bool，禁
  备用）在 Executor 消费生效。D13-001：分派侧经共享构造保证两键必填；本层
  仅在旧版快照（键缺失 / 非法）上按构造默认兼容，且 **warning 留痕**——不静默
  回退（用户策略丢失不得伪装成功，R21）；``fallback_enabled=False`` 只派发路由
  首个候选，其余候选记 skipped（不静默）。

B-02-3（HANDOFF §6.1）：旧入口 ``execute`` 与新入口 ``execute_chat_turn`` 共享
同一遍历主干与逐尝试执行段（预留 → 派发绑定 → 授权 → attempt 登记 → 执行 →
usage → 结算），入口差异经 :class:`_AttemptPolicy` 参数化：

- **execute_chat_turn 契约**：完整模型回合；``on_event`` 可等待（背压经 await
  透传，SQ-02「实际 delta 有界事件」）；返回 :class:`core.model_management_types.ChatTurnOutcome`
  ——transport 与 settlement 两维分离、各自可表达 unknown（INV-07）；旧
  ``execute`` 校验入口行为不变；
- **binding**（受理冻结模型绑定，无秘密 mapping）：必需 ``scenario_id``（路由
  查找键）与 ``task_id``（真实子任务标识，F34 落账 / F26 资格门控）；缺失 /
  非字符串在入口 fail-closed（ValueError，零副作用）。invocation 身份取
  ``model_call_id``（一回合一 invocation，model_call → invocation → attempt
  稳定关联，HANDOFF 模型桥关联要求）；
  # NOTE(lazy): trigger 沿旧链固定 "manual"。 ceiling: Agent 回合触发身份未在
  # 设计正本冻结。 upgrade: D 系列 workflow 接线冻结触发语义时经 binding 显式携带。
- **§6.4-5 已公开分片后失败**：任一真实事件送达调用方（on_event 之前即置位，
  保守口径）后失败——保 partial/unknown 及原 attempt，不重试不换候选（不换
  模型拼接）；未公开分片前的失败沿冻结候选策略处理（与旧链一致）；
- **拒绝不换候选规避**（DD §10.1）：``finish_reason="refused"`` 的完整回合以
  failed 终态 + 完整 response 承载并中断候选遍历（halt），禁止换候选绕过；
  用量已观测不丢弃（R21）；
- **协议失败**：断流 / 缺 finish / 畸形闭合参数经适配器层
  ``ModelTurnProtocolError`` 呈现为确认失败（B-02-1 已实现协议侧），不因迭代
  耗尽成功（§6.4-4）；取消传播边界沿共享 F19 受限清理后 re-raise（R2）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast

from core.errors import AIContentRefusedError
from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    CandidateDecision,
    ChatStreamEvent,
    ChatStreamEventHandler,
    ChatInferenceAdapter,
    ChatTurnOutcome,
    ChatTurnRequest,
    ChatTurnResponse,
    ChatTurnSettlementState,
    ChatTurnTransportState,
    ExecutionSnapshot,
    InferenceAdapter,
    InferenceRequest,
    NormalizedLLMUsage,
    ResolvedConnection,
    ScenarioExecutionResult,
    TextContentPart,
    adapter_kind_requires_egress,
    ensure_inference_adapter,
)
from services.ai_service.connection_pricing import (
    ConnectionPricing,
    compute_cost_cny,
    parse_connection_pricing,
    pricing_snapshot_metadata,
)
from services.ai_service.token_budget import OUTPUT_RESERVE_TOKENS, _estimate_tokens
from tenacity import AsyncRetrying, RetryCallState, stop_after_attempt
from utils.egress_ack import bind_egress_ack_permit
from utils.error_classifier import classify_error
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import get_loop_local

logger = logging.getLogger(__name__)

# 重试退避（秒）：首次 1s、第二次 2s（方案 D7），随 retry_index 指数递增至封顶
_RETRY_BACKOFF_BASE_S: Final[float] = 1.0
_RETRY_BACKOFF_MAX_S: Final[float] = 2.0
# F27：服务端 Retry-After 尊重上限（秒）——不再截断为退避基线，但须有上限保护
# 防恶意 / 畸形大值（实际等待仍受剩余 deadline 二次 clamp）
_RETRY_AFTER_CAP_S: Final[float] = 60.0

# F27：内容安全拒绝的终态错误码 / 决策原因（与 _CANCELLED_ERROR_CODE 同风格，
# 消费端按此稳定码映射，禁止重试与换候选绕过）
_CONTENT_REFUSED_ERROR_CODE: Final[str] = "CONTENT_REFUSED"
# F27：禁备用策略（fallback_enabled=False）下未派发候选的 skipped 原因
_FALLBACK_DISABLED_REASON: Final[str] = "fallback disabled by route policy"

# loop-local 键（R11）：Executor 逐循环状态（信号量 + 冷却表）
_LOOP_STATE_KEY: Final[str] = "scenario_executor_state"

# 归属月时区偏移：CST 无夏令时，固定 +8h（D2 Asia/Shanghai 口径）
_CST_OFFSET = timedelta(hours=8)

# owner 租约时长（F16，秒）：覆盖单请求 deadline（≤180s）+ 用量确认窗口（60s）
# 的保守富余；租约过期的预留经 F23 语义自愈（失权 owner 预留不可再推进，
# reserved 退出预算占用为正确的事实零释放，保守多占可接受）。
_OWNER_LEASE_SECONDS: Final[float] = 900.0

# F19：取消边界清理的终态提交超时（秒）——受限清理不得拖延取消传播。默认回落值；
# 实际预算按 recorder 落账内部超时 + 余量推导（见 _cancel_cleanup_budget），
# 确保取消路径不会先于落账超时掐断（F19-003：清理 2s < 落账 5s 时落账被取消，
# 记录既不落账也不保留 → attempt 永久 dispatched）。
_CANCEL_CLEANUP_TIMEOUT_S: Final[float] = 6.0
# F19：清理预算相对落账内部超时的余量（秒）
_CANCEL_CLEANUP_MARGIN_S: Final[float] = 1.0
# F19：取消终态错误码（与超时 / 失败 / 仍运行可区分的账本事实）
_CANCELLED_ERROR_CODE: Final[str] = "CANCELLED"


def _cancel_cleanup_budget(recorder: Any) -> float:
    """取消边界清理预算（秒，F19-003）：≥ recorder 落账内部超时 + 余量。

    清理预算短于落账内部超时会在落账完成前取消它（此时记录既未落账也未进入
    重放队列，attempt 永久停留 dispatched）。recorder 未暴露数值超时（测试替身 /
    旧形态）时回落常量预算，不臆造数值（R21）。
    """
    settle_timeout = getattr(recorder, "settle_timeout_s", None)
    if isinstance(settle_timeout, int | float) and not isinstance(settle_timeout, bool):
        return float(settle_timeout) + _CANCEL_CLEANUP_MARGIN_S
    return _CANCEL_CLEANUP_TIMEOUT_S


def _default_local_only_mode_reader() -> bool:
    """local-only 政策默认读取器（R01-D1）：新鲜读配置，不做任何缓存。

    services → utils 为合法方向（R1，utils 横切叶子）；lazy import 避免顶层
    加载配置模块。每次调用即时反映配置切换（禁止缓存陈旧配置）。
    """
    from utils.config_handler import ConfigHandler

    return ConfigHandler.is_ai_local_only_mode()


@dataclass(slots=True)
class _LoopState:
    """Executor 逐事件循环状态（R11）：同连接信号量 + 冷却表。"""

    semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    cooldowns: dict[tuple[str, int], float] = field(default_factory=dict)


class ScenarioExecutor:
    """统一场景执行器（DI：config_service / recorder / compatibility / adapters）。"""

    def __init__(
        self,
        *,
        config_service: Any,
        recorder: Any,
        compatibility: Any,
        adapters: Mapping[str, InferenceAdapter],
        clock: Callable[[], float] | None = None,
        retry_attempts: int = 1,
        retry_max_attempts: int = 2,
        cooldown_seconds: float = 30.0,
        local_only_mode_reader: Callable[[], bool] | None = None,
    ) -> None:
        self._config_service = config_service
        self._recorder = recorder
        self._compatibility = compatibility
        # 检视 F02：注入点逐项协议校验——错误形状 adapter 在组装期 TypeError 快速失败，
        # 而非执行期 adapter.invoke AttributeError 被误判为模型故障重试。
        self._adapters: dict[str, InferenceAdapter] = {}
        for kind, adapter in adapters.items():
            ensure_inference_adapter(kind, adapter)
            self._adapters[kind] = adapter
        self._clock = clock
        # F19-003：清理预算按 recorder 落账内部超时推导（不得先于落账被掐断）
        self._cancel_cleanup_timeout_s = _cancel_cleanup_budget(recorder)
        self._retry_attempts = max(0, min(retry_attempts, retry_max_attempts))
        self._cooldown_seconds = cooldown_seconds
        # R01-D1：local-only 政策读取器（DI，与 clock 同风格）；默认经 ConfigHandler
        # 新鲜读。不缓存返回值——政策判定每次 execute 重新读取。
        self._local_only_mode_reader = local_only_mode_reader or _default_local_only_mode_reader

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def execute(
        self,
        request: InferenceRequest,
        snapshot: ExecutionSnapshot,
        validate_output: Callable[[AdapterResponse], Any],
    ) -> ScenarioExecutionResult:
        """按快照路由执行：候选遍历 + 失败切换，返回场景终态。

        旧校验入口（HANDOFF §6.1：行为不变）——请求身份投影为
        :class:`_TurnIdentity` 后与 ``execute_chat_turn`` 共享遍历主干
        （:meth:`_execute_traversal`）与逐尝试执行段；适配器调用（invoke）与
        响应校验收敛（validate_output）由旧链策略承载。
        """
        identity = _TurnIdentity(
            invocation_id=request.invocation_id,
            scenario_id=request.scenario_id,
            task_id=str(request.task_id),
            deadline=request.deadline,
            run_id=request.run_id,
            payload=request.payload,
        )

        async def _finalize_legacy(response: Any, scope: _AttemptScope, duration_ms: float) -> _CandidateOutcome:
            """旧链成功响应收敛（校验入口行为不变）：F27 输出非法类——不重试
            同候选，允许切换备用候选（与内容安全拒绝的终止语义严格区分：输出
            非法是质量事实而非安全策略，换候选不构成绕过）。"""
            legacy_response = cast(AdapterResponse, response)
            try:
                validated = validate_output(legacy_response)
            except Exception as e:
                await self._submit_failed_terminal(
                    scope.identity,
                    scope.attempt_id,
                    f"output validation failed: {type(e).__name__}",
                    scope.reservation_id,
                    metadata=scope.metadata,
                    ledger=scope.ledger,
                    duration_ms=duration_ms,
                )
                return _CandidateOutcome(
                    status="failed",
                    reason=f"output validation failed: {type(e).__name__}",
                    outputs=(),
                    usages=(),
                    attempt_id=scope.attempt_id,
                )

            usage_tuple = (legacy_response.usage,) if legacy_response.usage is not None else ()
            await self._submit_terminal_logged(
                _terminal_record(
                    scope.identity,
                    scope.attempt_id,
                    usage=legacy_response.usage,
                    actual_model=legacy_response.actual_model,
                    status="succeeded",
                    error_code=None,
                    reservation_id=scope.reservation_id,
                    metadata=scope.metadata,
                    duration_ms=duration_ms,
                ),
                scope.ledger,
            )
            return _CandidateOutcome(
                status="succeeded",
                reason="ok",
                outputs=(
                    # F03：统一公共输出类型 ScenarioOutput——保留校验器 metadata
                    # （actual_model 等模型身份）与程序生成溯源（连接身份 /
                    # security_revision 等，Executor 侧权威，后置覆盖同源键）。
                    ScenarioOutput(
                        output=dict(validated.output)
                        if isinstance(validated.output, Mapping)
                        else {"output": validated.output},
                        metadata={
                            **dict(validated.metadata),
                            **scope.metadata,
                        },
                    ),
                ),
                usages=usage_tuple,
                attempt_id=scope.attempt_id,
            )

        policy = _AttemptPolicy(
            invoke=_legacy_invoke(request),
            finalize=_finalize_legacy,
            on_adapter_failure=None,
        )
        result = await self._execute_traversal(identity, snapshot, policy)
        return ScenarioExecutionResult(
            scenario_id=request.scenario_id,
            invocation_id=request.invocation_id,
            status=result.status,  # type: ignore[arg-type]
            outputs=result.outputs,
            candidate_decisions=result.decisions,
            actual_usage=result.usages,
        )

    # ------------------------------------------------------------------
    # 完整聊天回合入口（B-02-3，HANDOFF §6.1）
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def execute_chat_turn(
        self,
        request: ChatTurnRequest,
        snapshot: ExecutionSnapshot,
        *,
        binding: Mapping[str, Any],
        on_event: ChatStreamEventHandler | None = None,
    ) -> ChatTurnOutcome:
        """完整模型聊天回合（B-02-3，HANDOFF §6.1 / DD §10.1，SQ-02）。

        与旧 :meth:`execute` 共享遍历主干与逐尝试执行段（预留 → 派发绑定 →
        授权 → attempt 登记 → 执行 → usage → 结算）；差异：

        - **入口协议**（HANDOFF §6.1）：``binding`` 为受理冻结模型绑定（无秘密
          mapping，必需 ``scenario_id`` / ``task_id``，缺失 fail-closed 零副作用）；
          invocation 身份取 ``model_call_id``（一回合一 invocation，model_call →
          invocation → attempt 稳定关联）；``on_event`` 可等待（真实 delta 有界
          事件，背压经 await 透传）；
        - **聊天适配器**：经 :class:`core.model_management_types.ChatInferenceAdapter`
          协议（``invoke_chat``）执行——非聊天适配器（FinBERT 等）派发前淘汰
          （rejected，零预留副作用）；``stream=True``、``response_format=output_schema``
          原样透传，路由与业务 final 校验不在本入口（§6.1，由调用方承载）；
        - **§6.4-5**：任一真实事件送达调用方后失败——保 partial/unknown 及原
          attempt，不重试不换候选（不换模型拼接）；未公开分片前沿冻结候选策略；
        - **拒绝不换候选规避**（DD §10.1）：refused 完整回合以 failed 终态 +
          完整 response 承载并中断遍历，用量已观测不丢弃（R21）；
        - **终态**：transport 与 settlement 两维分离（INV-07）；取消沿共享 F19
          受限清理后 re-raise（R2），不产出 outcome。

        Raises:
            ValueError: ``request`` 协议非法或 ``binding`` 缺 ``scenario_id`` /
                ``task_id``（零副作用 fail-closed）。
            asyncio.CancelledError: 取消始终传播（R2）。
        """
        request.ensure_valid()
        scenario_id = binding.get("scenario_id")
        task_id = binding.get("task_id")
        if not isinstance(scenario_id, str) or not scenario_id or not isinstance(task_id, str) or not task_id:
            raise ValueError(
                "execute_chat_turn requires a frozen binding mapping with non-empty "
                "'scenario_id' (route lookup key) and 'task_id' (real subtask identity)"
            )
        identity = _TurnIdentity(
            invocation_id=request.model_call_id,
            scenario_id=scenario_id,
            task_id=task_id,
            deadline=request.deadline,
            run_id=None,
            payload=_chat_estimation_payload(request),
        )
        exposure = _ChatStreamExposure()

        async def _invoke_chat(
            adapter: InferenceAdapter, connection: ResolvedConnection, attempt_context: AttemptContext
        ) -> ChatTurnResponse:
            # 准入已核 invoke_chat 存在性（_chat_adapter_check）；协议结构化cast
            return await cast(ChatInferenceAdapter, adapter).invoke_chat(
                request,
                connection,
                request.deadline,
                attempt_context,
                stream=True,
                response_format=request.output_schema,
                on_event=_make_tracked_event(exposure, on_event),
            )

        async def _on_chat_failure(error: Exception, scope: _AttemptScope) -> _CandidateOutcome | None:
            if not exposure.exposed:
                return None
            # §6.4-5：已公开分片后失败——保 partial/unknown 及原 attempt，
            # 不重试不换候选（不换模型拼接）；失败事实已由共享段提交终态（R21）。
            return _CandidateOutcome(
                status="failed",
                decision_hint="failed",
                reason=f"chat stream failed after public fragments: {type(error).__name__}",
                outputs=(),
                usages=(),
                attempt_id=scope.attempt_id,
                partial_text=exposure.partial_text,
                stop_traversal=True,
            )

        policy = _AttemptPolicy(
            invoke=_invoke_chat,
            finalize=self._finalize_chat,
            on_adapter_failure=_on_chat_failure,
            adapter_check=_chat_adapter_check,
        )
        result = await self._execute_traversal(identity, snapshot, policy)
        candidate = result.candidate
        if result.status == "succeeded" and candidate is not None:
            outcome = ChatTurnOutcome(
                model_call_id=request.model_call_id,
                invocation_id=identity.invocation_id,
                status="succeeded",
                transport_state="ok",
                settlement_state=candidate.settlement_state,
                attempt_id=candidate.attempt_id,
                response=candidate.chat_response,
                partial_text=None,
                error_code=None,
            )
            outcome.ensure_valid()
            return outcome
        # 失败终态：拒绝回合（完整 response，传输事实成立）、分片后失败
        # （partial/unknown）与确认失败分别承载（INV-07，R21 不伪装）。
        transport: ChatTurnTransportState = "failed"
        if candidate is not None and candidate.chat_response is not None:
            transport = "ok"
        outcome = ChatTurnOutcome(
            model_call_id=request.model_call_id,
            invocation_id=identity.invocation_id,
            status="failed",
            transport_state=transport,
            settlement_state=candidate.settlement_state if candidate is not None else None,
            attempt_id=candidate.attempt_id if candidate is not None else None,
            response=candidate.chat_response if candidate is not None else None,
            partial_text=candidate.partial_text if candidate is not None else None,
            error_code=result.failure_reason,
        )
        outcome.ensure_valid()
        return outcome

    # ------------------------------------------------------------------
    # 候选遍历共享主干（新旧入口唯一执行路径，HANDOFF §6.1）
    # ------------------------------------------------------------------

    async def _execute_traversal(
        self,
        identity: _TurnIdentity,
        snapshot: ExecutionSnapshot,
        policy: _AttemptPolicy,
    ) -> _TraversalResult:
        """候选遍历共享主干：快照解析 / F16 父请求建立 / 逐候选门控与执行。

        门控序 / 决策记录 / 冷却 / 中断语义与旧 ``execute`` 逐点一致；成功候选
        与失败收敛以 :class:`_TraversalResult` 中性承载，公共终态由各入口包装。
        """
        loop = asyncio.get_running_loop()
        clock = self._clock or loop.time
        state = get_loop_local(_LOOP_STATE_KEY, _LoopState)

        context = await self._prepare_traversal(identity, snapshot)
        route = context.route
        if route is None:
            # F16：ensure_invocation 已在 _prepare_traversal 先于本判定建立
            # （零尝试请求同样在请求页可查，R21）
            return _TraversalResult(
                status="failed",
                decisions=(),
                outputs=(),
                usages=(),
                failure_reason=f"no route for scenario {identity.scenario_id}",
            )

        candidates: tuple[Mapping[str, Any], ...] = route.get("candidates", ())  # type: ignore[assignment]
        # F27 路由策略（快照冻结语义）：分派侧保存的零重试 / 禁备用策略在消费端
        # 生效。D13-001：分派侧共享构造保证两键必填；旧版快照键缺失 / 非法时按
        # 构造默认兼容但 warning 留痕——静默回退会让用户策略丢失后仍报成功（R21）。
        route_retries = _route_transient_retries(route)
        fallback_enabled = _route_fallback_enabled(route)
        if route_retries is None or fallback_enabled is None:
            logger.warning(
                "[ScenarioExecutor] route policy unavailable or invalid (scenario=%s, "
                "transient_retries=%r, fallback_enabled=%r); legacy snapshot compatibility, "
                "construction defaults applied",
                route.get("scenario_id"),
                route.get("transient_retries"),
                route.get("fallback_enabled"),
            )
        # 路由策略重试预算与构造重试预算取小（_retry_attempts 已按构造上限 clamp）
        total_attempts = (
            self._retry_attempts if route_retries is None else min(route_retries, self._retry_attempts)
        ) + 1
        fallback_skipped: tuple[CandidateDecision, ...] = ()
        if fallback_enabled is False and len(candidates) > 1:
            # 禁备用（F27）：只派发路由首个候选；其余候选记 skipped（R21 不静默），
            # 决策记录在遍历结果之后追加（保持「先实际遍历、后策略跳过」可读序）。
            fallback_skipped = tuple(
                CandidateDecision(
                    task_id=str(cand.get("task_id", "")),
                    candidate_index=int(cand.get("candidate_index", 0)),
                    connection_id=cand.get("connection_id"),
                    decision="skipped",
                    reason=_FALLBACK_DISABLED_REASON,
                )
                for cand in candidates[1:]
            )
            candidates = candidates[:1]
        # R01-D1：local-only 政策请求级新鲜读——每次遍历读取一次（函数局部，
        # 不落实例字段，配置切换下一次遍历即时生效；同请求内判定一致不混版）。
        local_only = self._local_only_mode_reader()
        decisions: list[CandidateDecision] = []
        last_outcome: _CandidateOutcome | None = None
        for cand in candidates:
            admission = await self._admit_candidate(identity, context, cand, clock, state, local_only, policy)
            if admission.decision is not None:
                decisions.append(admission.decision)
                if admission.stop:
                    break
                continue

            connection = admission.connection
            semaphore = admission.semaphore
            cooldown_key = admission.cooldown_key
            if connection is None or semaphore is None or cooldown_key is None:
                # 准入不变量（类型收窄；_admit_candidate 保证 admitted 即齐备）
                raise AssertionError("admitted candidate must carry connection, semaphore and cooldown key")

            try:
                # F18：预留 / 派发绑定 / 授权 / 执行均按真实尝试粒度在重试循环内
                # 逐次进行（每次真实尝试独立身份），见 _run_candidate。
                outcome = await self._run_candidate(
                    identity,
                    snapshot,
                    connection,
                    cand,
                    clock,
                    policy,
                    month=context.month,
                    owner_session_id=context.owner_session_id,
                    owner_epoch=context.owner_epoch,
                    owner_lease_expires_at=context.owner_lease_expires_at,
                    pricing=context.pricing_by_id.get(connection.connection_id),
                    total_attempts=total_attempts,
                )
            finally:
                semaphore.release()

            last_outcome = outcome
            if outcome.status == "succeeded":
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision="succeeded",
                        reason="ok",
                    )
                )
                return _TraversalResult(
                    status="succeeded",
                    decisions=(*decisions, *fallback_skipped),
                    outputs=outcome.outputs,
                    usages=outcome.usages,
                    candidate=outcome,
                )

            if outcome.halt_candidates or outcome.stop_traversal:
                # 中断整个候选遍历：halt_candidates = invocation 级事实（D5 费用型
                # 预留被拒 / 派发绑定失权 / 内容拒绝 / 必需登记失败，切换候选无济，
                # 不进冷却）；stop_traversal = §6.4-5 已公开分片后失败（保原
                # attempt 不换候选，属连接侧事实沿常规失败路径进冷却——R-B02-2
                # 行为与契约一致化：break 前登记连接冷却）。
                if outcome.stop_traversal and not outcome.halt_candidates:
                    state.cooldowns[cooldown_key] = clock() + self._cooldown_seconds
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision=outcome.decision_hint,
                        reason=outcome.reason,
                    )
                )
                break

            # 失败：连接进入冷却，按结果标记切换/拒绝（authorize 失败属 rejected）
            state.cooldowns[cooldown_key] = clock() + self._cooldown_seconds
            decisions.append(
                CandidateDecision(
                    task_id=str(cand.get("task_id", "")),
                    candidate_index=int(cand.get("candidate_index", 0)),
                    connection_id=connection.connection_id,
                    decision=outcome.decision_hint,
                    reason=outcome.reason,
                )
            )

        return _TraversalResult(
            status="failed",
            decisions=(*decisions, *fallback_skipped),
            outputs=(),
            usages=(),
            candidate=last_outcome,
            failure_reason=_traversal_failure_reason([*decisions, *fallback_skipped]),
        )

    async def _prepare_traversal(self, identity: _TurnIdentity, snapshot: ExecutionSnapshot) -> _TraversalContext:
        """遍历准备（新旧入口共享）：快照连接解析 / F17 计价冻结 / F16 父请求建立。"""
        connections_by_id: dict[str, ResolvedConnection] = {}
        eligibility_by_id: dict[str, _ConnectionEligibility] = {}
        # F17 计价冻结快照（快照语义：普通保存只影响后续请求）：连接解析点随
        # 资格证据一并提取 billing 计价块，不再丢弃；billing 缺失 / 畸形 →
        # None（未知语义，不伪装 usage_only / 免费，R21）。
        pricing_by_id: dict[str, ConnectionPricing | None] = {}
        for raw in snapshot.connections:
            try:
                resolved, eligibility = _resolve_connection(raw)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "[ScenarioExecutor] snapshot connection malformed (%s): %s",
                    raw.get("connection_id", "?") if isinstance(raw, Mapping) else "?",
                    type(e).__name__,
                )
                continue
            billing = raw.get("billing") if isinstance(raw, Mapping) else None
            pricing_by_id[resolved.connection_id] = parse_connection_pricing(
                billing, connection_hint=resolved.connection_id
            )
            connections_by_id[resolved.connection_id] = resolved
            eligibility_by_id[resolved.connection_id] = eligibility

        route = next((r for r in snapshot.routes if r.get("scenario_id") == identity.scenario_id), None)

        # F16 父请求建立（先于候选遍历）：每请求一个 invocation（批次轨道每股 /
        # 单次场景各产生父请求），全候选被门控拒绝 / deadline 耗尽等零尝试请求
        # 同样在请求页可查（R21：失败不伪装为未发生）。owner 身份 per-invocation
        # 生成（F23 fencing 最小身份），租约覆盖 deadline + 用量确认窗口。
        now_cst = datetime.now(UTC) + _CST_OFFSET
        owner_session_id = uuid.uuid4().hex
        owner_epoch = 1
        owner_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=_OWNER_LEASE_SECONDS)
        month = f"{now_cst.year}{now_cst.month:02d}"
        budget_policy_snapshot = _budget_policy_snapshot(route)
        invocation_outcome = await self._recorder.ensure_invocation(
            invocation_id=identity.invocation_id,
            scenario_id=identity.scenario_id,
            trigger="manual",
            owner_session_id=owner_session_id,
            owner_epoch=owner_epoch,
            owner_lease_expires_at=owner_lease_expires_at,
            budget_policy_snapshot=budget_policy_snapshot,
        )
        if not invocation_outcome.persisted:
            logger.warning(
                "[ScenarioExecutor] invocation row unavailable (%s); fee candidates will fail closed",
                invocation_outcome.error_code,
            )
        return _TraversalContext(
            route=route,
            connections_by_id=connections_by_id,
            eligibility_by_id=eligibility_by_id,
            pricing_by_id=pricing_by_id,
            month=month,
            owner_session_id=owner_session_id,
            owner_epoch=owner_epoch,
            owner_lease_expires_at=owner_lease_expires_at,
        )

    async def _admit_candidate(
        self,
        identity: _TurnIdentity,
        context: _TraversalContext,
        cand: Mapping[str, Any],
        clock: Callable[[], float],
        state: _LoopState,
        local_only: bool,
        policy: _AttemptPolicy,
    ) -> _CandidateAdmission:
        """候选准入门控（新旧入口共享，序与旧 ``execute`` 逐点一致）。

        deadline → 连接存在性 → F26 资格 → R01-D1 local-only → 冷却 → 适配器
        存在性（+ 入口协议附加检查）→ 连接并发许可（F29：排队计入 deadline；
        未取得许可不进入 _run_candidate——无 attempt 状态，与 F19 取消收敛正交；
        CancelledError 不捕获始终传播，R2）。适配器缺失 / 协议不满足属确定性
        配置事实，派发前淘汰（F16 预留纪律：零预留副作用）且不进连接冷却。
        """

        def _decision(connection_id: Any, decision: Literal["skipped", "rejected"], reason: str) -> CandidateDecision:
            return CandidateDecision(
                task_id=str(cand.get("task_id", "")),
                candidate_index=int(cand.get("candidate_index", 0)),
                connection_id=connection_id,
                decision=decision,
                reason=reason,
            )

        remaining = identity.deadline - clock()
        if remaining <= 0:
            return _CandidateAdmission(decision=_decision(None, "skipped", "deadline exhausted"), stop=True)

        connection = context.connections_by_id.get(str(cand.get("connection_id", "")))
        if connection is None:
            return _CandidateAdmission(
                decision=_decision(cand.get("connection_id"), "rejected", "connection not found in snapshot")
            )

        # F26 连接资格门控：派发前按冻结快照证据核实启用 / 任务适用性——
        # 禁用或已确认不支持该任务的连接不派发（零副作用：不取信号量、不绑
        # 派发身份、不授权）；原因随决策记录不静默（R21）；unknown 等未确认
        # 状态不门控（按既有确认政策处理），UI 保留不适用项不代表允许执行。
        ineligibility = _ineligibility_reason(context.eligibility_by_id[connection.connection_id], identity.task_id)
        if ineligibility is not None:
            return _CandidateAdmission(decision=_decision(connection.connection_id, "rejected", ineligibility))

        # R01-D1 local-only 政策门控（候选层强制，SEC-03 语义）：仅本地模式下需
        # 外发的适配器候选（adapter_kind_requires_egress）派发前一律拒绝——不进
        # 授权链，零授权 / 零派发 / 零外发副作用；设备内本地 kind（gguf_*）不受
        # 此政策约束照常执行。与 F26 同点同模式（rejected + 原因记录，R21 不静默；
        # reason 含 "policy" 供消费端映射 policy_blocked）。注意：候选的
        # requires_egress_grant=False 仅代表「免 grant」，不构成「禁外发」——
        # 禁外发必须在此候选层淘汰，不得以 grant 语义实现。
        if local_only and adapter_kind_requires_egress(connection.adapter_kind):
            return _CandidateAdmission(
                decision=_decision(connection.connection_id, "rejected", "local-only policy forbids egress candidate")
            )

        cooldown_key = (connection.connection_id, connection.connection_revision)
        if state.cooldowns.get(cooldown_key, 0.0) > clock():
            return _CandidateAdmission(
                decision=_decision(
                    connection.connection_id,
                    "skipped",
                    f"cooldown until {state.cooldowns[cooldown_key]:.0f}",
                )
            )

        # F16 预留纪律：适配器缺失 / 不满足入口协议的候选在预留前淘汰（无法产生
        # 任何实际尝试，预留只会造成保守多占）；与 F26 门控同思路（派发前淘汰，
        # 零预留副作用）。
        adapter = self._adapters.get(connection.adapter_kind)
        if adapter is None:
            return _CandidateAdmission(
                decision=_decision(
                    connection.connection_id, "rejected", f"no adapter for kind {connection.adapter_kind}"
                )
            )
        if policy.adapter_check is not None:
            check_reason = policy.adapter_check(adapter, connection)
            if check_reason is not None:
                return _CandidateAdmission(decision=_decision(connection.connection_id, "rejected", check_reason))

        # 方案 §6.2 步骤 3：先取得连接并发许可（排队计入 deadline），再绑定派发，
        # 最后逐次 authorize_attempt——不在安全许可后再排队（步骤 4）。
        # F29：排队等待同样受请求级冻结绝对 deadline 约束——按剩余预算 wait_for
        # 等待许可，超时候选记 skipped 并收敛遍历（排队不得无限等待，过期后不得
        # 再做新准备启动出站）；超时点先于 _run_candidate（无 attempt 状态，
        # 与 F19 取消收敛正交），CancelledError 不捕获始终传播（R2）。
        semaphore = state.semaphores.setdefault(
            connection.connection_id, asyncio.Semaphore(max(1, connection.concurrency_limit))
        )
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=max(0.0, identity.deadline - clock()))
        except TimeoutError:
            return _CandidateAdmission(
                decision=_decision(
                    connection.connection_id,
                    "skipped",
                    "deadline exhausted waiting for connection permit",
                ),
                stop=True,
            )
        return _CandidateAdmission(
            decision=None,
            connection=connection,
            semaphore=semaphore,
            cooldown_key=cooldown_key,
        )

    # ------------------------------------------------------------------
    # 单候选执行（重试内循环）
    # ------------------------------------------------------------------

    async def _run_candidate(
        self,
        identity: _TurnIdentity,
        snapshot: ExecutionSnapshot,
        connection: ResolvedConnection,
        cand: Mapping[str, Any],
        clock: Callable[[], float],
        policy: _AttemptPolicy,
        *,
        month: str,
        owner_session_id: str,
        owner_epoch: int,
        owner_lease_expires_at: Any,
        pricing: ConnectionPricing | None,
        total_attempts: int | None = None,
    ) -> _CandidateOutcome:
        """单候选执行（重试内循环，新旧入口共享执行段，HANDOFF §6.1）。

        预留 → 派发绑定 → 授权 → attempt 登记 → 执行（``policy.invoke``）→
        usage 观察 → 收敛（``policy.finalize`` / ``policy.on_adapter_failure``）
        共享；出口统一投影结算事实（``settlement_state``）与最后真实 attempt
        身份（ChatTurnOutcome 承载，旧入口不消费）。
        """
        trail = _AttemptTrail()
        outcome = await self._candidate_attempts(
            trail,
            identity,
            snapshot,
            connection,
            cand,
            clock,
            policy,
            month=month,
            owner_session_id=owner_session_id,
            owner_epoch=owner_epoch,
            owner_lease_expires_at=owner_lease_expires_at,
            pricing=pricing,
            total_attempts=total_attempts,
        )
        return replace(
            outcome,
            settlement_state=_settlement_state(trail.ledger),
            attempt_id=outcome.attempt_id if outcome.attempt_id is not None else trail.attempt_id,
        )

    async def _candidate_attempts(
        self,
        trail: _AttemptTrail,
        identity: _TurnIdentity,
        snapshot: ExecutionSnapshot,
        connection: ResolvedConnection,
        cand: Mapping[str, Any],
        clock: Callable[[], float],
        policy: _AttemptPolicy,
        *,
        month: str,
        owner_session_id: str,
        owner_epoch: int,
        owner_lease_expires_at: Any,
        pricing: ConnectionPricing | None,
        total_attempts: int | None = None,
    ) -> _CandidateOutcome:
        # 适配器存在性已由 _admit_candidate 准入保证（派发前淘汰，零预留副作用）
        adapter = self._adapters[connection.adapter_kind]
        # F19：单次预留金额按连接计价快照 + 请求 payload 估算（dispatch 不持有
        # payload，不伪造金额）；未配置月限额 → usage_only（金额 NULL 不占预算）。
        # D13-005：限额已配置但金额不可估算（计价快照缺失 / usage_only 模式 /
        # token 不可估算）= 价格未知——usage_only 放行会让预算门（DAO 仅在
        # amount/limit 双非 None 时判定）完全失效，形成无上限消耗；此处 fail-closed
        # 拒绝本候选、零出站（不静默降级 usage_only，R21）；计价可估算路径不受影响。
        budget_limit = cand.get("budget_limit_cny")
        budget_amount = (
            _estimate_reservation_amount(pricing, identity.payload, model=connection.model)
            if budget_limit is not None
            else None
        )
        if budget_limit is not None and budget_amount is None:
            return _CandidateOutcome(
                status="failed",
                decision_hint="rejected",
                reason="budget amount unestimable: price or usage unknown (fail-closed)",
                outputs=(),
                usages=(),
            )
        last_reason = "exhausted"
        # F27：尝试预算可由路由冻结快照的瞬态重试策略（transient_retries）驱动
        # （execute 计算传入）；None 回退构造默认。
        attempts = total_attempts if total_attempts is not None else self._retry_attempts + 1

        # C12（ADR-0018 承接 / ADR-0014 §1 重试路线）：同候选瞬态重试由 Tenacity
        # ``AsyncRetrying`` 声明式承接（pyproject 直接依赖 tenacity>=9.2.1,<10），
        # 替换此前的手写 for 退避循环（自管偏离不追认）。参数契约与原实现逐项等价：
        # - 次数：stop_after_attempt(attempts)（首次 + retry_attempts，路由取小不变）；
        # - 退避：wait=_wait_backoff（_backoff_seconds 单一实现，与 Retry-After 取大、
        #   clamp 剩余 deadline；Retry-After cap 不回归）；
        # - deadline：_attempt_once 开头逐尝试检查（耗尽 raise 哨兵，不越 deadline 出站）；
        # - 逐次授权计量：每次真实尝试的 reserve/dispatch/authorize/begin/terminal
        #   链路与 retry_index 递增语义原样保留（闭包计数器 0 起步）；
        # - 终止类：内容拒绝等非瞬态失败经 retry 策略判 False → 原样重抛（不重试、
        #   不换候选绕过）；取消（R2）永不重试、清理后 re-raise。
        attempt_counter = 0

        def _should_retry_transient(retry_state: RetryCallState) -> bool:
            raw = retry_state.outcome.exception() if retry_state.outcome is not None else None
            if not isinstance(raw, Exception) or isinstance(raw, asyncio.CancelledError):
                # R2：取消永不重试（tenacity 以非重试路径原样重抛，R2 传播保持）；
                # 非 Exception 的 BaseException（KeyboardInterrupt 等）同样不重试
                return False
            return _classify_adapter_failure(raw) == "transient"

        def _wait_backoff(retry_state: RetryCallState) -> float:
            raw = retry_state.outcome.exception() if retry_state.outcome is not None else None
            exc = raw if isinstance(raw, Exception) else Exception()
            return _backoff_seconds(exc, retry_state.attempt_number - 1, now=clock(), deadline=identity.deadline)

        retryer = AsyncRetrying(
            sleep=asyncio.sleep,
            stop=stop_after_attempt(attempts),
            wait=_wait_backoff,
            retry=_should_retry_transient,
            reraise=True,
        )

        async def _attempt_once() -> _CandidateOutcome:
            nonlocal last_reason, attempt_counter
            # F18：每次真实尝试独立身份——独立 attempt / reservation 与递增
            # retry_index（外部重试 = 新的真实调用 = 新账目事实）；同一次写账的
            # 持久化重放沿用该次身份，由 DAO 幂等（ON CONFLICT DO NOTHING /
            # usage_revision 水位）承载，不在本层重试。
            retry_index = attempt_counter
            attempt_counter += 1
            if identity.deadline - clock() <= 0:
                last_reason = "deadline exhausted"
                raise _DeadlineExhaustedError()

            attempt_id = uuid.uuid4().hex
            trail.attempt_id = attempt_id
            reservation_token = uuid.uuid4().hex
            # F34：尝试起点计时（预留 / 派发 / 授权 / 执行全程计入尝试耗时），
            # 终态构造时点为终点——duration_ms 不再恒 None（R21：未知留 None，
            # 但本路径耗时恒可知，恒为真实值）。
            attempt_started_at = clock()
            # F19③：派发时点固化审计元数据——归属月（execute 起点冻结值，月界
            # 不漂移）/ retry_index / 连接身份 / 计价冻结快照在预留前构建，观察与
            # 终态（含取消清理）一律携带本份（copy 增量），不依赖事后重算；
            # security_revision 于 authorize 线性化点就位。
            attempt_metadata = _metadata(identity, cand, connection, None, 0, pricing, retry_index, month=month)
            # F19：单次尝试的终态收敛状态（取消边界清理依据）
            ledger = trail.ledger = _AttemptLedgerState()
            # F19②：外发事实起点——adapter.invoke 开始后消费不可证明（收费未知语义分界）
            outbound_started = False

            try:
                # F16 逐实际尝试原子预留（每次真实尝试独立预留，预留 ↔ attempt 生命
                # 周期对齐：该次终态同事务结算该次预留）：限额候选携带执行侧估算
                # 金额（F19，dispatch 冻结限额 / 计价快照，金额按 payload 估算），
                # usage_only 候选登记 NULL 金额预留（不占预算）。
                # 前次失败尝试的预留已随其 failed 终态 settled 退出在途占用，预算
                # 门逐尝试独立判定，无跨尝试双计。
                reserve_outcome = await self._recorder.try_reserve(
                    invocation_id=identity.invocation_id,
                    scenario_id=identity.scenario_id,
                    task_id=identity.task_id,
                    trigger="manual",
                    month=month,
                    reservation_id=reservation_token,
                    attempt_id=attempt_id,
                    candidate_index=int(cand.get("candidate_index", 0)),
                    retry_index=retry_index,
                    amount_cny=budget_amount,
                    budget_limit_cny=budget_limit,
                    budget_policy_snapshot=cand.get("budget_policy_snapshot"),
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    owner_lease_expires_at=owner_lease_expires_at,
                )
                reservation_id: str | None = None
                if reserve_outcome.persisted and reserve_outcome.reservation is not None:
                    reservation_id = reservation_token
                elif budget_amount is not None:
                    # D5：费用型预留被拒（余额不足 / owner 失权）或账本不可用 →
                    # 零出站（不降级外发）；原因随决策记录不静默（R21）。预算 /
                    # owner 为 invocation 级事实，切换候选无济 → 中断整个候选遍历。
                    return _CandidateOutcome(
                        status="failed",
                        decision_hint="rejected",
                        reason=f"budget reserve rejected: {reserve_outcome.error_code}",
                        outputs=(),
                        usages=(),
                        halt_candidates=True,
                    )
                # usage_only：预留未持久化 → 降级进程内派发身份（reservation_id=None）

                # 派发绑定（D5）：预留 reserved→dispatched（F23：错误预留 ID /
                # owner 失权 rowcount=0）；有限额费用型 persisted=False 时禁止派发
                dispatch_outcome = await self._recorder.mark_dispatched(
                    invocation_id=identity.invocation_id,
                    reservation_id=reservation_id,
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                )
                if not dispatch_outcome.persisted and budget_amount is not None:
                    # 预留成功但派发绑定失权 / 账本不可用：预留留 reserved，由
                    # F23 租约语义自愈（保守多占，不伪装已释放），零出站
                    return _CandidateOutcome(
                        status="failed",
                        decision_hint="rejected",
                        reason=f"budget dispatch forbidden: {dispatch_outcome.error_code}",
                        outputs=(),
                        usages=(),
                        halt_candidates=True,
                    )

                # D13-002：外发确认按真实执行位置区分——litellm_chat 出站需 SEC-01 许可，
                # gguf_* 设备内本地模型不外发不签发。
                requires_egress = adapter_kind_requires_egress(connection.adapter_kind)

                # 许可线性化（§4.3 / 方案 §6.2 步骤 3「再逐次调用 authorize_attempt」）：
                # 每次尝试单独授权，不预发下一次重试的许可；失败即拒绝切换，不重试。
                # F06：credential_version 传快照冻结连接的实际执行版本（与下方
                # effective_connection 出站解析同源），撤销校验绑定执行对象而非磁盘现值。
                # F01：目标 / 用途取候选携带的分派授权身份（``or ""`` 防 str(None)）；
                # requires_egress_grant 按真实执行位置声明（litellm_chat 出站需 grant，
                # gguf_* 设备内本地模型不外发免 grant，凭据撤销等安全检查保留）。
                # R2：CancelledError 不被 except Exception 捕获（BaseException），
                # 直接传播至本迭代取消边界。
                try:
                    permit = await self._config_service.authorize_attempt(
                        connection.connection_id,
                        str(cand.get("target_fingerprint") or ""),
                        str(cand.get("purpose") or ""),
                        snapshot.security_scope_version,
                        connection.credential_version,
                        attempt_id,
                        requires_egress_grant=requires_egress,
                    )
                except Exception as e:
                    reason = f"authorize failed: {type(e).__name__}"
                    if reservation_id is not None:
                        # F16：预留已绑定派发（dispatched）而授权失败——无 attempt 行，
                        # 提交 failed 终态经活跃预留背书补记（F23）并转换预留 settled，
                        # 不留悬挂占用；usage_only（无预留）不补记，维持既有零副作用
                        await self._submit_failed_terminal(
                            identity,
                            attempt_id,
                            reason,
                            reservation_id,
                            metadata=attempt_metadata,
                            ledger=ledger,
                            duration_ms=_elapsed_ms(clock, attempt_started_at),
                        )
                    return _CandidateOutcome(
                        status="failed",
                        decision_hint="rejected",
                        reason=reason,
                        outputs=(),
                        usages=(),
                        attempt_id=attempt_id,
                    )
                # F19③：许可线性化点的安全修订固化进审计元数据（F01：permit 权威）
                attempt_metadata["security_revision"] = getattr(permit, "security_revision", 0)

                compatibility = self._compatibility.resolve_execution_params(
                    {"adapter_kind": connection.adapter_kind, "adapter_options": dict(connection.adapter_options)}
                )
                if not compatibility.compatible:
                    last_reason = f"incompatible: {compatibility.reason}"
                    if reservation_id is not None:
                        # 本次尝试已预留 / 派发，失败终态即时收敛（结算该次预留）
                        await self._submit_failed_terminal(
                            identity,
                            attempt_id,
                            last_reason,
                            reservation_id,
                            metadata=attempt_metadata,
                            ledger=ledger,
                            duration_ms=_elapsed_ms(clock, attempt_started_at),
                        )
                    # C12：原自管循环 break 语义等价——结束本候选，返回失败终态
                    # （不重试；Tenacity 对成功返回值直接透传）
                    return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())
                effective_connection = replace(connection, adapter_options=compatibility.resolved_options)

                # D13-002：在许可线性化点为本次 attempt 签发外发确认许可（纯绑定，零 IO），
                # 绑定 attempt / invocation / owner / 连接 / provider / 模型 / 目标 / 有效期；
                # 云适配器在凭据解析前校验，禁止跨 attempt / 跨会话复用（缺许可 fail-closed）。
                attempt_context = AttemptContext(
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    invocation_id=identity.invocation_id,
                    attempt_id=attempt_id,
                )
                if requires_egress:
                    attempt_context = replace(
                        attempt_context,
                        egress_permit=bind_egress_ack_permit(
                            attempt_context=attempt_context,
                            resolved_connection=effective_connection,
                            now=clock(),
                            deadline=identity.deadline,
                        ),
                    )

                # 真正进入适配器执行前建立实际尝试记录（§6.2 步骤 4）。F18：每次真实
                # 尝试独立 attempt 行（携带该次 retry_index）；同一次写账的持久化重放
                # 沿用该次身份（幂等，不重建）。F16：owner 齐传启用 F23 身份强门；
                # attempt_row 携带预留 ID。
                begin_outcome = await self._recorder.begin_attempt(
                    attempt_id=attempt_id,
                    invocation_id=identity.invocation_id,
                    configured_model=connection.model,
                    attempt_row=self._attempt_row(
                        identity, cand, connection, snapshot, permit, reservation_id, retry_index
                    ),
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                )
                if not begin_outcome.persisted:
                    # 必需登记失败（DD SQ-02 / F27「必需记录失败 0 出站」）：账本不可用 /
                    # 超时 / owner 失权均为 invocation 级事实，切换候选无济——本尝试零
                    # 出站并明确失败；有活跃预留时提交 failed 终态经 F23 背书补记并转换
                    # settled（不留悬挂占用）；usage_only（无预留）无账本副作用可收敛，
                    # 维持零补记。与费用型预留 / 派发失败处置一致，中断整个候选遍历。
                    reason = f"attempt registration unavailable: {begin_outcome.error_code}"
                    logger.warning(
                        "[ScenarioExecutor] required attempt registration failed (%s), zero outbound (task=%s)",
                        begin_outcome.error_code,
                        identity.task_id,
                    )
                    if reservation_id is not None:
                        await self._submit_failed_terminal(
                            identity,
                            attempt_id,
                            reason,
                            reservation_id,
                            metadata=attempt_metadata,
                            ledger=ledger,
                            duration_ms=_elapsed_ms(clock, attempt_started_at),
                        )
                    return _CandidateOutcome(
                        status="failed",
                        decision_hint="rejected",
                        reason=reason,
                        outputs=(),
                        usages=(),
                        halt_candidates=True,
                        attempt_id=attempt_id,
                    )

                # F19②：外发事实起点（await 表达式即视为外发已发起，此后取消时
                # 无法证明是否已被上游消费）
                outbound_started = True
                try:
                    response = await policy.invoke(adapter, effective_connection, attempt_context)
                except Exception as e:
                    # F27：先分类后处置——终止判定（内容安全拒绝）先于重试判定，
                    # 禁止先重试再发现。
                    failure_class = _classify_adapter_failure(e)
                    if failure_class == "content_refused":
                        # 终止类：内容安全拒绝——提交策略终态（错误码 CONTENT_REFUSED）
                        # 并中断整个候选遍历（halt），禁止重试与换候选绕过；不进连接
                        # 冷却（内容侧而非连接侧故障，与 F18 halt 家族一致）。
                        last_reason = _CONTENT_REFUSED_ERROR_CODE
                        logger.warning(
                            "[ScenarioExecutor] content refused by provider policy, "
                            "halting candidate traversal (task=%s)",
                            identity.task_id,
                        )
                        await self._submit_failed_terminal(
                            identity,
                            attempt_id,
                            last_reason,
                            reservation_id,
                            metadata=attempt_metadata,
                            ledger=ledger,
                        )
                        return _CandidateOutcome(
                            status="failed",
                            decision_hint="rejected",
                            reason=last_reason,
                            outputs=(),
                            usages=(),
                            halt_candidates=True,
                            attempt_id=attempt_id,
                        )
                    last_reason = f"adapter error: {type(e).__name__}"
                    # F18：失败尝试即时收敛（结算该次预留 + 记失败事实，R21 不静默），
                    # 再退避重试——迟到响应在适配器层被丢弃，无第二次观察通道，费用
                    # 未知以 None 承载（R21 禁止填 0 伪装）。
                    await self._submit_failed_terminal(
                        identity,
                        attempt_id,
                        last_reason,
                        reservation_id,
                        metadata=attempt_metadata,
                        ledger=ledger,
                        duration_ms=_elapsed_ms(clock, attempt_started_at),
                    )
                    # 入口特有失败收敛（chat §6.4-5：已公开分片后失败——保原 attempt
                    # 不重试不换候选）；返回 None 走共享默认（C12 Tenacity 瞬态重试 /
                    # 切换）。
                    if policy.on_adapter_failure is not None:
                        override = await policy.on_adapter_failure(
                            e, _AttemptScope(identity, attempt_id, reservation_id, attempt_metadata, ledger)
                        )
                        if override is not None:
                            return override
                    # C12：瞬态判定（_should_retry_transient）、退避（_wait_backoff）
                    # 与次数预算（stop_after_attempt）交由 Tenacity 承接——失败事实已
                    # 即时收敛，异常原样上抛：transient → 退避重试；非瞬态 / 次数耗尽
                    # → 原样重抛，由外层转失败终态（reason 语义不回归）。
                    raise

                # D6：适配器返回后立即同步转移用量（幂等按 attempt_id）；
                # security_revision 取许可线性化点的真实修订（F01：快照携带的
                # security_scope_version 是授权范围版本，不得混作安全修订落账）。
                # F17：metadata 携带计价冻结快照——成本在终态落账时点由 Recorder
                # 按真实 usage 计算（cost 与 usage 同源，避免重试场景失配）。
                # F19③：actual_model 为响应事实（增量覆盖派发时点固化元数据）。
                # D13-009：encoder 计价连接的用量证据以适配器报告的**真实** token
                # 覆盖（截断后输入 / partial 仅完成块），payload 估算仅另标
                # ``encoder_input_tokens_estimate``；未报告 → 不写 ``encoder_input_tokens``
                # （结算按不可计价 None，R21 未知不填 0）。
                attempt_metadata = {
                    **attempt_metadata,
                    "actual_model": response.actual_model,
                    **_encoder_usage_metadata(pricing, identity, connection.model, response),
                }
                scope = _AttemptScope(identity, attempt_id, reservation_id, attempt_metadata, ledger)
                self._recorder.observe_usage(_observation_from(identity, attempt_id, response, attempt_metadata))

                # 成功响应收敛（入口差异经 policy.finalize：旧链校验 + ScenarioOutput；
                # chat 拒绝处置 + response 原样承载）
                return await policy.finalize(response, scope, _elapsed_ms(clock, attempt_started_at))
            except asyncio.CancelledError:
                # F19：取消传播边界的受限清理——尽力收敛终态（cancelled 终态或在途
                # 终态幂等重提交，短超时）后必须 re-raise（R2 不吞取消；提交失败
                # 不阻断取消传播）。attempt 不再永久停留 dispatched。
                await self._cleanup_on_cancel(
                    identity,
                    attempt_id=attempt_id,
                    reservation_token=reservation_token,
                    outbound_started=outbound_started,
                    ledger=ledger,
                    metadata=attempt_metadata,
                    duration_ms=_elapsed_ms(clock, attempt_started_at),
                )
                raise

        # C12：Tenacity 承接重试迭代——成功结果（含 halt / rejected 终态 outcome）
        # 直接透传；非瞬态 / 次数耗尽 / deadline 耗尽原样重抛后在此转失败终态
        # （reason 语义与原自管 break 逐字一致）；CancelledError 穿透（R2）。
        try:
            return await retryer(_attempt_once)
        except asyncio.CancelledError:
            # R2：清理已在 _attempt_once 的取消边界完成，此处仅放行传播
            raise
        except _DeadlineExhaustedError:
            return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())
        except Exception as e:
            last_reason = f"adapter error: {type(e).__name__}"
            return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())

    async def _finalize_chat(self, response: Any, scope: _AttemptScope, duration_ms: float) -> _CandidateOutcome:
        """chat 入口成功响应收敛（B-02-3）：拒绝处置 + 终态落账 + response 承载。

        - 拒绝回合（finish_reason=refused，DD §10.1「拒绝不换候选规避」）：拒绝
          是完成的模型回合——failed 终态携带稳定码 CONTENT_REFUSED 与已观测用量
          （R21 不丢弃），halt 候选遍历（禁止换候选绕过），完整 response 随
          outcome 供调用方读取原文；
        - 其余：succeeded 终态 + response 原样承载（业务 final 校验不在本入口，
          §6.1——由调用方 workflow / Gateway 层承载）。
        """
        chat_response = cast(ChatTurnResponse, response)
        if chat_response.finish_reason == "refused":
            logger.warning(
                "[ScenarioExecutor] chat turn refused by provider policy, halting candidate traversal (task=%s)",
                scope.identity.task_id,
            )
            await self._submit_terminal_logged(
                _terminal_record(
                    scope.identity,
                    scope.attempt_id,
                    usage=chat_response.usage,
                    actual_model=chat_response.actual_model,
                    status="failed",
                    error_code=_CONTENT_REFUSED_ERROR_CODE,
                    reservation_id=scope.reservation_id,
                    metadata=scope.metadata,
                    duration_ms=duration_ms,
                ),
                scope.ledger,
            )
            return _CandidateOutcome(
                status="failed",
                decision_hint="rejected",
                reason=_CONTENT_REFUSED_ERROR_CODE,
                outputs=(),
                usages=(),
                halt_candidates=True,
                attempt_id=scope.attempt_id,
                chat_response=chat_response,
            )

        usage_tuple = (chat_response.usage,) if chat_response.usage is not None else ()
        await self._submit_terminal_logged(
            _terminal_record(
                scope.identity,
                scope.attempt_id,
                usage=chat_response.usage,
                actual_model=chat_response.actual_model,
                status="succeeded",
                error_code=None,
                reservation_id=scope.reservation_id,
                metadata=scope.metadata,
                duration_ms=duration_ms,
            ),
            scope.ledger,
        )
        return _CandidateOutcome(
            status="succeeded",
            reason="ok",
            outputs=(),
            usages=usage_tuple,
            attempt_id=scope.attempt_id,
            chat_response=chat_response,
        )

    async def _submit_failed_terminal(
        self,
        identity: _TurnIdentity,
        attempt_id: str,
        error_code: str,
        reservation_id: str | None,
        *,
        metadata: dict[str, Any],
        ledger: _AttemptLedgerState,
        duration_ms: float | None = None,
    ) -> None:
        """失败尝试终态落账（§6.2 步骤 5「Executor 收敛错误并提交终态」）。

        用量缺失由 Recorder 标 unavailable（R21）；落账失败不阻断切换流程。
        F16：预留 ID 贯穿终态——非 None 时结算经 F22 预留转换 / F23 活跃预留
        背书补记收敛预留，不留悬挂占用；None（usage_only / 无预留）行为不变。
        F18/F19：终态携带派发时点固化的审计元数据（归属月 / retry_index /
        连接身份），经 ledger 登记在途引用——提交被取消打断时由取消边界幂等
        重提交（F19 受限清理）。F34：``duration_ms`` 为该次尝试真实耗时
        （尝试起点 → 终态构造）。
        """
        await self._submit_terminal_logged(
            _terminal_record(
                identity,
                attempt_id,
                usage=None,
                actual_model=None,
                status="failed",
                error_code=error_code,
                reservation_id=reservation_id,
                metadata=dict(metadata),
                duration_ms=duration_ms,
            ),
            ledger,
        )

    async def _submit_terminal_logged(self, record: Any, ledger: _AttemptLedgerState) -> None:
        """终态落账（尽力而为，§6.2 步骤 5）：不持久化仅记日志，不阻断执行流。

        F19：记录登记进 ledger——提交被取消打断（CancelledError 传播）时由
        取消边界幂等重提交（DAO revision 水位保证不双计）；持久化成功即收敛
        完成（清 pending），此后取消无需再收敛该尝试。``submitted`` 标记终态
        已提交（B-02-3 结算事实投影依据：concluded → settled / submitted 未
        concluded → deferred）。
        """
        ledger.pending = record
        ledger.submitted = True
        outcome = await self._recorder.submit_terminal(record)
        if outcome.persisted:
            ledger.concluded = True
            ledger.pending = None
        else:
            logger.info("[ScenarioExecutor] terminal not persisted (%s)", outcome.error_code)

    async def _cleanup_on_cancel(
        self,
        identity: _TurnIdentity,
        *,
        attempt_id: str,
        reservation_token: str,
        outbound_started: bool,
        ledger: _AttemptLedgerState,
        metadata: dict[str, Any],
        duration_ms: float | None = None,
    ) -> None:
        """取消传播边界的受限清理（F19）：尽力收敛终态后由调用方 re-raise。

        R2：不吞取消——本方法不捕获 CancelledError（清理期间二次取消直接
        传播，让位于取消语义）；非取消失败（预算耗尽 / 账本不可用）仅记日志，
        不阻断取消传播。

        F19-003：清理预算取 ``_cancel_cleanup_timeout_s``（≥ recorder 落账内部
        超时 + 余量）——预算耗尽时 recorder 侧落账协程被取消，但终态记录经
        ``UsageRecorder.submit_terminal`` 的取消分支保留待重放（不再直穿丢弃）。

        终态收敛语义（R21 收费未知不伪装确定终态）：
        - 该尝试终态已落账 → 无需收敛（revision 水位下重提交亦无推进）；
        - 在途终态（提交被取消打断）→ 短超时幂等重提交原记录；
        - 否则提交 cancelled 终态（usage 缺失标 unavailable，actual_model=None）：
          外发未开始（可证零消费）→ 携带预留 token，F23 背书补记缺失 attempt 行
          且预留同事务 settled（费用确定为零口径）；外发已开始（无法证明是否已
          被上游消费）→ 不携带预留 ID（R21：禁止记 settled 伪装费用确定），预留
          留 dispatched 经 F23 租约到期自愈为 held_unconfirmed（「结果未知」占用，
          人工处置）——对齐 F11 commit_unknown「无法证明即标注未知」精神。
        """
        if ledger.concluded:
            return
        record = ledger.pending
        if record is None:
            # F34：新建 cancelled 终态携带该次尝试真实耗时；在途重提交沿用原
            # 记录构造时点的耗时（终态确定时点，不虚增清理等待时长）
            record = _terminal_record(
                identity,
                attempt_id,
                usage=None,
                actual_model=None,
                status="cancelled",
                error_code=_CANCELLED_ERROR_CODE,
                reservation_id=None if outbound_started else reservation_token,
                metadata=dict(metadata),
                duration_ms=duration_ms,
            )
        try:
            outcome = await asyncio.wait_for(
                self._recorder.submit_terminal(record), timeout=self._cancel_cleanup_timeout_s
            )
            if not outcome.persisted:
                logger.info("[ScenarioExecutor] cancel-cleanup terminal not persisted (%s)", outcome.error_code)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 提交失败不阻断取消传播（R2：受限清理让位于取消）
            logger.warning("[ScenarioExecutor] cancel-cleanup terminal not submitted: %s", type(e).__name__)

    async def _backoff(
        self, error: Exception, request: InferenceRequest, clock: Callable[[], float], *, retry_index: int = 0
    ) -> None:
        """退避睡眠（C12）：时长契约见 :func:`_backoff_seconds`（单一实现，Tenacity
        wait 策略同源消费）；本方法保留为该契约的直调形态。"""
        seconds = _backoff_seconds(error, retry_index, now=clock(), deadline=request.deadline)
        await asyncio.sleep(seconds)

    # ------------------------------------------------------------------
    # 组装辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _attempt_row(
        identity: _TurnIdentity,
        cand: Mapping[str, Any],
        connection: ResolvedConnection,
        snapshot: ExecutionSnapshot,
        permit: Any,
        reservation_id: str | None,
        retry_index: int,
    ) -> dict[str, Any]:
        return {
            # F34：真实子任务标识（identity.task_id），与终态元数据 / 预留同源——
            # 候选携带的 task_id 是分派器 scene ID 占位，不落账
            "task_id": identity.task_id,
            "candidate_index": int(cand.get("candidate_index", 0)),
            # F18：该次真实尝试的序号（逐尝试递增），不再固定 0
            "retry_index": retry_index,
            "connection_id": connection.connection_id,
            "connection_revision": connection.connection_revision,
            "adapter_kind": connection.adapter_kind,
            "security_revision": getattr(permit, "security_revision", 0),
            "reservation_id": reservation_id,
        }


def _resolve_connection(raw: Mapping[str, Any]) -> tuple[ResolvedConnection, _ConnectionEligibility]:
    """快照连接定义（mapping，S2 快照契约）→ (ResolvedConnection, 资格证据)。

    必需字段（connection_id / connection_revision / adapter_kind / model）缺失即抛
    KeyError / ValueError（缺失不伪装，R21 邻域语义），由调用方降级 rejected。

    F26 资格证据（``enabled`` / ``capabilities``）随同一解析点提取，不再丢弃：
    防御性解析（非 Mapping / 缺 ``support`` 键的证据条目按无证据处理，视为
    unknown，不门控）；``enabled`` 缺键取 schema 默认 True（与改动前行为一致）。
    """
    adapter_kind = str(raw["adapter_kind"])
    if adapter_kind not in ("litellm_chat", "gguf_chat", "gguf_sentiment"):
        raise ValueError(f"unknown adapter_kind: {adapter_kind}")
    capabilities: dict[str, str] = {}
    capabilities_raw = raw.get("capabilities")
    if isinstance(capabilities_raw, Mapping):
        for evidence_task_id, evidence in capabilities_raw.items():
            if isinstance(evidence, Mapping) and evidence.get("support") is not None:
                capabilities[str(evidence_task_id)] = str(evidence["support"])
    resolved = ResolvedConnection(
        connection_id=str(raw["connection_id"]),
        connection_revision=int(raw["connection_revision"]),
        adapter_kind=cast(Literal["litellm_chat", "gguf_chat", "gguf_sentiment"], adapter_kind),
        provider=str(raw.get("provider", "")),
        model=str(raw["model"]),
        api_base=raw.get("api_base"),
        adapter_options=dict(raw.get("adapter_options") or {}),
        context_window=raw.get("context_window"),
        concurrency_limit=max(1, int(raw.get("concurrency_limit", 1))),
        credential_ref=raw.get("credential_ref"),
        credential_version=raw.get("credential_version"),
    )
    return resolved, _ConnectionEligibility(enabled=bool(raw.get("enabled", True)), capabilities=capabilities)


def _ineligibility_reason(eligibility: _ConnectionEligibility, task_id: str) -> str | None:
    """连接资格门控判定（F26）：不合格返回原因，合格返回 None。

    仅「已确认」资格事实构成门控：``enabled=False``（用户停用）或该任务能力
    ``support == "unsupported"``（已确认不支持）；证据缺失 / unknown 等未确认
    状态不门控（不得未经契约一概禁用，按既有确认政策处理）。
    """
    if not eligibility.enabled:
        return "connection disabled in snapshot"
    if eligibility.capabilities.get(task_id) == "unsupported":
        return f"connection unsupported for task {task_id}"
    return None


# F27 适配器异常分类（终止判定先于重试判定的判据正本；映射表式，不散落 if-else）
_AdapterFailureClass = Literal["content_refused", "transient", "no_retry_switchable"]


def _classify_adapter_failure(error: Exception) -> _AdapterFailureClass:
    """适配器异常四类判据（F27）。

    - ``content_refused``（终止类）：内容安全拒绝——``AIContentRefusedError``（云
      响应 refusal / content_filter 提炼，litellm_client 产出）或
      ``classify_error(context="llm")`` 判为 ``content_policy``（SDK 层内容策略
      异常，如 LiteLLM ContentPolicyViolationError）。处置：策略终态 + 中断整个
      候选遍历（禁止重试与换候选绕过）。
    - ``transient``（瞬态类）：``classify_error`` 结构化 ``should_retry=True``
      判据（429 / 5xx / 超时 / 网络类）。处置：退避后同候选重试。
    - ``no_retry_switchable``（不重试可切换类）：认证 / 配额 / 模型不存在 /
      未知等 ``should_retry=False`` 判据；输出非法（validate_output 失败走独立
      路径）同属「不重试、可切换候选」且与内容安全拒绝严格区分。未知 provider
      异常格式保守不重试（不可一律重试），未核验格式不伪装已覆盖（R21）。
    """
    if isinstance(error, AIContentRefusedError):
        return "content_refused"
    info = classify_error(error, context="llm")
    if info.get("code") == "content_policy":
        return "content_refused"
    if bool(info.get("should_retry")):
        return "transient"
    return "no_retry_switchable"


def _parse_retry_after_seconds(value: Any) -> float | None:
    """解析适配器异常携带的 Retry-After（秒，F27）：数值与纯数字字符串；其余
    （bool / 非正数 / HTTP-date 等未核验格式）返回 None——不伪装支持（R21）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


class _DeadlineExhaustedError(Exception):
    """C12 内部哨兵：逐尝试 deadline 检查耗尽（非瞬态，不重试）。

    仅在 ``_run_candidate`` 的 Tenacity 重试边界内作为控制流信号——由 retry 策略
    判为不可重试后原样重抛，外层转 failed 终态（reason=「deadline exhausted」，
    与自管实现 break 语义逐字一致）。
    """


def _backoff_seconds(error: Exception, retry_index: int, *, now: float, deadline: float) -> float:
    """退避契约（F27/方案 D7，C12 单一实现）：基线 1s/2s 随 retry_index 指数递增
    （封顶 2s），与服务端 Retry-After 取大——Retry-After 不再截断为退避封顶（修复
    429 等待失真），尊重至 ``_RETRY_AFTER_CAP_S`` 上限（防恶意 / 畸形大值），最后仍
    clamp 至剩余 deadline。

    Retry-After 仅承诺数值（秒）与纯数字字符串形态（经
    :func:`_parse_retry_after_seconds`）；HTTP-date 等未核验格式不伪装支持（R21）。
    """
    base = min(_RETRY_BACKOFF_BASE_S * (2**retry_index), _RETRY_BACKOFF_MAX_S)
    retry_after = _parse_retry_after_seconds(getattr(error, "retry_after", None))
    if retry_after is not None:
        base = max(base, min(retry_after, _RETRY_AFTER_CAP_S))
    remaining = deadline - now
    return max(0.0, min(base, remaining))


def _route_transient_retries(route: Mapping[str, Any] | None) -> int | None:
    """路由冻结快照的瞬态重试策略（F27）：0（零重试）..2；缺失 / 非法 → None
    （回退构造默认，不伪装策略存在，R21）。bool 是 int 子类，显式排除。"""
    if route is None:
        return None
    value = route.get("transient_retries")
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 2):
        return None
    return value


def _route_fallback_enabled(route: Mapping[str, Any] | None) -> bool | None:
    """路由冻结快照的禁备用策略（F27）：True / False；缺失 / 非法 → None（回退
    既有行为——允许切换候选）。"""
    if route is None:
        return None
    value = route.get("fallback_enabled")
    return value if isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class _ConnectionEligibility:
    """冻结连接资格证据（F26）：快照连接映射的启用状态 + 任务能力支持状态。

    仅承载解析后的「已确认」资格事实：``enabled=False`` 或任务能力
    ``support == "unsupported"`` 才构成派发门控；无证据即 unknown 语义（不门控）。
    """

    enabled: bool
    capabilities: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _TurnIdentity:
    """共享执行段的请求身份投影（新旧入口各自投影，HANDOFF §6.1）。

    ``payload`` 为预留估算投影（F19 金额估算输入，非出站形状）：旧入口原样
    携带 InferenceRequest.payload；chat 入口经 :func:`_chat_estimation_payload`
    投影 instructions / messages 文本与 max_output_tokens。
    """

    invocation_id: str
    scenario_id: str
    task_id: str
    deadline: float
    run_id: str | None
    payload: Mapping[str, Any]


@dataclass(slots=True)
class _TraversalContext:
    """遍历准备产物（新旧入口共享）：路由 / 连接 / 资格 / 计价 / F16 owner 身份。"""

    route: Mapping[str, Any] | None
    connections_by_id: dict[str, ResolvedConnection]
    eligibility_by_id: dict[str, _ConnectionEligibility]
    pricing_by_id: dict[str, ConnectionPricing | None]
    month: str
    owner_session_id: str
    owner_epoch: int
    owner_lease_expires_at: Any


@dataclass(frozen=True, slots=True)
class _CandidateAdmission:
    """候选准入结果（共享遍历主干）：``decision`` 非 None 即未准入（含原因）；
    ``stop=True`` 表示收敛整个遍历（deadline 耗尽，与旧 break 语义一致）；
    准入时携带许可句柄（connection / semaphore / cooldown_key 齐备）。"""

    decision: CandidateDecision | None
    stop: bool = False
    connection: ResolvedConnection | None = None
    semaphore: asyncio.Semaphore | None = None
    cooldown_key: tuple[str, int] | None = None


@dataclass(frozen=True, slots=True)
class _TraversalResult:
    """候选遍历中性终态（新旧入口各自包装为公共结果，HANDOFF §6.1）。

    ``candidate``：succeeded 时的原始候选结果 / 失败时最后一个实际执行的候选
    结果（chat 终态投影 settlement / attempt / partial 的来源）；``failure_reason``
    为失败收敛原因（chat error_code 来源，旧入口不消费）。
    """

    status: Literal["succeeded", "failed"]
    decisions: tuple[CandidateDecision, ...]
    outputs: tuple[Mapping[str, Any], ...]
    usages: tuple[NormalizedLLMUsage, ...]
    candidate: _CandidateOutcome | None = None
    failure_reason: str | None = None


@dataclass(slots=True)
class _AttemptScope:
    """单次尝试的共享执行段上下文（policy 回调句柄，HANDOFF §6.1）。

    ``metadata`` 为派发时点固化的审计元数据（响应后增量覆盖 actual_model，与
    取消清理同源）；``reservation_id`` 贯穿终态（F16/F22/F23）。
    """

    identity: _TurnIdentity
    attempt_id: str
    reservation_id: str | None
    metadata: dict[str, Any]
    ledger: _AttemptLedgerState


@dataclass(frozen=True, slots=True)
class _AttemptPolicy:
    """入口差异策略（HANDOFF §6.1 共享执行段参数化）。

    - ``invoke``：适配器调用形态（旧链 ``adapter.invoke``；chat
      ``adapter.invoke_chat`` + 事件追踪包装）；
    - ``finalize``：成功响应收敛（共享段 observe_usage 之后的校验 / 终态 /
      输出投影；入口特有失败处置亦在其中返回对应结果）；
    - ``on_adapter_failure``：适配器异常且非终止类（content_refused 已在共享段
      halt）提交失败终态后的入口特有收敛——返回结果即按其收敛（不重试不切换），
      返回 None 走共享默认（F27 退避重试 / 切换候选）；
    - ``adapter_check``：准入期附加协议检查（chat：invoke_chat 存在性）；
      返回拒绝原因或 None。
    """

    invoke: Callable[[InferenceAdapter, ResolvedConnection, AttemptContext], Awaitable[Any]]
    finalize: Callable[[Any, _AttemptScope, float], Awaitable[_CandidateOutcome]]
    on_adapter_failure: Callable[[Exception, _AttemptScope], Awaitable[_CandidateOutcome | None]] | None = None
    adapter_check: Callable[[InferenceAdapter, ResolvedConnection], str | None] | None = None


@dataclass(slots=True)
class _ChatStreamExposure:
    """chat 回合的公开分片追踪（§6.4-5）：任一真实事件送达调用方（on_event
    之前即置位，保守口径——handler 异常时分片亦已到达调用边界）即 ``exposed``——
    其后失败保原 attempt，不重试不换候选（不换模型拼接）。``partial_parts``
    累积已公开 text delta（partial 证据）。"""

    exposed: bool = False
    partial_parts: list[str] = field(default_factory=list)

    @property
    def partial_text(self) -> str | None:
        return "".join(self.partial_parts) or None


@dataclass(slots=True)
class _AttemptTrail:
    """单候选内逐尝试轨迹（_run_candidate 出口统一投影结算事实与 attempt 身份）。

    ``ledger`` / ``attempt_id`` 随每次真实尝试刷新；出口取最后一次尝试投影。
    """

    ledger: _AttemptLedgerState | None = None
    attempt_id: str | None = None


def _settlement_state(ledger: _AttemptLedgerState | None) -> ChatTurnSettlementState | None:
    """结算事实投影（ChatTurnOutcome.settlement_state）：``concluded`` →
    settled；``submitted``（终态已提交、持久化未确认，F20 重放承接）→ deferred；
    无账目事实 → None。``unknown``（收费无法证明）仅由取消路径 F19
    held_unconfirmed 语义承载——取消传播不产出 outcome，该值供类型完备性。"""
    if ledger is None:
        return None
    if ledger.concluded:
        return "settled"
    if ledger.submitted:
        return "deferred"
    return None


def _traversal_failure_reason(decisions: list[CandidateDecision]) -> str:
    """失败收敛原因（chat error_code 来源）：取最后一条决策原因；无决策时
    （空候选）回退诚实缺省（R21 不伪装）。"""
    for decision in reversed(decisions):
        if decision.reason:
            return decision.reason
    return "no eligible candidate"


def _make_tracked_event(
    exposure: _ChatStreamExposure,
    on_event: ChatStreamEventHandler | None,
) -> ChatStreamEventHandler:
    """事件追踪包装（§6.4-5）：送达即置 exposed（保守口径）；text delta 累积为
    partial 证据；on_event 可等待，await 语义透传（背压）。"""

    async def _tracked(event: ChatStreamEvent) -> None:
        exposure.exposed = True
        if event.kind == "text" and isinstance(event.text_delta, str):
            exposure.partial_parts.append(event.text_delta)
        if on_event is not None:
            outcome = on_event(event)
            if inspect.isawaitable(outcome):
                await outcome

    return _tracked


def _chat_adapter_check(adapter: InferenceAdapter, connection: ResolvedConnection) -> str | None:
    """chat 入口准入附加检查（HANDOFF §6.1）：非聊天适配器（FinBERT 等）不接
    ChatInferenceAdapter 协议——派发前淘汰（零预留副作用）。"""
    if not callable(getattr(adapter, "invoke_chat", None)):
        return f"no chat adapter for kind {connection.adapter_kind}"
    return None


def _legacy_invoke(
    request: InferenceRequest,
) -> Callable[[InferenceAdapter, ResolvedConnection, AttemptContext], Awaitable[Any]]:
    """旧链适配器调用形态（共享执行段参数化，HANDOFF §6.1）：单 prompt 出口，
    校验收敛经 ``validate_output``（入口行为不变）。"""

    async def _invoke(
        adapter: InferenceAdapter, connection: ResolvedConnection, attempt_context: AttemptContext
    ) -> Any:
        return await adapter.invoke(request, connection, request.deadline, attempt_context)

    return _invoke


def _chat_estimation_payload(request: ChatTurnRequest) -> dict[str, Any]:
    """chat 回合的预留估算投影（F19 金额估算输入，非出站形状）：``instructions``
    与 ``messages`` 文本 part 拼接 + ``max_output_tokens``——键口径与
    :func:`_estimate_reservation_amount` 的 llm_tokens 估算对齐（system_prompt /
    prompt / max_tokens）；图片 part 不参与估算（桥层无二进制解析语义）。"""
    prompt_parts: list[str] = []
    for message in request.messages:
        for part in message.content_parts:
            if isinstance(part, TextContentPart):
                prompt_parts.append(part.text)
    return {
        "system_prompt": "\n".join(request.instructions),
        "prompt": "\n".join(prompt_parts),
        "max_tokens": request.max_output_tokens,
    }


@dataclass(frozen=True, slots=True)
class ScenarioOutput(Mapping[str, Any]):
    """场景成功输出的公共契约（F03）：Executor 唯一生产出口，消费者按此类型读取。

    - ``output``：校验通过的业务载荷（Mapping）；
    - ``metadata``：程序生成溯源（连接身份 / actual_model / security_revision 等，
      模型输出不可伪造）+ 校验器携带的适配器元数据合并。

    实现 Mapping 视图（仅 ``output`` / ``metadata`` 两键）以兼容
    ``ScenarioExecutionResult.outputs`` 的 ``Mapping[str, Any]`` 声明
    （core 公共类型禁改），同时保留消费者属性访问协议。
    """

    output: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if key == "output":
            return self.output
        if key == "metadata":
            return self.metadata
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield "output"
        yield "metadata"

    def __len__(self) -> int:
        return 2


@dataclass(frozen=True, slots=True)
class _CandidateOutcome:
    """单候选执行内部结果。

    ``decision_hint``：失败时候选决策标记（authorize 失败属 rejected，其余 switched）。
    ``halt_candidates``（F18）：费用型预留被拒 / 派发绑定失权 / 内容拒绝 / 必需
    登记失败时置 True——预算 / owner / 内容 / 登记为 invocation 级事实，切换候选
    无济，中断整个候选遍历（D5 既有 break 语义）；置位时不进连接冷却（非连接侧
    故障）。

    B-02-3 扩展（共享执行段出口统一投影，旧入口不消费）：
    ``attempt_id`` / ``settlement_state``（ChatTurnOutcome 身份关联与结算事实）、
    ``chat_response``（chat 回合最终响应，拒绝回合随 failed 终态承载）、
    ``partial_text``（§6.4-5 已公开分片证据）、``stop_traversal``（已公开分片后
    失败——中断遍历，属连接侧事实沿常规失败路径进冷却）。
    """

    status: str
    reason: str
    outputs: tuple[Mapping[str, Any], ...]
    usages: tuple[NormalizedLLMUsage, ...]
    decision_hint: Literal["skipped", "rejected", "attempted", "switched", "succeeded", "failed"] = "switched"
    halt_candidates: bool = False
    attempt_id: str | None = None
    settlement_state: ChatTurnSettlementState | None = None
    chat_response: ChatTurnResponse | None = None
    partial_text: str | None = None
    stop_traversal: bool = False


@dataclass(slots=True)
class _AttemptLedgerState:
    """单次尝试的终态收敛状态（F19，取消边界清理依据；逐尝试新建，无共享可变态）。

    ``pending``：最后构建、尚未确认持久化的终态记录——提交被取消打断时由
    取消边界幂等重提交（DAO revision 水位保证不双计）；``concluded``：终态
    已持久化，此后取消无需再收敛该尝试（与 pending 互斥维护：持久化即清空）；
    ``submitted``（B-02-3）：终态已提交（持久化未确认时由 F20 重放队列承接）——
    结算事实投影依据（concluded → settled / submitted 未 concluded → deferred /
    均无 → 无账目事实）。
    """

    pending: Any = None
    concluded: bool = False
    submitted: bool = False


def _observation_from(
    identity: _TurnIdentity,
    attempt_id: str,
    response: AdapterResponse,
    metadata: dict[str, Any],
) -> Any:
    from services.ai_service.usage_recorder import AttemptUsageObservation

    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id=identity.invocation_id,
        usage=response.usage,
        actual_model=response.actual_model,
        metadata=metadata,
    )


def _terminal_record(
    identity: _TurnIdentity,
    attempt_id: str,
    *,
    usage: NormalizedLLMUsage | None,
    actual_model: str | None,
    status: str,
    error_code: str | None,
    reservation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    duration_ms: float | None = None,
) -> Any:
    from services.ai_service.usage_recorder import AttemptTerminalRecord

    return AttemptTerminalRecord(
        attempt_id=attempt_id,
        invocation_id=identity.invocation_id,
        status=status,
        usage=usage,
        error_code=error_code,
        duration_ms=duration_ms,
        reservation_id=reservation_id,
        metadata=metadata,
    )


def _elapsed_ms(clock: Callable[[], float], started_at: float) -> float:
    """尝试真实耗时（毫秒）：尝试起点 → 终态构造时点（F34，clock 单调非负）。"""
    return (clock() - started_at) * 1000.0


def _estimate_reservation_amount(
    pricing: ConnectionPricing | None,
    payload: Mapping[str, Any],
    *,
    model: str,
) -> str | None:
    """按连接计价冻结快照 + 请求 payload 估算单次预留金额（F19，CNY 十进制串）。

    dispatch 不持有请求 payload，单次金额只能在执行侧逐候选估算；估算口径与
    结算同源（``compute_cost_cny``，不可计价一律 ``None``）：
    - ``llm_tokens``：输入 = ``system_prompt`` + ``prompt`` 的 token 估算；输出
      取 payload ``max_tokens``（未声明时按 ``OUTPUT_RESERVE_TOKENS`` 保守预留）；
    - ``encoder_input_tokens``：payload ``texts`` 逐条 token 估算求和；
    - 计价快照缺失 / ``mode`` 非 ``estimate_cost`` / 单价或用量不可得 → ``None``
      （R21：未知成本不以 0 占位）。D13-005：限额已配置时 ``None`` 由调用方按
      fail-closed 拒绝（不静默降级 usage_only 绕过预算门）。
    """
    if pricing is None or pricing.mode != "estimate_cost":
        return None
    if pricing.pricing_unit == "llm_tokens":
        input_tokens = _estimate_tokens(payload.get("system_prompt"), model) + _estimate_tokens(
            payload.get("prompt"), model
        )
        if input_tokens <= 0:
            return None
        raw_max_tokens = payload.get("max_tokens")
        output_tokens = (
            raw_max_tokens
            if isinstance(raw_max_tokens, int) and not isinstance(raw_max_tokens, bool) and raw_max_tokens > 0
            else OUTPUT_RESERVE_TOKENS
        )
        usage = NormalizedLLMUsage(
            input_total_tokens=input_tokens,
            uncached_input_tokens=input_tokens,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=output_tokens,
        )
        return compute_cost_cny(pricing, usage)
    if pricing.pricing_unit == "encoder_input_tokens":
        texts = payload.get("texts")
        if not isinstance(texts, (list, tuple)) or not texts:
            return None
        tokens = sum(_estimate_tokens(text, model) for text in texts)
        if tokens <= 0:
            return None
        usage = NormalizedLLMUsage(
            input_total_tokens=0,
            uncached_input_tokens=0,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=0,
        )
        return compute_cost_cny(pricing, usage, encoder_input_tokens=tokens)
    return None


def _reported_encoder_input_tokens(usage: NormalizedLLMUsage | None) -> int | None:
    """适配器报告的 encoder 真实输入 token（D13-009）；无报告 → ``None``。

    encoder 计价无缓存分量语义，取输入总量（``input_total_tokens``，缺失回退
    ``uncached_input_tokens``）；畸形报告（非整数 / 负数）同样按未知处理——
    结算不得用估算顶替实测，未知保持 ``None``（R21）。
    """
    if usage is None:
        return None
    tokens = usage.input_total_tokens if usage.input_total_tokens is not None else usage.uncached_input_tokens
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        return None
    return tokens


def _encoder_usage_metadata(
    pricing: ConnectionPricing | None,
    identity: _TurnIdentity,
    model: str,
    response: AdapterResponse | None = None,
) -> dict[str, Any]:
    """encoder 计价连接的用量证据（F17 / D13-009，落账侧成本计算所需）。

    - ``encoder_input_tokens`` 仅在适配器**报告真实用量**时写入（D13-009：
      结算与账本列用 Scorer 完成块的真实 token——截断后输入、partial 仅完成
      块）；未报告（失败 / 无 token 计量）不写该键 → 结算按不可计价返回
      ``None``（R21 未知不填 0，也不拿预估冒充实测）。
    - payload ``texts`` 逐条估算只作 ``encoder_input_tokens_estimate`` 另标
      （与预留金额同口径留痕），不参与结算。
    """
    if pricing is None or pricing.pricing_unit != "encoder_input_tokens":
        return {}
    metadata: dict[str, Any] = {}
    texts = identity.payload.get("texts")
    if isinstance(texts, (list, tuple)) and texts:
        metadata["text_count"] = len(texts)
        estimate = sum(_estimate_tokens(text, model) for text in texts)
        if estimate > 0:
            metadata["encoder_input_tokens_estimate"] = estimate
    reported = _reported_encoder_input_tokens(response.usage if response is not None else None)
    if reported is not None:
        metadata["encoder_input_tokens"] = reported
    return metadata


def _budget_policy_snapshot(route: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """父请求携带的预算政策快照（F16）：取路由候选冻结的预算身份（dispatch 层
    统一冻结，整批同源一致）；路由缺失 / 候选为空 / 候选未携带 → ``None``
    （usage_only 语义，不伪装政策存在，R21）。"""

    if route is None:
        return None
    for cand in route.get("candidates") or ():
        if isinstance(cand, Mapping):
            snap = cand.get("budget_policy_snapshot")
            if snap is not None:
                return dict(snap)
    return None


def _metadata(
    identity: _TurnIdentity,
    cand: Mapping[str, Any],
    connection: ResolvedConnection,
    response: AdapterResponse | None,
    security_revision: int,
    pricing: ConnectionPricing | None = None,
    retry_index: int = 0,
    *,
    month: str,
) -> dict[str, Any]:
    """落账元数据（归属月 / 连接身份 / 计价冻结快照 / retry_index）。

    F18：``retry_index`` 取该次真实尝试的序号（逐尝试递增），不再读候选映射
    （dispatch 冻结候选从不携带该键，恒 0）；``response`` 允许 None——失败
    路径（适配器异常 / 授权失败）无响应可观察，``actual_model=None``
    （未知不伪装，R21），元数据供无观察终态回退落账。
    F19③：``month`` 为必传参数（execute 起点冻结值），不再于本函数内重算
    系统时间——观察 / 失败终态 / 取消终态的归属月与预留归属月同源（月界不
    漂移），审计字段在派发时点固化，不依赖事后重算。
    F34：``task_id`` 取请求身份投影的真实子任务标识（``identity.task_id``）——
    候选携带的 ``task_id`` 是分派器冻结的 scene ID 占位（scenario_dispatch 以
    scenario_id 填充），不落账；预留 / attempt 行 / 终态元数据三处同源。
    """
    return {
        "connection_id": connection.connection_id,
        "connection_revision": connection.connection_revision,
        "adapter_kind": connection.adapter_kind,
        "configured_model": connection.model,
        "actual_model": response.actual_model if response is not None else None,
        "scenario_id": identity.scenario_id,
        "task_id": identity.task_id,
        "trigger": str(cand.get("trigger", "manual")),
        "candidate_index": int(cand.get("candidate_index", 0)),
        "retry_index": retry_index,
        # F01：许可线性化点的安全修订（permit），不再混用快照授权范围版本
        "security_revision": security_revision,
        "month": month,
        "run_id": identity.run_id,
        # F17：连接计价冻结快照（mode / pricing_unit / 价格来源 / 单价 / 汇率 /
        # 生效时点，金额十进制字符串保精度）。成本不在此计算——由 Recorder 在
        # 终态落账时点按真实 usage 经 connection_pricing.compute_cost_cny 计算
        # （cost 与 usage 同源）；快照缺失 → None（未知语义留证，R21 不伪装）。
        "pricing": pricing_snapshot_metadata(pricing),
        # F17 / D13-009：encoder 计价连接的用量证据（text_count / 估算另标）。
        # 派发时点尚无适配器真实用量 → 不写 encoder_input_tokens（结算侧
        # compute_cost_cny 与账本列依赖真实 token，由响应返回后增量覆盖）。
        # 非 encoder 计价连接不引入该键。
        **_encoder_usage_metadata(pricing, identity, connection.model),
    }
