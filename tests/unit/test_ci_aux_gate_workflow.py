"""CI-F05：独立补偿检查稳定门禁 `CI Aux Gate` 的契约测试。

覆盖：
- 门禁 workflow 的触发/权限/并发与稳定聚合 check；
- sidecar / precommit workflow 从直接 `pull_request` 迁移到 `workflow_call`；
- 变更选择：rename 的 `filename` 与 `previous_filename` 取并集（D26）；
- 变更探测的路径模式、3000 条目计数口径与 fail-closed 分支；
- 聚合判定对 success / skipped / failure / cancelled 的语义；
- 文档同步（check 名称、脚本索引、required 注册责任）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from scripts.classify_aux_changed_paths import (
    PRECOMMIT_PATTERNS,
    SIDECAR_PATTERNS,
    classify,
    collect_paths,
    main,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / ".github" / "workflows" / "ci-aux-gate.yml"
SIDECAR_PATH = REPO_ROOT / ".github" / "workflows" / "sidecar.yml"
PRECOMMIT_PATH = REPO_ROOT / ".github" / "workflows" / "precommit-config-ci.yml"
CI_CD_DOC_PATH = REPO_ROOT / "docs" / "guides" / "ci-cd.md"


def _load_yaml(path: Path) -> dict:
    # BaseLoader 保留 `on` 字面键（safe_load 会把 YAML 1.1 的 on 解析为布尔 True）。
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _api_pair_lines(entries: list[dict[str, str]]) -> str:
    """模拟 workflow 的 jq 投影：``[filename, previous_filename // ""] | @tsv``。

    测试 fixture 的路径不含 TAB / 换行，故无需复现 jq ``@tsv`` 的字段内转义。
    """
    return "".join(f"{entry['filename']}\t{entry.get('previous_filename', '')}\n" for entry in entries)


def _classify_entries(entries: list[dict[str, str]]) -> tuple[bool, bool]:
    return classify(collect_paths(_api_pair_lines(entries).splitlines()))


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


@pytest.fixture(scope="module")
def detect_run(gate_jobs: dict) -> str:
    return gate_jobs["detect"]["steps"][-1]["run"]


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

    def test_pr_paths_come_from_paginated_api(self, detect_run: str) -> None:
        assert "gh api --paginate" in detect_run
        assert "pulls/$PR_NUMBER/files" in detect_run
        assert "set +e" in detect_run

    def test_pr_query_keeps_both_rename_paths(self, detect_run: str) -> None:
        # D26：只取新路径会让「sidecar / 配置移出匹配路径」的 rename 漏选补偿检查。
        assert ".filename" in detect_run
        assert ".previous_filename" in detect_run
        assert "@tsv" in detect_run, "两列必须以 TAB 分隔输出，供选择器拆列"
        assert "--jq '.[].filename'" not in detect_run, "不得退回只取新路径的旧表达式"

    def test_selector_script_receives_both_columns(self, detect_run: str) -> None:
        assert 'python scripts/classify_aux_changed_paths.py < "$files"' in detect_run
        assert "s/^sidecar=//p" in detect_run
        assert "s/^precommit=//p" in detect_run

    def test_file_count_is_api_entry_lines(self, detect_run: str) -> None:
        # 3000 上限按 API 条目计数：每文件一行两列，重命名不使行数翻倍。
        count_line = 'count="$(grep -c . "$files" || true)"'
        assert count_line in detect_run
        assert 'if [ "$count" -ge 3000 ]' in detect_run
        assert detect_run.index(count_line) < detect_run.index('classify_file "$files"')

    def test_merge_group_preserves_both_rename_ends(self, detect_run: str) -> None:
        # --no-renames 把 rename 拆成「删除旧路径 + 新增新路径」，两端都进入选择器。
        assert 'git diff --no-renames --name-only "$BASE_SHA" "$HEAD_SHA"' in detect_run

    def test_merge_group_uses_base_and_head_sha(self, gate_jobs: dict, detect_run: str) -> None:
        env = gate_jobs["detect"]["steps"][-1]["env"]
        assert env["BASE_SHA"] == "${{ github.event.merge_group.base_sha }}"
        assert env["HEAD_SHA"] == "${{ github.event.merge_group.head_sha }}"
        checkout = [
            step for step in gate_jobs["detect"]["steps"] if step.get("if") == "github.event_name == 'merge_group'"
        ]
        assert len(checkout) == 1
        assert checkout[0]["with"]["fetch-depth"] == "0"

    def test_pr_checkout_provides_selector_script(self, gate_jobs: dict) -> None:
        checkouts = [
            step for step in gate_jobs["detect"]["steps"] if step.get("if") == "github.event_name == 'pull_request'"
        ]
        assert len(checkouts) == 1
        assert checkouts[0]["uses"].startswith("actions/checkout@")

    def test_detect_is_fail_closed(self, detect_run: str) -> None:
        # 所有无法确定变更的场景都必须落到「两者都跑」，而不是跳过。
        assert "PR 变更文件 API 读取失败" in detect_run
        assert "达到 API 分页上限 3000" in detect_run
        assert 'count" -ge 3000' in detect_run
        assert "辅助变更选择器执行失败" in detect_run
        assert "merge_group 缺少 base/head SHA" in detect_run
        assert "merge_group git diff 失败" in detect_run
        assert "workflow_dispatch" in detect_run
        assert "未知事件" in detect_run
        assert 'echo "sidecar=true" >> "$GITHUB_OUTPUT"' in detect_run
        assert 'echo "precommit=true" >> "$GITHUB_OUTPUT"' in detect_run


class TestAuxPathSelection:
    """D26：rename / 删除 / delete+add 的两端都必须参与辅助检查选择。"""

    @pytest.mark.parametrize(
        ("previous_filename", "filename", "expected"),
        [
            (
                "sidecars/qtrading-pg-sidecar/src/main.rs",
                "docs/main.rs",
                (True, False),
            ),
            (
                "docs/main.rs",
                "sidecars/qtrading-pg-sidecar/src/main.rs",
                (True, False),
            ),
            (".github/workflows/sidecar.yml", "docs/sidecar.yml", (True, False)),
            (".github/workflows/ci-aux-gate.yml", "docs/aux-gate.yml", (True, True)),
            (".pre-commit-config.yaml", "config/pre-commit.yaml", (False, True)),
            ("config/pre-commit.yaml", ".pre-commit-config.yaml", (False, True)),
            (".github/workflows/precommit-config-ci.yml", "docs/precommit.yml", (False, True)),
            ("docs/a.md", "docs/b.md", (False, False)),
        ],
        ids=[
            "sidecar-to-docs",
            "docs-to-sidecar",
            "sidecar-workflow-to-docs",
            "aux-gate-workflow-to-docs",
            "precommit-config-to-dir",
            "dir-to-precommit-config",
            "precommit-workflow-to-docs",
            "doc-to-doc",
        ],
    )
    def test_renamed_paths_are_unioned(
        self, previous_filename: str, filename: str, expected: tuple[bool, bool]
    ) -> None:
        entries = [{"status": "renamed", "filename": filename, "previous_filename": previous_filename}]
        assert _classify_entries(entries) == expected

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ({"status": "removed", "filename": "sidecars/qtrading-pg-sidecar/src/main.rs"}, (True, False)),
            ({"status": "removed", "filename": ".github/workflows/sidecar.yml"}, (True, False)),
            ({"status": "removed", "filename": ".pre-commit-config.yaml"}, (False, True)),
            ({"status": "removed", "filename": "docs/removed.md"}, (False, False)),
        ],
        ids=["sidecar-delete", "sidecar-workflow-delete", "precommit-delete", "doc-delete"],
    )
    def test_plain_deletions_still_select(self, entry: dict[str, str], expected: tuple[bool, bool]) -> None:
        assert _classify_entries([entry]) == expected

    def test_delete_add_split_keeps_sidecar_old_end(self) -> None:
        entries = [
            {"status": "removed", "filename": "sidecars/qtrading-pg-sidecar/src/main.rs"},
            {"status": "added", "filename": "docs/main.rs"},
        ]
        assert _classify_entries(entries) == (True, False)

    def test_delete_add_split_keeps_precommit_old_end(self) -> None:
        entries = [
            {"status": "removed", "filename": ".pre-commit-config.yaml"},
            {"status": "added", "filename": "config/pre-commit.yaml"},
        ]
        assert _classify_entries(entries) == (False, True)

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ({"status": "renamed", "filename": "docs/only.md"}, (False, False)),
            ({"status": "renamed", "filename": "docs/only.md", "previous_filename": ""}, (False, False)),
            ({"status": "renamed", "filename": "sidecars/qtrading-pg-sidecar/src/main.rs"}, (True, False)),
        ],
        ids=["missing-previous", "empty-previous", "missing-previous-sidecar"],
    )
    def test_missing_previous_filename_falls_back_to_filename(
        self, entry: dict[str, str], expected: tuple[bool, bool]
    ) -> None:
        assert _classify_entries([entry]) == expected

    def test_modified_sidecar_file_is_selected(self) -> None:
        assert _classify_entries([{"status": "modified", "filename": "sidecars/qtrading-pg-sidecar/src/main.rs"}]) == (
            True,
            False,
        )

    def test_empty_change_set_selects_nothing(self) -> None:
        assert classify([]) == (False, False)


class TestAuxPathCollection:
    """并集与去重：同一路径只计一次，双列顺序为 filename 在前。"""

    def test_collects_filename_before_previous_filename(self) -> None:
        assert collect_paths(["docs/main.rs\tsidecars/qtrading-pg-sidecar/src/main.rs\n"]) == [
            "docs/main.rs",
            "sidecars/qtrading-pg-sidecar/src/main.rs",
        ]

    def test_deduplicates_repeated_paths(self) -> None:
        assert collect_paths(["docs/a.md\tdocs/a.md\n", "docs/a.md\n"]) == ["docs/a.md"]

    def test_ignores_blank_lines_and_empty_columns(self) -> None:
        assert collect_paths(["\n", "  \n", "docs/a.md\t\n", "\t.pre-commit-config.yaml\n"]) == [
            "docs/a.md",
            ".pre-commit-config.yaml",
        ]

    def test_accepts_plain_newline_paths_for_merge_group(self) -> None:
        assert collect_paths(["docs/a.md\n", "sidecars/qtrading-pg-sidecar/src/main.rs\n"]) == [
            "docs/a.md",
            "sidecars/qtrading-pg-sidecar/src/main.rs",
        ]

    def test_pair_line_count_matches_api_entry_count(self) -> None:
        # 3000 上限判断依赖「一文件一行」：rename 不使行数翻倍。
        entries = [
            {"status": "modified", "filename": "docs/a.md"},
            {"status": "renamed", "filename": "docs/b.md", "previous_filename": "sidecars/x/main.rs"},
            {"status": "added", "filename": "docs/c.md"},
        ]
        assert len(_api_pair_lines(entries).splitlines()) == len(entries)


class TestAuxSelectorCli:
    def test_outputs_both_flags_for_pr_tsv(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entries = [
            {
                "status": "renamed",
                "filename": "docs/main.rs",
                "previous_filename": "sidecars/qtrading-pg-sidecar/src/main.rs",
            }
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines(entries)))
        assert main() == 0
        assert capsys.readouterr().out.splitlines() == ["sidecar=true", "precommit=false"]

    def test_outputs_flags_for_plain_paths(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("docs/a.md\n.pre-commit-config.yaml\n"))
        assert main() == 0
        assert capsys.readouterr().out.splitlines() == ["sidecar=false", "precommit=true"]

    def test_empty_stdin_outputs_false_flags(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert main() == 0
        assert capsys.readouterr().out.splitlines() == ["sidecar=false", "precommit=false"]

    def test_patterns_are_non_empty_tuples(self) -> None:
        assert isinstance(SIDECAR_PATTERNS, tuple)
        assert isinstance(PRECOMMIT_PATTERNS, tuple)
        assert len(SIDECAR_PATTERNS) > 0
        assert len(PRECOMMIT_PATTERNS) > 0
        # 门禁自身变更必须同时触发两项补偿检查。
        assert ".github/workflows/ci-aux-gate.yml" in SIDECAR_PATTERNS
        assert ".github/workflows/ci-aux-gate.yml" in PRECOMMIT_PATTERNS


class TestAggregateGate:
    def test_stable_required_check_name(self, gate_jobs: dict) -> None:
        aggregate = gate_jobs["ci-aux-gate"]
        assert aggregate["name"] == "CI Aux Gate"

    def test_needs_all_auxiliary_results_and_always_runs(self, gate_jobs: dict) -> None:
        aggregate = gate_jobs["ci-aux-gate"]
        assert aggregate["needs"] == ["detect", "ci-meta-contract", "sidecar-check", "precommit-check"]
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


class TestMetaContractJob:
    """D22：独立必达元检查必须无条件运行并被聚合判定强制。"""

    def test_meta_contract_job_runs_the_script(self, gate_jobs: dict) -> None:
        job = gate_jobs["ci-meta-contract"]
        assert job["name"] == "Auxiliary check: CI meta contract"
        # 不做路径过滤、不依赖 detect：每次事件都必须执行。
        assert "if" not in job
        runs = "\n".join(step["run"] for step in job["steps"] if "run" in step)
        assert "python scripts/check_ci_meta_contract.py" in runs
        assert job["permissions"] == {"contents": "read"}

    def test_meta_result_is_mandatory_in_aggregate(self, gate_jobs: dict) -> None:
        aggregate = gate_jobs["ci-aux-gate"]
        assert "ci-meta-contract" in aggregate["needs"]
        run_step = aggregate["steps"][-1]
        assert run_step["env"]["META_RESULT"] == "${{ needs.ci-meta-contract.result }}"
        assert '"$META_RESULT" != "success"' in run_step["run"]


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

    def test_docs_register_aux_selector_script(self) -> None:
        docs = CI_CD_DOC_PATH.read_text(encoding="utf-8")
        assert "scripts/classify_aux_changed_paths.py" in docs
