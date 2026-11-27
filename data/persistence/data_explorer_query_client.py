"""数据浏览器（表浏览 + SQL 控制台）只读查询客户端。

SQL 控制台的越界直读防护分两层，**两层必须同时在位**：

1. **数据库角色最小权限（主防线，真正的安全边界）**：``execute_sql`` 在事务内
   ``SET LOCAL ROLE qtrading_explorer_ro``。该角色只对公开业务表有 ``SELECT``，
   ``agent_*``（迁移 0048 的原始体/模型可见/公开导出三表示）与 ``app_state`` 无任何
   权限；``query_to_xml``／``table_to_xml`` 等执行器为 SECURITY INVOKER，受限角色下
   同样读不到受管表。角色创建属部署职责（应用不在迁移/启动时建角色），DBA 一次性执行::

       CREATE ROLE qtrading_explorer_ro NOLOGIN;
       GRANT CONNECT ON DATABASE <database> TO qtrading_explorer_ro;
       GRANT USAGE ON SCHEMA public TO qtrading_explorer_ro;
       REVOKE ALL ON ALL TABLES IN SCHEMA public FROM qtrading_explorer_ro;
       DO $$
       DECLARE tbl text;
       BEGIN
         FOR tbl IN
           SELECT tablename FROM pg_tables
           WHERE schemaname = 'public'
             AND tablename NOT LIKE 'agent\\_%'
             AND tablename <> 'app_state'
         LOOP
           EXECUTE format('GRANT SELECT ON TABLE public.%I TO qtrading_explorer_ro', tbl);
         END LOOP;
       END $$;
       GRANT qtrading_explorer_ro TO <runtime_role>;  -- SET LOCAL ROLE 需成员资格

   每次迁移新增表后重跑「收回 + 逐表授予」块（默认权限会覆盖新表，故需重跑）。

2. **应用层前置校验（快失败提示，非安全边界）**：``validate_read_only_sql`` 拒绝
   多语句、非 SELECT、危险关键字、受管标识符（``agent_*``／``pg_catalog``／
   ``information_schema``）与任意执行/文件/大对象/改角色函数（``query_to_*``、
   ``table_to_*``、``lo_*``、``pg_*``、``dblink*``、``set_config``）。
   纯 sqlparse 词法检查**不完备**——``query_to_xml('SELECT ... FROM agent_message', ...)``
   把 SQL 藏在字符串常量里，词法上只是普通字面量；因此必须依赖第 1 层兜底。

角色不存在时第 1 层自动跳过（不破坏既有初始化），此时只剩应用层校验——这正是
应用层不能省、但也不能单独承担安全边界的原因。若角色已创建但运行时角色不是其成员，
``SET LOCAL ROLE`` 会失败并以脱敏错误返回（运行角色须按上方 GRANT 授权）。
"""

import logging
import re
import threading
import typing

import pandas as pd
import sqlalchemy as sa
import sqlparse

from data.sync.base import safe_error
from utils.config_handler import ConfigHandler
from utils.db_utils import get_db_pool_config, to_sync_db_url
from utils.df_normalize import normalize_decimal_columns

logger = logging.getLogger(__name__)

# C6（review03）：会话级语句超时，防止用户手滑的重查询挂住连接（SELECT pg_sleep 等）。
# 不扩展关键字黑名单——statement_timeout 不关心 SQL 内容，只关心执行时长（报告 03 §2.2 C6）。
_STATEMENT_TIMEOUT = "10s"

# N03：Agent 受管表（``agent_`` 前缀）不进入通用数据页目录。这些表存放原值/模型
# 可见/公开导出三表示（迁移 0048），公开表示只经 Agent 读取 API 提供；通用数据页
# 按目录反射返回原始全列会绕过该投影（如 ``agent_message.body_text`` 原值、
# ``model_text`` 模型可见），故浏览与导出共用的目录校验统一排除（fail closed）。
_AGENT_MANAGED_TABLE_PREFIX = "agent_"

