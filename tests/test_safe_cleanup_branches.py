import subprocess
import sys
from pathlib import Path

# 将项目根目录添加到系统路径以导入 scripts 下的模块
sys.path.append(str(Path(__file__).parent.parent))

from scripts.safe_cleanup_branches import (  # noqa: E402 - sys.path 注入后导入
    filter_branches,
    get_merged_local_branches,
    get_merged_remote_branches,
)


def test_filter_branches_safety():
    # 模拟输入数据
    local_branches = [
        "main",
        "master",
        "fix/e2e-headless-db-ensure",  # 被 worktree 占用
        "fix/meta-review-2026-07-19",  # 已合并，安全可删
        "fix/weak-assertions-ci",  # 已合并，安全可删
    ]

    remote_branches = [
        "origin/main",
        "origin/master",
        "origin/fix/e2e-headless-db-ensure",  # 被 worktree 占用对应远程，需要保护
        "origin/fix/meta-review-2026-07-19",  # 已合并，安全可删
        "origin/feat/embedded-pg",  # 已合并，安全可删
    ]

    worktree_branches = {"fix/e2e-headless-db-ensure", "fix/e2e-seed-date-consistency"}

    # 执行过滤函数
    to_delete_local, to_delete_remote, preserved_local, preserved_remote = filter_branches(
        local_branches, remote_branches, worktree_branches
    )

    # 验证本地待删除分支
    assert "fix/meta-review-2026-07-19" in to_delete_local
    assert "fix/weak-assertions-ci" in to_delete_local
    assert "main" not in to_delete_local
    assert "master" not in to_delete_local
    assert "fix/e2e-headless-db-ensure" not in to_delete_local

    # 验证本地保留分支及其原因
    assert preserved_local["main"] == "主分支保护"
    assert preserved_local["master"] == "主分支保护"
    assert preserved_local["fix/e2e-headless-db-ensure"] == "当前工作树(Worktree)占用"

    # 验证远程待删除分支
    assert "origin/fix/meta-review-2026-07-19" in to_delete_remote
    assert "origin/feat/embedded-pg" in to_delete_remote
    assert "origin/main" not in to_delete_remote
    assert "origin/master" not in to_delete_remote
    assert "origin/fix/e2e-headless-db-ensure" not in to_delete_remote

    # 验证远程保留分支及其原因
    assert preserved_remote["origin/main"] == "主分支保护"
    assert preserved_remote["origin/master"] == "主分支保护"
    assert preserved_remote["origin/fix/e2e-headless-db-ensure"] == "当前工作树(Worktree)占用"


def test_merged_local_branches_strip_worktree_marker(monkeypatch):
    """`git branch --merged` 给其他 worktree 检出的分支加 `+ ` 前缀，解析须剥离。

    R18 常态多 worktree 下若保留 `+ `，分支名无法与 ``get_worktree_branches`` 的
    结果匹配，会被误列为「已合并可删」（git 虽会拒绝删除，但预览已误导）。
    """
    output = "* main\n+ fix/worktree-a\n  fix/merged\n"

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    branches = get_merged_local_branches("main")
    assert branches == ["main", "fix/worktree-a", "fix/merged"]


def test_merged_remote_branches_skip_symbolic_head(monkeypatch):
    """`git branch -r` 的 `origin/HEAD -> origin/main` 是符号引用，不得进入待删清单。"""
    output = "  origin/HEAD -> origin/main\n  origin/main\n  origin/fix/merged\n"

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    branches = get_merged_remote_branches("origin/main")
    assert branches == ["origin/main", "origin/fix/merged"]


def test_worktree_occupied_branch_not_deletable(monkeypatch):
    """端到端：带 `+ ` 标记的 worktree 占用分支不得出现在本地待删清单。"""

    def fake_check_output(*args, **kwargs):
        return "* main\n+ fix/worktree-a\n  fix/merged\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    to_delete_local, _, preserved_local, _ = filter_branches(get_merged_local_branches("main"), [], {"fix/worktree-a"})
    assert "fix/worktree-a" not in to_delete_local
    assert preserved_local["fix/worktree-a"] == "当前工作树(Worktree)占用"
    assert "fix/merged" in to_delete_local


def test_remote_symbolic_head_not_in_delete_list(monkeypatch):
    """端到端：`origin/HEAD -> origin/main` 不得出现在远程待删清单。"""

    def fake_check_output(*args, **kwargs):
        return "  origin/HEAD -> origin/main\n  origin/fix/merged\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    _, to_delete_remote, _, _ = filter_branches([], get_merged_remote_branches("origin/main"), set())
    assert "origin/HEAD -> origin/main" not in to_delete_remote
    assert to_delete_remote == ["origin/fix/merged"]
