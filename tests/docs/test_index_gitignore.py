"""D5：文档索引全覆盖候选集的 gitignored/未跟踪排除（检视开放问题 4）。

问题：check_docs_index_completeness 以 Path.rglob 枚举 docs/**，不识别 .gitignore——
未跟踪的本地产物目录（如 docs/plans/ 会话计划）会被误报「未被索引引用」，
造成真实仓库门禁假失败（与 _collect_checked_docs 的 H1 修法不一致）。

修法：候选集单一来源改为 git 跟踪文件（git ls-files，与 _collect_checked_docs 同源）；
git 不可用时降级文件系统发现 + 静态排除清单。负例三件：
- 未跟踪目录中的文件不再触发索引失败（本修复核心）；
- tracked 文件漏登记仍失败（不因修复漏检）；
- 索引入口缺失仍显式失败。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402

pytestmark = pytest.mark.docs_pure


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
    )


@pytest.fixture()
def git_doc_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """最小 git 仓库：docs/README.md（索引自引用）+ CONTRIBUTING.md + 已跟踪索引文档。"""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "README.md").write_text("# 索引\n\n- [indexed](./indexed.md)\n", encoding="utf-8")
    (docs / "indexed.md").write_text("# 已登记\n", encoding="utf-8")
    (repo / "CONTRIBUTING.md").write_text(
        "# 贡献\n\n目录结构见 docs/README.md（无链接，避免目录级覆盖干扰）。\n", encoding="utf-8"
    )
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-m", "init")

    monkeypatch.setattr(cdc, "ROOT", repo)
    monkeypatch.setattr(cdc, "DOCS_README_PATH", docs / "README.md")
    monkeypatch.setattr(cdc, "CONTRIBUTING_PATH", repo / "CONTRIBUTING.md")
    monkeypatch.setattr(cdc, "_GITIGNORED_ARTIFACT_DIRS", ())
    monkeypatch.setattr(cdc, "_LOCAL_ARTIFACT_DIRS", ())
    return repo


def test_untracked_dir_file_does_not_trigger(git_doc_repo: Path):
    """未跟踪目录（docs/plans/ 会话产物）文件不再触发「未被引用」失败。"""
    plans = git_doc_repo / "docs" / "plans"
    plans.mkdir()
    (plans / "scratch-session.md").write_text("# 本地会话计划\n", encoding="utf-8")
    errors = cdc.check_docs_index_completeness()
    assert not any("scratch-session.md" in e for e in errors), f"未跟踪产物被误报未登记: {errors}"
    assert errors == [], f"无其他候选时应完全通过: {errors}"


def test_tracked_file_missing_from_index_still_fails(git_doc_repo: Path):
    """tracked 文件漏登记仍失败（修复不得引入漏检）。"""
    (git_doc_repo / "docs" / "extra.md").write_text("# 新页\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    errors = cdc.check_docs_index_completeness()
    assert any("extra.md" in e for e in errors), f"tracked 漏登记必须失败: {errors}"


def test_tracked_yaml_candidate_covered_or_reported(git_doc_repo: Path):
    """候选集覆盖 .yml/.yaml/.json（DS-10 语义保持）：tracked 未登记 yml 报错。"""
    (git_doc_repo / "docs" / "governance.yml").write_text("key: value\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    errors = cdc.check_docs_index_completeness()
    assert any("governance.yml" in e for e in errors), f"tracked yml 漏登记必须失败: {errors}"


def test_missing_docs_readme_still_fails(git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """索引入口缺失仍显式失败（F17）。"""
    monkeypatch.setattr(cdc, "DOCS_README_PATH", git_doc_repo / "docs" / "_absent.md")
    errors = cdc.check_docs_index_completeness()
    assert errors, "索引入口缺失必须显式失败"
