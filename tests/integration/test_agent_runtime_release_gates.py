"""I87 发行兼容关键门实证（HANDOFF §8.1 第 2 条 / §8.4 / §10.6，真 PG + 真进程）。

补齐「真实恢复与发行兼容关键门」缺失的证据（隔离环境逐故障边界）：

1. **迁移中途被杀**：子进程完成 DBOS 系统迁移后、Datasource 迁移前硬退出。
   断言已提交的 DBOS 迁移保持、未提交的 Datasource 迁移不存在、零新增
   workflow 行（0 派发）；版本守卫对不一致状态拒绝启动；启动编排在
   中间失败时不 launch；故障消失后可安全重装配（不丢历史、不暗降级）。
2. **旧版本制品只读**：上一发行版本（旧 ``application_version``）崩溃遗留的
   ``PENDING`` 制品，新版本启动隔离/launch 后保持原状态、零重新派发
   （只读零改写），且仍可查回（历史不丢）。

已有真实证据（本文件不重复）：崩溃恢复（``test_agent_runtime_recovery_spike``）、
dispatcher fence（``test_agent_dao_recovery.TestDispatchFence``）、双迁移产物/
最小权限/篡改守卫（``test_agent_runtime_migrator``）。

标记：``pytest.mark.integration`` + ``pytest.mark.embedded_real``（CI 以真实
sidecar PG 串行执行；本地缺 sidecar binary 时由 fixture skip，不计为通过）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from core.agent_types import DispatchCloseFact, DispatchCloseRequest, StartupIsolationRecord, stable_id
from data.persistence import agent_migrator
from data.persistence.agent_migrator import (
    AgentRuntimeMigrationError,
    verify_agent_runtime_versions,
)
from dbos import DBOS
from dbos._datasource_migration import get_datasource_migration_versions
from dbos._migration import get_migration_versions
from services.agent_service.recovery import (
    StartupIsolationReceipt,
    run_startup_isolation,
    start_agent_runtime,
)
from services.agent_service.runtime import (
    RUNTIME_DB_NAME,
    AgentRuntimeSettings,
    InstanceGuardEvidence,
    assemble_agent_runtime,
    launch_agent_runtime,
)
from tests.integration._agent_recovery_host import (
    LEAF_C_ID,
    RESEARCH_A_ID,
    SPIKE_WORKFLOW_NAMES,
)
from tests.integration.test_agent_runtime_migrator import (
    _create_runtime_database,
    _drop_runtime_database,
    _ensure_runtime_role,
    _reset_runtime_schemas,
)
from tests.integration.test_agent_runtime_recovery_spike import (
    _marker_lines,
    _statuses,
    _subprocess_env,
)

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CURRENT_APP_VERSION = "0.11.7"
_OLD_APP_VERSION = "0.11.6"
_SUBPROCESS_TIMEOUT_SEC = 120.0


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def release_gate_db_url(real_embedded_pg) -> AsyncGenerator[str]:
    """独立逻辑库（module 级），逐用例重置两 schema。"""
    info = real_embedded_pg
    admin_url = info.url
    await _create_runtime_database(admin_url)
    await _ensure_runtime_role(admin_url)
    sync_url = (
        sa.make_url(admin_url)
        .set(drivername="postgresql+psycopg", database="qtrading_agent_runtime")
        .render_as_string(hide_password=False)
    )
    yield sync_url
    DBOS.destroy(destroy_registry=True)
    await _drop_runtime_database(admin_url)


def _run_crash_host(db_url: str, epoch: str, marker: Path, app_version: str) -> None:
    """真进程崩溃宿主：退出码 1 = 按设计硬崩溃；2 = 前置失败。"""
    result = subprocess.run(
        [sys.executable, "-m", "tests.integration._agent_recovery_host"],
        env={
            **_subprocess_env(),
            "AGENT_RECOVERY_DB_URL": db_url,
            "AGENT_RECOVERY_EXECUTOR_ID": epoch,
            "AGENT_RECOVERY_APP_VERSION": app_version,
            "AGENT_RECOVERY_MARKER": str(marker),
        },
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SEC,
        check=False,
    )
    assert result.returncode == 1, (
        f"崩溃宿主未按设计退出：code={result.returncode}\nstdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
    )


def _run_migration_fault_host(db_url: str) -> None:
    """真进程在 Datasource 迁移前硬退出（退出码 3 = 按设计被杀）。"""
    result = subprocess.run(
        [sys.executable, "-m", "tests.integration._agent_migration_fault_host"],
        env={
            **_subprocess_env(),
            "AGENT_MIGRATION_FAULT_DB_URL": db_url,
        },
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SEC,
        check=False,
    )
    assert result.returncode == 3, (
        f"迁移故障宿主未按设计退出：code={result.returncode}\nstdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
    )


def _migration_versions(db_url: str) -> tuple[int, int, int, int]:
    """``(dbos_recorded, dbos_latest, datasource_recorded, datasource_latest)``。"""
    engine = sa.create_engine(db_url)
    try:
        dbos_recorded, dbos_latest = get_migration_versions(engine, "dbos", use_listen_notify=True)
        with engine.connect() as conn:
            ds_recorded, ds_latest = get_datasource_migration_versions(conn, "agent_ds")
        return dbos_recorded, dbos_latest, ds_recorded, ds_latest
    finally:
        engine.dispose()


def _workflow_row_count(db_url: str) -> int:
    """原生 workflow 行数（0 = 启动失败未产生任何派发/历史写入）。"""
    engine = sa.create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT count(*) FROM dbos.workflow_status")).fetchone()
            assert row is not None
            return int(row[0])
    finally:
        engine.dispose()


class _EmptyRegistry:
    """空登记面（原生语义实证用）：无已登记执行，记录门闩/保存/回填调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_recovery_executions(self, user_scope: str) -> tuple[Any, ...]:
        self.calls.append("list")
        return ()

    async def mark_dispatch_closed(self, request: DispatchCloseRequest) -> DispatchCloseFact:
        self.calls.append("fence")
        return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=())

    async def save_startup_isolation(self, record: StartupIsolationRecord) -> str:
        self.calls.append("save")
        return stable_id("command", [record.user_scope, record.target_id, record.client_command_id])

    async def finalize_startup_isolation(
        self, *, user_scope: str, isolation_command_id: str, receipt_payload: str
    ) -> None:
        self.calls.append("finalize")

    async def grant_recovery_epoch(self, request: Any) -> Any:
        raise AssertionError("grant_recovery_epoch not expected in startup isolation gate")


