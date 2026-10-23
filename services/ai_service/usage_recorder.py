"""AI 用量落账 Recorder（S3，ADR-0014 §6.1/§10，方案 D6）。

职责：Executor 与账本 DAO 之间的唯一落账边界——
- ``observe_usage``：适配器返回前同步转移不可变用量快照，按 attempt_id 幂等；
- ``mark_dispatched`` / ``submit_terminal``：每次落账 ``wait_for`` 限时（默认 5s），
  失败/超时/引擎不可用一律降级为 ``persisted=False`` + 脱敏错误码，缺失不伪装（R21）；
- 60s 用量窗口惰性扫描（无后台 timer）：``submit_terminal`` 顺带关闭到期窗口，
  ``aclose`` 兜底全量 flush；
- ``dao_provider`` 惰性解析账本 DAO（engine 未就绪 → ``DB_UNAVAILABLE``）。

用量落账不阻塞推理主链路：所有 DAO 异常在本层收敛为 PersistenceOutcome。
"""

from __future__ import annotations

import asyncio
import logging
import time
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
    """

    attempt_id: str
    invocation_id: str
    status: str
    usage: NormalizedLLMUsage | None
    error_code: str | None
    duration_ms: float | None
    reservation_id: str | None = None


class UsageRecorder:
    """用量落账边界（DI 单实例；owner Task 强引用由 Executor 侧持有）。"""

    def __init__(
        self,
        *,
        dao_provider: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
        settle_timeout_s: float = _DEFAULT_SETTLE_TIMEOUT_S,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._dao_provider = dao_provider
        self._clock = clock
        self._settle_timeout_s = settle_timeout_s
        self._window_seconds = window_seconds
        # attempt_id → 不可变用量快照（幂等：首次 observe 生效）
        self._observations: dict[str, AttemptUsageObservation] = {}
        # attempt_id → 窗口到期单调时刻（惰性扫描用）
        self._windows: dict[str, float] = {}
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

        幂等（ON CONFLICT DO NOTHING）：同 attempt_id 重试沿用已绑定身份。
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
        """
        obs = self._observations.get(record.attempt_id)
        usage = record.usage if record.usage is not None else (obs.usage if obs else None)
        actual_model = obs.actual_model if obs is not None else None
        metadata = dict(obs.metadata) if obs is not None else {}
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

        outcome = await self._guarded_outcome(_run, db_timeout_code=_DB_SETTLE_TIMEOUT)
        # 终态进入用量确认窗口（未 observe 的直接登记），随后惰性扫描到期项
        self._windows.setdefault(record.attempt_id, self._clock() + self._window_seconds)
        await self._scan_expired_windows()
        self._observations.pop(record.attempt_id, None)
        return outcome

    async def _scan_expired_windows(self) -> None:
        """惰性扫描：关闭到期用量窗口（无后台 timer，D6）。"""
        dao = self._dao_provider()
        if dao is None:
            return
        now = self._clock()
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
    # 关闭兜底（D6：aclose 幂等）
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """停机兜底：清空观察缓冲与窗口登记（幂等；不抛账本异常）。"""
        if self._closed:
            self._observations.clear()
            self._windows.clear()
            return
        self._closed = True
        await self._scan_expired_windows()
        self._observations.clear()
        self._windows.clear()

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
