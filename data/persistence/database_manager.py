import logging
import re
import typing

import pandas as pd
import sqlalchemy as sa
import sqlparse

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

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
    Provides read-only access (with safety checks) to the PostgreSQL database.

    P0-3: All queries are built using SQLAlchemy Core to completely
    eliminate f-string SQL injection vectors.

    Lazy Initialization: Engine is created on first use, not in __init__.
    This allows the application to start without a configured database URL.
    """

    def __init__(self):
        self._engine = None
        self._initialized = False

    def _ensure_engine(self):
        """
        Lazy initialization of the database engine.
        Raises RuntimeError if database URL is not configured.
        """
        if self._engine is not None:
            return

        db_url_async = ConfigHandler.get_db_url()
        if not db_url_async:
            raise RuntimeError("Database URL is not configured. Please complete the onboarding wizard first.")

        db_url_sync = db_url_async.replace("+asyncpg", "")

        try:
            pool_size = int(ConfigHandler.get_db_connection_pool_size())
        except (TypeError, ValueError):
            pool_size = 10

        try:
            max_overflow = int(ConfigHandler.get_db_max_overflow())
        except (TypeError, ValueError):
            max_overflow = 5

        try:
            pool_timeout = int(ConfigHandler.get_db_pool_timeout())
        except (TypeError, ValueError):
            pool_timeout = 30

        try:
            pool_recycle = int(ConfigHandler.get_db_pool_recycle())
        except (TypeError, ValueError):
            pool_recycle = 1800

        try:
            pool_pre_ping = ConfigHandler.get_db_pool_pre_ping()
        except (TypeError, ValueError):
            pool_pre_ping = True

        self._engine = sa.create_engine(
            db_url_sync,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            pool_recycle=pool_recycle,
            pool_timeout=pool_timeout,
        )
        self._initialized = True

    def close(self):
        """Disposes the SQLAlchemy engine connection pool, releasing file locks."""
        if hasattr(self, "_engine") and self._engine:
            self._engine.dispose()
            self._engine = None
            self._initialized = False

    def get_all_tables(self):
        """
        Get a list of all tables in the database.
        Returns:
            list[str]: List of table names.
        """
        self._ensure_engine()
        try:
            insp = sa.inspect(self._engine)  # type: ignore[arg-type]
            return sorted(insp.get_table_names())
        except Exception as e:
            logger.error(f"Error fetching tables: {e}")
            return []

    def get_table_schema(self, table_name: str):
        """
        Get the schema (column names and types) for a specific table.
        Returns:
            list[dict]: List of column info {'name': 'col_name', 'type': 'TEXT'}
        """
        self._ensure_engine()
        try:
            self._validate_table_name(table_name)
            insp = sa.inspect(self._engine)  # type: ignore[arg-type]
            columns = []
            for col_info in insp.get_columns(table_name):
                columns.append(
                    {"name": col_info["name"], "type": str(col_info["type"])},
                )
            return columns
        except Exception as e:
            logger.error(f"Error fetching schema for {table_name}: {e}")
            return []

    def get_table_count(self, table_name: str, filters: list | None = None):
        """
        Get total row count for a table, optionally with filters.

        Args:
            table_name (str): Name of the table.
            filters (list): List of filter tuples (column, operator, value).
                            Example: [('ts_code', '=', '000001.SZ')]
        """
        self._ensure_engine()
        try:
            self._validate_table_name(table_name)
            tbl = sa.table(table_name)
            stmt = sa.select(sa.func.count()).select_from(tbl)

            if filters:
                schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
                stmt = self._apply_filters(stmt, filters, schema_cols=schema_cols)

            with self._engine.connect() as conn:  # type: ignore[union-attr]
                result = conn.execute(stmt)
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Error counting rows for {table_name}: {e}")
            return 0

    def _validate_table_name(self, table_name: str):
        """Ensure table name is valid and exists to prevent injection."""
        allowed_tables = self.get_all_tables()
        if table_name not in allowed_tables:
            raise ValueError(f"Invalid table name: {table_name}")

    @staticmethod
    def _apply_filters(stmt: typing.Any, filters: list | None, schema_cols: typing.Any = None):
        """Apply WHERE filters using SQLAlchemy Core operators (zero f-string).
        schema_cols: optional set of valid column names for whitelist validation."""
        if not filters:
            return stmt
        for col_name, op, val in filters:
            # Whitelist validation: reject columns not in schema (mirrors sort_col check)
            if schema_cols and col_name not in schema_cols:
                logger.warning(
                    f"[DatabaseManager] Filter column '{col_name}' not in schema, skipped.",
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
        table_name: str,
        page: typing.Any = 1,
        page_size: typing.Any = 50,
        filters: list | None = None,
        sort_col: typing.Any = None,
        sort_asc: typing.Any = True,
    ):
        """
        Query data from a table with pagination and filtering.
        All SQL is built via SQLAlchemy Core — zero f-string concatenation.

        Returns:
            pd.DataFrame: DataFrame containing the result rows.
        """
        self._ensure_engine()
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
                        sa.asc(col_obj) if sort_asc else sa.desc(col_obj),
                    )
            elif table_name == "daily_quotes":
                # Default sort for common tables
                stmt = stmt.order_by(
                    sa.desc(sa.column("trade_date")),
                    sa.asc(sa.column("ts_code")),
                )

            # 3. Apply Pagination
            stmt = stmt.limit(page_size).offset(offset)

            with self._engine.connect() as conn:  # type: ignore[union-attr]
                result = conn.execute(stmt)
                rows = result.fetchall()
                if not rows:
                    return pd.DataFrame()
                cols = list(result.keys())
                return pd.DataFrame(rows, columns=cols)

        except Exception as e:
            logger.error(f"Error querying table {table_name}: {e}")
            return pd.DataFrame()

    def execute_sql(self, sql_query: typing.Any):
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
        # 0. Ensure engine is initialized
        try:
            self._ensure_engine()
        except RuntimeError as e:
            return {"success": False, "data": None, "error": str(e)}

        # 1. Security Check with sqlparse
        try:
            parsed = sqlparse.parse(sql_query)
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"SQL Parse Error: {e!s}",
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

        # 1.5. Defense-in-depth: Block dangerous keywords even if sqlparse misclassifies
        # P0-2: Use regex word-boundary matching instead of trailing-space strings.
        # Trailing-space matching ("DROP ") can be bypassed with tabs, newlines,
        # or comments: DROP\ttable, DROP\n table, DROP(--comment)table.
        _DANGEROUS_KEYWORD_PATTERN = re.compile(
            r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXECUTE|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        match = _DANGEROUS_KEYWORD_PATTERN.search(sql_query)
        if match:
            return {
                "success": False,
                "data": None,
                "error": f"Security Alert: Dangerous keyword '{match.group(1).upper()}' detected in query.",
            }

        # 2. Execute with strictly Read-Only transaction and Memory Protection
        # P0-1: Use REPEATABLE READ + SET TRANSACTION READ ONLY instead of
        # AUTOCOMMIT.  AUTOCOMMIT is NOT read-only — it auto-commits every
        # statement, allowing writes to slip through if keyword/sqlparse checks
        # are bypassed (e.g. SELECT ... INTO, COPY ... TO PROGRAM).
        conn = None
        try:
            with self._engine.connect() as conn:  # type: ignore[union-attr]
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    conn.execute(sa.text("SET TRANSACTION READ ONLY"))
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
