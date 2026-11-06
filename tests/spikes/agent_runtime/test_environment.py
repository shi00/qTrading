"""E00：环境/测试库/端点取证（Linux 沙箱实际证据，不改共享环境）。

Windows/frozen 发行/真实模型端点未在本机运行，按 Plans 登记 not_run。
"""

import os
import subprocess
import sys
from importlib.metadata import version

import pytest

from tests.integration import _db_config

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _run_python(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=merged,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_runtime_versions_are_the_declared_baseline():
    assert sys.version_info >= (3, 13)
    assert version("SQLAlchemy").split(".")[:2] in (["2", "1"], ["2", "2"])
    assert version("pydantic-ai-harness").startswith("0.65.")
    assert version("pydantic-ai-slim").startswith("2.65.")


def test_test_database_defaults_are_isolated_and_loopback_only():
    assert _db_config.TEST_DB_HOST in {"localhost", "127.0.0.1", "postgres"}
    assert _db_config.TEST_DB_NAME.startswith("test_")
    assert _db_config.TEST_DB_NAME.replace("_", "").isalnum()
    assert _db_config.TEST_DB_URL.startswith("postgresql+asyncpg://")
    assert "@localhost:" in _db_config.TEST_DB_URL or "@127.0.0.1:" in _db_config.TEST_DB_URL


def test_business_database_name_is_rejected_before_connection():
    result = _run_python("from tests.integration import _db_config", TEST_DB_NAME="astock_production")
    assert result.returncode != 0
    assert "TEST_DB_NAME must start with 'test_'" in result.stderr


def test_non_loopback_endpoint_is_rejected_before_connection():
    result = _run_python("from tests.integration import _db_config", TEST_DB_HOST="db.example.com")
    assert result.returncode != 0
    assert "TEST_DB_HOST must be one of" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 取证仅在 Windows 运行器执行")
def test_windows_runner_is_recorded_only_on_windows():
    assert sys.platform == "win32"
