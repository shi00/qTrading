"""D22：CI 选择元检查的自身回证（真实 job 图 / 选择 / 条件 / 错误传播）。

``tests/docs/test_consistency.py`` 的 workflow 合同此前只断言触发、job 名称、
并发与权限；把 docs 验证 job 的 ``run`` 换成 ``echo`` 时 5 个用例仍全绿。本文件
对 ``scripts/check_ci_meta_contract.py`` 做正例（真实仓库）+ 反例（临时副本上构造
删除必需文件 / echo 替换 / 禁用 job / 吞失败 / 摘除自身接线）回证。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# 与 scripts/check_ci_meta_contract.py 的选择合同对应：验证 job 选中且必须存在的文件。
REQUIRED_FILES = (
    "scripts/check_docs_consistency.py",
    "tests/docs/pytest.ini",
    "tests/docs/test_consistency.py",
    "tests/unit/test_docs_consistency.py",
    "tests/unit/test_docs_template_symbols.py",
    "tests/unit/test_docs_accessibility_baseline_api.py",
)

Mutation = Callable[[Path, dict[str, Any], dict[str, Any]], None]


def _meta_module():
    if str(REPO_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_ci_meta_contract

    return check_ci_meta_contract


def _load_workflow(name: str) -> dict[str, Any]:
    # BaseLoader 保留 `on` 字面键（safe_load 会按 YAML 1.1 解析为布尔 True）。
    return yaml.load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _build_repo(tmp_path: Path, mutate: Mutation | None = None) -> Path:
    """复制真实 workflow + 选择目标文件到临时仓库，可选施加单变因。"""
    root = tmp_path / "repo"
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    docs_ci = _load_workflow("docs-ci.yml")
    aux_gate = _load_workflow("ci-aux-gate.yml")
    for rel in REQUIRED_FILES:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    if mutate is not None:
        mutate(root, docs_ci, aux_gate)
    for name, data in (("docs-ci.yml", docs_ci), ("ci-aux-gate.yml", aux_gate)):
        (wf_dir / name).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return root


class TestRealRepoPasses:
    def test_real_workflows_satisfy_meta_contract(self) -> None:
        errors = _meta_module().check_repo(REPO_ROOT)
        assert errors == []

    def test_cli_exits_zero_on_real_repo(self) -> None:
        assert _meta_module().main(["--root", str(REPO_ROOT)]) == 0


class TestNegativeMutations:
    def test_deleting_required_test_file_is_rejected(self, tmp_path: Path) -> None:
        root = _build_repo(
            tmp_path,
            lambda repo, _docs, _gate: (repo / "tests/unit/test_docs_template_symbols.py").unlink(),
        )
        errors = _meta_module().check_repo(root)
        assert any("必需文件缺失" in e and "test_docs_template_symbols.py" in e for e in errors), errors

    def test_echo_replacing_run_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, docs: dict[str, Any], _gate: dict[str, Any]) -> None:
            for job_id in ("docs-static", "docs-application-contracts"):
                for step in docs["jobs"][job_id]["steps"]:
                    if "run" in step:
                        step["run"] = "echo skipped"

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("空操作" in e for e in errors), errors
        assert any("未选择必需命令" in e for e in errors), errors

    def test_disabling_verification_job_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, docs: dict[str, Any], _gate: dict[str, Any]) -> None:
            docs["jobs"]["docs-static"]["if"] = "false"

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("被条件关闭" in e and "docs-static" in e for e in errors), errors

    def test_swallowing_failure_marker_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, docs: dict[str, Any], _gate: dict[str, Any]) -> None:
            step = docs["jobs"]["docs-application-contracts"]["steps"][-1]
            step["run"] = f"{step['run']}\n          || true"

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("吞失败标记" in e and "|| true" in e for e in errors), errors

    def test_continue_on_error_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, docs: dict[str, Any], _gate: dict[str, Any]) -> None:
            docs["jobs"]["docs-static"]["steps"][-1]["continue-on-error"] = "true"

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("continue-on-error" in e for e in errors), errors

    def test_removing_meta_job_from_aggregate_needs_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, _docs: dict[str, Any], gate: dict[str, Any]) -> None:
            aggregate = gate["jobs"]["ci-aux-gate"]
            aggregate["needs"] = [job for job in aggregate["needs"] if job != "ci-meta-contract"]

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("needs 未包含 `ci-meta-contract`" in e for e in errors), errors

    def test_missing_aggregate_job_is_rejected(self, tmp_path: Path) -> None:
        def mutate(_root: Path, docs: dict[str, Any], _gate: dict[str, Any]) -> None:
            del docs["jobs"]["docs-check"]

        errors = _meta_module().check_repo(_build_repo(tmp_path, mutate))
        assert any("缺少聚合 job `docs-check`" in e for e in errors), errors

    def test_cli_exits_nonzero_on_mutation(self, tmp_path: Path) -> None:
        root = _build_repo(tmp_path, lambda repo, _docs, _gate: (repo / "tests/docs/pytest.ini").unlink())
        assert _meta_module().main(["--root", str(root)]) == 1
