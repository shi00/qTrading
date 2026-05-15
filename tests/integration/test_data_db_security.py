import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config
from tests.integration.conftest import TEST_DB_HOST, TEST_DB_NAME, TEST_DB_PASSWORD, TEST_DB_PORT, TEST_DB_USER
from data.persistence.database_manager import DatabaseManager
from tests.integration.test_infra_base import TEST_DB_URL, TestDatabaseBase


class TestDatabaseManagerSecurity(TestDatabaseBase):
    """Test database security features using test_astock PostgreSQL database."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        config.DB_URL_SYNC = (
            f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
        )
        self.db_manager = DatabaseManager()

        self._ddl_engine = create_async_engine(TEST_DB_URL, echo=False)

        async with self._ddl_engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE IF NOT EXISTS test_users (id SERIAL PRIMARY KEY, username TEXT, password TEXT)")
            )
            await conn.execute(text("DELETE FROM test_users"))
            await conn.execute(text("INSERT INTO test_users (username, password) VALUES ('admin', 'secret')"))
            await conn.execute(text("INSERT INTO test_users (username, password) VALUES ('guest', '12345')"))

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


class TestSafeIdentifier(unittest.TestCase):
    """Test _is_safe_identifier validation for SQL injection prevention."""

    def test_valid_identifiers(self):
        from data.persistence.daos.quote_dao import _is_safe_identifier

        self.assertTrue(_is_safe_identifier("daily_quotes"))
        self.assertTrue(_is_safe_identifier("trade_date"))
        self.assertTrue(_is_safe_identifier("table_1"))
        self.assertTrue(_is_safe_identifier("a"))

    def test_invalid_identifiers(self):
        from data.persistence.daos.quote_dao import _is_safe_identifier

        self.assertFalse(_is_safe_identifier(""))
        self.assertFalse(_is_safe_identifier("DROP TABLE"))
        self.assertFalse(_is_safe_identifier("1; DROP TABLE"))
        self.assertFalse(_is_safe_identifier("table; DROP TABLE users"))
        self.assertFalse(_is_safe_identifier("table--comment"))
        self.assertFalse(_is_safe_identifier("table' OR '1'='1"))
        self.assertFalse(_is_safe_identifier("UPPERCASE"))
        self.assertFalse(_is_safe_identifier("table name"))

    def test_cached_dates_whitelist_rejection(self):
        from data.persistence.daos.quote_dao import _is_safe_identifier

        malicious_names = [
            "daily_quotes; DROP TABLE users--",
            "1; DELETE FROM daily_quotes",
            "daily_quotes' UNION SELECT * FROM users--",
        ]
        for name in malicious_names:
            self.assertFalse(_is_safe_identifier(name), f"Should reject: {name}")


if __name__ == "__main__":
    unittest.main()
