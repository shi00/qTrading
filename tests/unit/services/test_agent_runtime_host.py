"""Agent 运行时宿主组装单元测试（设计 §10.6 / HANDOFF §8.1-§8.3）。

通过模块级 seam 注入验证组装契约，无真实 DB：
- 组装顺序：双迁移成功后才创建引擎/DBOS/Datasource/Client；迁移失败全部不建
- 引擎预算：runtime 4/overflow 0/timeout 3/hide_parameters；control 1；双引擎均
  psycopg application_name 标识；运行期增量 7 = 4 运行 + 1 LISTEN + 1 控制 + 1 实例锁
  （检视 R12）
- DBOS 配置：run_migrations=False、enable_otlp=False、max_executor_threads=4、
  use_listen_notify=True、sharding=False、真实 application_version、每进程 executor_id
- Datasource/Client 契约：Datasource 注入 runtime_engine+run_migrations=False；
  Client 注入 control_engine+禁监听+禁无限连接重试
- close() 顺序：DBOS.destroy → Client.destroy → 双引擎 dispose
- 失败收口（检视 R08）：任一后续阶段失败按已取得对象逆序收口（Client → DBOS →
  双引擎）；清理确认成功后传播原始异常，收口未确认时以
  ``AgentRuntimeAssemblyCloseError`` 上报真实异常与未核阶段并保留可重试半成品；
  失败后可再次组装

真实 PG 行为见 ``tests/integration/test_agent_runtime_host.py``。
owner-loop 桥（§8.2）与 launch 启动隔离（§8.4 needs-spike）不在本片范围。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

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
        self.engine_urls: dict[int, str] = {}
        self.migration_url: str | None = None
        self.dbos_config: dict[str, Any] | None = None
        self.datasource_kwargs: dict[str, Any] = {}
        self.client_kwargs: dict[str, Any] = {}
        self.destroyed = False

    def fake_run_migrations(self, url: str) -> None:
        self.calls.append("migrations")
        self.migration_url = url

    def fake_create_engine(self, url: str, **kwargs: Any) -> object:
        self.calls.append(f"engine:{kwargs.get('pool_size')}")
        pool_size = kwargs.get("pool_size")
        assert isinstance(pool_size, int)
        self.engines[pool_size] = kwargs
        self.engine_urls[pool_size] = url
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


_RUNTIME_URL = "postgresql+psycopg://agent_runtime@host/qtrading_agent_runtime"
_MIGRATION_URL = "postgresql+psycopg://agent_migrator@host/qtrading_agent_runtime"


def _settings() -> AgentRuntimeSettings:
    return AgentRuntimeSettings(
        system_database_url=_RUNTIME_URL,
        migration_database_url=_MIGRATION_URL,
        application_version="0.11.7",
    )


class TestAssemblyContract:
    """组装顺序与配置契约。"""

    def test_order_and_budgets(self, host: _RecordingHost) -> None:
        handle = assemble_agent_runtime(_settings())
        assert host.calls == [
            "migrations",
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"engine:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            "dbos",
            "datasource",
            "client",
        ]
        # 迁移与运行凭据分离：迁移走专用高权限连接，双运行池只用运行连接
        assert host.migration_url == _MIGRATION_URL
        assert host.engine_urls[runtime_mod.RUNTIME_ENGINE_POOL_SIZE] == _RUNTIME_URL
        assert host.engine_urls[runtime_mod.CONTROL_ENGINE_POOL_SIZE] == _RUNTIME_URL

        runtime_kwargs = host.engines[runtime_mod.RUNTIME_ENGINE_POOL_SIZE]
        assert runtime_kwargs["max_overflow"] == 0
        assert runtime_kwargs["pool_timeout"] == 3
        assert runtime_kwargs["hide_parameters"] is True
        # 会话超时（检视 F1：注入引擎后 SDK 不兜底，HANDOFF §8.1 有限超时）
        runtime_options = runtime_kwargs["connect_args"]["options"]
        assert "statement_timeout=30000" in runtime_options
        assert "idle_in_transaction_session_timeout=60000" in runtime_options
        control_kwargs = host.engines[runtime_mod.CONTROL_ENGINE_POOL_SIZE]
        assert control_kwargs["max_overflow"] == 0
        assert control_kwargs["hide_parameters"] is True
        assert "statement_timeout=30000" in control_kwargs["connect_args"]["options"]

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
        assert config["dbos_system_schema"] == "dbos"

        assert host.datasource_kwargs["schema"] == "agent_ds"
        assert host.datasource_kwargs["engine"] is handle.runtime_engine
        assert host.datasource_kwargs["run_migrations"] is False

        assert host.client_kwargs["system_database_engine"] is handle.control_engine
        assert host.client_kwargs["use_listen_notify"] is False
        assert host.client_kwargs["retry_connection_errors"] is False
        # 应用稳定名（检视 F2：HANDOFF §8.3 固定签名，§8.4 归属过滤依据）
        assert host.client_kwargs["application_name"] == "qtrading-agent-runtime"

    def test_connection_budget_includes_instance_guard(self) -> None:
        """检视 R12：运行期增量 7 条 = 4 运行池 + 1 脱池 LISTEN + 1 控制池 + 1 实例锁。

        实例锁为独立第 4 类所有权（独立引擎单连接），不得靠注释并入 LISTEN 或
        控制池；任一项为 0/负数或合计偏离 7 都说明预算被悄悄放大。
        """
        ledger = {
            "runtime_pool": runtime_mod.RUNTIME_ENGINE_POOL_SIZE,
            "dbos_listen": runtime_mod.DBOS_LISTEN_CONNECTION_RESERVE,
            "control_pool": runtime_mod.CONTROL_ENGINE_POOL_SIZE,
            "instance_guard": runtime_mod.INSTANCE_GUARD_CONNECTION_RESERVE,
        }
        assert ledger == {"runtime_pool": 4, "dbos_listen": 1, "control_pool": 1, "instance_guard": 1}
        assert min(ledger.values()) >= 1
        assert sum(ledger.values()) == runtime_mod.AGENT_RUNTIME_CONNECTION_BUDGET == 7

    def test_settings_default_executor_id_is_per_process(self, host: _RecordingHost) -> None:
        """executor_id 缺省为每进程唯一 epoch（HANDOFF §8.4 第 4 条）。"""
        first = assemble_agent_runtime(_settings())
        second = assemble_agent_runtime(_settings())
        assert first.executor_id
        assert second.executor_id
        assert first.executor_id != second.executor_id

    def test_explicit_executor_id_and_url_wired(self, host: _RecordingHost) -> None:
        settings = AgentRuntimeSettings(
            system_database_url="postgresql+psycopg://agent_runtime@host/db",
            migration_database_url="postgresql+psycopg://agent_migrator@host/db",
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
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"engine:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            "dbos",
            "datasource",
            "client",
            "dbos_destroy",
            "client_destroy",
            f"dispose:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"dispose:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
        ]


class TestCredentialSeparation:
    """迁移/运行凭据分离（R2-I38）：共用只能在显式允许时发生，且不冒充已分离。"""

    def test_missing_migration_credentials_refused(self, host: _RecordingHost) -> None:
        """未配置迁移凭据且未显式允许共用：拒绝组装，不建引擎、不记迁移。"""
        settings = AgentRuntimeSettings(system_database_url=_RUNTIME_URL, application_version="1")
        with pytest.raises(runtime_mod.AgentRuntimeMigrationError, match="迁移凭据"):
            assemble_agent_runtime(settings)
        assert host.calls == []
        assert host.migration_url is None

    def test_same_url_without_opt_in_refused(self, host: _RecordingHost) -> None:
        """迁移 URL 与运行 URL 相同（误配置）视作共用，未显式允许即拒绝。"""
        settings = AgentRuntimeSettings(
            system_database_url=_RUNTIME_URL,
            migration_database_url=_RUNTIME_URL,
            application_version="1",
        )
        with pytest.raises(runtime_mod.AgentRuntimeMigrationError, match="迁移凭据"):
            assemble_agent_runtime(settings)
        assert host.calls == []

    def test_shared_credentials_explicit_opt_in_warns(
        self, host: _RecordingHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        """显式允许共用（本机单用户）：迁移与运行同连接，并以告警如实说明。"""
        caplog.set_level(logging.WARNING, logger=runtime_mod.__name__)
        settings = AgentRuntimeSettings(
            system_database_url=_RUNTIME_URL,
            allow_shared_migration_credentials=True,
            application_version="1",
        )
        assemble_agent_runtime(settings)
        assert host.migration_url == _RUNTIME_URL
        assert host.engine_urls[runtime_mod.RUNTIME_ENGINE_POOL_SIZE] == _RUNTIME_URL
        assert "share one connection" in caplog.text
        # 日志不得输出连接串（R9）
        assert "migrator-host" not in caplog.text


class TestMigrationTargetIdentity:
    """②：迁移连接必须与运行连接同实例同逻辑库（URL 规范化后比较）。"""

    def test_url_variants_accepted(self, host: _RecordingHost) -> None:
        """驱动名/角色/主机大小写/显式默认端口不改变实例与库身份。"""

        settings = AgentRuntimeSettings(
            system_database_url="postgresql+psycopg://agent_runtime@HOST/qtrading_agent_runtime",
            migration_database_url="postgresql://agent_migrator@host:5432/qtrading_agent_runtime",
            application_version="0.11.7",
        )
        assemble_agent_runtime(settings)

        assert host.migration_url == settings.migration_database_url

    @pytest.mark.parametrize(
        "migration_url",
        [
            "postgresql+psycopg://agent_migrator@other-host/qtrading_agent_runtime",
            "postgresql+psycopg://agent_migrator@host:5433/qtrading_agent_runtime",
            "postgresql+psycopg://agent_migrator@host/other_database",
        ],
    )
    def test_different_instance_or_database_refused(self, host: _RecordingHost, migration_url: str) -> None:
        """迁移落到别的实例/别的逻辑库：拒绝启动，不建引擎、不记迁移。"""

        settings = AgentRuntimeSettings(
            system_database_url=_RUNTIME_URL,
            migration_database_url=migration_url,
            application_version="0.11.7",
        )
        with pytest.raises(runtime_mod.AgentRuntimeMigrationError, match="同一 PostgreSQL 实例"):
            assemble_agent_runtime(settings)

        assert host.calls == []
        assert host.migration_url is None

    def test_unix_socket_dir_is_part_of_instance_identity(self, host: _RecordingHost) -> None:
        """Unix socket 目录（query 参数 host）参与实例比较：不同目录即不同实例。"""

        socket_runtime = "postgresql+psycopg://agent_runtime@/qtrading_agent_runtime?host=/var/run/postgresql"
        same_socket = "postgresql+psycopg://agent_migrator@/qtrading_agent_runtime?host=/var/run/postgresql"
        other_socket = "postgresql+psycopg://agent_migrator@/qtrading_agent_runtime?host=/tmp/pg"
        assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=socket_runtime,
                migration_database_url=same_socket,
                application_version="0.11.7",
            )
        )
        assert host.migration_url == same_socket

        with pytest.raises(runtime_mod.AgentRuntimeMigrationError, match="同一 PostgreSQL 实例"):
            assemble_agent_runtime(
                AgentRuntimeSettings(
                    system_database_url=socket_runtime,
                    migration_database_url=other_socket,
                    application_version="0.11.7",
                )
            )

    def test_unparsable_migration_url_refused_without_echo(self, host: _RecordingHost) -> None:
        """URL 无法解析：拒绝启动且消息不回显连接串（R9）。"""

        settings = AgentRuntimeSettings(
            system_database_url=_RUNTIME_URL,
            migration_database_url="not-a-valid-url",
            application_version="0.11.7",
        )
        with pytest.raises(runtime_mod.AgentRuntimeMigrationError) as excinfo:
            assemble_agent_runtime(settings)

        assert "not-a-valid-url" not in str(excinfo.value)
        assert host.calls == []


class _FailureSeams:
    """失败路径 seam 记录器：可配置各阶段抛错及清理自身失败。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.control_engine_error: BaseException | None = None
        self.datasource_error: BaseException | None = None
        self.client_error: BaseException | None = None
        self.dbos_destroy_error: BaseException | None = None
        self.dispose_errors: set[int] = set()

    def run_migrations(self, url: str) -> None:
        self.calls.append("migrations")

    def create_engine(self, url: str, **kwargs: Any) -> _FakeFailureEngine:
        size = kwargs["pool_size"]
        assert isinstance(size, int)
        if size == runtime_mod.CONTROL_ENGINE_POOL_SIZE and self.control_engine_error is not None:
            raise self.control_engine_error
        self.calls.append(f"engine:{size}")
        return _FakeFailureEngine(self, size)

    def create_datasource(self, **kwargs: Any) -> object:
        self.calls.append("datasource")
        if self.datasource_error is not None:
            raise self.datasource_error
        return object()

    def create_client(self, **kwargs: Any) -> _FakeFailureClient:
        self.calls.append("client")
        if self.client_error is not None:
            raise self.client_error
        return _FakeFailureClient(self)


