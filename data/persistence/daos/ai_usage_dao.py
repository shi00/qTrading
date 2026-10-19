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

# ---------------------------------------------------------------------------
# 使用记录读取（S5 UI 展示边界，模型统一.md §11.5）
# 全部经 ``sa.text()`` 命名参数化（R4；无 f-string / %s / 字面量拼接）。
# 终态 / 费用状态为「按 ai_attempt 派生」的展示列——invocation 表（0040 迁移 /
# models.py）未存终态 / 费用状态列，故派生而非臆造存储列；派生口径见方法 docstring。
# ---------------------------------------------------------------------------

# 请求页派生查询：invocation LEFT JOIN 尝试聚合（a）与候选决策聚合（d）。
# 连接 / 子任务过滤经 EXISTS（一个请求一行，天然去重，§11.5）。
_REQUEST_PAGE_SELECT_SQL: Final[str] = """
SELECT
    r.invocation_id,
    r.scenario_id,
    r.trigger,
    r.created_at,
    r.updated_at,
    r.is_test,
    r.task_summary,
    r.attempt_count,
    r.total_duration_ms,
    r.source_summary,
    r.known_cost_cny,
    r.priced_attempts,
    CASE
        WHEN r.succeeded_attempts > 0 THEN 'succeeded'
        WHEN r.cancelled_attempts > 0 THEN 'cancelled'
        WHEN r.dispatched_attempts > 0 THEN 'running'
        WHEN r.failed_attempts > 0 THEN 'failed'
        WHEN r.attempt_count = 0 AND r.skipped_decisions > 0 THEN 'skipped'
        ELSE 'unknown'
    END AS terminal_status,
    CASE
        WHEN r.attempt_count = 0 THEN NULL
        WHEN r.priced_attempts = r.attempt_count THEN 'known'
        WHEN r.priced_attempts = 0 AND r.unpriced_attempts = r.attempt_count THEN 'unpriced'
        WHEN r.priced_attempts = 0 THEN 'unknown'
        ELSE 'partial'
    END AS cost_status
FROM (
    SELECT
        i.invocation_id,
        i.scenario_id,
        i.trigger,
        i.created_at,
        i.updated_at,
        (i.test_operation_id IS NOT NULL) AS is_test,
        i.candidate_decisions,
        COALESCE(a.attempt_count, 0) AS attempt_count,
        COALESCE(a.succeeded_attempts, 0) AS succeeded_attempts,
        COALESCE(a.failed_attempts, 0) AS failed_attempts,
        COALESCE(a.cancelled_attempts, 0) AS cancelled_attempts,
        COALESCE(a.dispatched_attempts, 0) AS dispatched_attempts,
        COALESCE(a.priced_attempts, 0) AS priced_attempts,
        COALESCE(a.unpriced_attempts, 0) AS unpriced_attempts,
        a.total_duration_ms,
        a.known_cost_cny,
        a.source_summary,
        a.task_summary,
        COALESCE(d.skipped_decisions, 0) AS skipped_decisions
    FROM ai_invocation i
    LEFT JOIN (
        SELECT
            a.invocation_id,
            COUNT(*)::int AS attempt_count,
            COUNT(*) FILTER (WHERE a.status = 'succeeded')::int AS succeeded_attempts,
            COUNT(*) FILTER (WHERE a.status = 'failed')::int AS failed_attempts,
            COUNT(*) FILTER (WHERE a.status = 'cancelled')::int AS cancelled_attempts,
            COUNT(*) FILTER (WHERE a.status = 'dispatched')::int AS dispatched_attempts,
            COUNT(*) FILTER (WHERE a.cost_cny IS NOT NULL)::int AS priced_attempts,
            COUNT(*) FILTER (WHERE a.usage_status = 'unavailable')::int AS unpriced_attempts,
            SUM(a.duration_ms) AS total_duration_ms,
            SUM(a.cost_cny) AS known_cost_cny,
            string_agg(DISTINCT COALESCE(a.actual_model, a.configured_model, a.connection_id), ', ') AS source_summary,
            string_agg(DISTINCT a.task_id, ', ') AS task_summary
        FROM ai_attempt a
        GROUP BY a.invocation_id
    ) a ON a.invocation_id = i.invocation_id
    LEFT JOIN (
        SELECT
            i2.invocation_id,
            COUNT(*) FILTER (WHERE d->>'decision' = 'skipped')::int AS skipped_decisions
        FROM ai_invocation i2,
             jsonb_array_elements(COALESCE(i2.candidate_decisions, '[]'::jsonb)) AS d
        GROUP BY i2.invocation_id
    ) d ON d.invocation_id = i.invocation_id
) r
"""

