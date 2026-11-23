"""C-05-4 needs-spike 真进程崩溃宿主脚本（HANDOFF §8.4）。

以独立真实进程模拟「上一实例崩溃」：组装+launch+启动阻塞 workflow（recv 等待），
轮询到 PENDING 后 ``os._exit(1)`` 硬崩溃（无优雅清理），运行库中遗留
``status=PENDING AND executor_id=<本进程 epoch> AND application_version=<真实版本>``
的 workflow 行，供父进程启动隔离实验使用。

工作流副作用：每次执行向 ``AGENT_SPIKE_MARKER`` 追加一行 ``run``——
父进程以行数判定「是否被重新派发执行」。

用法（父测试以子进程方式运行）::

    python -m tests.integration._agent_spike_host   # cwd=worktree

环境变量：AGENT_SPIKE_DB_URL / AGENT_SPIKE_EXECUTOR_ID / AGENT_SPIKE_APP_VERSION /
AGENT_SPIKE_MARKER（含密码 URL 经环境变量传递，不经 argv，避免进程列表泄露）。

本模块被父测试导入以获得同名 workflow 注册（恢复重执行需同代码版本注册）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 允许 `python tests/integration/_agent_spike_host.py` 直接运行（非 -m 模式）：
# 脚本模式 sys.path[0] 为本目录，需补 worktree 根以导入项目模块。
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dbos import DBOS, SetWorkflowID  # noqa: E402  # sys.path 先置


def _marker_path() -> str:
    return os.environ["AGENT_SPIKE_MARKER"]


def _marker_executed() -> bool:
    """工作流函数体已真实执行（已追加执行标记）。

    DBOS 在派发前就以 ``PENDING`` 落行（``_init_workflow``），异步 workflow 还需
    跨线程移交后台事件循环，故「状态为 PENDING」不能证明函数体已运行；父测试
    以 marker 行数为派发证据，宿主必须等到 marker 出现再硬崩溃。
    """
    path = Path(_marker_path())
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


@DBOS.workflow()
async def agent_spike_blocking_wf() -> None:
    """阻塞宿主的工作流：先写执行标记（非 step，恢复重执行会重复追加），再长等待。"""
    with open(_marker_path(), "a", encoding="utf-8") as f:
        f.write("run\n")
    await DBOS.recv_async(topic="agent-spike", timeout_seconds=600)


def main() -> int:
    from services.agent_service.runtime import (
        AgentRuntimeSettings,
        assemble_agent_runtime,
        launch_agent_runtime,
    )

    handle = assemble_agent_runtime(
        AgentRuntimeSettings(
            system_database_url=os.environ["AGENT_SPIKE_DB_URL"],
            allow_shared_migration_credentials=True,  # 子进程宿主单 superuser 实例：显式共用
            application_version=os.environ["AGENT_SPIKE_APP_VERSION"],
            executor_id=os.environ["AGENT_SPIKE_EXECUTOR_ID"],
        )
    )
    launch_agent_runtime(handle)
    with SetWorkflowID("agent-spike-wf-1"):
        wf_handle = DBOS.start_workflow(agent_spike_blocking_wf)
    # 就绪条件：函数体已执行（marker 已写）且仍阻塞在 recv（状态 PENDING），上限 30s
    import time

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if _marker_executed():
            status = wf_handle.get_status()
            if status is not None and status.status == "PENDING":
                break
        time.sleep(0.2)
    else:
        return 2  # 未达就绪（函数体未执行或未阻塞），实验前置失败
    # 真进程硬崩溃：无优雅清理（os._exit 不触发 atexit/DBOS.destroy）
    os._exit(1)


if __name__ == "__main__":
    sys.exit(main())
