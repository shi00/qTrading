"""CI-F03：Linux 测试拆分后的覆盖率采集/合并契约。

守护目标：
- 单测 / 集成 / embedded 三个采集 job 只产覆盖率片段（``COVERAGE_FILE=cov/*.data`` 且
  ``--cov-report=``），不得在采集阶段产出 ``coverage.xml`` / ``coverage.json``，避免下游
  或人工消费到局部报告。
- ``ci-coverage`` 必须按 ``coverage combine`` → ``coverage xml`` → ``coverage json`` →
  ``coverage report`` 的顺序产出唯一最终报告；``fail_under`` 只在合并后的数据上判定。
- per-file / 分层 / diff / Codecov / artifact 等下游步骤必须位于 combine 之后，且消费
  同名 ``coverage.xml`` / ``coverage.json``。
- ``pyproject.toml`` 的 ``[tool.coverage.report] fail_under`` 仍为 85，workflow 不得以更低
  的 ``--cov-fail-under`` 放宽，也不得对覆盖率步骤使用 ``continue-on-error``。
- 四个 Linux 测试 job（unit / integration / embedded / coverage）的跳过条件必须完全一致，
  防 fork / dependabot 策略在多处复制后漂移。

测试只读解析 YAML / TOML，不安装 pytest-cov、不执行 workflow。
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

GATE_JOB = "ci-coverage"
GATE_COMBINE_STEP = "Combine coverage and generate reports"
GATE_DOWNSTREAM_STEPS = (
    "Check Per-File Coverage >= 80%",
    "Report Layered Coverage (review07-G14, advisory)",
    "Check diff coverage",
    "Upload coverage to Codecov",
    "Upload coverage reports",
)

# 采集 job → (测试步骤名, 片段名)
COLLECTORS = {
    "ci-checks": ("Run Unit Tests", "unit"),
    "ci-checks-integration": ("Run Integration Tests", "integration"),
    "ci-checks-embedded": ("Run agent embedded integration tests (serial, sidecar)", "embedded"),
}
LINUX_JOBS = (*COLLECTORS, GATE_JOB)

_FAIL_UNDER_FLOOR = 85


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    assert job in workflow["jobs"], f"ci_cd.yml 缺少 job: {job}"
    return workflow["jobs"][job]["steps"]


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"应恰有一个 `{name}` 步骤，实际: {len(matches)}"
    return matches[0]


def _step_index(steps: list[dict[str, Any]], name: str) -> int:
    indexes = [index for index, step in enumerate(steps) if step.get("name") == name]
    assert len(indexes) == 1, f"应恰有一个 `{name}` 步骤，实际: {len(indexes)}"
    return indexes[0]


def _tokens(step: dict[str, Any]) -> list[str]:
    run = step.get("run")
    assert isinstance(run, str), f"步骤 `{step.get('name')}` 缺少 run 脚本"
    return run.replace("\\\n", " ").split()


def _has_flag(tokens: list[str], flag: str) -> bool:
    return any(token == flag for token in tokens)


def _flag_values(tokens: list[str], flag: str) -> list[str]:
    prefix = f"{flag}="
    return [token[len(prefix) :] for token in tokens if token.startswith(prefix)]


def _assert_collector(job: str, steps: list[dict[str, Any]], step_name: str, fragment: str) -> None:
    step = _step(steps, step_name)
    tokens = _tokens(step)
    assert _has_flag(tokens, "--cov"), f"{job} 采集阶段缺少 --cov"
    assert not _has_flag(tokens, "--cov-append"), f"{job} 拆分后不得再使用 --cov-append"
    assert _flag_values(tokens, "--cov-report") == [""], (
        f"{job} 采集阶段必须且只能使用 `--cov-report=`（禁止产出局部 coverage.xml/json 或触发分片 fail_under），"
        f"实际: {_flag_values(tokens, '--cov-report')}"
    )
    env = step.get("env", {})
    assert env.get("COVERAGE_FILE") == f"cov/{fragment}.data", (
        f"{job} 必须使用独立覆盖率片段文件 cov/{fragment}.data，实际: {env.get('COVERAGE_FILE')}"
    )

    upload = _step(steps, "Upload test reports")
    upload_path = str(upload.get("with", {}).get("path", ""))
    assert "coverage.xml" not in upload_path and "coverage.json" not in upload_path, (
        f"{job} 的测试工件不得包含局部覆盖率报告（应由 ci-coverage 统一产出），实际: {upload_path}"
    )

    fragment_upload = _step(steps, "Upload coverage fragment")
    with_ = fragment_upload.get("with", {})
    assert with_.get("path") == f"cov/{fragment}.data", (
        f"{job} 覆盖率片段上传路径应与 COVERAGE_FILE 一致，实际: {with_.get('path')}"
    )
    assert with_.get("if-no-files-found") == "error", f"{job} 片段上传须在缺失时失败（避免静默丢数据）"


def _assert_combine_order(tokens: list[str]) -> None:
    joined = " ".join(tokens)
    markers = ("coverage combine fragments", "coverage xml", "coverage json", "coverage report")
    indexes = [joined.find(marker) for marker in markers]
    assert all(index >= 0 for index in indexes), f"缺少 combine/报告命令: {dict(zip(markers, indexes, strict=True))}"
    assert indexes == sorted(indexes), (
        f"combine → xml → json → report 顺序错误: {dict(zip(markers, indexes, strict=True))}"
    )
    assert "--cov-fail-under" not in joined, "阈值必须由 pyproject fail_under 提供，不得在 workflow 覆盖"


def _cov_fail_under_values(workflow: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if not isinstance(step.get("run"), str):
                continue
            tokens = _tokens(step)
            for index, token in enumerate(tokens):
                if token.startswith("--cov-fail-under="):
                    values.append(float(token.split("=", 1)[1]))
                elif token == "--cov-fail-under" and index + 1 < len(tokens):
                    values.append(float(tokens[index + 1]))
    return values


@pytest.mark.parametrize("job", sorted(COLLECTORS))
def test_collectors_emit_fragments_only(workflow: dict[str, Any], job: str) -> None:
    step_name, fragment = COLLECTORS[job]
    _assert_collector(job, _steps(workflow, job), step_name, fragment)


def test_gate_combines_then_reports(workflow: dict[str, Any]) -> None:
    tokens = _tokens(_step(_steps(workflow, GATE_JOB), GATE_COMBINE_STEP))
    _assert_combine_order(tokens)


def test_gate_combine_step_precedes_all_downstream_consumers(workflow: dict[str, Any]) -> None:
    steps = _steps(workflow, GATE_JOB)
    combine_index = _step_index(steps, GATE_COMBINE_STEP)
    for name in GATE_DOWNSTREAM_STEPS:
        assert _step_index(steps, name) > combine_index, f"`{name}` 必须消费 combine 后的最终报告"


def test_codecov_and_artifact_consume_final_reports(workflow: dict[str, Any]) -> None:
    steps = _steps(workflow, GATE_JOB)
    codecov_with = _step(steps, "Upload coverage to Codecov").get("with", {})
    assert codecov_with.get("files") == "coverage.xml", (
        f"Codecov 消费路径应保持 coverage.xml，实际: {codecov_with.get('files')}"
    )
    artifact_path = _step(steps, "Upload coverage reports").get("with", {}).get("path", "")
    assert "coverage.xml" in artifact_path
    assert "coverage.json" in artifact_path


def test_coverage_steps_are_not_soft_failed(workflow: dict[str, Any]) -> None:
    checked = [(job, step_name) for job, (step_name, _) in COLLECTORS.items()]
    checked.append((GATE_JOB, GATE_COMBINE_STEP))
    checked.extend((GATE_JOB, name) for name in GATE_DOWNSTREAM_STEPS)
    for job, name in checked:
        step = _step(_steps(workflow, job), name)
        assert step.get("continue-on-error") is None, f"覆盖率步骤 `{job}.{name}` 不得以 continue-on-error 放宽门禁"


def test_pyproject_fail_under_remains_85() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    fail_under = pyproject["tool"]["coverage"]["report"]["fail_under"]
    assert fail_under == _FAIL_UNDER_FLOOR, f"整体覆盖率阈值应为 {_FAIL_UNDER_FLOOR}，实际: {fail_under}"


def test_workflow_does_not_lower_fail_under(workflow: dict[str, Any]) -> None:
    lowered = [value for value in _cov_fail_under_values(workflow) if value < _FAIL_UNDER_FLOOR]
    assert not lowered, f"workflow 不得以低于 {_FAIL_UNDER_FLOOR} 的 --cov-fail-under 放宽阈值: {lowered}"


def test_linux_test_jobs_share_identical_skip_condition(workflow: dict[str, Any]) -> None:
    conditions = {job: workflow["jobs"][job].get("if") for job in LINUX_JOBS}
    assert all(conditions.values()), f"Linux 测试 job 必须显式声明跳过条件: {conditions}"
    assert len(set(conditions.values())) == 1, f"四个 Linux 测试 job 的跳过条件必须完全一致（防策略漂移）: {conditions}"


# ---- 变异测试：断言上述契约不是纯文本匹配 ----


def test_mutation_missing_combine_is_detected(workflow: dict[str, Any]) -> None:
    tokens = _tokens(_step(_steps(workflow, GATE_JOB), GATE_COMBINE_STEP))
    mutated = [token for token in tokens if token != "combine"]
    with pytest.raises(AssertionError):
        _assert_combine_order(mutated)


def test_mutation_wrong_report_order_is_detected(workflow: dict[str, Any]) -> None:
    step = copy.deepcopy(_step(_steps(workflow, GATE_JOB), GATE_COMBINE_STEP))
    step["run"] = "coverage xml\ncoverage json\ncoverage combine fragments\ncoverage report"
    with pytest.raises(AssertionError, match="顺序错误"):
        _assert_combine_order(_tokens(step))


def test_mutation_local_report_in_collector_is_detected(workflow: dict[str, Any]) -> None:
    steps = copy.deepcopy(_steps(workflow, "ci-checks"))
    step = _step(steps, "Run Unit Tests")
    step["run"] = step["run"].replace("--cov-report=", "--cov-report=xml")
    with pytest.raises(AssertionError, match="cov-report"):
        _assert_collector("ci-checks", steps, "Run Unit Tests", "unit")


def test_mutation_lowered_fail_under_is_detected(workflow: dict[str, Any]) -> None:
    mutated = copy.deepcopy(workflow)
    step = _step(_steps(mutated, GATE_JOB), GATE_COMBINE_STEP)
    step["run"] = step["run"] + "\ncoverage report --cov-fail-under=80\n"
    lowered = [value for value in _cov_fail_under_values(mutated) if value < _FAIL_UNDER_FLOOR]
    assert lowered == [80.0], "变异用例必须能被反降级断言捕获"
