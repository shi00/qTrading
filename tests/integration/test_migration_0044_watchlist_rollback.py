"""Integration tests for 0044/0046 watchlist added_price backfill and rollback guards.

D04-F02：0044 downgrade 反算 ``close / adj_factor`` 可能超出旧列 ``Numeric(12, 6)``
上限。本文件验证 fail-closed 预检与显式有损回滚路径。

D06-002：0044 的「可用行情」判据曾只要求 ``adj_factor > 0``，``close = 0 / -1``
会被回填成 0 / -2（而非「未知」）。本文件同时验证 upgrade 的「有限正」判据、含
NaN / Infinity 的边界，以及已发布库对应的前向 revision 0046 的归一回填。

独立成文件（原位于 ``test_data_db_migrator.py``）：避免与 D04-F01 在同一共享文件的
同一区域新增测试产生合并冲突。需要 PostgreSQL；无 PG 时在 fixture 建连阶段失败。
"""

# pyright: reportOptionalSubscript=false
# 测试断言直接访问 fetchone() 的列值，本文件局部关闭 Optional 下标告警。

import argparse
import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.db_migrator import DatabaseMigrator
from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg

pytestmark = pytest.mark.integration

_W0044_MIGRATED_TO = "0044"
_W0044_ROLLBACK_TARGET = "0043"
_W0044_BEFORE = "0043"
_W0046_MIGRATED_TO = "0045"

# (ts_code, 加入交易日)；预期 downgrade 后 added_price：
# T1/T2/T9/T10 反算 close/adj_factor >= 1e6，旧列 Numeric(12,6) 无法表示；
# T3/T8/T11 可表示；T4 无行情、T5 adj=0、T6 adj=NULL、T7 原本即 NULL；
# T12~T18 为 D06-002 的非法 close/factor（0 / 负 / NULL / NaN / Infinity）。
_W0044_WATCHLIST = [
    ("T1", date(2026, 1, 2), True),
    ("T2", date(2026, 1, 2), True),
    ("T3", date(2026, 1, 2), True),
    ("T4", date(2026, 1, 2), False),
    ("T5", date(2026, 1, 2), True),
    ("T6", date(2026, 1, 2), True),
    ("T7", date(2026, 1, 2), False),
    ("T8", date(2026, 1, 2), True),
    ("T9", date(2026, 1, 2), True),
    ("T10", date(2026, 1, 2), True),
    ("T11", date(2026, 1, 2), True),
    ("T12", date(2026, 1, 2), True),
    ("T13", date(2026, 1, 2), True),
    ("T14", date(2026, 1, 2), True),
    ("T15", date(2026, 1, 2), True),
    ("T16", date(2026, 1, 2), True),
    ("T17", date(2026, 1, 2), True),
    ("T18", date(2026, 1, 2), True),
]
# T12: close=0 → 旧实现回填 0；T13: close=-1, adj=2 → 旧实现回填 -2；
# T15: close=NaN；T16: close=Infinity；T17: adj=-1；T18: adj=NaN。
_W0044_QUOTES: dict[str, tuple[Decimal | None, Decimal | None]] = {
    "T1": (Decimal("10"), Decimal("0.000001")),
    "T2": (Decimal("10000"), Decimal("0.000000000001")),
    "T3": (Decimal("10"), Decimal("2")),
    "T5": (Decimal("5"), Decimal("0")),
    "T6": (Decimal("6"), None),
    "T7": (Decimal("7"), Decimal("1")),
    "T8": (Decimal("10"), Decimal("0.000010000001")),
    "T9": (Decimal("10"), Decimal("0.000009999999")),
    # 舍入临界：精确商 ≈ 1e6，6 位小数舍入为 1000000.000000 → 旧列溢出
    "T10": (Decimal("999999.9999"), Decimal("0.999999999900")),
    "T11": (Decimal("999999.9999"), Decimal("1.000000000001")),
    "T12": (Decimal("0"), Decimal("2")),
    "T13": (Decimal("-1"), Decimal("2")),
    "T14": (None, Decimal("1")),
    "T15": (Decimal("NaN"), Decimal("2")),
    "T16": (Decimal("Infinity"), Decimal("2")),
    "T17": (Decimal("10"), Decimal("-1")),
    "T18": (Decimal("10"), Decimal("NaN")),
}
_W0044_UNREPRESENTABLE = {"T1", "T2", "T9", "T10"}
# D06-002：close / adj_factor 非「有限正数」→ 行情不可用，值必须为 NULL。
_W0044_UNUSABLE_QUOTE = {"T12", "T13", "T14", "T15", "T16", "T17", "T18"}
# 这些行需要非 NULL 的「任意旧值」，用于验证非法行情不会把旧值反算成 0 / 负值。
# T15/T18 的旧值不取 NaN（NaN != NaN 会破坏「零写入」前后的字典比较）。
_W0044_FIXED_PRICE = {
    "T5": Decimal("999"),
    "T6": Decimal("999"),
    "T14": Decimal("999"),
    "T15": Decimal("999"),
    "T18": Decimal("999"),
}