# 数据浏览器专用只读角色的会话名（D2-01）。角色由部署流程创建（见模块 docstring），
# 存在时 execute_sql 以 SET LOCAL ROLE 切到该角色；不存在时跳过，不破坏既有初始化。
_EXPLORER_ROLE = "qtrading_explorer_ro"

# 应用层前置校验（快失败提示，非安全边界；安全边界见模块 docstring 第 1 层）。
_BLOCKED_IDENTIFIER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bagent_\w*", re.IGNORECASE),  # Agent 受管表（迁移 0048 三表示）
    re.compile(r"\bpg_catalog\b", re.IGNORECASE),  # 系统目录
    re.compile(r"\binformation_schema\b", re.IGNORECASE),  # 标准元数据视图
)
# 可执行任意 SQL / 读写服务器文件 / 切换会话角色的函数前缀（含 schema 限定形态）。
_BLOCKED_FUNCTION_PREFIXES = (
    "query_to_",  # query_to_xml：在字符串常量内执行任意 SQL
    "table_to_",  # table_to_xml：按表名读取整表
    "cursor_to_",
    "schema_to_",
    "database_to_",
    "lo_",  # 大对象 lo_import / lo_export
    "pg_",  # pg_read_file / pg_sleep / pg_terminate_backend ...
    "dblink",  # dblink / dblink_exec
)
# set_config 可改写 role 等 GUC，绕过第 1 层角色隔离。
_BLOCKED_FUNCTION_NAMES = frozenset({"set_config"})
_FUNCTION_CALL_PATTERN = re.compile(r"\b([a-z_][a-z0-9_$]*)\s*\(", re.IGNORECASE)
# U&"..." 可用 \005f 之类的转义拼出 agent_message，词法上与受管标识符不同名，故整体禁用。
_UNICODE_ESCAPE_IDENTIFIER_PATTERN = re.compile(r"\bu&\s*\"", re.IGNORECASE)
# 危险关键字：词边界匹配，防制表符/换行/注释分隔绕过（P0-2）。
_DANGEROUS_KEYWORD_PATTERN = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXECUTE|GRANT|REVOKE|INTO|COPY)\b",
    re.IGNORECASE,
)


def _statement_code_without_literals(statement: typing.Any) -> str:
    """语句 → 去掉注释与字面量常量后的代码文本（保留双引号标识符原样）。

    字面量（单引号、``$$...$$`` dollar-quoted、数值）内的文本不参与标识符/函数匹配
    （它只是被服务器当参数传递的文本）；双引号是标识符引用（``Literal.String.Symbol``），
    内容需参与匹配。
    """
    parts: list[str] = []
    for token in statement.flatten():
        ttype = token.ttype
        if ttype is not None and (ttype in sqlparse.tokens.Comment or ttype in sqlparse.tokens.Literal):
            # 双引号标识符（Literal.String.Symbol）保留内容参与匹配，其余字面量/注释整体抹去
            parts.append(token.value if ttype in sqlparse.tokens.Literal.String.Symbol else " ")
            continue
        parts.append(token.value)
    return "".join(parts)


def _has_effective_content(statement: typing.Any) -> bool:
    """语句是否含有效内容（忽略空白、注释与多余分号，如 ``SELECT 1;;`` 的尾项）。"""
    for token in statement.flatten():
        ttype = token.ttype
        if ttype is not None and (ttype in sqlparse.tokens.Comment or ttype in sqlparse.tokens.Whitespace):
            continue
        if token.value.strip(" ;\t\r\n") == "":
            continue
        return True
    return False


