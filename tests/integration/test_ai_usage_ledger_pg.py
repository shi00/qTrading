"""AI 用量账本 sidecar PG 集成测试（S3，方案五场景 + R22）。

单测（tests/unit/data/test_ai_usage_dao.py）以 mock 连接断言 SQL 语义；
本文件在真实 PG（sidecar）上验证行为契约，对应模型统一.md §9/§10 与
方案 D1~D4 的落地结论：

1. ``TestConcurrentTryReserve``：并发 try_reserve 预算内仅一个获准（D3 三口径：
   已结算 SUM + legacy 分转元 + 有效预留金额合计）；
2. ``TestOwnerRecovery``：owner 复活三暂停点（D4）——reserved 不被 recover 转移 /
   dispatched 过期转 held_unconfirmed / epoch 递增后旧 owner 条件写入失权；
3. ``TestLateDeltaIdempotency``：迟到差额幂等整体推进 + R22 乱序水位取最大；
4. ``TestTwoScopesIsolation``：scenario / connection 两 scope 汇总行独立不相加（§10）；
5. ``TestConcurrentMixedOpsNoDeadlock``：同月锁序并发无死锁（D2）。

依赖与隔离策略对齐 test_embedded_pg_dao_rw.py：
- ``real_embedded_pg``（conftest session fixture，真实 sidecar PG）
- session fixture 重置 schema + ``alembic upgrade head`` + ``override_db_url`` +
  ``CacheManager.init_db``；teardown 关闭引擎并重置单例（R7）
- function fixture 每测试前后清空三表与 legacy app_state 键
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import datetime
from contextlib import ExitStack
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text

from data.cache.cache_manager import CacheManager
from data.persistence.daos.ai_usage_dao import AIUsageDao, SCOPE_CONNECTION, SCOPE_SCENARIO
from data.persistence.db_url_override import override_db_url
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from core.model_management_types import NormalizedLLMUsage
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_LEDGER_TABLES = ("ai_invocation", "ai_attempt", "ai_usage_monthly")
_LEDGER_LEGACY_KEY_PREFIX = "ai_cost:"


def _utcnow_naive() -> datetime.datetime:
    """账本时间列均为 DateTime(timezone=False)（naive UTC），测试统一构造 naive UTC。"""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _current_month() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m")


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ledger_pg_with_schema(real_embedded_pg: ConnectionInfo):
    """初始化 embedded PG schema + CacheManager 单例（session 共享，隔离自他文件）。"""
    info = real_embedded_pg

    await _reset_schema(info.url)

    cfg = make_alembic_cfg(info.url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    with ExitStack() as url_stack:
        url_stack.enter_context(override_db_url(info.url))
        CacheManager._reset_singleton()
        cache = CacheManager()
        await cache.init_db(auto_migrate=True)
        yield info
        try:
            await cache.close()
        except asyncio.CancelledError:
            raise  # R2: 不吞 CancelledError
        CacheManager._reset_singleton()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_ledger(ledger_pg_with_schema: ConnectionInfo):
    """每个测试前后清空三表与 legacy app_state 键，保证用例间隔离。"""
    cache = CacheManager()
    assert cache.engine is not None

    async def _cleanup() -> None:
        async with cache.engine.begin() as conn:
            for table in _LEDGER_TABLES:
                await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 表名为模块内常量
            await conn.execute(
                text("DELETE FROM app_state WHERE config_key LIKE :prefix"),
                {"prefix": f"{_LEDGER_LEGACY_KEY_PREFIX}%"},
            )

    await _cleanup()
    yield cache
    await _cleanup()


@pytest_asyncio.fixture(loop_scope="session")
async def ledger_dao(clean_ledger: CacheManager) -> AIUsageDao:
    """从 CacheManager 取账本 DAO（R13 登记路径）。"""
    assert clean_ledger.ai_usage_dao is not None
    return clean_ledger.ai_usage_dao


def _make_reserve_kwargs(invocation_id: str, *, owner_epoch: int = 1, amount: str = "1.00") -> dict:
    lease = _utcnow_naive() + datetime.timedelta(hours=1)
    return {
        "invocation_id": invocation_id,
        "scenario_id": "scn-itest",
        "task_id": "task-itest",
        "trigger": "manual",
        "month": _current_month(),
        "reservation_id": f"res-{invocation_id}",
        "attempt_id": None,
        "candidate_index": 0,
        "retry_index": 0,
        "amount_cny": amount,
        "budget_limit_cny": "1.00",
        "budget_policy_snapshot": {"budget_limit_cny": "1.00"},
        "owner_session_id": "sess-owner",
        "owner_epoch": owner_epoch,
        "owner_lease_expires_at": lease,
        "lease_expires_at": lease,
    }


def _make_settle_kwargs(
    attempt_id: str,
    *,
    month: str | None = None,
    usage_revision: int = 1,
    cost_cny: str = "0.50",
    connection_id: str = "conn-itest",
    scenario_id: str = "scn-itest",
    succeeded: bool = True,
) -> dict:
    return {
        "attempt_id": attempt_id,
        "invocation_id": f"inv-{attempt_id}",
        "status": "succeeded" if succeeded else "failed",
        "usage_status": "ok",
        "usage": NormalizedLLMUsage(
            input_total_tokens=100,
            uncached_input_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=None,
            output_tokens=50,
        ),
        "usage_revision": usage_revision,
        "error_code": None,
        "duration_ms": 120.0,
        "cost_cny": cost_cny,
        "actual_model": "gpt-itest",
        "connection_id": connection_id,
        "connection_revision": 1,
        "adapter_kind": "litellm_chat",
        "configured_model": "gpt-itest",
        "scenario_id": scenario_id,
        "task_id": "task-itest",
        "trigger": "manual",
        "candidate_index": 0,
        "retry_index": 0,
        "security_revision": 1,
        "month": month or _current_month(),
        "invocation_count_delta": 1,
    }


async def _fetch_reservations(dao: AIUsageDao, invocation_id: str) -> list[dict]:
    assert dao.engine is not None
    async with dao.engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT reservations FROM ai_invocation WHERE invocation_id = :iid"),
                {"iid": invocation_id},
            )
        ).fetchone()
    assert row is not None
    return list(row[0])


class TestConcurrentTryReserve:
    """场景 1（D3）：并发 try_reserve 预算内仅一个获准；三口径余额。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_concurrent_reserve_over_budget_only_one_wins(self, ledger_dao: AIUsageDao):
        """预算 1.00，两个并发 try_reserve 各 1.00：advisory 月锁序列化后恰一个获准。"""
        results = await asyncio.gather(
            ledger_dao.try_reserve(**_make_reserve_kwargs("inv-conc-1")),
            ledger_dao.try_reserve(**_make_reserve_kwargs("inv-conc-2")),
        )
        granted = [r for r in results if r is not None]
        assert len(granted) == 1, f"并发超额预留应恰一个获准，实际 {len(granted)}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reserve_blocked_by_settled_usage(self, ledger_dao: AIUsageDao):
        """已结算口径：settle 0.80 后，预算 1.00 内预留 0.50 拒绝、0.20 获准。"""
        month = _current_month()
        settled = await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-settled", month=month, cost_cny="0.80")
        )
        assert settled is True

        over = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-over", amount="0.50"))
        assert over is None, "已结算 0.80 + 预留 0.50 超预算 1.00，应拒绝"

        under = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-under", amount="0.20"))
        assert under is not None, "已结算 0.80 + 预留 0.20 未超预算 1.00，应获准"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reserve_blocked_by_legacy_cents(self, ledger_dao: AIUsageDao, clean_ledger: CacheManager):
        """legacy 口径：app_state ai_cost:{month}（分）150 = 1.50 元，预算 1.00 全额拒绝。"""
        month = _current_month()
        assert clean_ledger.engine is not None
        async with clean_ledger.engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO app_state (config_key, config_value) VALUES (:k, :v)"),
                {"k": f"{_LEDGER_LEGACY_KEY_PREFIX}{month}", "v": "150"},
            )

        blocked = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-legacy", amount="0.50"))
        assert blocked is None, "legacy 已耗 1.50 元 + 预留 0.50 超预算 1.00，应拒绝"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_in_flight_reservation_counts_toward_budget(self, ledger_dao: AIUsageDao):
        """有效预留口径：in-flight 预留 1.00 占用预算，后续 0.50 拒绝。"""
        first = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-inflight", amount="1.00"))
        assert first is not None
        second = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-inflight-2", amount="0.50"))
        assert second is None, "in-flight 预留 1.00 + 新预留 0.50 超预算，应拒绝"


