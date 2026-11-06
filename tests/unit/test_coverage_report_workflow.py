"""CI-F03：覆盖率报告必须在集成阶段 append 后重建（契约测试）。

守护目标：
- ``ci-checks`` 的 ``Run Unit Tests`` 阶段以 ``--cov-report=xml`` + ``--cov-report=json``
  写出该阶段报告，并保留 ``--cov-report=term-missing``（触发 fail_under 门禁）。
- ``Run Integration Tests`` 阶段必须保留 ``--cov-append``，并在 append 后以同名
  ``coverage.xml`` / ``coverage.json`` 重新生成最终报告；移除其中任一 xml/json 报告
  即回归为「下游消费单测阶段陈旧报告」。
- 报告重建步骤必须位于单测步骤之后；per-file / diff / Codecov / artifact 等下游
  消费步骤必须位于集成步骤之后，且消费路径保持 ``coverage.xml`` / ``coverage.json``。
- ``pyproject.toml`` 的 ``[tool.coverage.report] fail_under`` 仍为 85，workflow 不得
  以更低的 ``--cov-fail-under`` 覆盖或放宽该阈值，也不得对覆盖率步骤使用
  ``continue-on-error``。

测试只读解析 YAML / TOML，不安装 pytest-cov、不执行 workflow。
"""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

JOB = "ci-checks"
UNIT_STEP = "Run Unit Tests"
INTEGRATION_STEP = "Run Integration Tests"
CODECOV_STEP = "Upload coverage to Codecov"
ARTIFACT_STEP = "Upload test reports"
DOWNSTREAM_STEPS = (
    "Check Per-File Coverage >= 80%",
    "Report Layered Coverage (review07-G14, advisory)",
    "Check diff coverage",
    CODECOV_STEP,
    ARTIFACT_STEP,
)

# 集成阶段允许的报告种类：term-missing 触发 fail_under，xml/json 供下游消费。
# 不允许带路径重定向（如 xml:other.xml），否则同名覆盖约定失效。
EXPECTED_INTEGRATION_REPORT_SPECS = {"term-missing", "xml", "json"}
_FAIL_UNDER_FLOOR = 85


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """加载 CI workflow 的 YAML 结构（仅解析，不执行）。"""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    assert JOB in workflow["jobs"], f"ci_cd.yml 缺少 job: {JOB}"
    return workflow["jobs"][JOB]["steps"]


