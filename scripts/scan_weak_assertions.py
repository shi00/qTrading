"""CI 健康度脚本：扫描测试文件中的弱断言。

检测模式：
- assert True / assert 1  （裸布尔断言）
- pass  （空测试体，仅 docstring）
- mock.assert_called() / mock.assert_called_once()  （Mock 弱断言，不验证参数）

用法：
    python scripts/scan_weak_assertions.py
    python scripts/scan_weak_assertions.py --path tests/unit/
    python scripts/scan_weak_assertions.py --strict  # 发现弱断言时返回非零退出码

退出码：
    0: 无弱断言（或未启用 --strict）
    1: 发现弱断言且启用 --strict
    2: 路径不存在
"""

import argparse
import ast
import sys
from pathlib import Path


# Mock 弱断言方法名（不验证调用参数）
WEAK_MOCK_METHODS = frozenset(
    {
        "assert_called",
        "assert_called_once",
    }
)


def _is_weak_assert(node: ast.stmt) -> bool:
    """判断 assert 语句是否为弱断言（assert True / assert 1）。"""
    if isinstance(node, ast.Assert):
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            return True
        if isinstance(node.test, ast.Constant) and node.test.value == 1:
            return True
    return False


def _is_empty_test(body: list) -> bool:
    """判断测试方法体是否为空（仅 pass 或 docstring）。"""
    if not body:
        return True
    real_stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not real_stmts:
        return True
    return len(real_stmts) == 1 and isinstance(real_stmts[0], ast.Pass)


def _is_weak_mock_assert(node: ast.stmt) -> bool:
    """判断是否为 Mock 弱断言（mock.assert_called() 等表达式语句）。"""
    if not isinstance(node, ast.Expr):
        return False
    val = node.value
    if not isinstance(val, ast.Call):
        return False
    func = val.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in WEAK_MOCK_METHODS


def scan_file(filepath: Path) -> list:
    """扫描单个测试文件，返回弱断言列表。

    返回格式：[(line_no, issue_type, detail), ...]
    """
    issues = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return issues

    for node in ast.walk(tree):
        if _is_weak_assert(node):
            issues.append((node.lineno, "weak_assert", "assert True / assert 1"))

        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            if _is_empty_test(node.body):
                issues.append((node.lineno, "empty_test", f"空测试方法: {node.name}"))

        if _is_weak_mock_assert(node):
            issues.append((node.lineno, "weak_mock", "Mock 弱断言（不验证参数）"))

    return issues


def scan_directory(root: Path) -> dict:
    """扫描目录下所有 test_*.py 文件，返回 {filepath: [issues]}。"""
    results = {}
    for filepath in root.rglob("test_*.py"):
        issues = scan_file(filepath)
        if issues:
            results[filepath] = issues
    return results


def main():
    parser = argparse.ArgumentParser(description="扫描测试文件中的弱断言")
    parser.add_argument("--path", default="tests/", help="扫描路径（默认 tests/）")
    parser.add_argument("--strict", action="store_true", help="发现弱断言时返回非零退出码")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"错误：路径不存在 {root}")
        return 2

    results = scan_directory(root)

    if not results:
        print(f"✓ {root} 下无弱断言")
        return 0

    total_issues = sum(len(issues) for issues in results.values())
    print(f"发现 {total_issues} 处弱断言，涉及 {len(results)} 个文件：\n")
    for filepath, issues in sorted(results.items()):
        rel_path = filepath.relative_to(Path.cwd()) if filepath.is_absolute() else filepath
        print(f"  {rel_path}:")
        for line_no, issue_type, detail in issues:
            print(f"    L{line_no} [{issue_type}] {detail}")
        print()

    if args.strict:
        print(f"✗ 启用 --strict：发现 {total_issues} 处弱断言")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
