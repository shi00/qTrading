import subprocess
import re
import sys
from dataclasses import dataclass

# `git branch` 在分支名前加标记：`*` 当前检出，`+` 被其他 worktree 检出（共享仓库时出现）。
# 标记后跟一个空格，因此仅剥离「标记 + 空白」，不误伤以 `*`/`+` 开头的合法分支名。
_BRANCH_MARKER_RE = re.compile(r"^[*+]\s+")

_LOCAL_BASE_BRANCH = "main"
_BASE_REMOTE = "origin"
_REMOTE_BASE_BRANCH = f"{_BASE_REMOTE}/main"
_REMOTE_REF_PREFIX = "refs/remotes/"
_REMOTE_DISPLAY_PREFIX = "remotes/"


class GitQueryError(RuntimeError):
    """git 状态查询失败；调用方必须中止删除，而不是把它当作空结果。"""


@dataclass(frozen=True)
class RemoteBranchCandidate:
    """枚举时捕获的远程分支候选，保留 remote/ref/OID 供后续 lease 校验。"""

    remote: str
    branch: str
    oid: str

    @property
    def display_name(self) -> str:
        return f"{self.remote}/{self.branch}"

    @property
    def head_ref(self) -> str:
        return f"refs/heads/{self.branch}"


def _git_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True)
    except (subprocess.SubprocessError, OSError) as e:
        raise GitQueryError(f"git {' '.join(args)} 执行失败: {e}") from e


def get_worktree_branches() -> set[str]:
    """获取当前所有 Git 工作树占用的本地分支集合。

    查询失败时抛出 ``GitQueryError``：空集合会被误解为「没有被占用」，
    从而把仍在使用中的分支列为可删候选。
    """
    try:
        output = subprocess.check_output(["git", "worktree", "list"], text=True)
    except (subprocess.SubprocessError, OSError) as e:
        raise GitQueryError(f"git worktree list 执行失败: {e}") from e

    # 匹配末尾的中括号，如 [branch-name]
    pattern = re.compile(r"\[([^\]]+)\]\s*$")
    branches = set()
    for line in output.splitlines():
        match = pattern.search(line.strip())
        if match:
            branches.add(match.group(1))
    return branches


def get_merged_local_branches(base_branch: str = "main") -> list[str]:
    """获取所有已合并到主分支的本地分支列表"""
    try:
        output = subprocess.check_output(["git", "branch", "--merged", base_branch], text=True)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Error executing git branch --merged: {e}", file=sys.stderr)
        return []

    branches = []
    for line in output.splitlines():
        # 剥离 `*`（当前检出）/ `+`（其他 worktree 检出）标记；否则被 worktree
        # 占用的分支名带 `+ ` 前缀，无法与 worktree_branches 匹配而被误列为可删。
        branch = _BRANCH_MARKER_RE.sub("", line.strip())
        if branch:
            branches.append(branch)
    return branches


def _strip_remote_prefix(ref: str) -> str:
    """去掉 ``refs/remotes/`` 或旧式 ``remotes/`` 前缀，返回 ``<remote>/<branch>``。"""
    for prefix in (_REMOTE_REF_PREFIX, _REMOTE_DISPLAY_PREFIX):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def _remote_branch_short_name(ref: str) -> str:
    """把任意远端引用归一化为本地分支短名。

    只剥离第一个路径段（远端名），保留分支名中的 ``/``（如 ``fix/deep/topic``），
    因此不依赖特定远端名，也不误伤含斜杠的合法分支。
    """
    remote_and_branch = _strip_remote_prefix(ref)
    _, separator, branch = remote_and_branch.partition("/")
    return branch if separator and branch else remote_and_branch


def _parse_remote_ref(ref: str, oid: str) -> RemoteBranchCandidate | None:
    if not ref.startswith(_REMOTE_REF_PREFIX):
        return None
    # `refs/remotes/origin/feature/x` -> remote=origin, branch=feature/x；
    # 只切第一个斜杠，避免含斜杠的分支名被截错。
    remote, separator, branch = _strip_remote_prefix(ref).partition("/")
    if not separator or not branch or branch == "HEAD":
        return None
    return RemoteBranchCandidate(remote=remote, branch=branch, oid=oid)


def get_merged_remote_candidates(base_branch: str = "origin/main") -> list[RemoteBranchCandidate]:
    """枚举已合并到 ``base_branch`` 的远程分支，并同时记录当时的 OID。"""
    output = _git_output(
        [
            "for-each-ref",
            f"--merged={base_branch}",
            "--format=%(refname)%09%(objectname)",
            "refs/remotes/",
        ]
    )
    candidates = []
    for line in output.splitlines():
        ref, separator, oid = line.partition("\t")
        if not separator or not ref or not oid:
            continue
        candidate = _parse_remote_ref(ref, oid)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def get_merged_remote_branches(base_branch: str = "origin/main") -> list[str]:
    """获取所有已合并到远程主分支的远程分支列表（兼容原字符串接口）。"""
    return [candidate.display_name for candidate in get_merged_remote_candidates(base_branch)]


