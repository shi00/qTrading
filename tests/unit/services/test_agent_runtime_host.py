"""Agent 运行时宿主组装单元测试（设计 §10.6 / HANDOFF §8.1-§8.3）。

通过模块级 seam 注入验证组装契约，无真实 DB：
- 组装顺序：双迁移成功后才创建引擎/DBOS/Datasource/Client；迁移失败全部不建
- 引擎预算：runtime 4/overflow 0/timeout 3/hide_parameters；control 2；双引擎均
  psycopg application_name 标识
- DBOS 配置：run_migrations=False、enable_otlp=False、max_executor_threads=4、
  use_listen_notify=True、sharding=False、真实 application_version、每进程 executor_id
- Datasource/Client 契约：Datasource 注入 runtime_engine+run_migrations=False；
  Client 注入 control_engine+禁监听+禁无限连接重试
- close() 顺序：DBOS.destroy → Client.destroy → 双引擎 dispose

真实 PG 行为见 ``tests/integration/test_agent_runtime_host.py``。
owner-loop 桥（§8.2）与 launch 启动隔离（§8.4 needs-spike）不在本片范围。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from services.agent_service import runtime as runtime_mod
from services.agent_service.runtime import AgentRuntimeSettings, assemble_agent_runtime

pytestmark = pytest.mark.unit


class _RecordingHost:
    """seam 记录器：按调用顺序登记宿主对象构建与销毁。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.engines: dict[int, dict[str, Any]] = {}
        self.dbos_config: dict[str, Any] | None = None
        self.datasource_kwargs: dict[str, Any] = {}
        self.client_kwargs: dict[str, Any] = {}
        self.destroyed = False

    def fake_run_migrations(self, url: str) -> None:
        self.calls.append("migrations")

    def fake_create_engine(self, url: str, **kwargs: Any) -> object:
        self.calls.append(f"engine:{kwargs.get('pool_size')}")
        self.engines[kwargs.get("pool_size")] = kwargs
        return object()  # 不透明句柄，仅供身份比对

    def fake_client_init(self, **kwargs: Any) -> _FakeClient:
        self.calls.append("client")
        self.client_kwargs = kwargs
        return _FakeClient(self)


class _FakeDatasource:
    def __init__(self, host: _RecordingHost) -> None:
        self._host = host


class _FakeClient:
    def __init__(self, host: _RecordingHost) -> None:
        self._host = host

    def destroy(self) -> None:
        self._host.calls.append("client_destroy")


@pytest.fixture()
def host(monkeypatch: pytest.MonkeyPatch) -> _RecordingHost:
    """注入全部 seam 并返回记录器。"""
    rec = _RecordingHost()
    monkeypatch.setattr(runtime_mod, "run_agent_runtime_migrations", rec.fake_run_migrations)
    monkeypatch.setattr(runtime_mod, "_create_engine", rec.fake_create_engine)

    def fake_dbos_destroy(**kwargs: Any) -> None:
        rec.calls.append("dbos_destroy")

    class _FakeDBOS:
        def __init__(self, *, config: dict[str, Any]) -> None:
            rec.calls.append("dbos")
            rec.dbos_config = config

        destroy = staticmethod(fake_dbos_destroy)

    def fake_datasource_create(**kwargs: Any) -> _FakeDatasource:
        rec.calls.append("datasource")
        rec.datasource_kwargs = kwargs
        return _FakeDatasource(rec)

    class _FakeDatasourceClass:
        create = staticmethod(fake_datasource_create)

    monkeypatch.setattr(runtime_mod, "DBOS", _FakeDBOS)
    monkeypatch.setattr(runtime_mod, "SQLAlchemyDatasource", _FakeDatasourceClass)
    monkeypatch.setattr(runtime_mod, "DBOSClient", rec.fake_client_init)
    return rec


def _settings() -> AgentRuntimeSettings:
    return AgentRuntimeSettings(
        system_database_url="postgresql+psycopg://migrator@host/qtrading_agent_runtime",
        application_version="0.11.7",
    )


