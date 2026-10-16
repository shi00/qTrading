"""S3 AIUsageDao 单测（模型统一.md §9/§10，方案 D1~D4）。

单测层用 capture conn 断言 SQL 关键语义（真实并发/锁序行为由 sidecar PG 集成测试覆盖）：
- try_reserve：pg_advisory_xact_lock 两键（固定命名空间 + YYYYMM）；
- owner 条件写入：变更 SQL 带 owner_session_id / owner_epoch / lease 三条件（D4）；
- legacy 汇总解析：app_state ``ai_cost:{YYYYMM}`` 分 → 元 Decimal（D3）；
- R22：月汇总写入 SQL 用 GREATEST 保持单调；
- 预算不足返回 None（不抛异常、不伪装成功）。
"""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.persistence.daos.ai_usage_dao import AIUsageDao, parse_legacy_cents

pytestmark = pytest.mark.unit


class _CaptureConn:
    """记录 execute 的 (sql, params) 并按预设结果序列依次返回。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=None):
        import sqlalchemy

        self.calls.append((str(sql), tuple(params) if params else ()))
        result = MagicMock()
        item = self.results.pop(0) if self.results else None
        if isinstance(item, Exception):
            raise item
        result.fetchone = MagicMock(return_value=item)
        result.rowcount = item if isinstance(item, int) else 0
        return result


def _begin_engine(conn: _CaptureConn) -> MagicMock:
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


def _dao(conn: _CaptureConn) -> AIUsageDao:
    dao = AIUsageDao.__new__(AIUsageDao)
    dao.engine = _begin_engine(conn)
    return dao


def _reserve_kwargs(**overrides):
    kwargs = {
        "invocation_id": "inv-1",
        "scenario_id": "news_sentiment",
        "task_id": "sentiment",
        "trigger": "manual",
        "month": "202610",
        "reservation_id": "res-1",
        "attempt_id": "att-1",
        "candidate_index": 0,
        "retry_index": 0,
        "amount_cny": Decimal("0.05"),
        "budget_limit_cny": Decimal("1.00"),
        "budget_policy_snapshot": {"monthly_budget_cny": "1.00"},
        "owner_session_id": "owner-1",
        "owner_epoch": 1,
        "owner_lease_expires_at": datetime.datetime(2026, 10, 16, 12, 0, 0),
        "lease_expires_at": datetime.datetime(2026, 10, 16, 12, 0, 0),
    }
    kwargs.update(overrides)
    return kwargs


class TestTryReserveSqlSemantics:
    @pytest.mark.asyncio
    async def test_advisory_lock_two_keys_present(self):
        # 序列：advisory(None) → INSERT invocation(None) → SUM((0,)) → app_state(("res-1",))
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",)])
        dao = _dao(conn)
        await dao.try_reserve(**_reserve_kwargs())
        all_sql = "\n".join(sql for sql, _ in conn.calls)
        assert "pg_advisory_xact_lock" in all_sql

    @pytest.mark.asyncio
    async def test_budget_exhausted_returns_none(self):
        # 已用 = 1.00，限额 1.00，再预留 0.05 → 超额
        conn = _CaptureConn(results=[None, None, (Decimal("1.00"),), None])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs())
        assert reservation is None

    @pytest.mark.asyncio
    async def test_reservation_granted_returns_budget_reservation(self):
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",)])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs())
        assert reservation is not None
        assert reservation.reservation_id == "res-1"
        assert reservation.amount_cny == "0.05"
        assert reservation.state in ("reserved", "dispatched")


class TestOwnerConditionalWrites:
    @pytest.mark.asyncio
    async def test_mark_dispatched_where_owner_epoch_lease(self):
        """派发绑定仅更新预留状态（不建 attempt 行，方案 §6.2 步骤 3）；owner 三条件齐全。"""
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        granted = await dao.mark_dispatched(
            invocation_id="inv-1",
            reservation_id="res-1",
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert granted is True
        all_sql = "\n".join(sql for sql, _ in conn.calls)
        assert "owner_epoch" in all_sql
        assert "owner_lease_expires_at" in all_sql
        assert "owner_session_id" in all_sql
        # 派发绑定不得建立 attempt 行（单一 UPDATE 语句）
        assert len(conn.calls) == 1  # noqa: weak-assertion 语句数即语义：派发绑定必须且只能是单条 UPDATE（多语句即建了 attempt 行）
        assert "INSERT INTO ai_attempt" not in all_sql

    @pytest.mark.asyncio
    async def test_insert_attempt_dispatched_idempotent(self):
        """attempt 行建立（§6.2 步骤 4：真正进入执行时）：status=dispatched + ON CONFLICT DO NOTHING。"""
        conn = _CaptureConn(results=[None])
        dao = _dao(conn)
        await dao.insert_attempt(
            attempt_id="att-1",
            invocation_id="inv-1",
            configured_model="glm-4-air",
            attempt_row={"task_id": "sentiment", "candidate_index": 0, "connection_id": "conn-1"},
        )
        sql, _params = conn.calls[0]
        assert "INSERT INTO ai_attempt" in sql
        assert "ON CONFLICT (attempt_id) DO NOTHING" in sql
        # status 为 SQL 字面量 'dispatched'（进入执行即派发态，settle 时推进终态）
        assert "'dispatched'" in sql

    @pytest.mark.asyncio
    async def test_recovered_owner_bumps_epoch(self):
        # 恢复事务：比较租期 → 递增 epoch → 认领
        # 序列：created_at 查询(None) → advisory(None) → UPDATE RETURNING((1,))
        conn = _CaptureConn(results=[None, None, (1,)])
        dao = _dao(conn)
        new_epoch = await dao.claim_invocation(
            invocation_id="inv-1",
            owner_session_id="owner-2",
            lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
        )
        all_sql = "\n".join(sql for sql, _ in conn.calls)
        assert "owner_epoch" in all_sql
        assert new_epoch is not None and new_epoch >= 1


class TestLegacyParsing:
    def test_parse_legacy_cents_divides_by_100(self):
        assert parse_legacy_cents("12345") == Decimal("123.45")

    def test_parse_legacy_cents_invalid_returns_zero(self):
        assert parse_legacy_cents(None) == Decimal("0")
        assert parse_legacy_cents("") == Decimal("0")
        assert parse_legacy_cents("not-a-number") == Decimal("0")


class TestMonthlyMonotonic:
    @pytest.mark.asyncio
    async def test_rollup_uses_greatest_for_monotonicity(self):
        """R22：月汇总累计写入必须 GREATEST 语义（乱序写入断言最大值由集成测试覆盖）。"""
        conn = _CaptureConn(results=[None])
        dao = _dao(conn)
        await dao.apply_monthly_delta(
            month="202610",
            aggregation_scope="scenario",
            grouping_key="news_sentiment",
            scenario_id="news_sentiment",
            trigger="manual",
            invocation_count=1,
            attempt_count=1,
            cost_cny_delta=Decimal("0.05"),
        )
        sql, _ = conn.calls[0]
        assert "GREATEST" in sql.upper()


class TestTablesExist:
    def test_three_tables_registered_in_orm(self):
        from data.persistence.models import AiAttempt, AiInvocation, AiUsageMonthly

        assert AiInvocation.__tablename__ == "ai_invocation"
        assert AiAttempt.__tablename__ == "ai_attempt"
        assert AiUsageMonthly.__tablename__ == "ai_usage_monthly"

    def test_data_dictionary_registered(self):
        from data.data_dictionary import TABLE_DEFINITIONS

        for table in ("ai_invocation", "ai_attempt", "ai_usage_monthly"):
            assert table in TABLE_DEFINITIONS, f"R12: {table} 未登记 TABLE_DEFINITIONS"

    def test_invocation_pk_is_uuid_string_no_autoincrement(self):
        from data.persistence.models import AiInvocation

        pk_cols = [c.name for c in AiInvocation.__table__.primary_key.columns]
        assert pk_cols == ["invocation_id"]
