r"""D5：F21 触发器感知的 N5 执行证据与 workflow rename 幽灵方向（检视 §6.1 T5）。

F21：「触发/配置存在被当执行证据」——AST 名字/缩进子串不证明必达。
- 粗配置检查保留并明确范围（_extract_workflow_run_blocks 语义收窄为原始块提取）；
- 触发器补齐：`if: false` / `continue-on-error: true` 包裹的 run 块、`--collect-only`
  测试选择、`|| true` 吞失败，均**不构成** N5 的 pytest 执行证据（各有负例）；
- rename 幽灵方向：ci-cd.md 登记 `\.github/workflows/<name>.yml` 但文件不存在（rename
  后旧名残留）→ check_workflow_enum 报错（新旧路径并集语义：登记集合须与实际一致）。

范围声明（F21 不冒充执行）：触发器感知为缩进扫描近似，复合条件表达式按可达处理；
执行必达由真实 CI 运行层证明，粗配置检查只守护「声称的机制配置可达」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402

pytestmark = pytest.mark.docs_pure

_REDLINE_R2 = {
    "id": "R2",
    "enforcement": "CI-test（部分覆盖）",
    "automation_coverage": "partial",
    "human_review_required": True,
}


def _env(workflow_content: str) -> cdc.EnforcementEnvironment:
    return cdc.EnforcementEnvironment(
        precommit_content="",
        workflow_contents=(workflow_content,),
        pyproject_content="",
        check_redlines_script_exists=False,
        gitleaks_config_exists=False,
    )


class TestRunBlockTriggers:
    """触发器感知提取：禁用形态的 run 块不冒充执行证据。"""

    def test_normal_run_block_enabled(self):
        content = "jobs:\n  test:\n    steps:\n      - run: python -m pytest tests/unit/ -q\n"
        blocks = cdc._extract_run_blocks_with_triggers(content)
        assert [b.enabled for b in blocks] == [True]
        assert "pytest" in blocks[0].text

    def test_step_if_false_disabled(self):
        content = "jobs:\n  test:\n    steps:\n      - if: false\n        run: pytest tests/unit/ -q\n"
        blocks = cdc._extract_run_blocks_with_triggers(content)
        assert [b.enabled for b in blocks] == [False], "if: false 的 step run 块不得作为执行证据"

    def test_step_continue_on_error_disabled(self):
        content = "jobs:\n  test:\n    steps:\n      - continue-on-error: true\n        run: pytest tests/unit/ -q\n"
        blocks = cdc._extract_run_blocks_with_triggers(content)
        assert [b.enabled for b in blocks] == [False], "continue-on-error: true 的 run 块不得作为执行证据"

    def test_job_level_if_false_disables_all_steps(self):
        content = "jobs:\n  test:\n    if: false\n    steps:\n      - run: pytest tests/unit/ -q\n"
        blocks = cdc._extract_run_blocks_with_triggers(content)
        assert [b.enabled for b in blocks] == [False], "job 级 if: false 禁用其下全部 run 块"

    def test_backwards_compat_wrapper_returns_text_only(self):
        content = "jobs:\n  test:\n    steps:\n      - run: pytest -q\n"
        assert cdc._extract_workflow_run_blocks(content) == ["pytest -q"]


class TestN5TriggerAwareness:
    """N5 不变量：仅可达且真实执行的 pytest 命令构成 CI-test 证据。"""

    def test_disabled_evidence_alone_fails(self):
        content = "jobs:\n  test:\n    steps:\n      - if: false\n        run: pytest tests/unit/ -q\n"
        errors = cdc._check_enforcement_invariants([_REDLINE_R2], _env(content))
        assert any("N5" in e for e in errors), "唯一 pytest 证据被禁用时 N5 必须报错"

    def test_collect_only_is_not_evidence(self):
        content = "jobs:\n  test:\n    steps:\n      - run: pytest tests/unit/ --collect-only\n"
        errors = cdc._check_enforcement_invariants([_REDLINE_R2], _env(content))
        assert any("N5" in e for e in errors), "--collect-only 测试选择不构成执行证据（F21）"

    def test_swallowed_failure_is_not_evidence(self):
        content = "jobs:\n  test:\n    steps:\n      - run: pytest tests/unit/ || true\n"
        errors = cdc._check_enforcement_invariants([_REDLINE_R2], _env(content))
        assert any("N5" in e for e in errors), "|| true 吞失败的 pytest 命令不构成执行证据"

    def test_enabled_pytest_passes(self):
        content = "jobs:\n  test:\n    steps:\n      - run: python -m pytest tests/unit/ -q\n"
        errors = cdc._check_enforcement_invariants([_REDLINE_R2], _env(content))
        assert not any("N5" in e for e in errors), "正常可达 pytest 命令仍构成证据（正例不回退）"


class TestWorkflowEnumRenameGhost:
    """rename 新旧路径并集：文档登记的 workflow 文件必须实际存在（旧名残留即幽灵）。"""

    def test_ghost_registered_workflow_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci_cd.yml").write_text("on: push\n", encoding="utf-8")
        ci_cd = tmp_path / "ci-cd.md"
        ci_cd.write_text(
            "# CI\n\n- [CI](../.github/workflows/ci_cd.yml)\n"
            "- 旧流水线 ../.github/workflows/old-ci.yml 已 rename 但文档残留\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cdc, "WORKFLOWS_DIR", workflows)
        monkeypatch.setattr(cdc, "CI_CD_PATH", ci_cd)
        errors = cdc.check_workflow_enum()
        assert any("old-ci.yml" in e for e in errors), f"rename 后旧名残留须报幽灵: {errors}"
        assert not any("ci_cd.yml" in e for e in errors), "现名登记不得误报"

    def test_missing_ci_cd_doc_still_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci_cd.yml").write_text("on: push\n", encoding="utf-8")
        monkeypatch.setattr(cdc, "WORKFLOWS_DIR", workflows)
        monkeypatch.setattr(cdc, "CI_CD_PATH", tmp_path / "_absent" / "ci-cd.md")
        errors = cdc.check_workflow_enum()
        assert errors, "ci-cd.md 缺失必须显式失败（F17）"
