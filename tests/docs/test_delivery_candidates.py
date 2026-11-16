"""Q15：受检集 / 生成候选接入「暂存前显式交付候选」。

问题：``check_docs_consistency.py`` 的受检集（``_collect_checked_docs``）与
``docs_generated.py`` 的生成候选（``_docs_index_candidates``）原先只取 git 索引，
尚未 ``git add`` 的新增正式页在暂存前不可发现（正常 ``git add`` 后即受检，故不是
已证提交漏保护）。

修法：候选集 = git 索引（含已 staged 新增）∪ 调用方经 ``DOCS_DELIVERY_CANDIDATES``
显式申报的暂存前正式页；ignored 产物与未申报 untracked（草案）不升为候选。
验证四件：暂存前正式页可发现、草案不误入、ignored 产物排除、stage/rename/delete 集合准确。

N01：``_docs_index_candidates`` 的降级判据必须是 ``None``（git 查询失败/不可用），
不得用真值判断——git 查询成功但返回空集时保持空，不得回退磁盘扫描把 ignored/
未申报文件升为候选。三个方向：成功空集、成功非空集、None 降级。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402
import docs_generated as dg  # noqa: E402

pytestmark = pytest.mark.docs_pure

_ENV = "DOCS_DELIVERY_CANDIDATES"


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
    (repo / "CONTRIBUTING.md").write_text("# 贡献\n\n目录结构见 docs/README.md。\n", encoding="utf-8")
    (repo / ".gitignore").write_text("docs/drafts/\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-m", "init")

    monkeypatch.setattr(cdc, "ROOT", repo)
    monkeypatch.setattr(cdc, "DOCS_README_PATH", docs / "README.md")
    monkeypatch.setattr(cdc, "CONTRIBUTING_PATH", repo / "CONTRIBUTING.md")
    monkeypatch.setattr(cdc, "_GITIGNORED_ARTIFACT_DIRS", ())
    monkeypatch.setattr(cdc, "_LOCAL_ARTIFACT_DIRS", ())
    monkeypatch.delenv(_ENV, raising=False)
    return repo


def _checked_rels(repo: Path) -> set[str]:
    return {p.relative_to(repo).as_posix() for p in cdc._collect_checked_docs()}


def _indexed_rels(repo: Path) -> set[str]:
    return {p.relative_to(repo).as_posix() for p in dg._docs_index_candidates(repo)}


def test_declared_page_discovered_before_staging(git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """暂存前申报的新增正式页进入受检集与生成候选，并触发「未被索引引用」检出。"""
    (git_doc_repo / "docs" / "new-page.md").write_text("# 新页\n", encoding="utf-8")
    assert "docs/new-page.md" not in _checked_rels(git_doc_repo)
    assert "docs/new-page.md" not in _indexed_rels(git_doc_repo)

    monkeypatch.setenv(_ENV, os.pathsep.join(["docs/new-page.md", "./docs/indexed.md"]))
    assert "docs/new-page.md" in _checked_rels(git_doc_repo)
    assert "docs/new-page.md" in _indexed_rels(git_doc_repo)
    errors = cdc.check_docs_index_completeness()
    assert any("new-page.md" in e for e in errors), f"暂存前正式页漏登记必须可发现: {errors}"


def test_undeclared_untracked_draft_not_collected(git_doc_repo: Path):
    """未申报的 untracked 文件（草案）不升为候选，也不触发索引误报。"""
    (git_doc_repo / "docs" / "draft.md").write_text("# 草案\n", encoding="utf-8")
    assert "docs/draft.md" not in _checked_rels(git_doc_repo)
    assert "docs/draft.md" not in _indexed_rels(git_doc_repo)
    assert not any("draft.md" in e for e in cdc.check_docs_index_completeness())


def test_declared_ignored_artifact_excluded(git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """显式申报也不能把 .gitignore 产物升为候选。"""
    drafts = git_doc_repo / "docs" / "drafts"
    drafts.mkdir()
    (drafts / "note.md").write_text("# 本地产物\n", encoding="utf-8")
    monkeypatch.setenv(_ENV, "docs/drafts/note.md")
    assert "docs/drafts/note.md" not in _checked_rels(git_doc_repo)
    assert "docs/drafts/note.md" not in _indexed_rels(git_doc_repo)
    assert not any("note.md" in e for e in cdc.check_docs_index_completeness())


def test_stage_rename_delete_sets_are_accurate(git_doc_repo: Path):
    """stage / rename / delete 三种 git 操作后的候选集合准确（不需要环境申报）。"""
    docs = git_doc_repo / "docs"
    (docs / "staged.md").write_text("# staged\n", encoding="utf-8")
    _git(git_doc_repo, "add", "docs/staged.md")
    assert "docs/staged.md" in _indexed_rels(git_doc_repo)

    _git(git_doc_repo, "mv", "docs/indexed.md", "docs/renamed.md")
    renamed = _indexed_rels(git_doc_repo)
    assert "docs/renamed.md" in renamed
    assert "docs/indexed.md" not in renamed, f"rename 后旧路径不得残留: {renamed}"

    _git(git_doc_repo, "rm", "-f", "docs/staged.md")
    assert "docs/staged.md" not in _indexed_rels(git_doc_repo), "staged 删除后不得残留候选"


@pytest.fixture()
def git_repo_without_tracked_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """git 查询成功但 docs/** 无追踪文件的仓库（``git ls-files -- docs`` 返回空集）。"""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".gitignore").write_text("docs/artifacts/\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".gitignore")
    _git(repo, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-m", "init")
    monkeypatch.delenv(_ENV, raising=False)
    return repo


def test_empty_successful_git_result_stays_empty(git_repo_without_tracked_docs: Path):
    """成功空集必须保持空：ignored / 未申报 / 子目录未申报文件不得回退磁盘扫描升为候选。"""
    docs = git_repo_without_tracked_docs / "docs"
    (docs / "draft.md").write_text("# 未申报草案\n", encoding="utf-8")
    (docs / "artifacts").mkdir()
    (docs / "artifacts" / "note.md").write_text("# ignored 产物\n", encoding="utf-8")
    (docs / "nested").mkdir()
    (docs / "nested" / "deep.md").write_text("# 子目录未申报\n", encoding="utf-8")

    assert dg.git_delivery_files(git_repo_without_tracked_docs, "docs") == [], (
        "前置条件：git 查询成功且候选为空列表（区别于 None 降级）"
    )
    assert _indexed_rels(git_repo_without_tracked_docs) == set(), "成功空集不得回退磁盘扫描"


def test_non_empty_git_result_contains_only_tracked(git_doc_repo: Path):
    """成功非空集只含追踪文件：未申报草案与 ignored 产物不入候选。"""
    (git_doc_repo / "docs" / "draft.md").write_text("# 草案\n", encoding="utf-8")
    drafts = git_doc_repo / "docs" / "drafts"
    drafts.mkdir()
    (drafts / "note.md").write_text("# ignored 产物\n", encoding="utf-8")
    assert _indexed_rels(git_doc_repo) == {"docs/README.md", "docs/indexed.md"}


def test_none_git_result_falls_back_to_disk_scan(git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """git 查询失败（None）才允许降级磁盘扫描：未申报草案仍被文件系统发现。"""
    (git_doc_repo / "docs" / "draft.md").write_text("# 草案\n", encoding="utf-8")
    monkeypatch.setattr(dg, "_git_ls_files", lambda root, *args: None)
    rels = _indexed_rels(git_doc_repo)
    assert "docs/draft.md" in rels, "None 降级必须回退磁盘扫描"
    assert {"docs/README.md", "docs/indexed.md"} <= rels


def test_declared_escape_and_missing_paths_rejected(git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """越界 / 不存在 / 绝对路径的申报不升为候选，合法申报仍生效。"""
    (git_doc_repo.parent / "outside.md").write_text("# 仓库外\n", encoding="utf-8")
    (git_doc_repo / "docs" / "valid.md").write_text("# 合法申报\n", encoding="utf-8")
    monkeypatch.setenv(
        _ENV,
        os.pathsep.join(
            ["docs/valid.md", "docs/../docs/valid.md", "../outside.md", "docs/missing.md", str(Path("/etc/hosts"))]
        ),
    )
    candidates = _indexed_rels(git_doc_repo)
    assert "docs/valid.md" in candidates
    assert not any("outside" in rel or "missing" in rel or rel.endswith("hosts") for rel in candidates)
