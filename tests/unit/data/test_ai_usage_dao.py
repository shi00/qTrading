"""S3 AIUsageDao 单测（模型统一.md §9/§10，方案 D1~D4）。

单测层用 capture conn 断言 SQL 关键语义（真实并发/锁序行为由 sidecar PG 集成测试覆盖）：
- try_reserve：pg_advisory_xact_lock 两键（固定命名空间 + YYYYMM）；
- owner 条件写入：变更 SQL 带 owner_session_id / owner_epoch / lease 三条件（D4）；
- legacy 汇总解析：app_state ``ai_cost:{YYYYMM}`` 分 → 元 Decimal（D3）；
- R22：月汇总写入 SQL 用 GREATEST 保持单调；
- 预算不足返回 None（不抛异常、不伪装成功）；
- legacy 封口（S6 D1/D5）：首封冻结基线快照 / 重封幂等同 seal_id / 读接口 / advisory 月锁。
"""

import datetime
import json
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


class TestTryReserveBudgetAllScopeAggregation:
    """F21 回归：月度预算为全部场景与测试请求共享，预算判定必须合计全部场景 scope。

    检视报告 F21 触发用例：限额 100，场景 A 已结算 90、无残余预留，
    场景 B 再预留 20——修复前只查 B 自身 grouping_key（漏掉 A 的 90）仍获准，
    修复后同月锁内合计全部场景 scope 应拒绝。
    """

    @pytest.mark.asyncio
    async def test_reserve_blocked_by_other_scenario_settled_usage(self):
        """场景 A 已结算 90，场景 B 预留 20、限额 100 → 跨场景合计后拒绝。"""
        # 序列：lock(None) → INSERT(None) → SUM 全场景((90.00,)) → app_state(None)
        conn = _CaptureConn(results=[None, None, (Decimal("90.00"),), None])
        dao = _dao(conn)
        reservation = await dao.try_reserve(
            **_reserve_kwargs(scenario_id="news_risk", amount_cny="20", budget_limit_cny="100")
        )
        assert reservation is None

    @pytest.mark.asyncio
    async def test_reserve_granted_when_all_scenarios_within_limit(self):
        """三场景已结算合计 60，当前场景再预留 30、限额 100 → 总口径内获准。"""
        # 序列：lock(None) → INSERT(None) → SUM 全场景((60.00,)) → app_state(None)
        conn = _CaptureConn(results=[None, None, (Decimal("60.00"),), None])
        dao = _dao(conn)
        reservation = await dao.try_reserve(
            **_reserve_kwargs(scenario_id="news_sentiment", amount_cny="30", budget_limit_cny="100")
        )
        assert reservation is not None

    @pytest.mark.asyncio
    async def test_legacy_baseline_added_once_across_scenarios(self):
        """legacy 基线只加一次：settled 90 + legacy 5.00 + 预留 4 = 99 ≤ 100 → 获准。

        若 legacy 被按场景重复计入（N 次），90 + 5N + 4 > 100（N≥2）即拒绝——
        获准本身证明 legacy 恰加一次；再断言 app_state 查询恰好 1 次。
        """
        # 序列：lock(None) → INSERT(None) → SUM 全场景((90.00,)) → app_state(("500",))
        conn = _CaptureConn(results=[None, None, (Decimal("90.00"),), ("500",)])
        dao = _dao(conn)
        reservation = await dao.try_reserve(
            **_reserve_kwargs(scenario_id="news_risk", amount_cny="4", budget_limit_cny="100")
        )
        assert reservation is not None
        assert reservation.amount_cny == "4"
        app_state_calls = [sql for sql, _ in conn.calls if "app_state" in sql]
        assert len(app_state_calls) == 1  # noqa: weak-assertion legacy 基线只读一次（多读即按场景重复计入）

    @pytest.mark.asyncio
    async def test_budget_sum_sql_has_no_grouping_key_filter(self):
        """预算 SUM 查询不得按当前场景 grouping_key 过滤（params 仅 month/scope）。"""
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), None])
        dao = _dao(conn)
        await dao.try_reserve(**_reserve_kwargs())
        sum_calls = [(sql, params) for sql, params in conn.calls if "ai_usage_monthly" in sql]
        assert len(sum_calls) == 1  # noqa: weak-assertion 预算口径恰一条月汇总查询
        sum_sql, sum_params = sum_calls[0]
        assert "aggregation_scope" in sum_sql
        assert "grouping_key" not in sum_sql
        assert sum_params == {"month": "202610", "scope": "scenario"}


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