async def _create_isolated_db(params: dict, db_name: str) -> None:
    """Create an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _drop_isolated_db(params: dict, db_name: str) -> None:
    """Drop an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        db_name_sql = db_name.replace('"', '""')
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
    finally:
        await conn.close()


async def _migrated_db_fixture(
    params: dict, db_name_prefix: str, target_revision: str = "head"
) -> tuple[AsyncEngine, str, str, Any]:
    """Create isolated DB and migrate to target revision.

    Returns:
        (async_engine, db_name, sync_db_url, sync_engine)
    """
    db_name = f"{db_name_prefix}_{uuid.uuid4().hex[:8]}"
    await _create_isolated_db(params, db_name)

    sync_url, async_url = build_db_urls(params, db_name)
    sync_engine = create_engine(sync_url)

    with override_db_url(sync_url):
        cfg = make_alembic_cfg(sync_url)
        await asyncio.to_thread(command.upgrade, cfg, target_revision)

    # Alembic 通过独立同步引擎执行 DDL（downgrade 改列宽），会使本 async engine
    # 连接上的 asyncpg prepared statement 计划缓存失效并抛 InvalidCachedStatementError。
    # 关闭语句缓存后同一 engine 可在 DDL 前后安全复用（同 D04-F06 的处理方式）。
    async_engine = create_test_engine(async_url, connect_args={"statement_cache_size": 0})

    return async_engine, db_name, sync_url, sync_engine


async def _cleanup_migrated_db(params: dict, async_engine: AsyncEngine, sync_engine: Any, db_name: str) -> None:
    """Dispose engines and drop the isolated database."""
    await async_engine.dispose()
    sync_engine.dispose()
    await _drop_isolated_db(params, db_name)


@pytest.fixture
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return get_pg_connection_params()