class TestOwnerRecovery:
    """场景 2（D4）：owner 复活三暂停点。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reserved_state_not_converted_by_recover(self, ledger_dao: AIUsageDao):
        """暂停点 A：仅 reserved（未派发）过期后 recover 不转移状态，返回 0。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-pause-a"))
        assert reserved is not None

        # 把 owner 租约置为过期
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-pause-a"},
            )

        recovered = await ledger_dao.recover_expired_owners()
        assert recovered == 0, "reserved 状态预留不属于 recover 转移范围"

        reservations = await _fetch_reservations(ledger_dao, "inv-pause-a")
        assert reservations[0]["state"] == "reserved"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_dispatched_expired_converts_to_held_unconfirmed(self, ledger_dao: AIUsageDao):
        """暂停点 B：dispatched 后 owner 租约过期，recover 转 held_unconfirmed。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-pause-b"))
        assert reserved is not None

        dispatched = await ledger_dao.mark_dispatched(
            invocation_id="inv-pause-b",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert dispatched is True

        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-pause-b"},
            )

        recovered = await ledger_dao.recover_expired_owners()
        assert recovered == 1

        reservations = await _fetch_reservations(ledger_dao, "inv-pause-b")
        assert reservations[0]["state"] == "held_unconfirmed"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_claim_increments_epoch_and_old_owner_loses_write(self, ledger_dao: AIUsageDao):
        """暂停点 C：新 owner claim 递增 epoch；旧 epoch 条件写入失权（rowcount=0）。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-pause-c"))
        assert reserved is not None

        dispatched = await ledger_dao.mark_dispatched(
            invocation_id="inv-pause-c",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert dispatched is True

        # 租约过期 → 新 owner claim（不同 session）递增 epoch
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-pause-c"},
            )

        new_epoch = await ledger_dao.claim_invocation(
            invocation_id="inv-pause-c",
            owner_session_id="sess-recover",
            lease_expires_at=_utcnow_naive() + datetime.timedelta(hours=1),
        )
        assert new_epoch is not None and new_epoch == 2, f"claim 应递增 epoch 至 2，实际 {new_epoch}"

        # 旧 owner（epoch=1）条件写入失权
        stale_write = await ledger_dao.mark_dispatched(
            invocation_id="inv-pause-c",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert stale_write is False, "旧 epoch 的 owner 条件写入应失权（rowcount=0）"

        # 新 owner 同 session 重入 claim 仍可换发（幂等重入语义）
        reentrant = await ledger_dao.claim_invocation(
            invocation_id="inv-pause-c",
            owner_session_id="sess-recover",
            lease_expires_at=_utcnow_naive() + datetime.timedelta(hours=1),
        )
        assert reentrant == 3, "同 session 重入 claim 应继续递增 epoch"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_claim_rejected_while_lease_active(self, ledger_dao: AIUsageDao):
        """活跃租约被其他 owner 持有时 claim 返回 None。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-active"))
        assert reserved is not None

        rejected = await ledger_dao.claim_invocation(
            invocation_id="inv-active",
            owner_session_id="sess-other",
            lease_expires_at=_utcnow_naive() + datetime.timedelta(hours=1),
        )
        assert rejected is None, "活跃租约未过期时其他 owner 不得认领"


class TestLateDeltaIdempotency:
    """场景 3：迟到差额幂等整体推进 + R22 乱序水位。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_late_revision_accounts_delta_not_total(self, ledger_dao: AIUsageDao, clean_ledger: CacheManager):
        """rev=1 cost 0.50 → rev=2 cost 0.70：月汇总推进差额 0.20 至 0.70（非累计 1.20）。"""
        month = _current_month()
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-delta", month=month, usage_revision=1, cost_cny="0.50")
        )
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-delta", month=month, usage_revision=2, cost_cny="0.70")
        )

        assert clean_ledger.engine is not None
        async with clean_ledger.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_SCENARIO, "g": "scn-itest"},
                )
            ).fetchone()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("0.70"), "迟到修订应整体推进至 0.70，而非叠加 1.20"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_repeated_and_out_of_order_settle_ignored(self, ledger_dao: AIUsageDao):
        """重复 settle（同 rev）与乱序 settle（旧 rev）均不推进。"""
        month = _current_month()
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-idem", month=month, usage_revision=2, cost_cny="0.70")
        )
        assert not await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-idem", month=month, usage_revision=2, cost_cny="0.70")
        ), "同修订重复结算应返回 False"
        assert not await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-idem", month=month, usage_revision=1, cost_cny="0.30")
        ), "旧修订乱序结算应返回 False"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_monthly_watermark_monotonic_under_out_of_order_writes(
        self, ledger_dao: AIUsageDao, clean_ledger: CacheManager
    ):
        """R22：乱序写入（rev 5 → rev 3）后 last_usage_revision 保持最大值 5。"""
        month = _current_month()
        await ledger_dao.apply_monthly_delta(
            month=month,
            aggregation_scope=SCOPE_CONNECTION,
            grouping_key="conn-monotonic",
            scenario_id="scn-itest",
            trigger="manual",
            invocation_count=1,
            attempt_count=1,
            cost_cny_delta=Decimal("1.00"),
            last_usage_revision=5,
        )
        await ledger_dao.apply_monthly_delta(
            month=month,
            aggregation_scope=SCOPE_CONNECTION,
            grouping_key="conn-monotonic",
            scenario_id="scn-itest",
            trigger="manual",
            invocation_count=1,
            attempt_count=1,
            cost_cny_delta=Decimal("0.50"),
            last_usage_revision=3,
        )

        assert clean_ledger.engine is not None
        async with clean_ledger.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT last_usage_revision, cost_cny, attempt_count FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_CONNECTION, "g": "conn-monotonic"},
                )
            ).fetchone()
        assert row is not None
        assert int(row[0]) == 5, "R22：水位必须单调，乱序写入不得回退"
        assert int(row[2]) == 2, "attempt_count 为差额累加"
        assert Decimal(str(row[1])) == Decimal("1.50"), "cost 为差额累加"


