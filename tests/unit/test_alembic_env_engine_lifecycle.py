"""alembic/env.py run_async_migrations 引擎释放生命周期单测。

LEAD-ENGINE-001：create_async_engine 之后的 connect / run_sync 抛错或被取消时，
引擎必须在 finally 中 dispose；dispose 自身失败不得覆盖原始异常。

alembic/env.py 是脚本式模块（导入即执行 get_database_url / 离线分支），
故以 stub 模块 + importlib 方式加载，不连接真实数据库。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import types
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
import sqlalchemy.ext.asyncio

_ENV_PATH = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
_MODULE_NAME = "alembic_env_under_test"
_STUBBED_MODULES = ("alembic", "alembic.context", "config", "data.persistence.models")


class _NullTransaction:
    def __enter__(self) -> _NullTransaction:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _set_attr(module: types.ModuleType, name: str, value: object) -> None:
    """以变量名写入 stub 模块属性，兼容 ruff B010 与 pyright 对 ModuleType 的检查。"""
    setattr(module, name, value)


def _load_env_module() -> types.ModuleType:
    """在 stub 依赖下加载 alembic/env.py，返回可调用 run_async_migrations 的模块。"""
    fake_config = types.SimpleNamespace(
        config_file_name=None,
        attributes={"database_url": "postgresql+asyncpg://user:pass@localhost:5432/testdb"},
        get_main_option=lambda _name: None,
    )
    fake_context = types.ModuleType("alembic.context")
    _set_attr(fake_context, "config", fake_config)
    _set_attr(fake_context, "is_offline_mode", lambda: True)  # 导入期走离线分支，避免自动建引擎
    _set_attr(fake_context, "configure", lambda **_kwargs: None)
    _set_attr(fake_context, "begin_transaction", lambda: _NullTransaction())
    _set_attr(fake_context, "run_migrations", lambda: None)

    fake_alembic = types.ModuleType("alembic")
    _set_attr(fake_alembic, "context", fake_context)

    fake_project_config = types.ModuleType("config")
    _set_attr(fake_project_config, "DB_URL", None)

    fake_models = types.ModuleType("data.persistence.models")
    _set_attr(fake_models, "Base", types.SimpleNamespace(metadata=object()))

    stubs = {
        "alembic": fake_alembic,
        "alembic.context": fake_context,
        "config": fake_project_config,
        "data.persistence.models": fake_models,
    }

    saved_modules: dict[str, types.ModuleType | None] = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    saved_path = list(sys.path)
    saved_tz = os.environ.get("TZ")
    try:
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _ENV_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    finally:
        for name, saved in saved_modules.items():
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
        sys.modules.pop(_MODULE_NAME, None)
        sys.path[:] = saved_path
        if saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_tz
        tzset = getattr(time, "tzset", None)
        if tzset is not None:
            tzset()
    return module


class _FakeConnection:
    def __init__(self, on_run_sync: Callable[[], Awaitable[None]] | None) -> None:
        self._on_run_sync = on_run_sync

    async def run_sync(self, _fn: object) -> None:
        if self._on_run_sync is not None:
            await self._on_run_sync()


class _FakeConnectContext:
    def __init__(self, engine: _FakeAsyncEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _FakeConnection:
        if self._engine.connect_error is not None:
            raise self._engine.connect_error
        return _FakeConnection(self._engine.on_run_sync)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _FakeAsyncEngine:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        on_run_sync: Callable[[], Awaitable[None]] | None = None,
        dispose_error: Exception | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.on_run_sync = on_run_sync
        self.dispose_error = dispose_error
        self.connect_calls = 0
        self.dispose_calls = 0

    def connect(self) -> _FakeConnectContext:
        self.connect_calls += 1
        return _FakeConnectContext(self)

    async def dispose(self) -> None:
        self.dispose_calls += 1
        if self.dispose_error is not None:
            raise self.dispose_error


async def _raise_runtime_error(message: str) -> None:
    raise RuntimeError(message)


async def _raise_cancelled() -> None:
    raise asyncio.CancelledError


@pytest.fixture(scope="module")
def alembic_env() -> types.ModuleType:
    return _load_env_module()


@pytest.fixture
def install_engine(monkeypatch: pytest.MonkeyPatch) -> Callable[[_FakeAsyncEngine], _FakeAsyncEngine]:
    def _install(engine: _FakeAsyncEngine) -> _FakeAsyncEngine:
        monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", lambda *_args, **_kwargs: engine)
        return engine

    return _install


async def test_success_path_disposes_engine(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    engine = install_engine(_FakeAsyncEngine())

    await alembic_env.run_async_migrations()

    assert engine.connect_calls == 1
    assert engine.dispose_calls == 1


async def test_connect_failure_still_disposes_engine(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    engine = install_engine(_FakeAsyncEngine(connect_error=ConnectionError("connect refused")))

    with pytest.raises(ConnectionError, match="connect refused"):
        await alembic_env.run_async_migrations()

    assert engine.dispose_calls == 1


async def test_migration_failure_still_disposes_engine(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    async def _fail() -> None:
        await _raise_runtime_error("migration failed")

    engine = install_engine(_FakeAsyncEngine(on_run_sync=_fail))

    with pytest.raises(RuntimeError, match="migration failed"):
        await alembic_env.run_async_migrations()

    assert engine.dispose_calls == 1


async def test_cancelled_error_propagates_and_disposes_engine(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    engine = install_engine(_FakeAsyncEngine(on_run_sync=_raise_cancelled))

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await alembic_env.run_async_migrations()

    assert exc_info.type is asyncio.CancelledError
    assert engine.dispose_calls == 1


async def test_task_cancellation_still_disposes_engine(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    started = asyncio.Event()

    async def _block() -> None:
        started.set()
        await asyncio.Event().wait()

    engine = install_engine(_FakeAsyncEngine(on_run_sync=_block))
    task = asyncio.create_task(alembic_env.run_async_migrations())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task

    assert exc_info.type is asyncio.CancelledError
    assert engine.dispose_calls == 1


async def test_dispose_failure_propagates_when_no_original_error(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
) -> None:
    engine = install_engine(_FakeAsyncEngine(dispose_error=RuntimeError("dispose failed")))

    with pytest.raises(RuntimeError, match="dispose failed"):
        await alembic_env.run_async_migrations()

    assert engine.dispose_calls == 1


async def test_dispose_failure_does_not_mask_original_error(
    alembic_env: types.ModuleType,
    install_engine: Callable[[_FakeAsyncEngine], _FakeAsyncEngine],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _fail() -> None:
        await _raise_runtime_error("migration failed")

    engine = install_engine(_FakeAsyncEngine(on_run_sync=_fail, dispose_error=RuntimeError("dispose failed")))
    caplog.set_level("WARNING", logger=alembic_env.__name__)

    with pytest.raises(RuntimeError, match="migration failed"):
        await alembic_env.run_async_migrations()

    assert engine.dispose_calls == 1
    messages = [record.getMessage() for record in caplog.records]
    assert "Failed to dispose Alembic migration engine: dispose failed" in messages
