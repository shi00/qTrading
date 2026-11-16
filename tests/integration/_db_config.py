"""测试 DB 配置（轻量模块，仅依赖 stdlib）。

从 tests/integration/conftest.py 提取，使 subprocess 测试无需导入重型依赖
（asyncpg/flet/sqlalchemy/CacheManager）即可读取 DB 配置变量。

根因修复：test_conftest_safety.py 的 subprocess.run 通过
``import tests.integration.conftest as m`` 读取 TEST_DB_* 变量，
但 conftest.py 模块级导入了重型依赖，CI Windows 4-worker 并行环境下
子进程导入耗时 60s+ 导致 subprocess.TimeoutExpired。

DB 命名与所有权（D21-OTHER-002）：

- 默认名 ``test_astock_<run-id>[_<worker>]``。run-id 由 controller（无
  ``PYTEST_XDIST_WORKER`` 的进程）生成并写入 ``TEST_DB_RUN_ID``，xdist worker
  子进程启动时继承；两个并发 run 即使 worker 名相同也得到不同目标库。
- 默认名属于本次 run 独占：允许首次 clean slate（DROP+CREATE）与 session
  结束清理。
- 显式 ``TEST_DB_NAME`` 视为外部共享目标：默认只 CREATE IF NOT EXISTS，绝不
  DROP（不能仅凭 ``test_`` 前缀推断所有权）；仅当 ``TEST_DB_ALLOW_REBUILD=1``
  显式声明独占并允许重建时才允许 DROP。
"""

import getpass
import hashlib
import os
import re
import secrets
import warnings
from typing import Any
from urllib.parse import quote_plus

TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
TEST_DB_USER = os.environ.get("TEST_DB_USER", "postgres")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD")
if not TEST_DB_PASSWORD:
    _run_id = os.environ.get("GITHUB_RUN_ID", "")
    if _run_id:
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_ci_{_run_id}".encode()).hexdigest()[:24]
    else:
        try:
            _local_user = getpass.getuser()
        except (OSError, KeyError):
            _local_user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_local_{_local_user}".encode()).hexdigest()[:24]
    warnings.warn(
        "Using derived test DB password. Set TEST_DB_PASSWORD or CI_PG_PASSWORD env var for production CI.",
        UserWarning,
        stacklevel=2,
    )

_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "").strip()

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9]{4,32}$")
_DB_NAME_MAX_LEN = 63
_REBUILD_TRUE = frozenset({"1", "true", "yes"})
_REBUILD_FALSE = frozenset({"0", "false", "no"})
_ALLOWED_REBUILD_VALUES = _REBUILD_TRUE | _REBUILD_FALSE


def validate_test_db_name(name: str) -> str:
    """校验库名安全边界（前缀 / 字符集 / 长度），返回原值。"""
    if len(name) > _DB_NAME_MAX_LEN:
        raise ValueError(f"TEST_DB_NAME must be at most {_DB_NAME_MAX_LEN} characters, got: {name!r}")
    if not name.startswith("test_"):
        raise ValueError(f"TEST_DB_NAME must start with 'test_' for safety, got: {name!r}")
    if not name.replace("_", "").isalnum():
        raise ValueError("TEST_DB_NAME must contain only letters, digits, and underscores")
    return name


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"TEST_DB_RUN_ID must match {_RUN_ID_PATTERN.pattern}, got: {run_id!r}")
    return run_id


def _generate_run_id() -> str:
    """生成每次 controller 会话唯一的 run-id（不依赖 CI job 边界，避免分片同名）。"""
    return secrets.token_hex(6)


def _resolve_run_id() -> str:
    """解析本次 run 身份；controller 生成后导出，xdist worker 子进程继承。"""
    explicit = os.environ.get("TEST_DB_RUN_ID", "").strip()
    if explicit:
        return _validate_run_id(explicit)
    run_id = _generate_run_id()
    if not _xdist_worker and not os.environ.get("TEST_DB_RUN_ID", "").strip():
        # controller（或非 xdist 单进程）先落环境变量；xdist 在 sessionstart
        # 派生 worker 子进程时继承该值，保证同一 run 内 run-id 一致。
        os.environ["TEST_DB_RUN_ID"] = run_id
    return run_id


TEST_DB_RUN_ID = _resolve_run_id()