class TestWatchlistPriceRollback0044:
    """0044 downgrade 的 fail-closed 保护与显式有损回滚路径。"""

    @pytest_asyncio.fixture(loop_scope="function")
    async def db_0044(self, pg_params):
        """Create an isolated database migrated to 0044 for rollback tests."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_w0044", _W0044_MIGRATED_TO
        )
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @staticmethod
    async def _seed(engine: AsyncEngine) -> None:
        """Populate watchlist/daily_quotes with boundary cases used by 0044 downgrade."""
        watchlist_params = []
        for ts_code, trade_date, has_price in _W0044_WATCHLIST:
            if not has_price:
                added_price = None
            elif ts_code in _W0044_FIXED_PRICE:
                added_price = _W0044_FIXED_PRICE[ts_code]
            else:
                close, adj = _W0044_QUOTES[ts_code]
                added_price = None if close is None or adj is None else close * adj
            watchlist_params.append(
                {
                    "ts_code": ts_code,
                    "stock_name": ts_code,
                    "added_trade_date": trade_date,
                    "added_price": added_price,
                }
            )
        quote_params = [
            {"ts_code": ts_code, "trade_date": date(2026, 1, 2), "close": close, "adj_factor": adj}
            for ts_code, (close, adj) in _W0044_QUOTES.items()
        ]
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO watchlist (ts_code, stock_name, added_trade_date, added_price) "
                    "VALUES (:ts_code, :stock_name, :added_trade_date, :added_price)"
                ),
                watchlist_params,
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, adj_factor) "
                    "VALUES (:ts_code, :trade_date, :close, :adj_factor)"
                ),
                quote_params,
            )

    @staticmethod
    async def _added_prices(engine: AsyncEngine) -> dict[str, Decimal | None]:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT ts_code, added_price FROM watchlist"))
            return {row[0]: row[1] for row in result.fetchall()}

    @staticmethod
    async def _version(engine: AsyncEngine) -> str:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            return result.fetchone()[0]

    @staticmethod
    async def _added_price_column_type(engine: AsyncEngine) -> tuple[int, int]:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'watchlist' "
                    "AND column_name = 'added_price'"
                )
            )
            row = result.fetchone()
            return (row[0], row[1])

    @staticmethod
    async def _row_count(engine: AsyncEngine) -> int:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM watchlist"))
            return result.scalar_one()

    async def _downgrade(self, sync_url: str, *, allow_lossy: bool) -> None:
        with override_db_url(sync_url):

            def _do_downgrade():
                cfg = make_alembic_cfg(sync_url)
                if allow_lossy:
                    # 与 CLI `alembic -x allow_lossy_rollback=1 ...` 等价
                    cfg.cmd_opts = argparse.Namespace(x=["allow_lossy_rollback=1"])
                command.downgrade(cfg, _W0044_ROLLBACK_TARGET)

            await asyncio.to_thread(_do_downgrade)

    async def _upgrade_head(self, sync_url: str) -> None:
        with override_db_url(sync_url):

            def _do_upgrade():
                cfg = make_alembic_cfg(sync_url)
                command.upgrade(cfg, "head")

            await asyncio.to_thread(_do_upgrade)

    @pytest.mark.asyncio
    async def test_unrepresentable_rows_block_downgrade_without_flag(self, db_0044):
        """未授权时必须 fail-closed：抛中文异常、零写入、版本与列宽均不变。"""
        engine, db_name, sync_url, sync_engine = db_0044
        await self._seed(engine)
        before = await self._added_prices(engine)
        assert await self._version(engine) == _W0044_MIGRATED_TO

        with pytest.raises(RuntimeError, match="allow_lossy_rollback=1"):
            await self._downgrade(sync_url, allow_lossy=False)

        assert await self._version(engine) == _W0044_MIGRATED_TO
        assert await self._added_price_column_type(engine) == (24, 6)
        assert await self._added_prices(engine) == before
        assert await self._row_count(engine) == len(_W0044_WATCHLIST)

    @pytest.mark.asyncio
    async def test_authorized_lossy_downgrade_nulls_only_unrepresentable_rows(self, db_0044):
        """显式授权后仅不可表示行置 NULL，可表示行正常反算，行数守恒。"""
        engine, db_name, sync_url, sync_engine = db_0044
        await self._seed(engine)

        await self._downgrade(sync_url, allow_lossy=True)

        assert await self._version(engine) == _W0044_ROLLBACK_TARGET
        assert await self._added_price_column_type(engine) == (12, 6)
        prices = await self._added_prices(engine)
        assert await self._row_count(engine) == len(_W0044_WATCHLIST)

        for ts_code in _W0044_UNREPRESENTABLE:
            assert prices[ts_code] is None, f"{ts_code} 应因旧列无法表示而置 NULL"
        # 无可用行情（缺行 / adj=0 / adj NULL）同样置 NULL
        for ts_code in {"T4", "T5", "T6"} | _W0044_UNUSABLE_QUOTE:
            assert prices[ts_code] is None, f"{ts_code} 应因行情不可用而置 NULL"
        # 原本 NULL 的行不被伪造
        assert prices["T7"] is None
        # 可表示行按 close / adj_factor 反算（列宽 6 位小数舍入）
        assert prices["T3"] == Decimal("5.000000")
        assert prices["T8"] == Decimal("999999.900000")
        assert prices["T11"] == Decimal("999999.999899")

        # added_trade_date 保留（仅价格置 NULL）
        async with engine.connect() as conn:
            retained = {}
            for ts_code in sorted(_W0044_UNREPRESENTABLE):
                result = await conn.execute(
                    text("SELECT added_trade_date FROM watchlist WHERE ts_code = :ts_code"),
                    {"ts_code": ts_code},
                )
                retained[ts_code] = result.fetchone()[0]
        for ts_code in _W0044_UNREPRESENTABLE:
            assert retained[ts_code] == date(2026, 1, 2)

        # 再升级不得伪造有损置 NULL 的值
        await self._upgrade_head(sync_url)
        assert await self._version(engine) == await DatabaseMigrator._get_head_revision()
        prices_after = await self._added_prices(engine)
        for ts_code in _W0044_UNREPRESENTABLE:
            assert prices_after[ts_code] is None
        assert prices_after["T3"] == Decimal("20.000000")

    @pytest.mark.asyncio
    async def test_downgrade_without_unrepresentable_rows_needs_no_flag(self, pg_params):
        """无不可表示行时，未授权也可正常反算并窄化列宽。"""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_w0044_safe", _W0044_MIGRATED_TO
        )
        try:
            await self._seed(async_engine)
            # 删除不可表示行，仅保留可表示/行情不可用场景
            async with async_engine.begin() as conn:
                for ts_code in sorted(_W0044_UNREPRESENTABLE):
                    await conn.execute(
                        text("DELETE FROM watchlist WHERE ts_code = :ts_code"),
                        {"ts_code": ts_code},
                    )

            await self._downgrade(sync_url, allow_lossy=False)

            assert await self._version(async_engine) == _W0044_ROLLBACK_TARGET
            assert await self._added_price_column_type(async_engine) == (12, 6)
            prices = await self._added_prices(async_engine)
            assert prices["T3"] == Decimal("5.000000")
            assert prices["T8"] == Decimal("999999.900000")
            assert prices["T11"] == Decimal("999999.999899")
            for ts_code in {"T4", "T5", "T6", "T7"} | _W0044_UNUSABLE_QUOTE:
                assert prices[ts_code] is None
        finally:
            await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)


async def _alembic_upgrade(sync_url: str, target: str) -> None:
    """Run alembic upgrade to target revision on an isolated database."""
    with override_db_url(sync_url):

        def _do_upgrade():
            cfg = make_alembic_cfg(sync_url)
            command.upgrade(cfg, target)

        await asyncio.to_thread(_do_upgrade)


async def _alembic_downgrade(sync_url: str, target: str) -> None:
    """Run alembic downgrade to target revision on an isolated database."""
    with override_db_url(sync_url):

        def _do_downgrade():
            cfg = make_alembic_cfg(sync_url)
            command.downgrade(cfg, target)

        await asyncio.to_thread(_do_downgrade)


# D06-002：0043 时期（0044 之前）的旧值 + 边界行情。U1 正常；U2~U11 的
# close / adj_factor 分别为 0 / 负 / NULL / NaN / Infinity；U10 无行情；
# U12 旧值本身为 NULL（不得被回填）。
_W0044_UPGRADE_QUOTES: dict[str, tuple[Decimal | None, Decimal | None]] = {
    "U1": (Decimal("10"), Decimal("2")),
    "U2": (Decimal("0"), Decimal("2")),
    "U3": (Decimal("-1"), Decimal("2")),
    "U4": (None, Decimal("2")),
    "U5": (Decimal("NaN"), Decimal("2")),
    "U6": (Decimal("Infinity"), Decimal("2")),
    "U7": (Decimal("10"), Decimal("0")),
    "U8": (Decimal("10"), Decimal("-1")),
    "U9": (Decimal("10"), None),
    "U11": (Decimal("10"), Decimal("NaN")),
}
_W0044_UPGRADE_CODES = [f"U{i}" for i in range(1, 13)]
_W0044_UPGRADE_NULL = set(_W0044_UPGRADE_CODES) - {"U1"}


class TestWatchlistPriceUpgrade0044:
    """0044 upgrade 只接受「有限正」行情，其余置 NULL（不写 0 / 负值）。"""

    @pytest_asyncio.fixture(loop_scope="function")
    async def db_before_0044(self, pg_params):
        """Create an isolated database migrated to 0043 (before 0044)."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_u0044", _W0044_BEFORE
        )
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @staticmethod
    async def _seed(engine: AsyncEngine) -> None:
        """Seed legacy reciprocal values plus boundary quotes before 0044 runs."""
        watchlist_params = [
            {
                "ts_code": ts_code,
                "stock_name": ts_code,
                "note": f"keep-{ts_code}",
                "added_trade_date": date(2026, 1, 2),
                "added_price": None if ts_code == "U12" else Decimal("5"),
            }
            for ts_code in _W0044_UPGRADE_CODES
        ]
        quote_params = [
            {"ts_code": ts_code, "trade_date": date(2026, 1, 2), "close": close, "adj_factor": adj}
            for ts_code, (close, adj) in _W0044_UPGRADE_QUOTES.items()
        ]
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO watchlist (ts_code, stock_name, note, added_trade_date, added_price) "
                    "VALUES (:ts_code, :stock_name, :note, :added_trade_date, :added_price)"
                ),
                watchlist_params,
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, adj_factor) "
                    "VALUES (:ts_code, :trade_date, :close, :adj_factor)"
                ),
                quote_params,
            )

    @pytest.mark.asyncio
    async def test_upgrade_uses_finite_positive_quote_only(self, db_before_0044):
        """正常行情回填乘积；0 / 负 / NULL / NaN / Infinity 一律置 NULL。"""
        engine, db_name, sync_url, sync_engine = db_before_0044
        await self._seed(engine)

        await _alembic_upgrade(sync_url, _W0044_MIGRATED_TO)

        assert await TestWatchlistPriceRollback0044._version(engine) == _W0044_MIGRATED_TO
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT ts_code, added_price, added_trade_date, note FROM watchlist"))
            rows = {row[0]: (row[1], row[2], row[3]) for row in result.fetchall()}

        assert len(rows) == len(_W0044_UPGRADE_CODES)
        assert rows["U1"][0] == Decimal("20.000000")
        for ts_code in _W0044_UPGRADE_NULL:
            assert rows[ts_code][0] is None, f"{ts_code} 的行情不是有限正数，价格必须为未知"
        # 日期与备注守恒（只降级价格，不删除观察起点本身）
        for ts_code in _W0044_UPGRADE_CODES:
            assert rows[ts_code][1] == date(2026, 1, 2)
            assert rows[ts_code][2] == f"keep-{ts_code}"


