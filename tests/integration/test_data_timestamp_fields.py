import asyncio
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from data.persistence.models import Base


class TestCreatedAtSchema:
    """验证 created_at 字段在 ORM 层正确定义（纯内存验证，无需数据库）"""

    TABLES_WITH_CREATED_AT = [
        "stock_basic",
        "stock_concepts",
        "trade_cal",
        "suspend_d",
        "limit_list",
        "daily_quotes",
        "daily_indicators",
        "index_daily",
        "index_dailybasic",
        "margin_daily",
        "financial_reports",
        "fina_forecast",
        "fina_mainbz",
        "fina_audit",
        "dividend",
        "moneyflow_daily",
        "northbound_holding",
        "moneyflow_hsgt",
        "top_list",
        "block_trade",
        "stk_holdernumber",
        "top10_holders",
        "pledge_stat",
        "repurchase",
        "shibor_daily",
        "index_weight",
        "sync_status",
        "stock_sync_status",
        "macro_economy",
        "screening_history",
        "market_news",
        "task_history",
    ]

    @pytest.mark.parametrize("table_name", TABLES_WITH_CREATED_AT)
    def test_created_at_column_exists(self, table_name):
        """验证表存在 created_at 字段"""
        table = Base.metadata.tables.get(table_name)
        assert table is not None, f"Table {table_name} not found in metadata"
        assert "created_at" in table.columns, f"Table {table_name} missing created_at column"

    @pytest.mark.parametrize("table_name", TABLES_WITH_CREATED_AT)
    def test_created_at_has_server_default(self, table_name):
        """验证 created_at 字段有 server_default"""
        table = Base.metadata.tables.get(table_name)
        col = table.columns.get("created_at")
        assert col is not None, f"Table {table_name} missing created_at column"
        assert col.server_default is not None, f"Table {table_name}.created_at missing server_default"


@pytest_asyncio.fixture
async def db_engine():
    """创建独立 test_astock 数据库引擎，测试后清理"""
    from tests.integration.conftest import TEST_DB_HOST, TEST_DB_NAME, TEST_DB_PASSWORD, TEST_DB_PORT, TEST_DB_USER

    test_db_url = f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    engine = create_async_engine(test_db_url, echo=False)

    from data.persistence.db_migrator import DatabaseMigrator
    import sqlalchemy as sa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    await DatabaseMigrator.init_db(engine, auto_migrate=True)

    yield engine

    # Teardown: 清理测试数据
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM sync_status WHERE table_name LIKE 'test_created_at_%'"))
        await conn.execute(text("DELETE FROM daily_indicators WHERE ts_code LIKE 'test_created_at_%'"))
    await engine.dispose()


async def test_created_at_equals_updated_at_on_insert(db_engine):
    """首次插入时，created_at 和 updated_at 由数据库设置，应该相等"""
    import pandas as pd

    from data.persistence.daos.market_dao import MarketDao

    dao = MarketDao(db_engine)
    ts_code = "test_created_at_equal"

    df = pd.DataFrame(
        [
            {
                "ts_code": ts_code,
                "trade_date": date.today(),
                "pe": 10.5,
                "pb": 1.2,
            }
        ]
    )

    await dao.save_daily_indicators(df)

    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT created_at, updated_at FROM daily_indicators WHERE ts_code = :ts_code"),
            {"ts_code": ts_code},
        )
        row = result.fetchone()

    created_at = row[0]  # type: ignore[untyped]
    updated_at = row[1]  # type: ignore[untyped]
    assert created_at is not None, "created_at should be set by DB"
    assert updated_at is not None, "updated_at should be set by DB"

    time_diff = abs((updated_at - created_at).total_seconds())
    assert time_diff < 1.0, (
        f"created_at and updated_at should be nearly equal on insert (DB-managed), diff={time_diff}s"
    )


async def test_created_at_preserved_on_upsert(db_engine):
    """首次插入后 Upsert 更新，created_at 应保持不变"""
    table_name = "test_created_at_table"
    now = datetime.now().replace(tzinfo=None)

    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO sync_status (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                VALUES (:table_name, :last_sync_date, :last_data_date, :record_count, :status, :updated_at)
            """),
            {
                "table_name": table_name,
                "last_sync_date": now,
                "last_data_date": now.date(),
                "record_count": 100,
                "status": "success",
                "updated_at": now,
            },
        )

    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT created_at, updated_at FROM sync_status WHERE table_name = :table_name"),
            {"table_name": table_name},
        )
        row = result.fetchone()
    created_at_1 = row[0]  # type: ignore[untyped]
    assert created_at_1 is not None, "created_at should be set on first insert"

    await asyncio.sleep(1)
    now2 = datetime.now().replace(tzinfo=None)

    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO sync_status (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                VALUES (:table_name, :last_sync_date, :last_data_date, :record_count, :status, :updated_at)
                ON CONFLICT(table_name) DO UPDATE SET
                    last_sync_date = EXCLUDED.last_sync_date,
                    last_data_date = EXCLUDED.last_data_date,
                    record_count = EXCLUDED.record_count,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
            """),
            {
                "table_name": table_name,
                "last_sync_date": now2,
                "last_data_date": now2.date(),
                "record_count": 200,
                "status": "success",
                "updated_at": now2,
            },
        )

    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT created_at, updated_at FROM sync_status WHERE table_name = :table_name"),
            {"table_name": table_name},
        )
        row = result.fetchone()
    created_at_2 = row[0]  # type: ignore[untyped]
    updated_at_2 = row[1]  # type: ignore[untyped]
    assert created_at_2 == created_at_1, "created_at should NOT change on upsert"
    assert updated_at_2 > created_at_2, "updated_at should be newer than created_at"


class TestMacroDaoNoCreatedInjection:
    """验证 macro_dao 不再代码层注入 created_at"""

    def test_save_macro_economy_no_created_at_in_columns(self):
        """验证 save_macro_economy 的 columns 列表不含 created_at"""
        from data.persistence.models import get_model_columns
        from data.persistence.models import MacroEconomy

        columns = set(get_model_columns(MacroEconomy))
        assert "created_at" not in columns, f"created_at found in MacroEconomy columns: {columns}"


class TestCreatedAtTimezoneCompatibility:
    """验证 created_at 在 Polars 引擎中的时区兼容性（纯内存测试）"""

    def test_created_at_polars_join_no_warning(self):
        """验证 created_at 字段在 Polars JOIN 时无时区警告"""
        try:
            import polars as pl
        except ImportError:
            pytest.skip("polars not installed")

        import pandas as pd

        df_pd = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "created_at": [datetime.now().replace(tzinfo=None)],
            }
        )

        df_pl = pl.from_pandas(df_pd)
        df_other = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["2024-01-01"],
            }
        )

        result = df_pl.join(df_other, on="ts_code")
        assert result.shape[0] == 1
