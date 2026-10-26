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
5. ``TestConcurrentMixedOpsNoDeadlock``：同月锁序并发无死锁（D2）；
6. ``TestF22ReservationLifecycle``：预留状态机三条回归链的预算闭环（F22）；
7. ``TestF23OwnerFencing``：owner/epoch fencing 贯穿账本写入口（F23）——
   失权 owner 四入口全拒绝 / 错误预留 ID 零派发 / 合法恢复凭活跃预留补记。

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
import json
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
    cost_cny: str | float = "0.50",
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


async def _seed_backing_reservation(dao: AIUsageDao, attempt_id: str) -> str:
    """为缺行结算登记活跃预留背书（F23 fencing）。

    invocation_id 对齐 ``_make_settle_kwargs`` 约定（``f"inv-{attempt_id}"``）；
    金额 0.00 不挤占用例自身的预算口径。返回 reservation_id。
    """
    reserved = await dao.try_reserve(**_make_reserve_kwargs(f"inv-{attempt_id}", amount="0.00"))
    assert reserved is not None, f"背书预留登记失败: inv-{attempt_id}"
    return reserved.reservation_id


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
        rid = await _seed_backing_reservation(ledger_dao, "att-settled")
        settled = await ledger_dao.settle_attempt_usage(
            **{**_make_settle_kwargs("att-settled", month=month, cost_cny="0.80"), "reservation_id": rid}
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
        rid = await _seed_backing_reservation(ledger_dao, "att-delta")
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-delta", month=month, usage_revision=1, cost_cny="0.50"),
                "reservation_id": rid,
            }
        )
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-delta", month=month, usage_revision=2, cost_cny="0.70"),
                "reservation_id": rid,
            }
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
        rid = await _seed_backing_reservation(ledger_dao, "att-idem")
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-idem", month=month, usage_revision=2, cost_cny="0.70"),
                "reservation_id": rid,
            }
        )
        assert not await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-idem", month=month, usage_revision=2, cost_cny="0.70"),
                "reservation_id": rid,
            }
        ), "同修订重复结算应返回 False"
        assert not await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-idem", month=month, usage_revision=1, cost_cny="0.30"),
                "reservation_id": rid,
            }
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
        rid1 = await _seed_backing_reservation(ledger_dao, "att-scope-1")
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs(
                    "att-scope-1", month=month, cost_cny="0.40", connection_id="conn-a", scenario_id="scn-scope"
                ),
                "reservation_id": rid1,
            }
        )
        rid2 = await _seed_backing_reservation(ledger_dao, "att-scope-2")
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs(
                    "att-scope-2", month=month, cost_cny="0.60", connection_id="conn-b", scenario_id="scn-scope"
                ),
                "reservation_id": rid2,
            }
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
        rid = await _seed_backing_reservation(ledger_dao, "att-read-1")
        assert await ledger_dao.settle_attempt_usage(
            **{
                **_make_settle_kwargs("att-read-1", month=month, cost_cny="0.40", connection_id="conn-read"),
                "reservation_id": rid,
            }
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

        # F23：缺行结算需活跃预留背书（先串行登记 0.00 预留，再进入并发阶段）
        rids = [await _seed_backing_reservation(ledger_dao, f"att-mixed-{i}") for i in range(4)]

        reserve_tasks = [
            ledger_dao.try_reserve(**_make_reserve_kwargs(f"inv-mixed-{i}", amount="0.10")) for i in range(4)
        ]
        settle_tasks = [
            ledger_dao.settle_attempt_usage(
                **{
                    **_make_settle_kwargs(
                        f"att-mixed-{i}", month=month, cost_cny="0.10", connection_id=f"conn-mixed-{i}"
                    ),
                    "reservation_id": rids[i],
                }
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


class TestF23OwnerFencing:
    """场景 7（F23）：owner/epoch fencing 贯穿账本写入口的行为契约。

    修复前缺陷：try_reserve 不检查 owner 条件 UPDATE rowcount（失权仍返回非持久
    预留）；mark_dispatched 仅凭 owner 条件更新行数（错误预留 ID 误判派发成功）；
    insert_attempt 无 owner/epoch 条件（失权可建立 attempt 事实）；settle 缺行
    无条件插行（无背书的凭空消费事实）。

    回归目标：A 过期 → B claim → A 的 reserve / begin / mark / settle 都不能
    任意推进；错误预留 ID 零派发；合法恢复仍能补记实际消费。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_owner_all_entries_fenced_after_claim(self, ledger_dao: AIUsageDao):
        """A 失权后四入口全拒绝：reserve / insert_attempt / mark_dispatched / settle 缺行。"""
        # A 建立 invocation（带预留），随后租约过期、B claim 接管（epoch 递增）
        assert await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f23-a", amount="0.10")) is not None
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-f23-a"},
            )
        assert (
            await ledger_dao.claim_invocation(
                invocation_id="inv-f23-a",
                owner_session_id="sess-b",
                lease_expires_at=_utcnow_naive() + datetime.timedelta(hours=1),
            )
            is not None
        )

        # 1) A 的 try_reserve：owner 条件 UPDATE rowcount=0 → None（预留未持久化）
        stale_reserve = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f23-a", amount="0.10"))
        assert stale_reserve is None, "失权 owner 的预留必须拒绝（不返回非持久预留）"

        # 2) A 的 insert_attempt：owner 身份强门不匹配 → False（不建 attempt 行）
        assert not await ledger_dao.insert_attempt(
            attempt_id="att-f23-stale",
            invocation_id="inv-f23-a",
            configured_model="gpt-itest",
            owner_session_id="sess-owner",
            owner_epoch=1,
        ), "失权 owner 不得建立 attempt 行"

        # 3) A 的 mark_dispatched：owner 三条件失权 → False（零派发）
        assert not await ledger_dao.mark_dispatched(
            invocation_id="inv-f23-a",
            reservation_id="res-inv-f23-a",
            owner_session_id="sess-owner",
            owner_epoch=1,
        ), "失权 owner 的派发绑定必须拒绝"

        # 4) A 的缺行结算（无预留背书的不存在 invocation）→ False（不得凭空建立事实）
        assert not await ledger_dao.settle_attempt_usage(
            **_make_settle_kwargs("att-f23-noback", month=_current_month(), cost_cny="0.10")
        ), "无预留背书的缺行结算必须拒绝（fail-closed）"

        # 失权写入均未落地：预留仍为 A 原始 reserved、无 attempt 行、无月汇总费用
        reservations = await _fetch_reservations(ledger_dao, "inv-f23-a")
        assert [r["state"] for r in reservations] == ["reserved"], "失权写入不得改写预留状态"
        month = _current_month()
        async with ledger_dao.engine.begin() as conn:
            attempt_row = (
                await conn.execute(
                    text("SELECT 1 FROM ai_attempt WHERE attempt_id = :aid"),
                    {"aid": "att-f23-stale"},
                )
            ).fetchone()
            usage_row = (
                await conn.execute(
                    text(
                        "SELECT cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_SCENARIO, "g": "scn-itest"},
                )
            ).fetchone()
        assert attempt_row is None, "失权 insert_attempt 不得建立 attempt 行"
        assert usage_row is None, "失权写入不得推进月汇总费用"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wrong_reservation_id_never_dispatches(self, ledger_dao: AIUsageDao):
        """错误预留 ID 零派发：EXISTS 守卫不命中 → False，任何预留状态不得改写。"""
        reserved = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f23-b", amount="0.20"))
        assert reserved is not None

        wrong = await ledger_dao.mark_dispatched(
            invocation_id="inv-f23-b",
            reservation_id="res-nonexistent",
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert wrong is False, "错误预留 ID 必须零派发（修复前仅凭 owner 条件 rowcount=1 误判成功）"
        reservations = await _fetch_reservations(ledger_dao, "inv-f23-b")
        assert reservations[0]["state"] == "reserved", "错误预留 ID 不得改写任何预留状态"

        correct = await ledger_dao.mark_dispatched(
            invocation_id="inv-f23-b",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert correct is True
        reservations = await _fetch_reservations(ledger_dao, "inv-f23-b")
        assert reservations[0]["state"] == "dispatched"

        # 已 dispatched 的预留重复派发（同 ID）→ 非 reserved → EXISTS 不命中 → False
        repeat = await ledger_dao.mark_dispatched(
            invocation_id="inv-f23-b",
            reservation_id=reserved.reservation_id,
            owner_session_id="sess-owner",
            owner_epoch=1,
        )
        assert repeat is False, "已派发预留不得重复派发（状态守卫）"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_recovery_settle_with_active_reservation_records_usage(self, ledger_dao: AIUsageDao):
        """合法恢复补记：B 认领后凭活跃预留背书补记实际消费（不盲目拒绝真实迟到事实）。"""
        assert await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-f23-c", amount="0.30")) is not None

        # 崩溃恢复：租约过期 → B claim 接管（attempt 行从未建立）
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai_invocation SET owner_lease_expires_at = :past WHERE invocation_id = :iid"),
                {"past": _utcnow_naive() - datetime.timedelta(hours=1), "iid": "inv-f23-c"},
            )
        assert (
            await ledger_dao.claim_invocation(
                invocation_id="inv-f23-c",
                owner_session_id="sess-b",
                lease_expires_at=_utcnow_naive() + datetime.timedelta(hours=1),
            )
            is not None
        )

        # B 以活跃预留背书补记实际消费（缺行结算唯一合法通道）
        settle_kwargs = {
            **_make_settle_kwargs("att-f23-c", month=_current_month(), cost_cny="0.20"),
            "invocation_id": "inv-f23-c",
            "reservation_id": "res-inv-f23-c",
        }
        assert await ledger_dao.settle_attempt_usage(**settle_kwargs), "活跃预留背书的补记结算必须成功"

        # 预留同事务转换为 settled + attempt 行落库 + 费用计入月汇总
        reservations = await _fetch_reservations(ledger_dao, "inv-f23-c")
        assert reservations[0]["state"] == "settled"
        assert reservations[0]["settled_attempt_id"] == "att-f23-c"
        month = _current_month()
        async with ledger_dao.engine.begin() as conn:
            usage_row = (
                await conn.execute(
                    text(
                        "SELECT cost_cny FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_SCENARIO, "g": "scn-itest"},
                )
            ).fetchone()
        assert usage_row is not None
        assert Decimal(str(usage_row[0])) == Decimal("0.20"), "补记的实际消费必须计入月汇总"


class TestAmountGuardContract:
    """场景 8（R05 D2/D3/D5）：金额入参契约与存量垃圾读数容错的端到端行为。

    单测以 mock 断言 SQL 语义；本类在真实 PG 上验证：
    - 垃圾金额（负数 / NaN / 非法字符）入口契约化 ValueError，零落库；
    - 历史 jsonb 垃圾预留（validator 生效前写入）不再毒化预算门（crash → 容错降级）；
    - float cost 归一化落库无二进制噪音（相邻移交）。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reserve_rejects_garbage_amount_with_zero_side_effect(self, ledger_dao: AIUsageDao):
        """D2：负预留入口即拒（ValueError），invocation 不落库（校验先于事务与 fencing）。"""
        with pytest.raises(ValueError, match="amount_cny: 金额必须为非负数"):
            await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-d2-neg", amount="-5.00"))
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT 1 FROM ai_invocation WHERE invocation_id = :iid"),
                    {"iid": "inv-d2-neg"},
                )
            ).fetchone()
        assert row is None, "契约拒绝必须发生在事务开启前（零落库）"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reserve_rejects_nan_and_infinity_amount(self, ledger_dao: AIUsageDao):
        """D2：NaN / -Infinity 静默接受路径已封死（契约化 ValueError）。"""
        for bad in ("NaN", "-Infinity", "sNaN", "1e999999"):
            with pytest.raises(ValueError, match="amount_cny"):
                await ledger_dao.try_reserve(**_make_reserve_kwargs(f"inv-d2-{bad}", amount=bad))

    @pytest.mark.asyncio(loop_scope="session")
    async def test_settle_rejects_garbage_cost_zero_side_effect(self, ledger_dao: AIUsageDao):
        """D3：settle 垃圾 cost 入口即拒（先于 F23 背书判定），月汇总零推进。"""
        month = _current_month()
        with pytest.raises(ValueError, match="cost_cny: 金额必须为非负数"):
            await ledger_dao.settle_attempt_usage(**_make_settle_kwargs("att-d3-neg", month=month, cost_cny="-0.01"))
        with pytest.raises(ValueError, match="cost_cny"):
            await ledger_dao.settle_attempt_usage(**_make_settle_kwargs("att-d3-nan", month=month, cost_cny="NaN"))
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM ai_usage_monthly "
                        "WHERE month = :m AND aggregation_scope = :s AND grouping_key = :g"
                    ),
                    {"m": month, "s": SCOPE_SCENARIO, "g": "scn-itest"},
                )
            ).fetchone()
        assert row is None, "契约拒绝不得推进月汇总"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_apply_monthly_delta_rejects_negative_delta(self, ledger_dao: AIUsageDao):
        """D3：公开入口负差额直接拒绝（负 delta 不得减已结算费用）。"""
        month = _current_month()
        with pytest.raises(ValueError, match="cost_cny_delta: 金额必须为非负数"):
            await ledger_dao.apply_monthly_delta(
                month=month,
                aggregation_scope=SCOPE_SCENARIO,
                grouping_key="scn-itest",
                scenario_id="scn-itest",
                trigger="manual",
                invocation_count=1,
                attempt_count=1,
                cost_cny_delta=Decimal("-0.50"),
            )
        with pytest.raises(ValueError, match="cost_cny_delta"):
            await ledger_dao.apply_monthly_delta(
                month=month,
                aggregation_scope=SCOPE_SCENARIO,
                grouping_key="scn-itest",
                scenario_id="scn-itest",
                trigger="manual",
                invocation_count=1,
                attempt_count=1,
                cost_cny_delta=Decimal("NaN"),
            )

    async def _seed_garbage_reservation(self, dao: AIUsageDao, invocation_id: str, amount_text: str) -> None:
        """手工写入 D1 validator 生效前的历史形态垃圾预留（amount_cny 任意文本）。

        owner 三元组对齐 ``_make_reserve_kwargs``（``sess-owner`` / epoch 1 / 有效租约）：
        后续 try_reserve 须凭同一 owner 通过 F23 fencing 把新预留追加进该行，
        预算门 SUM 读数才会包含垃圾元素（owner 失配则 rowcount=0 直接失权，用例失真）。
        """
        lease = _utcnow_naive() + datetime.timedelta(hours=1)
        reservations = json.dumps(
            [
                {
                    "reservation_id": f"res-{invocation_id}",
                    "task_id": "task-garbage",
                    "candidate_index": 0,
                    "retry_index": 0,
                    "amount_cny": amount_text,
                    "state": "reserved",
                    "attempt_id": None,
                    "owner_session_id": "sess-owner",
                    "owner_epoch": 1,
                }
            ],
            ensure_ascii=False,
        )
        assert dao.engine is not None
        async with dao.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ai_invocation (
                        invocation_id, scenario_id, trigger, owner_session_id,
                        owner_epoch, owner_lease_expires_at, reservations
                    ) VALUES (
                        :iid, 'scn-itest', 'manual', 'sess-owner',
                        1, :lease, CAST(:res AS jsonb)
                    )
                    """
                ),
                {"iid": invocation_id, "lease": lease, "res": reservations},
            )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_nan_reservation_degrades_budget_gate_instead_of_crash(self, ledger_dao: AIUsageDao):
        """D2 存量闭环：历史 'NaN' 预留毒化 SUM（修复前 InvalidOperation crash）→
        预算门按事实零降级，新预留照常获准（不 crash、不误拒）。"""
        await self._seed_garbage_reservation(ledger_dao, "inv-garbage-nan", "NaN")
        # 修复前：SUM(numeric) 含 NaN → Decimal 比较 InvalidOperation 未捕获 crash
        granted = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-after-nan", amount="0.50"))
        assert granted is not None, "存量 NaN 垃圾应按事实零降级放行，而非 crash/误拒"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_negative_reservation_clamped_not_credited(self, ledger_dao: AIUsageDao):
        """D2 存量闭环：历史负预留降低 SUM 占用（修复前预算门失守）→ clamp 0。"""
        await self._seed_garbage_reservation(ledger_dao, "inv-garbage-neg", "-5.00")
        # SUM = -5.00 → clamp 0 → 0.90 ≤ 1.00 获准；负占用不得「归还」预算额度
        granted = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-after-neg", amount="0.90"))
        assert granted is not None, "负预留 clamp 后预算门按事实零计算，不 crash 也不放大额度"

        # 交叉验证：负占用确未归还——合法预留 0.90 后，再预留 0.20 超预算被拒
        blocked = await ledger_dao.try_reserve(**_make_reserve_kwargs("inv-after-neg-2", amount="0.20"))
        assert blocked is None, "clamp 语义不得放大预算额度（0.90 已占用 + 0.20 > 1.00）"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_settle_float_cost_persists_without_binary_noise(self, ledger_dao: AIUsageDao):
        """相邻移交：float cost 归一为最短往返串落库（修复前 Decimal(float) 全长展开
        入 numeric 列——PG 无精度 numeric 原样存储 55 位二进制噪音，读回 ≠ 0.50）。"""
        rid = await _seed_backing_reservation(ledger_dao, "att-d3-float")
        settled = await ledger_dao.settle_attempt_usage(
            **{**_make_settle_kwargs("att-d3-float", month=_current_month(), cost_cny=0.5), "reservation_id": rid}
        )
        assert settled is True
        assert ledger_dao.engine is not None
        async with ledger_dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT cost_cny FROM ai_attempt WHERE attempt_id = :aid"),
                    {"aid": "att-d3-float"},
                )
            ).fetchone()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("0.50"), "费用落库必须为规范十进制值"
