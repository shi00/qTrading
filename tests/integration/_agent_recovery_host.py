"""D-03 真进程恢复 spike 宿主（HANDOFF §8.4 第 2-3 条）。

以独立真实进程模拟「上一实例崩溃」：组装 + launch + 启动两个**自有命名**
workflow（``recovery_spike_research`` / ``recovery_spike_leaf``，阻塞在
``recv``），待均进入 ``PENDING`` 后 ``os._exit(1)`` 硬崩溃，运行库遗留可被
启动隔离列举的 PENDING 行。

每次执行（含恢复重执行）向 ``AGENT_RECOVERY_MARKER`` 追加一行本 workflow ID：
父测试以该文件判定「是否被重新派发/重执行」。

用法（父测试以子进程方式运行）::

    python -m tests.integration._agent_recovery_host   # cwd=worktree

环境变量：AGENT_RECOVERY_DB_URL / AGENT_RECOVERY_EXECUTOR_ID /
AGENT_RECOVERY_APP_VERSION / AGENT_RECOVERY_MARKER（含密码 URL 只经环境变量）。

本模块也被父测试导入以获得同名 workflow 注册（恢复重执行需同代码版本注册）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dbos import DBOS, SetWorkflowID, WorkflowSerializationFormat  # noqa: E402  # sys.path 先置

RESEARCH_A_ID = "agent_research:recovery-a"
LEAF_C_ID = "agent_leaf:recovery-c"

#: 本宿主自有 workflow 名：不得复用 D-02 发布名（``agent_research``/``agent_leaf``），
#: 否则父测试同进程导入生产 workflow 与本模块时 DBOS 拒重复注册（同名不同函数）。
RESEARCH_WORKFLOW_NAME = "recovery_spike_research"
LEAF_WORKFLOW_NAME = "recovery_spike_leaf"
SPIKE_WORKFLOW_NAMES = (RESEARCH_WORKFLOW_NAME, LEAF_WORKFLOW_NAME)


def _record() -> None:
    """记录一次实际执行（workflow 体内普通写文件，重执行会再次追加）。

    父测试进程恢复重执行时无 marker 环境变量：不写标记，直接返回（不因缺环境变量失败）。
    """

    marker = os.environ.get("AGENT_RECOVERY_MARKER")
    if marker is None:
        return
    workflow_id = DBOS.workflow_id or "unknown"
    with open(marker, "a", encoding="utf-8") as handle:
        handle.write(f"{workflow_id}\n")


@DBOS.workflow(name=RESEARCH_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def recovery_host_research() -> None:
    _record()
    await DBOS.recv_async(topic="recovery-spike", timeout_seconds=600)


@DBOS.workflow(name=LEAF_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def recovery_host_leaf() -> None:
    _record()
    await DBOS.recv_async(topic="recovery-spike", timeout_seconds=600)


def main() -> int:
    from services.agent_service.runtime import (
        AgentRuntimeSettings,
        assemble_agent_runtime,
        launch_agent_runtime,
    )

    handle = assemble_agent_runtime(
        AgentRuntimeSettings(
            system_database_url=os.environ["AGENT_RECOVERY_DB_URL"],
            application_version=os.environ["AGENT_RECOVERY_APP_VERSION"],
            executor_id=os.environ["AGENT_RECOVERY_EXECUTOR_ID"],
        )
    )
    launch_agent_runtime(handle)
    handles = []
    for workflow_id, function in ((RESEARCH_A_ID, recovery_host_research), (LEAF_C_ID, recovery_host_leaf)):
        with SetWorkflowID(workflow_id):
            handles.append(DBOS.start_workflow(function))

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        statuses = [item.get_status() for item in handles]
        if all(status is not None and status.status == "PENDING" for status in statuses):
            break
        time.sleep(0.2)
    else:
        return 2  # 未达 PENDING，实验前置失败
    os._exit(1)


if __name__ == "__main__":
    sys.exit(main())