# 有序尝试明细（含候选序号 / retry_index / 状态 / 错误 / 价格快照 / 用量）。
_REQUEST_ATTEMPTS_SQL: Final[str] = """
SELECT
    a.attempt_id,
    a.task_id,
    a.candidate_index,
    a.retry_index,
    a.connection_id,
    a.status,
    a.error_code,
    a.actual_model,
    a.configured_model,
    a.duration_ms,
    a.cost_cny,
    a.usage_status,
    a.input_total_tokens,
    a.output_tokens
FROM ai_attempt a
WHERE a.invocation_id = :invocation_id
ORDER BY a.task_id, a.candidate_index, a.retry_index, a.created_at
"""

# 跳过行来源：candidate_decisions（decision='skipped'）。仅取非敏感字段，
# 不生成用量（§10.1：append_decision 不建 attempt、不增 usage）。
_REQUEST_SKIPPED_DECISIONS_SQL: Final[str] = """
SELECT
    d->>'task_id' AS task_id,
    d->>'candidate_index' AS candidate_index,
    d->>'retry_index' AS retry_index,
    d->>'connection_id' AS connection_id,
    d->>'reason' AS reason
FROM ai_invocation i,
     jsonb_array_elements(COALESCE(i.candidate_decisions, '[]'::jsonb)) AS d
WHERE i.invocation_id = :invocation_id
  AND d->>'decision' = 'skipped'
ORDER BY d->>'task_id', d->>'candidate_index', d->>'retry_index'
"""

# 已知费用：月汇总 scenario scope 合计（NULL 表示无数据 → None，禁伪零，R21）。
_MONTH_KNOWN_COST_SQL: Final[str] = """
SELECT SUM(cost_cny)
FROM ai_usage_monthly
WHERE month = :month AND aggregation_scope = :scope
"""

# 有效预留：归属月内、租约未过期的活跃预留金额合计（与 D3 口径一致）。
_MONTH_EFFECTIVE_RESERVED_SQL: Final[str] = """
SELECT SUM((elem->>'amount_cny')::numeric)
FROM ai_invocation, jsonb_array_elements(reservations) AS elem
WHERE elem->>'state' = ANY(CAST(:states AS text[]))
  AND elem->>'amount_cny' IS NOT NULL
  AND owner_lease_expires_at > (now() AT TIME ZONE 'UTC')
  AND created_at >= :month_start
  AND created_at < :next_month_start
"""

# 未确认预留：held_unconfirmed（不受租约是否过期影响，需人工处置）。
_MONTH_UNCONFIRMED_RESERVED_SQL: Final[str] = """
SELECT SUM((elem->>'amount_cny')::numeric)
FROM ai_invocation, jsonb_array_elements(reservations) AS elem
WHERE elem->>'state' = 'held_unconfirmed'
  AND elem->>'amount_cny' IS NOT NULL
  AND created_at >= :month_start
  AND created_at < :next_month_start
"""

