"""AI 用量落账 Recorder（S3，ADR-0014 §6.1/§10，方案 D6）。

职责：Executor 与账本 DAO 之间的唯一落账边界——
- ``observe_usage``：适配器返回前同步转移不可变用量快照，按 attempt_id 幂等；
- ``mark_dispatched`` / ``submit_terminal``：每次落账 ``wait_for`` 限时（默认 5s），
  失败/超时/引擎不可用一律降级为 ``persisted=False`` + 脱敏错误码，缺失不伪装（R21）；
- 60s 用量窗口惰性扫描（无后台 timer）：``submit_terminal`` 顺带关闭到期窗口，
  ``aclose`` 兜底全量 flush；
- F20：``submit_terminal`` 落账失败（persisted=False）不丢弃观察——可恢复失败
  （账本不可用 / 超时）的终态记录进入**有界内存重试队列**，挂靠后续成功落账
  与 ``aclose`` 重放（成功或确认不可恢复才移除，防双计由 DAO usage_revision
  水位兜底）；``aclose`` 停机前全量 flush（失败如实告警，R21 不伪装已结算）；
- ``dao_provider`` 惰性解析账本 DAO（engine 未就绪 → ``DB_UNAVAILABLE``）。

用量落账不阻塞推理主链路：所有 DAO 异常在本层收敛为 PersistenceOutcome。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from data.persistence.daos.base_dao import EngineDisposedError
from core.model_management_types import (
    BudgetReservation,
    NormalizedLLMUsage,
    PersistenceOutcome,
)
from services.ai_service.connection_pricing import compute_cost_cny, pricing_from_metadata

logger = logging.getLogger(__name__)

# 默认单次落账限时（秒）：账本不可用时快速降级，不拖垮推理链路
_DEFAULT_SETTLE_TIMEOUT_S = 5.0
# 用量确认窗口（秒）：终态落账后仍允许迟到修订推进的时间窗（§10）
_DEFAULT_WINDOW_SECONDS = 60.0

_DB_UNAVAILABLE = "DB_UNAVAILABLE"
_DB_SETTLE_TIMEOUT = "DB_SETTLE_TIMEOUT"
# 预留被拒（F16）：try_reserve 返回 None = 余额不足 **或** owner 失权（D2/D3+F23
# 语义统一按「不可派发」处置，DAO 层不可区分，错误码合并表达）。
_RESERVE_REJECTED = "RESERVE_REJECTED"

# F20：可恢复失败错误码（保留重放）。其余错误码确认为不可恢复——
# METADATA_INCOMPLETE：重放无新元数据来源（观察 / 记录不可变）；
# OWNER_LEASE_LOST：DAO 返回 False = 旧修订或已结算（事实已在账本）或缺行
# 无活跃预留背书（F23 确定性拒绝），重放结果不变。
_RETAINABLE_ERROR_CODES = frozenset({_DB_UNAVAILABLE, _DB_SETTLE_TIMEOUT})
# F20：结算失败重试队列上限（有界内存；溢出 FIFO 逐出最旧并如实告警，R21）
_MAX_PENDING_SETTLE = 64
# F20：单次挂靠重放上限（无后台 timer，D6；限制单次 submit_terminal 追加延迟）
_MAX_DRAIN_PER_SWEEP = 8
# F20：重放指数退避（2s 起 ×2 递增，上限 30s；防止账本抖动时高频无效重试）
_RETRY_BACKOFF_BASE_S = 2.0
_RETRY_BACKOFF_CAP_S = 30.0


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    """预算预留结果（F16）：封装 DAO ``try_reserve`` 的三态。

    - ``persisted=True`` + ``reservation`` 非 None：预留成功（月锁内原子登记）；
    - ``persisted=True`` + ``reservation=None``（``error_code=RESERVE_REJECTED``）：
      预算门拒绝（余额不足）或 owner 失权（F23 rowcount 贯穿检查），调用方
      统一按「不可派发」处置（D5：费用型零出站）；
    - ``persisted=False``：账本不可用 / 超时（脱敏错误码），费用型同样零出站。
    """

    persisted: bool
    error_code: str | None = None
    reservation: BudgetReservation | None = None


@dataclass(frozen=True, slots=True)
class AttemptUsageObservation:
    """一次尝试的用量观察（适配器返回前同步转移的不可变快照）。"""

    attempt_id: str
    invocation_id: str
    usage: NormalizedLLMUsage | None
    actual_model: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AttemptTerminalRecord:
    """尝试终态记录：由 Executor 收敛提交（§6.1）。

    ``reservation_id``（F16）：该尝试绑定的预算预留 ID——非 None 时 DAO 结算
    同事务将预留转换为 ``settled``（F22 预留转换），且 attempt 行缺失时的
    补记以该活跃预留为背书（F23 fencing）；None = 无预留（usage_only 降级 /
    预留未建立），结算行为与既有完全一致。

    ``metadata``（F18）：无用量观察（obs 缺失）终态的落账元数据回退来源——
    适配器异常 / 授权失败等失败路径没有 ``observe_usage`` 先行，终态需自带
    归属月 / retry_index / 连接身份等结算参数，否则被 METADATA_INCOMPLETE
    守卫阻断，该次尝试的失败事实与预留收敛不可达账本。有观察时观察元数据
    仍为权威（本字段内容与其同源一致）。
    """

    attempt_id: str
    invocation_id: str
    status: str
    usage: NormalizedLLMUsage | None
    error_code: str | None
    duration_ms: float | None
    reservation_id: str | None = None
    actual_model: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _PendingSettle:
    """F20 结算失败保留条目：原始终态记录 + 重试退避状态（不可变快照）。

    ``record`` 保留 executor 提交的原始终态（含 reservation_id），重放语义与
    首次提交完全一致；``attempts`` 驱动指数退避；``next_retry_at`` 为下次重放
    的单调时刻（重放仅处理到期条目）。
    """

    record: AttemptTerminalRecord
    attempts: int
    next_retry_at: float


class UsageRecorder:
    """用量落账边界（DI 单实例；owner Task 强引用由 Executor 侧持有）。"""

    def __init__(
        self,
        *,
        dao_provider: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
        settle_timeout_s: float = _DEFAULT_SETTLE_TIMEOUT_S,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        max_pending_settle: int = _MAX_PENDING_SETTLE,
    ) -> None:
        self._dao_provider = dao_provider
        self._clock = clock
        self._settle_timeout_s = settle_timeout_s
        self._window_seconds = window_seconds
        self._max_pending_settle = max_pending_settle
        # attempt_id → 不可变用量快照（幂等：首次 observe 生效）
        self._observations: dict[str, AttemptUsageObservation] = {}
        # attempt_id → 窗口到期单调时刻（惰性扫描用）
        self._windows: dict[str, float] = {}
        # F20：结算失败保留队列（插入序 = 首次失败序，FIFO 逐出最旧）
        self._pending_settle: OrderedDict[str, _PendingSettle] = OrderedDict()
        self._closed = False

    # ------------------------------------------------------------------
    # 用量观察（D6：同步转移、按 attempt_id 幂等）
    # ------------------------------------------------------------------

    def observe_usage(self, observation: AttemptUsageObservation) -> None:
        """适配器返回前同步持有用量快照；同 attempt_id 首次观察生效。"""
        if observation.attempt_id in self._observations:
            return
        # 浅拷贝 metadata：转移后调用方修改原 mapping 不影响已观察数据
        self._observations[observation.attempt_id] = AttemptUsageObservation(
            attempt_id=observation.attempt_id,
            invocation_id=observation.invocation_id,
            usage=observation.usage,
            actual_model=observation.actual_model,
            metadata=dict(observation.metadata),
        )
        self._windows[observation.attempt_id] = self._clock() + self._window_seconds

    def pending_observation_count(self) -> int:
        return len(self._observations)

    def observed_usage(self, attempt_id: str) -> dict[str, Any] | None:
        obs = self._observations.get(attempt_id)
        if obs is None:
            return None
        # 平铺视图：metadata 键与标准字段同层，便于调用方直读
        view: dict[str, Any] = {
            "attempt_id": obs.attempt_id,
            "invocation_id": obs.invocation_id,
            "usage": obs.usage,
            "actual_model": obs.actual_model,
        }
        view.update(obs.metadata)
        view["metadata"] = dict(obs.metadata)
        return view

    # ------------------------------------------------------------------
    # 父请求建立与预算预留（F16：场景链父子账目入口）
    # ------------------------------------------------------------------

    async def ensure_invocation(
        self,
        *,
        invocation_id: str,
        scenario_id: str,
        trigger: str,
        owner_session_id: str,
        owner_epoch: int,
        owner_lease_expires_at: Any,
        budget_policy_snapshot: dict[str, Any] | None,
    ) -> PersistenceOutcome:
        """建立 / 幂等复用父请求行（F16，先于候选遍历——零尝试请求亦可查）。

        账本不可用 / 超时收敛为 ``persisted=False``（不伪装已建立，R21）；
        费用型候选随后的预算预留同样失败 → 零派发（D5），语义自洽。
        """

        async def _run() -> bool:
            dao = self._dao_provider()
            if dao is None:
                raise _DaoUnavailable()
            return bool(
                await dao.create_invocation(
                    invocation_id=invocation_id,
                    scenario_id=scenario_id,
                    trigger=trigger,
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                    owner_lease_expires_at=owner_lease_expires_at,
                    budget_policy_snapshot=budget_policy_snapshot,
                )
            )

        return await self._guarded_outcome(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)

    async def try_reserve(
        self,
        *,
        invocation_id: str,
        scenario_id: str,
        task_id: str,
        trigger: str,
        month: str,
        reservation_id: str,
        attempt_id: str | None,
        candidate_index: int,
        retry_index: int,
        amount_cny: str | None,
        budget_limit_cny: str | None,
        budget_policy_snapshot: dict[str, Any] | None,
        owner_session_id: str,
        owner_epoch: int,
        owner_lease_expires_at: Any,
    ) -> ReservationOutcome:
        """逐实际尝试原子预算预留（F16，D3 月锁内原子）。

        ``amount_cny`` 非 None 且 ``budget_limit_cny`` 非 None 时执行预算门
        判定（已结算 + 在途 + 本次 > 上限 → 拒绝）；两者均 None（usage_only）
        时仅登记 NULL 金额预留（不占预算）。返回 :class:`ReservationOutcome`
        三态（见其 docstring），调用方按费用型 / usage_only 各自语义处置。
        """

        async def _run() -> BudgetReservation | None:
            dao = self._dao_provider()
            if dao is None:
                raise _DaoUnavailable()
            return await dao.try_reserve(
                invocation_id=invocation_id,
                scenario_id=scenario_id,
                task_id=task_id,
                trigger=trigger,
                month=month,
                reservation_id=reservation_id,
                attempt_id=attempt_id,
                candidate_index=candidate_index,
                retry_index=retry_index,
                amount_cny=amount_cny,
                budget_limit_cny=budget_limit_cny,
                budget_policy_snapshot=budget_policy_snapshot,
                owner_session_id=owner_session_id,
                owner_epoch=owner_epoch,
                owner_lease_expires_at=owner_lease_expires_at,
                lease_expires_at=owner_lease_expires_at,
            )

        result = await self._guarded_run(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)
        if isinstance(result, _GuardedFailure):
            return ReservationOutcome(persisted=False, error_code=result.error_code)
        if result is None:
            # F23：None = 余额不足或 owner 失权（不返回非持久预留），统一「不可派发」
            return ReservationOutcome(persisted=True, error_code=_RESERVE_REJECTED, reservation=None)
        return ReservationOutcome(persisted=True, error_code=None, reservation=result)

    # ------------------------------------------------------------------
    # 派发绑定（D4 owner 条件写入的落账包装）
    # ------------------------------------------------------------------

    async def mark_dispatched(
        self,
        *,
        invocation_id: str,
        reservation_id: str | None,
        owner_session_id: str,
        owner_epoch: int,
    ) -> PersistenceOutcome:
        """绑定派发（预留 reserved→dispatched）：账本不可用/失权均不伪装成功
        （D5 调用方据 persisted 处置）。不建立实际尝试记录（方案 §6.2 步骤 3）。"""

        async def _run() -> bool:
            dao = self._dao_provider()
            if dao is None:
                raise _DaoUnavailable()
            return bool(
                await dao.mark_dispatched(
                    invocation_id=invocation_id,
                    reservation_id=reservation_id,
                    owner_session_id=owner_session_id,
                    owner_epoch=owner_epoch,
                )
            )

        return await self._guarded_outcome(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)

    async def begin_attempt(
        self,
        *,
        attempt_id: str,
        invocation_id: str,
        configured_model: str,
        attempt_row: dict[str, Any] | None = None,
        owner_session_id: str | None = None,
        owner_epoch: int | None = None,
    ) -> PersistenceOutcome:
        """建立实际尝试记录（方案 §6.2 步骤 4：真正进入适配器执行时调用）。

        幂等（ON CONFLICT DO NOTHING）：同一次写账的持久化重放沿用已绑定身份；
        外部真实重试（新的实际调用）由调用方生成新 attempt 身份（F18），
        不共用。
        ``owner_session_id`` / ``owner_epoch`` 齐传启用 F23 owner 身份强门
        （失权 owner 不得建立新的 attempt 事实）；失败降级 persisted=False
        （执行已授权开始，落账尽力而为，不阻断推理）。
        """

        async def _run() -> bool:
            dao = self._dao_provider()
            if dao is None:
                raise _DaoUnavailable()
            await dao.insert_attempt(
                attempt_id=attempt_id,
                invocation_id=invocation_id,
                configured_model=configured_model,
                attempt_row=attempt_row,
                owner_session_id=owner_session_id,
                owner_epoch=owner_epoch,
            )
            return True

        return await self._guarded_outcome(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)

    # ------------------------------------------------------------------
    # 终态落账（§10：迟到差额幂等整体推进由 DAO 保证）
    # ------------------------------------------------------------------

    async def submit_terminal(self, record: AttemptTerminalRecord) -> PersistenceOutcome:
        """终态落账：usage 缺失标 unavailable（R21），顺带惰性扫描到期窗口。

        F17：成本在此计算——usage 确定后按 metadata 携带的连接计价冻结快照
        （executor 连接解析点冻结）经 ``connection_pricing.compute_cost_cny``
        以 Decimal 计算，cost 与 usage 同源（观察时点计价会在重试场景与成功
        attempt 的用量失配）。不可计价（usage_only / 配置缺失 / 单价或用量
        未知）→ ``cost_cny=None``（未知语义直通，R21 禁止填 0 伪装）；唯一
        合法零 = 价格事实为 0（免费，已知零）。

        F20：落账失败（persisted=False）不丢弃观察——可恢复失败（账本不可用 /
        超时）进入有界重试队列待重放；不可恢复失败（METADATA_INCOMPLETE /
        OWNER_LEASE_LOST）确认丢弃并如实告警。返回值语义不变（R21 诚实）。
        """
        outcome = await self._settle_record(record)
        if outcome.persisted:
            self._observations.pop(record.attempt_id, None)
            # F20：同 attempt 迟到重提交成功（如 F19 executor 幂等重提交）→
            # 保留条目一并清除（事实已落账，无需重放）
            self._pending_settle.pop(record.attempt_id, None)
        elif outcome.error_code in _RETAINABLE_ERROR_CODES:
            self._retain(record)
        else:
            # F20：不可恢复失败确认丢弃（原实现无条件 pop 观察即丢数据）——
            # 重试无新事实来源（METADATA_INCOMPLETE）或事实已在账本（
            # OWNER_LEASE_LOST = DAO False = 旧修订或已结算 / 无背书确定性拒绝）
            self._observations.pop(record.attempt_id, None)
            logger.warning(
                "[UsageRecorder] terminal settle not recoverable (%s), dropping observation attempt=%s...",
                outcome.error_code,
                record.attempt_id[:8],
            )
        # 终态进入用量确认窗口（未 observe 的直接登记），随后惰性扫描到期项
        self._windows.setdefault(record.attempt_id, self._clock() + self._window_seconds)
        # F20：本次落账成功 = 账本刚证明可达，顺带重放历史失败条目（无后台 timer，D6）
        if outcome.persisted and self._pending_settle:
            await self._drain_pending_settle()
        await self._scan_expired_windows()
        return outcome

    async def _settle_record(self, record: AttemptTerminalRecord) -> PersistenceOutcome:
        """单条终态结算（F20 自 submit_terminal 提取，供失败重放 / 停机 flush 复用）。

        无副作用：不触碰观察缓冲 / 窗口登记 / 重试队列，仅执行 DAO 结算调用并
        收敛结果；观察元数据仍为权威来源。
        """
        obs = self._observations.get(record.attempt_id)
        usage = record.usage if record.usage is not None else (obs.usage if obs else None)
        if obs is not None:
            # 观察元数据为权威（适配器返回前同步转移的不可变快照）
            actual_model = obs.actual_model
            metadata = dict(obs.metadata)
        else:
            # F18：无观察终态（适配器异常 / 授权失败等失败路径）回退记录自带
            # 元数据——失败事实与预留收敛仍可达账本，不再被 METADATA_INCOMPLETE
            # 阻断（字段缺省 None 时行为与既有一致：仍按缺失拦截）
            actual_model = record.actual_model
            metadata = dict(record.metadata) if record.metadata is not None else {}
        # 缺失不伪装：无用量即 unavailable，禁止填 0（R21）
        usage_status = "reported" if usage is not None else "unavailable"
        # F17：按冻结计价快照 × 真实 usage 计算成本；无用量 → 不可计价（None）
        cost_cny = (
            compute_cost_cny(
                pricing_from_metadata(metadata.get("pricing")),
                usage,
                encoder_input_tokens=metadata.get("encoder_input_tokens"),
            )
            if usage is not None
            else None
        )
        # 归属月缺失 = observe 未先行（编程契约违规），不进账本也不误报 DB 超时
        if not metadata.get("month"):
            logger.warning(
                "[UsageRecorder] submit_terminal without observation metadata (attempt=%s...)",
                record.attempt_id[:8],
            )
            return PersistenceOutcome(persisted=False, error_code="METADATA_INCOMPLETE")

        async def _run() -> bool:
            dao = self._dao_provider()
            if dao is None:
                raise _DaoUnavailable()
            return bool(
                await dao.settle_attempt_usage(
                    attempt_id=record.attempt_id,
                    invocation_id=record.invocation_id,
                    status=record.status,
                    usage_status=usage_status,
                    usage=usage,
                    usage_revision=1,
                    error_code=record.error_code,
                    duration_ms=record.duration_ms,
                    cost_cny=cost_cny,
                    actual_model=actual_model,
                    connection_id=metadata.get("connection_id", ""),
                    connection_revision=int(metadata.get("connection_revision", 0)),
                    adapter_kind=metadata.get("adapter_kind", "litellm_chat"),
                    configured_model=metadata.get("configured_model", actual_model or ""),
                    scenario_id=metadata.get("scenario_id", ""),
                    task_id=metadata.get("task_id", ""),
                    trigger=metadata.get("trigger", "manual"),
                    candidate_index=int(metadata.get("candidate_index", 0)),
                    retry_index=int(metadata.get("retry_index", 0)),
                    security_revision=int(metadata.get("security_revision", 0)),
                    month=metadata.get("month", ""),
                    encoder_input_tokens=metadata.get("encoder_input_tokens"),
                    text_count=metadata.get("text_count"),
                    usage_window_expires_at=metadata.get("usage_window_expires_at"),
                    usage_fingerprint=metadata.get("usage_fingerprint"),
                    reservation_id=record.reservation_id,
                )
            )

        return await self._guarded_outcome(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)

    def _retain(self, record: AttemptTerminalRecord) -> None:
        """F20 结算失败条目入有限重试队列（溢出 FIFO 逐出最旧并如实告警，R21）。

        同 attempt 重复失败（如 F19 幂等重提交同样失败）沿用既有条目的失败
        次数推进退避，不重复占位。
        """
        existing = self._pending_settle.get(record.attempt_id)
        attempts = 1 if existing is None else existing.attempts + 1
        if existing is None:
            while self._pending_settle and len(self._pending_settle) >= self._max_pending_settle:
                evicted_id, _evicted = self._pending_settle.popitem(last=False)
                self._observations.pop(evicted_id, None)
                logger.warning(
                    "[UsageRecorder] settle retry queue full (%d): dropping oldest unresolved usage fact attempt=%s...",
                    self._max_pending_settle,
                    evicted_id[:8],
                )
        backoff = min(_RETRY_BACKOFF_BASE_S * (2 ** (attempts - 1)), _RETRY_BACKOFF_CAP_S)
        self._pending_settle[record.attempt_id] = _PendingSettle(
            record=record,
            attempts=attempts,
            next_retry_at=self._clock() + backoff,
        )

    async def _drain_pending_settle(self) -> None:
        """F20 顺带重放：重试队列中退避到期的条目逐条重试（无后台 timer，D6）。

        取消安全（R2）：不预先移除条目——CancelledError 中途到达时未处理条目
        保留在队列；并发 / 重复重放由 DAO usage_revision 水位幂等兜底（F19 防双计）。
        """
        # NOTE(lazy): 重放挂靠成功落账而非独立后台任务（与 D6 无后台 timer 惯例一致）.
        # ceiling: 单轮 _MAX_DRAIN_PER_SWEEP 条 × settle_timeout_s，且仅在当前落账刚成功的时刻发生；
        # 当前记录先于重放落定，取消仅中断重放（条目保留）.
        # upgrade: 实测落账延迟影响推理链路 P95 或取消清理超时率上升时，改为独立后台任务重放.
        now = self._clock()
        due = [aid for aid, pending in self._pending_settle.items() if pending.next_retry_at <= now]
        for attempt_id in due[:_MAX_DRAIN_PER_SWEEP]:
            pending = self._pending_settle.get(attempt_id)
            if pending is None:
                continue
            outcome = await self._settle_record(pending.record)
            if outcome.persisted:
                self._pending_settle.pop(attempt_id, None)
                self._observations.pop(attempt_id, None)
            else:
                self._retain(pending.record)

    async def _scan_expired_windows(self, *, force: bool = False) -> None:
        """惰性扫描：关闭到期用量窗口（无后台 timer，D6）。

        ``force=True``（aclose 停机兜底）：关闭全部剩余窗口——停机后无迟到修订
        来源，窗口登记随内存清空，不收口将永久滞留为未关闭。
        """
        dao = self._dao_provider()
        if dao is None:
            return
        now = self._clock()
        if force:
            expired = list(self._windows)
        else:
            expired = [aid for aid, expires_at in self._windows.items() if expires_at <= now]
        for attempt_id in expired:
            self._windows.pop(attempt_id, None)
            try:
                await asyncio.wait_for(dao.close_usage_window(attempt_id=attempt_id), timeout=self._settle_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[UsageRecorder] close_usage_window failed: %s", type(e).__name__)

    # ------------------------------------------------------------------
    # 关闭兜底（D6：aclose 幂等；F20 停机前全量 flush）
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """停机兜底（F20）：全量 flush 未落账事实后再清理缓冲（幂等；尽力而为）。

        失败如实告警不吞没（R21——不伪装成已结算，未落账事实随清理释放前留痕）；
        CancelledError 传播不吞（R2——cleanup 回调 5s 预算取消时，未落账条目保留
        在内存缓冲，不静默销毁；二次 aclose 幂等分支清理）。
        """
        if self._closed:
            self._observations.clear()
            self._pending_settle.clear()
            self._windows.clear()
            return
        self._closed = True
        await self._flush_before_close()
        self._observations.clear()
        self._pending_settle.clear()
        self._windows.clear()

    async def _flush_before_close(self) -> None:
        """停机前全量 flush（F20），顺序：

        ① 重放保留队列——原始终态记录（status / reservation_id 语义最完整）；
        ② 从未提交终态的观察合成 ``cancelled`` 终态补交——用量已观察 = 消费事实，
          停机中断 = 未完成事实，均非伪装（R21）；无预留转换（reservation_id
          不可知，预留经租约到期退出）；
        ③ 强制关闭全部剩余用量窗口（停机后无迟到修订来源）。
        """
        for attempt_id, pending in list(self._pending_settle.items()):
            outcome = await self._settle_record(pending.record)
            if outcome.persisted:
                self._pending_settle.pop(attempt_id, None)
                self._observations.pop(attempt_id, None)
            else:
                logger.warning(
                    "[UsageRecorder] aclose flush: retained settle still failed (%s), "
                    "usage fact dropped at shutdown attempt=%s...",
                    outcome.error_code,
                    attempt_id[:8],
                )
        for attempt_id, obs in list(self._observations.items()):
            if attempt_id in self._pending_settle:
                # ① 已对原始终态记录尽力重放且失败——不覆盖为合成 cancelled 二次结算
                continue
            if not obs.metadata.get("month"):
                logger.warning(
                    "[UsageRecorder] aclose flush: observation without month metadata, "
                    "usage fact dropped at shutdown attempt=%s...",
                    attempt_id[:8],
                )
                continue
            record = AttemptTerminalRecord(
                attempt_id=obs.attempt_id,
                invocation_id=obs.invocation_id,
                status="cancelled",
                usage=obs.usage,
                error_code=None,
                duration_ms=None,
                actual_model=obs.actual_model,
                metadata=dict(obs.metadata),
            )
            outcome = await self._settle_record(record)
            if outcome.persisted:
                self._observations.pop(attempt_id, None)
            else:
                logger.warning(
                    "[UsageRecorder] aclose flush: observation settle failed (%s), "
                    "usage fact dropped at shutdown attempt=%s...",
                    outcome.error_code,
                    attempt_id[:8],
                )
        await self._scan_expired_windows(force=True)

    # ------------------------------------------------------------------
    # 内部：受限落账 + 结果收敛
    # ------------------------------------------------------------------

    async def _guarded_run(self, coro_factory: Callable[[], Any], *, db_timeout_code: str) -> Any:
        """限时执行 DAO 调用：成功返回原始值，失败返回 :class:`_GuardedFailure` 哨兵
        （异常表单一来源，:meth:`_guarded_outcome` 与 :meth:`try_reserve` 共用）。"""
        try:
            return await asyncio.wait_for(coro_factory(), timeout=self._settle_timeout_s)
        except asyncio.CancelledError:
            raise
        except _DaoUnavailable:
            return _GuardedFailure(_DB_UNAVAILABLE)
        except EngineDisposedError:
            return _GuardedFailure(_DB_UNAVAILABLE)
        except TimeoutError:
            return _GuardedFailure(db_timeout_code)
        except Exception as e:
            logger.warning("[UsageRecorder] ledger write failed: %s", type(e).__name__)
            return _GuardedFailure(db_timeout_code)

    async def _guarded_outcome(self, coro_factory: Callable[[], Any], *, db_timeout_code: str) -> PersistenceOutcome:
        """限时执行 DAO 调用，异常/超时/不可用统一收敛为 PersistenceOutcome。"""
        result = await self._guarded_run(coro_factory, db_timeout_code=db_timeout_code)
        if isinstance(result, _GuardedFailure):
            return PersistenceOutcome(persisted=False, error_code=result.error_code)
        return PersistenceOutcome(persisted=bool(result), error_code=None if result else "OWNER_LEASE_LOST")


class _GuardedFailure:
    """受限落账失败哨兵（内部）：携带脱敏错误码，与 DAO 成功返回值显式区分。"""

    __slots__ = ("error_code",)

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code


class _DaoUnavailable(RuntimeError):
    """dao_provider 返回 None（engine 未就绪 / 账本 DAO 未组装）。"""
