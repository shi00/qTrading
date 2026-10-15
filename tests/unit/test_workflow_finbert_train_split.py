"""finbert-train.yml 最小权限拆分（FT-06）静态断言。

守护目标（检视报告 FT-06，P2 确定缺陷）：
- 训练/导出/Gate job（train-export-gate）permissions 仅有 ``contents: read``，
  依赖安装 / 语料下载 / 训练 / 导出 / Gate 全链路期间 job token 无写仓库能力；
- 发布 job（publish-release）单独授予 ``contents: write``，且仅在
  train-export-gate 结果为 success 时运行（Gate 失败 → 发布 job 跳过）；
- 发布 job 只消费 job1 上传的发布 artifact（最小文件集），下载后先
  ``sha256sum -c`` 本地复验再发布，不 checkout、不执行仓库脚本；
- 发布步骤版本不可变（FT-05）：Release tag 已存在时拒绝发布并失败
  （exit 1 + 明确指引），不提供 ``--clobber`` 部分更新（只换资产二进制、
  不换 note 会令 lineage 静默错位）；``gh release create`` 仅在 tag
  不存在分支可达。

全部为 workflow YAML 的静态断言，不触发任何训练 / 发布副作用。
"""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "finbert-train.yml"

JOB1_NAME = "train-export-gate"
JOB2_NAME = "publish-release"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_top_level_permissions_are_read_only(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}


