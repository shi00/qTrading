"""TP03-F01 回归：schema consistency 隔离库清理逻辑。

覆盖两点：
1. CREATE 失败时不执行 DROP（避免误删非本测试拥有的库）。
2. 清理阶段异常（engine.dispose / DROP）不覆盖 setup/test 的原始异常。
"""

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, cast

import pytest

import data.persistence.db_url_override as db_url_override_module
from tests.integration import test_schema_consistency as schema_consistency

pytestmark = pytest.mark.unit

_PG_PARAMS = {"host": "localhost", "port": 5432, "user": "postgres", "password": ""}


class _FakeConnection:
    def __init__(self, executed: list[str], *, fail_on: str | None = None) -> None:
        self.executed = executed
        self.fail_on = fail_on

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self.fail_on is not None and sql.startswith(self.fail_on):
            raise RuntimeError(f"injected failure: {self.fail_on}")

    async def close(self) -> None:
        return None


class _FakeEngine:
    def __init__(self, *, dispose_error: Exception | None = None) -> None:
        self.dispose_error = dispose_error

    def dispose(self) -> None:
        if self.dispose_error is not None:
            raise self.dispose_error


def _fixture_function(fixture: object) -> Callable[..., Any]:
    """取回 pytest fixture 的原始生成器函数（pytest 内部包装，避免 pyright 直接访问）。"""
    return cast(Any, fixture).__wrapped__


def _install_fake_asyncpg(
    monkeypatch,
    connections: list[_FakeConnection],
    executed: list[str],
    *,
    fail_on_create: bool = False,
    fail_on_drop: bool = False,
) -> None:
    async def _connect(**kwargs):
        fail_on = "CREATE DATABASE" if fail_on_create else "DROP DATABASE" if fail_on_drop else None
        conn = _FakeConnection(executed, fail_on=fail_on)
        connections.append(conn)
        return conn

    monkeypatch.setattr(schema_consistency.asyncpg, "connect", _connect)


@pytest.mark.parametrize(
    "fixture_name",
    ["metadata_db_engine", "alembic_db_engine"],
)
def test_fixture_skips_drop_when_create_fails(monkeypatch, fixture_name):
    connections: list[_FakeConnection] = []
    executed: list[str] = []
    _install_fake_asyncpg(monkeypatch, connections, executed, fail_on_create=True)

    fixture = getattr(schema_consistency, fixture_name)
    gen = _fixture_function(fixture)(_PG_PARAMS)
    with pytest.raises(RuntimeError, match="CREATE DATABASE"):
        next(gen)

    assert len(connections) == 1, "CREATE 失败后不应再建立连接执行 DROP"
    assert not any(sql.startswith("DROP DATABASE") for sql in executed)
    gen.close()


def test_metadata_fixture_cleanup_failure_does_not_mask_original(monkeypatch, caplog):
    connections: list[_FakeConnection] = []
    executed: list[str] = []
    _install_fake_asyncpg(monkeypatch, connections, executed, fail_on_drop=True)
    monkeypatch.setattr(
        schema_consistency, "create_engine", lambda url: _FakeEngine(dispose_error=RuntimeError("dispose boom"))
    )
    monkeypatch.setattr(schema_consistency.Base.metadata, "create_all", lambda engine: None)

    gen = _fixture_function(schema_consistency.metadata_db_engine)(_PG_PARAMS)
    next(gen)  # setup 成功

    with caplog.at_level("WARNING"):
        with pytest.raises(AssertionError, match="original failure"):
            gen.throw(AssertionError("original failure"))

    assert "dispose boom" in caplog.text
    assert "DROP DATABASE failed" in caplog.text


def test_alembic_fixture_cleanup_failure_does_not_mask_original(monkeypatch, caplog):
    @contextmanager
    def _noop_override(url):
        yield

    connections: list[_FakeConnection] = []
    executed: list[str] = []
    _install_fake_asyncpg(monkeypatch, connections, executed, fail_on_drop=True)
    monkeypatch.setattr(db_url_override_module, "override_db_url", _noop_override)
    monkeypatch.setattr(schema_consistency.command, "upgrade", lambda cfg, rev: None)
    monkeypatch.setattr(
        schema_consistency, "create_engine", lambda url: _FakeEngine(dispose_error=RuntimeError("dispose boom"))
    )

    gen = _fixture_function(schema_consistency.alembic_db_engine)(_PG_PARAMS)
    next(gen)  # setup 成功

    with caplog.at_level("WARNING"):
        with pytest.raises(AssertionError, match="original failure"):
            gen.throw(AssertionError("original failure"))

    assert "dispose boom" in caplog.text
    assert "DROP DATABASE failed" in caplog.text