def filter_branches(local_branches, remote_branches, worktree_branches):
    """
    根据安全规则过滤分支
    返回: to_delete_local, to_delete_remote, preserved_local, preserved_remote
    """
    protected_names = {"main", "master"}

    to_delete_local = []
    preserved_local = {}

    # 过滤本地分支
    for branch in local_branches:
        if branch in protected_names:
            preserved_local[branch] = "主分支保护"
        elif branch in worktree_branches:
            preserved_local[branch] = "当前工作树(Worktree)占用"
        else:
            to_delete_local.append(branch)

    # 如果有被 worktree 占用但在 local_branches 之外的分支，也登记保留状态
    for branch in worktree_branches:
        if branch not in preserved_local:
            if branch in protected_names:
                preserved_local[branch] = "主分支保护"
            else:
                preserved_local[branch] = "当前工作树(Worktree)占用"

    to_delete_remote = []
    preserved_remote = {}

    # 过滤远程分支
    for r_branch in remote_branches:
        # 保留展示名（去掉 refs/remotes/ 或 remotes/ 前缀），供删除与日志使用。
        display_name = _strip_remote_prefix(r_branch)
        # 归一化为本地分支短名（剥离首个远端名，保留分支名中的 "/"），
        # 使 main/master 与 worktree 保护对任意远端生效。
        local_equiv = _remote_branch_short_name(r_branch)

        # 保护主分支
        if local_equiv in protected_names:
            preserved_remote[display_name] = "主分支保护"
        # 保护被 worktree 本地占用的远程分支
        elif local_equiv in worktree_branches:
            preserved_remote[display_name] = "当前工作树(Worktree)占用"
        else:
            to_delete_remote.append(display_name)

    return to_delete_local, to_delete_remote, preserved_local, preserved_remote


def _remote_local_equiv(candidate: RemoteBranchCandidate) -> str:
    """远程候选对应的本地占用名，与 ``filter_branches`` 的保护口径保持一致。"""
    return candidate.branch


def get_remote_tip_oid(remote: str, branch: str) -> str | None:
    """直接查询远端当前 tip；分支已不存在时返回 ``None``。"""
    ref = f"refs/heads/{branch}"
    output = _git_output(["ls-remote", "--heads", remote, ref])
    for line in output.splitlines():
        oid, separator, found_ref = line.partition("\t")
        if separator and oid and found_ref == ref:
            return oid
    return None


def is_merged_into_base(oid: str, base_branch: str) -> bool:
    """确认 ``oid`` 仍可从 ``base_branch`` 到达；查询失败按「不能确认」处理。"""
    try:
        _git_output(["merge-base", "--is-ancestor", oid, base_branch])
    except GitQueryError:
        return False
    return True


def verify_remote_candidates(
    candidates: list[RemoteBranchCandidate], base_branch: str
) -> tuple[list[str], set[RemoteBranchCandidate]]:
    """确认前重新核验远端 tip 与合并关系。

    返回 ``(problems, already_gone)``；``problems`` 非空时调用方必须零删除中止。
    """
    problems = []
    already_gone = set()
    for candidate in candidates:
        tip = get_remote_tip_oid(candidate.remote, candidate.branch)
        if tip is None:
            already_gone.add(candidate)
        elif tip != candidate.oid:
            problems.append(f"{candidate.display_name}: 远端 tip 已从 {candidate.oid[:12]} 变为 {tip[:12]}")
        elif not is_merged_into_base(tip, base_branch):
            problems.append(f"{candidate.display_name}: 远端 tip 已不再合并到 {base_branch}")
    return problems, already_gone


def run_command(cmd):
    """运行 git 命令并检查返回值，同时将输出重定向到终端"""
    try:
        subprocess.run(cmd, check=True)
        return True
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"命令执行失败: {' '.join(cmd)} (错误: {e})", file=sys.stderr)
        return False


def delete_remote_branch(candidate: RemoteBranchCandidate) -> bool:
    """使用枚举时观察到的 OID 作为 lease 删除远程分支，绝不无前置条件删除。"""
    print(f"正在删除远程分支: {candidate.branch} 来自 {candidate.remote} (lease OID: {candidate.oid[:12]})")
    return run_command(
        [
            "git",
            "push",
            f"--force-with-lease={candidate.head_ref}:{candidate.oid}",
            candidate.remote,
            f":{candidate.head_ref}",
        ]
    )


def _abort(message: str, *, deletion_started: bool = False) -> int:
    print(f"错误: {message}", file=sys.stderr)
    if deletion_started:
        print("清理已部分执行，请核对上方输出；剩余分支需重新预览并再次确认。", file=sys.stderr)
    else:
        print("已中止清理，未执行任何删除。", file=sys.stderr)
    return 1


