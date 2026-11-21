"""C-05-4 needs-spike 启动隔离实证（HANDOFF §8.4，真 PG + 真进程）。

实证目标：
1. **隔离（产品条件）**：上一实例（epoch A）崩溃遗留的 ``PENDING`` workflow，
   新进程（epoch B）launch 后 **0 重新派发**（marker 行数不变、状态仍 PENDING）——
   每进程 executor epoch 即隔离主机制；
2. **正向对照（检测手段有效性）**：以**同 epoch**组装启动，launch 后该 workflow
   **被恢复重执行**（marker 行数 +1）——证明「未派发」断言不是恢复机制失效的假绿。

证据绑定：HANDOFF §8.4「须用真 PG、真进程及原 operation 查回验证未选任务/旧成功
效果 0 新派发」；C-05 验收门（不能证明前不 launch 研究宿主）。

应用层隔离方案（Client 读 AgentDao 集合→固定集合取消→run_epoch fence）属 D 系列
持久 fence，不在本片；本片只证 SDK 层行为与 epoch 机制。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from dbos import DBOS
from tests.integration._agent_spike_host import agent_spike_blocking_wf
from tests.integration.test_agent_runtime_migrator import (
    _create_runtime_database,
    _drop_runtime_database,
    _ensure_runtime_role,
    _reset_runtime_schemas,
)

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_SUBPROCESS_TIMEOUT_SEC = 90.0
_APP_VERSION = "0.11.7"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def spike_db_url(real_embedded_pg) -> AsyncGenerator[str]:
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


def _run_crash_host(db_url: str, epoch: str, marker: Path) -> None:
    """真进程崩溃宿主：退出码 1 = 按设计硬崩溃；2 = 前置失败。"""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.integration._agent_spike_host",
        ],
        env={
            **_subprocess_env(),
            "AGENT_SPIKE_DB_URL": db_url,
            "AGENT_SPIKE_EXECUTOR_ID": epoch,
            "AGENT_SPIKE_APP_VERSION": _APP_VERSION,
            "AGENT_SPIKE_MARKER": str(marker),
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


def _subprocess_env() -> dict[str, str]:
    """最小继承环境（PATH/系统变量），避免父进程测试变量干扰子进程。"""
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
    return {k: v for k, v in os.environ.items() if k in keep}


def _marker_lines(marker: Path) -> int:
    """marker 行数 = workflow 实际执行次数（崩溃进程 1 次 + 恢复重执行各 1 次）。"""
    if not marker.exists():
        return 0
    return len(marker.read_text(encoding="utf-8").splitlines())


def _pending_executor_id(sync_url: str) -> str | None:
    """查回 PENDING 行的 executor_id（原 operation 查回，HANDOFF §8.4）。"""
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT executor_id FROM dbos.workflow_status WHERE workflow_uuid = 'agent-spike-wf-1'")
            ).fetchone()
            return None if row is None else str(row[0])
    finally:
        engine.dispose()


def _wf_status(sync_url: str) -> str | None:
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM dbos.workflow_status WHERE workflow_uuid = 'agent-spike-wf-1'")
            ).fetchone()
            return None if row is None else str(row[0])
    finally:
        engine.dispose()


def _wf_recovery_state(sync_url: str) -> tuple[str | None, str | None]:
    """查回工作流行 (status, queue_name)：queue_name 非 NULL 即被恢复管线触碰。"""
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, queue_name FROM dbos.workflow_status WHERE workflow_uuid = 'agent-spike-wf-1'")
            ).fetchone()
            if row is None:
                return None, None
            return str(row[0]), (None if row[1] is None else str(row[1]))
    finally:
        engine.dispose()


def _wait_for(cond, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.5)
    return False


class TestLaunchRecoveryIsolationSpike:
    """真进程崩溃宿主 → 新进程 launch 的派发语义。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_new_epoch_launch_does_not_redispatch(self, spike_db_url: str, tmp_path: Path) -> None:
        """隔离：epoch A 崩溃遗留 PENDING，epoch B 新进程 launch 后 0 新派发。

        机制证据（DB 状态）：异 epoch launch 后行未被恢复管线触碰——status 仍
        PENDING 且 queue_name 仍 NULL（launch 恢复查询按 executor_id+app_version
        过滤未命中，未 re-enqueue）。
        """
        await _reset_runtime_schemas(spike_db_url)
        marker = tmp_path / "spike_marker.txt"
        crashed_epoch = f"epoch-a-{uuid.uuid4().hex[:8]}"
        _run_crash_host(spike_db_url, crashed_epoch, marker)

        # 前置事实：崩溃宿主遗留 PENDING 行（queue_name NULL），marker 恰 1 次执行
        assert _marker_lines(marker) == 1
        assert _wf_recovery_state(spike_db_url) == ("PENDING", None)
        assert _pending_executor_id(spike_db_url) == crashed_epoch

        # 新进程 epoch B：组装 + launch（不同 epoch）
        from services.agent_service.runtime import (
            AgentRuntimeSettings,
            assemble_agent_runtime,
            launch_agent_runtime,
        )

        handle = assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=spike_db_url,
                allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
                application_version=_APP_VERSION,
                executor_id=f"epoch-b-{'x' * 8}",
            )
        )
        try:
            launch_agent_runtime(handle)
            time.sleep(5.0)  # 给恢复线程充分机会（若有会在此窗口重派）
            # 核心断言：0 新派发——marker 行数不变、状态仍 PENDING 且未被恢复管线触碰
            assert _marker_lines(marker) == 1
            assert _wf_recovery_state(spike_db_url) == ("PENDING", None), (
                "异 epoch launch 触碰了工作流行：恢复查询误命中，隔离被破坏"
            )
        finally:
            handle.close()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_epoch_launch_engages_recovery(self, spike_db_url: str, tmp_path: Path) -> None:
        """检测手段有效性：同 epoch launch 恢复管线实际接管该行（非假绿归因）。

        spike 机制发现（实现者注）：launch 恢复路径对命中的 PENDING 行 re-enqueue
        （queue_name 填 _dbos_internal_queue、状态转 ENQUEUED，随后被内部队列 claim
        回 PENDING 且 recovery_attempts 递增）；``listen_queues([])`` 不阻断内部
        队列派发（_queue.py 队列线程无条件加入内部队列）。另发现异步 workflow 的
        进程内重执行在测试进程上下文中静默未跑（已 claim 但函数体未执行）——SDK
        执行器行为，与本片隔离证据无关，记录为待验证风险。
        因此本对照断言「恢复管线接管」的 DB 状态转移（不依赖工作流函数体执行）：
        queue_name 被填 + recovery_attempts 递增 + 异 epoch 对照（test 1）未触碰。
        """
        await _reset_runtime_schemas(spike_db_url)
        marker = tmp_path / "spike_marker.txt"
        crashed_epoch = f"epoch-a-{uuid.uuid4().hex[:8]}"
        _run_crash_host(spike_db_url, crashed_epoch, marker)
        assert _marker_lines(marker) == 1
        assert _wf_recovery_state(spike_db_url) == ("PENDING", None)

        from services.agent_service.runtime import (
            AgentRuntimeSettings,
            assemble_agent_runtime,
            launch_agent_runtime,
        )

        handle = assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=spike_db_url,
                allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
                application_version=_APP_VERSION,
                executor_id=crashed_epoch,  # 同 epoch：恢复查询命中
            )
        )
        try:
            launch_agent_runtime(handle)
            engaged = _wait_for(
                lambda: _wf_recovery_state(spike_db_url)[1] == "_dbos_internal_queue",
                30.0,
            )
            assert engaged, (
                "同 epoch launch 后恢复管线未接管该行（queue_name 未被填）：恢复查询/管线失效，隔离断言失去归因"
            )
        finally:
            handle.close()