# D06-002：0044 已发布库可能已回填 0 / -2 / NaN / Infinity。0046 重放归一化：
# 行情不可用 → NULL；行情可用 → 重算乘积（修正历史错值）；原 NULL 不回填。
_W0046_QUOTES: dict[str, tuple[Decimal | None, Decimal | None]] = {
    "V1": (Decimal("10"), Decimal("2")),
    "V2": (Decimal("0"), Decimal("2")),
    "V3": (Decimal("-1"), Decimal("2")),
    "V4": (None, Decimal("2")),
    "V5": (Decimal("NaN"), Decimal("2")),
    "V6": (Decimal("Infinity"), Decimal("2")),
    "V7": (Decimal("5"), Decimal("0")),
    "V8": (Decimal("6"), None),
    "V10": (Decimal("10"), Decimal("2")),
    "V11": (Decimal("10"), Decimal("2")),
    "V12": (Decimal("10"), Decimal("-1")),
    "V13": (Decimal("10"), Decimal("NaN")),
}
_W0046_CODES = ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10", "V11", "V12", "V13"]
# 模拟 0044（旧判据）写入后的现状：0 / -2 / NaN / Infinity / 无行情对应值。
_W0046_STORED: dict[str, Decimal | None] = {
    "V1": Decimal("0"),
    "V2": Decimal("0"),
    "V3": Decimal("-2"),
    "V4": Decimal("0"),
    "V5": Decimal("0"),
    "V6": Decimal("0"),
    "V7": Decimal("0"),
    "V8": Decimal("0"),
    "V9": Decimal("5"),
    "V10": Decimal("20"),
    "V11": None,
    "V12": Decimal("-10"),
    "V13": Decimal("0"),
}
_W0046_NULL = {"V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V11", "V12", "V13"}


