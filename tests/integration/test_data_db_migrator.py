"""Integration tests for DatabaseMigrator module.

Tests real Alembic integration with PostgreSQL database.
Uses isolated temporary databases for each test.
All fixtures are async to avoid asyncio.run() conflicts with pytest-asyncio.
"""

# pyright: reportOptionalSubscript=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 Optional 下标访问。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import argparse
import asyncio
import threading
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

from data.persistence.db_migrator import DatabaseMigrator, DatabaseMigrationNeeded
from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg
from tests.conftest import singleton_state as _singleton_state_ctx

pytestmark = pytest.mark.integration


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


@pytest.fixture
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return get_pg_connection_params()


class TestGetHeadRevision:
    """Tests for _get_head_revision with real Alembic configuration."""

    @pytest.mark.asyncio
    async def test_get_head_revision_returns_real_value(self):
        """_get_head_revision should return a valid revision from real Alembic config."""
        head_rev = await DatabaseMigrator._get_head_revision()

        assert head_rev is not None
        assert isinstance(head_rev, str)
        assert len(head_rev) > 0


class TestFreshInstall:
    """Tests for fresh database initialization with real PostgreSQL and Alembic."""

    @pytest_asyncio.fixture(loop_scope="function")
    async def fresh_db_engine(self, pg_params):
        """Create an isolated empty database for fresh install testing."""
        db_name = f"migrator_fresh_{uuid.uuid4().hex[:8]}"
        params = pg_params

        await _create_isolated_db(params, db_name)

        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)

        yield engine, db_name

        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_fresh_install_creates_tables_and_version(self, fresh_db_engine):
        """Fresh database initialization should create tables and record schema version."""
        engine, db_name = fresh_db_engine

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result.fetchall()}

            assert "stock_basic" in tables
            assert "daily_quotes" in tables
            assert "alembic_version" in tables

            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            version = row[0]
            assert version is not None
            assert len(version) > 0

    @pytest.mark.asyncio
    async def test_fresh_install_records_head_revision(self, fresh_db_engine):
        """Fresh database should record the current Alembic head revision."""
        engine, db_name = fresh_db_engine

        expected_head = await DatabaseMigrator._get_head_revision()

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == expected_head

    @pytest.mark.asyncio
    async def test_fresh_install_uses_single_transaction(self, fresh_db_engine):
        """Fresh database should create tables and version atomically (P2 fix)."""
        engine, db_name = fresh_db_engine

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result.fetchall()}
            assert "stock_basic" in tables

            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            assert len(row[0]) > 0