class TestTwoScopesIsolation:
    """场景 4（§10）：scenario / connection 两 scope 汇总独立不相加。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scopes_are_separate_rows(self, ledger_dao: AIUsageDao, clean_ledger: CacheManager):
        """同一 scenario 两次结算（不同 connection）：scenario 1 行聚合，connection 2 行独立。"""
        month = _current_month()
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs(
                "att-scope-1", month=month, cost_cny="0.40", connection_id="conn-a", scenario_id="scn-scope"
            )
        )
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs(
                "att-scope-2", month=month, cost_cny="0.60", connection_id="conn-b", scenario_id="scn-scope"
            )
        )

        assert clean_ledger.engine is not None
        async with clean_ledger.engine.begin() as conn:
            scenario_rows = (
                await conn.execute(
                    text(
                        "SELECT grouping_key, attempt_count, succeeded_count, cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s ORDER BY grouping_key"
                    ),
                    {"m": month, "s": SCOPE_SCENARIO},
                )
            ).fetchall()
            connection_rows = (
                await conn.execute(
                    text(
                        "SELECT grouping_key, attempt_count, cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s ORDER BY grouping_key"
                    ),
                    {"m": month, "s": SCOPE_CONNECTION},
                )
            ).fetchall()

        assert len(scenario_rows) == 1, "scenario scope 按聚合 key 汇总为一行"
        assert scenario_rows[0][0] == "scn-scope"
        assert int(scenario_rows[0][1]) == 2
        assert int(scenario_rows[0][2]) == 2
        assert Decimal(str(scenario_rows[0][3])) == Decimal("1.00")

        assert len(connection_rows) == 2, "connection scope 按连接独立成行（不相加）"
        by_key = {r[0]: r for r in connection_rows}
        assert Decimal(str(by_key["conn-a"][2])) == Decimal("0.40")
        assert Decimal(str(by_key["conn-b"][2])) == Decimal("0.60")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_read_month_usage_filters_by_scope(self, ledger_dao: AIUsageDao):
        """read_month_usage 按 scope 筛选：互不串行。"""
        month = _current_month()
        assert await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-read-1", month=month, cost_cny="0.40", connection_id="conn-read")
        )

        scenario_rows = await ledger_dao.read_month_usage(month=month, aggregation_scope=SCOPE_SCENARIO)
        connection_rows = await ledger_dao.read_month_usage(month=month, aggregation_scope=SCOPE_CONNECTION)

        assert {r["grouping_key"] for r in scenario_rows} == {"scn-itest"}
        assert {r["grouping_key"] for r in connection_rows} == {"conn-read"}
        assert all(r["aggregation_scope"] == SCOPE_SCENARIO for r in scenario_rows)
        assert all(r["aggregation_scope"] == SCOPE_CONNECTION for r in connection_rows)


class TestConcurrentMixedOpsNoDeadlock:
    """场景 5（D2）：同月混合操作（预留 + 结算 + 独立月汇总）并发无死锁。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_mixed_concurrent_operations_complete(self, ledger_dao: AIUsageDao, clean_ledger: CacheManager):
        """8 个并发任务（4 预留 + 4 结算）在锁序约束下全部完成，汇总账目正确。"""
        month = _current_month()

        reserve_tasks = [
            ledger_dao.try_reserve(**_make_reserve_kwargs(f"inv-mixed-{i}", amount="0.10")) for i in range(4)
        ]
        settle_tasks = [
            ledger_dao.settle_attempt_usage(
                **_make_settle_kwargs(f"att-mixed-{i}", month=month, cost_cny="0.10", connection_id=f"conn-mixed-{i}")
            )
            for i in range(4)
        ]

        results = await asyncio.wait_for(asyncio.gather(*reserve_tasks, *settle_tasks), timeout=60.0)
        assert all(r is not None for r in results), "预算 1.00 内 4 笔 0.10 预留与 4 笔结算应全部成功"

        assert clean_ledger.engine is not None
        async with clean_ledger.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT attempt_count, cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_CONNECTION, "g": "conn-mixed-0"},
                )
            ).fetchone()
        assert row is not None
        assert int(row[0]) == 1
        assert Decimal(str(row[1])) == Decimal("0.10")


