import logging
import re
import sqlite3
from contextlib import contextmanager

import pandas as pd
import sqlparse

import config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages database interactions for the Data Explorer feature.
    Provides read-only access (with safety checks) to the SQLite database.
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    @contextmanager
    def _get_conn(self):
        """Context manager for database connection"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def get_all_tables(self):
        """
        Get a list of all tables in the database.
        Returns:
            list[str]: List of table names.
        """
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
                tables = [row[0] for row in cursor.fetchall()]
                return tables
        except Exception as e:
            logger.error(f"Error fetching tables: {e}")
            return []

    def get_table_schema(self, table_name):
        """
        Get the schema (column names and types) for a specific table.
        Returns:
            list[dict]: List of column info {'name': 'col_name', 'type': 'TEXT'}
        """
        try:
            self._validate_table_name(table_name)
            with self._get_conn() as conn:
                cursor = conn.cursor()
                # Use PRAGMA table_info to safely get schema
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = []
                for row in cursor.fetchall():
                    # row format: (cid, name, type, notnull, dflt_value, pk)
                    columns.append({
                        'name': row[1],
                        'type': row[2]
                    })
                return columns
        except Exception as e:
            logger.error(f"Error fetching schema for {table_name}: {e}")
            return []

    def get_table_count(self, table_name, filters=None):
        """
        Get total row count for a table, optionally with filters.
        
        Args:
            table_name (str): Name of the table.
            filters (list): List of filter tuples (column, operator, value). 
                            Example: [('ts_code', '=', '000001.SZ')]
        """
        try:
            self._validate_table_name(table_name)
            query = f"SELECT COUNT(*) FROM {table_name}"
            params = []

            if filters:
                where_clauses = []
                for col, op, val in filters:
                    # Basic SQL injection prevention for operators
                    if op not in ['=', '>', '<', '>=', '<=', 'LIKE', '!=']:
                        continue
                    where_clauses.append(f"{col} {op} ?")
                    params.append(val)

                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error counting rows for {table_name}: {e}")
            return 0

    def _validate_table_name(self, table_name):
        """Ensure table name is valid and exists to prevent injection."""
        allowed_tables = self.get_all_tables()
        if table_name not in allowed_tables:
            raise ValueError(f"Invalid table name: {table_name}")

    def query_table(self, table_name, page=1, page_size=50, filters=None, sort_col=None, sort_asc=True):
        """
        Query data from a table with pagination and filtering.
        
        Returns:
            pd.DataFrame: DataFrame containing the result rows.
        """
        try:
            self._validate_table_name(table_name)

            offset = (page - 1) * page_size
            query = f"SELECT * FROM {table_name}"
            params = []

            # 1. Apply Filters
            if filters:
                where_clauses = []
                for col, op, val in filters:
                    if op not in ['=', '>', '<', '>=', '<=', 'LIKE', '!=']:
                        continue
                    # Simple column name validation (alphanumeric)
                    if not re.match(r'^[a-zA-Z0-9_]+$', col):
                        continue

                    where_clauses.append(f"{col} {op} ?")
                    # Handle LIKE wildcards if not present
                    if op == 'LIKE' and '%' not in str(val):
                        val = f"%{val}%"
                    params.append(val)

                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

            # 2. Apply Sorting
            if sort_col:
                # Sanitize sort_col to prevent injection (simple check)
                clean_col = re.sub(r'[^a-zA-Z0-9_]', '', sort_col)
                direction = "ASC" if sort_asc else "DESC"
                query += f" ORDER BY {clean_col} {direction}"
            elif table_name == 'daily_quotes':
                # Default sort for common tables
                query += " ORDER BY trade_date DESC, ts_code ASC"

            # 3. Apply Pagination
            query += f" LIMIT {page_size} OFFSET {offset}"

            with self._get_conn() as conn:
                # Use pandas for easier DataFrame creation
                df = pd.read_sql_query(query, conn, params=params)
                return df

        except Exception as e:
            logger.error(f"Error querying table {table_name}: {e}")
            return pd.DataFrame()

    def execute_sql(self, sql_query):
        """
        Execute a raw SQL query from the SQL Console.
        Includes safety checks to prevent modification queries and memory exhaustion.
        
        Returns:
            dict: {
                'success': bool,
                'data': pd.DataFrame or None,
                'error': str or None,
                'rows_affected': int (0 for select)
            }
        """
        # 1. Security Check with sqlparse
        try:
            parsed = sqlparse.parse(sql_query)
        except Exception as e:
            return {
                'success': False,
                'data': None,
                'error': f"SQL Parse Error: {str(e)}"
            }

        if not parsed:
            return {
                'success': False,
                'data': None,
                'error': "Empty query"
            }

        for statement in parsed:
            if statement.get_type() != 'SELECT':
                return {
                    'success': False,
                    'data': None,
                    'error': f"Security Alert: Only SELECT statements are allowed. Found: {statement.get_type()}"
                }

        # 2. Execute with strictly Read-Only connection and Memory Protection
        conn = None
        try:
            db_uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(db_uri, uri=True)
            cursor = conn.cursor()

            cursor.execute(sql_query)

            # Protection: Fetch at most 2000 rows to prevent memory explosion (DoS)
            # The UI only shows 100 anyway, but we allow slightly more for export potential later if needed.
            # This replaces the dangerous pd.read_sql which fetches ALL rows.
            MAX_FETCH = 2000

            cols = [description[0] for description in cursor.description]
            rows = cursor.fetchmany(MAX_FETCH)

            df = pd.DataFrame(rows, columns=cols)

            return {
                'success': True,
                'data': df,
                'error': None if len(
                    rows) < MAX_FETCH else f"Warning: Result truncated to {MAX_FETCH} rows for performance."
            }
        except Exception as e:
            return {
                'success': False,
                'data': None,
                'error': str(e)
            }
        finally:
            if conn:
                conn.close()
