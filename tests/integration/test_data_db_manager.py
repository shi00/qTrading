from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config
from tests.integration.conftest import TEST_DB_HOST, TEST_DB_NAME, TEST_DB_PASSWORD, TEST_DB_PORT, TEST_DB_USER
from data.persistence.database_manager import DatabaseManager
from tests.integration.test_infra_base import TEST_DB_URL, TestDatabaseBase


class TestDatabaseManager(TestDatabaseBase):
    """Test database manager using test_astock PostgreSQL database."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        config.DB_URL_SYNC = (
            f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
        )
        self.db_manager = DatabaseManager()

        self._ddl_engine = create_async_engine(TEST_DB_URL, echo=False)

        async with self._ddl_engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE IF NOT EXISTS test_stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
            )
            await conn.execute(text("DELETE FROM test_stock_basic"))
            await conn.execute(text("INSERT INTO test_stock_basic VALUES ('000001.SZ', 'PingAn')"))
            await conn.execute(text("INSERT INTO test_stock_basic VALUES ('600519.SH', 'Moutai')"))

            await conn.execute(
                text("CREATE TABLE IF NOT EXISTS test_daily_quotes (ts_code TEXT, trade_date TEXT, close REAL)")
            )
            await conn.execute(text("DELETE FROM test_daily_quotes"))
            for i in range(100):
                date = f"202301{i:02d}" if i < 31 else "20230201"
                await conn.execute(text(f"INSERT INTO test_daily_quotes VALUES ('000001.SZ', '{date}', {10.0 + i})"))

    async def asyncTearDown(self):
        async with self._ddl_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS test_stock_basic"))
            await conn.execute(text("DROP TABLE IF EXISTS test_daily_quotes"))
        if hasattr(self, "db_manager"):
            self.db_manager.close()
        if hasattr(self, "_ddl_engine"):
            await self._ddl_engine.dispose()
        await super().asyncTearDown()

    def test_get_all_tables(self):
        tables = self.db_manager.get_all_tables()
        self.assertIn("test_stock_basic", tables)
        self.assertIn("test_daily_quotes", tables)

    def test_get_table_schema(self):
        schema = self.db_manager.get_table_schema("test_stock_basic")
        cols = {col["name"]: col["type"] for col in schema}
        self.assertIn("ts_code", cols)
        self.assertIn("name", cols)

    def test_query_table_pagination(self):
        df = self.db_manager.query_table("test_daily_quotes", page=1, page_size=10)
        self.assertEqual(len(df), 10)

        df2 = self.db_manager.query_table("test_daily_quotes", page=2, page_size=10)
        self.assertEqual(len(df2), 10)

    def test_execute_sql_valid(self):
        result = self.db_manager.execute_sql(
            "SELECT * FROM test_stock_basic WHERE name = 'PingAn'",
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["data"]), 1)

    def test_execute_sql_security_and_limits(self):
        result = self.db_manager.execute_sql("DELETE FROM test_stock_basic")
        self.assertFalse(result["success"])
        self.assertIn("Only SELECT", result.get("error", ""))

    def test_validate_table_name_security(self):
        df = self.db_manager.query_table("invalid_table")
        self.assertTrue(df.empty)

    async def test_execute_sql_memory_limit(self):
        async with self._ddl_engine.begin() as conn:
            for i in range(2500):
                await conn.execute(text(f"INSERT INTO test_daily_quotes VALUES ('TMP', '2023{i}', {float(i)})"))

        result = self.db_manager.execute_sql("SELECT * FROM test_daily_quotes")
        self.assertTrue(result["success"])
        df = result["data"]
        self.assertEqual(len(df), 2000)
        self.assertIn("truncated", result["error"])