class _FakeFailureEngine:
    def __init__(self, seams: _FailureSeams, size: int) -> None:
        self._seams = seams
        self._size = size

    def dispose(self) -> None:
        self._seams.calls.append(f"dispose:{self._size}")
        if self._size in self._seams.dispose_errors:
            raise RuntimeError(f"dispose failed: {self._size}")


class _FakeFailureClient:
    def __init__(self, seams: _FailureSeams) -> None:
        self._seams = seams

    def destroy(self) -> None:
        self._seams.calls.append("client_destroy")


@pytest.fixture()
def failure_seams(monkeypatch: pytest.MonkeyPatch) -> _FailureSeams:
    seams = _FailureSeams()
    monkeypatch.setattr(runtime_mod, "run_agent_runtime_migrations", seams.run_migrations)
    monkeypatch.setattr(runtime_mod, "_create_engine", seams.create_engine)

    class _FakeDBOS:
        def __init__(self, *, config: dict[str, Any]) -> None:
            seams.calls.append("dbos")

        @staticmethod
        def destroy() -> None:
            seams.calls.append("dbos_destroy")
            if seams.dbos_destroy_error is not None:
                raise seams.dbos_destroy_error

    class _FakeDatasourceClass:
        create = staticmethod(seams.create_datasource)

    monkeypatch.setattr(runtime_mod, "DBOS", _FakeDBOS)
    monkeypatch.setattr(runtime_mod, "SQLAlchemyDatasource", _FakeDatasourceClass)
    monkeypatch.setattr(runtime_mod, "DBOSClient", seams.create_client)
    return seams