class TestAssemblyContract:
    """组装顺序与配置契约。"""

    def test_order_and_budgets(self, host: _RecordingHost) -> None:
        handle = assemble_agent_runtime(_settings())
        assert host.calls == ["migrations", "engine:4", "engine:2", "dbos", "datasource", "client"]

        runtime_kwargs = host.engines[4]
        assert runtime_kwargs["max_overflow"] == 0
        assert runtime_kwargs["pool_timeout"] == 3
        assert runtime_kwargs["hide_parameters"] is True
        control_kwargs = host.engines[2]
        assert control_kwargs["max_overflow"] == 0
        assert control_kwargs["hide_parameters"] is True

        assert host.dbos_config is not None
        config = host.dbos_config
        assert config["name"] == "qtrading-agent-runtime"
        assert config["run_migrations"] is False
        assert config["enable_otlp"] is False
        assert config["max_executor_threads"] == 4
        assert config["use_listen_notify"] is True
        assert config["sharding"] is False
        assert config["application_version"] == "0.11.7"
        assert config["system_database_engine"] is handle.runtime_engine

        assert host.datasource_kwargs["schema"] == "agent_ds"
        assert host.datasource_kwargs["engine"] is handle.runtime_engine
        assert host.datasource_kwargs["run_migrations"] is False

        assert host.client_kwargs["system_database_engine"] is handle.control_engine
        assert host.client_kwargs["use_listen_notify"] is False
        assert host.client_kwargs["retry_connection_errors"] is False

    def test_settings_default_executor_id_is_per_process(self, host: _RecordingHost) -> None:
        """executor_id 缺省为每进程唯一 epoch（HANDOFF §8.4 第 4 条）。"""
        first = assemble_agent_runtime(_settings())
        second = assemble_agent_runtime(_settings())
        assert first.executor_id
        assert second.executor_id
        assert first.executor_id != second.executor_id

    def test_explicit_executor_id_and_url_wired(self, host: _RecordingHost) -> None:
        settings = AgentRuntimeSettings(
            system_database_url="postgresql+psycopg://migrator@host/db",
            application_version="1.2.3",
            executor_id="epoch-42",
        )
        handle = assemble_agent_runtime(settings)
        assert handle.executor_id == "epoch-42"
        assert host.dbos_config is not None
        assert host.dbos_config["executor_id"] == "epoch-42"
        assert host.dbos_config["system_database_url"] == settings.system_database_url

    def test_migration_failure_builds_nothing(self, host: _RecordingHost, monkeypatch: pytest.MonkeyPatch) -> None:
        """双迁移失败：异常传播，引擎/DBOS/Datasource/Client 一律不建。"""

        def _failing(url: str) -> None:
            host.calls.append("migrations")
            raise runtime_mod.AgentRuntimeMigrationError("boom")

        monkeypatch.setattr(runtime_mod, "run_agent_runtime_migrations", _failing)
        with pytest.raises(runtime_mod.AgentRuntimeMigrationError, match="boom"):
            assemble_agent_runtime(_settings())
        assert host.calls == ["migrations"]

    def test_close_order_and_dispose(self, host: _RecordingHost, monkeypatch: pytest.MonkeyPatch) -> None:
        """close()：DBOS.destroy → Client.destroy → 双引擎 dispose；幂等安全。"""

        def fake_create_engine(url: str, **kwargs: Any) -> Any:
            size = kwargs["pool_size"]
            host.calls.append(f"engine:{size}")

            class _Engine:
                def dispose(self) -> None:
                    host.calls.append(f"dispose:{size}")

            return _Engine()

        monkeypatch.setattr(runtime_mod, "_create_engine", fake_create_engine)
        handle = assemble_agent_runtime(_settings())
        handle.close()
        handle.close()  # 幂等：仅关闭一次
        assert host.calls == [
            "migrations",
            "engine:4",
            "engine:2",
            "dbos",
            "datasource",
            "client",
            "dbos_destroy",
            "client_destroy",
            "dispose:4",
            "dispose:2",
        ]
