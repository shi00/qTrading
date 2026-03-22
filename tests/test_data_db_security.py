import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config
from data.database_manager import DatabaseManager
from tests.test_infra_base import TEST_DB_URL, TestDatabaseBase


class TestDatabaseManagerSecurity(TestDatabaseBase):
    """Test database security features using test_astock PostgreSQL database."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        config.DB_URL_SYNC = "postgresql://postgres:123456@localhost:5432/test_astock"
        self.db_manager = DatabaseManager()

        self._ddl_engine = create_async_engine(TEST_DB_URL, echo=False)

        async with self._ddl_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS test_users (id SERIAL PRIMARY KEY, username TEXT, password TEXT)"
                )
            )
            await conn.execute(text("DELETE FROM test_users"))
            await conn.execute(
                text(
                    "INSERT INTO test_users (username, password) VALUES ('admin', 'secret')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO test_users (username, password) VALUES ('guest', '12345')"
                )
            )

    async def asyncTearDown(self):
        if hasattr(self, "db_manager"):
            self.db_manager.close()
        if hasattr(self, "_ddl_engine"):
            await self._ddl_engine.dispose()
        await super().asyncTearDown()

    def test_query_table_sql_injection_in_filter(self):
        injection_payload = "' OR '1'='1"
        filters = [("username", "=", injection_payload)]
        df = self.db_manager.query_table("test_users", filters=filters)
        self.assertEqual(len(df), 0)

        df_valid = self.db_manager.query_table(
            "test_users",
            filters=[("username", "=", "admin")],
        )
        self.assertEqual(len(df_valid), 1)

    def test_execute_sql_chained_commands(self):
        sql = "SELECT * FROM test_users; DELETE FROM test_users;"
        result = self.db_manager.execute_sql(sql)
        self.assertFalse(result["success"])
        self.assertIn("Only SELECT statements are allowed", result.get("error", ""))

        count = self.db_manager.get_table_count("test_users")
        self.assertEqual(count, 2)

    def test_execute_sql_non_select(self):
        result = self.db_manager.execute_sql(
            "INSERT INTO test_users (username, password) VALUES ('hacker', '123')",
        )
        self.assertFalse(result["success"])
        self.assertIn("Only SELECT statements are allowed", result.get("error", ""))

        result = self.db_manager.execute_sql(
            "UPDATE test_users SET password='hacked' WHERE id=1",
        )
        self.assertFalse(result["success"])
        self.assertIn("Only SELECT statements are allowed", result.get("error", ""))

        result = self.db_manager.execute_sql("DELETE FROM test_users")
        self.assertFalse(result["success"])
        self.assertIn("Only SELECT statements are allowed", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
