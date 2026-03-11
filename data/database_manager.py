import logging

import pandas as pd
import sqlparse
import sqlalchemy as sa

import config

logger = logging.getLogger(__name__)

# --- SQLAlchemy Core operator mapping ---
_OP_MAP = {
    "=": lambda c, v: c == v,
    ">": lambda c, v: c > v,
    "<": lambda c, v: c < v,
    ">=": lambda c, v: c >= v,
    "<=": lambda c, v: c <= v,
    "!=": lambda c, v: c != v,
    "LIKE": lambda c, v: c.like(v),
}


class DatabaseManager:
    """
    Manages database interactions for the Data Explorer feature.
    Provides read-only access (with safety checks) to the SQLite database.

    P0-3: All queries are built using SQLAlchemy Core to completely
    eliminate f-string SQL injection vectors.
    """

    def __init__(self):
        self._engine = sa.create_engine(
            config.DB_URL_SYNC,
            echo=False,
        )

    def close(self):
        """Disposes the SQLAlchemy engine connection pool, releasing file locks."""
        if hasattr(self, "_engine") and self._engine:
            self._engine.dispose()

    def get_all_tables(self):
        """
        Get a list of all tables in the database.
        Returns:
            list[str]: List of table names.
        """
        try:
            insp = sa.inspect(self._engine)
            return sorted(insp.get_table_names())
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
            insp = sa.inspect(self._engine)
            columns = []
            for col_info in insp.get_columns(table_name):
                columns.append(
                    {"name": col_info["name"], "type": str(col_info["type"])}
                )
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
            tbl = sa.table(table_name)
            stmt = sa.select(sa.func.count()).select_from(tbl)

            if filters:
                schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
                stmt = self._apply_filters(stmt, filters, schema_cols=schema_cols)

            with self._engine.connect() as conn:
                result = conn.execute(stmt)
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Error counting rows for {table_name}: {e}")
            return 0

    def _validate_table_name(self, table_name):
        """Ensure table name is valid and exists to prevent injection."""
        allowed_tables = self.get_all_tables()
        if table_name not in allowed_tables:
            raise ValueError(f"Invalid table name: {table_name}")

    @staticmethod
    def _apply_filters(stmt, filters, schema_cols=None):
        """Apply WHERE filters using SQLAlchemy Core operators (zero f-string).
        schema_cols: optional set of valid column names for whitelist validation."""
        for col_name, op, val in filters:
            # Whitelist validation: reject columns not in schema (mirrors sort_col check)
            if schema_cols and col_name not in schema_cols:
                logger.warning(
                    f"[DatabaseManager] Filter column '{col_name}' not in schema, skipped."
                )
                continue
            op_func = _OP_MAP.get(op)
            if op_func is None:
                continue  # Skip unsupported operator
            col_obj = sa.column(col_name)
            # Handle LIKE wildcards
            if op == "LIKE" and "%" not in str(val):
                val = f"%{val}%"
            stmt = stmt.where(op_func(col_obj, val))
        return stmt

    def query_table(
        self,
        table_name,
        page=1,
        page_size=50,
        filters=None,
        sort_col=None,
        sort_asc=True,
    ):
        """
        Query data from a table with pagination and filtering.
        All SQL is built via SQLAlchemy Core — zero f-string concatenation.

        Returns:
            pd.DataFrame: DataFrame containing the result rows.
        """
        try:
            self._validate_table_name(table_name)

            offset = (page - 1) * page_size
            tbl = sa.table(table_name)
            stmt = sa.select(sa.text("*")).select_from(tbl)

            # 1. Apply Filters
            schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
            if filters:
                stmt = self._apply_filters(stmt, filters, schema_cols=schema_cols)

            # 2. Apply Sorting
            if sort_col:
                # Validate sort_col exists in the table schema
                schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
                if sort_col in schema_cols:
                    col_obj = sa.column(sort_col)
                    stmt = stmt.order_by(
                        sa.asc(col_obj) if sort_asc else sa.desc(col_obj)
                    )
            elif table_name == "daily_quotes":
                # Default sort for common tables
                stmt = stmt.order_by(
                    sa.desc(sa.column("trade_date")), sa.asc(sa.column("ts_code"))
                )

            # 3. Apply Pagination
            stmt = stmt.limit(page_size).offset(offset)

            with self._engine.connect() as conn:
                result = conn.execute(stmt)
                rows = result.fetchall()
                if not rows:
                    return pd.DataFrame()
                cols = list(result.keys())
                return pd.DataFrame(rows, columns=cols)

        except Exception as e:
            logger.error(f"Error querying table {table_name}: {e}")
            return pd.DataFrame()

    def execute_sql(self, sql_query):
        """
        Execute a raw SQL query from the SQL Console.
        Includes safety checks to prevent modification queries and memory exhaustion.

        NOTE: This method intentionally accepts raw SQL (user's explicit intent).
        Defense layers: sqlparse SELECT-only check + read-only connection.

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
                "success": False,
                "data": None,
                "error": f"SQL Parse Error: {str(e)}",
            }

        if not parsed:
            return {"success": False, "data": None, "error": "Empty query"}

        for statement in parsed:
            if statement.get_type() != "SELECT":
                return {
                    "success": False,
                    "data": None,
                    "error": f"Security Alert: Only SELECT statements are allowed. Found: {statement.get_type()}",
                }

        # 2. Execute with strictly Read-Only connection and Memory Protection
        conn = None
        try:
            with self._engine.connect() as conn:
                result = conn.execute(sa.text(sql_query))

                # Protection: Fetch at most 2000 rows to prevent memory explosion (DoS)
                MAX_FETCH = 2000

                cols = list(result.keys())
                rows = result.fetchmany(MAX_FETCH)

                df = pd.DataFrame(rows, columns=cols)

                return {
                    "success": True,
                    "data": df,
                    "error": None
                    if len(rows) < MAX_FETCH
                    else f"Warning: Result truncated to {MAX_FETCH} rows for performance.",
                }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
