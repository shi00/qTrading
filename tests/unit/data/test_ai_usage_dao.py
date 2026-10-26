"""S3 AIUsageDao 单测（模型统一.md §9/§10，方案 D1~D4）。

单测层用 capture conn 断言 SQL 关键语义（真实并发/锁序行为由 sidecar PG 集成测试覆盖）：
- try_reserve：pg_advisory_xact_lock 两键（固定命名空间 + YYYYMM）；
- owner 条件写入：变更 SQL 带 owner_session_id / owner_epoch / lease 三条件（D4）；
- legacy 汇总解析：app_state ``ai_cost:{YYYYMM}`` 分 → 元 Decimal（D3）；
- R22：月汇总写入 SQL 用 GREATEST 保持单调；
- 预算不足返回 None（不抛异常、不伪装成功）；
- legacy 封口（S6 D1/D5）：首封冻结基线快照 / 重封幂等同 seal_id / 读接口 / advisory 月锁；
- F23 fencing：owner 失权不返回非持久预留 / 错误预留 ID 零派发 / 失权不建 attempt 行 /
  缺行结算须活跃预留背书 / mark_dispatched 统一月锁写入口。
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


class TestCreateInvocation:
    """F16 父请求建立（create_invocation）：upsert 幂等 + 参数化 + 快照序列化。"""

    @pytest.mark.asyncio
    async def test_create_invocation_upsert_returns_true(self):
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        assert (
            await dao.create_invocation(
                invocation_id="inv-1",
                scenario_id="news_sentiment",
                trigger="manual",
                owner_session_id="owner-1",
                owner_epoch=1,
                owner_lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
                budget_policy_snapshot={"monthly_cny_limit": "50.00"},
            )
            is True
        )
        sql, params = conn.calls[0]
        assert "INSERT INTO ai_invocation" in sql
        assert "ON CONFLICT (invocation_id) DO UPDATE" in sql
        # R4：原生 SQL 命名参数（无 %s / f-string 拼接）
        assert "%s" not in sql
        assert params["invocation_id"] == "inv-1"
        assert params["scenario_id"] == "news_sentiment"
        assert params["trigger"] == "manual"
        assert params["owner_session_id"] == "owner-1"
        assert params["owner_epoch"] == 1
        assert params["owner_lease_expires_at"] == datetime.datetime(2026, 10, 16, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_create_invocation_snapshot_serialized_to_json(self):
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        await dao.create_invocation(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            trigger="manual",
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
            budget_policy_snapshot={"monthly_cny_limit": "50.00"},
        )
        _, params = conn.calls[0]
        assert json.loads(params["budget_policy_snapshot"]) == {"monthly_cny_limit": "50.00"}

    @pytest.mark.asyncio
    async def test_create_invocation_none_snapshot_binds_null(self):
        """usage_only（无政策快照）→ NULL 参数（不伪装政策存在，R21）。"""
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        await dao.create_invocation(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            trigger="manual",
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
            budget_policy_snapshot=None,
        )
        _, params = conn.calls[0]
        assert params["budget_policy_snapshot"] is None

    @pytest.mark.asyncio
    async def test_create_invocation_rowcount_zero_returns_false(self):
        conn = _CaptureConn(results=[0])
        dao = _dao(conn)
        assert (
            await dao.create_invocation(
                invocation_id="inv-1",
                scenario_id="news_sentiment",
                trigger="manual",
                owner_session_id="owner-1",
                owner_epoch=1,
                owner_lease_expires_at=datetime.datetime(2026, 10, 16, 12, 0, 0),
            )
            is False
        )


class TestTryReserveSqlSemantics:
    @pytest.mark.asyncio
    async def test_advisory_lock_two_keys_present(self):
        # 序列：advisory(None) → INSERT invocation(None) → SUM settled((0,)) → app_state(("res-1",))
        #       → SUM in-flight((0,)) → UPDATE(1)
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",), (Decimal("0.00"),), 1])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs())
        assert reservation is not None
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
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",), (Decimal("0.00"),), 1])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs())
        assert reservation is not None
        assert reservation.reservation_id == "res-1"
        assert reservation.amount_cny == "0.05"
        assert reservation.state in ("reserved", "dispatched")

    @pytest.mark.asyncio
    async def test_expired_owner_reserve_returns_none_not_transient_grant(self):
        """F23 fencing：owner 条件 UPDATE rowcount=0（失权）→ None，不返回非持久预留。

        预算检查通过但 invocation 已被新 owner 接管（owner_session_id 不匹配）：
        预留未持久化，必须返回 None——修复前返回预留对象（调用方误以为预留成功）。
        """
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), None, (Decimal("0.00"),), 0])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs())
        assert reservation is None, "失权 owner 的预留 UPDATE rowcount=0 不得返回预留对象"


class TestOwnerConditionalWrites:
    @pytest.mark.asyncio
    async def test_mark_dispatched_where_owner_epoch_lease(self):
        """派发绑定仅更新预留状态（不建 attempt 行，方案 §6.2 步骤 3）；owner 三条件齐全。"""
        # F23 后序列：created_at 查询(None) → advisory(None) → UPDATE(1)
        conn = _CaptureConn(results=[None, None, 1])
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
        # 派发绑定不得建立 attempt 行（月键读 + advisory 锁 + 单条 UPDATE）
        assert len(conn.calls) == 3  # noqa: weak-assertion 语句数即语义：月键/月锁/UPDATE，多语句即建了 attempt 行
        assert "INSERT INTO ai_attempt" not in all_sql

    @pytest.mark.asyncio
    async def test_mark_dispatched_takes_month_lock_and_reservation_guard(self):
        """F23 fencing：mark 先取归属月 advisory 锁（统一 reservations 写入口），
        UPDATE 带 EXISTS 守卫（目标预留存在且 state='reserved'）。"""
        conn = _CaptureConn(results=[None, None, 1])
        dao = _dao(conn)
        await dao.mark_dispatched(
            invocation_id="inv-1",
            reservation_id="res-1",
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert "pg_advisory_xact_lock" in conn.calls[1][0], "mark_dispatched 必须经归属月 advisory 锁"
        update_sql = conn.calls[2][0]
        assert ":reservation_id IS NULL" in update_sql, "usage_only（rid=None）路径仅 owner 条件"
        assert "EXISTS" in update_sql
        assert "e->>'state' = 'reserved'" in update_sql, "错误预留 ID / 非 reserved 状态必须零派发"

    @pytest.mark.asyncio
    async def test_mark_dispatched_usage_only_keeps_empty_reservations(self):
        """usage_only（reservation_id=None）全量重写不得把空 reservations 聚合成 NULL。

        ``jsonb_agg`` 对空集返回 NULL，直写会违反 ``ai_invocation.reservations``
        NOT NULL 约束（usage_only 预留未持久化时的降级派发路径）；
        ``COALESCE(..., '[]'::jsonb)`` 保证空数组原样保留。
        """
        conn = _CaptureConn(results=[None, None, 1])
        dao = _dao(conn)
        granted = await dao.mark_dispatched(
            invocation_id="inv-1",
            reservation_id=None,
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert granted is True
        update_sql = " ".join(conn.calls[2][0].split())
        assert "COALESCE(jsonb_agg(" in update_sql
        assert "'[]'::jsonb" in update_sql

    @pytest.mark.asyncio
    async def test_insert_attempt_dispatched_idempotent(self):
        """attempt 行建立（§6.2 步骤 4：真正进入执行时）：status=dispatched + ON CONFLICT DO NOTHING。"""
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        created = await dao.insert_attempt(
            attempt_id="att-1",
            invocation_id="inv-1",
            configured_model="glm-4-air",
            attempt_row={"task_id": "sentiment", "candidate_index": 0, "connection_id": "conn-1"},
        )
        assert created is True
        sql, params = conn.calls[0]
        assert "INSERT INTO ai_attempt" in sql
        assert "ON CONFLICT (attempt_id) DO NOTHING" in sql
        # status 为 SQL 字面量 'dispatched'（进入执行即派发态，settle 时推进终态）
        assert "'dispatched'" in sql
        # F23：INSERT ... SELECT 门控（invocation 存在 + 租约有效 + owner 门）
        assert "FROM ai_invocation" in sql
        assert "owner_lease_expires_at > (now() AT TIME ZONE 'UTC')" in sql
        assert ":owner_session_id IS NULL" in sql
        assert params["owner_session_id"] is None and params["owner_epoch"] is None

    @pytest.mark.asyncio
    async def test_insert_attempt_owner_strong_gate_params_bound(self):
        """F23 owner 强门：owner_session_id/owner_epoch 齐传时与 invocation 当前 owner 比对。"""
        conn = _CaptureConn(results=[1])
        dao = _dao(conn)
        created = await dao.insert_attempt(
            attempt_id="att-1",
            invocation_id="inv-1",
            configured_model="glm-4-air",
            owner_session_id="owner-1",
            owner_epoch=2,
        )
        assert created is True
        _sql, params = conn.calls[0]
        assert params["owner_session_id"] == "owner-1"
        assert params["owner_epoch"] == 2
        assert "i.owner_session_id = :owner_session_id" in conn.calls[0][0]

    @pytest.mark.asyncio
    async def test_insert_attempt_rowcount_zero_distinguishes_idempotent_from_rejected(self):
        """rowcount=0 时二次查询区分：已存在行 = 幂等沿用 True；不存在 = owner 门拒绝 False。"""
        # 幂等沿用：INSERT 冲突忽略（rowcount=0）→ 二次 SELECT 命中
        conn = _CaptureConn(results=[0, (1,)])
        dao = _dao(conn)
        assert await dao.insert_attempt(attempt_id="att-1", invocation_id="inv-1", configured_model="m") is True
        # owner 门拒绝：INSERT 未命中（rowcount=0）→ 二次 SELECT 无行
        conn2 = _CaptureConn(results=[0, None])
        dao2 = _dao(conn2)
        assert await dao2.insert_attempt(attempt_id="att-2", invocation_id="inv-1", configured_model="m") is False
        followup_sql = conn2.calls[1][0]
        assert "SELECT 1 FROM ai_attempt" in followup_sql

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
        # 序列：lock(None) → SELECT FOR UPDATE((0, 0.02, succeeded, True)) → UPDATE(None) → upsert×2(None)
        conn = _CaptureConn(results=[None, (0, Decimal("0.02"), "succeeded", True), None, None, None])
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
        # F34：accounted=0 首次结算跃迁 → attempt/invocation 各 +1
        assert scenario[0]["attempt_count"] == 1
        assert scenario[0]["invocation_count"] == 1
        # connection scope：scenario/task 维度置 None（§10 两 scope 不相加不混算）；
        # invocation_count 维持 0（一次请求可跨多连接，invocation 语义不混算）
        assert connection[0]["scenario_id"] is None
        assert connection[0]["task_id"] is None
        assert connection[0]["connection_id"] == "conn-1"
        assert connection[0]["invocation_count"] == 0

    @pytest.mark.asyncio
    async def test_settle_stale_revision_returns_false(self):
        """迟到旧修订不覆盖新修订（R22 水位语义）：不 UPDATE、不计费。"""
        conn = _CaptureConn(results=[None, (3, Decimal("0.02"), "succeeded", False)])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=2))
        assert result is False
        assert len(conn.calls) == 2  # noqa: weak-assertion 语句数即语义：旧修订仅 lock+修订查询，无 UPDATE/月汇总
        assert "accounted_usage_revision" in conn.calls[1][0]  # 第二条即修订水位查询

    @pytest.mark.asyncio
    async def test_settle_same_revision_replay_returns_false(self):
        """F18 持久化重放幂等：同一次写账以相同 usage_revision 重放 → 不重复计费。

        外部真实重试是新的 attempt 身份（逐尝试独立行）；同 attempt 的落账重放
        （网络重试 / F23 恢复补记竞态）由修订水位拦截，月汇总零推进。
        """
        # 序列：lock(None) → SELECT FOR UPDATE((2, 0.05, succeeded, False))——已入账修订=本次重放修订
        conn = _CaptureConn(results=[None, (2, Decimal("0.05"), "succeeded", False)])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=2))
        assert result is False
        assert len(conn.calls) == 2  # noqa: weak-assertion 语句数即语义：重放仅 lock+修订查询，无 UPDATE/月汇总
        assert all("ai_usage_monthly" not in sql for sql, _ in conn.calls)

    @pytest.mark.asyncio
    async def test_settle_missing_attempt_without_backing_returns_false(self):
        """F23 fencing：attempt 行缺失且无活跃预留背书（rid 缺省）→ 不插行不计费，返回 False。

        失权 owner 不得凭空建立 attempt 事实；无预留背书的缺行结算拒绝（fail-closed）。
        """
        # 序列：lock(0) → SELECT FOR UPDATE(行缺失) → invocation 首结判定(True) → INSERT...SELECT(0 行，未命中背书)
        conn = _CaptureConn(results=[None, None, (True,), 0])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None)
        )
        assert result is False
        assert len(conn.calls) == 4  # noqa: weak-assertion 语句数即语义：拒绝路径 lock/SELECT/首结判定/门控 INSERT
        insert_sql = conn.calls[3][0]
        assert "INSERT INTO ai_attempt" in insert_sql
        assert ":reservation_id IS NOT NULL" in insert_sql, "缺行补记必须要求活跃预留背书"
        assert "EXISTS" in insert_sql
        assert "jsonb_array_elements(i.reservations)" in insert_sql
        # 无月汇总推进（拒绝即不计费）
        assert all("ai_usage_monthly" not in sql for sql, _ in conn.calls)

    @pytest.mark.asyncio
    async def test_settle_missing_attempt_with_active_reservation_records_usage(self):
        """F23 合法恢复补记：缺行 + 活跃预留背书（rid 命中）→ 插终态行 + 计费 + 预留转换。

        B 认领后持预留 reservation_id 补记实际消费（不盲目拒绝真实迟到事实）。
        """
        # 序列：lock(None) → SELECT FOR UPDATE(None) → 首结判定(True) → INSERT...SELECT(1) → 预留转换(1) → upsert×2
        conn = _CaptureConn(results=[None, None, (True,), 1, 1, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None, reservation_id="res-1")
        )
        assert result is True
        assert len(conn.calls) == 7  # noqa: weak-assertion 语句数即语义：lock/SELECT/首结判定/插行/预留转换/upsert×2
        _insert_sql, insert_params = conn.calls[3]
        assert insert_params["reservation_id"] == "res-1"
        assert set(insert_params["active_states"]) == {"reserved", "dispatched", "held_unconfirmed"}
        trans_sql, trans_params = conn.calls[4]
        assert "UPDATE ai_invocation" in trans_sql
        assert trans_params["settled_attempt_id"] == "att-1"


class TestSettleCountingProjection:
    """F34 计数跃迁投影：attempt/invocation 仅首次结算 +1，迟到修订只更新值；
    succeeded 按状态跃迁差额保持真投影；乱序修订下计数不重复、不回退（R22 用例）。"""

    @pytest.mark.asyncio
    async def test_first_settlement_failed_counts_attempt_not_succeeded(self):
        """首次结算（accounted=0）failed 终态：attempt/invocation +1，succeeded 0。"""
        # 序列：lock → SELECT((0, 0, failed, True)) → UPDATE → upsert×2
        conn = _CaptureConn(results=[None, (0, Decimal("0"), "failed", True), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None)
        )
        assert result is True
        upserts = [params for _sql, params in conn.calls[3:]]
        scenario = [p for p in upserts if p["grouping_key"] == "news_sentiment"][0]
        assert scenario["attempt_count"] == 1
        assert scenario["succeeded_count"] == 0
        assert scenario["invocation_count"] == 1

    @pytest.mark.asyncio
    async def test_late_revision_same_status_never_recounts(self):
        """迟到修订（accounted=2 → revision=3）同状态：只推进成本差额，三计数全 0。"""
        conn = _CaptureConn(results=[None, (2, Decimal("0.02"), "succeeded", False), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=3, cost_cny="0.05"))
        assert result is True
        upserts = [params for _sql, params in conn.calls[3:]]
        scenario = [p for p in upserts if p["grouping_key"] == "news_sentiment"][0]
        # 修正前缺陷：每个新 revision 重复 +1 → 一次调用被计成多次
        assert scenario["attempt_count"] == 0
        assert scenario["succeeded_count"] == 0
        assert scenario["invocation_count"] == 0
        assert Decimal(scenario["cost_cny"]) == Decimal("0.03")  # 值仍更新（差额语义不变）

    @pytest.mark.asyncio
    async def test_late_revision_status_transition_corrects_succeeded_projection(self):
        """迟到修订状态跃迁：failed→succeeded 差额 +1 / succeeded→failed 差额 −1（真投影修正）。"""
        # failed → succeeded：+1
        conn = _CaptureConn(results=[None, (2, Decimal("0.02"), "failed", False), None, None, None])
        dao = _dao(conn)
        await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=3, cost_cny="0.05"))
        upserts = [params for _sql, params in conn.calls[3:]]
        assert all(p["succeeded_count"] == 1 for p in upserts)
        assert all(p["attempt_count"] == 0 and p["invocation_count"] == 0 for p in upserts)
        # succeeded → failed：−1
        conn2 = _CaptureConn(results=[None, (2, Decimal("0.05"), "succeeded", False), None, None, None])
        dao2 = _dao(conn2)
        await dao2.settle_attempt_usage(**_settle_kwargs(status="failed", usage=None, usage_revision=3, cost_cny=None))
        upserts2 = [params for _sql, params in conn2.calls[3:]]
        assert all(p["succeeded_count"] == -1 for p in upserts2)

    @pytest.mark.asyncio
    async def test_out_of_order_revisions_count_exactly_once(self):
        """R22 乱序写入：rev3 先结算（首次跃迁计数），rev2 后到被水位拦截——
        计数恰一次、无第二次月汇总推进、水位不回退。"""
        # 第一次：rev3 首次结算（accounted=0）；第二次调用序列追加在后
        # （lock(None) → SELECT((3, …, False))——首次结算后水位 3，乱序 rev2 被拦截）
        conn = _CaptureConn(
            results=[
                None,
                (0, Decimal("0"), "succeeded", True),
                None,
                None,
                None,
                None,
                (3, Decimal("0.05"), "succeeded", False),
            ]
        )
        dao = _dao(conn)
        first = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=3, cost_cny="0.05"))
        assert first is True
        # 第二次：rev2 乱序迟到 → 旧修订拦截（2 <= 3）
        second = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=2, cost_cny="0.01"))
        assert second is False
        # 仅第一次结算产生月汇总（共 2 条 upsert），scenario scope 计数跃迁恰一次
        upserts = [params for sql, params in conn.calls if "ai_usage_monthly" in sql]
        assert len(upserts) == 2  # noqa: weak-assertion 乱序修订不产生第二次推进
        scenario_upsert = [p for p in upserts if p["grouping_key"] == "news_sentiment"][0]
        assert scenario_upsert["attempt_count"] == 1 and scenario_upsert["invocation_count"] == 1
        assert all(p["usage_revision"] == 3 for p in upserts)  # GREATEST 水位输入为最大修订

    @pytest.mark.asyncio
    async def test_sibling_attempt_settled_skips_invocation_count(self):
        """同 invocation 兄弟 attempt 已结算（invocation_first=False）：仅 attempt +1，
        invocation 不重复计数（F18 逐尝试结算 → 请求数恰一次）。"""
        conn = _CaptureConn(results=[None, (0, Decimal("0"), "succeeded", False), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs())
        assert result is True
        upserts = [params for _sql, params in conn.calls[3:]]
        scenario = [p for p in upserts if p["grouping_key"] == "news_sentiment"][0]
        assert scenario["attempt_count"] == 1
        assert scenario["invocation_count"] == 0

    @pytest.mark.asyncio
    async def test_backfill_with_sibling_settled_keeps_invocation_count_zero(self):
        """F23 缺行补记 + 兄弟 attempt 已结算：补记 attempt 计数 +1，invocation 保持 0。"""
        # 序列：lock → SELECT(None) → 首结判定(False) → INSERT...SELECT(1) → 预留转换(1) → upsert×2
        conn = _CaptureConn(results=[None, None, (False,), 1, 1, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None, reservation_id="res-1")
        )
        assert result is True
        upserts = [params for _sql, params in conn.calls[5:]]
        scenario = [p for p in upserts if p["grouping_key"] == "news_sentiment"][0]
        assert scenario["attempt_count"] == 1
        assert scenario["invocation_count"] == 0


class TestSettleReservationTransition:
    """F22 回归链 1（预留→派发→结算）：结算同事务内预留转 settled，消除双占。

    状态机：reserved → dispatched → settled（正常链）；held_unconfirmed → settled
    （崩溃恢复后结算确认）；转换幂等（已终态 rowcount=0 跳过）。
    """

    @pytest.mark.asyncio
    async def test_settle_with_reservation_converts_active_to_settled(self):
        """带 reservation_id 结算：UPDATE attempt 后、月汇总前，同事务执行预留转换。"""
        # 序列：lock → SELECT FOR UPDATE((0, 0.02, succeeded, True)) → UPDATE attempt → 预留转换 → upsert×2 = 6 条
        conn = _CaptureConn(results=[None, (0, Decimal("0.02"), "succeeded", True), None, 1, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(reservation_id="res-1"))
        assert result is True
        assert len(conn.calls) == 6  # noqa: weak-assertion 语句数即语义：lock/SELECT/UPDATE/预留转换/upsert×2
        trans_sql, trans_params = conn.calls[3]
        assert "UPDATE ai_invocation" in trans_sql
        assert "jsonb_agg" in trans_sql  # 保留其余元素（不删除历史）
        assert "jsonb_build_object" in trans_sql
        assert "'settled'" in trans_sql
        assert "EXISTS" in trans_sql  # 幂等守卫：无活跃目标元素时不改写（防空数组置 NULL）
        assert trans_params["reservation_id"] == "res-1"
        assert trans_params["settled_attempt_id"] == "att-1"
        assert trans_params["invocation_id"] == "inv-1"
        assert set(trans_params["active_states"]) == {"reserved", "dispatched", "held_unconfirmed"}
        assert isinstance(trans_params["settled_at"], str)
        # 转换在月汇总 upsert 之前且同一事务（费用落账与预留退出原子生效）
        assert "ai_usage_monthly" in conn.calls[4][0]
        assert "ai_usage_monthly" in conn.calls[5][0]

    @pytest.mark.asyncio
    async def test_settle_without_reservation_keeps_legacy_flow(self):
        """不带 reservation_id（未接入预留的调用链）：不执行转换，行为与既有一致。"""
        conn = _CaptureConn(results=[None, (0, Decimal("0.02"), "succeeded", True), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs())
        assert result is True
        assert len(conn.calls) == 5  # noqa: weak-assertion 语句数即语义：无预留转换语句
        assert all("UPDATE ai_invocation" not in sql for sql, _ in conn.calls)

    @pytest.mark.asyncio
    async def test_settle_missing_attempt_branch_also_converts_reservation(self):
        """attempt 行缺失（直接插终态）分支同样必须转换预留：费用落账即预留退出。"""
        # 序列：lock → SELECT FOR UPDATE(None) → 首结判定(True) → INSERT...SELECT 门控命中(1) → 预留转换(1) → upsert×2 = 7 条
        conn = _CaptureConn(results=[None, None, (True,), 1, 1, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(
            **_settle_kwargs(status="failed", usage=None, usage_revision=1, cost_cny=None, reservation_id="res-1")
        )
        assert result is True
        assert len(conn.calls) == 7  # noqa: weak-assertion 语句数即语义：insert 分支同样带预留转换
        trans_sql, trans_params = conn.calls[4]
        assert "UPDATE ai_invocation" in trans_sql
        assert trans_params["settled_attempt_id"] == "att-1"
        assert trans_params["reservation_id"] == "res-1"

    @pytest.mark.asyncio
    async def test_stale_revision_skips_reservation_transition(self):
        """旧修订（本次无推进）不触发预留转换：预留已在前次成功结算时转换（幂等）。"""
        conn = _CaptureConn(results=[None, (3, Decimal("0.02"), "succeeded", False)])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(usage_revision=2, reservation_id="res-1"))
        assert result is False
        assert len(conn.calls) == 2  # noqa: weak-assertion 语句数即语义：仅 lock + 修订水位查询
        assert all("UPDATE ai_invocation" not in sql for sql, _ in conn.calls)

    @pytest.mark.asyncio
    async def test_terminal_states_not_in_active_states(self):
        """F22 回归链 3（手动解除）预算面：settled / released 均不占用预算（状态常量）。"""
        from data.persistence.daos.ai_usage_dao import _ACTIVE_RESERVATION_STATES

        assert "settled" not in _ACTIVE_RESERVATION_STATES
        assert "released" not in _ACTIVE_RESERVATION_STATES


class TestActiveReservationsBudgetGate:
    """F22 回归链 2（崩溃→过期恢复）：预算有效占用口径。

    修复前 _read_active_reservations_sum 的行级租约条件把「过期 owner 的
    held_unconfirmed」一并过滤——未确认金额从预算占用中消失；修复后
    held_unconfirmed 分支不受租约约束（OR 形态），reserved / dispatched 仍受约束。
    """

    @pytest.mark.asyncio
    async def test_held_unconfirmed_counts_beyond_expired_lease(self):
        """过期 owner 的 held_unconfirmed 0.80 持续占用：预算 1.00 内再预留 0.50 应拒绝。"""
        # 序列：lock → INSERT → SUM settled((0,)) → legacy app_state(None) → SUM in-flight((0.80,)) → UPDATE(0)
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), None, (Decimal("0.80"),), 0])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny="0.50", budget_limit_cny="1.00"))
        assert reservation is None  # 0.80（未确认持续占用）+ 0.50 > 1.00
        sum_sql = next(sql for sql, _ in conn.calls if "jsonb_array_elements(reservations)" in sql)
        # held_unconfirmed 分支独立于租约条件（OR 形态），过期不失占
        assert "elem->>'state' = 'held_unconfirmed'" in sum_sql
        assert "OR owner_lease_expires_at > (now() AT TIME ZONE 'UTC')" in sum_sql

    @pytest.mark.asyncio
    async def test_active_states_still_lease_gated_in_sum_sql(self):
        """reserved / dispatched 仍受 owner 租约条件约束（D3 语义不扩大）。"""
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), None, (Decimal("0.00"),), 1])
        dao = _dao(conn)
        await dao.try_reserve(**_reserve_kwargs())
        sum_sql = next(sql for sql, _ in conn.calls if "jsonb_array_elements(reservations)" in sql)
        assert "elem->>'state' = ANY(CAST(:states AS text[]))" in sum_sql
        # 活跃状态集合仍由 _ACTIVE_RESERVATION_STATES 参数化（含租约门控的 reserved/dispatched）
        states_sql = [params for sql, params in conn.calls if "jsonb_array_elements(reservations)" in sql][0]
        assert set(states_sql["states"]) == {"reserved", "dispatched", "held_unconfirmed"}


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
        conn = _CaptureConn(results=[None, None, 1])  # lock → INSERT → UPDATE(rowcount=1 持久预留)
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
        # 序列：lock → INSERT → SUM((None,)) → app_state(("500",)) → SUM in-flight((0,)) → UPDATE(1)
        conn = _CaptureConn(results=[None, None, (None,), ("500",), (Decimal("0.00"),), 1])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny="0.05", budget_limit_cny="10.00"))
        assert reservation is not None  # used 0 + legacy 5.00 + 0.05 < 10.00
        assert Decimal(reservation.amount_cny) == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_month_committed_bad_sum_treated_as_zero(self):
        """SUM 返回坏值 → Decimal 解析异常 → 0（不阻断预留、不伪装成本）。"""
        conn = _CaptureConn(results=[None, None, ("garbage",), None, (Decimal("0.00"),), 1])
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
        # 序列：lock(None) → INSERT(None) → SUM 全场景((60.00,)) → app_state(None) → SUM in-flight((0,)) → UPDATE(1)
        conn = _CaptureConn(results=[None, None, (Decimal("60.00"),), None, (Decimal("0.00"),), 1])
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
        # 序列：lock(None) → INSERT(None) → SUM 全场景((90.00,)) → app_state(("500",)) → SUM in-flight((0,)) → UPDATE(1)
        conn = _CaptureConn(results=[None, None, (Decimal("90.00"),), ("500",), (Decimal("0.00"),), 1])
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
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), None, (Decimal("0.00"),), 1])
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
                _StubResult(one=(0, 0, 0, 0)),  # F17 计价完整性
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
                _StubResult(one=(0, 0, 0, 0)),
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
    async def test_pricing_integrity_free_and_unpriced_separate(self):
        """F17 / R21：免费（cost 已知 0）与未计价（cost NULL 未知）分列，
        未计价行给 input/output token 完整性。"""
        conn = _QueueConn(
            [
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=(2, 3, 150, 40)),  # unpriced=2 / free=3 / 未计价 token 完整性
                _StubResult(one=None),
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["unpriced_attempts"] == 2
        assert summary["free_attempts"] == 3
        assert summary["unpriced_input_tokens"] == 150
        assert summary["unpriced_output_tokens"] == 40
        # 完整性查询按归属月过滤（invocation created_at 月界，D2 口径）
        integrity_sql, integrity_params = conn.calls[3]
        assert "ai_attempt" in integrity_sql
        assert "cost_cny IS NULL" in integrity_sql
        assert "cost_cny = 0" in integrity_sql
        assert "month_start" in integrity_params and "next_month_start" in integrity_params

    @pytest.mark.asyncio
    async def test_legacy_cents_included_in_known(self):
        conn = _QueueConn(
            [
                _StubResult(one=(None,)),
                _StubResult(one=(Decimal("0"),)),
                _StubResult(one=(None,)),
                _StubResult(one=(0, 0, 0, 0)),
                _StubResult(one=("500",)),  # legacy 分 → 5.00 元
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["known_cny"] == Decimal("5.00")
        # unconfirmed 查询 SQL 指明 held_unconfirmed
        unconfirmed_sql = conn.calls[2][0]
        assert "held_unconfirmed" in unconfirmed_sql

    @pytest.mark.asyncio
    async def test_effective_column_excludes_held_unconfirmed(self):
        """F23 展示口径（F22 遗留③）：有效预留列仅 owner 在世正常占用两态，
        与「未确认」列（held_unconfirmed 字面量）不相交，同笔金额不重复展示。"""
        conn = _QueueConn(
            [
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=(None,)),
                _StubResult(one=(0, 0, 0, 0)),
                _StubResult(one=None),
            ]
        )
        dao = _dao(conn)
        await dao.read_budget_summary(month="202610")
        effective_sql, effective_params = conn.calls[1]
        assert "owner_lease_expires_at > (now() AT TIME ZONE 'UTC')" in effective_sql
        assert set(effective_params["states"]) == {"reserved", "dispatched"}
        assert "held_unconfirmed" not in effective_params["states"]
        # 未确认列独立查询且不受租约约束（需人工处置，§10.3）
        unconfirmed_sql = conn.calls[2][0]
        assert "held_unconfirmed" in unconfirmed_sql
        assert "owner_lease_expires_at" not in unconfirmed_sql


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


class TestAmountContractD2D3:
    """金额入参契约（R05 D2/D3）：垃圾值契约化 ValueError，先于事务/fencing，独立于预算短路。"""

    @pytest.mark.parametrize(
        ("bad_amount", "expected_msg"),
        [
            ("-5.00", "金额必须为非负数"),
            ("-Infinity", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("Infinity", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("NaN", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("sNaN", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("abc", "金额无法解析为十进制数"),
            ("1e999999", "金额超出允许上限 1e15"),
            ("1,000", "金额无法解析为十进制数"),
        ],
    )
    @pytest.mark.asyncio
    async def test_try_reserve_rejects_garbage_amount_before_transaction(self, bad_amount: str, expected_msg: str):
        """D2：垃圾 amount_cny 入口即拒（ValueError 契约化），零 SQL 副作用（先于 fencing）。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match=f"amount_cny: {expected_msg}") as exc_info:
            await dao.try_reserve(**_reserve_kwargs(amount_cny=bad_amount))
        # 精确异常属性断言：字段名前缀 + validator 正本消息（非裸 ValueError）
        assert "amount_cny" in str(exc_info.value)
        assert conn.calls == []

    @pytest.mark.parametrize(
        "bad_budget",
        ["-1.00", "NaN", "-Infinity", "abc", "1e999999"],
    )
    @pytest.mark.asyncio
    async def test_try_reserve_rejects_garbage_budget_limit(self, bad_budget: str):
        """D2：budget_limit_cny 垃圾同样入口即拒（与 amount 校验对称）。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match="budget_limit_cny"):
            await dao.try_reserve(**_reserve_kwargs(budget_limit_cny=bad_budget))
        assert conn.calls == []

    @pytest.mark.asyncio
    async def test_try_reserve_validates_amount_even_when_budget_is_none(self):
        """D2：校验独立于预算判定短路——budget=None 时垃圾 amount 仍拒（修复前静默入库）。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match="amount_cny: 金额必须为非负数"):
            await dao.try_reserve(**_reserve_kwargs(amount_cny="-5.00", budget_limit_cny=None))
        assert conn.calls == []

    @pytest.mark.asyncio
    async def test_try_reserve_accepts_legal_amount_variants(self):
        """D2 回归：合法 str / Decimal / int / float 全接受，Decimal 归一为规范十进制串。"""
        conn = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",), (Decimal("0.00"),), 1])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny=Decimal("0.05")))
        assert reservation is not None
        assert reservation.amount_cny == "0.05"  # Decimal → 最短往返十进制串，无噪音

        conn2 = _CaptureConn(results=[None, None, (Decimal("0.00"),), ("res-1",), (Decimal("0.00"),), 1])
        dao2 = _dao(conn2)
        reservation2 = await dao2.try_reserve(**_reserve_kwargs(amount_cny="0.05"))
        assert reservation2 is not None
        assert reservation2.amount_cny == "0.05"

    @pytest.mark.asyncio
    async def test_try_reserve_usage_only_none_amount_passes(self):
        """D2：None 直通（usage_only 无金额语义），不触发校验。"""
        conn = _CaptureConn(results=[None, None, 1])
        dao = _dao(conn)
        reservation = await dao.try_reserve(**_reserve_kwargs(amount_cny=None, budget_limit_cny=None))
        assert reservation is not None
        assert reservation.amount_cny is None

    @pytest.mark.parametrize(
        ("bad_cost", "expected_msg"),
        [
            ("-0.01", "金额必须为非负数"),
            ("NaN", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("-Infinity", "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            ("bad", "金额无法解析为十进制数"),
            ("1e999999", "金额超出允许上限 1e15"),
        ],
    )
    @pytest.mark.asyncio
    async def test_settle_rejects_garbage_cost_before_transaction(self, bad_cost: str, expected_msg: str):
        """D3：垃圾 cost_cny 入口即拒（先于事务与 F23 背书判定），零 SQL 副作用。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match=f"cost_cny: {expected_msg}"):
            await dao.settle_attempt_usage(**_settle_kwargs(cost_cny=bad_cost))
        assert conn.calls == []

    @pytest.mark.asyncio
    async def test_settle_normalizes_float_cost_to_shortest_decimal_string(self):
        """相邻移交：float cost（pricing.estimate_cost 链路）归一为最短往返串入 SQL，
        修复前 Decimal(float) 二进制全长展开入 numeric 列（精度噪音）。"""
        conn = _CaptureConn(results=[None, (0, Decimal("0.02"), "succeeded", True), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(cost_cny=0.05))
        assert result is True
        update_sql, update_params = conn.calls[2]
        assert "UPDATE ai_attempt" in update_sql
        assert update_params["cost_cny"] == "0.05"  # str(0.05) 最短往返，非 0.05000000000000000277...
        upserts = [params for _sql, params in conn.calls[3:]]
        assert all(Decimal(p["cost_cny"]) == Decimal("0.03") for p in upserts)  # 差额 0.05-0.02

    @pytest.mark.asyncio
    async def test_settle_late_revision_negative_delta_still_accounts(self):
        """迟到修订合法通道保持：修订成本降低（0.05 → 0.03）产生负差额，
        必须照常推进月汇总（负 delta 仅拒绝于 apply_monthly_delta 公开入口，
        settle 内部差额不受 D3 入参校验影响）。"""
        conn = _CaptureConn(results=[None, (1, Decimal("0.05"), "succeeded", False), None, None, None])
        dao = _dao(conn)
        result = await dao.settle_attempt_usage(**_settle_kwargs(cost_cny="0.03", usage_revision=2))
        assert result is True
        upserts = [params for _sql, params in conn.calls[3:]]
        assert len(upserts) == 2  # noqa: weak-assertion 语句数即语义：两 scope 各推进一次
        assert all(Decimal(p["cost_cny"]) == Decimal("-0.02") for p in upserts)

    @pytest.mark.parametrize(
        ("bad_delta", "expected_msg"),
        [
            (Decimal("-0.01"), "金额必须为非负数"),
            (Decimal("NaN"), "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            (Decimal("-Infinity"), "金额必须为有限十进制数（禁止 NaN / Infinity）"),
            (Decimal("1e999999"), "金额超出允许上限 1e15"),
        ],
    )
    @pytest.mark.asyncio
    async def test_apply_monthly_delta_rejects_garbage_delta(self, bad_delta: Decimal, expected_msg: str):
        """D3：公开入口拒负/非有限/超上界差额（负 delta 不得直接减已结算费用）。"""
        conn = _CaptureConn(results=[])
        dao = _dao(conn)
        with pytest.raises(ValueError, match=f"cost_cny_delta: {expected_msg}"):
            await dao.apply_monthly_delta(
                month="202610",
                aggregation_scope="scenario",
                grouping_key="news_sentiment",
                scenario_id="news_sentiment",
                trigger="manual",
                invocation_count=1,
                attempt_count=1,
                cost_cny_delta=bad_delta,
            )
        assert conn.calls == []


class TestLegacyParseObservabilityD4:
    """legacy 值解析（R05 D4）：NaN / ±Infinity 回零 + 坏值降级 warning 可观测。"""

    @pytest.mark.parametrize("raw", ["NaN", "sNaN", "-Infinity", "Infinity"])
    def test_parse_legacy_cents_non_finite_returns_zero(self, raw: str):
        """D4：非有限值回零（修复前 'NaN' 解析为 NaN 毒化预算摘要）。"""
        assert parse_legacy_cents(raw) == Decimal("0")

    def test_parse_legacy_cents_huge_exponent_returns_zero(self):
        """巨数串解析成功但 quantize 精度溢出 → 回零（不抛）。"""
        assert parse_legacy_cents("1e999") == Decimal("0")

    def test_parse_legacy_cents_legal_values_unchanged(self):
        """D4 回归：合法分值解析不受影响。"""
        assert parse_legacy_cents("150") == Decimal("1.50")
        assert parse_legacy_cents(150) == Decimal("1.50")
        assert parse_legacy_cents("12345") == Decimal("123.45")
        assert parse_legacy_cents(None) == Decimal("0")

    @pytest.mark.parametrize("raw", ["not-a-number", "NaN", "1e999"])
    def test_parse_legacy_cents_degradation_logged(self, raw: str, caplog: pytest.LogCaptureFixture):
        """D4：坏值回零必须 warning 可观测（修复前完全静默）。None 缺键不告警。"""
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="data.persistence.daos.ai_usage_dao"):
            assert parse_legacy_cents(raw) == Decimal("0")
        assert any("legacy 月成本值" in r.message and r.levelno == _logging.WARNING for r in caplog.records)

    def test_parse_legacy_cents_none_no_warning(self, caplog: pytest.LogCaptureFixture):
        """None（缺键）是「无数据」非坏值：回零但不告警。"""
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="data.persistence.daos.ai_usage_dao"):
            assert parse_legacy_cents(None) == Decimal("0")
        assert not any("legacy 月成本值" in r.message for r in caplog.records)


class TestDecimalOrZeroD5:
    """预算摘要读数（R05 D5）：NaN / ±Infinity / 负值 → 事实零。"""

    def test_nan_row_returns_zero(self):
        from data.persistence.daos.ai_usage_dao import _decimal_or_zero

        assert _decimal_or_zero((Decimal("NaN"),)) == Decimal("0")

    def test_infinity_row_returns_zero(self):
        from data.persistence.daos.ai_usage_dao import _decimal_or_zero

        assert _decimal_or_zero((Decimal("-Infinity"),)) == Decimal("0")
        assert _decimal_or_zero((Decimal("Infinity"),)) == Decimal("0")

    def test_negative_row_returns_zero(self):
        from data.persistence.daos.ai_usage_dao import _decimal_or_zero

        assert _decimal_or_zero((Decimal("-5.00"),)) == Decimal("0")

    def test_none_and_legal_rows_unchanged(self):
        from data.persistence.daos.ai_usage_dao import _decimal_or_zero

        assert _decimal_or_zero(None) == Decimal("0")
        assert _decimal_or_zero((None,)) == Decimal("0")
        assert _decimal_or_zero((Decimal("1.25"),)) == Decimal("1.25")

    def test_degradation_logged(self, caplog: pytest.LogCaptureFixture):
        import logging as _logging

        from data.persistence.daos.ai_usage_dao import _decimal_or_zero

        with caplog.at_level(_logging.WARNING, logger="data.persistence.daos.ai_usage_dao"):
            _decimal_or_zero((Decimal("NaN"),))
        assert any("按事实零降级" in r.message for r in caplog.records)


class TestStaleGarbageTolerance:
    """存量垃圾读数容错（R05 D2 存量闭环）：D1 validator 生效前写入的历史 jsonb
    垃圾预留 / D3 生效前历史负 delta，SUM 读数非有限/负 → 事实零 + warning。"""

    @pytest.mark.asyncio
    async def test_active_reservations_nan_sum_clamped_to_zero(self, caplog: pytest.LogCaptureFixture):
        """历史 jsonb 'NaN' 预留经 ::numeric 入 SUM 传播 → NaN：预算门按 0 降级，不 crash。"""
        import logging as _logging

        conn = _CaptureConn(results=[(Decimal("NaN"),)])
        dao = _dao(conn)
        with caplog.at_level(_logging.WARNING, logger="data.persistence.daos.ai_usage_dao"):
            got = await dao._read_active_reservations_sum(conn, "202610")
        assert got == Decimal("0")
        assert any("active_reservations_sum" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_active_reservations_negative_sum_clamped_to_zero(self):
        """历史负金额预留（降低 SUM 占用）→ clamp 0（负占用无事实意义）。"""
        conn = _CaptureConn(results=[(Decimal("-5.00"),)])
        got = await _dao(conn)._read_active_reservations_sum(conn, "202610")
        assert got == Decimal("0")

    @pytest.mark.asyncio
    async def test_active_reservations_legal_sum_unchanged(self):
        """D2 回归：合法 SUM 读数不受容错影响。"""
        conn = _CaptureConn(results=[(Decimal("0.75"),)])
        got = await _dao(conn)._read_active_reservations_sum(conn, "202610")
        assert got == Decimal("0.75")

    @pytest.mark.asyncio
    async def test_committed_usage_negative_sum_clamped_to_zero(self, caplog: pytest.LogCaptureFixture):
        """D3 存量闭环：历史负 delta 毒化的已结算 SUM → clamp 0 + warning。"""
        import logging as _logging

        conn = _CaptureConn(results=[(Decimal("-1.00"),), None])
        dao = _dao(conn)
        with caplog.at_level(_logging.WARNING, logger="data.persistence.daos.ai_usage_dao"):
            got = await dao._read_month_committed_usage(conn, "202610")
        assert got == Decimal("0")  # settled clamp 0 + legacy 缺键 0
        assert any("month_committed_usage" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_budget_summary_known_garbage_settled_clamped(self):
        """D5 存量闭环：known_cny 的 settled SUM 垃圾 → 事实零（legacy 合法部分保留）。"""
        conn = _QueueConn(
            [
                _StubResult(one=(Decimal("NaN"),)),  # known settled SUM（垃圾）
                _StubResult(one=(None,)),  # effective reserved
                _StubResult(one=(None,)),  # unconfirmed reserved
                _StubResult(one=(0, 0, 0, 0)),  # F17 计价完整性
                _StubResult(one=("150",)),  # legacy（合法，1.50 元）
            ]
        )
        dao = _dao(conn)
        summary = await dao.read_budget_summary(month="202610")
        assert summary["known_cny"] == Decimal("1.50")  # settled 降级 0 + legacy 1.50
