import subprocess
import re
import sys


def get_worktree_branches():
    """获取当前所有 Git 工作树占用的本地分支集合"""
    try:
        output = subprocess.check_output(["git", "worktree", "list"], text=True)
    except subprocess.SubprocessError as e:
        print(f"Error executing git worktree list: {e}", file=sys.stderr)
        return set()

    # 匹配末尾的中括号，如 [branch-name]
    pattern = re.compile(r"\[([^\]]+)\]\s*$")
    branches = set()
    for line in output.splitlines():
        match = pattern.search(line.strip())
        if match:
            branches.add(match.group(1))
    return branches


def get_merged_local_branches(base_branch="main"):
    """获取所有已合并到主分支的本地分支列表"""
    try:
        output = subprocess.check_output(["git", "branch", "--merged", base_branch], text=True)
    except subprocess.SubprocessError as e:
        print(f"Error executing git branch --merged: {e}", file=sys.stderr)
        return []

    branches = []
    for line in output.splitlines():
        # 去掉 * 前缀和空白
        branch = line.replace("*", "").strip()
        if branch:
            branches.append(branch)
    return branches


def get_merged_remote_branches(base_branch="origin/main"):
    """获取所有已合并到远程主分支的远程分支列表"""
    try:
        output = subprocess.check_output(["git", "branch", "-r", "--merged", base_branch], text=True)
    except subprocess.SubprocessError as e:
        print(f"Error executing git branch -r --merged: {e}", file=sys.stderr)
        return []

    branches = []
    for line in output.splitlines():
        branch = line.strip()
        if branch:
            branches.append(branch)
    return branches


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
        # 去掉 remotes/ 前缀
        clean_r_branch = r_branch.replace("remotes/", "")

        # 提取远程分支对应的本地分支名称，如 origin/fix/e2e -> fix/e2e
        local_equiv = clean_r_branch
        if clean_r_branch.startswith("origin/"):
            local_equiv = clean_r_branch[len("origin/") :]

        # 保护主分支
        if local_equiv in protected_names:
            preserved_remote[clean_r_branch] = "主分支保护"
        # 保护被 worktree 本地占用的远程分支
        elif local_equiv in worktree_branches:
            preserved_remote[clean_r_branch] = "当前工作树(Worktree)占用"
        else:
            to_delete_remote.append(clean_r_branch)

    return to_delete_local, to_delete_remote, preserved_local, preserved_remote


def run_command(cmd):
    """运行 git 命令并检查返回值，同时将输出重定向到终端"""
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"命令执行失败: {' '.join(cmd)} (错误: {e})", file=sys.stderr)
        return False


def main():
    print("=================== Git 分支安全清理程序 ===================")
    print("正在同步远程分支数据 (git fetch -p)...")
    if not run_command(["git", "fetch", "-p"]):
        print("警告: 无法同步远程分支，将使用当前本地缓存的远程状态。")

    print("正在搜集本地与远程分支状态...")
    worktree_branches = get_worktree_branches()
    local_branches = get_merged_local_branches("main")
    remote_branches = get_merged_remote_branches("origin/main")

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
        return

    print("\n" + "=" * 60)
    print("⚠️  警告：以上待删除的分支将被永久物理删除！")
    try:
        user_input = input("请输入完整的 'yes' 确认清理，输入其他任意内容取消: ").strip()
    except KeyboardInterrupt:
        print("\n操作已被用户中止。")
        return

    if user_input.lower() == "yes":
        print("\n开始执行清理...")

        # 删除本地分支
        if to_delete_local:
            print("\n--- 正在清理本地分支 ---")
            for branch in to_delete_local:
                print(f"正在删除本地分支: {branch}")
                # 使用 -d 进行安全删除
                run_command(["git", "branch", "-d", branch])

        # 删除远程分支
        if to_delete_remote:
            print("\n--- 正在清理远程分支 ---")
            for branch in to_delete_remote:
                # 远程分支解析出来的格式通常是 origin/branch-name 或者 remotes/origin/branch-name
                # 我们需要提取出远程仓库名(origin)和具体的分支路径
                parts = branch.split("/", 1)
                if len(parts) == 2:
                    remote, remote_branch = parts
                    print(f"正在删除远程分支: {remote_branch} 来自 {remote}")
                    run_command(["git", "push", remote, "--delete", remote_branch])
                else:
                    print(f"跳过无法解析格式的远程分支: {branch}", file=sys.stderr)
        print("\n🎉 清理工作已完成！")
    else:
        print("\n操作已取消，未做任何更改。")


if __name__ == "__main__":
    main()
