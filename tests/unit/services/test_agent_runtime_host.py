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

import asyncio
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
        self.dbos_config: dict[str, Any] | None = None
        self.datasource_kwargs: dict[str, Any] = {}
        self.client_kwargs: dict[str, Any] = {}
        self.destroyed = False

    def fake_run_migrations(self, url: str) -> None:
        self.calls.append("migrations")

    def fake_create_engine(self, url: str, **kwargs: Any) -> object:
        self.calls.append(f"engine:{kwargs.get('pool_size')}")
        pool_size = kwargs.get("pool_size")
        assert isinstance(pool_size, int)
        self.engines[pool_size] = kwargs
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
        # 会话超时（检视 F1：注入引擎后 SDK 不兜底，HANDOFF §8.1 有限超时）
        runtime_options = runtime_kwargs["connect_args"]["options"]
        assert "statement_timeout=30000" in runtime_options
        assert "idle_in_transaction_session_timeout=60000" in runtime_options
        control_kwargs = host.engines[2]
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
