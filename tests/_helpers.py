"""
Test Utilities

Shared utility functions for test modules.
"""

import inspect


def get_model_columns(model_class: type) -> set:
    """Extract column names from SQLAlchemy model class.

    Uses __table__.columns directly instead of inspect.getmembers,
    which is more robust and doesn't depend on Python internals.
    """
    return {c.name for c in model_class.__table__.columns}


def get_model_db_columns(model_class: type) -> set:
    """Extract database column names from SQLAlchemy model class.

    Unlike get_model_columns which returns Python attribute names,
    this returns the actual database column names (which may differ
    when using Column(name=...) parameter).
    """
    return {c.name for c in model_class.__table__.columns}


def extract_cols_from_method(method) -> set | None:
    """Extract cols list from DAO save method by resolving the model class.

    Since all DAO save methods use the pattern:
        cols = get_model_columns(ModelClass)
    we resolve the ModelClass by inspecting the method's closure and globals,
    then call get_model_columns directly. This avoids fragile source-string
    parsing via inspect.getsource + AST.
    """
    try:
        from data.persistence.models import get_model_columns as gmc

        model_class = _resolve_model_class_from_method(method)
        if model_class is not None:
            exclude = _resolve_exclude_from_method(method)
            return set(gmc(model_class, exclude=exclude))

        return _resolve_hardcoded_cols_from_method(method)
    except (ImportError, AttributeError, OSError, TypeError):
        return None


def _resolve_model_class_from_method(method) -> type | None:
    """Resolve the SQLAlchemy model class referenced in a DAO save method.

    Inspects the method's closure variables and global scope for calls
    to get_model_columns(SomeModel), resolving SomeModel without AST parsing.
    """
    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return None

    import re

    patterns = [
        r"get_model_columns\(\s*(\w+)\s*\)",
        r"get_model_columns\(\s*(\w+)\s*,",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            model_name = match.group(1)
            module = inspect.getmodule(method)
            if module and hasattr(module, model_name):
                return getattr(module, model_name)
            method_globals = getattr(method, "__globals__", {})
            if model_name in method_globals:
                return method_globals[model_name]
    return None


def _resolve_exclude_from_method(method) -> set | None:
    """Resolve the exclude= parameter from a DAO save method if present."""
    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return None

    import re

    match = re.search(r"exclude\s*=\s*\{([^}]+)\}", source)
    if match:
        items = match.group(1)
        return {s.strip().strip("\"'") for s in items.split(",") if s.strip()}
    return None


def _resolve_hardcoded_cols_from_method(method) -> set | None:
    """Fallback: try to resolve hardcoded column lists from method source."""
    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return None

    import re

    match = re.search(r"(?:cols|columns|all_cols)\s*=\s*\[([^\]]+)\]", source)
    if match:
        items = match.group(1)
        result = set()
        for item in items.split(","):
            item = item.strip().strip("\"'")
            if item and not item.startswith("#"):
                result.add(item)
        return result if result else None
    return None


def extract_fields_from_api_method(method) -> set:
    """Extract fields list from TushareClient API method.

    Resolves the fields="..." keyword argument by inspecting the method's
    default values and source, without relying on AST source parsing.
    """
    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return set()

    import re

    match = re.search(r'fields\s*=\s*["\']([^"\']+)["\']', source)
    if match:
        fields_str = match.group(1)
        return set(f.strip() for f in fields_str.split(",") if f.strip())

    return set()


# --- Integration test helpers for database migration tests ---

import os
from pathlib import Path
from urllib.parse import quote_plus

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_test_engine(url: str, *, echo: bool = False, **kwargs) -> AsyncEngine:
    """创建测试用 async engine，强制 PG 会话时区为 UTC。

    统一所有测试 engine 的时区设置，与生产环境前提对齐（stock_dao.py 注释：
    PG 时区必须为 UTC）。避免本地 Windows PG 默认 Asia/Shanghai 导致
    to_utc_for_db() 写入的 UTC tz-naive 与 SQL now() 比较时偏移 8 小时，
    进而让 cooldown / next_retry_at 等时间比较语义错误。

    所有自建 engine 的测试应使用本 helper，而非直接调用 create_async_engine。
    例外：仅用于 dialect.compile() 不真实连接的 fake engine（如 test_review_round_trip.py）。

    Args:
        url: 数据库连接 URL（postgresql+asyncpg://...）。
        echo: 是否开启 SQL echo。
        **kwargs: 透传给 create_async_engine，connect_args 会被合并（调用方优先）。

    Returns:
        AsyncEngine，会话时区已强制为 UTC。
    """
    connect_args = dict(kwargs.pop("connect_args", {}) or {})
    server_settings = dict(connect_args.get("server_settings", {}) or {})
    server_settings.setdefault("timezone", "UTC")
    connect_args["server_settings"] = server_settings
    return create_async_engine(url, echo=echo, connect_args=connect_args, **kwargs)


def get_pg_connection_params() -> dict:
    """Get PostgreSQL connection parameters from environment or defaults.

    Used by integration tests that need to create/drop isolated databases.
    """
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = int(os.environ.get("TEST_DB_PORT", "5432"))
    user = os.environ.get("TEST_DB_USER", "postgres")
    password = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD", "")
    return {"host": host, "port": port, "user": user, "password": password}


def make_alembic_cfg(db_url: str) -> Config:
    """Create Alembic config with correct project paths.

    Used by integration tests that need to run Alembic commands directly.

    Note: Uses attributes['database_url'] to pass URL, bypassing ConfigParser
    interpolation which would fail on URLs containing percent-encoded characters.
    """
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    # Use attributes to pass URL directly, avoiding ConfigParser interpolation issues
    # with special characters like '%40' (URL-encoded '@')
    cfg.attributes["database_url"] = db_url
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


def build_db_urls(params: dict, db_name: str) -> tuple[str, str]:
    """Build sync and async database URLs from connection params and db_name.

    Returns:
        (sync_url, async_url) tuple
    """
    # URL-encode password to handle special characters like '@', ':', '/' etc.
    encoded_pwd = quote_plus(params["password"]) if params.get("password") else ""
    sync_url = f"postgresql://{params['user']}:{encoded_pwd}@{params['host']}:{params['port']}/{db_name}"
    async_url = f"postgresql+asyncpg://{params['user']}:{encoded_pwd}@{params['host']}:{params['port']}/{db_name}"
    return sync_url, async_url
