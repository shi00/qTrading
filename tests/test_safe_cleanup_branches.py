import sys
from pathlib import Path

# 将项目根目录添加到系统路径以导入 scripts 下的模块
sys.path.append(str(Path(__file__).parent.parent))

from scripts.safe_cleanup_branches import filter_branches


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