def test_job1_permissions_are_read_only(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert JOB1_NAME in jobs
    assert jobs[JOB1_NAME]["permissions"] == {"contents": "read"}


def test_job2_permissions_are_write(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert JOB2_NAME in jobs
    assert jobs[JOB2_NAME]["permissions"] == {"contents": "write"}


def test_job2_depends_on_job1_and_guards_on_success(workflow: dict) -> None:
    job2 = workflow["jobs"][JOB2_NAME]
    assert job2["needs"] == [JOB1_NAME]
    assert job2["if"] == f"needs.{JOB1_NAME}.result == 'success'"


def test_job1_exposes_outputs_for_publish(workflow: dict) -> None:
    outputs = workflow["jobs"][JOB1_NAME]["outputs"]
    assert outputs["model_version"] == "${{ steps.validate_inputs.outputs.model_version }}"
    assert outputs["release_tag"] == "${{ steps.validate_inputs.outputs.release_tag }}"


def test_job2_steps_order_download_verify_publish(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    assert [s["name"] for s in steps] == [
        "Download release artifacts",
        "Verify checksums before publish",
        "Publish release (finbert-gguf-v*)",
    ]


def test_job2_verifies_checksums_before_publish(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    verify = next(s for s in steps if s["name"] == "Verify checksums before publish")
    run = verify["run"]
    assert "set -euo pipefail" in run
    assert "test -f release-assets/release_note.md" in run
    assert "sha256sum -c SHA256SUMS.txt" in run


def test_job2_downloads_verified_release_artifact_only(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    download_steps = [s for s in steps if "download-artifact" in s.get("uses", "")]
    assert len(download_steps) == 1
    with_block = download_steps[0]["with"]
    assert with_block["name"] == (
        "finbert-gguf-release-${{ needs.train-export-gate.outputs.model_version }}-${{ github.run_id }}"
    )
    assert with_block["path"] == "release-assets"


def test_download_artifact_is_sha_pinned(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    uses_list = [s["uses"] for s in steps if "uses" in s]
    download_uses = [u for u in uses_list if u.startswith("actions/download-artifact@")]
    assert len(download_uses) == 1
    ref = download_uses[0].split("@", 1)[1]
    # 对齐仓库既有锚定风格：完整 40 位 commit SHA（非 mutable tag）
    assert re.fullmatch(r"[0-9a-f]{40}", ref), f"download-artifact ref 非 40 位 SHA 锚定: {ref}"


def test_release_artifact_fileset_is_minimal(workflow: dict) -> None:
    job1 = workflow["jobs"][JOB1_NAME]
    upload_steps = [
        s for s in job1["steps"] if str(s.get("with", {}).get("name", "")).startswith("finbert-gguf-release-")
    ]
    assert len(upload_steps) == 1
    with_block = upload_steps[0]["with"]
    paths = sorted(p.strip() for p in with_block["path"].strip().splitlines())
    assert paths == [
        "stage_b_out/SHA256SUMS.txt",
        "stage_b_out/finbert2-news-f16.gguf",
        "stage_b_out/head.npz",
        "stage_b_out/release_note.md",
    ]
    # 文件缺失时上传即失败（fail-safe），不允许静默空 artifact 进入发布 job
    assert with_block["if-no-files-found"] == "error"


def test_gate_runs_before_release_artifact_upload_in_job1(workflow: dict) -> None:
    steps = workflow["jobs"][JOB1_NAME]["steps"]
    names = [s.get("name") for s in steps]
    gate_idx = names.index("Consistency gate (verify_finbert_gguf.py --head)")
    sums_idx = names.index("Generate SHA256SUMS.txt")
    note_idx = names.index("Generate release note")
    rel_idx = names.index("Upload release artifacts (for publish job)")
    assert gate_idx < sums_idx < note_idx < rel_idx


def test_debug_artifact_upload_kept_always(workflow: dict) -> None:
    job1 = workflow["jobs"][JOB1_NAME]
    debug_steps = [s for s in job1["steps"] if s.get("name") == "Upload artifacts (incl. gate-failure debugging)"]
    assert len(debug_steps) == 1
    assert debug_steps[0]["if"] == "always()"


def test_job1_has_no_release_publish_step(workflow: dict) -> None:
    # 防回归：发布写操作必须留在 job2，job1（只读权限）不得残留 gh release 写命令
    job1 = workflow["jobs"][JOB1_NAME]
    for step in job1["steps"]:
        run = step.get("run", "")
        assert "gh release create" not in run
        assert "gh release upload" not in run


def test_job2_does_not_execute_repo_code(workflow: dict) -> None:
    # 发布 job 不 checkout、不运行仓库脚本：仅 artifact 下载 + 复验 + gh 发布
    job2 = workflow["jobs"][JOB2_NAME]
    for step in job2["steps"]:
        assert not str(step.get("uses", "")).startswith("actions/checkout@")


def test_job2_has_no_stale_step_context_refs(workflow: dict) -> None:
    # 拆 job 后不得残留对 job1 step 上下文的引用（steps.validate_inputs 在
    # job2 不存在，GitHub Actions 运行时会直接报错）
    job2_raw = yaml.safe_dump(workflow["jobs"][JOB2_NAME], allow_unicode=True)
    assert "steps.validate_inputs" not in job2_raw


def test_publish_step_uses_job_outputs(workflow: dict) -> None:
    job2 = workflow["jobs"][JOB2_NAME]
    publish = next(s for s in job2["steps"] if s.get("name") == "Publish release (finbert-gguf-v*)")
    assert publish["env"]["RELEASE_TAG"] == "${{ needs.train-export-gate.outputs.release_tag }}"
    run = publish["run"]
    assert "needs.train-export-gate.outputs.model_version" in run
    assert "release-assets/finbert2-news-f16.gguf" in run
    assert "release-assets/SHA256SUMS.txt" in run
    assert "--notes-file release-assets/release_note.md" in run
    assert '--target "$GITHUB_SHA"' in run


def test_gh_token_env_preserved_in_both_jobs(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert jobs[JOB1_NAME]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert jobs[JOB2_NAME]["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_timeout_budgets_preserved(workflow: dict) -> None:
    jobs = workflow["jobs"]
    # 训练大头（1~3h）保留 360 分钟预算；发布 job 收紧到 10 分钟
    assert jobs[JOB1_NAME]["timeout-minutes"] == 360
    assert jobs[JOB2_NAME]["timeout-minutes"] == 10


def test_publish_step_rejects_existing_release_tag(workflow: dict) -> None:
    # FT-05：同 tag 重跑拒绝覆盖——model_version 为不可变发布标识。
    # Release（tag）已存在时必须显式失败（exit 1 + 指引提升 model_version），
    # 防止 --clobber 部分更新令 lineage（note ↔ 资产 digest ↔ 运行参数）错位。
    job2 = workflow["jobs"][JOB2_NAME]
    publish = next(s for s in job2["steps"] if s.get("name") == "Publish release (finbert-gguf-v*)")
    run = publish["run"]
    # tag 已存在检测入口（gh release view 命中即拒绝）
    assert "gh release view" in run
    # 拒绝语义：非零退出 + 明确错误指引（含中断恢复路径：人工删 Release 留痕）
    assert "exit 1" in run
    assert "拒绝覆盖" in run
    assert "提升 model_version" in run
    # gh release create 仅在 tag 不存在分支可达：view 检查与拒绝退出
    # 必须先于 create 出现（顺序即分支语义的静态表达）
    assert run.index("gh release view") < run.index("gh release create")
    assert run.index("exit 1") < run.index("gh release create")


def test_publish_step_has_no_clobber_partial_update(workflow: dict) -> None:
    # FT-05：不把 --clobber 部分更新（只换资产不换 note）当作重跑幂等——
    # job2 不得残留 gh release upload / --clobber 上传路径。
    # （yaml.safe_load 丢弃 YAML 注释后 dump，注释中的说明性 "--clobber"
    # 提及不计入；job1 语料下载的 gh release download --clobber 为本地
    # 临时目录覆盖下载，与 Release 资产发布无关，不在本断言范围。）
    job2 = workflow["jobs"][JOB2_NAME]
    job2_raw = yaml.safe_dump(job2, allow_unicode=True)
    assert "--clobber" not in job2_raw
    assert "gh release upload" not in job2_raw
    for step in job2["steps"]:
        run = step.get("run", "")
        assert "gh release upload" not in run
        assert "--clobber" not in run
