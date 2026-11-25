"""N01 回归：Agent 专用依赖导入故障时，READY 主壳与业务页仍可打开，研究如实不可用（零派发）。

缺陷现场（修复前）：READY 视图无保护地导入 ``ui.app_layout``，其顶层导入业务研究 VM
再触发 ``services.agent_service`` 包初始化（DBOS / pydantic_ai），依赖缺失时主界面
整体打不开。

本测试在子进程中屏蔽 ``dbos`` / ``pydantic_ai`` 两个 Agent 专用依赖，验证：
1. 启动视图 / 主壳 / 首页 / 设置页 / 业务研究 VM / MCP VM 均可导入（旧首页与业务导航可打开）；
2. 研究入口解析为「未接线」且 ``available=False``（研究不可用，不伪装）；
3. 全程未导入 ``services.agent_service`` 包（零派发面，无 Agent 运行时初始化）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

_CHILD_SCRIPT = r"""
import json
import sys

BLOCKED = ("dbos", "pydantic_ai")


class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked agent dependency: {name}")
        return None


for mod in [m for m in sys.modules if m.split(".")[0] in BLOCKED]:
    del sys.modules[mod]
sys.meta_path.insert(0, _Blocker())

result = {}
for target in (
    "ui.startup_views",
    "ui.app_layout",
    "ui.views.home_view",
    "ui.views.settings_view",
    "ui.viewmodels.business_research_view_model",
    "ui.viewmodels.mcp_services_view_model",
):
    __import__(target)
    result[target] = True

from ui.viewmodels.business_research_view_model import (
    resolve_business_research_service,
    shared_business_research,
)
from ui.viewmodels.research_observer_view_model import (
    resolve_agent_research_service,
    shared_research_observer,
)

research_service, research_reason = resolve_agent_research_service(None)
research_vm = shared_research_observer(None, unavailable_reason_key=research_reason)
business_service, business_reason = resolve_business_research_service(None)
business_vm = shared_business_research(None, unavailable_reason_key=business_reason)

result.update(
    {
        "research_service_is_none": research_service is None,
        "research_reason": research_reason,
        "research_available": research_vm.state.available,
        "research_state_reason": research_vm.state.unavailable_reason_key,
        "business_service_is_none": business_service is None,
        "business_available": business_vm.state.available,
        "business_state_reason": business_vm.state.unavailable_reason_key,
        "agent_package_imported": any(m == "services.agent_service" or m.startswith("services.agent_service.") for m in sys.modules),
    }
)
print("N01_RESULT:" + json.dumps(result))
"""


def _run_child() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, f"子进程导入失败：\nstdout={completed.stdout}\nstderr={completed.stderr}"
    for line in completed.stdout.splitlines():
        if line.startswith("N01_RESULT:"):
            payload = json.loads(line[len("N01_RESULT:") :])
            assert isinstance(payload, dict)
            return payload
    raise AssertionError(f"子进程未输出结果：\nstdout={completed.stdout}\nstderr={completed.stderr}")


@pytest.fixture(scope="module")
def child_result() -> dict[str, object]:
    """子进程只跑一次（屏蔽依赖后完整导入 + 解析研究入口），供两个用例共享。"""
    return _run_child()


@pytest.mark.unit
def test_startup_shell_imports_without_agent_dependencies(child_result: dict[str, object]) -> None:
    """屏蔽 Agent 专用依赖后，启动视图 / 主壳 / 旧首页 / 设置页仍可导入。"""
    payload = child_result
    for target in (
        "ui.startup_views",
        "ui.app_layout",
        "ui.views.home_view",
        "ui.views.settings_view",
        "ui.viewmodels.business_research_view_model",
        "ui.viewmodels.mcp_services_view_model",
    ):
        assert payload.get(target) is True, f"{target} 在 Agent 依赖缺失时导入失败"


@pytest.mark.unit
def test_research_unavailable_and_zero_dispatch_without_agent_dependencies(child_result: dict[str, object]) -> None:
    """Agent 依赖缺失时研究入口如实不可用，且不导入 Agent 运行时（零派发面）。"""
    payload = child_result
    assert payload["research_service_is_none"] is True
    assert payload["research_reason"] == "agent_research_not_wired"
    assert payload["research_available"] is False
    assert payload["research_state_reason"] == "agent_research_not_wired"
    assert payload["business_service_is_none"] is True
    assert payload["business_available"] is False
    assert payload["business_state_reason"] == "agent_research_not_wired"
    assert payload["agent_package_imported"] is False, "Agent 依赖缺失路径不得初始化 services.agent_service（零派发）"