class TestWatchlistPositiveClose0046:
    """0046 前向 revision：已发布库中非法行情回填值归一为 NULL / 正确乘积。"""

    @pytest_asyncio.fixture(loop_scope="function")
    async def db_0046(self, pg_params):
        """Create an isolated database migrated to 0045 (head before 0046)."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_w0046", _W0046_MIGRATED_TO
        )
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @staticmethod
    async def _seed(engine: AsyncEngine) -> None:
        """Seed values a released 0044 database may already contain."""
        watchlist_params = [
            {
                "ts_code": ts_code,
                "stock_name": ts_code,
                "note": f"keep-{ts_code}",
                "added_trade_date": date(2026, 1, 2),
                "added_price": _W0046_STORED[ts_code],
            }
            for ts_code in _W0046_CODES
        ]
        quote_params = [
            {"ts_code": ts_code, "trade_date": date(2026, 1, 2), "close": close, "adj_factor": adj}
            for ts_code, (close, adj) in _W0046_QUOTES.items()
        ]
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO watchlist (ts_code, stock_name, note, added_trade_date, added_price) "
                    "VALUES (:ts_code, :stock_name, :note, :added_trade_date, :added_price)"
                ),
                watchlist_params,
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, adj_factor) "
                    "VALUES (:ts_code, :trade_date, :close, :adj_factor)"
                ),
                quote_params,
            )

    @staticmethod
    async def _snapshot(engine: AsyncEngine) -> dict[str, tuple[Decimal | None, date | None, str | None]]:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT ts_code, added_price, added_trade_date, note FROM watchlist"))
            return {row[0]: (row[1], row[2], row[3]) for row in result.fetchall()}

    @pytest.mark.asyncio
    async def test_upgrade_normalizes_released_backfill(self, db_0046):
        """非法行情的旧回填值降级为 NULL；可用行情重算乘积；原 NULL 不回填。"""
        engine, db_name, sync_url, sync_engine = db_0046
        await self._seed(engine)

        await _alembic_upgrade(sync_url, "head")

        rows = await self._snapshot(engine)
        assert len(rows) == len(_W0046_CODES)
        for ts_code in _W0046_NULL:
            assert rows[ts_code][0] is None, f"{ts_code} 的行情不可用，价格必须为未知"
        # 可用行情按 close × adj_factor 重算（修正被写成 0 的 V1）
        assert rows["V1"][0] == Decimal("20.000000")
        assert rows["V10"][0] == Decimal("20.000000")
        # 行数 / 日期 / 备注守恒
        for ts_code in _W0046_CODES:
            assert rows[ts_code][1] == date(2026, 1, 2)
            assert rows[ts_code][2] == f"keep-{ts_code}"

    @pytest.mark.asyncio
    async def test_downgrade_replays_same_normalization(self, db_0046):
        """downgrade 不恢复已知错误值，只重放同一归一化（幂等）。"""
        engine, db_name, sync_url, sync_engine = db_0046
        await self._seed(engine)

        await _alembic_upgrade(sync_url, "head")
        after_upgrade = await self._snapshot(engine)

        await _alembic_downgrade(sync_url, _W0046_MIGRATED_TO)

        assert await TestWatchlistPriceRollback0044._version(engine) == _W0046_MIGRATED_TO
        after_downgrade = await self._snapshot(engine)
        assert after_downgrade == after_upgrade
        assert len(after_downgrade) == len(_W0046_CODES)
        for ts_code in _W0046_NULL:
            assert after_downgrade[ts_code][0] is None
        assert after_downgrade["V1"][0] == Decimal("20.000000")