class _AlwaysAcquiredGuard:
    """唯一执行锁替身（真实 advisory lock 由 ``test_agent_dao_recovery`` 覆盖）。"""

    def __init__(self) -> None:
        self.released = 0

    async def acquire(self) -> InstanceGuardEvidence:
        return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="fake")

    def release(self) -> None:
        self.released += 1


async def _direct_blocking(fn: Any) -> Any:
    return fn()


def _settings(db_url: str, app_version: str = _CURRENT_APP_VERSION) -> AgentRuntimeSettings:
    return AgentRuntimeSettings(
        system_database_url=db_url,
        allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
        application_version=app_version,
        executor_id=f"epoch-{uuid.uuid4().hex[:8]}",
    )


class _RaisingDatasource:
    """进程内故障注入：DBOS 迁移成功后 Datasource 迁移抛错（中间失败）。"""

    @staticmethod
    def migrate(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("datasource migrate failed mid-way")


class TestMigrationKilledMidway:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_killed_midway_preserves_dbos_artifacts_and_blocks_launch(
        self, release_gate_db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = release_gate_db_url
        await _reset_runtime_schemas(url)

        # 真进程：DBOS 迁移提交后、Datasource 迁移前被杀
        _run_migration_fault_host(url)

        dbos_recorded, dbos_latest, ds_recorded, ds_latest = _migration_versions(url)
        assert (dbos_latest, ds_latest) == (136, 1)
        assert dbos_recorded == 136  # 已提交的系统迁移保持（不丢历史/不暗降级）
        assert ds_recorded == 0  # 未提交的 Datasource 迁移不存在
        assert _workflow_row_count(url) == 0  # 被杀进程未产生任何派发

        # 版本守卫：不一致状态拒绝启动（不得以迁移中间态上线）
        engine = sa.create_engine(url)
        try:
            with pytest.raises(AgentRuntimeMigrationError) as exc_info:
                verify_agent_runtime_versions(engine)
            assert "Datasource" in str(exc_info.value)  # 不一致项点名（不吞成笼统失败）
        finally:
            engine.dispose()

        # 启动编排：中间失败只报 assemble_failed，不隔离、不 launch、不建宿主
        with monkeypatch.context() as patched:
            patched.setattr(agent_migrator, "SQLAlchemyDatasource", _RaisingDatasource)
            outcome = await start_agent_runtime(
                _settings(url),
                user_scope="user-release",
                isolation_id=f"startup-{uuid.uuid4().hex[:8]}",
                registry=_EmptyRegistry(),
                instance_guard=_AlwaysAcquiredGuard(),
                blocking_runner=_direct_blocking,
            )
        assert outcome.handle is None
        assert outcome.isolation is None
        assert outcome.failure == "assemble_failed:RuntimeError"
        assert _workflow_row_count(url) == 0
        assert _migration_versions(url)[0] == 136  # 仍未改写已提交迁移

        # 故障消失后重装配：补齐 Datasource 迁移并精确到达冻结合同（可安全重试）
        handle = assemble_agent_runtime(_settings(url))
        try:
            assert _migration_versions(url) == (136, 136, 1, 1)
        finally:
            handle.close()


class TestOldVersionArtifactsReadOnly:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_old_version_artifacts_stay_pending_and_undispatched(
        self, release_gate_db_url: str, tmp_path: Path
    ) -> None:
        url = release_gate_db_url
        await _reset_runtime_schemas(url)
        marker = tmp_path / "old_version_marker.txt"
        _run_crash_host(url, f"old-epoch-{uuid.uuid4().hex[:8]}", marker, _OLD_APP_VERSION)
        assert _marker_lines(marker) == 2  # root + leaf 各执行一次
        old_ids = (RESEARCH_A_ID, LEAF_C_ID)
        assert _statuses(url, old_ids) == dict.fromkeys(old_ids, "PENDING")

        handle = assemble_agent_runtime(_settings(url))
        try:
            # 启动隔离按当前应用版本过滤：旧版本制品不在快照内（不被取消/改写）
            receipt = await run_startup_isolation(
                user_scope="user-release",
                isolation_id=f"startup-{uuid.uuid4().hex[:8]}",
                target_id=RUNTIME_DB_NAME,
                client=handle.client,
                registry=_EmptyRegistry(),
                application_name=RUNTIME_DB_NAME,
                app_version=_CURRENT_APP_VERSION,
                workflow_names=SPIKE_WORKFLOW_NAMES,
            )
            assert isinstance(receipt, StartupIsolationReceipt)
            assert receipt.ready is True, receipt.blocking_reasons
            assert receipt.isolated_workflow_ids == ()  # 旧版本制品未进入取消集合
            assert _statuses(url, old_ids) == dict.fromkeys(old_ids, "PENDING")  # 只读零改写

            # 新版本 launch 后：旧制品仍为 PENDING，零重新派发（历史可读、未被接管）
            launch_agent_runtime(handle)
            time.sleep(5.0)
            assert _marker_lines(marker) == 2
            assert _statuses(url, old_ids) == dict.fromkeys(old_ids, "PENDING")
        finally:
            handle.close()
        # 关闭后仍可查回：历史不丢
        assert _statuses(url, old_ids) == dict.fromkeys(old_ids, "PENDING")
