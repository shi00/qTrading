import subprocess
import sys
from pathlib import Path

# 将项目根目录添加到系统路径以导入 scripts 下的模块
sys.path.append(str(Path(__file__).parent.parent.parent))

from scripts.safe_cleanup_branches import (  # noqa: E402 - sys.path 注入后导入
    RemoteBranchCandidate,
    filter_branches,
    get_merged_local_branches,
    get_merged_remote_branches,
    get_merged_remote_candidates,
    main,
)

_OID_A = "a" * 40
_OID_B = "b" * 40


class FakeGit:
    """以内存状态替代真实 git 调用，任何测试都不会 fetch 或删除远端分支。"""

    def __init__(
        self,
        *,
        fetch_ok=True,
        worktree_error=False,
        worktree_outputs=None,
        local_merged=None,
        merged_remote_refs=None,
        tips=None,
        ancestor_ok=True,
        push_results=None,
        ls_remote_error=False,
        branch_delete_ok=True,
    ):
        self.fetch_ok = fetch_ok
        self.worktree_error = worktree_error
        self.ls_remote_error = ls_remote_error
        self.branch_delete_ok = branch_delete_ok
        self.worktree_outputs = list(worktree_outputs or ["* main\n"])
        self.worktree_calls = 0
        self.local_merged = list(local_merged or [])
        self.merged_remote_refs = list(merged_remote_refs or [])
        self.tips = dict(tips or {})
        self.ancestor_ok = ancestor_ok
        self.push_results = list(push_results or [])
        self.run_calls = []
        self.push_calls = []
        self.query_calls = []

    def _next_worktree_output(self):
        index = min(self.worktree_calls, len(self.worktree_outputs) - 1)
        self.worktree_calls += 1
        return self.worktree_outputs[index]

    def check_output(self, cmd, text=False, **kwargs):
        self.query_calls.append(list(cmd))
        subcommand = cmd[1]
        if subcommand == "worktree":
            if self.worktree_error:
                raise subprocess.CalledProcessError(1, cmd)
            return self._next_worktree_output()
        if subcommand == "branch":
            lines = ["* main"]
            lines.extend(f"  {branch}" for branch in self.local_merged)
            return "\n".join(lines) + "\n"
        if subcommand == "for-each-ref":
            return "".join(f"refs/remotes/{name}\t{oid}\n" for name, oid in self.merged_remote_refs)
        if subcommand == "ls-remote":
            if self.ls_remote_error:
                raise subprocess.CalledProcessError(1, cmd)
            remote = cmd[3]
            ref = cmd[4]
            branch = ref.removeprefix("refs/heads/")
            oid = self.tips.get((remote, branch))
            if oid is None:
                return ""
            return f"{oid}\t{ref}\n"
        if subcommand == "merge-base":
            if not self.ancestor_ok:
                raise subprocess.CalledProcessError(1, cmd)
            return ""
        raise AssertionError(f"未预期的查询命令: {cmd}")

    def run(self, cmd, check=True, **kwargs):
        self.run_calls.append(list(cmd))
        if cmd[1:3] == ["fetch", "-p"]:
            returncode = 0 if self.fetch_ok else 1
        elif cmd[1] == "push":
            self.push_calls.append(list(cmd))
            success = self.push_results.pop(0) if self.push_results else True
            returncode = 0 if success else 1
        elif cmd[1:3] == ["branch", "-d"]:
            returncode = 0 if self.branch_delete_ok else 1
        else:
            returncode = 0
        if returncode != 0 and check:
            raise subprocess.CalledProcessError(returncode, cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")


def _install_fake(monkeypatch, fake):
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(subprocess, "check_output", fake.check_output)


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


def test_filter_branches_protects_main_and_master_on_all_remotes():
    """D18-F05：main/master 保护必须对任意远端生效，而非仅 origin。"""
    remote_branches = ["origin/main", "origin/master", "upstream/main", "upstream/master", "fork/master"]

    to_delete_local, to_delete_remote, _, preserved_remote = filter_branches([], remote_branches, set())

    assert to_delete_local == []
    assert to_delete_remote == []
    assert preserved_remote == {
        "origin/main": "主分支保护",
        "origin/master": "主分支保护",
        "upstream/main": "主分支保护",
        "upstream/master": "主分支保护",
        "fork/master": "主分支保护",
    }


def test_filter_branches_protects_worktree_branch_on_all_remotes():
    """D18-F05：worktree 占用保护必须对任意远端生效，而非仅 origin。"""
    remote_branches = [
        "origin/fix/occupied",
        "upstream/fix/occupied",
        "origin/fix/free",
        "upstream/fix/free",
    ]
    worktree_branches = {"fix/occupied"}

    to_delete_local, to_delete_remote, _, preserved_remote = filter_branches([], remote_branches, worktree_branches)

    assert to_delete_local == []
    assert preserved_remote["origin/fix/occupied"] == "当前工作树(Worktree)占用"
    assert preserved_remote["upstream/fix/occupied"] == "当前工作树(Worktree)占用"
    assert to_delete_remote == ["origin/fix/free", "upstream/fix/free"]


def test_filter_branches_preserves_nested_branch_path_after_remote_prefix():
    """D18-F05：只剥离首个远端名，含 `/` 的分支名必须完整保留用于保护匹配。"""
    remote_branches = [
        "upstream/fix/deep/topic",
        "origin/fix/deep/topic",
        "upstream/feature/a/b",
    ]
    worktree_branches = {"fix/deep/topic"}

    _, to_delete_remote, _, preserved_remote = filter_branches([], remote_branches, worktree_branches)

    assert preserved_remote["upstream/fix/deep/topic"] == "当前工作树(Worktree)占用"
    assert preserved_remote["origin/fix/deep/topic"] == "当前工作树(Worktree)占用"
    assert to_delete_remote == ["upstream/feature/a/b"]


def test_filter_branches_does_not_protect_branch_whose_name_ends_with_main():
    """D18-F05：列保护按完整短名匹配，分支名里出现 main/master 不得被误保护。"""
    remote_branches = ["origin/upstream/main", "upstream/fix/main", "origin/fix/master"]

    _, to_delete_remote, _, preserved_remote = filter_branches([], remote_branches, set())

    assert preserved_remote == {}
    assert to_delete_remote == ["origin/upstream/main", "upstream/fix/main", "origin/fix/master"]


def test_filter_branches_accepts_refs_remotes_and_legacy_prefixes():
    """D18-F05：`refs/remotes/<remote>/<branch>` 与旧 `remotes/` 前缀均须正确归一化。"""
    remote_branches = [
        "refs/remotes/upstream/main",
        "remotes/upstream/master",
        "refs/remotes/upstream/fix/topic",
    ]

    _, to_delete_remote, _, preserved_remote = filter_branches([], remote_branches, set())

    assert preserved_remote == {
        "upstream/main": "主分支保护",
        "upstream/master": "主分支保护",
    }
    assert to_delete_remote == ["upstream/fix/topic"]


def test_merged_remote_candidates_parse_non_origin_remote(monkeypatch):
    """D18-F05：非 origin 远端的 ref 须解析出正确的 remote/含斜杠分支名。"""
    output = f"refs/remotes/upstream/main\t{_OID_A}\nrefs/remotes/upstream/fix/deep/topic\t{_OID_B}\n"

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    candidates = get_merged_remote_candidates("origin/main")
    assert candidates == [
        RemoteBranchCandidate(remote="upstream", branch="main", oid=_OID_A),
        RemoteBranchCandidate(remote="upstream", branch="fix/deep/topic", oid=_OID_B),
    ]


def test_main_never_deletes_non_origin_protected_branches(monkeypatch):
    """D18-F05：端到端确认 upstream 的 main 与 worktree 分支不被删除。"""
    fake = FakeGit(
        worktree_outputs=["/work/main [main]\n", "/work/main [main]\n/work/occupied [fix/occupied]\n"],
        merged_remote_refs=[
            ("upstream/main", _OID_A),
            ("upstream/fix/occupied", _OID_A),
            ("upstream/fix/topic", _OID_B),
        ],
        tips={("upstream", "fix/topic"): _OID_B},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 0
    assert fake.push_calls == [
        [
            "git",
            "push",
            f"--force-with-lease=refs/heads/fix/topic:{_OID_B}",
            "upstream",
            ":refs/heads/fix/topic",
        ]
    ]
    assert not [
        cmd
        for cmd in fake.query_calls
        if cmd[1] == "ls-remote" and cmd[3] == "upstream" and cmd[4] == "refs/heads/main"
    ]


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
    """`refs/remotes/<remote>/HEAD` 是符号引用，不得进入待删清单。"""
    output = (
        f"refs/remotes/origin/HEAD\t{_OID_A}\nrefs/remotes/origin/main\t{_OID_A}\n"
        f"refs/remotes/origin/fix/merged\t{_OID_B}\n"
    )

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    branches = get_merged_remote_branches("origin/main")
    assert branches == ["origin/main", "origin/fix/merged"]


def test_merged_remote_candidates_keep_remote_branch_and_oid(monkeypatch):
    """结构化候选须保留 remote / 含斜杠的分支名 / 枚举时观察到的 OID。"""
    output = (
        f"refs/remotes/origin/HEAD\t{_OID_A}\n"
        f"refs/remotes/origin/main\t{_OID_A}\n"
        f"refs/remotes/origin/fix/deep/topic\t{_OID_B}\n"
    )

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    candidates = get_merged_remote_candidates("origin/main")
    assert candidates == [
        RemoteBranchCandidate(remote="origin", branch="main", oid=_OID_A),
        RemoteBranchCandidate(remote="origin", branch="fix/deep/topic", oid=_OID_B),
    ]
    assert candidates[1].head_ref == "refs/heads/fix/deep/topic"


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
    """端到端：`origin/HEAD` 符号引用不得出现在远程待删清单。"""
    output = f"refs/remotes/origin/HEAD\t{_OID_A}\nrefs/remotes/origin/fix/merged\t{_OID_B}\n"

    def fake_check_output(*args, **kwargs):
        return output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    _, to_delete_remote, _, _ = filter_branches([], get_merged_remote_branches("origin/main"), set())
    assert "origin/HEAD" not in to_delete_remote
    assert to_delete_remote == ["origin/fix/merged"]


def test_main_aborts_without_deletions_when_fetch_fails(monkeypatch):
    """fetch 失败时不得使用缓存状态继续删除。"""
    fake = FakeGit(
        fetch_ok=False,
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert fake.run_calls == [["git", "fetch", "-p", "origin"]]
    assert fake.push_calls == []


def test_main_aborts_without_deletions_when_worktree_query_fails(monkeypatch):
    """worktree 查询失败不能被当作「无占用」；必须零删除中止。"""
    fake = FakeGit(
        worktree_error=True,
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert fake.run_calls == [["git", "fetch", "-p", "origin"]]
    assert fake.push_calls == []


def test_main_refuses_stale_remote_tip_and_deletes_nothing(monkeypatch):
    """预览时 A、确认前远端变为 B：必须拒绝删除且零成功删除。"""
    fake = FakeGit(
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_B},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert fake.push_calls == []
    assert [cmd for cmd in fake.run_calls if cmd[1] == "branch"] == []


def test_main_aborts_when_remote_tip_query_fails(monkeypatch):
    """确认后无法查询远端 tip 时必须零删除中止，不能猜测远端状态。"""
    fake = FakeGit(
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
        ls_remote_error=True,
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert fake.push_calls == []


def test_main_refuses_remote_tip_that_is_no_longer_merged(monkeypatch):
    """OID 未变但合并关系不再成立时，同样零删除中止。"""
    fake = FakeGit(
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
        ancestor_ok=False,
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert fake.push_calls == []


def test_main_deletes_stable_merged_remote_with_observed_lease(monkeypatch):
    """稳定已合并的 A 只删除预览中的 A，且 push 必须携带观察到的 OID lease。"""
    fake = FakeGit(
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 0
    assert fake.push_calls == [
        [
            "git",
            "push",
            f"--force-with-lease=refs/heads/fix/topic:{_OID_A}",
            "origin",
            ":refs/heads/fix/topic",
        ]
    ]
    assert [cmd for cmd in fake.run_calls if cmd[1] == "branch"] == [["git", "branch", "-d", "fix/local-merged"]]


def test_main_reports_partial_failure_when_local_branch_delete_fails(monkeypatch):
    """本地分支删除失败不得被报告为「清理完成」，但不应阻断已通过预检的远程删除。"""
    fake = FakeGit(
        local_merged=["fix/local-merged"],
        branch_delete_ok=False,
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert len(fake.push_calls) == 1


def test_main_does_not_delete_remote_branch_that_became_worktree_occupied(monkeypatch):
    """确认等待期间被 worktree 检出的远程分支不得删除。"""
    fake = FakeGit(
        worktree_outputs=["/work/main [main]\n", "/work/main [main]\n/work/occupied [fix/occupied]\n"],
        merged_remote_refs=[("origin/fix/occupied", _OID_A)],
        tips={("origin", "fix/occupied"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 0
    assert fake.push_calls == []


def test_main_stops_remote_cleanup_after_lease_failure(monkeypatch):
    """lease 失败必须停止并提示重新预览，不得继续删除后续分支。"""
    fake = FakeGit(
        merged_remote_refs=[("origin/fix/first", _OID_A), ("origin/fix/second", _OID_B)],
        tips={("origin", "fix/first"): _OID_A, ("origin", "fix/second"): _OID_B},
        push_results=[False],
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    assert main() == 1
    assert len(fake.push_calls) == 1
    assert "fix/first" in fake.push_calls[0][2]


def test_main_does_not_delete_when_input_is_rejected(monkeypatch):
    """非 yes 输入必须零删除。"""
    fake = FakeGit(
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")

    assert main() == 0
    assert fake.run_calls == [["git", "fetch", "-p", "origin"]]
    assert fake.push_calls == []


def test_main_does_not_delete_on_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt 必须零删除。"""
    fake = FakeGit(
        local_merged=["fix/local-merged"],
        merged_remote_refs=[("origin/fix/topic", _OID_A)],
        tips={("origin", "fix/topic"): _OID_A},
    )
    _install_fake(monkeypatch, fake)

    def raise_keyboard_interrupt(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_keyboard_interrupt)

    assert main() == 0
    assert fake.run_calls == [["git", "fetch", "-p", "origin"]]
    assert fake.push_calls == []
