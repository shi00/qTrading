"""D18-A01：build-windows 的 sidecar Release 选择必须结构化、分页、共享且 fail-closed。

原实现解析 ``gh release list`` 人类表格的第一列（实际是 NAME 而非 TAG），两个 embedded
矩阵腿各自查询，且以 ``--limit 100`` 当全量。本测试守护：唯一 resolve job 经分页 REST API
读取全部 Release、用显式 JSON 过滤（精确 ``sidecar-v`` 前缀、排除 draft/prerelease）并稳定
排序取最新；两个 embedded 变体消费同一份输出；失败不静默、不把“未在前 100 项”当不存在。

新增依赖只落在 build-windows（其 needs 增加只读的 resolve job），CI Gate 的 required
job 集合不变；resolve 失败会让 build-windows 因 needs 未成功而跳过，进而 release-windows
跳过，保持 fail-closed。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci_cd.yml"

RESOLVE_JOB = "resolve-sidecar-release"
BUILD_JOB = "build-windows"
SELECT_STEP = "Select latest stable sidecar-v* release via paginated REST API"
DOWNLOAD_STEP = "Download sidecar binary (embedded variants only, Phase 5)"

# R04 后续会话需要的选择结果契约（tag / release 身份 / target commit / 资产名与摘要）。
R04_OUTPUTS = {
    "tag",
    "release_id",
    "target_commitish",
    "target_commit",
    "binary_asset_name",
    "binary_asset_digest",
    "sha256sums_asset_name",
    "sha256sums_asset_digest",
}


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """加载 CI workflow 的 YAML 结构（仅解析，不执行）。"""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def resolve_job(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow["jobs"][RESOLVE_JOB]


@pytest.fixture(scope="module")
def build_job(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow["jobs"][BUILD_JOB]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    """按 step name 获取 workflow step 定义。"""
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"job 缺少 step：{name}")


def test_selection_uses_paginated_rest_api_with_explicit_json(
    resolve_job: dict[str, Any],
) -> None:
    """选择必须分页读取 REST API 并显式解析 JSON，不解析人类表格、不以 limit 当全量。"""
    script = _step(resolve_job, SELECT_STEP)["run"]

    assert "gh api --paginate" in script, "未使用分页 REST API 读取 Release"
    assert "releases?per_page=100" in script, "未指定联系人可读的 releases 端点"
    assert "jq -s" in script, "未用 jq 显式合并/解析分页 JSON"
    assert "gh release list" not in script, "仍在使用 gh release list 人类输出"
    assert "--limit 100" not in script, "仍以 limit 当全量"


def test_workflow_no_longer_parses_human_readable_release_table() -> None:
    """整个 workflow 不再解析 gh 人类表格第一列（第一列是 NAME，不是 TAG）。"""
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "gh release list" not in text, "workflow 仍在使用 gh release list"
    assert '-split "`t"' not in text, "workflow 仍按制表符解析人类表格"


def test_selection_filters_exact_prefix_and_excludes_draft_pre_release(
    resolve_job: dict[str, Any],
) -> None:
    """精确 ``sidecar-vX.Y.Z`` 前缀过滤，排除 draft/prerelease，并稳定排序取最新。"""
    script = _step(resolve_job, SELECT_STEP)["run"]

    assert r"^sidecar-v[0-9]+[.][0-9]+[.][0-9]+$" in script, "未锚定 sidecar-v 精确版本前缀"
    assert "jq -r '.tag_name'" in script, "未按 tag_name（而非 release 标题）取值"
    assert ".draft == false" in script, "未排除 draft Release"
    assert ".prerelease == false" in script, "未排除 prerelease Release"
    assert 'capture("^sidecar-v(?<major>' in script, "未解析语义版本"
    assert "_version" in script, "未按数字版本排序（0.10.0 可能被 0.9.0 压过）"
    assert "sort_by([._version, .published_at, .tag_name])" in script, "排序键不稳定（缺少并列时的确定性）"
    assert "| last" in script, "未取排序后的最新一条"


def test_selection_fails_closed_on_api_error_and_empty_result(
    resolve_job: dict[str, Any],
) -> None:
    """API 失败或空结果必须显式报错退出，不回落“未在前 100 项=不存在”。"""
    script = _step(resolve_job, SELECT_STEP)["run"]

    assert "set -euo pipefail" in script
    assert 'if ! releases="$(gh api --paginate' in script, "gh api 失败未被显式捕获"
    assert "::error::failed to list releases" in script, "API 失败未给出显式错误"
    assert '[ "$selected" = "null" ]' in script, "空结果未显式判定为 null"
    assert "no stable sidecar-v* release found" in script, "空结果未 fail-closed"
    assert "|| true" not in script, "存在吞掉查询失败的静默回退"


def test_resolve_job_exposes_r04_contract_outputs(resolve_job: dict[str, Any]) -> None:
    """输出 tag、release id、target commitish/commit、资产名与摘要，供 R04 SHA 绑定。"""
    outputs = set(resolve_job["outputs"])
    assert outputs >= R04_OUTPUTS, f"缺少输出契约字段：{sorted(R04_OUTPUTS - outputs)}"

    script = _step(resolve_job, SELECT_STEP)["run"]
    for name in R04_OUTPUTS:
        assert f'echo "{name}=' in script, f"未写入 output：{name}"
    assert '>> "$GITHUB_OUTPUT"' in script, "未写入 GITHUB_OUTPUT"

    assert "commits/${tag}" in script, "未从 tag 解析 target commit"
    assert '--arg n "$binary_asset_name"' in script and '--arg n "$sha256sums_asset_name"' in script, (
        "未校验 Windows binary / sha256sums 资产存在"
    )


def test_resolve_job_is_read_only_and_tag_scoped(
    workflow: dict[str, Any], resolve_job: dict[str, Any], build_job: dict[str, Any]
) -> None:
    """resolve 与 build-windows 同触发范围，只读权限，在轻量 runner 上执行。"""
    assert resolve_job["if"] == build_job["if"], "resolve 与 build-windows 触发范围不一致"
    assert resolve_job["permissions"] == {"contents": "read"}, "resolve 不应有写权限"
    assert resolve_job["runs-on"] == "ubuntu-latest", "resolve 应使用轻量 Linux runner"
    assert resolve_job["timeout-minutes"] <= 5, "resolve 超时预算过大"

    # 只读 job 不排队等待质量 job，tag 推送时可尽早 fail-closed。
    assert "needs" not in resolve_job

    # CI Gate 的 required job 集合不变（resolve 仅约束 build-windows）。
    assert RESOLVE_JOB not in workflow["jobs"]["ci-gate"]["needs"]


def test_build_windows_needs_resolve_and_keeps_quality_gates(build_job: dict[str, Any]) -> None:
    """build-windows 依赖 resolve，但不移除既有质量 job；失败链条保持 fail-closed。"""
    assert RESOLVE_JOB in build_job["needs"], "build-windows 未依赖共享的 resolve 输出"
    assert {
        "ci-checks",
        "ci-checks-static",
        "ci-checks-db",
        "ci-checks-windows",
        "e2e-tests-windows",
        "embedded-tests",
    } <= set(build_job["needs"]), "既有质量门禁 needs 被移除"


def test_build_windows_download_consumes_shared_outputs(build_job: dict[str, Any]) -> None:
    """embed 下载步骤直接消费 resolve 输出，不再自行查询 Release。"""
    step = _step(build_job, DOWNLOAD_STEP)
    env = step["env"]
    assert env["SIDECAR_RELEASE_TAG"] == "${{ needs.resolve-sidecar-release.outputs.tag }}"
    assert env["SIDECAR_BINARY_ASSET"] == "${{ needs.resolve-sidecar-release.outputs.binary_asset_name }}"
    assert env["SIDECAR_SHA256SUMS_ASSET"] == "${{ needs.resolve-sidecar-release.outputs.sha256sums_asset_name }}"
    assert env["SIDECAR_TARGET_COMMIT"] == "${{ needs.resolve-sidecar-release.outputs.target_commit }}"

    script = step["run"]
    assert "$tagName = $env:SIDECAR_RELEASE_TAG" in script, "未使用共享 tag"
    assert "gh release download $tagName" in script, "未按选定 tag 下载"
    assert "if (-not $env:SIDECAR_RELEASE_TAG)" in script, "缺少空 tag 的 fail-closed 检查"
    assert "SHA256 mismatch" in script, "原有供应链校验被移除"


def test_two_embedded_legs_share_one_selection(build_job: dict[str, Any], resolve_job: dict[str, Any]) -> None:
    """两个 embedded 矩阵腿只有唯一次 Release 查询（在 resolve job），结果共享。"""
    matrix = build_job["strategy"]["matrix"]["include"]
    embedded = {item["variant"] for item in matrix if item["embedded"] == "true"}
    assert embedded == {"embedded-cpu", "embedded-cuda"}, "embedded 矩阵腿发生变化"

    resolve_script = _step(resolve_job, SELECT_STEP)["run"]
    assert resolve_script.count("gh api --paginate") == 1, "resolve job 应只查询一次"
    download_script = _step(build_job, DOWNLOAD_STEP)["run"]
    assert "gh api --paginate" not in download_script, "下载步骤仍在各自查询 Release"