def validate_read_only_sql(sql_query: str) -> str | None:
    """SQL 控制台应用层前置校验：通过返回 None，否则返回错误消息。

    规则：单条语句 + 仅 SELECT + 无危险关键字 + 无受管表/系统目录标识符 +
    无任意执行/文件/大对象/改角色函数。仅为快失败提示，安全边界见模块 docstring
    第 1 层（会话级只读低权限角色）。
    """
    if not isinstance(sql_query, str) or not sql_query.strip():
        return "Empty query"
    try:
        parsed = sqlparse.parse(sql_query)
    except Exception as e:
        # F5-P2: R9 脱敏
        return f"SQL Parse Error: {safe_error(e)}"

    statements = [statement for statement in parsed if _has_effective_content(statement)]
    if not statements:
        return "Empty query"
    if len(statements) > 1:
        return "Security Alert: Only a single SQL statement is allowed."

    statement = statements[0]
    if statement.get_type() != "SELECT":
        return f"Security Alert: Only SELECT statements are allowed. Found: {statement.get_type()}"

    keyword = _DANGEROUS_KEYWORD_PATTERN.search(sql_query)
    if keyword:
        return f"Security Alert: Dangerous keyword '{keyword.group(1).upper()}' detected in query."

    code_with_quotes = _statement_code_without_literals(statement)
    if _UNICODE_ESCAPE_IDENTIFIER_PATTERN.search(code_with_quotes):
        return "Security Alert: Unicode-escaped identifiers are not allowed."
    code = code_with_quotes.replace('"', "")
    for pattern in _BLOCKED_IDENTIFIER_PATTERNS:
        identifier = pattern.search(code)
        if identifier:
            return f"Security Alert: Access to managed identifier '{identifier.group(0)}' is not allowed."

    for function_name in _FUNCTION_CALL_PATTERN.findall(code):
        lowered = function_name.lower()
        if lowered in _BLOCKED_FUNCTION_NAMES or lowered.startswith(_BLOCKED_FUNCTION_PREFIXES):
            return f"Security Alert: Function '{lowered}' is not allowed in the SQL console."
    return None


_OP_MAP = {
    "=": lambda c, v: c == v,
    ">": lambda c, v: c > v,
    "<": lambda c, v: c < v,
    ">=": lambda c, v: c >= v,
    "<=": lambda c, v: c <= v,
    "!=": lambda c, v: c != v,
    "LIKE": lambda c, v: c.like(v),
}


