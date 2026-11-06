"""D18-F02：sidecar tag 发布必须原子化（单一 publisher，draft → 复验 → publish）。

守护目标（检视报告 D18-F02，P2 确定缺陷）：
- tag 推送复用 ``build-artifacts`` 的四平台只读构建产物；``lint-test`` 在 tag
  事件执行质量门禁（``build-artifacts`` 依赖它，故为必要前置）；
- 发布写操作收敛到单一 ``publish-release`` job：``needs`` 四平台聚合产物与
  ``audit``，且仅 tag 触发；任何构建/测试/审计 failure、cancelled、skipped 都
  不会进入 Release 写操作（不使用 ``always()`` 绕过）；
- 发布 job 精确下载 4 个平台 artifact，核对 4 binary + 4 manifest + 4
  sha256sums 共 12 项，并交叉验证每平台 ``sums`` / ``manifest`` / 实测摘要；
- 创建或复用 draft，上传后按服务端 digest 复核，最后一次 PATCH 发布；正式
  Release 已存在即失败拒绝覆盖；失败最多留下 draft；
- 废弃四腿 matrix 各自创建正式 Release 与 ``--clobber`` 覆盖路径。

全部为 workflow YAML 的静态断言，不触发任何构建 / 发布副作用。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sidecar.yml"

LINT_JOB = "lint-test"
BUILD_JOB = "build-artifacts"
AUDIT_JOB = "audit"
PUBLISH_JOB = "publish-release"
TAG_PREFIX = "refs/tags/sidecar-v"

LLVM_COV_STEP = "cargo llvm-cov (unit tests coverage, Linux only, enforced)"
COVERAGE_UPLOAD_STEP = "Upload coverage report (Linux only)"

DOWNLOAD_STEP = "Download all sidecar release artifacts"
LOCAL_VERIFY_STEP = "Verify downloaded artifacts and tag commit"
STATE_STEP = "Check release state (refuse published; reuse draft)"
UPLOAD_STEP = "Create or reuse draft Release and upload 12 assets"
REMOTE_VERIFY_STEP = "Verify draft assets and SHA256 digests (gate before publish)"
PUBLISH_STEP = "Publish draft Release as latest (verification passed)"

PLATFORM_ROWS = (
    "windows|x86_64-pc-windows-msvc|qtrading-pg-sidecar-windows-x86_64.exe",
    "linux|x86_64-unknown-linux-gnu|qtrading-pg-sidecar-linux-x86_64",
    "linux-arm|aarch64-unknown-linux-gnu|qtrading-pg-sidecar-linux-aarch64",
    "macos|aarch64-apple-darwin|qtrading-pg-sidecar-macos-aarch64",
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert name in jobs, f"sidecar.yml 缺少 job {name}"
    return jobs[name]


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    steps = _job(workflow, job)["steps"]
    match = [s for s in steps if s.get("name") == name]
    assert len(match) == 1, f"{job} 中应恰好有一个步骤 {name}"
    return match[0]


def _dump(job: dict[str, Any]) -> str:
    return yaml.safe_dump(job, allow_unicode=True)


def test_top_level_permissions_stay_read_only(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}


def test_lint_test_runs_on_sidecar_tag_push(workflow: dict[str, Any]) -> None:
    lint_if = _job(workflow, LINT_JOB)["if"]
    assert "github.event_name == 'push'" in lint_if
    assert f"startsWith(github.ref, '{TAG_PREFIX}')" in lint_if
    # 既有事件用途保持：PR / dispatch / schedule / main push 仍执行测试
    for event in ("'pull_request'", "'workflow_dispatch'", "'schedule'"):
        assert event in lint_if, f"lint-test 丢失既有事件 {event}"
    assert "refs/heads/main" in lint_if


def test_build_artifacts_reuses_tag_and_stays_read_only(workflow: dict[str, Any]) -> None:
    build = _job(workflow, BUILD_JOB)
    assert build["needs"] == LINT_JOB or build["needs"] == [LINT_JOB]
    assert "github.event_name == 'push'" in build["if"]
    assert f"startsWith(github.ref, '{TAG_PREFIX}')" in build["if"]
    assert "refs/heads/main" in build["if"]
    # 构建 job 不得取得写权限（发布写操作只在 publisher）
    assert build.get("permissions") in (None, {"contents": "read"})


def test_build_artifacts_generates_and_uploads_checksums(workflow: dict[str, Any]) -> None:
    build = _job(workflow, BUILD_JOB)
    names = [s.get("name") for s in build["steps"]]
    assert "Generate SHA256 checksums" in names
    checksums = _step(workflow, BUILD_JOB, "Generate SHA256 checksums")
    run = checksums["run"]
    assert 'cp "${{ matrix.binary_path }}" "${{ matrix.artifact_name }}"' in run
    assert 'sha256sum "${{ matrix.artifact_name }}"' in run
    assert "sha256sums-${{ matrix.target }}.txt" in run

    upload = _step(workflow, BUILD_JOB, "Upload release artifact (含 manifest)")
    paths = [p.strip() for p in upload["with"]["path"].strip().splitlines()]
    assert paths == [
        "sidecars/qtrading-pg-sidecar/${{ matrix.artifact_name }}",
        "sidecars/qtrading-pg-sidecar/sidecar-manifest-${{ matrix.os }}.json",
        "sidecars/qtrading-pg-sidecar/sha256sums-${{ matrix.target }}.txt",
    ]
    assert upload["with"]["if-no-files-found"] == "error"


def test_release_matrix_job_is_replaced_by_single_publisher(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    assert "release" not in jobs, "四腿 matrix release job 必须被单一 publisher 取代"
    publisher = _job(workflow, PUBLISH_JOB)
    assert "matrix" not in publisher
    assert "strategy" not in publisher
    assert publisher["runs-on"] == "ubuntu-latest"
    assert publisher["timeout-minutes"] == 10


def test_publisher_needs_all_build_legs_and_audit(workflow: dict[str, Any]) -> None:
    publisher = _job(workflow, PUBLISH_JOB)
    # build-artifacts 为 4 平台矩阵，整体 success 才满足聚合语义；audit 同次成功才放行
    assert publisher["needs"] == [BUILD_JOB, AUDIT_JOB]
    assert f"startsWith(github.ref, '{TAG_PREFIX}')" in publisher["if"]
    assert "github.event_name == 'push'" in publisher["if"]


def test_publisher_needs_blocks_after_any_failed_or_cancelled_matrix_leg(workflow: dict[str, Any]) -> None:
    """fail-fast=false 只让矩阵腿继续跑，聚合结果仍要求全部 success 才放行 publisher。"""
    build = _job(workflow, BUILD_JOB)
    assert build["strategy"]["fail-fast"] is False, "矩阵腿需各自跑完，便于收集全部平台证据"
    assert len(build["strategy"]["matrix"]["include"]) == 4
    # 构建矩阵整体依赖质量门禁，任一平台失败/取消会使 build-artifacts 聚合结果非 success
    assert build["needs"] in (LINT_JOB, [LINT_JOB])

    publisher = _job(workflow, PUBLISH_JOB)
    assert publisher["needs"] == [BUILD_JOB, AUDIT_JOB]
    dumped = _dump(publisher)
    # 未显式写 status check 函数时 GitHub 默认 AND success()；任何 always/failure/cancelled
    # 覆盖都会让失败矩阵腿仍进入发布，故禁止出现。
    for status_function in ("always()", "failure()", "cancelled()", "success()"):
        assert status_function not in dumped, f"publisher 不得用 {status_function} 绕过 needs 聚合"


def test_publisher_is_tag_only_and_cannot_bypass_failures(workflow: dict[str, Any]) -> None:
    publisher = _job(workflow, PUBLISH_JOB)
    dumped = _dump(publisher)
    assert "always()" not in dumped, "不得用 always() 绕过 needs 失败/取消语义"
    assert "continue-on-error" not in dumped, "发布链不得吞掉失败"
    for step in publisher["steps"]:
        run = step.get("run", "")
        assert "|| true" not in run, f"步骤 {step.get('name')} 吞掉了命令失败"


def test_only_publisher_has_write_permissions(workflow: dict[str, Any]) -> None:
    winners = []
    for name, job in workflow["jobs"].items():
        permissions = job.get("permissions")
        if permissions is None:
            continue
        if permissions.get("contents") == "write":
            winners.append(name)
    assert winners == [PUBLISH_JOB], f"写权限只能授予 {PUBLISH_JOB}，实际: {winners}"


def test_no_matrix_release_creation_and_no_clobber_anywhere(workflow: dict[str, Any]) -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # 副作用集中：不再有四腿各自创建 Release，也不使用 --clobber 改写既有资产。
    assert "gh release create" not in text
    assert "--clobber" not in text
    # gh release upload 只允许出现在单一 publisher 的上传步骤；四平台构建与其余 job
    # 均为只读，不得执行发布写操作。
    names = [s.get("name") for s in _job(workflow, PUBLISH_JOB)["steps"]]
    assert names.count(UPLOAD_STEP) == 1
    upload_run = _step(workflow, PUBLISH_JOB, UPLOAD_STEP)["run"]
    invocations = [line.strip() for line in upload_run.splitlines() if line.strip().startswith("gh release upload")]
    assert len(invocations) == 1, "只允许单一 gh release upload 上传调用"
    for name in workflow["jobs"]:
        if name == PUBLISH_JOB:
            continue
        assert "gh release upload" not in _dump(workflow["jobs"][name]), f"{name} 不得执行发布写操作"


def test_publisher_downloads_all_four_platform_artifacts(workflow: dict[str, Any]) -> None:
    download = _step(workflow, PUBLISH_JOB, DOWNLOAD_STEP)
    assert download["uses"].startswith("actions/download-artifact@")
    ref = download["uses"].split("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), f"download-artifact 未按 40 位 SHA 锚定: {ref}"
    with_block = download["with"]
    assert with_block["pattern"] == "qtrading-pg-sidecar-*"
    assert with_block["merge-multiple"] is False
    assert with_block["path"] == "release-assets"


def test_publisher_step_order_is_download_verify_state_upload_verify_publish(workflow: dict[str, Any]) -> None:
    names = [s.get("name") for s in _job(workflow, PUBLISH_JOB)["steps"]]
    assert names == [
        DOWNLOAD_STEP,
        LOCAL_VERIFY_STEP,
        STATE_STEP,
        UPLOAD_STEP,
        REMOTE_VERIFY_STEP,
        PUBLISH_STEP,
    ]


def test_local_verify_checks_twelve_assets_and_cross_platform_digests(workflow: dict[str, Any]) -> None:
    run = _step(workflow, PUBLISH_JOB, LOCAL_VERIFY_STEP)["run"]
    assert "set -euo pipefail" in run
    # 四平台精确清单（目录 = artifact 名，每目录恰好 3 个文件）
    for row in PLATFORM_ROWS:
        assert row in run
    assert 'find "$dir" -maxdepth 1 -type f' in run
    assert '"$artifact" "$manifest" "$sums"' in run
    assert 'if [ "$expected_count" -ne 12 ]' in run
    # 每平台摘要三方一致：sums 记录值 / manifest sha256 / 实测 binary 摘要
    assert "awk '{print $1}'" in run
    assert "jq -r '.sha256 // \"\"'" in run
    assert 'sha256sum "${dir}/${artifact}"' in run
    # 清单 schema / target / 构建提交锁定
    assert '.target // ""' in run
    assert '.schema // ""' in run
    assert '.git_sha // ""' in run
    assert "qtrading.sidecar.manifest.v1" in run
    assert '[[ ! "$tag_commit_sha" == "$manifest_git"* ]]' in run
    # 远端 tag 必须存在并解析到被标记 commit（annotated tag 需再 peel 一层）
    assert 'gh api "repos/${GH_REPO}/git/ref/tags/${TAG}"' in run
    assert 'gh api "repos/${GH_REPO}/git/tags/${object_sha}"' in run
    assert "tag_commit_sha" in run and "object_sha" in run and "GITHUB_SHA" in run


def test_state_step_refuses_published_release_and_reuses_only_draft(workflow: dict[str, Any]) -> None:
    run = _step(workflow, PUBLISH_JOB, STATE_STEP)["run"]
    assert "set -euo pipefail" in run
    assert 'gh api --paginate "repos/${GH_REPO}/releases?per_page=100"' in run
    assert ".draft" in run
    assert "already published" in run
    assert "refusing to overwrite" in run
    assert "exit 1" in run
    # 正式版拒绝必须发生在任何创建/上传动作之前（顺序即静态分支语义）
    publish_steps = [s.get("name") for s in _job(workflow, PUBLISH_JOB)["steps"]]
    assert publish_steps.index(STATE_STEP) < publish_steps.index(UPLOAD_STEP)
    # 复用 draft 时先清空其旧资产，保证后续上传不覆盖旧字节且最终集合精确
    assert "/releases/${release_id}/assets" in run
    assert "--method DELETE" in run
    assert "release_id=${release_id}" in run


def test_draft_is_created_unpublished_then_published_exactly_once(workflow: dict[str, Any]) -> None:
    upload = _step(workflow, PUBLISH_JOB, UPLOAD_STEP)["run"]
    assert "-F draft=true" in upload
    assert "-F generate_release_notes=true" in upload
    # 上传端点必须来自 Release 对象的 upload_url（普通 API 的 assets 路径不能替代）。
    assert ".upload_url" in upload
    assert 'upload_url="${upload_url%%\\{*}"' in upload, "upload_url 必须剥离 URI template"
    assert "gh release upload" in upload
    assert "--clobber" not in upload
    assert "draft=false" not in upload

    remote_verify = _step(workflow, PUBLISH_JOB, REMOTE_VERIFY_STEP)["run"]
    assert 'if [ "$(gh api "repos/${GH_REPO}/releases/${release_id}" --jq \'.draft\')" != "true" ]' in remote_verify
    assert "sha256:" in remote_verify
    assert "remote_digest" in remote_verify
    assert 'if [ "$actual_count" -ne 12 ]' in remote_verify
    assert 'echo "release_id=${release_id}" >> "$GITHUB_OUTPUT"' in remote_verify

    publish_step = _step(workflow, PUBLISH_JOB, PUBLISH_STEP)
    publish = publish_step["run"]
    assert "-F draft=false" in publish
    assert "-F make_latest=true" in publish
    assert "steps.verify-draft.outputs.release_id" in publish_step["env"]["RELEASE_ID"]
    assert "draft=false" not in yaml.safe_dump(_job(workflow, PUBLISH_JOB)["steps"][:4], allow_unicode=True), (
        "draft=false 只能出现在最后一步 publish"
    )


def test_upload_step_fetches_and_validates_upload_url_in_both_paths(workflow: dict[str, Any]) -> None:
    upload = _step(workflow, PUBLISH_JOB, UPLOAD_STEP)["run"]
    # 新建 draft：创建响应本身即 Release 对象；复用 draft：再次 GET 该 Release。
    assert "release_json=release-assets/draft.json" in upload
    assert "release_json=release-assets/reused-draft.json" in upload
    assert 'gh api "repos/${GH_REPO}/releases/${RELEASE_ID}" > "$release_json"' in upload
    assert '"$(jq -r \'.draft // false\' "$release_json")" != "true"' in upload
    # 两条分支汇合后统一读取 upload_url，并 fail-closed 校验。
    assert 'upload_url=$(jq -r \'.upload_url // ""\' "$release_json")' in upload
    assert "has no upload_url" in upload
    assert "^https?://[^/]+/" in upload, "upload_url 必须是带主机的绝对 URL"
    assert '"/repos/${GH_REPO}/releases/${RELEASE_ID}/assets"' in upload, "upload_url 必须指向本仓库本 Release"
    assert 'api_base="${GITHUB_API_URL:-https://api.github.com}"' in upload, "不得硬编码单一域名"
    assert 'api_base="${api_base%/}"' in upload
    assert "refusing to use the API assets endpoint" in upload
    # 回归防线：上传调用不得再直接使用相对 API assets 路径 / 二进制 body 参数。
    assert "--input" not in upload
    assert "gh api --method POST" in upload  # 仅保留创建 draft 的 POST
    assert 'gh release upload "$TAG" "${files[@]}" --repo "$GH_REPO"' in upload


def test_remote_verify_checks_exact_asset_set_and_server_digest(workflow: dict[str, Any]) -> None:
    run = _step(workflow, PUBLISH_JOB, REMOTE_VERIFY_STEP)["run"]
    for row in PLATFORM_ROWS:
        _, target, artifact = row.split("|")
        assert artifact in run
        assert f"sha256sums-{target}.txt" in run
    assert '.assets[] | [.name, (.digest // "")] | @tsv' in run
    assert "actual_count" in run or "expected_count" in run
    assert 'sha256sum "release-assets/${artifact}/${file}"' in run


def test_coverage_report_path_joins_run_working_directory_to_upload_path(workflow: dict[str, Any]) -> None:
    """CI-F06：cargo llvm-cov 报告写入 run 工作目录，upload-artifact 却按仓库根解析。

    生成路径 = ``defaults.run.working-directory`` + ``--output-path``；消费路径 =
    upload-artifact 的 ``path``（相对 GITHUB_WORKSPACE）。两者必须精确相等，否则
    报告落在子目录而上传从仓库根读取，门禁会「成功」却缺少覆盖率证据。
    """
    working_directory = _job(workflow, LINT_JOB)["defaults"]["run"]["working-directory"]

    coverage_run = _step(workflow, LINT_JOB, LLVM_COV_STEP)["run"]
    output_match = re.search(r"--output-path(?:[= ])(\S+)", coverage_run)
    assert output_match is not None, "cargo llvm-cov 必须显式指定 --output-path"
    output_path = output_match.group(1)

    upload = _step(workflow, LINT_JOB, COVERAGE_UPLOAD_STEP)
    upload_paths = [p.strip() for p in upload["with"]["path"].strip().splitlines()]

    # 断言精确路径而非子串：若回退为仓库根的 ``lcov.info``，此比较会失败
    assert upload_paths == [str(PurePosixPath(working_directory) / output_path)]
    assert upload["with"]["if-no-files-found"] == "error", "报告缺失必须使上传校验失败"
    assert upload["with"]["name"] == "rust-coverage-lcov"
    assert upload["with"]["retention-days"] == 14