class TestAssemblyFailureCleanup:
    """检视 R08：后续阶段失败按已取得对象逆序收口，且可再次组装。"""

    def test_control_engine_failure_disposes_runtime_engine(self, failure_seams: _FailureSeams) -> None:
        failure_seams.control_engine_error = RuntimeError("control init failed")
        with pytest.raises(RuntimeError, match="control init failed"):
            assemble_agent_runtime(_settings())
        assert failure_seams.calls == [
            "migrations",
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"dispose:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
        ]

    def test_datasource_failure_destroys_dbos_and_disposes_engines(self, failure_seams: _FailureSeams) -> None:
        boom = RuntimeError("verify failed")
        failure_seams.datasource_error = boom
        with pytest.raises(RuntimeError) as exc_info:
            assemble_agent_runtime(_settings())
        assert exc_info.value is boom  # 原始异常不被清理掩盖
        assert failure_seams.calls == [
            "migrations",
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"engine:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            "dbos",
            "datasource",
            "dbos_destroy",
            f"dispose:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            f"dispose:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
        ]

    def test_client_failure_destroys_dbos_and_disposes_engines(self, failure_seams: _FailureSeams) -> None:
        boom = ConnectionError("system db unreachable")
        failure_seams.client_error = boom
        with pytest.raises(ConnectionError) as exc_info:
            assemble_agent_runtime(_settings())
        assert exc_info.value is boom
        assert failure_seams.calls == [
            "migrations",
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"engine:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            "dbos",
            "datasource",
            "client",
            "dbos_destroy",
            f"dispose:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            f"dispose:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
        ]
        assert "client_destroy" not in failure_seams.calls  # 构造失败即无对象可收口

    def test_unverified_cleanup_reports_facts_and_keeps_partial_handle(self, failure_seams: _FailureSeams) -> None:
        """收口未确认：如实上报真实异常与未核阶段，保留可重试的半成品（不提前清引用）。"""

        boom = RuntimeError("verify failed")
        failure_seams.datasource_error = boom
        failure_seams.dbos_destroy_error = RuntimeError("destroy failed")
        failure_seams.dispose_errors.add(runtime_mod.CONTROL_ENGINE_POOL_SIZE)

        with pytest.raises(runtime_mod.AgentRuntimeAssemblyCloseError) as exc_info:
            assemble_agent_runtime(_settings())

        error = exc_info.value
        assert error.original is boom
        assert error.__cause__ is boom
        assert error.close_failures == ("dbos:RuntimeError", "control_engine:RuntimeError")
        # 原句柄可查：半成品保留已取得对象，且 runtime 引擎已确认为收口项
        assert error.pending.dbos is not None
        assert error.pending.control_engine is not None
        assert error.pending.runtime_engine is not None
        assert "RuntimeError" in str(error)
        # 清理自身失败后仍继续收口 runtime 引擎
        assert failure_seams.calls == [
            "migrations",
            f"engine:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
            f"engine:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            "dbos",
            "datasource",
            "dbos_destroy",
            f"dispose:{runtime_mod.CONTROL_ENGINE_POOL_SIZE}",
            f"dispose:{runtime_mod.RUNTIME_ENGINE_POOL_SIZE}",
        ]

    def test_partial_handle_retry_close_releases_references(self, failure_seams: _FailureSeams) -> None:
        """重试关闭确认成功后释放半成品引用（幂等、可交给调用方收口后再放锁）。"""

        failure_seams.datasource_error = RuntimeError("verify failed")
        failure_seams.dbos_destroy_error = RuntimeError("destroy failed")
        with pytest.raises(runtime_mod.AgentRuntimeAssemblyCloseError) as exc_info:
            assemble_agent_runtime(_settings())
        pending = exc_info.value.pending

        failure_seams.dbos_destroy_error = None
        pending.close()

        assert pending.client is None
        assert pending.dbos is None
        assert pending.control_engine is None
        assert pending.runtime_engine is None
        assert failure_seams.calls.count("dbos_destroy") == 2

    def test_cancel_during_assembly_is_not_masked_by_cleanup_failure(
        self, failure_seams: _FailureSeams, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``CancelledError`` 原样传播；收口未确认只如实记录，不冒充完成（R2/R21）。"""

        caplog.set_level(logging.ERROR, logger=runtime_mod.__name__)
        failure_seams.datasource_error = asyncio.CancelledError()
        failure_seams.dbos_destroy_error = RuntimeError("destroy failed")

        with pytest.raises(asyncio.CancelledError) as exc_info:
            assemble_agent_runtime(_settings())

        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert "dbos:RuntimeError" in caplog.text

    def test_reassembly_after_failed_attempt(self, failure_seams: _FailureSeams) -> None:
        failure_seams.datasource_error = RuntimeError("verify failed")
        with pytest.raises(RuntimeError) as exc_info:
            assemble_agent_runtime(_settings())
        assert str(exc_info.value) == "verify failed"
        failure_seams.datasource_error = None
        handle = assemble_agent_runtime(_settings())
        assert handle.launched is False
        assert failure_seams.calls.count("dbos") == 2  # 第二次为全新组装，不复用失败实例
        assert failure_seams.calls.count("dbos_destroy") == 1
        assert failure_seams.calls[-3:] == ["dbos", "datasource", "client"]


class _CloseFakes:
    """关闭门闩测试替身：派发门闩/桥/事实端口/宿主关闭均记录调用顺序。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.dispatch_ok = True
        self.inflight = 0
        self.saved = True
        self.unknown: tuple[str, ...] = ("operation-unknown-1",)
        self.sink_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.residual: tuple[str, ...] = ()
        self.closed = False
        self.frozen = False
        self.on_save: Callable[[], None] | None = None
        #: 保存事实暂停点：置位后 ``save_end_or_unknown`` 停在 await，用于并发测试。
        self.save_gate: asyncio.Event | None = None
        self.save_entered = asyncio.Event()

    def freeze_submissions(self) -> bool:
        self.calls.append("bridge_freeze")
        frozen = not self.frozen
        self.frozen = True
        return frozen

    def close_dispatch(self) -> bool:
        self.calls.append("dispatch_close")
        return self.dispatch_ok

    def dispatch_closed(self) -> bool:
        return self.dispatch_ok

    def inflight_task_count(self) -> int:
        return self.inflight

    def residual_io(self) -> tuple[str, ...]:
        return self.residual

    async def save_end_or_unknown(self, command: Any) -> Any:
        self.calls.append("save_facts")
        self.save_entered.set()
        if self.save_gate is not None:
            await self.save_gate.wait()
        if self.on_save is not None:
            self.on_save()
        if self.sink_error is not None:
            raise self.sink_error
        return runtime_mod.CloseFactOutcome(saved=self.saved, unknown_associations=self.unknown)

    def close(self) -> None:
        self.calls.append("handle_close")
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


def _runtime(fakes: _CloseFakes, guard: Any = None) -> Any:
    return runtime_mod.AgentRuntime(
        handle=cast(Any, fakes),
        bridge=cast(Any, fakes),
        dispatch_gate=cast(Any, fakes),
        fact_sink=cast(Any, fakes),
        blocking_runner=_direct_runner,
        residual_io_probe=fakes.residual_io,
        instance_guard=guard,
    )


async def _direct_runner(fn: Any) -> None:
    fn()


def _command() -> Any:
    return runtime_mod.CloseCommandIdentity(user_scope="user-1", close_command_id="close-1")


class TestPrepareCloseLatch:
    """关闭前置门闩（HANDOFF §8.5）：未就绪不进入清理、顺序固定、幂等重试。"""

    async def test_ready_after_all_gates_and_in_order(self) -> None:
        fakes = _CloseFakes()
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is True
        assert receipt.blocking_reasons == ()
        assert receipt.unknown_associations == ("operation-unknown-1",)
        assert receipt.inflight_task_count == 0
        assert receipt.command.close_command_id == "close-1"
        assert fakes.calls == ["bridge_freeze", "dispatch_close", "save_facts", "handle_close"]
        assert receipt.checked_at.endswith("Z")

    async def test_dispatch_not_closed_blocks_before_any_other_step(self) -> None:
        fakes = _CloseFakes()
        fakes.dispatch_ok = False
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_DISPATCH_NOT_CLOSED,)
        assert fakes.calls == ["bridge_freeze", "dispatch_close"]  # 不保存、不关闭
        assert receipt.unknown_associations == ()

    async def test_inflight_io_blocks_fact_save_and_close(self) -> None:
        fakes = _CloseFakes()
        fakes.inflight = 2
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_IO_IN_FLIGHT,)
        assert receipt.inflight_task_count == 2
        assert fakes.calls == ["bridge_freeze", "dispatch_close"]

    async def test_freeze_submissions_precedes_dispatch_close(self) -> None:
        """关闭门闩第一步即冻结桥提交（R09：ready 后不得再起跑新 IO）。"""
        fakes = _CloseFakes()
        await _runtime(fakes).prepare_close(_command())
        assert fakes.frozen is True
        assert fakes.calls[:2] == ["bridge_freeze", "dispatch_close"]

    async def test_io_started_during_fact_save_blocks_close(self) -> None:
        """保存事实 await 期间起跑的 IO 必须在关闭前重核、不得关闭资源（R09）。"""
        fakes = _CloseFakes()

        def _io_starts_during_save() -> None:
            fakes.inflight = 1

        fakes.on_save = _io_starts_during_save
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_IO_IN_FLIGHT,)
        assert receipt.inflight_task_count == 1
        assert "handle_close" not in fakes.calls

    async def test_unsaved_facts_block_close_and_keep_unknown(self) -> None:
        fakes = _CloseFakes()
        fakes.saved = False
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_FACTS_NOT_SAVED,)
        assert receipt.unknown_associations == ("operation-unknown-1",)
        assert "handle_close" not in fakes.calls

    async def test_sink_exception_is_retryable(self) -> None:
        fakes = _CloseFakes()
        fakes.sink_error = RuntimeError("db down")
        runtime = _runtime(fakes)
        first = await runtime.prepare_close(_command())
        assert first.ready is False
        assert first.blocking_reasons == (runtime_mod.CLOSE_BLOCK_FACTS_SAVE_FAILED,)
        fakes.sink_error = None
        second = await runtime.prepare_close(_command())
        assert second.ready is True  # 失败后保留资源，可重试

    async def test_close_exception_blocks_and_allows_retry(self) -> None:
        fakes = _CloseFakes()
        fakes.close_error = RuntimeError("destroy failed")
        runtime = _runtime(fakes)
        first = await runtime.prepare_close(_command())
        assert first.ready is False
        assert first.blocking_reasons == (runtime_mod.CLOSE_BLOCK_RUNTIME_CLOSE_FAILED,)
        fakes.close_error = None
        second = await runtime.prepare_close(_command())
        assert second.ready is True
        assert fakes.closed is True

    async def test_partial_close_reports_incomplete_not_ready(self) -> None:
        """资源未全部关闭（closed=False）不得报就绪（检视 B1/R21）。"""

        class _PartialHandle(_CloseFakes):
            def close(self) -> None:
                self.calls.append("handle_close")
                # 假装 destroy 静默半途失败：closed 保持 False

        fakes = _PartialHandle()
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_RUNTIME_CLOSE_INCOMPLETE,)

    async def test_residual_io_blocks_before_fact_save(self) -> None:
        fakes = _CloseFakes()
        fakes.residual = ("sdk_stream", "mcp_worker")
        receipt = await _runtime(fakes).prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (f"{runtime_mod.CLOSE_BLOCK_RESIDUAL_IO}:sdk_stream,mcp_worker",)
        assert receipt.residual_io == ("sdk_stream", "mcp_worker")
        assert fakes.calls == ["bridge_freeze", "dispatch_close"]

    async def test_missing_residual_probe_blocks_close(self) -> None:
        """未注入残留 IO 探针：缺退出证据不得报就绪（R2-I35/R21）。"""
        fakes = _CloseFakes()
        runtime = runtime_mod.AgentRuntime(
            handle=cast(Any, fakes),
            bridge=cast(Any, fakes),
            dispatch_gate=cast(Any, fakes),
            fact_sink=cast(Any, fakes),
            blocking_runner=_direct_runner,
        )
        receipt = await runtime.prepare_close(_command())
        assert receipt.ready is False
        assert receipt.blocking_reasons == (runtime_mod.CLOSE_BLOCK_RESIDUAL_IO_UNPROBED,)
        assert receipt.residual_io_probed is False
        assert fakes.calls == ["bridge_freeze", "dispatch_close"]  # 不保存、不关闭

    async def test_idempotent_retry_returns_original_receipt(self) -> None:
        fakes = _CloseFakes()
        runtime = _runtime(fakes)
        first = await runtime.prepare_close(_command())
        second = await runtime.prepare_close(_command())
        assert second is first  # 原身份重试：不重复 destroy
        assert fakes.calls.count("handle_close") == 1

    async def test_different_close_identity_is_not_reported_ready(self) -> None:
        fakes = _CloseFakes()
        runtime = _runtime(fakes)
        first = await runtime.prepare_close(_command())
        assert first.ready is True
        other = runtime_mod.CloseCommandIdentity(user_scope="user-1", close_command_id="close-2")
        second = await runtime.prepare_close(other)
        assert second.ready is False
        assert second.blocking_reasons == (runtime_mod.CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH,)
        assert second.command.close_command_id == "close-1"  # 返回原关闭命令身份

    async def test_cross_scope_same_command_id_is_not_reused(self) -> None:
        """跨 user_scope 同 command ID 不得复用旧回执（检视 R10：完整身份比较）。"""
        fakes = _CloseFakes()
        runtime = _runtime(fakes)
        first = await runtime.prepare_close(_command())
        assert first.ready is True
        cross_scope = runtime_mod.CloseCommandIdentity(user_scope="user-2", close_command_id="close-1")
        second = await runtime.prepare_close(cross_scope)
        assert second.ready is False
        assert second.blocking_reasons == (runtime_mod.CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH,)
        assert second.command == first.command  # 返回原关闭命令完整身份
        assert fakes.calls.count("handle_close") == 1  # 不重复关闭

    async def test_concurrent_same_identity_shares_single_close_chain(self) -> None:
        """并发同身份调用共享一条关闭链（检视 R10：不重复保存/关闭）。"""
        fakes = _CloseFakes()
        fakes.save_gate = asyncio.Event()
        runtime = _runtime(fakes)
        first_task = asyncio.create_task(runtime.prepare_close(_command()))
        await fakes.save_entered.wait()
        second_task = asyncio.create_task(runtime.prepare_close(_command()))
        await asyncio.sleep(0)
        assert not second_task.done()  # 等待同一条关闭链，不另起一条
        fakes.save_gate.set()
        first = await first_task
        second = await second_task
        assert first.ready is True
        assert second is first
        assert fakes.calls.count("bridge_freeze") == 1
        assert fakes.calls.count("save_facts") == 1
        assert fakes.calls.count("handle_close") == 1

    async def test_concurrent_other_identity_rejected_without_second_chain(self) -> None:
        """关闭链在途时异身份（含跨域同键）不得搭便车（检视 R10）。"""
        fakes = _CloseFakes()
        fakes.save_gate = asyncio.Event()
        runtime = _runtime(fakes)
        first_task = asyncio.create_task(runtime.prepare_close(_command()))
        await fakes.save_entered.wait()

        other = runtime_mod.CloseCommandIdentity(user_scope="user-1", close_command_id="close-2")
        cross_scope = runtime_mod.CloseCommandIdentity(user_scope="user-2", close_command_id="close-1")
        for command in (other, cross_scope):
            blocked = await runtime.prepare_close(command)
            assert blocked.ready is False
            assert blocked.blocking_reasons == (runtime_mod.CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH,)
            assert blocked.command == _command()  # 返回进行中原关闭命令身份

        fakes.save_gate.set()
        first = await first_task
        assert first.ready is True
        assert fakes.calls.count("bridge_freeze") == 1
        assert fakes.calls.count("save_facts") == 1
        assert fakes.calls.count("handle_close") == 1

    async def test_cancelled_error_propagates(self) -> None:
        fakes = _CloseFakes()
        fakes.sink_error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await _runtime(fakes).prepare_close(_command())
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert "handle_close" not in fakes.calls

    async def test_empty_command_identity_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            await _runtime(_CloseFakes()).prepare_close(
                runtime_mod.CloseCommandIdentity(user_scope="", close_command_id="close-1")
            )
        assert "close command identity" in str(exc_info.value)

    async def test_instance_guard_released_only_after_ready(self) -> None:
        class _Guard:
            def __init__(self) -> None:
                self.releases = 0

            def release(self) -> None:
                self.releases += 1

        guard = _Guard()
        fakes = _CloseFakes()
        receipt = await _runtime(fakes, guard).prepare_close(_command())
        assert receipt.ready is True
        assert guard.releases == 1

        blocked_fakes = _CloseFakes()
        blocked_fakes.dispatch_ok = False
        blocked_guard = _Guard()
        blocked = await _runtime(blocked_fakes, blocked_guard).prepare_close(_command())
        assert blocked.ready is False
        assert blocked_guard.releases == 0