# 未确认预留明细（§11.5「未确认详情」）：逐条 reservation_id / 子任务 / 金额 / 归属月，
# 供使用记录页按条解除；仅择非敏感字段（不返回 UUID 之外的凭据 / 正文）。
# **不按当前月过滤**：预留可能跨月仍停留 held_unconfirmed，若只查当前月则历史遗漏永远
# 无法解除，违反 §10.3「未确认预留需先明确处理」；按归属月升序展示便于识别陈欠。
_UNCONFIRMED_RESERVATIONS_SQL: Final[str] = """
SELECT
    elem->>'reservation_id' AS reservation_id,
    elem->>'task_id' AS task_id,
    elem->>'amount_cny' AS amount_cny,
    to_char((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') AS billing_month
FROM ai_invocation, jsonb_array_elements(reservations) AS elem
WHERE elem->>'state' = 'held_unconfirmed'
  AND elem->>'reservation_id' IS NOT NULL
ORDER BY billing_month, elem->>'reservation_id'
"""

_LEGACY_MONTH_COST_SQL: Final[str] = "SELECT config_value FROM app_state WHERE config_key = :key"


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


def _month_utc_bounds(month: str) -> tuple[datetime.datetime, datetime.datetime]:
    """Asia/Shanghai 归属月 → 该月的 naive UTC 墙钟 ``[start, next_start)`` 边界。"""
    tz = ZoneInfo("Asia/Shanghai")
    year, mon = int(month[:4]), int(month[4:6])
    start = datetime.datetime(year, mon, 1, tzinfo=tz).astimezone(datetime.UTC).replace(tzinfo=None)
    next_year, next_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    next_start = datetime.datetime(next_year, next_mon, 1, tzinfo=tz).astimezone(datetime.UTC).replace(tzinfo=None)
    return start, next_start


