"""场景执行器（S3，ADR-0014 §6.1，模型统一.md §6.2，方案 D7）。

职责：按快照路由把一次推理请求编排为「许可 → 派发 → 适配器调用 → 校验 →
落账收敛」的候选遍历；不做配置读写（快照冻结语义由 §3.3 保证）、不直接
触账本（一律经 UsageRecorder）。

关键契约（方案 §6.2 固定执行流程）：
- **执行序**：父请求建立（F16，先于候选遍历——全候选被拒 / deadline 耗尽等
  零尝试请求同样在请求页可查）→ 冷却检查 → 连接并发许可（排队计入 deadline）→
  逐实际尝试原子预算预留（F16，D3 月锁内 try_reserve）→ mark_dispatched 绑定
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
  备用）在 Executor 消费生效——键缺失 / 非法时回退构造默认（分派侧未填充该键
  的诚实缺省，不伪装策略存在，R21）；``fallback_enabled=False`` 只派发路由首
  个候选，其余候选记 skipped（不静默）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast

from core.errors import AIContentRefusedError
from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    CandidateDecision,
    ExecutionSnapshot,
    InferenceAdapter,
    InferenceRequest,
    NormalizedLLMUsage,
    ResolvedConnection,
    ScenarioExecutionResult,
    adapter_kind_requires_egress,
    ensure_inference_adapter,
)
from services.ai_service.connection_pricing import (
    ConnectionPricing,
    parse_connection_pricing,
    pricing_snapshot_metadata,
)
from utils.loop_local import get_loop_local
from utils.error_classifier import classify_error
from utils.log_decorators import PerfThreshold, log_async_operation

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

# F19：取消边界清理的终态提交短超时（秒）——受限清理不得拖延取消传播；超时
# 放弃提交（外发已开始时预留经 F23 租约到期自愈 held_unconfirmed 兜底）。
_CANCEL_CLEANUP_TIMEOUT_S: Final[float] = 2.0
# F19：取消终态错误码（与超时 / 失败 / 仍运行可区分的账本事实）
_CANCELLED_ERROR_CODE: Final[str] = "CANCELLED"


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
        """按快照路由执行：候选遍历 + 失败切换，返回场景终态。"""
        loop = asyncio.get_running_loop()
        clock = self._clock or loop.time
        state = get_loop_local(_LOOP_STATE_KEY, _LoopState)

        route = next((r for r in snapshot.routes if r.get("scenario_id") == request.scenario_id), None)
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
        decisions: list[CandidateDecision] = []

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
            invocation_id=request.invocation_id,
            scenario_id=request.scenario_id,
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

        if route is None:
            return self._terminal(request, "failed", (), decisions, ())

        candidates: tuple[Mapping[str, Any], ...] = route.get("candidates", ())  # type: ignore[assignment]
        # F27 路由策略（快照冻结语义）：分派侧保存的零重试 / 禁备用策略在消费端
        # 生效；键缺失 / 非法回退构造默认（不伪装策略存在，R21）。
        route_retries = _route_transient_retries(route)
        # 路由策略重试预算与构造重试预算取小（_retry_attempts 已按构造上限 clamp）
        total_attempts = (
            self._retry_attempts if route_retries is None else min(route_retries, self._retry_attempts)
        ) + 1
        fallback_enabled = _route_fallback_enabled(route)
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
        # R01-D1：local-only 政策请求级新鲜读——每次 execute 读取一次（函数局部，
        # 不落实例字段，配置切换下一次 execute 即时生效；同请求内判定一致不混版）。
        local_only = self._local_only_mode_reader()
        for cand in candidates:
            remaining = request.deadline - clock()
            if remaining <= 0:
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=cand.get("connection_id"),
                        decision="skipped",
                        reason="deadline exhausted",
                    )
                )
                break

            connection = connections_by_id.get(str(cand.get("connection_id", "")))
            if connection is None:
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=cand.get("connection_id"),
                        decision="rejected",
                        reason="connection not found in snapshot",
                    )
                )
                continue

            # F26 连接资格门控：派发前按冻结快照证据核实启用 / 任务适用性——
            # 禁用或已确认不支持该任务的连接不派发（零副作用：不取信号量、不绑
            # 派发身份、不授权）；原因随决策记录不静默（R21）；unknown 等未确认
            # 状态不门控（按既有确认政策处理），UI 保留不适用项不代表允许执行。
            ineligibility = _ineligibility_reason(eligibility_by_id[connection.connection_id], request.task_id)
            if ineligibility is not None:
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision="rejected",
                        reason=ineligibility,
                    )
                )
                continue

            # R01-D1 local-only 政策门控（候选层强制，SEC-03 语义）：仅本地模式下需
            # 外发的适配器候选（adapter_kind_requires_egress）派发前一律拒绝——不进
            # 授权链，零授权 / 零派发 / 零外发副作用；设备内本地 kind（gguf_*）不受
            # 此政策约束照常执行。与 F26 同点同模式（rejected + 原因记录，R21 不静默；
            # reason 含 "policy" 供消费端映射 policy_blocked）。注意：候选的
            # requires_egress_grant=False 仅代表「免 grant」，不构成「禁外发」——
            # 禁外发必须在此候选层淘汰，不得以 grant 语义实现。
            if local_only and adapter_kind_requires_egress(connection.adapter_kind):
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision="rejected",
                        reason="local-only policy forbids egress candidate",
                    )
                )
                continue

            cooldown_key = (connection.connection_id, connection.connection_revision)
            if state.cooldowns.get(cooldown_key, 0.0) > clock():
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision="skipped",
                        reason=f"cooldown until {state.cooldowns[cooldown_key]:.0f}",
                    )
                )
                continue

            # F16 预留纪律：适配器缺失的候选在预留前淘汰（无法产生任何实际尝试，
            # 预留只会造成保守多占）；与 F26 门控同思路（派发前淘汰，零预留副作用）。
            if connection.adapter_kind not in self._adapters:
                decisions.append(
                    CandidateDecision(
                        task_id=str(cand.get("task_id", "")),
                        candidate_index=int(cand.get("candidate_index", 0)),
                        connection_id=connection.connection_id,
                        decision="rejected",
                        reason=f"no adapter for kind {connection.adapter_kind}",
                    )
                )
                continue

            # 方案 §6.2 步骤 3：先取得连接并发许可（排队计入 deadline），再绑定派发，
            # 最后逐次 authorize_attempt——不在安全许可后再排队（步骤 4）
            semaphore = state.semaphores.setdefault(
                connection.connection_id, asyncio.Semaphore(max(1, connection.concurrency_limit))
            )
            async with semaphore:
                # F18：预留 / 派发绑定 / 授权 / 执行均按真实尝试粒度在重试循环内
                # 逐次进行（每次真实尝试独立身份），见 _run_candidate。
                outcome = await self._run_candidate(
                    request,
                    snapshot,
                    connection,
                    cand,
                    clock,
                    validate_output,
                    month=month,
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    owner_lease_expires_at=owner_lease_expires_at,
                    pricing=pricing_by_id.get(connection.connection_id),
                    total_attempts=total_attempts,
                )

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
                return self._terminal(
                    request, "succeeded", outcome.outputs, [*decisions, *fallback_skipped], outcome.usages
                )

            if outcome.halt_candidates:
                # D5：费用型预留被拒 / 派发绑定失权——invocation 级事实（预算耗尽 /
                # owner 失权），切换候选无济，中断整个候选遍历（F16 既有 break 语义；
                # 非连接侧故障，不进冷却）
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

        return self._terminal(request, "failed", (), [*decisions, *fallback_skipped], ())

    # ------------------------------------------------------------------
    # 单候选执行（重试内循环）
    # ------------------------------------------------------------------

    async def _run_candidate(
        self,
        request: InferenceRequest,
        snapshot: ExecutionSnapshot,
        connection: ResolvedConnection,
        cand: Mapping[str, Any],
        clock: Callable[[], float],
        validate_output: Callable[[AdapterResponse], Any],
        *,
        month: str,
        owner_session_id: str,
        owner_epoch: int,
        owner_lease_expires_at: Any,
        pricing: ConnectionPricing | None,
        total_attempts: int | None = None,
    ) -> _CandidateOutcome:
        adapter = self._adapters.get(connection.adapter_kind)
        if adapter is None:
            # 候选不合格（无适配器），未授权未执行，不建 attempt（§6.2 步骤 2）
            return _CandidateOutcome(
                status="failed",
                decision_hint="rejected",
                reason=f"no adapter for kind {connection.adapter_kind}",
                outputs=(),
                usages=(),
            )

        budget_amount = cand.get("budget_amount_cny")
        last_reason = "exhausted"
        # F27：尝试预算可由路由冻结快照的瞬态重试策略（transient_retries）驱动
        # （execute 计算传入）；None 回退构造默认。
        attempts = total_attempts if total_attempts is not None else self._retry_attempts + 1
        for retry_index in range(attempts):
            if request.deadline - clock() <= 0:
                last_reason = "deadline exhausted"
                break

            # F18：每次真实尝试独立身份——独立 attempt / reservation 与递增
            # retry_index（外部重试 = 新的真实调用 = 新账目事实）；同一次写账的
            # 持久化重放沿用该次身份，由 DAO 幂等（ON CONFLICT DO NOTHING /
            # usage_revision 水位）承载，不在本层重试。
            attempt_id = uuid.uuid4().hex
            reservation_token = uuid.uuid4().hex
            # F34：尝试起点计时（预留 / 派发 / 授权 / 执行全程计入尝试耗时），
            # 终态构造时点为终点——duration_ms 不再恒 None（R21：未知留 None，
            # 但本路径耗时恒可知，恒为真实值）。
            attempt_started_at = clock()
            # F19③：派发时点固化审计元数据——归属月（execute 起点冻结值，月界
            # 不漂移）/ retry_index / 连接身份 / 计价冻结快照在预留前构建，观察与
            # 终态（含取消清理）一律携带本份（copy 增量），不依赖事后重算；
            # security_revision 于 authorize 线性化点就位。
            attempt_metadata = _metadata(request, cand, connection, None, 0, pricing, retry_index, month=month)
            # F19：单次尝试的终态收敛状态（取消边界清理依据）
            ledger = _AttemptLedgerState()
            # F19②：外发事实起点——adapter.invoke 开始后消费不可证明（收费未知语义分界）
            outbound_started = False

            try:
                # F16 逐实际尝试原子预留（每次真实尝试独立预留，预留 ↔ attempt 生命
                # 周期对齐：该次终态同事务结算该次预留）：费用型候选带预算身份
                # （dispatch 冻结），usage_only 候选登记 NULL 金额预留（不占预算）。
                # 前次失败尝试的预留已随其 failed 终态 settled 退出在途占用，预算
                # 门逐尝试独立判定，无跨尝试双计。
                reserve_outcome = await self._recorder.try_reserve(
                    invocation_id=request.invocation_id,
                    scenario_id=request.scenario_id,
                    task_id=str(request.task_id),
                    trigger="manual",
                    month=month,
                    reservation_id=reservation_token,
                    attempt_id=attempt_id,
                    candidate_index=int(cand.get("candidate_index", 0)),
                    retry_index=retry_index,
                    amount_cny=budget_amount,
                    budget_limit_cny=cand.get("budget_limit_cny"),
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
                    invocation_id=request.invocation_id,
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
                        requires_egress_grant=adapter_kind_requires_egress(connection.adapter_kind),
                    )
                except Exception as e:
                    reason = f"authorize failed: {type(e).__name__}"
                    if reservation_id is not None:
                        # F16：预留已绑定派发（dispatched）而授权失败——无 attempt 行，
                        # 提交 failed 终态经活跃预留背书补记（F23）并转换预留 settled，
                        # 不留悬挂占用；usage_only（无预留）不补记，维持既有零副作用
                        await self._submit_failed_terminal(
                            request,
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
                            request,
                            attempt_id,
                            last_reason,
                            reservation_id,
                            metadata=attempt_metadata,
                            ledger=ledger,
                            duration_ms=_elapsed_ms(clock, attempt_started_at),
                        )
                    break
                effective_connection = replace(connection, adapter_options=compatibility.resolved_options)

                # 真正进入适配器执行前建立实际尝试记录（§6.2 步骤 4）。F18：每次真实
                # 尝试独立 attempt 行（携带该次 retry_index）；同一次写账的持久化重放
                # 沿用该次身份（幂等，不重建）。落账失败降级继续执行（执行已授权开始，
                # 费用型此前已被预留/派发门拦截）。F16：owner 齐传启用 F23 身份强门；
                # attempt_row 携带预留 ID。
                begin_outcome = await self._recorder.begin_attempt(
                    attempt_id=attempt_id,
                    invocation_id=request.invocation_id,
                    configured_model=connection.model,
                    attempt_row=self._attempt_row(
                        request, cand, connection, snapshot, permit, reservation_id, retry_index
                    ),
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                )
                if not begin_outcome.persisted:
                    logger.warning(
                        "[ScenarioExecutor] attempt row unavailable (%s), continuing in-memory execution",
                        begin_outcome.error_code,
                    )

                attempt_context = AttemptContext(
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    invocation_id=request.invocation_id,
                    attempt_id=attempt_id,
                )
                # F19②：外发事实起点（await 表达式即视为外发已发起，此后取消时
                # 无法证明是否已被上游消费）
                outbound_started = True
                try:
                    response = await adapter.invoke(request, effective_connection, request.deadline, attempt_context)
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
                            request.task_id,
                        )
                        await self._submit_failed_terminal(
                            request,
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
                        )
                    last_reason = f"adapter error: {type(e).__name__}"
                    # F18：失败尝试即时收敛（结算该次预留 + 记失败事实，R21 不静默），
                    # 再退避重试——迟到响应在适配器层被丢弃，无第二次观察通道，费用
                    # 未知以 None 承载（R21 禁止填 0 伪装）。
                    await self._submit_failed_terminal(
                        request,
                        attempt_id,
                        last_reason,
                        reservation_id,
                        metadata=attempt_metadata,
                        ledger=ledger,
                        duration_ms=_elapsed_ms(clock, attempt_started_at),
                    )
                    if failure_class == "transient" and retry_index < attempts - 1:
                        await self._backoff(e, request, clock, retry_index=retry_index)
                        continue
                    # 瞬态重试预算耗尽，或不重试可切换类（认证 / 配额 / 未知等
                    # should_retry=False 判据，F27 保守不重试、不可一律重试）：结束
                    # 本候选，交外层按结果切换 / 收敛。
                    break

                # D6：适配器返回后立即同步转移用量（幂等按 attempt_id）；
                # security_revision 取许可线性化点的真实修订（F01：快照携带的
                # security_scope_version 是授权范围版本，不得混作安全修订落账）。
                # F17：metadata 携带计价冻结快照——成本在终态落账时点由 Recorder
                # 按真实 usage 计算（cost 与 usage 同源，避免重试场景失配）。
                # F19③：actual_model 为响应事实（增量覆盖派发时点固化元数据）。
                attempt_metadata = {**attempt_metadata, "actual_model": response.actual_model}
                self._recorder.observe_usage(_observation_from(request, attempt_id, response, attempt_metadata))

                try:
                    validated = validate_output(response)
                except Exception as e:
                    # F27 输出非法类：响应合法返回但业务输出不合法——不重试同候选，
                    # 允许切换备用候选（与内容安全拒绝的终止语义严格区分：输出非法
                    # 是质量事实而非安全策略，换候选不构成绕过）。
                    await self._submit_failed_terminal(
                        request,
                        attempt_id,
                        f"output validation failed: {type(e).__name__}",
                        reservation_id,
                        metadata=attempt_metadata,
                        ledger=ledger,
                        duration_ms=_elapsed_ms(clock, attempt_started_at),
                    )
                    return _CandidateOutcome(
                        status="failed",
                        reason=f"output validation failed: {type(e).__name__}",
                        outputs=(),
                        usages=(),
                    )

                usage_tuple = (response.usage,) if response.usage is not None else ()
                await self._submit_terminal_logged(
                    _terminal_record(
                        request,
                        attempt_id,
                        usage=response.usage,
                        actual_model=response.actual_model,
                        status="succeeded",
                        error_code=None,
                        reservation_id=reservation_id,
                        metadata=attempt_metadata,
                        duration_ms=_elapsed_ms(clock, attempt_started_at),
                    ),
                    ledger,
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
                                **attempt_metadata,
                            },
                        ),
                    ),
                    usages=usage_tuple,
                )
            except asyncio.CancelledError:
                # F19：取消传播边界的受限清理——尽力收敛终态（cancelled 终态或在途
                # 终态幂等重提交，短超时）后必须 re-raise（R2 不吞取消；提交失败
                # 不阻断取消传播）。attempt 不再永久停留 dispatched。
                await self._cleanup_on_cancel(
                    request,
                    attempt_id=attempt_id,
                    reservation_token=reservation_token,
                    outbound_started=outbound_started,
                    ledger=ledger,
                    metadata=attempt_metadata,
                    duration_ms=_elapsed_ms(clock, attempt_started_at),
                )
                raise

        return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())

    async def _submit_failed_terminal(
        self,
        request: InferenceRequest,
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
                request,
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
        完成（清 pending），此后取消无需再收敛该尝试。
        """
        ledger.pending = record
        outcome = await self._recorder.submit_terminal(record)
        if outcome.persisted:
            ledger.concluded = True
            ledger.pending = None
        else:
            logger.info("[ScenarioExecutor] terminal not persisted (%s)", outcome.error_code)

    async def _cleanup_on_cancel(
        self,
        request: InferenceRequest,
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
        传播，让位于取消语义）；非取消失败（短超时 / 账本不可用）仅记日志，
        不阻断取消传播。

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
                request,
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
            outcome = await asyncio.wait_for(self._recorder.submit_terminal(record), timeout=_CANCEL_CLEANUP_TIMEOUT_S)
            if not outcome.persisted:
                logger.info("[ScenarioExecutor] cancel-cleanup terminal not persisted (%s)", outcome.error_code)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 提交失败不阻断取消传播（R2：受限清理让位于取消）
            logger.warning("[ScenarioExecutor] cancel-cleanup terminal not submitted: %s", type(e).__name__)

    async def _backoff(
        self, error: Exception, request: InferenceRequest, clock: Callable[[], float], *, retry_index: int = 0
    ) -> None:
        """退避契约（F27/方案 D7）：基线 1s/2s 随 retry_index 指数递增（封顶 2s），
        与服务端 Retry-After 取大——Retry-After 不再截断为退避封顶（修复 429 等待
        失真），尊重至 ``_RETRY_AFTER_CAP_S`` 上限（防恶意 / 畸形大值），最后仍
        clamp 至剩余 deadline。

        Retry-After 仅承诺数值（秒）与纯数字字符串形态（经
        :func:`_parse_retry_after_seconds`）；HTTP-date 等未核验格式不伪装支持（R21）。
        """
        base = min(_RETRY_BACKOFF_BASE_S * (2**retry_index), _RETRY_BACKOFF_MAX_S)
        retry_after = _parse_retry_after_seconds(getattr(error, "retry_after", None))
        if retry_after is not None:
            base = max(base, min(retry_after, _RETRY_AFTER_CAP_S))
        remaining = request.deadline - clock()
        await asyncio.sleep(max(0.0, min(base, remaining)))

    # ------------------------------------------------------------------
    # 组装辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _attempt_row(
        request: InferenceRequest,
        cand: Mapping[str, Any],
        connection: ResolvedConnection,
        snapshot: ExecutionSnapshot,
        permit: Any,
        reservation_id: str | None,
        retry_index: int,
    ) -> dict[str, Any]:
        return {
            # F34：真实子任务标识（request.task_id），与终态元数据 / 预留同源——
            # 候选携带的 task_id 是分派器 scene ID 占位，不落账
            "task_id": str(request.task_id),
            "candidate_index": int(cand.get("candidate_index", 0)),
            # F18：该次真实尝试的序号（逐尝试递增），不再固定 0
            "retry_index": retry_index,
            "connection_id": connection.connection_id,
            "connection_revision": connection.connection_revision,
            "adapter_kind": connection.adapter_kind,
            "security_revision": getattr(permit, "security_revision", 0),
            "reservation_id": reservation_id,
        }

    def _terminal(
        self,
        request: InferenceRequest,
        status: str,
        outputs: tuple[Mapping[str, Any], ...],
        decisions: list[CandidateDecision],
        usages: tuple[NormalizedLLMUsage, ...],
    ) -> ScenarioExecutionResult:
        return ScenarioExecutionResult(
            scenario_id=request.scenario_id,
            invocation_id=request.invocation_id,
            status=status,  # type: ignore[arg-type]
            outputs=outputs,
            candidate_decisions=tuple(decisions),
            actual_usage=usages,
        )


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
    ``halt_candidates``（F18）：费用型预留被拒 / 派发绑定失权时置 True——预算 /
    owner 为 invocation 级事实，切换候选无济，中断整个候选遍历（D5 既有 break
    语义）；置位时不进连接冷却（非连接侧故障）。
    """

    status: str
    reason: str
    outputs: tuple[Mapping[str, Any], ...]
    usages: tuple[NormalizedLLMUsage, ...]
    decision_hint: Literal["skipped", "rejected", "attempted", "switched", "succeeded", "failed"] = "switched"
    halt_candidates: bool = False


@dataclass(slots=True)
class _AttemptLedgerState:
    """单次尝试的终态收敛状态（F19，取消边界清理依据；逐尝试新建，无共享可变态）。

    ``pending``：最后构建、尚未确认持久化的终态记录——提交被取消打断时由
    取消边界幂等重提交（DAO revision 水位保证不双计）；``concluded``：终态
    已持久化，此后取消无需再收敛该尝试（与 pending 互斥维护：持久化即清空）。
    """

    pending: Any = None
    concluded: bool = False


def _observation_from(
    request: InferenceRequest,
    attempt_id: str,
    response: AdapterResponse,
    metadata: dict[str, Any],
) -> Any:
    from services.ai_service.usage_recorder import AttemptUsageObservation

    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id=request.invocation_id,
        usage=response.usage,
        actual_model=response.actual_model,
        metadata=metadata,
    )


def _terminal_record(
    request: InferenceRequest,
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
        invocation_id=request.invocation_id,
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
    request: InferenceRequest,
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
    F34：``task_id`` 取请求的真实子任务标识（``request.task_id``）——候选携带
    的 ``task_id`` 是分派器冻结的 scene ID 占位（scenario_dispatch 以
    scenario_id 填充），不落账；预留 / attempt 行 / 终态元数据三处同源。
    """
    return {
        "connection_id": connection.connection_id,
        "connection_revision": connection.connection_revision,
        "adapter_kind": connection.adapter_kind,
        "configured_model": connection.model,
        "actual_model": response.actual_model if response is not None else None,
        "scenario_id": request.scenario_id,
        "task_id": str(request.task_id),
        "trigger": str(cand.get("trigger", "manual")),
        "candidate_index": int(cand.get("candidate_index", 0)),
        "retry_index": retry_index,
        # F01：许可线性化点的安全修订（permit），不再混用快照授权范围版本
        "security_revision": security_revision,
        "month": month,
        "run_id": request.run_id,
        # F17：连接计价冻结快照（mode / pricing_unit / 价格来源 / 单价 / 汇率 /
        # 生效时点，金额十进制字符串保精度）。成本不在此计算——由 Recorder 在
        # 终态落账时点按真实 usage 经 connection_pricing.compute_cost_cny 计算
        # （cost 与 usage 同源）；快照缺失 → None（未知语义留证，R21 不伪装）。
        "pricing": pricing_snapshot_metadata(pricing),
    }