def main() -> int:
    print("=================== Git 分支安全清理程序 ===================")
    print(f"正在同步远程分支数据 (git fetch -p {_BASE_REMOTE})...")
    # 显式 fetch 基准远端：仅 fetch -p 可能同步当前分支的默认远端而非 origin，
    # 导致 origin/main 仍是旧缓存。
    if not run_command(["git", "fetch", "-p", _BASE_REMOTE]):
        return _abort("无法同步远程分支状态，拒绝使用可能过期的本地缓存继续删除。")

    print("正在搜集本地与远程分支状态...")
    try:
        worktree_branches = get_worktree_branches()
    except GitQueryError as e:
        return _abort(f"无法查询 worktree 占用状态: {e}")

    local_branches = get_merged_local_branches(_LOCAL_BASE_BRANCH)
    try:
        remote_candidates = get_merged_remote_candidates(_REMOTE_BASE_BRANCH)
    except GitQueryError as e:
        return _abort(f"无法枚举已合并的远程分支: {e}")
    remote_branches = [candidate.display_name for candidate in remote_candidates]

    to_delete_local, to_delete_remote, preserved_local, preserved_remote = filter_branches(
        local_branches, remote_branches, worktree_branches
    )

    # 打印保护分支预览
    print("\n🛡️  将被保留的分支 (不受清理影响):")
    if preserved_local:
        for branch, reason in sorted(preserved_local.items()):
            print(f"  [本地] {branch:<30} ({reason})")
    if preserved_remote:
        for branch, reason in sorted(preserved_remote.items()):
            print(f"  [远程] {branch:<30} ({reason})")

    # 打印准备删除的本地分支
    print("\n🗑️  准备删除的本地分支 (已合并至 main):")
    if to_delete_local:
        for branch in sorted(to_delete_local):
            print(f"  - {branch}")
    else:
        print("  (无)")

    # 打印准备删除的远程分支
    print("\n🗑️  准备删除的远程分支 (已合并至 origin/main):")
    if to_delete_remote:
        for branch in sorted(to_delete_remote):
            print(f"  - {branch}")
    else:
        print("  (无)")

    if not to_delete_local and not to_delete_remote:
        print("\n🎉 未发现可以安全清理的分支。")
        return 0

    print("\n" + "=" * 60)
    print("⚠️  警告：以上待删除的分支将被永久物理删除！")
    try:
        user_input = input("请输入完整的 'yes' 确认清理，输入其他任意内容取消: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n操作已被用户中止。")
        return 0

    if user_input.lower() != "yes":
        print("\n操作已取消，未做任何更改。")
        return 0

    # 等待确认期间远端或 worktree 状态都可能变化，删除前必须重新核验。
    try:
        worktree_after = get_worktree_branches()
    except GitQueryError as e:
        return _abort(f"确认后无法重新查询 worktree 占用状态: {e}")

    candidates_by_name: dict[str, RemoteBranchCandidate] = {
        candidate.display_name: candidate for candidate in remote_candidates
    }
    missing_candidates = [name for name in to_delete_remote if name not in candidates_by_name]
    if missing_candidates:
        return _abort(f"缺少 {missing_candidates} 的 OID 记录，无法安全删除。")

    candidates_to_delete = [candidates_by_name[name] for name in to_delete_remote]
    try:
        problems, already_gone = verify_remote_candidates(candidates_to_delete, _REMOTE_BASE_BRANCH)
    except GitQueryError as e:
        return _abort(f"无法重新核验远程分支状态: {e}")
    if problems:
        for problem in problems:
            print(f"安全校验失败: {problem}", file=sys.stderr)
        return _abort("远端分支在确认后已变化，请重新运行预览并再次确认（不会自动放宽条件）。")

    remote_to_delete = []
    for candidate in candidates_to_delete:
        if candidate in already_gone:
            print(f"跳过已不存在的远程分支: {candidate.display_name}")
        elif _remote_local_equiv(candidate) in worktree_after:
            print(f"跳过已被 worktree 占用的远程分支: {candidate.display_name}")
        else:
            remote_to_delete.append(candidate)

    # 等待期间新被 worktree 检出的本地分支同样不删除。
    local_to_delete = [branch for branch in to_delete_local if branch not in worktree_after]
    for branch in to_delete_local:
        if branch in worktree_after:
            print(f"跳过已被 worktree 占用的本地分支: {branch}")

    print("\n开始执行清理...")

    # 删除本地分支
    failed_local = []
    if local_to_delete:
        print("\n--- 正在清理本地分支 ---")
        for branch in local_to_delete:
            print(f"正在删除本地分支: {branch}")
            # 使用 -d 进行安全删除
            if not run_command(["git", "branch", "-d", branch]):
                failed_local.append(branch)

    # 删除远程分支
    if remote_to_delete:
        print("\n--- 正在清理远程分支 ---")
        for candidate in remote_to_delete:
            if not delete_remote_branch(candidate):
                return _abort(
                    "远程分支删除被拒绝（lease 校验失败或远端已变化），请重新运行预览并再次确认。",
                    deletion_started=True,
                )

    if failed_local:
        print(f"警告: 以下本地分支未能删除，请人工核对: {failed_local}", file=sys.stderr)
        return 1

    print("\n🎉 清理工作已完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
