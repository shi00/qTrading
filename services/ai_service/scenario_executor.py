"""场景执行器（S3，ADR-0014 §6.1，模型统一.md §6.2，方案 D7）。

职责：按快照路由把一次推理请求编排为「许可 → 派发 → 适配器调用 → 校验 →
落账收敛」的候选遍历；不做配置读写（快照冻结语义由 §3.3 保证）、不直接
触账本（一律经 UsageRecorder）。

关键契约（方案 §6.2 固定执行流程）：
- **执行序**：父请求建立（F16，先于候选遍历——全候选被拒 / deadline 耗尽等
  零尝试请求同样在请求页可查）→ 冷却检查 → 连接并发许可（排队计入 deadline）→
  逐候选原子预算预留（F16，D3 月锁内 try_reserve）→ mark_dispatched 绑定
  派发身份 → 逐次 authorize_attempt（每次尝试单独授权，不预发下一次重试的
  许可）→ begin_attempt 建立实际尝试记录 → 适配器执行；不在安全许可后再排队
  （§4.3）；F26 连接资格门控（禁用 / 已确认不支持任务，按冻结快照证据）先于
  冷却检查——派发前淘汰不合格候选（决策记录 rejected + 原因）；
- **重试**：同候选默认 1 次、上限 2 次（构造可调），退避 1/2s 与适配器
  Retry-After 取大，并 clamp 至剩余 deadline；
- **D5 降级**：有限额费用型在预留被拒（余额不足 / owner 失权）或账本不可用
  （try_reserve / mark_dispatched persisted=False）时零出站；usage_only 预留
  或落账不可用时允许进程内一次性派发身份；失败候选收敛错误提交终态
  （§6.2 步骤 5，F16：终态携带预留 ID 经 F22 转换 / F23 背书收敛预留）；
- **R2**：CancelledError 始终传播，不包装为失败。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast
from collections.abc import Callable

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
from utils.log_decorators import PerfThreshold, log_async_operation

logger = logging.getLogger(__name__)

# 重试退避（秒）：首次 1s、第二次 2s（方案 D7）
_RETRY_BACKOFF_BASE_S: Final[float] = 1.0
_RETRY_BACKOFF_MAX_S: Final[float] = 2.0

# loop-local 键（R11）：Executor 逐循环状态（信号量 + 冷却表）
_LOOP_STATE_KEY: Final[str] = "scenario_executor_state"

# 归属月时区偏移：CST 无夏令时，固定 +8h（D2 Asia/Shanghai 口径）
_CST_OFFSET = timedelta(hours=8)

# owner 租约时长（F16，秒）：覆盖单请求 deadline（≤180s）+ 用量确认窗口（60s）
# 的保守富余；租约过期的预留经 F23 语义自愈（失权 owner 预留不可再推进，
# reserved 退出预算占用为正确的事实零释放，保守多占可接受）。
_OWNER_LEASE_SECONDS: Final[float] = 900.0


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

            attempt_id = uuid.uuid4().hex
            # 方案 §6.2 步骤 3：先取得连接并发许可（排队计入 deadline），再绑定派发，
            # 最后逐次 authorize_attempt——不在安全许可后再排队（步骤 4）
            semaphore = state.semaphores.setdefault(
                connection.connection_id, asyncio.Semaphore(max(1, connection.concurrency_limit))
            )
            async with semaphore:
                # F16 逐实际尝试原子预留（先于派发绑定，同一 reservation_id 贯穿
                # 预留 → 派发 → 结算）：费用型候选带预算身份（dispatch 冻结），
                # usage_only 候选登记 NULL 金额预留（不占预算）。
                budget_amount = cand.get("budget_amount_cny")
                reservation_token = uuid.uuid4().hex
                reserve_outcome = await self._recorder.try_reserve(
                    invocation_id=request.invocation_id,
                    scenario_id=request.scenario_id,
                    task_id=str(cand.get("task_id", request.task_id)),
                    trigger="manual",
                    month=month,
                    reservation_id=reservation_token,
                    attempt_id=attempt_id,
                    candidate_index=int(cand.get("candidate_index", 0)),
                    retry_index=0,
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
                    # 零出站（不降级外发）；原因随决策记录不静默（R21）
                    decisions.append(
                        CandidateDecision(
                            task_id=str(cand.get("task_id", "")),
                            candidate_index=int(cand.get("candidate_index", 0)),
                            connection_id=connection.connection_id,
                            decision="rejected",
                            reason=f"budget reserve rejected: {reserve_outcome.error_code}",
                        )
                    )
                    break
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
                    decisions.append(
                        CandidateDecision(
                            task_id=str(cand.get("task_id", "")),
                            candidate_index=int(cand.get("candidate_index", 0)),
                            connection_id=connection.connection_id,
                            decision="rejected",
                            reason=f"budget dispatch forbidden: {dispatch_outcome.error_code}",
                        )
                    )
                    break

                outcome = await self._run_candidate(
                    request,
                    snapshot,
                    connection,
                    cand,
                    attempt_id,
                    clock,
                    validate_output,
                    reservation_id=reservation_id,
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    pricing=pricing_by_id.get(connection.connection_id),
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
                return self._terminal(request, "succeeded", outcome.outputs, decisions, outcome.usages)

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

        return self._terminal(request, "failed", (), decisions, ())

    # ------------------------------------------------------------------
    # 单候选执行（重试内循环）
    # ------------------------------------------------------------------

    async def _run_candidate(
        self,
        request: InferenceRequest,
        snapshot: ExecutionSnapshot,
        connection: ResolvedConnection,
        cand: Mapping[str, Any],
        attempt_id: str,
        clock: Callable[[], float],
        validate_output: Callable[[AdapterResponse], Any],
        *,
        reservation_id: str | None,
        owner_session_id: str,
        owner_epoch: int,
        pricing: ConnectionPricing | None,
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

        last_reason = "exhausted"
        total_attempts = self._retry_attempts + 1
        for retry_index in range(total_attempts):
            if request.deadline - clock() <= 0:
                last_reason = "deadline exhausted"
                break

            # 许可线性化（§4.3 / 方案 §6.2 步骤 3「再逐次调用 authorize_attempt」）：
            # 每次尝试单独授权，不预发下一次重试的许可；失败即拒绝切换，不重试。
            # F06：credential_version 传快照冻结连接的实际执行版本（与下方
            # effective_connection 出站解析同源），撤销校验绑定执行对象而非磁盘现值。
            # F01：目标 / 用途取候选携带的分派授权身份（``or ""`` 防 str(None)）；
            # requires_egress_grant 按真实执行位置声明（litellm_chat 出站需 grant，
            # gguf_* 设备内本地模型不外发免 grant，凭据撤销等安全检查保留）。
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
            except asyncio.CancelledError:
                raise
            except Exception as e:
                reason = f"authorize failed: {type(e).__name__}"
                if reservation_id is not None:
                    # F16：预留已绑定派发（dispatched）而授权失败——无 attempt 行，
                    # 提交 failed 终态经活跃预留背书补记（F23）并转换预留 settled，
                    # 不留悬挂占用；usage_only（无预留）不补记，维持既有零副作用
                    await self._submit_failed_terminal(request, attempt_id, reason, reservation_id)
                return _CandidateOutcome(
                    status="failed",
                    decision_hint="rejected",
                    reason=reason,
                    outputs=(),
                    usages=(),
                )

            compatibility = self._compatibility.resolve_execution_params(
                {"adapter_kind": connection.adapter_kind, "adapter_options": dict(connection.adapter_options)}
            )
            if not compatibility.compatible:
                last_reason = f"incompatible: {compatibility.reason}"
                break
            effective_connection = replace(connection, adapter_options=compatibility.resolved_options)

            # 真正进入适配器执行前建立实际尝试记录（§6.2 步骤 4；幂等，重试不重建）。
            # 落账失败降级继续执行（执行已授权开始，费用型此前已被预留/派发门拦截）。
            # F16：owner 齐传启用 F23 身份强门；attempt_row 携带预留 ID。
            begin_outcome = await self._recorder.begin_attempt(
                attempt_id=attempt_id,
                invocation_id=request.invocation_id,
                configured_model=connection.model,
                attempt_row=self._attempt_row(request, cand, connection, snapshot, permit, reservation_id),
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
            try:
                response = await adapter.invoke(request, effective_connection, request.deadline, attempt_context)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_reason = f"adapter error: {type(e).__name__}"
                if retry_index < total_attempts - 1:
                    await self._backoff(e, request, clock)
                continue

            # D6：适配器返回后立即同步转移用量（幂等按 attempt_id）；
            # security_revision 取许可线性化点的真实修订（F01：快照携带的
            # security_scope_version 是授权范围版本，不得混作安全修订落账）。
            # F17：metadata 携带计价冻结快照——成本在终态落账时点由 Recorder
            # 按真实 usage 计算（cost 与 usage 同源，避免重试场景失配）。
            self._recorder.observe_usage(
                _observation_from(request, cand, connection, attempt_id, response, permit.security_revision, pricing)
            )

            try:
                validated = validate_output(response)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._submit_failed_terminal(
                    request, attempt_id, f"output validation failed: {type(e).__name__}", reservation_id
                )
                return _CandidateOutcome(
                    status="failed",
                    reason=f"output validation failed: {type(e).__name__}",
                    outputs=(),
                    usages=(),
                )

            usage_tuple = (response.usage,) if response.usage is not None else ()
            terminal = await self._recorder.submit_terminal(
                _terminal_record(
                    request,
                    attempt_id,
                    usage=response.usage,
                    actual_model=response.actual_model,
                    status="succeeded",
                    error_code=None,
                    reservation_id=reservation_id,
                )
            )
            if not terminal.persisted:
                # D5：usage_only 允许进程内一次性派发身份（persisted=False 不阻断）
                logger.info(
                    "[ScenarioExecutor] terminal ledger unavailable (%s), in-memory dispatch identity kept",
                    terminal.error_code,
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
                            **_metadata(request, cand, connection, response, permit.security_revision, pricing),
                        },
                    ),
                ),
                usages=usage_tuple,
            )

        # 重试耗尽 / 兼容失败：收敛错误提交终态（方案 §6.2 步骤 5），再切换候选
        await self._submit_failed_terminal(request, attempt_id, last_reason, reservation_id)
        return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())

    async def _submit_failed_terminal(
        self,
        request: InferenceRequest,
        attempt_id: str,
        error_code: str,
        reservation_id: str | None,
    ) -> None:
        """失败候选终态落账（§6.2 步骤 5「Executor 收敛错误并提交终态」）。

        用量缺失由 Recorder 标 unavailable（R21）；落账失败不阻断切换流程。
        F16：预留 ID 贯穿终态——非 None 时结算经 F22 预留转换 / F23 活跃预留
        背书补记收敛预留，不留悬挂占用；None（usage_only / 无预留）行为不变。
        """
        terminal = await self._recorder.submit_terminal(
            _terminal_record(
                request,
                attempt_id,
                usage=None,
                actual_model=None,
                status="failed",
                error_code=error_code,
                reservation_id=reservation_id,
            )
        )
        if not terminal.persisted:
            logger.info(
                "[ScenarioExecutor] failed-attempt terminal not persisted (%s)",
                terminal.error_code,
            )

    async def _backoff(self, error: Exception, request: InferenceRequest, clock: Callable[[], float]) -> None:
        """退避 1/2s 与 Retry-After 取大，clamp 至剩余 deadline（方案 D7）。"""
        retry_after = getattr(error, "retry_after", None)
        base = _RETRY_BACKOFF_BASE_S
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            base = max(base, float(retry_after))
        base = min(base, _RETRY_BACKOFF_MAX_S)
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
    ) -> dict[str, Any]:
        return {
            "task_id": str(cand.get("task_id", request.task_id)),
            "candidate_index": int(cand.get("candidate_index", 0)),
            "retry_index": 0,
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
    """

    status: str
    reason: str
    outputs: tuple[Mapping[str, Any], ...]
    usages: tuple[NormalizedLLMUsage, ...]
    decision_hint: Literal["skipped", "rejected", "attempted", "switched", "succeeded", "failed"] = "switched"


