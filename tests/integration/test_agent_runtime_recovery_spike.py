"""D-03 启动隔离/手动恢复真实进程实证（HANDOFF §8.4，真 PG + 真进程）。

实证目标：

1. **完整集合隔离**：崩溃宿主遗留的 PENDING（root/leaf）+ 外部 enqueue 的
   ENQUEUED（内部队列）与 DELAYED 行，在固定快照后被**全部取消**；launch 后
   **0 新派发**（marker 行数不变、状态保持 CANCELLED）。
2. **只恢复被选集合**：恢复 A 后 A 离开 CANCELLED（ENQUEUED/PENDING），未选
   B、C 保持 CANCELLED（A 不动 B）。
3. **双迁移失败 0 自动派发**：组装失败时启动编排不隔离、不 launch、不触碰
   原生行（PENDING 原样保留，marker 不变）。

应用登记面（启动隔离命令落库/fence）由 ``test_agent_dao_recovery.py`` 在真实
业务库 schema 上验证；本片聚焦原生控制面与启动编排接线。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from dbos import DBOS, EnqueueOptions
from services.agent_service.recovery import (
    StartupIsolationReceipt,
    run_startup_isolation,
    start_agent_runtime,
)
from services.agent_service.runtime import (
    RUNTIME_DB_NAME,
    AgentRuntimeSettings,
    assemble_agent_runtime,
    launch_agent_runtime,
)
from tests.integration._agent_recovery_host import (  # 注册同名 workflow（自有命名，避开生产发布名）
    LEAF_C_ID,
    LEAF_WORKFLOW_NAME,
    RESEARCH_A_ID,
    RESEARCH_WORKFLOW_NAME,
    SPIKE_WORKFLOW_NAMES,
)
from tests.integration.test_agent_runtime_migrator import (
    _create_runtime_database,
    _drop_runtime_database,
    _ensure_runtime_role,
    _reset_runtime_schemas,
)

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_SUBPROCESS_TIMEOUT_SEC = 120.0
_APP_VERSION = "0.11.7"
_QUEUED_ID = "agent_research:recovery-queued"
_DELAYED_ID = "agent_leaf:recovery-delayed"
_ALL_IDS = (RESEARCH_A_ID, LEAF_C_ID, _QUEUED_ID, _DELAYED_ID)


@dataclass
class _FakeRegistry:
    """最小登记面（原生语义实证用）：登记本业务库的 root/队列/延迟行，记录门闩/回填。

    启动隔离按登记归属解析取消集（N06）：未登记且父级不可证的原生行不清理并阻塞，
    故本片把原生直接产生的行按当前业务库登记，验证「已归属集合被完整取消」。
    """

    calls: list[str] = field(default_factory=list)
    fence_workflow_ids: tuple[str, ...] = ()

    async def list_recovery_executions(self, user_scope: str) -> tuple[Any, ...]:
        from core.agent_types import RecoveryExecution

        self.calls.append("list")
        return tuple(
            RecoveryExecution(
                execution_id=f"exec-{index}",
                session_id="session-1",
                execution_kind="root",
                display_state="running",
                owner_epoch=1,
                dispatch_closed=False,
                ended_at=None,
                round_id=f"round-{index}",
                round_number=1,
                workflow_id=workflow_id,
                run_epoch=1,
                stop_reason=None,
            )
            for index, workflow_id in enumerate((RESEARCH_A_ID, LEAF_C_ID, _QUEUED_ID, _DELAYED_ID))
        )

    async def mark_dispatch_closed(self, request: Any) -> Any:
        from core.agent_types import DispatchCloseFact

        self.calls.append("fence")
        self.fence_workflow_ids = tuple(request.workflow_ids)
        return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=("exec-a",))

    async def save_startup_isolation(self, record: Any) -> str:
        from core.agent_types import stable_id

        self.calls.append("save")
        return stable_id("command", [record.user_scope, record.target_id, record.client_command_id])

    async def finalize_startup_isolation(
        self, *, user_scope: str, isolation_command_id: str, receipt_payload: str
    ) -> None:
        self.calls.append("finalize")

    async def grant_recovery_epoch(self, request: Any) -> Any:  # pragma: no cover - 本片不授予
        raise AssertionError("grant_recovery_epoch not expected in native spike")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def recovery_db_url(real_embedded_pg) -> AsyncGenerator[str]:
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


def _subprocess_env() -> dict[str, str]:
    keep = (
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "PATH",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    )
    return {key: value for key, value in os.environ.items() if key in keep}


def _run_crash_host(db_url: str, epoch: str, marker: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.integration._agent_recovery_host"],
        env={
            **_subprocess_env(),
            "AGENT_RECOVERY_DB_URL": db_url,
            "AGENT_RECOVERY_EXECUTOR_ID": epoch,
            "AGENT_RECOVERY_APP_VERSION": _APP_VERSION,
            "AGENT_RECOVERY_MARKER": str(marker),
        },
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SEC,
        check=False,
    )
    assert result.returncode == 1, (
        f"崩溃宿主未按设计退出：code={result.returncode}\nstdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
    )


def _marker_lines(marker: Path) -> int:
    if not marker.exists():
        return 0
    return len(marker.read_text(encoding="utf-8").splitlines())


def _statuses(db_url: str, workflow_ids: tuple[str, ...]) -> dict[str, str]:
    engine = sa.create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT workflow_uuid, status FROM dbos.workflow_status WHERE workflow_uuid = ANY(:ids)"),
                {"ids": list(workflow_ids)},
            ).fetchall()
            return {str(row[0]): str(row[1]) for row in rows}
    finally:
        engine.dispose()


def _enqueue_categories(handle: Any) -> None:
    """构造 ENQUEUED（内部队列）与 DELAYED（延迟）在途行。"""

    handle.client.enqueue(
        EnqueueOptions(
            workflow_name=RESEARCH_WORKFLOW_NAME,
            queue_name="recovery-spike-q",
            workflow_id=_QUEUED_ID,
            app_version=_APP_VERSION,
            application_name=RUNTIME_DB_NAME,
        )
    )
    handle.client.enqueue(
        EnqueueOptions(
            workflow_name=LEAF_WORKFLOW_NAME,
            queue_name="recovery-spike-q",
            workflow_id=_DELAYED_ID,
            delay_seconds=3600,
            app_version=_APP_VERSION,
            application_name=RUNTIME_DB_NAME,
        )
    )


class TestStartupIsolationSpike:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_full_set_cancelled_and_zero_dispatch_after_launch(
        self, recovery_db_url: str, tmp_path: Path
    ) -> None:
        await _reset_runtime_schemas(recovery_db_url)
        marker = tmp_path / "recovery_marker.txt"
        _run_crash_host(recovery_db_url, f"epoch-a-{uuid.uuid4().hex[:8]}", marker)
        assert _marker_lines(marker) == 2  # root A + leaf C 各执行一次
        assert _statuses(recovery_db_url, (RESEARCH_A_ID, LEAF_C_ID)) == {
            RESEARCH_A_ID: "PENDING",
            LEAF_C_ID: "PENDING",
        }

        handle = assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=recovery_db_url,
                allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
                application_version=_APP_VERSION,
                executor_id=f"epoch-b-{uuid.uuid4().hex[:8]}",
            )
        )
        try:
            _enqueue_categories(handle)
            before = _statuses(recovery_db_url, _ALL_IDS)
            assert before == {
                RESEARCH_A_ID: "PENDING",
                LEAF_C_ID: "PENDING",
                _QUEUED_ID: "ENQUEUED",
                _DELAYED_ID: "DELAYED",
            }

            registry = _FakeRegistry()
            receipt = await run_startup_isolation(
                user_scope="user-1",
                isolation_id="startup-spike-1",
                target_id=RUNTIME_DB_NAME,
                client=handle.client,
                registry=registry,
                application_name=RUNTIME_DB_NAME,
                app_version=_APP_VERSION,
                workflow_names=SPIKE_WORKFLOW_NAMES,
            )

            assert isinstance(receipt, StartupIsolationReceipt)
            assert receipt.ready is True, receipt.blocking_reasons
            assert set(receipt.isolated_workflow_ids) == set(_ALL_IDS)
            original = dict(receipt.original_states)
            assert original[_QUEUED_ID] == "ENQUEUED"
            assert original[_DELAYED_ID] == "DELAYED"
            assert registry.calls[:3] == ["list", "save", "fence"]
            assert set(registry.fence_workflow_ids) == {RESEARCH_A_ID, LEAF_C_ID, _QUEUED_ID, _DELAYED_ID}
            assert _statuses(recovery_db_url, _ALL_IDS) == dict.fromkeys(_ALL_IDS, "CANCELLED")

            # 隔离通过后才 launch：既有在途行不得被恢复管线重派（marker 不变）
            launch_agent_runtime(handle)
            time.sleep(5.0)
            assert _marker_lines(marker) == 2
            assert _statuses(recovery_db_url, _ALL_IDS) == dict.fromkeys(_ALL_IDS, "CANCELLED")
        finally:
            handle.close()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_resume_only_selected_workflow(self, recovery_db_url: str, tmp_path: Path) -> None:
        await _reset_runtime_schemas(recovery_db_url)
        marker = tmp_path / "recovery_marker.txt"
        _run_crash_host(recovery_db_url, f"epoch-a-{uuid.uuid4().hex[:8]}", marker)
        handle = assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=recovery_db_url,
                allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
                application_version=_APP_VERSION,
                executor_id=f"epoch-b-{uuid.uuid4().hex[:8]}",
            )
        )
        try:
            _enqueue_categories(handle)
            receipt = await run_startup_isolation(
                user_scope="user-1",
                isolation_id="startup-spike-2",
                target_id=RUNTIME_DB_NAME,
                client=handle.client,
                registry=_FakeRegistry(),
                application_name=RUNTIME_DB_NAME,
                app_version=_APP_VERSION,
                workflow_names=SPIKE_WORKFLOW_NAMES,
            )
            assert receipt.ready is True
            launch_agent_runtime(handle)

            await handle.client.resume_workflows_async([RESEARCH_A_ID])

            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                resumed = _statuses(recovery_db_url, (RESEARCH_A_ID,))[RESEARCH_A_ID]
                if resumed != "CANCELLED":
                    break
                time.sleep(0.2)
            statuses = _statuses(recovery_db_url, _ALL_IDS)
            assert statuses[RESEARCH_A_ID] in {"ENQUEUED", "PENDING"}, statuses
            # A 不动 B/C：未选集合保持 CANCELLED
            assert statuses[LEAF_C_ID] == "CANCELLED"
            assert statuses[_QUEUED_ID] == "CANCELLED"
            assert statuses[_DELAYED_ID] == "CANCELLED"
        finally:
            handle.close()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_assembly_failure_dispatches_nothing(self, recovery_db_url: str, tmp_path: Path) -> None:
        """双迁移/版本守卫失败：启动编排不隔离、不 launch、原生行原样（0 派发）。"""
        await _reset_runtime_schemas(recovery_db_url)
        marker = tmp_path / "recovery_marker.txt"
        _run_crash_host(recovery_db_url, f"epoch-a-{uuid.uuid4().hex[:8]}", marker)

        from data.persistence.agent_migrator import run_agent_runtime_migrations

        run_agent_runtime_migrations(recovery_db_url)
        engine = sa.create_engine(recovery_db_url)
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE agent_ds.dbos_datasource_migrations SET version = :v"), {"v": 2})
        finally:
            engine.dispose()

        outcome = await start_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=recovery_db_url,
                allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
                application_version=_APP_VERSION,
                executor_id=f"epoch-b-{uuid.uuid4().hex[:8]}",
            ),
            user_scope="user-1",
            isolation_id="startup-spike-3",
            registry=_FakeRegistry(),
        )
        assert outcome.handle is None
        assert outcome.isolation is None
        assert outcome.failure is not None and "AgentRuntimeMigrationError" in outcome.failure
        assert _marker_lines(marker) == 2
        assert _statuses(recovery_db_url, (RESEARCH_A_ID, LEAF_C_ID)) == {
            RESEARCH_A_ID: "PENDING",
            LEAF_C_ID: "PENDING",
        }
        DBOS.destroy(destroy_registry=True)
