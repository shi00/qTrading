"""CI：构建产物的 provenance 证明必须对 Rekor 外部抖动具备重试韧性。

守护目标（run 42376863695，job 128419638359）：
- ``Build Windows EXE (embedded-cpu)`` 唯一失败步骤为 ``Attest Build Provenance``，报错
  ``error creating tlog entry - (500) unexpected result from transparency log``；同 run 的
  ``cpu`` 变体同一步骤成功，job 权限（``id-token`` / ``attestations``）与 action pin 完全一致，
  同期 Sigstore 状态页 Rekor 为 downtime —— 属外部公共服务抖动，不是配置或代码缺陷。
- 修复方式为同一 attest action 连续三次尝试：前两次 ``continue-on-error: true`` 只记录，
  第三次不带 ``continue-on-error``，仍失败则 job 变红。禁止把第一次尝试也改成可忽略，
  否则 attestation 缺失会被静默成绿色（等同把「已知不可信」伪装成「无问题」）。
- 三次尝试必须使用同一 pinned commit 与同一 ``subject-path``，避免重试悄悄换版本或换产物。
- 重试步骤不得改变 job 结论以外的行为：upload / release 步骤与其 ``if`` 保持原样。

测试只静态解析 workflow YAML，不执行构建、不触发任何签名或发布副作用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

JOB = "build-windows"
ATTEST_STEP_NAMES = (
    "Attest Build Provenance",
    "Attest Build Provenance (retry 2/3)",
    "Attest Build Provenance (retry 3/3)",
)
ATTEST_STEP_IDS = (
    "attest-provenance",
    "attest-provenance-retry1",
    "attest-provenance-retry2",
)
EXPECTED_USES = "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"  # v4.2.2
EXPECTED_SUBJECT_PATH = "Output/AStockScreener-Setup-${{ matrix.variant }}.exe"


@pytest.fixture(scope="module")
def build_windows_job() -> dict[str, Any]:
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert JOB in jobs, f"ci_cd.yml 缺少 job {JOB}"
    return jobs[JOB]


def _attest_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job["steps"]
    found = [step for step in steps if step.get("name") in ATTEST_STEP_NAMES]
    assert [step["name"] for step in found] == list(ATTEST_STEP_NAMES), (
        "Attest Build Provenance 重试链缺失或顺序错误："
        f"期望 {ATTEST_STEP_NAMES}，实际 {[step.get('name') for step in found]}"
    )
    return found


def test_attest_retry_chain_uses_identical_action_pin_and_subject(
    build_windows_job: dict[str, Any],
) -> None:
    for step in _attest_steps(build_windows_job):
        assert step["uses"] == EXPECTED_USES, f"{step['name']} 必须锁定同一 action commit"
        assert step["with"]["subject-path"] == EXPECTED_SUBJECT_PATH


def test_attest_retry_chain_step_ids_are_stable(
    build_windows_job: dict[str, Any],
) -> None:
    steps = _attest_steps(build_windows_job)
    assert tuple(step["id"] for step in steps) == ATTEST_STEP_IDS


def test_only_final_attempt_can_fail_the_job(
    build_windows_job: dict[str, Any],
) -> None:
    first, second, last = _attest_steps(build_windows_job)
    assert first.get("continue-on-error") is True, "首次失败应记录后继续，交由重试兜底"
    assert second.get("continue-on-error") is True
    assert "continue-on-error" not in last, "末次尝试失败必须让 job 变红，不得静默"


def test_retries_run_only_after_previous_attempt_fails(
    build_windows_job: dict[str, Any],
) -> None:
    first, second, last = _attest_steps(build_windows_job)
    assert second["if"] == f"steps.{first['id']}.outcome == 'failure'"
    assert last["if"] == (f"steps.{first['id']}.outcome == 'failure' && steps.{second['id']}.outcome == 'failure'")


def test_retries_are_backed_off_between_attempts(
    build_windows_job: dict[str, Any],
) -> None:
    steps = build_windows_job["steps"]
    names = [step.get("name") for step in steps]
    order = [
        ATTEST_STEP_NAMES[0],
        "Wait before attest retry 2/3",
        ATTEST_STEP_NAMES[1],
        "Wait before attest retry 3/3",
        ATTEST_STEP_NAMES[2],
    ]
    indices = [names.index(name) for name in order]
    assert indices == sorted(indices), f"退避步骤必须夹在重试之间，实际顺序 {order}"
    # 紧邻重试会同时落在 Rekor 故障窗口内；30s/60s 递增退避与仓库既有下载重试范式一致。
    assert "Start-Sleep -Seconds 30" in steps[indices[1]]["run"]
    assert "Start-Sleep -Seconds 60" in steps[indices[3]]["run"]


def test_retry_chain_precedes_artifact_upload(
    build_windows_job: dict[str, Any],
) -> None:
    names = [step.get("name") for step in build_windows_job["steps"]]
    last_attest = names.index(ATTEST_STEP_NAMES[-1])
    upload = names.index("Upload Build Artifact (non-tag)")
    release = names.index("Upload Release Assets (tag only)")
    assert last_attest < upload < release, "证明链必须在产物上传前完成，避免未证明产物先发布"
