"""CI-F07：CUDA wheel 预热必须产出结构化结果（可观测性闭环）。

守护目标（CI-F07，P2 运行期可观测性缺陷）：
- ``Build CUDA wheel`` 单步有 ``continue-on-error: true`` + 单步超时（单源于 workflow env
  ``LLAMA_CUDA_BUILD_TIMEOUT_MIN``），超时会被吸收成绿色 job；``verify`` / ``publish`` 随之
  skipped，终态无法区分「缓存命中 / 成功构建并发布 / 构建超时 / 其他失败」，也无法知道 ``published``。
- 预热 job 末尾的 ``if: always()`` summary 步骤读取 restore / build / verify / publish 的 outcome / hit
  与起始时间，写出稳定结果枚举、``published`` 标志与 GITHUB_STEP_SUMMARY 表格，并设置 step / job outputs。
- summary 之后的 ``Fail warmup when bank stays cold`` 守卫把「银行仍为空」变成红色 job，
  终结「重步骤全部 continue-on-error 导致 job 恒绿、超时无人发现」的盲区。
- 预热 job 位于独立 workflow ``cuda-wheel-warmup.yml``：冷路径预算放宽后若留在 ``ci_cd.yml``，
  会与 push 到 main 的 run 共享 workflow 级并发组（同 ref、``cancel-in-progress: false``），
  让后续合并的整条 CI 排队（队头阻塞）。独立文件 + 独立并发组 + paths 过滤消除该影响。
- 触发面为 schedule / push 到 main / main 上的 dispatch（tag 不触发——同 run 产物来不及取用）。
- 预热与 ``build-windows`` 的两条 cuda 腿共用 ``.github/actions/restore-cuda-wheel``（下载重试 + 校验单源）。
- 跨 workflow 无法引用 env，故 ``CI_WHEEL_BANK_TAG`` / ``LLAMA_CUDA_WHEEL_KEY_SUFFIX`` 双写于两侧，
  由本测试守护逐字一致。
- 原 build / verify / publish 的 ``if`` / ``continue-on-error`` 与构建命令保持不变，summary 自身不改变 job 结论。

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
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "cuda-wheel-warmup.yml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

JOB = "build-llama-cuda-wheel"
SUMMARY_STEP = "Summarize CUDA wheel warmup outcome"
SUMMARY_ID = "warmup-summary"
GATE_STEP = "Fail warmup when bank stays cold"
GATE_ID = "warmup-gate"
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "restore-cuda-wheel" / "action.yml"
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
SHARED_ENV_KEYS = ("CI_WHEEL_BANK_TAG", "LLAMA_CUDA_WHEEL_KEY_SUFFIX")


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ci_workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def triggers() -> dict[str, Any]:
    # BaseLoader 保留 `on` 字面键（safe_load 会把 YAML 1.1 的 on 解析为布尔 True）。
    loaded = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return loaded["on"]


def _job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert JOB in jobs, f"cuda-wheel-warmup.yml 缺少 job {JOB}"
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


def test_summary_runs_always_before_the_guard(workflow: dict[str, Any]) -> None:
    """summary 必须 always 执行且先于守卫步骤（守卫依赖 summary 的 result output）。"""
    steps = _job(workflow)["steps"]
    ids = [step.get("id") for step in steps]
    assert SUMMARY_ID in ids
    summary = steps[ids.index(SUMMARY_ID)]
    assert summary.get("if") == "always()"
    assert summary.get("name") == SUMMARY_STEP
    assert ids.index(SUMMARY_ID) < ids.index(GATE_ID)


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
    assert "timeoutThresholdMinutes = [int]$env:LLAMA_CUDA_BUILD_TIMEOUT_MIN - 1" in summary_script
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
    budget = int(workflow["env"]["LLAMA_CUDA_BUILD_TIMEOUT_MIN"])
    build = _step(workflow, "Build CUDA wheel")
    assert build.get("if") == "steps.restore-wheel.outputs.hit != 'true'"
    assert build.get("continue-on-error") is True
    assert build.get("timeout-minutes") == budget

    verify = _step(workflow, "Verify wheel CUDA backend before publishing")
    assert verify.get("if") == "steps.restore-wheel.outputs.hit != 'true' && steps.build-wheel.outcome == 'success'"
    assert verify.get("continue-on-error") is True
    assert "timeout-minutes" not in verify

    publish = _step(workflow, "Publish CUDA wheel to release bank")
    assert publish.get("if") == "steps.restore-wheel.outputs.hit != 'true' && steps.verify-wheel.outcome == 'success'"
    assert publish.get("continue-on-error") is True
    assert "timeout-minutes" not in publish


def test_build_timeout_budget_covers_measured_cold_build(workflow: dict[str, Any]) -> None:
    """90 分钟实测会在 nvcc 编译未完成时被杀（run 42353067654），预算须显著放宽。

    job 级超时必须比单步预算大出足够余量：Build 之前的 Toolkit 安装实测可达 26 分钟，
    若 job 超时先触发，``if: always()`` 的 summary 与守卫都不会执行，job 结论变 cancelled（灰），
    恰好退回本次要消灭的「超时无红色信号」状态。
    """
    budget = int(workflow["env"]["LLAMA_CUDA_BUILD_TIMEOUT_MIN"])
    assert budget >= 210
    assert _job(workflow)["timeout-minutes"] - budget >= 60


def test_warmup_job_lives_outside_main_ci(ci_workflow: dict[str, Any]) -> None:
    """暖机 job 不得回到 ci_cd.yml：否则冷构建会与 push 到 main 共享并发组，阻塞整条主 CI。"""
    assert JOB not in ci_workflow["jobs"]
    assert "LLAMA_CUDA_BUILD_TIMEOUT_MIN" not in ci_workflow["env"]


def test_job_trigger_surface_excludes_tags_and_covers_main_push(triggers: dict[str, Any]) -> None:
    """tag 发布时预热与 build-windows 并发启动，同 run 产物必然来不及取用，触发只会白烧一次编译。"""
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers
    push = triggers["push"]
    assert push["branches"] == ["main"]
    assert "tags" not in push
    assert "tags" not in triggers
    paths = push["paths"]
    assert "requirements.txt" in paths
    assert ".github/workflows/cuda-wheel-warmup.yml" in paths
    # 键后缀 / bank tag 定义在 ci_cd.yml，改动须重新核对产物。
    assert ".github/workflows/ci_cd.yml" in paths
    assert ".github/actions/restore-cuda-wheel/**" in paths


def test_warmup_job_limited_to_main_ref(workflow: dict[str, Any]) -> None:
    """dispatch 会执行所选 ref 上的定义，须阻止分支 dispatch 向 bank 写入自定义产物。"""
    assert _job(workflow)["if"] == "github.ref == 'refs/heads/main'"


def test_warmup_builds_are_serialized(workflow: dict[str, Any]) -> None:
    assert workflow.get("concurrency") == {
        "group": "cuda-wheel-warmup",
        "cancel-in-progress": False,
    }


def test_guard_fails_warmup_when_bank_stays_cold(workflow: dict[str, Any]) -> None:
    steps = _job(workflow)["steps"]
    assert steps[-1].get("id") == GATE_ID
    assert steps[-1].get("name") == GATE_STEP
    assert steps[-1].get("if") == "always()"
    script = _normalized(_run(steps[-1]))
    assert "${{ steps.warmup-summary.outputs.result }}" in script
    for success in ("cache_hit", "built_published"):
        assert f'$result -eq "{success}"' in script
    assert "exit 0" in script and "exit 1" in script
    assert "::error::" in script


def test_shared_env_constants_match_ci_cd(workflow: dict[str, Any], ci_workflow: dict[str, Any]) -> None:
    """跨 workflow 无法引用 env，双写处必须逐字一致，否则产物键错配、命中率归零。"""
    for key in SHARED_ENV_KEYS:
        assert workflow["env"][key] == ci_workflow["env"][key], f"{key} 在两个 workflow 中不一致"


def test_build_timeout_env_has_single_source(workflow: dict[str, Any]) -> None:
    """预算单源：env 定义一处，Build 步骤 timeout-minutes 与 summary 判定都引用它。"""
    budget = int(workflow["env"]["LLAMA_CUDA_BUILD_TIMEOUT_MIN"])
    assert _step(workflow, "Build CUDA wheel")["timeout-minutes"] == budget


def test_restore_steps_share_one_composite_action(workflow: dict[str, Any], ci_workflow: dict[str, Any]) -> None:
    """下载重试与 sha256 / 文件名校验必须单源，避免三处副本漂移。"""
    assert ACTION_PATH.is_file(), "缺少 .github/actions/restore-cuda-wheel/action.yml"
    action = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert action["outputs"]["hit"]["value"] == "${{ steps.restore.outputs.hit }}"
    assert action["inputs"]["attempts"]["default"] == "3"
    retry_script = _normalized(action["runs"]["steps"][0]["run"])
    assert "for ($i = 1; $i -le $attempts; $i++)" in retry_script
    assert "SHA256SUMS" in retry_script

    callers = [s for s in _job(workflow)["steps"] if s.get("uses") == "./.github/actions/restore-cuda-wheel"]
    assert len(callers) == 1, f"{JOB} 应恰好有一个 restore 步骤引用共用 action"
    assert "run" not in callers[0], f"{JOB} 的 restore 步骤不应再内联脚本"

    build_windows_callers = [
        s
        for s in ci_workflow["jobs"]["build-windows"]["steps"]
        if s.get("uses") == "./.github/actions/restore-cuda-wheel"
    ]
    assert len(build_windows_callers) == 1, "build-windows 应恰好有一个 restore 步骤引用共用 action"
    assert "run" not in build_windows_callers[0], "build-windows 的 restore 步骤不应再内联脚本"
