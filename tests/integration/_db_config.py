"""测试 DB 配置（轻量模块，仅依赖 stdlib）。

从 tests/integration/conftest.py 提取，使 subprocess 测试无需导入重型依赖
（asyncpg/flet/sqlalchemy/CacheManager）即可读取 DB 配置变量。

根因修复：test_conftest_safety.py 的 subprocess.run 通过
``import tests.integration.conftest as m`` 读取 TEST_DB_* 变量，
但 conftest.py 模块级导入了重型依赖，CI Windows 4-worker 并行环境下
子进程导入耗时 60s+ 导致 subprocess.TimeoutExpired。
"""

import getpass
import hashlib
import os
import warnings
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

_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", f"test_astock_{_xdist_worker}" if _xdist_worker else "test_astock")
if _xdist_worker and _xdist_worker not in TEST_DB_NAME:
    TEST_DB_NAME = f"{TEST_DB_NAME}_{_xdist_worker}"
if not TEST_DB_NAME.startswith("test_"):
    raise ValueError(f"TEST_DB_NAME must start with 'test_' for safety, got: {TEST_DB_NAME!r}")
if not TEST_DB_NAME.replace("_", "").isalnum():
    raise ValueError("TEST_DB_NAME must contain only letters, digits, and underscores")
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
if TEST_DB_HOST not in _ALLOWED_HOSTS:
    raise ValueError(f"TEST_DB_HOST must be one of {_ALLOWED_HOSTS} for safety, got: {TEST_DB_HOST!r}")

# URL-encode password to handle special characters like '@', ':', '/' etc.
_ENCODED_PASSWORD = quote_plus(TEST_DB_PASSWORD) if TEST_DB_PASSWORD else ""
TEST_DB_URL = f"postgresql+asyncpg://{TEST_DB_USER}:{_ENCODED_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
