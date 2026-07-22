"""pre-commit hook: 对 staged test_*.py 扫描弱断言，增量阻断（与 CI 一致）。

用法（由 pre-commit 调用，文件名作为参数传入）::

    python scripts/check_staged_weak_assertions.py <file1> <file2> ...

仅扫描传入的 test_*.py 文件，与 CI 的 ``scan_weak_assertions.py --base`` 一致：
加载 ``tests/weak_assertion_baseline.json``，只阻断新增弱断言（不在 baseline 中的）。
历史弱断言（在 baseline 中）不阻断，避免修改已有测试文件时被历史债阻塞。

复用 ``scripts/scan_weak_assertions.py`` 的 ``scan_file`` / ``load_baseline`` /
``compute_new_issues``，规则与 CI 保持一致。
白名单机制（``# noqa: weak-assertion <reason>``）同样生效。

退出码:
    0: 无新增弱断言
    1: 发现新增弱断言
"""

import sys
from pathlib import Path

# 复用 scan_weak_assertions 的函数（同目录脚本，sys.path 补齐后导入）
sys.path.insert(0, str(Path(__file__).parent))
from scan_weak_assertions import compute_new_issues, load_baseline, scan_file  # noqa: E402

# baseline 路径与 CI 一致（scripts/ 的父目录是项目根）
BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "weak_assertion_baseline.json"


def _to_tests_relative(file_path: str) -> str:
    """将 git 路径转为相对 tests/ 的路径，与 CI baseline 签名格式一致。

    pre-commit 传入 ``tests/unit/test_foo.py``，baseline 中存 ``unit/test_foo.py``。
    不在 tests/ 下的文件原样返回（防御性处理，pre-commit files 已前置过滤）。
    """
    if file_path.startswith("tests/"):
        return file_path[len("tests/") :]
    return file_path


def main() -> int:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).name.startswith("test_")]
    if not files:
        return 0

    baseline = load_baseline(BASELINE_PATH)

    all_issues = []
    for f in files:
        path = Path(f)
        if not path.exists():
            continue
        rel_path = _to_tests_relative(f)
        issues = scan_file(path, rel_path=rel_path)
        all_issues.extend(issues)

    # 只阻断新增弱断言（不在 baseline 中的）
    new_issues = compute_new_issues(current=all_issues, baseline=baseline)

    if not new_issues:
        return 0

    print(f"✗ 发现 {len(new_issues)} 处新增弱断言：\n")
    for issue in sorted(new_issues, key=lambda e: (e.rel_path, e.line_no)):
        print(f"  {issue.rel_path}:L{issue.line_no} [{issue.issue_type}] {issue.detail}")
        print(f"    {issue.source_line.strip()}")
    print(
        "\n修复建议：替换为强断言（验证参数/内容/状态）；"
        "或添加 `# noqa: weak-assertion <reason>` 行内白名单（reason 必填）。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
