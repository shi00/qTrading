import unittest
import os
import sqlite3
from data.database_manager import DatabaseManager


class TestDatabaseManager(unittest.TestCase):
    def setUp(self):
        # Create a temporary test database
        self.test_db = "test_astock_ro.db"
        # Ensure absolute path for URI testing
        self.test_db_abs = os.path.abspath(self.test_db)

        # Setup initial DB
        conn = sqlite3.connect(self.test_db_abs)
        cursor = conn.cursor()

        # Create sample tables
        cursor.execute("CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
        cursor.execute(
            "INSERT INTO stock_basic VALUES ('000001.SZ', 'PingAn'), ('600519.SH', 'Moutai')"
        )

        cursor.execute(
            "CREATE TABLE daily_quotes (ts_code TEXT, trade_date TEXT, close REAL)"
        )
        for i in range(100):
            date = f"202301{i:02d}" if i < 31 else "20230201"
            cursor.execute(
                f"INSERT INTO daily_quotes VALUES ('000001.SZ', '{date}', {10.0 + i})"
            )

        conn.commit()
        conn.close()

        from data import database_manager

        database_manager.config.DB_URL_SYNC = f"sqlite:///{self.test_db_abs}"
        self.db_manager = DatabaseManager()

    def tearDown(self):
        if hasattr(self, "db_manager"):
            self.db_manager.close()
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except OSError:
                pass

    def test_get_all_tables(self):
        tables = self.db_manager.get_all_tables()
        self.assertIn("stock_basic", tables)
        self.assertIn("daily_quotes", tables)

    def test_get_table_schema(self):
        schema = self.db_manager.get_table_schema("stock_basic")
        cols = {col["name"]: col["type"] for col in schema}
        self.assertEqual(cols["ts_code"], "TEXT")
        self.assertEqual(cols["name"], "TEXT")

    def test_query_table_pagination(self):
        # Page 1, Size 10
        df = self.db_manager.query_table("daily_quotes", page=1, page_size=10)
        self.assertEqual(len(df), 10)

        # Page 2, Size 10
        df2 = self.db_manager.query_table("daily_quotes", page=2, page_size=10)
        self.assertEqual(len(df2), 10)

    def test_execute_sql_valid(self):
        result = self.db_manager.execute_sql(
            "SELECT * FROM stock_basic WHERE name = 'PingAn'"
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["data"]), 1)

    def test_execute_sql_security_and_limits(self):
        """Combined test for sqlparse security and memory limits"""

        # 1. Test Forbidden Keywords (Blocked by sqlparse)
        result = self.db_manager.execute_sql("DELETE FROM stock_basic")
        self.assertFalse(result["success"])
        # Current logic returns "Only SELECT statements are allowed"
        self.assertIn("Only SELECT", result.get("error", ""))

    def test_validate_table_name_security(self):
        """Test that invalid table names are rejected."""
        # 'users' exists (created in setUp), 'invalid_table' does not
        df = self.db_manager.query_table("invalid_table")
        self.assertTrue(df.empty)

    def test_execute_sql_memory_limit(self):
        """Test that execute_sql strictly limits row count."""
        # Insert enough rows to trigger limit (MAX_FETCH = 2000)
        self.conn = sqlite3.connect(self.test_db_abs)
        self.cursor = self.conn.cursor()
        # We need > 2000 rows
        data = [("TMP", f"2023{i}", float(i)) for i in range(2500)]
        self.cursor.executemany("INSERT INTO daily_quotes VALUES (?, ?, ?)", data)
        self.conn.commit()
        self.conn.close()

        # Query all
        result = self.db_manager.execute_sql("SELECT * FROM daily_quotes")
        self.assertTrue(result["success"])
        df = result["data"]
        # Should be capped at 2000
        self.assertEqual(len(df), 2000)
        self.assertIn("truncated", result["error"])


if __name__ == "__main__":
    unittest.main()