def _observation_from(
    request: InferenceRequest,
    cand: Mapping[str, Any],
    connection: ResolvedConnection,
    attempt_id: str,
    response: AdapterResponse,
    security_revision: int,
    pricing: ConnectionPricing | None = None,
) -> Any:
    from services.ai_service.usage_recorder import AttemptUsageObservation

    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id=request.invocation_id,
        usage=response.usage,
        actual_model=response.actual_model,
        metadata=_metadata(request, cand, connection, response, security_revision, pricing),
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
) -> Any:
    from services.ai_service.usage_recorder import AttemptTerminalRecord

    return AttemptTerminalRecord(
        attempt_id=attempt_id,
        invocation_id=request.invocation_id,
        status=status,
        usage=usage,
        error_code=error_code,
        duration_ms=None,
        reservation_id=reservation_id,
    )


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
    response: AdapterResponse,
    security_revision: int,
    pricing: ConnectionPricing | None = None,
) -> dict[str, Any]:
    now_cst = datetime.now(UTC) + _CST_OFFSET
    return {
        "connection_id": connection.connection_id,
        "connection_revision": connection.connection_revision,
        "adapter_kind": connection.adapter_kind,
        "configured_model": connection.model,
        "actual_model": response.actual_model,
        "scenario_id": request.scenario_id,
        "task_id": str(cand.get("task_id", request.task_id)),
        "trigger": str(cand.get("trigger", "manual")),
        "candidate_index": int(cand.get("candidate_index", 0)),
        "retry_index": int(cand.get("retry_index", 0)),
        # F01：许可线性化点的安全修订（permit），不再混用快照授权范围版本
        "security_revision": security_revision,
        "month": f"{now_cst.year}{now_cst.month:02d}",
        "run_id": request.run_id,
        # F17：连接计价冻结快照（mode / pricing_unit / 价格来源 / 单价 / 汇率 /
        # 生效时点，金额十进制字符串保精度）。成本不在此计算——由 Recorder 在
        # 终态落账时点按真实 usage 经 connection_pricing.compute_cost_cny 计算
        # （cost 与 usage 同源）；快照缺失 → None（未知语义留证，R21 不伪装）。
        "pricing": pricing_snapshot_metadata(pricing),
    }
