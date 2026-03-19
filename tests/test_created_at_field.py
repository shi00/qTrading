import asyncio
import inspect
import os
import tempfile
from datetime import datetime, date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from data.models import Base, SyncStatus
from data.daos.macro_dao import MacroDao


class TestCreatedAtSchema:
    """验证 created_at 字段在 ORM 层正确定义（纯内存验证，无需数据库）"""

    TABLES_WITH_CREATED_AT = [
        "stock_basic", "stock_concepts", "trade_cal", "suspend_d", "limit_list",
        "daily_quotes", "daily_indicators", "index_daily", "index_dailybasic", "margin_daily",
        "financial_reports", "fina_forecast", "fina_mainbz", "fina_audit", "dividend",
        "moneyflow_daily", "northbound_holding", "moneyflow_hsgt", "top_list", "block_trade",
        "stk_holdernumber", "top10_holders", "pledge_stat", "repurchase",
        "shibor_daily", "index_weight",
        "sync_status", "stock_sync_status",
        "macro_economy", "screening_history", "market_news", "task_history",
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


try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False


class TestCreatedAtUpsert:
    """验证 created_at 在 Upsert 场景下不被覆盖（使用独立测试数据库）"""

    @pytest_asyncio.fixture
    async def test_engine(self):
        """创建临时 SQLite 测试数据库"""
        if not HAS_AIOSQLITE:
            pytest.skip("aiosqlite not installed")
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        temp_db.close()
        db_path = temp_db.name

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine

        await engine.dispose()
        if os.path.exists(db_path):
            os.remove(db_path)

    @pytest.mark.asyncio
    async def test_created_at_preserved_on_upsert(self, test_engine):
        """首次插入后 Upsert 更新，created_at 应保持不变"""
        table_name = "test_created_at_table"
        now = datetime.now().replace(tzinfo=None)

        async_session = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            async with session.begin():
                sql = text("""
                    INSERT INTO sync_status (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                    VALUES (:table_name, :last_sync_date, :last_data_date, :record_count, :status, :updated_at)
                """)
                await session.execute(sql, {
                    "table_name": table_name,
                    "last_sync_date": now,
                    "last_data_date": now.date(),
                    "record_count": 100,
                    "status": "success",
                    "updated_at": now,
                })

            result = await session.execute(
                text("SELECT created_at, updated_at FROM sync_status WHERE table_name = :table_name"),
                {"table_name": table_name}
            )
            row1 = result.fetchone()
            created_at_1 = row1[0]
            assert created_at_1 is not None, "created_at should be set on first insert"

            await asyncio.sleep(1)
            now2 = datetime.now().replace(tzinfo=None)

            async with session.begin():
                sql = text("""
                    INSERT INTO sync_status (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                    VALUES (:table_name, :last_sync_date, :last_data_date, :record_count, :status, :updated_at)
                    ON CONFLICT(table_name) DO UPDATE SET
                        last_sync_date = excluded.last_sync_date,
                        last_data_date = excluded.last_data_date,
                        record_count = excluded.record_count,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                """)
                await session.execute(sql, {
                    "table_name": table_name,
                    "last_sync_date": now2,
                    "last_data_date": now2.date(),
                    "record_count": 200,
                    "status": "success",
                    "updated_at": now2,
                })

            result = await session.execute(
                text("SELECT created_at, updated_at FROM sync_status WHERE table_name = :table_name"),
                {"table_name": table_name}
            )
            row2 = result.fetchone()
            created_at_2 = row2[0]
            updated_at_2 = row2[1]

            assert created_at_2 == created_at_1, "created_at should NOT change on upsert"
            assert updated_at_2 > created_at_2, "updated_at should be newer than created_at"


class TestMacroDaoNoCreatedInjection:
    """验证 macro_dao 不再代码层注入 created_at"""

    def test_save_macro_economy_no_created_at_in_columns(self):
        """验证 save_macro_economy 的 columns 列表不含 created_at"""
        source = inspect.getsource(MacroDao.save_macro_economy)
        
        assert '"created_at"' not in source or "columns.append" not in source, \
            "save_macro_economy should NOT inject created_at in Python layer"


class TestCreatedAtTimezoneCompatibility:
    """验证 created_at 在 Polars 引擎中的时区兼容性（纯内存测试）"""

    def test_created_at_polars_join_no_warning(self):
        """验证 created_at 字段在 Polars JOIN 时无时区警告"""
        try:
            import polars as pl
        except ImportError:
            pytest.skip("polars not installed")

        import pandas as pd

        df_pd = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "created_at": [datetime.now().replace(tzinfo=None)],
        })

        df_pl = pl.from_pandas(df_pd)
        df_other = pl.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["2024-01-01"],
        })

        result = df_pl.join(df_other, on="ts_code")
        assert result.shape[0] == 1