class _StubResult:
    """读取结果桩：分别支持 ``.mappings().fetchall()`` 与 ``.fetchone()``。"""

    def __init__(self, *, rows=None, one=None):
        self._rows = list(rows or [])
        self._one = one

    def mappings(self):
        m = MagicMock()
        m.fetchall = MagicMock(return_value=list(self._rows))
        return m

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one


class _QueueConn:
    """按预设结果序列依次返回的读连接桩。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        return self.results.pop(0)


class TestReadRequestPage:
    @pytest.mark.asyncio
    async def test_returns_rows_and_total(self):
        """read_request_page：映射行 + 总数计数，SQL 参数化 limit/offset。"""
        rows = [
            {
                "invocation_id": "inv-1",
                "scenario_id": "news_sentiment",
                "terminal_status": "succeeded",
                "cost_status": "known",
            }
        ]
        conn = _QueueConn([_StubResult(rows=rows), _StubResult(one=(3,))])
        dao = _dao(conn)
        records, total = await dao.read_request_page(scenario_id="news_sentiment", limit=50, offset=0)
        assert records == [
            {
                "invocation_id": "inv-1",
                "scenario_id": "news_sentiment",
                "terminal_status": "succeeded",
                "cost_status": "known",
            }
        ]
        assert total == 3
        page_sql, page_params = conn.calls[0]
        assert "ai_invocation" in page_sql
        assert "ai_attempt" in page_sql
        assert "LIMIT :limit OFFSET :offset" in page_sql
        assert page_params["scenario_id"] == "news_sentiment"
        assert page_params["limit"] == 50 and page_params["offset"] == 0
        count_sql, _count_params = conn.calls[1]
        assert "COUNT(" in count_sql.upper()
        assert "ai_invocation" in count_sql

    @pytest.mark.asyncio
    async def test_connection_filter_uses_exists_to_dedupe_requests(self):
        """连接过滤经 EXISTS 子查询 → 一个请求一行，天然去重（§11.5）。"""
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        await dao.read_request_page(connection_id="conn-1")
        page_sql, params = conn.calls[0]
        assert "EXISTS" in page_sql.upper()
        assert params["connection_id"] == "conn-1"

    @pytest.mark.asyncio
    async def test_origin_filter_uses_test_operation_id(self):
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        await dao.read_request_page(origin="test")
        assert "p.is_test = TRUE" in conn.calls[0][0]
        conn2 = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao2 = _dao(conn2)
        await dao2.read_request_page(origin="business")
        assert "p.is_test = FALSE" in conn2.calls[0][0]

    @pytest.mark.asyncio
    async def test_unknown_origin_raises(self):
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        with pytest.raises(ValueError, match="unknown origin filter"):
            await dao.read_request_page(origin="bogus")

    @pytest.mark.asyncio
    async def test_terminal_and_cost_status_filters_bound(self):
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        await dao.read_request_page(terminal_status="failed", cost_status="known")
        page_sql, params = conn.calls[0]
        assert "terminal_status" in page_sql
        assert "cost_status" in page_sql
        assert params["terminal_status"] == "failed"
        assert params["cost_status"] == "known"

    @pytest.mark.asyncio
    async def test_derived_status_columns_filtered_via_wrapped_alias(self):
        """派生列（terminal_status / cost_status）过滤必须经外层别名 ``p`` 引用。

        回归：PostgreSQL 不允许在 ``WHERE`` 引用 SELECT 输出列别名，直接写
        ``WHERE terminal_status = :x`` 在真实 PG 上会报 ``column ... does not exist``；
        故 SQL 必须包裹派生表 ``p`` 并以 ``p.terminal_status`` 引用。count 语句同构。
        """
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        await dao.read_request_page(terminal_status="failed", cost_status="known")
        page_sql, _ = conn.calls[0]
        count_sql, _ = conn.calls[1]
        assert "AS p" in page_sql
        assert "p.terminal_status = :terminal_status" in page_sql
        assert "p.cost_status = :cost_status" in page_sql
        assert "ORDER BY p.created_at DESC" in page_sql
        assert "AS p" in count_sql
        # 派生列不得以裸列名 / 旧内层别名出现在 WHERE 中（防回归到 PG 非法形态）
        assert " WHERE terminal_status" not in page_sql
        assert " WHERE cost_status" not in page_sql
        assert "r.terminal_status" not in page_sql
        # 子任务过滤经基表（不再依赖已不暴露的 p.candidate_decisions）
        task_conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        await _dao(task_conn).read_request_page(task_id="sentiment")
        task_sql, _ = task_conn.calls[0]
        assert "ai_invocation ti" in task_sql
        assert "p.candidate_decisions" not in task_sql

    @pytest.mark.asyncio
    async def test_date_range_filters_bound(self):
        conn = _QueueConn([_StubResult(rows=[]), _StubResult(one=(0,))])
        dao = _dao(conn)
        start = datetime.datetime(2026, 10, 1)
        end = datetime.datetime(2026, 11, 1)
        await dao.read_request_page(start=start, end=end)
        _sql, params = conn.calls[0]
        assert params["start"] == start and params["end"] == end


class TestReadRequestAttempts:
    @pytest.mark.asyncio
    async def test_merges_attempts_and_skipped_decisions(self):
        """有序尝试 + candidate_decisions 跳过行（未执行，不返回虚构用量）。"""
        attempts = [
            {
                "attempt_id": "att-1",
                "task_id": "sentiment",
                "candidate_index": 0,
                "retry_index": 0,
                "connection_id": "conn-1",
                "status": "failed",
                "error_code": "timeout",
                "actual_model": None,
                "configured_model": "glm-4",
                "duration_ms": 120.0,
                "cost_cny": None,
                "usage_status": "unavailable",
                "input_total_tokens": None,
                "output_tokens": None,
            }
        ]
        decisions = [
            {
                "task_id": "sentiment",
                "candidate_index": "1",
                "retry_index": "0",
                "connection_id": "conn-2",
                "reason": "budget",
            }
        ]
        conn = _QueueConn([_StubResult(rows=attempts), _StubResult(rows=decisions)])
        dao = _dao(conn)
        out = await dao.read_request_attempts(invocation_id="inv-1")
        assert len(out) == 2
        kinds = {r["kind"] for r in out}
        assert kinds == {"attempt", "skipped"}
        skipped = next(r for r in out if r["kind"] == "skipped")
        assert skipped["executed"] is False
        assert skipped["status"] == "skipped"
        assert skipped["reason"] == "budget"
        assert skipped["cost_cny"] is None
        assert skipped["input_total_tokens"] is None
        executed = next(r for r in out if r["kind"] == "attempt")
        assert executed["executed"] is True
        assert executed["status"] == "failed"
        assert executed["reason"] == "timeout"
        # 跳过行读取 candidate_decisions
        decision_sql, _params = conn.calls[1]
        assert "candidate_decisions" in decision_sql
        assert "'skipped'" in decision_sql


class TestReadBudgetSummary:
    @pytest.mark.asyncio
    async def test_three_amounts_are_separate(self):
        """已知费用 / 有效预留 / 未确认预留三者分开（不混算）。"""
        conn = _QueueConn(
            [
                _StubResult(one=(Decimal("0.05"),)),  # 已知费用（月汇总 scenario scope）
                _StubResult(one=(Decimal("0.10"),)),  # 有效预留
                _StubResult(one=(Decimal("0.02"),)),  # 未确认预留
                _StubResult(one=None),  # legacy app_state 无值
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["known_cny"] == Decimal("0.05")
        assert summary["effective_reserved_cny"] == Decimal("0.10")
        assert summary["unconfirmed_reserved_cny"] == Decimal("0.02")
        assert summary["month"] == "202610"

    @pytest.mark.asyncio
    async def test_known_cost_unknown_returns_none_not_zero(self):
        """未知费用用 None，禁伪零（R21）。"""
        conn = _QueueConn(
            [
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=None),
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["known_cny"] is None
        # 无有效预留时数值 0 是事实（非未知）
        assert summary["effective_reserved_cny"] == Decimal("0")
        assert summary["unconfirmed_reserved_cny"] == Decimal("0")

    @pytest.mark.asyncio
    async def test_legacy_cents_included_in_known(self):
        conn = _QueueConn(
            [
                _StubResult(one=(None,)),
                _StubResult(one=(Decimal("0"),)),
                _StubResult(one=(None,)),
                _StubResult(one=("500",)),  # legacy 分 → 5.00 元
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["known_cny"] == Decimal("5.00")
        # unconfirmed 查询 SQL 指明 held_unconfirmed
        unconfirmed_sql = conn.calls[2][0]
        assert "held_unconfirmed" in unconfirmed_sql


class TestReleaseReservation:
    """解除未确认预留（§11.5）：held_unconfirmed → released，保留历史不删除元素。"""

    @pytest.mark.asyncio
    async def test_release_changes_state_to_released_and_returns_true(self):
        created = datetime.datetime(2026, 10, 16, 12, 0, 0)
        conn = _CaptureConn(results=[("inv-1",), (created,), None, 1])
        dao = _dao(conn)
        released = await dao.release_reservation(reservation_id="res-1")
        assert released is True
        sql, params = conn.calls[-1]
        assert "UPDATE ai_invocation" in sql
        assert "jsonb_build_object" in sql
        assert "'released'" in sql  # jsonb_build_object 写字符串值 '"released"' 的文本形态
        # 只改写 held_unconfirmed 元素（非删除：jsonb_agg 保留全部元素）
        assert "held_unconfirmed" in sql
        assert "jsonb_agg" in sql
        assert params["reservation_id"] == "res-1"
        assert params["invocation_id"] == "inv-1"
        assert isinstance(params["released_at"], str)
        # 锁序（§9.2 / §10.2）：先定位 holder → 归属月 advisory lock → 条件 UPDATE
        assert "SELECT invocation_id FROM ai_invocation" in conn.calls[0][0]
        assert conn.calls[0][1] == {"reservation_id": "res-1"}
        assert "pg_advisory_xact_lock" in conn.calls[2][0]

    @pytest.mark.asyncio
    async def test_release_not_held_returns_false(self):
        """找不到该 reservation 或状态非 held_unconfirmed → holder 为 None → False（不抛）。"""
        dao = _dao(_CaptureConn(results=[None]))
        assert await dao.release_reservation(reservation_id="res-missing") is False

    @pytest.mark.asyncio
    async def test_release_engine_disposed_raises(self, monkeypatch):
        """R5：入口 _check_engine 守卫（引擎已释放时抛 EngineDisposedError，不执行写）。"""
        from data.persistence import engine_provider
        from data.persistence.daos.base_dao import EngineDisposedError

        monkeypatch.setattr(engine_provider, "is_disposed", lambda _engine: True)
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await dao.release_reservation(reservation_id="res-1")
        assert conn.calls == []  # 守卫先于任何 SQL 执行


class TestReadUnconfirmedReservations:
    """未确认预留明细读取（§11.5「未确认详情」；§10.3 全局跨月可解除）。"""

    @pytest.mark.asyncio
    async def test_returns_unconfirmed_rows(self):
        rows = [{"reservation_id": "res-1", "task_id": "sentiment", "amount_cny": "0.02", "billing_month": "2026-10"}]
        conn = _QueueConn([_StubResult(rows=rows)])
        dao = _dao(conn)
        out = await dao.read_unconfirmed_reservations()
        assert out == rows
        sql, params = conn.calls[0]
        assert "ai_invocation" in sql
        assert "held_unconfirmed" in sql
        assert "reservation_id" in sql
        assert "billing_month" in sql
        # 全局查询无参数绑定：不按当前月过滤，历史遗留预留可解除（§10.3）
        assert params is None

    @pytest.mark.asyncio
    async def test_no_unconfirmed_returns_empty(self):
        dao = _dao(_QueueConn([_StubResult(rows=[])]))
        assert await dao.read_unconfirmed_reservations() == []


class TestLegacySeal:
    """legacy 封口（S6 D1/D5）：AppState JSON 行，幂等 + advisory 月锁内完成。"""

    @pytest.mark.asyncio
    async def test_first_seal_freezes_baseline_snapshot(self):
        """首封：分→元 Decimal 字符串、unpriced 缺键=0、owner 身份与版本就位。"""
        # 序列：advisory → 封口键读(None) → ai_cost(("1234",)) → calls(None) → tokens(("78",)) → INSERT
        conn = _CaptureConn(results=[None, None, ("1234",), None, ("78",), None])
        dao = _dao(conn)
        record = await dao.seal_legacy_month(month="2026-10", owner_session_id="owner-1", owner_epoch=3)
        assert record is not None
        assert record["month"] == "2026-10"
        assert record["cost_cny"] == "12.34"  # 1234 分 → 12.34 元
        assert record["unpriced_calls"] == 0  # 缺键 = 0
        assert record["unpriced_tokens"] == 78
        assert record["last_writer_session_id"] == "owner-1"
        assert record["last_writer_epoch"] == 3
        assert record["seal_version"] == 1
        assert record["seal_id"]
        assert record["sealed_at"]
        # advisory 月锁两键：namespace 常量 + YYYYMM 锁键
        lock_sql, lock_params = conn.calls[0]
        assert "pg_advisory_xact_lock" in lock_sql
        assert lock_params["lock_key"] == 202610
        # 封口行 key 归一 YYYYMM，JSON 内容与返回记录一致
        insert_sql, insert_params = conn.calls[-1]
        assert "INSERT INTO app_state" in insert_sql
        assert insert_params["key"] == "ai_legacy_seal:202610"
        assert json.loads(insert_params["value"]) == record

    @pytest.mark.asyncio
    async def test_reseal_returns_existing_record_idempotent(self):
        """重封幂等：已有封口行直接返回既有记录（seal_id 不变），不重写。"""
        existing = {"seal_id": "seal-fixed", "month": "2026-10", "cost_cny": "1.00", "seal_version": 1}
        # 序列：advisory → 封口键读(命中既有 JSON)；fetchone 返回行元组，值在 row[0]
        conn = _CaptureConn(results=[None, (json.dumps(existing),)])
        dao = _dao(conn)
        record = await dao.seal_legacy_month(month="202610", owner_session_id="owner-2", owner_epoch=9)
        assert record == existing
        # 幂等路径仅锁 + 读两条语句，无 INSERT
        assert len(conn.calls) == 2  # noqa: weak-assertion 语句数即语义：幂等仅锁+读，无写入
        assert all("INSERT INTO app_state" not in sql for sql, _ in conn.calls)

    @pytest.mark.asyncio
    async def test_read_legacy_seal_none_and_record(self):
        """读接口：缺行 → None；有行 → 记录 dict；key 归一 YYYYMM。"""
        conn = _QueueConn([_StubResult(one=None)])
        assert await _dao(conn).read_legacy_seal(month="2026-10") is None
        record = {"seal_id": "s-1", "month": "2026-10", "cost_cny": "0.00"}
        conn2 = _QueueConn([_StubResult(one=(json.dumps(record),))])
        assert await _dao(conn2).read_legacy_seal(month="2026-10") == record
        assert conn2.calls[0][1]["key"] == "ai_legacy_seal:202610"

    @pytest.mark.asyncio
    async def test_is_month_sealed_true_and_false(self):
        """封口判定：存在封口行即 True（不解析值），缺行 False。"""
        conn = _QueueConn([_StubResult(one=None)])
        assert await _dao(conn).is_month_sealed(month="2026-10") is False
        conn2 = _QueueConn([_StubResult(one=('{"seal_id": "s"}',))])
        assert await _dao(conn2).is_month_sealed(month="202610") is True

    @pytest.mark.asyncio
    async def test_invalid_month_raises_value_error_before_sql(self):
        """month 非法（非 YYYY-MM / YYYYMM）→ ValueError，且先于任何 SQL。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match="month 格式"):
            await dao.seal_legacy_month(month="2026-1", owner_session_id="o", owner_epoch=1)
        with pytest.raises(ValueError, match="month 格式"):
            await dao.read_legacy_seal(month="bad")
        with pytest.raises(ValueError, match="month 格式"):
            await dao.is_month_sealed(month="2026-13")
        assert conn.calls == []

    @pytest.mark.asyncio
    async def test_seal_engine_disposed_raises(self, monkeypatch):
        """R5：入口 _check_engine 守卫（引擎已释放时抛 EngineDisposedError，不执行写）。"""
        from data.persistence import engine_provider
        from data.persistence.daos.base_dao import EngineDisposedError

        monkeypatch.setattr(engine_provider, "is_disposed", lambda _engine: True)
        conn = _CaptureConn(results=[None])
        dao = _dao(conn)
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await dao.seal_legacy_month(month="2026-10", owner_session_id="o", owner_epoch=1)
        assert conn.calls == []
