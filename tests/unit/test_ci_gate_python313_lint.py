"""D18-Q02：Python 3.13 声明下界必须由聚合门禁 CI Gate 真实强制。

``pyproject.toml`` 的 ``requires-python = ">=3.13"`` 是源码安装下界，但主测试矩阵只跑
3.14。3.13 的守护落在 ``lint-fast-313``（仅 ruff 静态检查，不安装项目依赖）。本测试
只做 workflow YAML 静态断言，守护：

- 该 job 无 ``if`` / step 级 ``continue-on-error``，任何 PR 都会真实执行，不会以 skipped
  状态进入聚合判定；
- ``ci-gate.needs`` 包含 ``lint-fast-313``；
- 聚合门禁把 ``needs.lint-fast-313.result`` 接入 ``LINT_313`` 并以 ``false``（不允许
  skipped）参与 ``check_required`` 判定。

本契约**不**声称 3.13 运行时业务兼容：运行时矩阵与发布产物仍为 3.14。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

LINT_313_JOB = "lint-fast-313"
CI_GATE_JOB = "ci-gate"
GATE_STEP_NAME = "Evaluate required jobs"
LINT_313_CHECK = 'check_required "Fast Lint Check (3.13)" "$LINT_313" false'
LINT_313_ENV = "${{ needs.lint-fast-313.result }}"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """加载 CI workflow 的 YAML 结构（仅解析，不执行）。"""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


def _gate_step(workflow: dict[str, Any]) -> dict[str, Any]:
    steps = workflow["jobs"][CI_GATE_JOB]["steps"]
    matches = [step for step in steps if step.get("name") == GATE_STEP_NAME]
    assert len(matches) == 1, f"{CI_GATE_JOB} 应恰有一个 `{GATE_STEP_NAME}` 步骤，实际: {len(matches)}"
    return matches[0]


def test_lint_fast_313_always_runs_without_skip_conditions(workflow: dict[str, Any]) -> None:
    """3.13 job 不得被条件跳过或把失败降级为成功，否则聚合门禁的 false 判定不可达。"""
    job = workflow["jobs"][LINT_313_JOB]
    assert "if" not in job, "lint-fast-313 不应有 if 条件，否则可能 skipped 进入 CI Gate 判定"
    for step in job.get("steps", []):
        assert step.get("continue-on-error") is None, f"lint-fast-313 的步骤不得降级失败: {step.get('name')}"


def test_ci_gate_needs_lint_fast_313(workflow: dict[str, Any]) -> None:
    """CI Gate 必须等待并采集 3.13 job 的结果。"""
    assert LINT_313_JOB in workflow["jobs"][CI_GATE_JOB]["needs"], "ci-gate.needs 未包含 lint-fast-313"


def test_ci_gate_wires_lint_fast_313_result(workflow: dict[str, Any]) -> None:
    """聚合判定步骤的 env 必须真实引用 3.13 job 结果。"""
    env = _gate_step(workflow).get("env", {})
    assert env.get("LINT_313") == LINT_313_ENV, f"LINT_313 未接入 needs.lint-fast-313.result: {env.get('LINT_313')}"


def test_ci_gate_requires_lint_fast_313_success(workflow: dict[str, Any]) -> None:
    """3.13 job 不得以 skipped 放行（与 3.14 基线一致）。"""
    run = _gate_step(workflow)["run"]
    assert run.count(LINT_313_CHECK) == 1, f"缺少且仅应有一条 3.13 required 判定: {LINT_313_CHECK}"