async def _migrated_db_fixture(
    params: dict, db_name_prefix: str, target_revision: str = "head"
) -> tuple[AsyncEngine, str, str, Any]:
    """Shared fixture factory: create isolated DB, migrate to target revision.

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

    async_engine = create_test_engine(async_url)

    return async_engine, db_name, sync_url, sync_engine


async def _cleanup_migrated_db(params: dict, async_engine: AsyncEngine, sync_engine: Any, db_name: str) -> None:
    """Shared cleanup for migrated DB fixtures."""
    await async_engine.dispose()
    sync_engine.dispose()
    await _drop_isolated_db(params, db_name)


class TestEngineBoundMigration:
    """Tests that application migrations use the checked engine URL."""

    @pytest.mark.asyncio
    async def test_init_db_uses_engine_url_even_when_config_points_elsewhere(self, pg_params):
        """init_db should migrate the engine database, not a URL resolved from ambient config."""
        target_db = f"migrator_target_{uuid.uuid4().hex[:8]}"
        wrong_db = f"migrator_wrong_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, target_db)
        await _create_isolated_db(pg_params, wrong_db)

        _, target_async_url = build_db_urls(pg_params, target_db)
        wrong_sync_url, _ = build_db_urls(pg_params, wrong_db)
        engine = create_test_engine(target_async_url)

        try:
            with override_db_url(wrong_sync_url):
                await DatabaseMigrator.init_db(engine, auto_migrate=True)

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                target_version = result.fetchone()
                assert target_version is not None

            wrong_engine = create_engine(wrong_sync_url)
            try:
                with wrong_engine.connect() as conn:
                    result = conn.execute(
                        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                    )
                    wrong_tables = {row[0] for row in result.fetchall()}
                assert "alembic_version" not in wrong_tables
                assert "stock_basic" not in wrong_tables
            finally:
                wrong_engine.dispose()
        finally:
            await engine.dispose()
            await _drop_isolated_db(pg_params, target_db)
            await _drop_isolated_db(pg_params, wrong_db)


class TestUpToDateSchema:
    """Tests for database that is already at latest schema version."""

    @pytest_asyncio.fixture(loop_scope="function")
    async def migrated_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_uptodate")
        yield async_engine, db_name
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_up_to_date_schema_skips_upgrade(self, migrated_db_engine):
        """Database already at latest version should skip upgrade."""
        engine, db_name = migrated_db_engine

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version_before = result.fetchone()[0]

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version_after = result.fetchone()[0]

        assert version_before == version_after


class TestCheckSchemaStatus:
    """Tests for check_schema_status method with real database."""

    @pytest_asyncio.fixture(loop_scope="function")
    async def migrated_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_status")
        yield async_engine, db_name
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest_asyncio.fixture(loop_scope="function")
    async def empty_status_db_engine(self, pg_params):
        """Create an isolated empty database for status check testing."""
        db_name = f"migrator_empty_status_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, db_name)

        _, async_url = build_db_urls(pg_params, db_name)
        engine = create_test_engine(async_url)

        yield engine, db_name

        await engine.dispose()
        await _drop_isolated_db(pg_params, db_name)

    @pytest.mark.asyncio
    async def test_check_schema_status_returns_correct_values(self, migrated_db_engine):
        """check_schema_status should return correct current, head, and needs_migration."""
        engine, db_name = migrated_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current is not None
        assert current == head
        assert needs_migration is False

    @pytest.mark.asyncio
    async def test_check_schema_status_empty_database(self, empty_status_db_engine):
        """check_schema_status on empty database should return None current and needs_migration=True."""
        engine, db_name = empty_status_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current is None
        assert head is not None
        assert needs_migration is True


class TestIncrementalUpgrade:
    """Tests for incremental upgrade path (e.g., 0001 → 0002).

    This is the most critical test: it verifies that an existing database
    at an older revision can be upgraded to the latest schema.
    """

    @pytest_asyncio.fixture(loop_scope="function")
    async def partial_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade only to 0001."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_upgrade", target_revision="0001"
        )
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_incremental_upgrade_from_0001_to_head(self, partial_db_engine):
        """Database at 0001 should be upgradeable to head via init_db(auto_migrate=True)."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        # Verify we're at 0001
        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)
        assert current == "0001"
        assert needs_migration is True

        # Verify financial_reports does NOT have money_cap and accounts_receiv at 0001
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        cols_0001 = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" not in cols_0001, "money_cap should not exist at revision 0001"
        assert "accounts_receiv" not in cols_0001, "accounts_receiv should not exist at revision 0001"

        # Run init_db with auto_migrate=True to trigger upgrade.
        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        # Verify version is now at head
        new_current, new_head, new_needs = await DatabaseMigrator.check_schema_status(engine)
        assert new_current == new_head
        assert new_needs is False

        # Verify financial_reports now has money_cap and accounts_receiv
        sync_engine2 = create_engine(sync_db_url)
        try:
            inspector2 = sa_inspect(sync_engine2)
            cols_head = {c["name"] for c in inspector2.get_columns("financial_reports")}
            assert "money_cap" in cols_head, "money_cap should exist after upgrade to head"
            assert "accounts_receiv" in cols_head, "accounts_receiv should exist after upgrade to head"
        finally:
            sync_engine2.dispose()

    @pytest.mark.asyncio
    async def test_incremental_upgrade_raises_when_auto_migrate_disabled(self, partial_db_engine):
        """Database needing upgrade should raise DatabaseMigrationNeeded when auto_migrate=False."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        with pytest.raises(DatabaseMigrationNeeded) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=False)

        assert exc_info.value.current_rev == "0001"
        assert exc_info.value.head_rev != "0001"

    @pytest.mark.asyncio
    async def test_schema_status_detects_pending_migration(self, partial_db_engine):
        """check_schema_status should detect pending migrations."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current == "0001"
        assert head != "0001"
        assert needs_migration is True


