"""CI-F05：独立补偿检查稳定门禁 `CI Aux Gate` 的契约测试。

覆盖：
- 门禁 workflow 的触发/权限/并发与稳定聚合 check；
- sidecar / precommit workflow 从直接 `pull_request` 迁移到 `workflow_call`；
- 变更探测的路径模式与 fail-closed 分支；
- 聚合判定对 success / skipped / failure / cancelled 的语义；
- 文档同步（check 名称、required 注册责任）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / ".github" / "workflows" / "ci-aux-gate.yml"
SIDECAR_PATH = REPO_ROOT / ".github" / "workflows" / "sidecar.yml"
PRECOMMIT_PATH = REPO_ROOT / ".github" / "workflows" / "precommit-config-ci.yml"
CI_CD_DOC_PATH = REPO_ROOT / "docs" / "guides" / "ci-cd.md"


def _load_yaml(path: Path) -> dict:
    # BaseLoader 保留 `on` 字面键（safe_load 会把 YAML 1.1 的 on 解析为布尔 True）。
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.fixture(scope="module")
def gate() -> dict:
    return _load_yaml(GATE_PATH)


@pytest.fixture(scope="module")
def sidecar() -> dict:
    return _load_yaml(SIDECAR_PATH)


@pytest.fixture(scope="module")
def precommit() -> dict:
    return _load_yaml(PRECOMMIT_PATH)


@pytest.fixture(scope="module")
def gate_jobs(gate: dict) -> dict:
    return gate["jobs"]


class TestGateTriggers:
    def test_pull_request_is_enabled_without_paths_filter(self, gate: dict) -> None:
        triggers = gate["on"]
        assert "pull_request" in triggers
        assert "paths" not in (triggers["pull_request"] or {})

    def test_merge_group_and_workflow_dispatch_are_enabled(self, gate: dict) -> None:
        triggers = gate["on"]
        assert "merge_group" in triggers
        assert "workflow_dispatch" in triggers
        assert "paths" not in (triggers["merge_group"] or {})

    def test_least_privilege_permissions(self, gate: dict) -> None:
        permissions = gate["permissions"]
        assert permissions == {"contents": "read", "pull-requests": "read"}

    def test_concurrency_falls_back_to_ref_for_merge_group(self, gate: dict) -> None:
        concurrency = gate["concurrency"]
        assert "github.head_ref || github.ref" in concurrency["group"]
        assert concurrency["group"].startswith("ci-aux-gate-")


class TestReusableWorkflowMigration:
    def test_sidecar_no_longer_triggers_on_pull_request(self, sidecar: dict) -> None:
        triggers = sidecar["on"]
        assert "workflow_call" in triggers
        assert "pull_request" not in triggers
        # 既有发布语义必须保留。
        assert "push" in triggers
        assert "workflow_dispatch" in triggers
        assert "schedule" in triggers

    def test_precommit_no_longer_triggers_on_pull_request(self, precommit: dict) -> None:
        triggers = precommit["on"]
        assert "workflow_call" in triggers
        assert "pull_request" not in triggers
        assert "workflow_dispatch" in triggers

    def test_sidecar_lint_test_covers_merge_group(self, sidecar: dict) -> None:
        lint_if = sidecar["jobs"]["lint-test"]["if"]
        assert "github.event_name == 'merge_group'" in lint_if
        assert "github.event_name == 'pull_request'" in lint_if

    def test_sidecar_release_jobs_not_widened_to_merge_group(self, sidecar: dict) -> None:
        build_if = sidecar["jobs"]["build-artifacts"]["if"]
        assert "merge_group" not in build_if
        publish_if = sidecar["jobs"]["publish-release"]["if"]
        assert "merge_group" not in publish_if

    def test_gate_checks_use_local_reusable_workflows(self, gate_jobs: dict) -> None:
        assert gate_jobs["sidecar-check"]["uses"] == "./.github/workflows/sidecar.yml"
        assert gate_jobs["precommit-check"]["uses"] == "./.github/workflows/precommit-config-ci.yml"

    def test_sidecar_call_grant_meets_nested_release_permission(self, gate_jobs: dict) -> None:
        # sidecar.yml 的 publish-release 请求 contents: write，调用方上限低于它会导致
        # 整个 workflow startup_failure；write 仅被该 tag-only job 使用。
        assert gate_jobs["sidecar-check"]["permissions"] == {"contents": "write"}
        assert gate_jobs["precommit-check"]["permissions"] == {"contents": "read"}
        sidecar = _load_yaml(SIDECAR_PATH)
        assert sidecar["jobs"]["publish-release"]["permissions"] == {"contents": "write"}
        assert sidecar["permissions"] == {"contents": "read"}


class TestDetectJob:
    def test_outputs_drive_conditional_checks(self, gate_jobs: dict) -> None:
        detect = gate_jobs["detect"]
        assert detect["outputs"] == {
            "sidecar": "${{ steps.detect.outputs.sidecar }}",
            "precommit": "${{ steps.detect.outputs.precommit }}",
        }
        assert gate_jobs["sidecar-check"]["if"] == "needs.detect.outputs.sidecar == 'true'"
        assert gate_jobs["precommit-check"]["if"] == "needs.detect.outputs.precommit == 'true'"

    def test_pr_paths_come_from_paginated_api(self, gate_jobs: dict) -> None:
        detect_run = gate_jobs["detect"]["steps"][-1]["run"]
        assert "gh api --paginate" in detect_run
        assert "pulls/$PR_NUMBER/files" in detect_run
        assert "set +e" in detect_run

    def test_detect_matches_sidecar_and_precommit_paths(self, gate_jobs: dict) -> None:
        detect_run = gate_jobs["detect"]["steps"][-1]["run"]
        for pattern in (
            "sidecars/qtrading-pg-sidecar/",
            r"\.github/workflows/sidecar\.yml",
            r"\.pre-commit-config\.yaml",
            r"\.github/workflows/precommit-config-ci\.yml",
            r"\.github/workflows/ci-aux-gate\.yml",
        ):
            assert pattern in detect_run

    def test_merge_group_uses_base_and_head_sha(self, gate_jobs: dict) -> None:
        detect = gate_jobs["detect"]
        env = detect["steps"][-1]["env"]
        assert env["BASE_SHA"] == "${{ github.event.merge_group.base_sha }}"
        assert env["HEAD_SHA"] == "${{ github.event.merge_group.head_sha }}"
        assert 'git diff --name-only "$BASE_SHA" "$HEAD_SHA"' in detect["steps"][-1]["run"]
        checkout = detect["steps"][0]
        assert checkout["if"] == "github.event_name == 'merge_group'"
        assert checkout["with"]["fetch-depth"] == "0"

    def test_detect_is_fail_closed(self, gate_jobs: dict) -> None:
        detect_run = gate_jobs["detect"]["steps"][-1]["run"]
        # 所有无法确定变更的场景都必须落到「两者都跑」，而不是跳过。
        assert "PR 变更文件 API 读取失败" in detect_run
        assert "达到 API 分页上限 3000" in detect_run
        assert 'count" -ge 3000' in detect_run
        assert "merge_group 缺少 base/head SHA" in detect_run
        assert "merge_group git diff 失败" in detect_run
        assert "workflow_dispatch" in detect_run
        assert "未知事件" in detect_run
        assert 'echo "sidecar=true" >> "$GITHUB_OUTPUT"' in detect_run
        assert 'echo "precommit=true" >> "$GITHUB_OUTPUT"' in detect_run


class TestAggregateGate:
    def test_stable_required_check_name(self, gate_jobs: dict) -> None:
        aggregate = gate_jobs["ci-aux-gate"]
        assert aggregate["name"] == "CI Aux Gate"

    def test_needs_all_auxiliary_results_and_always_runs(self, gate_jobs: dict) -> None:
        aggregate = gate_jobs["ci-aux-gate"]
        assert aggregate["needs"] == ["detect", "sidecar-check", "precommit-check"]
        assert aggregate["if"] == "always()"

    def test_only_success_or_expected_skip_passes(self, gate_jobs: dict) -> None:
        aggregate_run = gate_jobs["ci-aux-gate"]["steps"][-1]["run"]
        assert '"$requested" = "true"' in aggregate_run
        assert '"$result" = "success"' in aggregate_run
        assert '"$requested" = "false"' in aggregate_run
        assert '"$result" = "skipped"' in aggregate_run

    def test_unexpected_results_fail_the_gate(self, gate_jobs: dict) -> None:
        aggregate_run = gate_jobs["ci-aux-gate"]["steps"][-1]["run"]
        assert 'if [ "$DETECT" != "success" ]' in aggregate_run
        assert "fail=1" in aggregate_run
        assert 'if [ "$fail" -ne 0 ]' in aggregate_run
        assert "exit 1" in aggregate_run
        assert "::error::CI Aux Gate 不通过" in aggregate_run
        assert '"$GITHUB_STEP_SUMMARY"' in aggregate_run

    def test_cancelled_or_failure_is_not_treated_as_skip(self, gate_jobs: dict) -> None:
        aggregate_run = gate_jobs["ci-aux-gate"]["steps"][-1]["run"]
        # 放行条件同时约束「需要」与「结果」：cancelled / failure / 非预期 skipped 都不会命中。
        assert 'if [ "$requested" = "true" ] && [ "$result" = "success" ]' in aggregate_run
        assert 'if [ "$requested" = "false" ] && [ "$result" = "skipped" ]' in aggregate_run
        assert "${result:-unknown}" in aggregate_run
        assert "return 1" in aggregate_run


class TestDocs:
    def test_docs_list_the_new_workflow(self) -> None:
        docs = CI_CD_DOC_PATH.read_text(encoding="utf-8")
        assert "ci-aux-gate.yml" in docs

    def test_docs_name_the_stable_required_check(self) -> None:
        docs = CI_CD_DOC_PATH.read_text(encoding="utf-8")
        assert "CI Aux Gate" in docs
        assert "main-required-checks" in docs

    def test_docs_assign_remote_registration_to_maintainer(self) -> None:
        docs = CI_CD_DOC_PATH.read_text(encoding="utf-8")
        assert "授权维护者" in docs
        assert "不修改远端 ruleset" in docs