class TestF22ReservationLifecycle:
    """场景 6（F22）：预留状态机三条回归链的预算闭环（真实 PG 行为契约）。

    修复前：结算后预留停留 dispatched（与已结算费用双占）；owner 租约过期后
    held_unconfirmed 从预算有效占用中消失。修复后：结算同事务将对应预留转为
    settled；held_unconfirmed 持续占用直到明确处置（released / settled）。

    链 1 预留→派发→结算：settle 带 reservation_id → 转 settled → 不再双占；
    链 2 崩溃→过期恢复：recover 转 held_unconfirmed → 持续占用预算；
    链 3 手动解除：release_reservation → released → 预算释放。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_settle_converts_reservation_and_avoids_double_count(self, ledger_dao: AIUsageDao):
        """链 1：结算同事务转换预留；结算后旧预留不再占用（再预留不双计）。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-a", amount="0.50"))
        assert reserved is not None
        assert await ledger_dao.mark_dispatched(
            invocation_id="inv-f22-a",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )

        settle_kwargs = {
            **_make_settle_kwargs("att-f22-a", month=_current_month(), cost_cny="0.50"),
            "invocation_id": "inv-f22-a",
            "reservation_id": reserved.reservation_id,
        }
        assert await ledger_dao.settle_attempt_usage(**settle_kwargs)

        reservations = await _fetch_reservations(ledger_dao, "inv-f22-a")
        assert reservations[0]["state"] == "settled"
        assert reservations[0]["settled_attempt_id"] == "att-f22-a"

        # 修复前：used 0.50 + 旧 dispatched 0.50 + 新 0.50 = 1.50 > 1.00 拒绝（双占）
        # 修复后：used 0.50 + settled 0 占用 + 新 0.50 = 1.00 ≤ 1.00 获准
        again = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-a-2", amount="0.50"))
        assert again is not None, "结算转换后旧预留不得继续占用（双占窗口应消除）"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_held_unconfirmed_keeps_budget_after_owner_expiry(self, ledger_dao: AIUsageDao):
        """链 2：崩溃→过期恢复→held_unconfirmed 持续占用，过期 owner 不释放占用。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-b", amount="0.80"))
        assert reserved is not None
        assert await ledger_dao.mark_dispatched(
            invocation_id="inv-f22-b",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )

        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-f22-b"},
            )
        assert await ledger_dao.recover_expired_owners() == 1
        reservations = await _fetch_reservations(ledger_dao, "inv-f22-b")
        assert reservations[0]["state"] == "held_unconfirmed"

        # 修复前：行级租约条件把过期 owner 的 0.80 从预算占用中过滤掉 → 0.50 获准（缺陷）
        # 修复后：held_unconfirmed 持续占用 → 0.80 + 0.50 > 1.00 拒绝
        blocked = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-b-2", amount="0.50"))
        assert blocked is None, "过期 owner 的未确认预留必须持续占用预算"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_manual_release_frees_budget(self, ledger_dao: AIUsageDao):
        """链 3：人工解除 held_unconfirmed → released → 预算占用释放。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-c", amount="0.80"))
        assert reserved is not None
        assert await ledger_dao.mark_dispatched(
            invocation_id="inv-f22-c",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )

        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-f22-c"},
            )
        assert await ledger_dao.recover_expired_owners() == 1
        assert await ledger_dao.release_reservation(reservation_id=reserved.reservation_id)
        reservations = await _fetch_reservations(ledger_dao, "inv-f22-c")
        assert reservations[0]["state"] == "released"

        # released 不在活跃集合 → 占用释放 → 0.80 ≤ 1.00 获准
        freed = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f22-c-2", amount="0.80"))
        assert freed is not None, "人工解除后预算占用应释放"
