"""ci_cd.yml release-windows 发布身份与服务端语义契约（D18-R02）。

守护目标（检视报告 D18-R02，P2）：
- **tag 身份锁定**：``Check release state and tag lock`` 不得只看 ``git/ref/tags``
  是否存在，必须把 tag 解析到 commit（annotated tag 经 ``git/tags/{sha}`` 逐层
  peel），并与触发本次运行的 ``GITHUB_SHA`` 比较；不符即 exit 1（fail-closed），
  防 ``target_commitish`` 默认值把 Release 建到别的 ref。
- **Release 定位分页**：两处按 tag_name 定位 draft 的列表查询必须带
  ``--paginate``——Release 超过 100 时也要找到既有 draft，避免误建重复 Release；
  ``--jq`` 按页执行，合并时只取首个命中 id。
- **PATCH 类型化布尔**：发布 draft 使用 ``-F draft=false -F make_latest=true``
  （类型化布尔）；``-f`` 发送字符串 "false"，会被 GitHub 视为真值。
- **原有触发/依赖语义不变**：release-windows 仍仅 tag 触发、仍依赖
  build-windows + real sidecar smoke，权限仍为 contents: write。

全部为 workflow YAML 与 run 文本的静态断言，不触发任何发布副作用。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

RELEASE_JOB_NAME = "release-windows"
TAG_LOCK_STEP_NAME = "Check release state and tag lock (fail-closed)"
DRAFT_VERIFY_STEP_NAME = "Verify draft assets and SHA256 digests (gate before publish)"
PUBLISH_STEP_NAME = "Publish draft Release as latest (verification passed)"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def release_job(workflow: dict) -> dict:
    return workflow["jobs"][RELEASE_JOB_NAME]


def _step(job: dict, name: str) -> dict:
    steps = [s for s in job["steps"] if s.get("name") == name]
    assert len(steps) == 1, f"release-windows 缺少唯一步骤: {name}"
    return steps[0]


def test_release_windows_trigger_and_needs_unchanged(release_job: dict) -> None:
    # 最小方案不改变触发面与依赖关系：仍仅 tag 触发、仍等待真实 sidecar smoke。
    assert release_job["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert release_job["needs"] == ["build-windows", "e2e-sidecar-real-smoke"]
    assert release_job["permissions"] == {"contents": "write"}


def test_tag_lock_resolves_annotated_tag_and_compares_github_sha(release_job: dict) -> None:
    run = _step(release_job, TAG_LOCK_STEP_NAME)["run"]
    # git/ref/tags 指向 annotated tag 的 tag 对象时，须经 git/tags/{sha} 逐层 peel
    # 到 commit（仅比较 ref 存在性不足以锁定发布身份）。
    assert "git/ref/tags/${TAG}" in run
    assert "git/tags/${obj_sha}" in run
    assert ".object.type" in run
    assert ".object.sha" in run
    assert 'while [ "$obj_type" = "tag" ]' in run
    # peel 结果与触发本次运行的 commit 比较；不符 / 解析失败 must exit 1
    assert '"$obj_sha" != "$GITHUB_SHA"' in run
    assert "exit 1" in run
    # 非 commit 对象（如 tree/blob）同样拒绝
    assert '"$obj_type" != "commit"' in run


def test_tag_lock_fails_closed_when_ref_query_fails(release_job: dict) -> None:
    run = _step(release_job, TAG_LOCK_STEP_NAME)["run"]
    # API 错误（含 tag 不存在）不得落入「继续创建 Release」分支；步骤须以 exit 1 收口。
    assert "if ! tag_json=$(gh api" in run
    assert "refusing to create a release from a different ref" in run
    assert "exit 1" in run


def test_release_lookup_is_paginated_in_both_steps(release_job: dict) -> None:
    # 同 job 两处按 tag_name 定位 draft：Release 超过 100 时无 --paginate 会漏命中。
    for step_name in (TAG_LOCK_STEP_NAME, DRAFT_VERIFY_STEP_NAME):
        run = _step(release_job, step_name)["run"]
        assert "--paginate" in run, step_name
        assert "releases?per_page=100" in run, step_name
        # --jq 按页执行：逐页输出匹配项（不留页数组歧义）
        assert "select(.tag_name" in run, step_name
    # 状态判定步骤逐条检查全部同 tag Release（「旧正式版 + 新 draft」不得误选 draft）
    state_run = _step(release_job, TAG_LOCK_STEP_NAME)["run"]
    assert "while read -r release_id draft" in state_run
    # 校验步骤按 id 精确定位：只取首个匹配 id（分页输出的跨页合并）
    verify_run = _step(release_job, DRAFT_VERIFY_STEP_NAME)["run"]
    assert "awk '!seen && NF" in verify_run


def test_publish_patch_uses_typed_boolean_fields(release_job: dict) -> None:
    run = _step(release_job, PUBLISH_STEP_NAME)["run"]
    # -F 为类型化字段（布尔）；-f 发送字符串 "false"（GitHub 视作真值）
    assert "-F draft=false" in run
    assert "-F make_latest=true" in run
    assert "-f draft=" not in run
    assert "-f make_latest=" not in run


def test_publish_step_keeps_identity_preconditions(release_job: dict) -> None:
    run = _step(release_job, PUBLISH_STEP_NAME)["run"]
    # 发布前必须具备 release_id（由 verify 步骤产出）且 draft 校验先于 PATCH 发布。
    assert "RELEASE_ID" in run
    assert "release id not available" in run
    assert "gh api -X PATCH" in run
    assert "exit 1" in run