def _step_by_name(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"{JOB} 应恰有一个 `{name}` 步骤，实际: {len(matches)}"
    return matches[0]


def _step_index(steps: list[dict[str, Any]], name: str) -> int:
    indexes = [index for index, step in enumerate(steps) if step.get("name") == name]
    assert len(indexes) == 1, f"{JOB} 应恰有一个 `{name}` 步骤，实际: {len(indexes)}"
    return indexes[0]


def _tokens(step: dict[str, Any]) -> list[str]:
    """把 run 脚本（含 YAML 块标量中的行继续符 ``\\``）切成命令 token。"""
    run = step.get("run")
    assert isinstance(run, str), f"步骤 `{step.get('name')}` 缺少 run 脚本"
    return run.replace("\\\n", " ").split()


def _has_flag(tokens: list[str], flag: str) -> bool:
    return any(token == flag for token in tokens)


def _flag_values(tokens: list[str], flag: str) -> list[str]:
    prefix = f"{flag}="
    return [token[len(prefix) :] for token in tokens if token.startswith(prefix)]


def _cov_fail_under_values(workflow: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for step in _job_steps(workflow):
        if not isinstance(step.get("run"), str):
            continue
        tokens = _tokens(step)
        for index, token in enumerate(tokens):
            if token.startswith("--cov-fail-under="):
                values.append(float(token.split("=", 1)[1]))
            elif token == "--cov-fail-under" and index + 1 < len(tokens):
                values.append(float(tokens[index + 1]))
    return values


def _assert_unit_reports(tokens: list[str]) -> None:
    specs = _flag_values(tokens, "--cov-report")
    assert "term-missing" in specs, "单测阶段缺少 term-missing，fail_under 门禁不会触发"
    assert "xml" in specs, "单测阶段缺少 xml 报告"
    assert "json" in specs, "单测阶段缺少 json 报告"


def _assert_integration_regenerates_reports(tokens: list[str]) -> None:
    """集成阶段必须在 append 后重建同名 xml/json 最终报告。"""
    assert _has_flag(tokens, "--cov"), "集成阶段缺少 --cov"
    assert _has_flag(tokens, "--cov-append"), "集成阶段必须保留 --cov-append"
    specs = _flag_values(tokens, "--cov-report")
    assert set(specs) == EXPECTED_INTEGRATION_REPORT_SPECS, (
        f"集成阶段必须在 append 后以同名的 term-missing/xml/json 重新生成报告，实际: {specs}"
    )


def test_unit_stage_writes_reports_for_fail_under(workflow: dict[str, Any]) -> None:
    tokens = _tokens(_step_by_name(_job_steps(workflow), UNIT_STEP))
    _assert_unit_reports(tokens)


def test_integration_stage_appends_and_regenerates_same_reports(workflow: dict[str, Any]) -> None:
    tokens = _tokens(_step_by_name(_job_steps(workflow), INTEGRATION_STEP))
    _assert_integration_regenerates_reports(tokens)


def test_contract_catches_removed_integration_xml_or_json(workflow: dict[str, Any]) -> None:
    """变异测试：分别移除集成阶段 xml / json，契约必须失败（防只断言文本的假守护）。"""
    steps = copy.deepcopy(_job_steps(workflow))
    integration = _step_by_name(steps, INTEGRATION_STEP)
    for removed in ("--cov-report=xml", "--cov-report=json"):
        mutated = copy.deepcopy(integration)
        tokens = [token for token in _tokens(mutated) if token != removed]
        mutated["run"] = " ".join(tokens)
        with pytest.raises(AssertionError, match="重新生成报告"):
            _assert_integration_regenerates_reports(_tokens(mutated))


def test_integration_stage_runs_after_unit_stage(workflow: dict[str, Any]) -> None:
    steps = _job_steps(workflow)
    assert _step_index(steps, INTEGRATION_STEP) > _step_index(steps, UNIT_STEP), (
        "集成阶段必须在单测阶段之后运行，才能以 append 后的数据覆盖同名报告"
    )


@pytest.mark.parametrize("step_name", DOWNSTREAM_STEPS)
def test_downstream_consumers_run_after_integration(workflow: dict[str, Any], step_name: str) -> None:
    steps = _job_steps(workflow)
    assert _step_index(steps, step_name) > _step_index(steps, INTEGRATION_STEP), (
        f"`{step_name}` 必须消费集成 append 后重建的最终报告"
    )


def test_codecov_and_artifact_consume_same_final_reports(workflow: dict[str, Any]) -> None:
    steps = _job_steps(workflow)
    codecov_with = _step_by_name(steps, CODECOV_STEP).get("with", {})
    assert codecov_with.get("files") == "coverage.xml", (
        f"Codecov 消费路径应保持 coverage.xml，实际: {codecov_with.get('files')}"
    )
    artifact_path = _step_by_name(steps, ARTIFACT_STEP).get("with", {}).get("path", "")
    assert "coverage.xml" in artifact_path
    assert "coverage.json" in artifact_path


def test_coverage_steps_are_not_soft_failed(workflow: dict[str, Any]) -> None:
    steps = _job_steps(workflow)
    checked = (UNIT_STEP, INTEGRATION_STEP, *DOWNSTREAM_STEPS)
    for name in checked:
        assert _step_by_name(steps, name).get("continue-on-error") is None, (
            f"覆盖率步骤 `{name}` 不得以 continue-on-error 放宽门禁"
        )


def test_pyproject_fail_under_remains_85() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    fail_under = pyproject["tool"]["coverage"]["report"]["fail_under"]
    assert fail_under == _FAIL_UNDER_FLOOR, f"整体覆盖率阈值应为 {_FAIL_UNDER_FLOOR}，实际: {fail_under}"


def test_workflow_does_not_lower_fail_under(workflow: dict[str, Any]) -> None:
    overrides = _cov_fail_under_values(workflow)
    lowered = [value for value in overrides if value < _FAIL_UNDER_FLOOR]
    assert not lowered, f"workflow 不得以低于 {_FAIL_UNDER_FLOOR} 的 --cov-fail-under 放宽阈值: {lowered}"


def test_report_specs_have_no_path_override(workflow: dict[str, Any]) -> None:
    tokens = _tokens(_step_by_name(_job_steps(workflow), INTEGRATION_STEP))
    for spec in _flag_values(tokens, "--cov-report"):
        assert re.fullmatch(r"term-missing|xml|json", spec), (
            f"报告不得重定向到其他路径（下游消费同名 coverage.xml/json），实际: {spec}"
        )
