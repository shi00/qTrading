import unittest
import os
import shutil
import sqlite3
import pandas as pd
import config
from data.database_manager import DatabaseManager

class TestDatabaseManagerSecurity(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary test database
        self.test_db = "test_astock_security.db"
        self.test_db_abs = os.path.abspath(self.test_db)
        
        # Setup initial DB
        conn = sqlite3.connect(self.test_db_abs)
        cursor = conn.cursor()
        
        # Create sample tables
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)")
        cursor.execute("INSERT INTO users VALUES (1, 'admin', 'secret')")
        cursor.execute("INSERT INTO users VALUES (2, 'guest', '12345')")
        
        conn.commit()
        conn.close()
        
        from data import database_manager
        database_manager.config.DB_URL_SYNC = f"sqlite:///{self.test_db_abs}"
        self.db_manager = DatabaseManager()

    def tearDown(self):
        if hasattr(self, 'db_manager'):
            self.db_manager.close()
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except:
                pass

    def test_query_table_sql_injection_in_filter(self):
        """Test if Table Viewer filter is vulnerable to injection"""
        # Attempt to inject ' OR '1'='1
        # If vulnerable: SELECT * FROM users WHERE username = '' OR '1'='1' -> Returns all users
        # If safe: SELECT * FROM users WHERE username = "' OR '1'='1" -> Returns nothing (no user with that name)
        
        injection_payload = "' OR '1'='1"
        filters = [('username', '=', injection_payload)]
        
        df = self.db_manager.query_table("users", filters=filters)
        
        # Should return 0 results because no user has that weird name
        self.assertEqual(len(df), 0)
        
        # Verify valid query works
        df_valid = self.db_manager.query_table("users", filters=[('username', '=', 'admin')])
        self.assertEqual(len(df_valid), 1)

    def test_execute_sql_chained_commands(self):
        """Test if SQL Console executes chained commands (potential injection)"""
        # Attempt: SELECT * FROM users; DELETE FROM users;
        
        sql = "SELECT * FROM users; DELETE FROM users;"
        
        # This should fail due to sqlparse check on second statement
        result = self.db_manager.execute_sql(sql)
        self.assertFalse(result['success'])
        # Expecting the new error message from sqlparse logic
        self.assertIn("Only SELECT statements are allowed", result.get('error', ''))
        
        # Verify data still exists
        count = self.db_manager.get_table_count("users")
        self.assertEqual(count, 2)

    def test_execute_sql_non_select(self):
        """Test if sqlparse blocks INSERT/UPDATE/DELETE"""
        
        # INSERT
        result = self.db_manager.execute_sql("INSERT INTO users VALUES (3, 'hacker', '123')")
        self.assertFalse(result['success'])
        self.assertIn("Only SELECT statements are allowed", result.get('error', ''))
        
        # UPDATE
        result = self.db_manager.execute_sql("UPDATE users SET password='hacked' WHERE id=1")
        self.assertFalse(result['success'])
        self.assertIn("Only SELECT statements are allowed", result.get('error', ''))
        
        # DELETE
        result = self.db_manager.execute_sql("DELETE FROM users")
        self.assertFalse(result['success'])
        self.assertIn("Only SELECT statements are allowed", result.get('error', ''))

if __name__ == '__main__':
    unittest.main()
