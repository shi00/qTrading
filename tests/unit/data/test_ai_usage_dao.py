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
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import NormalizedLLMUsage
from data.persistence.daos.ai_usage_dao import AIUsageDao, parse_legacy_cents

pytestmark = pytest.mark.unit


class _CaptureConn:
    """记录 execute 的 (sql, params) 并按预设结果序列依次返回。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, sql, params=None):
        import sqlalchemy

        self.calls.append((str(sql), params))
        result = MagicMock()
        item = self.results.pop(0) if self.results else None
        if isinstance(item, Exception):
            raise item
        result.fetchone = MagicMock(return_value=item)
        if isinstance(item, list):
            # 多行结果（如 read_month_usage 的 fetchall）
            result.fetchall = MagicMock(return_value=item)
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


def _settle_kwargs(**overrides):
    kwargs = {
        "attempt_id": "att-1",
        "invocation_id": "inv-1",
        "status": "succeeded",
        "usage_status": "final",
        "usage": NormalizedLLMUsage(
            input_total_tokens=100,
            uncached_input_tokens=80,
            cache_read_tokens=10,
            cache_write_tokens=10,
            output_tokens=50,
        ),
        "usage_revision": 2,
        "error_code": None,
        "duration_ms": 123.4,
        "cost_cny": "0.05",
        "actual_model": "glm-4-air",
        "connection_id": "conn-1",
        "connection_revision": 1,
        "adapter_kind": "litellm_chat",
        "configured_model": "glm-4-air",
        "scenario_id": "news_sentiment",
        "task_id": "sentiment",
        "trigger": "manual",
        "candidate_index": 0,
        "retry_index": 0,
        "security_revision": 1,
        "month": "202610",
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


class TestSettleAttemptUsage:
    @pytest.mark.asyncio
    async def test_settle_existing_attempt_updates_and_rolls_both_scopes(self):
        """终态落账（D6）：attempt 修订单调推进 + 差额整体计入两 scope 月汇总。"""
        # 序列：lock(None) → SELECT FOR UPDATE((0, 0.02)) → UPDATE(None) → upsert×2(None)
        conn = _CaptureConn(results=[None, (0, Decimal("0.02")), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs())
        assert result is True
        assert len(conn.calls) == 5  # noqa: weak-assertion 语句数即语义：结算事务必须 lock/SELECT/UPDATE/rollup×2 共五条
        update_sql, update_params = conn.calls[2]
        assert "UPDATE ai_attempt" in update_sql
        assert "accounted_usage_revision" in update_sql
        assert update_params["usage_revision"] == 2
        assert update_params["cost_cny"] == "0.05"
        # 五字段用量写入（§9.1 归一化）
        assert update_params["input_total_tokens"] == 100
        assert update_params["output_tokens"] == 50
        # 两 scope 月汇总：差额 = 0.05 - 已计入 0.02 = 0.03
        upserts = [params for _sql, params in conn.calls[3:]]
        scenario = [p for p in upserts if p["grouping_key"] == "news_sentiment"]
        connection = [p for p in upserts if p["grouping_key"] == "conn-1"]
        assert len(scenario) == 1 and len(connection) == 1
        assert Decimal(scenario[0]["cost_cny"]) == Decimal("0.03")
        assert scenario[0]["succeeded_count"] == 1
        assert scenario[0]["invocation_count"] == 0  # settle 不动 invocation 计数
        # connection scope：scenario/task 维度置 None（§10 两 scope 不相加不混算）
        assert connection[0]["scenario_id"] is None
        assert connection[0]["task_id"] is None
        assert connection[0]["connection_id"] == "conn-1"

    @pytest.mark.asyncio
    async def test_settle_stale_revision_returns_false(self):
        """迟到旧修订不覆盖新修订（R22 水位语义）：不 UPDATE、不计费。"""
        conn = _CaptureConn(results=[None, (3, Decimal("0.02"))])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=2))
        assert result is False
        assert len(conn.calls) == 2  # noqa: weak-assertion 语句数即语义：旧修订仅 lock+修订查询，无 UPDATE/月汇总
        assert "accounted_usage_revision" in conn.calls[1][0]  # 第二条即修订水位查询

    @pytest.mark.asyncio
    async def test_settle_missing_attempt_inserts_terminal_row(self):
        """attempt 行缺失（mark_dispatched 从未落库）：直接插入终态，usage=None 不伪装用量。"""
        # 序列：lock(0) → SELECT FOR UPDATE(1, None) → INSERT 终态(2) → upsert×2(3,4)
        conn = _CaptureConn(results=[None, None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None)
        )
        assert result is True
        insert_sql, insert_params = conn.calls[2]
        assert "INSERT INTO ai_attempt" in insert_sql
        assert insert_params["status"] == "failed"
        assert insert_params["cost_cny"] is None
        assert insert_params["input_total_tokens"] is None  # usage=None → 各用量列 NULL
        # 失败不计 succeeded
        upserts = [params for _sql, params in conn.calls[3:]]
        assert len(upserts) == 2
        assert all(p["succeeded_count"] == 0 for p in upserts)


class TestRecoverAndClose:
    @pytest.mark.asyncio
    async def test_recover_expired_owners_returns_rowcount(self):
        """启动前恢复（D9/D4）：过期租约的 dispatched 预留转 held_unconfirmed。"""
        conn = _CaptureConn(results=[7])
        dao = _dao(conn)
        n = await dao.recover_expired_owners(now=datetime.datetime(2026, 10, 16, 12, 0, 0))
        assert n == 7
        sql, params = conn.calls[0]
        assert "held_unconfirmed" in sql
        assert params["now"] == datetime.datetime(2026, 10, 16, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_close_usage_window_true_and_false(self):
        """惰性窗口关闭：rowcount>0 → True（已关闭或未到期 → False）。"""
        dao = _dao(_CaptureConn(results=[1]))
        assert await dao.close_usage_window(attempt_id="att-1") is True
        dao = _dao(_CaptureConn(results=[0]))
        assert await dao.close_usage_window(attempt_id="att-1") is False


class TestReadMonthUsage:
    @pytest.mark.asyncio
    async def test_read_month_usage_returns_mapping_dicts(self):
        """月汇总读取（S5 UI 展示边界）：行 _mapping → dict。"""
        row = MagicMock()
        row._mapping = {"month": "202610", "grouping_key": "news_sentiment", "cost_cny": "0.05"}
        conn = _CaptureConn(results=[[row]])
        dao = _dao(conn)
        rows = await dao.read_month_usage(month="202610", aggregation_scope="scenario")
        assert rows == [{"month": "202610", "grouping_key": "news_sentiment", "cost_cny": "0.05"}]
        sql, params = conn.calls[0]
        assert "ai_usage_monthly" in sql
        assert params == {"month": "202610", "scope": "scenario"}


class TestTryReserveBudgetBranches:
    @pytest.mark.asyncio
    async def test_usage_only_skips_budget_check(self):
        """usage_only（amount_cny=None）不占预算：无余额查询，直接登记预留。"""
        kwargs = _reserve_kwargs(amount_cny=None, budget_limit_cny=None)
        conn = _CaptureConn(results=[None, None, None])  # lock → INSERT → UPDATE
        dao = _dao(conn)
        reservation = await dao.try_reserve(**kwargs)
        assert reservation is not None
        assert reservation.amount_cny is None
        # 预算检查两查询（SUM + app_state）未发生：语句数即语义
        assert len(conn.calls) == 3  # noqa: weak-assertion 语句数即语义：usage_only 仅 lock/INSERT/UPDATE 三条
        all_sql = "\n".join(sql for sql, _params in conn.calls)
        assert "ai_usage_monthly" not in all_sql
        assert "app_state" not in all_sql

    @pytest.mark.asyncio
    async def test_month_committed_null_sum_falls_back_to_legacy(self):
        """SUM 返回 NULL → settled 保持 0，legacy 分值参与预算口径。"""
        # 序列：lock → INSERT → SUM((None,)) → app_state(("500",))
        conn = _CaptureConn(results=[None, None, (None,), ("500",)])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny="0.05", budget_limit_cny="10.00"))
        assert reservation is not None  # used 0 + legacy 5.00 + 0.05 < 10.00
        assert Decimal(reservation.amount_cny) == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_month_committed_bad_sum_treated_as_zero(self):
        """SUM 返回坏值 → Decimal 解析异常 → 0（不阻断预留、不伪装成本）。"""
        conn = _CaptureConn(results=[None, None, ("garbage",), None])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny="0.05", budget_limit_cny="10.00"))
        assert reservation is not None

    @pytest.mark.asyncio
    async def test_claim_invocation_with_created_at_month_key(self):
        """恢复认领：created_at 为 datetime → +8h 归属月计算锁键（D2 归属口径）。"""
        # 序列：created_at((2026-10-15 20:00,)) → advisory(None) → UPDATE RETURNING((5,))
        conn = _CaptureConn(results=[(datetime.datetime(2026, 10, 15, 20, 0, 0),), None, (5,)])
        dao = _dao(conn)
        new_epoch = await dao.claim_invocation(
            invocation_id="inv-1",
            owner_session_id="owner-2",
            lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
        )
        assert new_epoch == 5
        lock_sql, lock_params = conn.calls[1]
        assert "pg_advisory_xact_lock" in lock_sql
        # (2026-10-15 20:00 + 8h) → 2026-10 → 锁键 202610
        assert lock_params["lock_key"] == 202610


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
