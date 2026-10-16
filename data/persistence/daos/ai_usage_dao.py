"""AI 用量账本 DAO（S3，ADR-0014 §9.2/§10，模型统一.md 三表账本）。

锁与一致性契约（方案 D2~D4）：
- **advisory 月锁**：``pg_advisory_xact_lock(NAMESPACE, YYYYMM)`` 两键，事务级；
  归属月 = Asia/Shanghai 下 invocation 开始月；
- **锁序**：月锁 → invocation（UPSERT 锁定行）→ attempt → 月汇总，同类按主键序；
- **owner 条件写入**：所有变更带 owner_session_id / owner_epoch /
  owner_lease_expires_at > (now() AT TIME ZONE 'UTC') 三条件，rowcount=0 即失权
  （时间列统一 naive UTC 墙钟，now() 经 AT TIME ZONE 'UTC' 归一，与 session 时区无关）；
- **迟到差额幂等**：attempt 用量按 usage_revision 单调推进（旧修订不覆盖新修订），
  差额整体计入月汇总，月汇总 revision 用 GREATEST 保持单调（R22）；
- 无 FK 约束（D1），三表逻辑关联。

全部 SQL 经 ``sa.text()`` 参数化（R4）；所有公开方法入口经 ``_guarded_begin``
（R5 引擎状态守卫 + 维护事件复查）。
"""

import datetime
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from data.persistence.daos.base_dao import BaseDao
from core.model_management_types import (
    BudgetReservation,
    NormalizedLLMUsage,
)
from utils.log_decorators import PerfThreshold, log_async_operation

# 预留有效状态（D3）：预算占用口径
_ACTIVE_RESERVATION_STATES: Final[tuple[str, ...]] = ("reserved", "dispatched", "held_unconfirmed")

# advisory 锁固定命名空间（"AIUS" ASCII 编码低 32 位，全局唯一语义，勿改）
_ADVISORY_LOCK_NAMESPACE: Final[int] = 0x4149_5553

# legacy 成本 app_state key 前缀（S2 之前 AIUsageTracker 的月累计，单位：分）
_LEGACY_COST_KEY_PREFIX: Final[str] = "ai_cost:"

# 月汇总两种 scope（§10）：不相加、不混算
SCOPE_SCENARIO: Final[str] = "scenario"
SCOPE_CONNECTION: Final[str] = "connection"


def parse_legacy_cents(raw: Any) -> Decimal:
    """legacy app_state ``ai_cost:{YYYYMM}`` 值（分）→ 元 Decimal（D3）。

    非/坏值一律 0（legacy 汇总缺失不阻断预留，也不伪装为已知成本）。
    """
    if raw is None:
        return Decimal("0")
    try:
        return (Decimal(str(raw)) / Decimal(100)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, ArithmeticError):
        return Decimal("0")


def month_to_lock_key(month: str) -> int:
    """``"202610"`` → int 锁键（YYYYMM 数值）。"""
    return int(month)


def _reservation_to_jsonb(r: BudgetReservation) -> dict[str, Any]:
    return {
        "reservation_id": r.reservation_id,
        "task_id": r.task_id,
        "candidate_index": r.candidate_index,
        "retry_index": r.retry_index,
        "amount_cny": r.amount_cny,
        "state": r.state,
        "attempt_id": r.attempt_id,
        "owner_session_id": r.owner_session_id,
        "owner_epoch": r.owner_epoch,
    }


