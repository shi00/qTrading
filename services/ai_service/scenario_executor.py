"""场景执行器（S3，ADR-0014 §6.1，模型统一.md §6.2，方案 D7）。

职责：按快照路由把一次推理请求编排为「许可 → 派发 → 适配器调用 → 校验 →
落账收敛」的候选遍历；不做配置读写（快照冻结语义由 §3.3 保证）、不直接
触账本（一律经 UsageRecorder）。

关键契约（方案 §6.2 固定执行流程）：
- **执行序**：冷却检查 → 连接并发许可（排队计入 deadline）→ mark_dispatched 绑定
  派发身份（不建 attempt 行）→ 逐次 authorize_attempt（每次尝试单独授权，不预发
  下一次重试的许可）→ begin_attempt 建立实际尝试记录 → 适配器执行；不在安全许可
  后再排队（§4.3）；
- **重试**：同候选默认 1 次、上限 2 次（构造可调），退避 1/2s 与适配器
  Retry-After 取大，并 clamp 至剩余 deadline；
- **D5 降级**：有限额费用型在账本不可用（mark_dispatched persisted=False）时
  禁止派发；usage_only 允许进程内一次性派发身份；失败候选收敛错误提交终态
  （§6.2 步骤 5）；
- **R2**：CancelledError 始终传播，不包装为失败。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
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
        for raw in snapshot.connections:
            try:
                resolved = _resolve_connection(raw)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "[ScenarioExecutor] snapshot connection malformed (%s): %s",
                    raw.get("connection_id", "?") if isinstance(raw, Mapping) else "?",
                    type(e).__name__,
                )
                continue
            connections_by_id[resolved.connection_id] = resolved
        decisions: list[CandidateDecision] = []

        if route is None:
            return self._terminal(request, "failed", (), decisions, ())

        candidates: tuple[Mapping[str, Any], ...] = route.get("candidates", ())  # type: ignore[assignment]
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

            attempt_id = uuid.uuid4().hex
            # 方案 §6.2 步骤 3：先取得连接并发许可（排队计入 deadline），再绑定派发，
            # 最后逐次 authorize_attempt——不在安全许可后再排队（步骤 4）
            semaphore = state.semaphores.setdefault(
                connection.connection_id, asyncio.Semaphore(max(1, connection.concurrency_limit))
            )
            async with semaphore:
                # 派发绑定（D5）：有限额费用型在账本不可用时禁止派发
                dispatch_outcome = await self._recorder.mark_dispatched(
                    invocation_id=request.invocation_id,
                    reservation_id=cand.get("reservation_id"),
                    owner_session_id=str(cand.get("owner_session_id", "")),
                    owner_epoch=int(cand.get("owner_epoch", 0)),
                )
                budget_amount = cand.get("budget_amount_cny")
                if not dispatch_outcome.persisted and budget_amount is not None:
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
                    request, snapshot, connection, cand, attempt_id, clock, validate_output
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
                return _CandidateOutcome(
                    status="failed",
                    decision_hint="rejected",
                    reason=f"authorize failed: {type(e).__name__}",
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
            # 落账失败降级继续执行（执行已授权开始，费用型此前已被 mark_dispatched 拦截）。
            begin_outcome = await self._recorder.begin_attempt(
                attempt_id=attempt_id,
                invocation_id=request.invocation_id,
                configured_model=connection.model,
                attempt_row=self._attempt_row(request, cand, connection, snapshot, permit),
            )
            if not begin_outcome.persisted:
                logger.warning(
                    "[ScenarioExecutor] attempt row unavailable (%s), continuing in-memory execution",
                    begin_outcome.error_code,
                )

            attempt_context = AttemptContext(
                owner_session_id=str(cand.get("owner_session_id", "")),
                owner_epoch=int(cand.get("owner_epoch", 0)),
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
            # security_scope_version 是授权范围版本，不得混作安全修订落账）
            self._recorder.observe_usage(
                _observation_from(request, cand, connection, attempt_id, response, permit.security_revision)
            )

            try:
                validated = validate_output(response)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._submit_failed_terminal(request, attempt_id, f"output validation failed: {type(e).__name__}")
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
                    dict(validated.output) if isinstance(validated.output, Mapping) else {"output": validated.output},
                ),
                usages=usage_tuple,
            )

        # 重试耗尽 / 兼容失败：收敛错误提交终态（方案 §6.2 步骤 5），再切换候选
        await self._submit_failed_terminal(request, attempt_id, last_reason)
        return _CandidateOutcome(status="failed", reason=last_reason, outputs=(), usages=())

    async def _submit_failed_terminal(
        self,
        request: InferenceRequest,
        attempt_id: str,
        error_code: str,
    ) -> None:
        """失败候选终态落账（§6.2 步骤 5「Executor 收敛错误并提交终态」）。

        用量缺失由 Recorder 标 unavailable（R21）；落账失败不阻断切换流程。
        """
        terminal = await self._recorder.submit_terminal(
            _terminal_record(
                request,
                attempt_id,
                usage=None,
                actual_model=None,
                status="failed",
                error_code=error_code,
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
    ) -> dict[str, Any]:
        return {
            "task_id": str(cand.get("task_id", request.task_id)),
            "candidate_index": int(cand.get("candidate_index", 0)),
            "retry_index": 0,
            "connection_id": connection.connection_id,
            "connection_revision": connection.connection_revision,
            "adapter_kind": connection.adapter_kind,
            "security_revision": getattr(permit, "security_revision", 0),
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


def _resolve_connection(raw: Mapping[str, Any]) -> ResolvedConnection:
    """快照连接定义（mapping，S2 快照契约）→ ResolvedConnection。

    必需字段（connection_id / connection_revision / adapter_kind / model）缺失即抛
    KeyError / ValueError（缺失不伪装，R21 邻域语义），由调用方降级 rejected。
    """
    adapter_kind = str(raw["adapter_kind"])
    if adapter_kind not in ("litellm_chat", "gguf_chat", "gguf_sentiment"):
        raise ValueError(f"unknown adapter_kind: {adapter_kind}")
    return ResolvedConnection(
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
) -> Any:
    from services.ai_service.usage_recorder import AttemptUsageObservation

    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id=request.invocation_id,
        usage=response.usage,
        actual_model=response.actual_model,
        metadata=_metadata(request, cand, connection, response, security_revision),
    )


def _terminal_record(
    request: InferenceRequest,
    attempt_id: str,
    *,
    usage: NormalizedLLMUsage | None,
    actual_model: str | None,
    status: str,
    error_code: str | None,
) -> Any:
    from services.ai_service.usage_recorder import AttemptTerminalRecord

    return AttemptTerminalRecord(
        attempt_id=attempt_id,
        invocation_id=request.invocation_id,
        status=status,
        usage=usage,
        error_code=error_code,
        duration_ms=None,
    )


def _metadata(
    request: InferenceRequest,
    cand: Mapping[str, Any],
    connection: ResolvedConnection,
    response: AdapterResponse,
    security_revision: int,
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
    }