_explicit_db_name = os.environ.get("TEST_DB_NAME", "").strip()
TEST_DB_NAME_EXPLICIT = bool(_explicit_db_name)

_rebuild_raw = os.environ.get("TEST_DB_ALLOW_REBUILD", "").strip().lower()
if _rebuild_raw and _rebuild_raw not in _ALLOWED_REBUILD_VALUES:
    raise ValueError(
        f"TEST_DB_ALLOW_REBUILD must be one of {sorted(_ALLOWED_REBUILD_VALUES)} or empty, got: {_rebuild_raw!r}"
    )
TEST_DB_ALLOW_REBUILD = _rebuild_raw in _REBUILD_TRUE

if TEST_DB_NAME_EXPLICIT:
    TEST_DB_NAME = _explicit_db_name
    if _xdist_worker and _xdist_worker not in TEST_DB_NAME:
        TEST_DB_NAME = f"{TEST_DB_NAME}_{_xdist_worker}"
else:
    TEST_DB_NAME = f"test_astock_{TEST_DB_RUN_ID}"
    if _xdist_worker:
        TEST_DB_NAME = f"{TEST_DB_NAME}_{_xdist_worker}"
TEST_DB_NAME = validate_test_db_name(TEST_DB_NAME)

# 只有本次 run 独占的目标才允许 DROP：默认名天然独占；显式名必须经
# TEST_DB_ALLOW_REBUILD=1 明确声明独占，否则视为外部共享目标，绝不删除。
TEST_DB_RUN_OWNED = not TEST_DB_NAME_EXPLICIT or TEST_DB_ALLOW_REBUILD

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
if TEST_DB_HOST not in _ALLOWED_HOSTS:
    raise ValueError(f"TEST_DB_HOST must be one of {_ALLOWED_HOSTS} for safety, got: {TEST_DB_HOST!r}")

# URL-encode password to handle special characters like '@', ':', '/' etc.
_ENCODED_PASSWORD = quote_plus(TEST_DB_PASSWORD) if TEST_DB_PASSWORD else ""
TEST_DB_URL = f"postgresql+asyncpg://{TEST_DB_USER}:{_ENCODED_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"

# 常量 DDL 模板：目标库名经服务端 format(%I) 引用，源码不做 SQL 拼接（R4）。
DDL_CREATE_DB = "CREATE DATABASE %I"
DDL_DROP_DB = "DROP DATABASE IF EXISTS %I WITH (FORCE)"


async def execute_quoted_ddl(conn: Any, template: str, db_name: str) -> None:
    """以 ``format(%I, ...)`` 引用库名执行 DDL（连接由调用方注入，便于无 DB 单测）。"""
    validate_test_db_name(db_name)
    ddl = await conn.fetchval("SELECT format($1::text, $2::text)", template, db_name)
    await conn.execute(ddl)


async def _database_exists(conn: Any, db_name: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name))


async def ensure_test_db_state(
    conn: Any,
    *,
    clean_slate: bool,
    db_name: str = TEST_DB_NAME,
    owned: bool = TEST_DB_RUN_OWNED,
) -> None:
    """按所有权执行建库决策。

    - owned + clean_slate：DROP FORCE + CREATE（本次 run 独占的 clean slate）
    - owned + 非首次：库缺失时 CREATE（自愈）
    - 非 owned：只 CREATE IF NOT EXISTS，绝不 DROP（外部共享目标；并发使用
      显式目标需调用方自行串行化，本函数不删除对方库）
    """
    validate_test_db_name(db_name)
    if clean_slate and owned:
        await execute_quoted_ddl(conn, DDL_DROP_DB, db_name)
        await execute_quoted_ddl(conn, DDL_CREATE_DB, db_name)
        return
    if not await _database_exists(conn, db_name):
        await execute_quoted_ddl(conn, DDL_CREATE_DB, db_name)


async def drop_test_db_if_owned(
    conn: Any,
    *,
    db_name: str = TEST_DB_NAME,
    owned: bool = TEST_DB_RUN_OWNED,
) -> bool:
    """仅删除本次 run 独占的目标；返回是否实际执行 DROP。"""
    if not owned:
        return False
    await execute_quoted_ddl(conn, DDL_DROP_DB, db_name)
    return True