class AIUsageDao(BaseDao):
    """三表账本访问对象：ai_invocation / ai_attempt / ai_usage_monthly。"""

    # ------------------------------------------------------------------
    # 预算预留（D3，§9.2 try_reserve）
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
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
        owner_lease_expires_at: datetime.datetime,
        lease_expires_at: datetime.datetime,
    ) -> BudgetReservation | None:
        """预留预算：余额足够才追加唯一 reservation_id，返回 None = 余额不足。

        usage_only（amount_cny=None）不占预算，直接登记预留（金额 NULL）。
        """
        self._check_engine(context="ai_usage.try_reserve")
        async with self._guarded_begin() as conn:
            # 1) advisory 月锁（D2：两键，事务级）
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :lock_key)"),
                {"ns": _ADVISORY_LOCK_NAMESPACE, "lock_key": month_to_lock_key(month)},
            )

            # 2) invocation 行：不存在则建立（owner 身份写入），存在则锁定
            #    （INSERT ... ON CONFLICT DO UPDATE 回读等价 FOR UPDATE 语义）
            await conn.execute(
                sa.text(
                    """
                    INSERT INTO ai_invocation (
                        invocation_id, scenario_id, trigger, budget_policy_snapshot,
                        owner_session_id, owner_epoch, owner_lease_expires_at
                    ) VALUES (
                        :invocation_id, :scenario_id, :trigger,
                        CAST(:budget_policy_snapshot AS jsonb),
                        :owner_session_id, :owner_epoch, :owner_lease_expires_at
                    )
                    ON CONFLICT (invocation_id) DO UPDATE SET
                        updated_at = (now() AT TIME ZONE 'UTC')
                    """
                ),
                {
                    "invocation_id": invocation_id,
                    "scenario_id": scenario_id,
                    "trigger": trigger,
                    "budget_policy_snapshot": (
                        json.dumps(budget_policy_snapshot, ensure_ascii=False) if budget_policy_snapshot else None
                    ),
                    "owner_session_id": owner_session_id,
                    "owner_epoch": owner_epoch,
                    "owner_lease_expires_at": owner_lease_expires_at,
                },
            )

            if amount_cny is not None and budget_limit_cny is not None:
                used = await self._read_month_committed_usage(conn, month, scenario_id)
                in_flight = await self._read_active_reservations_sum(conn, month)
                if used + in_flight + Decimal(amount_cny) > Decimal(budget_limit_cny):
                    return None

            # 3) 追加预留（jsonb || 追加，唯一 reservation_id 由调用方保证）
            reservation = BudgetReservation(
                reservation_id=reservation_id,
                task_id=task_id,
                candidate_index=candidate_index,
                retry_index=retry_index,
                amount_cny=str(amount_cny) if amount_cny is not None else None,
                state="reserved",
                attempt_id=attempt_id,
                owner_session_id=owner_session_id,
                owner_epoch=owner_epoch,
            )
            await conn.execute(
                sa.text(
                    """
                    UPDATE ai_invocation
                    SET reservations = reservations || CAST(:reservation AS jsonb),
                        updated_at = (now() AT TIME ZONE 'UTC')
                    WHERE invocation_id = :invocation_id
                      AND owner_session_id = :owner_session_id
                      AND owner_epoch = :owner_epoch
                      AND owner_lease_expires_at > (now() AT TIME ZONE 'UTC')
                    """
                ),
                {
                    "reservation": json.dumps(_reservation_to_jsonb(reservation), ensure_ascii=False),
                    "invocation_id": invocation_id,
                    "owner_session_id": owner_session_id,
                    "owner_epoch": owner_epoch,
                },
            )
            return reservation

    async def _read_month_committed_usage(self, conn: Any, month: str, scenario_id: str) -> Decimal:
        """该月 scenario scope 已结算成本 + legacy 汇总（D3）。"""
        row = (
            await conn.execute(
                sa.text(
                    """
                    SELECT COALESCE(SUM(cost_cny), 0)
                    FROM ai_usage_monthly
                    WHERE month = :month
                      AND aggregation_scope = :scope
                      AND grouping_key = :grouping_key
                    """
                ),
                {"month": month, "scope": SCOPE_SCENARIO, "grouping_key": scenario_id},
            )
        ).fetchone()
        settled = Decimal("0")
        if row is not None and row[0] is not None:
            try:
                settled = Decimal(str(row[0]))
            except (InvalidOperation, ValueError, ArithmeticError):
                settled = Decimal("0")

        legacy_row = (
            # 0015 迁移后 app_state 列名为 config_key / config_value（R17 保留字重命名）
            await conn.execute(
                sa.text("SELECT config_value FROM app_state WHERE config_key = :key"),
                {"key": f"{_LEGACY_COST_KEY_PREFIX}{month}"},
            )
        ).fetchone()
        legacy = parse_legacy_cents(legacy_row[0]) if legacy_row is not None else Decimal("0")
        return settled + legacy

    async def _read_active_reservations_sum(self, conn: Any, month: str) -> Decimal:
        """该月归属、租约未过期的有效预留金额合计（D3）。

        归属月按 created_at（Asia/Shanghai 月界）判定，与 D2 归属月口径一致。

        created_at 为 naive UTC 墙钟，故月界先按 Asia/Shanghai 定位再折算为 naive UTC。
        """
        tz = ZoneInfo("Asia/Shanghai")
        year, mon = int(month[:4]), int(month[4:6])
        month_start = datetime.datetime(year, mon, 1, tzinfo=tz).astimezone(datetime.UTC).replace(tzinfo=None)
        next_year, next_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
        next_month = datetime.datetime(next_year, next_mon, 1, tzinfo=tz).astimezone(datetime.UTC).replace(tzinfo=None)
        row = (
            await conn.execute(
                sa.text(
                    """
                    SELECT COALESCE(SUM((elem->>'amount_cny')::numeric), 0)
                    FROM ai_invocation, jsonb_array_elements(reservations) AS elem
                    WHERE owner_lease_expires_at > (now() AT TIME ZONE 'UTC')
                      AND elem->>'state' = ANY(CAST(:states AS text[]))
                      AND elem->>'amount_cny' IS NOT NULL
                      AND created_at >= :month_start
                      AND created_at < :next_month_start
                    """
                ),
                {
                    "states": list(_ACTIVE_RESERVATION_STATES),
                    "month_start": month_start,
                    "next_month_start": next_month,
                },
            )
        ).fetchone()
        return Decimal(str(row[0])) if row is not None else Decimal("0")

    # ------------------------------------------------------------------
    # 派发绑定与 owner 恢复（D4，§9.2）
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def mark_dispatched(
        self,
        *,
        invocation_id: str,
        reservation_id: str | None,
        owner_session_id: str,
        owner_epoch: int,
    ) -> bool:
        """绑定派发：预留状态 reserved→dispatched（方案 §6.2 步骤 3）。

        仅分配派发身份，不建立实际尝试记录（ai_attempt 行由 ``insert_attempt``
        在真正进入适配器执行时建立）。
        返回 False = owner 失权（rowcount=0），调用方不得继续派发。
        """
        self._check_engine(context="ai_usage.mark_dispatched")
        async with self._guarded_begin() as conn:
            result = await conn.execute(
                sa.text(
                    """
                    UPDATE ai_invocation
                    SET reservations = (
                            SELECT jsonb_agg(
                                CASE
                                    WHEN elem->>'reservation_id' = :reservation_id
                                        THEN jsonb_set(elem, '{state}', '"dispatched"')
                                    ELSE elem
                                END
                            )
                            FROM jsonb_array_elements(reservations) AS elem
                        ),
                        updated_at = (now() AT TIME ZONE 'UTC')
                    WHERE invocation_id = :invocation_id
                      AND owner_session_id = :owner_session_id
                      AND owner_epoch = :owner_epoch
                      AND owner_lease_expires_at > (now() AT TIME ZONE 'UTC')
                    """
                ),
                {
                    "reservation_id": reservation_id,
                    "invocation_id": invocation_id,
                    "owner_session_id": owner_session_id,
                    "owner_epoch": owner_epoch,
                },
            )
            return bool(result.rowcount and result.rowcount > 0)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def insert_attempt(
        self,
        *,
        attempt_id: str,
        invocation_id: str,
        configured_model: str,
        attempt_row: dict[str, Any] | None = None,
    ) -> None:
        """建立实际尝试记录（方案 §6.2 步骤 4：真正进入适配器执行时）。

        ``ON CONFLICT DO NOTHING``：同 attempt_id 重试沿用已绑定身份，不重建
        （重试共用同一 attempt_id，方案 §6.2 步骤 4「不重新生成」）。
        """
        self._check_engine(context="ai_usage.insert_attempt")
        row = attempt_row or {}
        async with self._guarded_begin() as conn:
            await conn.execute(
                sa.text(
                    """
                    INSERT INTO ai_attempt (
                        attempt_id, invocation_id, task_id, candidate_index, retry_index,
                        connection_id, connection_revision, adapter_kind, configured_model,
                        security_revision, reservation_id, status
                    ) VALUES (
                        :attempt_id, :invocation_id, :task_id, :candidate_index, :retry_index,
                        :connection_id, :connection_revision, :adapter_kind, :configured_model,
                        :security_revision, :reservation_id, 'dispatched'
                    )
                    ON CONFLICT (attempt_id) DO NOTHING
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "invocation_id": invocation_id,
                    "task_id": row.get("task_id", ""),
                    "candidate_index": row.get("candidate_index", 0),
                    "retry_index": row.get("retry_index", 0),
                    "connection_id": row.get("connection_id", ""),
                    "connection_revision": row.get("connection_revision", 0),
                    "adapter_kind": row.get("adapter_kind", "litellm_chat"),
                    "configured_model": configured_model,
                    "security_revision": row.get("security_revision", 0),
                    "reservation_id": row.get("reservation_id"),
                },
            )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def claim_invocation(
        self,
        *,
        invocation_id: str,
        owner_session_id: str,
        lease_expires_at: datetime.datetime,
    ) -> int | None:
        """恢复认领：过期/原 owner 可重入时递增 epoch 并换发租约（D4）。

        返回新 owner_epoch；None = 该 invocation 活跃租约仍被其他 owner 持有。
        """
        self._check_engine(context="ai_usage.claim_invocation")
        async with self._guarded_begin() as conn:
            lock_key = await self._invocation_month_key(conn, invocation_id)
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :lock_key)"),
                {"ns": _ADVISORY_LOCK_NAMESPACE, "lock_key": lock_key},
            )
            row = (
                await conn.execute(
                    sa.text(
                        """
                        UPDATE ai_invocation
                        SET owner_session_id = :owner_session_id,
                            owner_epoch = owner_epoch + 1,
                            owner_lease_expires_at = :lease_expires_at,
                            updated_at = (now() AT TIME ZONE 'UTC')
                        WHERE invocation_id = :invocation_id
                          AND (owner_lease_expires_at <= (now() AT TIME ZONE 'UTC') OR owner_session_id = :owner_session_id)
                        RETURNING owner_epoch
                        """
                    ),
                    {
                        "owner_session_id": owner_session_id,
                        "lease_expires_at": lease_expires_at,
                        "invocation_id": invocation_id,
                    },
                )
            ).fetchone()
            return int(row[0]) if row is not None else None

    @staticmethod
    async def _invocation_month_key(conn: Any, invocation_id: str) -> int:
        """invocation 归属月锁键（Asia/Shanghai 归属，D2）。

        无锁读 created_at 计算归属月，随后 advisory lock + 行级 UPDATE 兜底
        TOCTOU；行不存在时回退当前月键（恢复路径新建归属）。
        """
        row = (
            await conn.execute(
                sa.text("SELECT created_at FROM ai_invocation WHERE invocation_id = :invocation_id"),
                {"invocation_id": invocation_id},
            )
        ).fetchone()
        created = row[0] if row is not None else None
        if not isinstance(created, datetime.datetime):
            now_cst = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=8)
            return now_cst.year * 100 + now_cst.month
        created_cst = created + datetime.timedelta(hours=8)
        return created_cst.year * 100 + created_cst.month

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def recover_expired_owners(self, *, now: datetime.datetime | None = None) -> int:
        """启动前恢复（D9/D4）：过期租约的 dispatched 预留转 held_unconfirmed。

        返回处理的 invocation 数；新 owner 经 claim_invocation 认领后自行处置
        held_unconfirmed（按原身份读确认不重发，D5）。
        """
        self._check_engine(context="ai_usage.recover_expired_owners")
        async with self._guarded_begin() as conn:
            result = await conn.execute(
                sa.text(
                    """
                    UPDATE ai_invocation
                    SET reservations = (
                            SELECT jsonb_agg(
                                CASE
                                    WHEN elem->>'state' = 'dispatched'
                                        THEN jsonb_set(elem, '{state}', '"held_unconfirmed"')
                                    ELSE elem
                                END
                            )
                            FROM jsonb_array_elements(reservations) AS elem
                        ),
                        updated_at = (now() AT TIME ZONE 'UTC')
                    WHERE owner_lease_expires_at <= :now
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements(reservations) AS e
                          WHERE e->>'state' = 'dispatched'
                      )
                    """
                ),
                {"now": now or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)},
            )
            return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # 用量结算（D6，§10：迟到差额幂等整体推进）
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def settle_attempt_usage(
        self,
        *,
        attempt_id: str,
        invocation_id: str,
        status: str,
        usage_status: str,
        usage: NormalizedLLMUsage | None,
        usage_revision: int,
        error_code: str | None,
        duration_ms: float | None,
        cost_cny: str | None,
        actual_model: str | None,
        connection_id: str,
        connection_revision: int,
        adapter_kind: str,
        configured_model: str,
        scenario_id: str,
        task_id: str,
        trigger: str,
        candidate_index: int,
        retry_index: int,
        security_revision: int,
        month: str,
        encoder_input_tokens: int | None = None,
        text_count: int | None = None,
        usage_window_expires_at: datetime.datetime | None = None,
        usage_fingerprint: str | None = None,
        invocation_count_delta: int = 0,
    ) -> bool:
        """终态落账：attempt 修订单调推进，差额整体计入月汇总（两 scope）。

        幂等：usage_revision <= accounted_usage_revision 时不重复计费（迟到差额
        只推进一次）；返回 False = 本次无推进（旧修订或已结算）。
        """
        self._check_engine(context="ai_usage.settle_attempt_usage")
        async with self._guarded_begin() as conn:
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :lock_key)"),
                {"ns": _ADVISORY_LOCK_NAMESPACE, "lock_key": month_to_lock_key(month)},
            )
            # 旧修订不覆盖新修订（水位语义，R22）
            row = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT accounted_usage_revision, cost_cny
                        FROM ai_attempt
                        WHERE attempt_id = :attempt_id
                        FOR UPDATE
                        """
                    ),
                    {"attempt_id": attempt_id},
                )
            ).fetchone()

            if row is None:
                # attempt 行缺失（mark_dispatched 从未落库）：直接插入终态
                await self._insert_terminal_attempt(
                    conn,
                    attempt_id=attempt_id,
                    invocation_id=invocation_id,
                    task_id=task_id,
                    candidate_index=candidate_index,
                    retry_index=retry_index,
                    connection_id=connection_id,
                    connection_revision=connection_revision,
                    adapter_kind=adapter_kind,
                    configured_model=configured_model,
                    actual_model=actual_model,
                    security_revision=security_revision,
                    status=status,
                    error_code=error_code,
                    duration_ms=duration_ms,
                    usage=usage,
                    usage_status=usage_status,
                    usage_revision=usage_revision,
                    cost_cny=cost_cny,
                    encoder_input_tokens=encoder_input_tokens,
                    text_count=text_count,
                    usage_window_expires_at=usage_window_expires_at,
                    usage_fingerprint=usage_fingerprint,
                )
                old_accounted_cost = Decimal("0")
                should_account = True
            else:
                accounted_rev = int(row[0] or 0)
                if usage_revision <= accounted_rev:
                    return False
                old_accounted_cost = Decimal(str(row[1])) if row[1] is not None else Decimal("0")
                await conn.execute(
                    sa.text(
                        """
                        UPDATE ai_attempt
                        SET status = :status,
                            error_code = :error_code,
                            duration_ms = :duration_ms,
                            input_total_tokens = :input_total_tokens,
                            uncached_input_tokens = :uncached_input_tokens,
                            cache_read_tokens = :cache_read_tokens,
                            cache_write_tokens = :cache_write_tokens,
                            output_tokens = :output_tokens,
                            encoder_input_tokens = :encoder_input_tokens,
                            text_count = :text_count,
                            usage_status = :usage_status,
                            usage_revision = :usage_revision,
                            accounted_usage_revision = :usage_revision,
                            usage_fingerprint = :usage_fingerprint,
                            usage_window_expires_at = :usage_window_expires_at,
                            actual_model = :actual_model,
                            cost_cny = :cost_cny,
                            updated_at = (now() AT TIME ZONE 'UTC')
                        WHERE attempt_id = :attempt_id
                        """
                    ),
                    self._usage_update_params(
                        attempt_id=attempt_id,
                        status=status,
                        error_code=error_code,
                        duration_ms=duration_ms,
                        usage=usage,
                        usage_status=usage_status,
                        usage_revision=usage_revision,
                        cost_cny=cost_cny,
                        encoder_input_tokens=encoder_input_tokens,
                        text_count=text_count,
                        usage_window_expires_at=usage_window_expires_at,
                        usage_fingerprint=usage_fingerprint,
                        actual_model=actual_model,
                    ),
                )
                should_account = True

            if not should_account:
                return False

            # 差额 = 本次成本 - 已计入成本，整体推进月汇总（迟到差额只计一次）
            new_cost = Decimal(cost_cny) if cost_cny is not None else Decimal("0")
            delta = new_cost - old_accounted_cost
            succeeded = status == "succeeded"
            for scope, grouping_key in (
                (SCOPE_SCENARIO, scenario_id),
                (SCOPE_CONNECTION, connection_id),
            ):
                await self._upsert_monthly_delta(
                    conn,
                    month=month,
                    scope=scope,
                    grouping_key=grouping_key,
                    scenario_id=scenario_id,
                    trigger=trigger,
                    task_id=task_id,
                    connection_id=connection_id,
                    invocation_count_delta=invocation_count_delta if scope == SCOPE_SCENARIO else 0,
                    attempt_count_delta=1,
                    succeeded_delta=1 if succeeded else 0,
                    cost_delta=delta,
                    usage_revision=usage_revision,
                )
            return True

    async def _insert_terminal_attempt(self, conn: Any, **kwargs: Any) -> None:
        await conn.execute(
            sa.text(
                """
                INSERT INTO ai_attempt (
                    attempt_id, invocation_id, task_id, candidate_index, retry_index,
                    connection_id, connection_revision, adapter_kind, configured_model,
                    actual_model, security_revision, status, error_code, duration_ms,
                    input_total_tokens, uncached_input_tokens, cache_read_tokens,
                    cache_write_tokens, output_tokens, encoder_input_tokens, text_count,
                    usage_status, usage_revision, accounted_usage_revision,
                    usage_fingerprint, usage_window_expires_at, cost_cny
                ) VALUES (
                    :attempt_id, :invocation_id, :task_id, :candidate_index, :retry_index,
                    :connection_id, :connection_revision, :adapter_kind, :configured_model,
                    :actual_model, :security_revision, :status, :error_code, :duration_ms,
                    :input_total_tokens, :uncached_input_tokens, :cache_read_tokens,
                    :cache_write_tokens, :output_tokens, :encoder_input_tokens, :text_count,
                    :usage_status, :usage_revision, :usage_revision,
                    :usage_fingerprint, :usage_window_expires_at, :cost_cny
                )
                """
            ),
            {
                "attempt_id": kwargs["attempt_id"],
                "invocation_id": kwargs["invocation_id"],
                "task_id": kwargs["task_id"],
                "candidate_index": kwargs["candidate_index"],
                "retry_index": kwargs["retry_index"],
                "connection_id": kwargs["connection_id"],
                "connection_revision": kwargs["connection_revision"],
                "adapter_kind": kwargs["adapter_kind"],
                "configured_model": kwargs["configured_model"],
                "actual_model": kwargs["actual_model"],
                "security_revision": kwargs["security_revision"],
                "status": kwargs["status"],
                "error_code": kwargs["error_code"],
                "duration_ms": kwargs["duration_ms"],
                "input_total_tokens": kwargs["usage"].input_total_tokens if kwargs["usage"] else None,
                "uncached_input_tokens": kwargs["usage"].uncached_input_tokens if kwargs["usage"] else None,
                "cache_read_tokens": kwargs["usage"].cache_read_tokens if kwargs["usage"] else None,
                "cache_write_tokens": kwargs["usage"].cache_write_tokens if kwargs["usage"] else None,
                "output_tokens": kwargs["usage"].output_tokens if kwargs["usage"] else None,
                "encoder_input_tokens": kwargs.get("encoder_input_tokens"),
                "text_count": kwargs.get("text_count"),
                "usage_status": kwargs["usage_status"],
                "usage_revision": kwargs["usage_revision"],
                "usage_fingerprint": kwargs.get("usage_fingerprint"),
                "usage_window_expires_at": kwargs.get("usage_window_expires_at"),
                "cost_cny": kwargs["cost_cny"],
            },
        )

    @staticmethod
    def _usage_update_params(**kwargs: Any) -> dict[str, Any]:
        usage = kwargs["usage"]
        return {
            "attempt_id": kwargs["attempt_id"],
            "status": kwargs["status"],
            "error_code": kwargs["error_code"],
            "duration_ms": kwargs["duration_ms"],
            "input_total_tokens": usage.input_total_tokens if usage else None,
            "uncached_input_tokens": usage.uncached_input_tokens if usage else None,
            "cache_read_tokens": usage.cache_read_tokens if usage else None,
            "cache_write_tokens": usage.cache_write_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "encoder_input_tokens": kwargs.get("encoder_input_tokens"),
            "text_count": kwargs.get("text_count"),
            "usage_status": kwargs["usage_status"],
            "usage_revision": kwargs["usage_revision"],
            "usage_fingerprint": kwargs.get("usage_fingerprint"),
            "usage_window_expires_at": kwargs.get("usage_window_expires_at"),
            "actual_model": kwargs["actual_model"],
            "cost_cny": kwargs["cost_cny"],
        }

    async def _upsert_monthly_delta(
        self,
        conn: Any,
        *,
        month: str,
        scope: str,
        grouping_key: str,
        scenario_id: str,
        trigger: str,
        task_id: str,
        connection_id: str,
        invocation_count_delta: int,
        attempt_count_delta: int,
        succeeded_delta: int,
        cost_delta: Decimal,
        usage_revision: int,
    ) -> None:
        """月汇总差额累加；last_usage_revision 用 GREATEST 保持单调（R22）。"""
        if scope == SCOPE_CONNECTION:
            scenario_col_value: str | None = None
            trigger_value = trigger
            task_col_value: str | None = None
        else:
            scenario_col_value = scenario_id
            trigger_value = trigger
            task_col_value = task_id
        await conn.execute(
            sa.text(
                """
                INSERT INTO ai_usage_monthly (
                    month, aggregation_scope, grouping_key, scenario_id, trigger,
                    task_id, connection_id, invocation_count, attempt_count,
                    succeeded_count, cost_cny, last_usage_revision
                ) VALUES (
                    :month, :scope, :grouping_key, :scenario_id, :trigger,
                    :task_id, :connection_id, :invocation_count, :attempt_count,
                    :succeeded_count, :cost_cny, :usage_revision
                )
                ON CONFLICT (month, aggregation_scope, grouping_key) DO UPDATE SET
                    invocation_count = ai_usage_monthly.invocation_count + EXCLUDED.invocation_count,
                    attempt_count = ai_usage_monthly.attempt_count + EXCLUDED.attempt_count,
                    succeeded_count = ai_usage_monthly.succeeded_count + EXCLUDED.succeeded_count,
                    cost_cny = ai_usage_monthly.cost_cny + EXCLUDED.cost_cny,
                    last_usage_revision = GREATEST(ai_usage_monthly.last_usage_revision, EXCLUDED.last_usage_revision),
                    updated_at = (now() AT TIME ZONE 'UTC')
                """
            ),
            {
                "month": month,
                "scope": scope,
                "grouping_key": grouping_key,
                "scenario_id": scenario_col_value,
                "trigger": trigger_value,
                "task_id": task_col_value,
                "connection_id": connection_id,
                "invocation_count": invocation_count_delta,
                "attempt_count": attempt_count_delta,
                "succeeded_count": succeeded_delta,
                "cost_cny": str(cost_delta),
                "usage_revision": usage_revision,
            },
        )

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def apply_monthly_delta(
        self,
        *,
        month: str,
        aggregation_scope: str,
        grouping_key: str,
        scenario_id: str,
        trigger: str,
        invocation_count: int,
        attempt_count: int,
        cost_cny_delta: Decimal,
        last_usage_revision: int = 0,
        succeeded_count: int = 0,
        task_id: str | None = None,
        connection_id: str | None = None,
    ) -> None:
        """月汇总差额独立应用（R22 单调水位；乱序写入断言最大值由集成测试覆盖）。"""
        self._check_engine(context="ai_usage.apply_monthly_delta")
        async with self._guarded_begin() as conn:
            await self._upsert_monthly_delta(
                conn,
                month=month,
                scope=aggregation_scope,
                grouping_key=grouping_key,
                scenario_id=scenario_id,
                trigger=trigger,
                task_id=task_id or "",
                connection_id=connection_id or "",
                invocation_count_delta=invocation_count,
                attempt_count_delta=attempt_count,
                succeeded_delta=succeeded_count,
                cost_delta=cost_cny_delta,
                usage_revision=last_usage_revision,
            )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def close_usage_window(self, *, attempt_id: str) -> bool:
        """惰性窗口关闭：submit_terminal 顺带扫描到期 attempt（D6）。"""
        self._check_engine(context="ai_usage.close_usage_window")
        async with self._guarded_begin() as conn:
            result = await conn.execute(
                sa.text(
                    """
                    UPDATE ai_attempt
                    SET usage_window_closed_at = (now() AT TIME ZONE 'UTC'), updated_at = (now() AT TIME ZONE 'UTC')
                    WHERE attempt_id = :attempt_id
                      AND usage_window_closed_at IS NULL
                      AND usage_window_expires_at <= (now() AT TIME ZONE 'UTC')
                    """
                ),
                {"attempt_id": attempt_id},
            )
            return bool(result.rowcount and result.rowcount > 0)

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def read_month_usage(self, *, month: str, aggregation_scope: str) -> list[dict[str, Any]]:
        """月汇总读取（S5 UI 展示边界）。"""
        self._check_engine(context="ai_usage.read_month_usage")
        async with self._guarded_begin() as conn:
            result = await conn.execute(
                sa.text(
                    """
                    SELECT month, aggregation_scope, grouping_key, scenario_id, trigger,
                           task_id, connection_id, invocation_count, attempt_count,
                           succeeded_count, cost_cny, last_usage_revision
                    FROM ai_usage_monthly
                    WHERE month = :month AND aggregation_scope = :scope
                    ORDER BY grouping_key
                    """
                ),
                {"month": month, "scope": aggregation_scope},
            )
            rows = result.fetchall() if hasattr(result, "fetchall") else result.mappings().fetchall()
            return [dict(r._mapping) for r in rows]