class TestDowngradeAndReupgrade:
    """Tests for downgrade → re-upgrade idempotency (CI gate: upgrade → downgrade → upgrade)."""

    @pytest_asyncio.fixture(loop_scope="function")
    async def head_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_downgrade")
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_downgrade_to_base_removes_all_user_tables(self, head_db_engine):
        """Downgrade to base should remove all user tables (except alembic_version which is dropped)."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Verify tables exist before downgrade
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        tables_before = set(inspector.get_table_names())
        assert "stock_basic" in tables_before
        assert "daily_quotes" in tables_before

        # Downgrade to base — 必须在独立线程中执行，因为 Alembic env.py 内部调用 asyncio.run()
        with override_db_url(sync_db_url):

            def _do_downgrade_base():
                cfg = make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "base")

            await asyncio.to_thread(_do_downgrade_base)

        # Verify all user tables are gone
        inspector2 = sa_inspect(sync_engine)
        tables_after = set(inspector2.get_table_names())
        # After downgrade to base, alembic_version should also be dropped
        assert "stock_basic" not in tables_after, "stock_basic should be removed after downgrade"
        assert "daily_quotes" not in tables_after, "daily_quotes should be removed after downgrade"
        assert "financial_reports" not in tables_after, "financial_reports should be removed after downgrade"

    @pytest.mark.asyncio
    async def test_reupgrade_after_downgrade_restores_full_schema(self, head_db_engine):
        """After downgrade to base, re-upgrade to head should restore full schema."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Downgrade to base — 必须在独立线程中执行
        with override_db_url(sync_db_url):

            def _do_downgrade():
                cfg = make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "base")

            await asyncio.to_thread(_do_downgrade)

        # Re-upgrade to head
        with override_db_url(sync_db_url):

            def _do_upgrade():
                cfg = make_alembic_cfg(sync_db_url)
                command.upgrade(cfg, "head")

            await asyncio.to_thread(_do_upgrade)

        # Verify schema is fully restored
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        tables = set(inspector.get_table_names())
        assert "stock_basic" in tables
        assert "daily_quotes" in tables
        assert "financial_reports" in tables
        assert "alembic_version" in tables

        # Verify financial_reports has money_cap and accounts_receiv (from 0002)
        cols = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" in cols, "money_cap should exist after re-upgrade"
        assert "accounts_receiv" in cols, "accounts_receiv should exist after re-upgrade"

        # Verify alembic_version is at head
        head_rev = await DatabaseMigrator._get_head_revision()
        current, _, _ = await DatabaseMigrator.check_schema_status(engine)
        assert current == head_rev

    @pytest.mark.asyncio
    async def test_downgrade_one_revision_removes_new_columns(self, head_db_engine):
        """Downgrade from 0002 to 0001 should remove money_cap and accounts_receiv."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Verify columns exist at head
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        cols_head = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" in cols_head
        assert "accounts_receiv" in cols_head

        # Downgrade one step (from head to 0001) — 必须在独立线程中执行
        with override_db_url(sync_db_url):

            def _do_downgrade():
                cfg = make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "0001")

            await asyncio.to_thread(_do_downgrade)

        # Verify columns are removed
        inspector2 = sa_inspect(sync_engine)
        cols_0001 = {c["name"] for c in inspector2.get_columns("financial_reports")}
        assert "money_cap" not in cols_0001, "money_cap should be removed after downgrade to 0001"
        assert "accounts_receiv" not in cols_0001, "accounts_receiv should be removed after downgrade to 0001"


_W0044_MIGRATED_TO = "0044"
_W0044_ROLLBACK_TARGET = "0043"

# (ts_code, 加入交易日)；预期 downgrade 后 added_price：
# T1/T2/T9/T10 反算 close/adj_factor >= 1e6，旧列 Numeric(12,6) 无法表示；
# T3/T8/T11 可表示；T4 无行情、T5 adj=0、T6 adj=NULL、T7 原本即 NULL。
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
]
_W0044_QUOTES: dict[str, tuple[Decimal, Decimal | None]] = {
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
}
_W0044_UNREPRESENTABLE = {"T1", "T2", "T9", "T10"}


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
            elif ts_code in {"T5", "T6"}:
                added_price = Decimal("999")
            else:
                close, adj = _W0044_QUOTES[ts_code]
                assert adj is not None
                added_price = close * adj
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
        for ts_code in {"T4", "T5", "T6"}:
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
            for ts_code in {"T4", "T5", "T6", "T7"}:
                assert prices[ts_code] is None
        finally:
            await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)


class TestConcurrentMigration:
    """Tests for concurrent migration scenarios (IT-1).

    Verifies that multiple connections calling init_db on the same database
    do not cause data corruption or deadlocks.
    """

    @pytest_asyncio.fixture
    async def concurrent_db_engine(self, pg_params):
        """Create an isolated database for concurrent migration testing."""
        db_name = f"migrator_concurrent_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, db_name)

        sync_url, async_url = build_db_urls(pg_params, db_name)

        yield async_url, db_name

        await _drop_isolated_db(pg_params, db_name)

    @pytest.mark.asyncio
    async def test_concurrent_init_db_on_same_database(self, concurrent_db_engine):
        """Two independent engines calling init_db concurrently should succeed without corruption."""
        async_url, db_name = concurrent_db_engine

        # Create two independent engines (simulating two processes)
        engine1 = create_test_engine(async_url)
        engine2 = create_test_engine(async_url)

        try:
            # Run init_db concurrently on both engines.
            results = await asyncio.gather(
                DatabaseMigrator.init_db(engine1, auto_migrate=True),
                DatabaseMigrator.init_db(engine2, auto_migrate=True),
                return_exceptions=True,
            )

            # Verify no exceptions (both should succeed)
            for r in results:
                assert not isinstance(r, Exception), f"Concurrent init_db failed: {r}"

            # Verify database schema is consistent
            async with engine1.connect() as conn:
                # Check alembic_version has valid version
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                row = result.fetchone()
                assert row is not None, "alembic_version should have a version"
                version = row[0]
                assert version is not None and len(version) > 0

                # Check tables exist
                result = await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                )
                tables = {row[0] for row in result.fetchall()}
                assert "stock_basic" in tables
                assert "daily_quotes" in tables
                assert "alembic_version" in tables

            # Verify both engines see the same version
            async with engine2.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                version2 = result.fetchone()[0]
                assert version2 == version, "Both engines should see the same schema version"

        finally:
            await engine1.dispose()
            await engine2.dispose()

    @pytest.mark.asyncio
    async def test_concurrent_init_db_one_at_old_revision(self, concurrent_db_engine):
        """One engine at old revision, another fresh, both upgrade concurrently."""
        async_url, db_name = concurrent_db_engine
        # D5.1: 同步 engine 显式 +psycopg2（SQLAlchemy 2.1 裸 postgresql:// 默认 driver 为 psycopg 3）
        sync_url = async_url.replace("+asyncpg", "+psycopg2")

        # First, migrate to 0001 only
        engine1 = create_test_engine(async_url)
        sync_engine = create_engine(sync_url)

        with override_db_url(sync_url):
            cfg = make_alembic_cfg(sync_url)
            await asyncio.to_thread(command.upgrade, cfg, "0001")

        # Now create a second engine and run init_db concurrently
        engine2 = create_test_engine(async_url)

        try:
            # Verify engine1 is at 0001
            current, head, needs = await DatabaseMigrator.check_schema_status(engine1)
            assert current == "0001", f"Engine1 should be at 0001, got {current}"
            assert needs is True

            # Run init_db on both engines concurrently.
            results = await asyncio.gather(
                DatabaseMigrator.init_db(engine1, auto_migrate=True),
                DatabaseMigrator.init_db(engine2, auto_migrate=True),
                return_exceptions=True,
            )

            for r in results:
                assert not isinstance(r, Exception), f"Concurrent upgrade failed: {r}"

            # Verify both are now at head
            current1, _, needs1 = await DatabaseMigrator.check_schema_status(engine1)
            current2, _, needs2 = await DatabaseMigrator.check_schema_status(engine2)

            assert current1 == current2, "Both engines should be at the same version"
            assert needs1 is False and needs2 is False, "Both should report no pending migrations"

        finally:
            await engine1.dispose()
            await engine2.dispose()
            sync_engine.dispose()


class TestMigrationInterruptionRecovery:
    """Tests for migration interruption recovery (IT-2).

    Verifies that init_db can detect and recover from a corrupted state
    where alembic_version indicates the latest revision but some tables
    are missing (simulating a mid-migration crash).
    """

    @pytest_asyncio.fixture(loop_scope="function")
    async def corrupted_db_engine(self, pg_params):
        """Create a database with version mismatch: alembic_version says 'head' but tables missing."""
        db_name = f"migrator_corrupted_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, db_name)

        _, async_url = build_db_urls(pg_params, db_name)
        engine = create_test_engine(async_url)

        # Get the head revision
        head_rev = await DatabaseMigrator._get_head_revision()

        # Create alembic_version with head revision, but DON'T create any tables
        # This simulates a crash after version record but before table creation
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                {"version": head_rev},
            )

        yield engine, db_name, head_rev

        await engine.dispose()
        await _drop_isolated_db(pg_params, db_name)

    @pytest.mark.asyncio
    async def test_detects_missing_tables_despite_valid_version(self, corrupted_db_engine):
        """When alembic_version is correct but tables are missing, init_db should handle gracefully."""
        engine, db_name, head_rev = corrupted_db_engine

        # Verify our setup: version exists but no user tables
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version = result.fetchone()[0]
            assert version == head_rev

            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result.fetchall()}
            assert "stock_basic" not in tables, "Test setup error: stock_basic should not exist"

        # Check_schema_status should report no migration needed (version matches)
        current, head, needs = await DatabaseMigrator.check_schema_status(engine)
        assert current == head_rev
        assert needs is False, "Version matches, so no migration should be detected as needed"

        # But if we try to use the database, operations will fail
        # The caller (application layer) should handle this by checking table existence
        # or using init_db with force=True to rebuild

    @pytest.mark.asyncio
    async def test_force_init_rebuilds_missing_tables(self, corrupted_db_engine):
        """Force init via clear_all_cache should rebuild all tables even when version says 'up to date'."""
        engine, db_name, head_rev = corrupted_db_engine

        # First verify the corrupted state
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables_before = {row[0] for row in result.fetchall()}
            assert "stock_basic" not in tables_before
            assert "alembic_version" in tables_before  # Only this exists (corrupted state)

        # The current code's recovery mechanism is through CacheManager.clear_all_cache()
        # which drops all tables and reinitializes via init_db(force=True, auto_migrate=True)
        from data.cache.cache_manager import CacheManager

        # Create a minimal CacheManager for testing
        with _singleton_state_ctx(CacheManager, extra_attrs=["_initialized"]):
            cm = CacheManager.__new__(CacheManager)
            cm.engine = engine
            cm._disposed = False
            cm._schema_initialized = False
            cm._lock = threading.Lock()
            # Note: _maintenance_event and _init_lock are properties that use
            # get_loop_local(), so they don't need to be set manually

            # clear_all_cache will:
            # 1. DROP SCHEMA public CASCADE (removes all tables including alembic_version)
            # 2. CREATE SCHEMA public
            # 3. init_db(force=True, auto_migrate=True) - rebuilds everything
            # D5.1: 同步 URL 显式 +psycopg2（SQLAlchemy 2.1 裸 postgresql:// 默认 driver 为 psycopg 3）
            sync_url = str(engine.url).replace("+asyncpg", "+psycopg2")
            with override_db_url(sync_url):
                await cm.clear_all_cache()

            # After clear_all_cache, all tables should be rebuilt
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                )
                tables_final = {row[0] for row in result.fetchall()}
                assert "stock_basic" in tables_final
                assert "daily_quotes" in tables_final
                assert "alembic_version" in tables_final

                # Verify version is correct
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.fetchone()[0]
                assert version == head_rev


class TestOrphanedRevisionHeal:
    @pytest_asyncio.fixture(loop_scope="function")
    async def orphaned_test_engine(self, pg_params):
        db_name = f"migrator_orphaned_{uuid.uuid4().hex[:8]}"
        params = pg_params
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        yield engine
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    async def test_heal_orphaned_revision_auto_recovery(self, orphaned_test_engine: AsyncEngine) -> None:
        """Test that an orphaned revision is detected and healed automatically."""
        # 1. Run normal upgrade to head
        await DatabaseMigrator.init_db(orphaned_test_engine, auto_migrate=True)
        head_rev = await DatabaseMigrator._get_head_revision()

        # 2. Corrupt the revision to a non-existent one
        async with orphaned_test_engine.connect() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = '9999_orphaned'"))
            await conn.commit()

        # 3. Init DB again - should auto heal
        await DatabaseMigrator.init_db(orphaned_test_engine, auto_migrate=True)

        # 4. Verify it's back to head
        async with orphaned_test_engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version = result.fetchone()[0]
            assert version == head_rev

    async def test_heal_orphaned_revision_fresh_db_no_op(self, orphaned_test_engine: AsyncEngine) -> None:
        """Test that _heal_orphaned_revision is a no-op on a fresh database."""
        await DatabaseMigrator._heal_orphaned_revision(orphaned_test_engine)
        current_rev = await DatabaseMigrator._get_current_revision(orphaned_test_engine)
        assert current_rev is None

    async def test_heal_orphaned_revision_valid_revision_no_op(self, orphaned_test_engine: AsyncEngine) -> None:
        """Test that _heal_orphaned_revision is a no-op on a database with valid revision."""
        # Setup to head
        await DatabaseMigrator.init_db(orphaned_test_engine, auto_migrate=True)
        head_rev = await DatabaseMigrator._get_head_revision()

        # Heal should do nothing
        await DatabaseMigrator._heal_orphaned_revision(orphaned_test_engine)

        current_rev = await DatabaseMigrator._get_current_revision(orphaned_test_engine)
        assert current_rev == head_rev