class DataExplorerQueryClient:
    """
    Manages database interactions for the Data Explorer feature.
    Provides read-only access (with safety checks) to the PostgreSQL database.

    P0-3: All queries are built using SQLAlchemy Core to completely
    eliminate f-string SQL injection vectors.

    Lazy Initialization: Engine is created on first use, not in __init__.
    This allows the application to start without a configured database URL.

    Resource Lifecycle: A shared class-level engine is used across all
    instances. ShutdownCoordinator can close it via close_all() during
    graceful shutdown (View will_unmount is not triggered on app exit).

    单例管理说明（C-1 决策：维持现状，不注册 @register_singleton）：
    本类采用"共享引擎非单例"模式——__init__ 为空，每次实例化是新对象，
    但所有实例共用类级 _shared_engine。未注册到 singleton_registry 的原因：
    1. R15 触发条件要求 __new__ + _instance，本类不满足，非违规；
    2. 强加 @register_singleton 需引入 __new__ 单例样板，违反 YAGNI（§1.2）；
    3. 测试隔离由 tests/unit/conftest.py 的 _reset_data_explorer_shared_engine
       autouse fixture 覆盖（R7 已满足）；
    4. 同步引擎（sa.create_engine）与 CacheManager 的异步引擎完全隔离，
       各自独立 dispose，无跨引擎风险。
    属于"非注册单例"，与 ConfigHandler/ProxyManager 同类，详见
    docs/architecture/singleton-lifecycle.md。
    """

    # 类级别共享引擎：所有实例共用，受 _engine_lock 保护（双重检查锁定）
    _shared_engine: sa.Engine | None = None
    _engine_lock = threading.Lock()

    @classmethod
    def close_all(cls) -> None:
        """关闭共享的同步引擎，供 ShutdownCoordinator 调用。

        幂等：多次调用安全。close_all() 后 _shared_engine 置 None，
        下次 _ensure_engine() 会尝试重新创建（若 DB URL 可用）。
        """
        with cls._engine_lock:
            if cls._shared_engine is not None:
                try:
                    cls._shared_engine.dispose()
                except Exception as e:
                    logger.warning("[DataExplorerQueryClient] close_all() failed: %s", safe_error(e))
                cls._shared_engine = None

    def _ensure_engine(self):
        """
        Lazy initialization of the shared database engine (double-checked locking).
        Raises RuntimeError if database URL is not configured.

        close_all() 后 _shared_engine 为 None，本方法会尝试重新创建引擎
        （支持测试场景和正常重启）。若 DB URL 不可用则 raise RuntimeError，
        该异常在查询方法的 try 块外抛出，不会被 except Exception 吞没。
        """
        if DataExplorerQueryClient._shared_engine is not None:
            return

        with DataExplorerQueryClient._engine_lock:
            if DataExplorerQueryClient._shared_engine is None:
                db_url_async = ConfigHandler.get_db_url()
                if not db_url_async:
                    raise RuntimeError("Database URL is not configured. Please complete the onboarding wizard first.")

                db_url_sync = to_sync_db_url(db_url_async)
                pool_config = get_db_pool_config()

                DataExplorerQueryClient._shared_engine = sa.create_engine(
                    db_url_sync,
                    echo=False,
                    **pool_config,
                )

    @property
    def _engine(self) -> sa.Engine | None:
        """只读属性，返回类级别共享引擎。"""
        return DataExplorerQueryClient._shared_engine

    def close(self):
        """实例级 close 为空操作。

        共享引擎的生命周期由 close_all() 统一管理。
        此方法仅保留接口兼容性，供 DataExplorerViewModel.dispose() 调用。
        """
        pass

    def get_all_tables(self):
        """
        Get a list of all tables in the database.

        Agent 受管表（``agent_`` 前缀）不纳入通用数据页目录：其公开表示由 Agent
        读取 API 提供（三表示见迁移 0048），原始全列读取会把未公开表示（原值/
        模型可见）带进浏览与导出。浏览（schema/rows）与导出共用本目录校验。
        Returns:
            list[str]: List of table names.
        """
        self._ensure_engine()
        try:
            assert self._engine is not None
            insp = sa.inspect(self._engine)
            return sorted(name for name in insp.get_table_names() if not name.startswith(_AGENT_MANAGED_TABLE_PREFIX))
        except Exception as e:
            logger.error("Error fetching tables: %s", safe_error(e))
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
            assert self._engine is not None
            insp = sa.inspect(self._engine)
            columns = []
            for col_info in insp.get_columns(table_name):
                columns.append(
                    {"name": col_info["name"], "type": str(col_info["type"])},
                )
            return columns
        except Exception as e:
            logger.error("Error fetching schema for %s: %s", table_name, safe_error(e))
            return []

    def get_table_count(self, table_name: str, filters: list | None = None):
        """
        Get total row count for a table, optionally with filters.

        Args:
            table_name (str): Name of the table.
            filters (list): List of filter tuples (column, operator, value).
                            Example: [('ts_code', '=', '000001.SZ')]

        Returns:
            int: 合法空表/无匹配过滤返回 0。

        Raises:
            ValueError: 表名不在库表白名单内。
            Exception: 计数查询执行失败（断连 / 无 SELECT 权限 / 列或参数不匹配等）原样向上
                传播，由调用方经 ``classify_error(context="db")`` 分类。**不得以 0 冒充
                「空表」**：0 行与执行失败对用户是两种完全不同的结论（UIR-F13-002）。
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
            logger.error("Error counting rows for %s: %s", table_name, safe_error(e))
            raise

    def get_latest_trade_date(self) -> str | None:
        """Get the latest trade_date from daily_quotes (Phase 6.4, FR-UX-006).

        Uses SQLAlchemy Core with hardcoded table/column names (no user input,
        R4 compliant). Returns trade_date as string (YYYYMMDD format) or None
        if table is empty / doesn't exist / query fails.
        """
        self._ensure_engine()
        try:
            # Hardcoded constants — no injection vector (R4 safe)
            allowed_tables = self.get_all_tables()
            if "daily_quotes" not in allowed_tables:
                return None
            tbl = sa.table("daily_quotes")
            stmt = sa.select(sa.func.max(sa.column("trade_date"))).select_from(tbl)
            with self._engine.connect() as conn:  # type: ignore[union-attr]
                result = conn.execute(stmt)
                val = result.scalar()
                if val is None:
                    return None
                return str(val)
        except Exception as e:
            logger.error("Error fetching latest trade_date: %s", safe_error(e))
            return None

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
                    "[DataExplorerQueryClient] Filter column '%s' not in schema, skipped.",
                    col_name,
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
            pd.DataFrame: DataFrame containing the result rows (合法空表/无匹配过滤为空表)。

        Raises:
            ValueError: 表名不在库表白名单内。
            Exception: 读行查询执行失败（断连 / 无 SELECT 权限 / 列或参数不匹配等）原样向上
                传播，由调用方经 ``classify_error(context="db")`` 分类。**不得以空 DataFrame
                冒充「无数据」**：空结果与执行失败对用户是两种完全不同的结论（UIR-F13-002）。
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
                # DAT-10/11: Numeric 列 Decimal → float64 归一化（量小，同步执行）
                return normalize_decimal_columns(pd.DataFrame(rows, columns=cols))

        except Exception as e:
            logger.error("Error querying table %s: %s", table_name, safe_error(e))
            raise

    def execute_sql(self, sql_query: typing.Any):
        """
        Execute a raw SQL query from the SQL Console.
        Includes safety checks to prevent modification queries and memory exhaustion.

        NOTE: This method intentionally accepts raw SQL (user's explicit intent).
        Defense layers (see module docstring):
        1. session-scoped least-privilege role ``qtrading_explorer_ro`` (security boundary);
        2. app-layer validation (single statement, SELECT-only, managed identifiers/functions);
        3. SET TRANSACTION READ ONLY + statement_timeout.

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
            # F5-P2: R9 脱敏，避免异常消息含 DB 连接字符串等敏感信息流向 UI
            return {"success": False, "data": None, "error": safe_error(e)}

        # 1. Application-layer pre-check (fast fail; R9 脱敏由 validator 内部处理)
        validation_error = validate_read_only_sql(sql_query)
        if validation_error is not None:
            return {"success": False, "data": None, "error": validation_error}

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
                    # C6: SET LOCAL 在事务内生效，超时后语句被服务端取消（resource 滥用防线）
                    # DAT-08: SET 不支持参数绑定，改用 set_config(..., true) 等价 SET LOCAL（仅当前事务生效；依赖下方显式事务块）
                    conn.execute(sa.text("SELECT set_config('statement_timeout', :v, true)"), {"v": _STATEMENT_TIMEOUT})
                    # 第 1 层（安全边界）：切到只读低权限角色，隔离 agent_*/app_state 等受管表。
                    # 角色由部署流程创建；不存在时跳过以不破坏既有初始化（见模块 docstring）。
                    role_exists = conn.execute(
                        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                        {"role": _EXPLORER_ROLE},
                    ).fetchone()
                    if role_exists is not None:
                        # R4：角色名为冻结常量，不可内插，故用静态字面量（须与 _EXPLORER_ROLE 一致）
                        conn.execute(sa.text("SET LOCAL ROLE qtrading_explorer_ro"))
                    result = conn.execute(sa.text(sql_query))

                    # Protection: Fetch at most 2000 rows to prevent memory explosion (DoS)
                    MAX_FETCH = 2000

                    cols = list(result.keys())
                    rows = result.fetchmany(MAX_FETCH)

                    # DAT-10/11: Numeric 列 Decimal → float64 归一化（量 ≤ 2000，同步执行）
                    df = normalize_decimal_columns(pd.DataFrame(rows, columns=cols))

                    return {
                        "success": True,
                        "data": df,
                        "error": None
                        if len(rows) < MAX_FETCH
                        else f"Warning: Result truncated to {MAX_FETCH} rows for performance.",
                    }
        except Exception as e:
            # F5-P2: R9 脱敏，避免异常消息含 DB 连接字符串等敏感信息流向 UI
            return {"success": False, "data": None, "error": safe_error(e)}