def _to_int(value: Any) -> int | None:
    """JSONB 文本值 → int；非法 / None 返回 None（不伪零）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_zero(row: Any) -> Decimal:
    """``fetchone`` 结果为 None / NULL / 坏值时返回 ``Decimal("0")``（事实零）。"""
    if row is None or row[0] is None:
        return Decimal("0")
    try:
        return Decimal(str(row[0]))
    except (InvalidOperation, ValueError, ArithmeticError):
        return Decimal("0")


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

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def release_reservation(self, *, reservation_id: str) -> bool:
        """解除未确认预留：``held_unconfirmed`` → ``released``（保留历史，不删除元素）。

        §11.5：未确认详情「解除未确认预留」——只把该预留元素 state 改为 ``released``，
        并记 ``released_at`` / ``released_reason``（保留历史记录，不删除、不改写既有已结算
        费用）；预算摘要由 state 派生，改后未确认 / 有效预留自然扣除，无需改月汇总行（D3）。

        锁序（§9.2 / §10.2）：先定位承载该预留的 invocation，再按其**归属月**取
        ``pg_advisory_xact_lock``，最后改 invocation 行——与 ``try_reserve`` /
        ``settle_attempt_usage`` 同序，避免跨月并发下与月汇总结算交错。无 owner 条件
        （人工解除非 owner 身份绑定操作）。找不到该 reservation / 状态非
        ``held_unconfirmed`` → rowcount=0 → 返回 False（不抛）。

        R5：入口 ``_check_engine`` 守卫引擎状态。
        """
        self._check_engine(context="ai_usage.release_reservation")
        released_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
        async with self._guarded_begin() as conn:
            invocation_id = await self._reservation_holder(conn, reservation_id)
            if invocation_id is None:
                return False
            lock_key = await self._invocation_month_key(conn, invocation_id)
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :lock_key)"),
                {"ns": _ADVISORY_LOCK_NAMESPACE, "lock_key": lock_key},
            )
            result = await conn.execute(
                sa.text(
                    """
                    UPDATE ai_invocation
                    SET reservations = (
                            SELECT jsonb_agg(
                                CASE
                                    WHEN elem->>'reservation_id' = :reservation_id
                                        AND elem->>'state' = 'held_unconfirmed'
                                        THEN elem || jsonb_build_object(
                                            'state', 'released',
                                            'released_at', cast(:released_at as text),
                                            'released_reason', 'manual'
                                        )
                                    ELSE elem
                                END
                            )
                            FROM jsonb_array_elements(reservations) AS elem
                        ),
                        updated_at = (now() AT TIME ZONE 'UTC')
                    WHERE invocation_id = :invocation_id
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements(reservations) AS e
                          WHERE e->>'reservation_id' = :reservation_id
                            AND e->>'state' = 'held_unconfirmed'
                      )
                    """
                ),
                {
                    "reservation_id": reservation_id,
                    "invocation_id": invocation_id,
                    "released_at": released_at,
                },
            )
            return bool(result.rowcount and result.rowcount > 0)

    @staticmethod
    async def _reservation_holder(conn: Any, reservation_id: str) -> str | None:
        """承载该预留的 invocation_id（无锁读，随后月锁 + 条件 UPDATE 兜底 TOCTOU）。

        同一 reservation_id 只属于一个 held_unconfirmed 元素；取 created_at 最新者以
        对齐归属月判定。
        """
        row = (
            await conn.execute(
                sa.text(
                    """
                    SELECT invocation_id FROM ai_invocation
                    WHERE EXISTS (
                        SELECT 1 FROM jsonb_array_elements(reservations) AS e
                        WHERE e->>'reservation_id' = :reservation_id
                          AND e->>'state' = 'held_unconfirmed'
                    )
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"reservation_id": reservation_id},
            )
        ).fetchone()
        return None if row is None else str(row[0])

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

    # ------------------------------------------------------------------
    # 使用记录读取（S5 UI 展示边界，模型统一.md §11.5）
    # ------------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def read_request_page(
        self,
        *,
        start: datetime.datetime | None = None,
        end: datetime.datetime | None = None,
        scenario_id: str | None = None,
        task_id: str | None = None,
        connection_id: str | None = None,
        origin: str | None = None,
        terminal_status: str | None = None,
        cost_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """请求分页（§11.5）：一份请求一行 + 总数，支持多维过滤、分页 50。

        ``terminal_status`` / ``cost_status`` 为「按 ai_attempt 派生」的展示列
        （见 ``_REQUEST_PAGE_SELECT_SQL``）：invocation 表未存终态 / 费用状态列，
        故按尝试聚合派生而非臆造存储列。

        ``origin``：``"test"`` → 测试请求（``test_operation_id IS NOT NULL``）；
        ``"business"`` → 业务请求；其它值 ``raise ValueError``（不静默放宽）。

        派生列过滤（``terminal_status`` / ``cost_status``）经外层包裹别名 ``p`` 引用
        （PostgreSQL 不允许在 ``WHERE`` 引用 SELECT 输出列别名）；子任务 / 连接过滤经
        ``EXISTS`` 子查询（一个请求一行，天然去重）。返回 ``(rows, total_count)``。
        """
        self._check_engine(context="ai_usage.read_request_page")
        if origin is not None and origin not in ("test", "business"):
            raise ValueError(f"unknown origin filter: {origin!r}")

        clauses: list[str] = []
        base_params: dict[str, Any] = {}
        if start is not None:
            clauses.append("p.created_at >= :start")
            base_params["start"] = start
        if end is not None:
            clauses.append("p.created_at < :end")
            base_params["end"] = end
        if scenario_id is not None:
            clauses.append("p.scenario_id = :scenario_id")
            base_params["scenario_id"] = scenario_id
        if task_id is not None:
            clauses.append(
                "(EXISTS (SELECT 1 FROM ai_attempt ta"
                " WHERE ta.invocation_id = p.invocation_id AND ta.task_id = :task_id)"
                " OR EXISTS (SELECT 1 FROM ai_invocation ti,"
                " jsonb_array_elements(COALESCE(ti.candidate_decisions, '[]'::jsonb)) td"
                " WHERE ti.invocation_id = p.invocation_id AND td->>'task_id' = :task_id))"
            )
            base_params["task_id"] = task_id
        if connection_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM ai_attempt ca"
                " WHERE ca.invocation_id = p.invocation_id AND ca.connection_id = :connection_id)"
            )
            base_params["connection_id"] = connection_id
        if origin == "test":
            clauses.append("p.is_test = TRUE")
        elif origin == "business":
            clauses.append("p.is_test = FALSE")
        if terminal_status is not None:
            clauses.append("p.terminal_status = :terminal_status")
            base_params["terminal_status"] = terminal_status
        if cost_status is not None:
            clauses.append("p.cost_status = :cost_status")
            base_params["cost_status"] = cost_status

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # 外层包裹派生表别名 p：WHERE 只能引用 FROM 项，不能引用 SELECT 输出列别名
        # （PG 语义），故把 WHERE 施加在包裹层、并以 p.<派生列> 引用。
        filtered_sql = "\n".join(["SELECT * FROM (", _REQUEST_PAGE_SELECT_SQL, ") AS p", where_sql])
        page_sql = "\n".join(
            [
                filtered_sql,
                "ORDER BY p.created_at DESC, p.invocation_id",
                "LIMIT :limit OFFSET :offset",
            ]
        )
        count_sql = "\n".join(["SELECT COUNT(*) AS total_cnt FROM (", filtered_sql, ") AS t"])
        page_params = {**base_params, "limit": int(limit), "offset": int(offset)}

        async with self._guarded_begin() as conn:
            page_rows = (await conn.execute(sa.text(page_sql), page_params)).mappings().fetchall()
            count_row = (await conn.execute(sa.text(count_sql), dict(base_params))).fetchone()

        total = int(count_row[0]) if count_row is not None and count_row[0] is not None else 0
        return [dict(row) for row in page_rows], total

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def read_request_attempts(self, *, invocation_id: str) -> list[dict[str, Any]]:
        """请求的有序尝试明细（§11.5）。

        实际尝试行（``ai_attempt``）标 ``kind="attempt", executed=True``，含候选
        序号 / retry_index / 终态 / 失败原因（error_code）/ 价格快照 / 用量。

        跳过行来自 ``candidate_decisions``（``decision="skipped"``），标
        ``kind="skipped", executed=False, status="skipped"``，用量字段全 ``None``
        （§10.1：跳过决策不建 attempt、不增 usage，禁止返回虚构用量，R21）。
        """
        self._check_engine(context="ai_usage.read_request_attempts")
        async with self._guarded_begin() as conn:
            attempt_rows = (
                (await conn.execute(sa.text(_REQUEST_ATTEMPTS_SQL), {"invocation_id": invocation_id}))
                .mappings()
                .fetchall()
            )
            skipped_rows = (
                (await conn.execute(sa.text(_REQUEST_SKIPPED_DECISIONS_SQL), {"invocation_id": invocation_id}))
                .mappings()
                .fetchall()
            )

        records: list[dict[str, Any]] = []
        for row in attempt_rows:
            d = dict(row)
            records.append(
                {
                    "kind": "attempt",
                    "executed": True,
                    "task_id": d.get("task_id"),
                    "candidate_index": _to_int(d.get("candidate_index")),
                    "retry_index": _to_int(d.get("retry_index")),
                    "connection_id": d.get("connection_id"),
                    "status": d.get("status"),
                    "reason": d.get("error_code"),
                    "actual_model": d.get("actual_model"),
                    "configured_model": d.get("configured_model"),
                    "duration_ms": d.get("duration_ms"),
                    "cost_cny": d.get("cost_cny"),
                    "usage_status": d.get("usage_status"),
                    "input_total_tokens": d.get("input_total_tokens"),
                    "output_tokens": d.get("output_tokens"),
                }
            )
        for row in skipped_rows:
            d = dict(row)
            records.append(
                {
                    "kind": "skipped",
                    "executed": False,
                    "task_id": d.get("task_id"),
                    "candidate_index": _to_int(d.get("candidate_index")),
                    "retry_index": _to_int(d.get("retry_index")),
                    "connection_id": d.get("connection_id"),
                    "status": "skipped",
                    "reason": d.get("reason"),
                    "actual_model": None,
                    "configured_model": None,
                    "duration_ms": None,
                    "cost_cny": None,
                    "usage_status": None,
                    "input_total_tokens": None,
                    "output_tokens": None,
                }
            )
        records.sort(
            key=lambda r: (
                r["task_id"] or "",
                r["candidate_index"] if r["candidate_index"] is not None else -1,
                r["retry_index"] if r["retry_index"] is not None else -1,
            )
        )
        return records

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def read_budget_summary(self, *, month: str) -> dict[str, Any]:
        """月度预算摘要（§11.5）：已知费用 / 有效预留 / 未确认预留三者独立。

        - ``known_cny``：已结算费用（月汇总 scenario scope 合计）+ legacy 汇总
          （app_state ``ai_cost:{YYYYMM}``，分→元）；两者皆无数据 → ``None``
          （未知禁伪零，R21）。
        - ``effective_reserved_cny``：有效预留（租约未过期活跃预留金额合计）；
          无预留 → ``0``（事实零，非未知）。
        - ``unconfirmed_reserved_cny``：未确认预留（``held_unconfirmed``，不受租约
          影响，需人工处置）；无 → ``0``。
        """
        self._check_engine(context="ai_usage.read_budget_summary")
        month_start, next_month_start = _month_utc_bounds(month)
        async with self._guarded_begin() as conn:
            known_row = (
                await conn.execute(sa.text(_MONTH_KNOWN_COST_SQL), {"month": month, "scope": SCOPE_SCENARIO})
            ).fetchone()
            effective_row = (
                await conn.execute(
                    sa.text(_MONTH_EFFECTIVE_RESERVED_SQL),
                    {
                        "states": list(_ACTIVE_RESERVATION_STATES),
                        "month_start": month_start,
                        "next_month_start": next_month_start,
                    },
                )
            ).fetchone()
            unconfirmed_row = (
                await conn.execute(
                    sa.text(_MONTH_UNCONFIRMED_RESERVED_SQL),
                    {"month_start": month_start, "next_month_start": next_month_start},
                )
            ).fetchone()
            legacy_row = (
                await conn.execute(
                    sa.text(_LEGACY_MONTH_COST_SQL),
                    {"key": f"{_LEGACY_COST_KEY_PREFIX}{month}"},
                )
            ).fetchone()

        settled = known_row[0] if known_row is not None else None
        legacy_raw = legacy_row[0] if legacy_row is not None else None
        if settled is None and legacy_raw is None:
            known: Decimal | None = None
        else:
            known = (Decimal(str(settled)) if settled is not None else Decimal("0")) + parse_legacy_cents(legacy_raw)

        return {
            "month": month,
            "known_cny": known,
            "effective_reserved_cny": _decimal_or_zero(effective_row),
            "unconfirmed_reserved_cny": _decimal_or_zero(unconfirmed_row),
        }

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def read_unconfirmed_reservations(self) -> list[dict[str, Any]]:
        """全局未确认预留明细（§11.5「未确认详情」）。

        逐条返回 ``reservation_id`` / ``task_id`` / ``amount_cny``（金额为 JSONB 文本，
        缺失 / 无法解析时保持原值或 None，不伪零，R21）与 ``billing_month``（Asia/Shanghai
        归属月），供使用记录页按条「解除未确认预留」。

        不按当前月过滤：预留可能跨月仍停留 ``held_unconfirmed``，只查当前月会让历史遗留
        永远无法解除，违反 §10.3「未确认预留需先明确处理」后方可清理的约束。
        """
        self._check_engine(context="ai_usage.read_unconfirmed_reservations")
        async with self._guarded_begin() as conn:
            rows = (await conn.execute(sa.text(_UNCONFIRMED_RESERVATIONS_SQL))).mappings().fetchall()
        return [dict(r) for r in rows]
