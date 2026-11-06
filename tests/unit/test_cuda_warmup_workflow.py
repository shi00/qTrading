"""CI-F07：CUDA wheel 预热 job 必须产出结构化结果（可观测性闭环）。

守护目标（CI-F07，P2 运行期可观测性缺陷）：
- ``Build CUDA wheel`` 单步有 ``continue-on-error: true`` + ``timeout-minutes: 90``，
  超时会被吸收成绿色 job；``verify`` / ``publish`` 随之 skipped，终态无法区分
  「缓存命中 / 成功构建并发布 / 构建超时 / 其他失败」，也无法知道 ``published``。
- job 末尾新增 ``if: always()`` 的 summary 步骤，读取 restore / build / verify / publish
  的 outcome / hit 与起始时间，写出稳定结果枚举、``published`` 标志与 GITHUB_STEP_SUMMARY
  表格，并设置 step / job outputs。
- 原 build / verify / publish 的 ``if`` / ``continue-on-error`` / ``timeout-minutes``
  与构建命令保持不变，summary 自身不改变 job 结论。

测试只静态解析 workflow YAML，不执行构建、不触发发布副作用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

JOB = "build-llama-cuda-wheel"
SUMMARY_STEP = "Summarize CUDA wheel warmup outcome"
SUMMARY_ID = "warmup-summary"
RESULT_ENUMS = (
    "cache_hit",
    "built_published",
    "build_timeout",
    "build_failed",
    "verify_failed",
    "publish_failed",
    "cancelled",
    "unexpected",
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert JOB in jobs, f"ci_cd.yml 缺少 job {JOB}"
    return jobs[JOB]


def _step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    steps = [s for s in _job(workflow)["steps"] if s.get("name") == name]
    assert len(steps) == 1, f"{JOB} 中应恰好有一个步骤 {name}"
    return steps[0]


def _run(step: dict[str, Any]) -> str:
    script = step.get("run")
    assert isinstance(script, str) and script.strip(), f"步骤 {step.get('name')} 缺少 run 脚本"
    return script


def _normalized(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def summary_script(workflow: dict[str, Any]) -> str:
    return _normalized(_run(_step(workflow, SUMMARY_STEP)))


def test_job_declares_structured_outputs(workflow: dict[str, Any]) -> None:
    outputs = _job(workflow).get("outputs")
    assert outputs == {
        "warmup_result": "${{ steps.warmup-summary.outputs.result }}",
        "warmup_published": "${{ steps.warmup-summary.outputs.published }}",
    }


def test_summary_is_last_step_and_always_runs(workflow: dict[str, Any]) -> None:
    steps = _job(workflow)["steps"]
    assert steps[-1].get("id") == SUMMARY_ID
    assert steps[-1].get("if") == "always()"
    assert steps[-1].get("name") == SUMMARY_STEP


def test_summary_does_not_change_job_conclusion(workflow: dict[str, Any]) -> None:
    summary = _step(workflow, SUMMARY_STEP)
    assert summary.get("continue-on-error") is True
    assert "timeout-minutes" not in summary


def test_summary_reads_all_step_signals_and_start_time(summary_script: str) -> None:
    for expression in (
        "${{ steps.restore-wheel.outputs.hit }}",
        "${{ steps.build-wheel.outcome }}",
        "${{ steps.verify-wheel.outcome }}",
        "${{ steps.publish-wheel.outcome }}",
    ):
        assert expression in summary_script, f"summary 未读取 {expression}"
    assert "CUDA_BUILD_STARTED_AT" in summary_script


def test_publish_step_exposes_outcome_id(workflow: dict[str, Any]) -> None:
    publish = _step(workflow, "Publish CUDA wheel to release bank")
    assert publish.get("id") == "publish-wheel"


def test_summary_emits_all_result_enums(summary_script: str) -> None:
    for result in RESULT_ENUMS:
        assert f'"{result}"' in summary_script, f"summary 缺少结果枚举 {result}"


def test_cache_hit_is_classified_before_build_outcomes(summary_script: str) -> None:
    assert re.search(r'\$hit -eq "true".*?\$result = "cache_hit"', summary_script)


def test_timeout_classified_by_elapsed_threshold(summary_script: str) -> None:
    assert "timeoutThresholdMinutes = 89" in summary_script
    assert re.search(
        r'\$buildOutcome -eq "failure".*?'
        r"\$elapsedMinutes -ge \$timeoutThresholdMinutes.*?"
        r'\$result = "build_timeout".*?'
        r'\$result = "build_failed"',
        summary_script,
    )


def test_published_true_only_for_built_published(summary_script: str) -> None:
    assert re.search(
        r'\$verifyOutcome -eq "success".*?'
        r'\$publishOutcome -eq "success".*?'
        r'\$result = "built_published"',
        summary_script,
    )
    assert '$published = if ($result -eq "built_published") { "true" } else { "false" }' in summary_script


def test_summary_writes_markdown_and_outputs(summary_script: str) -> None:
    assert "$env:GITHUB_STEP_SUMMARY" in summary_script
    for column in ("Cache hit", "Build", "Verify", "Publish", "Elapsed (min)", "Result", "Published"):
        assert f"| {column} |" in summary_script, f"summary 表格缺少列 {column}"
    assert 'Add-Content -Path $env:GITHUB_OUTPUT -Value "result=$result"' in summary_script
    assert 'Add-Content -Path $env:GITHUB_OUTPUT -Value "published=$published"' in summary_script


def test_non_success_and_non_cache_hit_emit_warning(summary_script: str) -> None:
    assert "::warning::" in summary_script
    assert '$result -ne "cache_hit" -and $result -ne "built_published"' in summary_script


def test_build_step_records_utc_start_before_build(workflow: dict[str, Any]) -> None:
    script = _normalized(_run(_step(workflow, "Build CUDA wheel")))
    assert "ToUniversalTime()" in script
    assert 'Add-Content -Path $env:GITHUB_ENV -Value "CUDA_BUILD_STARTED_AT=$startedAt"' in script
    assert script.index("CUDA_BUILD_STARTED_AT") < script.index("python -m pip wheel")


def test_build_command_unchanged(workflow: dict[str, Any]) -> None:
    script = _normalized(_run(_step(workflow, "Build CUDA wheel")))
    assert (
        "python -m pip wheel --no-deps --wheel-dir wheelhouse "
        "llama-cpp-python==${{ steps.llama-version.outputs.version }}" in script
    )


def test_build_verify_publish_semantics_unchanged(workflow: dict[str, Any]) -> None:
    build = _step(workflow, "Build CUDA wheel")
    assert build.get("if") == "steps.restore-wheel.outputs.hit != 'true'"
    assert build.get("continue-on-error") is True
    assert build.get("timeout-minutes") == 90

    verify = _step(workflow, "Verify wheel CUDA backend before publishing")
    assert verify.get("if") == "steps.restore-wheel.outputs.hit != 'true' && steps.build-wheel.outcome == 'success'"
    assert verify.get("continue-on-error") is True
    assert "timeout-minutes" not in verify

    publish = _step(workflow, "Publish CUDA wheel to release bank")
    assert publish.get("if") == "steps.restore-wheel.outputs.hit != 'true' && steps.verify-wheel.outcome == 'success'"
    assert publish.get("continue-on-error") is True
    assert "timeout-minutes" not in publish
