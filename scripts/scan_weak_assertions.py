"""CI 健康度脚本：扫描测试文件中的弱断言。

检测模式：
- assert True / assert 1  （裸布尔断言）
- pass  （空测试体，仅 docstring）
- mock.assert_called() / mock.assert_called_once()  （Mock 弱断言，不验证参数）

模式：
    python scripts/scan_weak_assertions.py
        默认 advisory 模式，发现弱断言只打印 ::warning，退出码 0。
    python scripts/scan_weak_assertions.py --strict
        发现任何弱断言即返回 1（用于全量门禁）。
    python scripts/scan_weak_assertions.py --base <baseline.json>
        增量门禁：与 baseline 对比，仅新增弱断言使 CI 失败（退出码 1）。
        同时检查 baseline 文件条目数只能下降不能上升（对比 git ref 旧版本）。
    python scripts/scan_weak_assertions.py --update-baseline <baseline.json>
        用当前扫描结果覆盖 baseline 文件（开发态使用，不检查 shrink）。

行内白名单：
    assert True  # noqa: weak-assertion <reason>
    跳过该行弱断言检测。<reason> 必填，简短说明为何该断言是充分的。

退出码：
    0: 通过（无新增弱断言 / advisory / baseline 未增长）
    1: 发现新增弱断言 或 baseline 数量上升
    2: 路径不存在
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Mock 弱断言方法名（不验证调用参数）
WEAK_MOCK_METHODS = frozenset(
    {
        "assert_called",
        "assert_called_once",
    }
)

WHITELIST_RE = re.compile(
    r"#\s*noqa:\s*weak-assertion\s+\S+.+?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WeakAssertion:
    """单条弱断言记录。

    Attributes:
        rel_path: 相对扫描根的路径（用于 baseline 索引与显示）。
        line_no: 行号（仅显示参考，不参与签名匹配，容忍行号漂移）。
        issue_type: weak_assert / empty_test / weak_mock。
        detail: 人类可读描述。
        source_line: 该行原始源代码（用于签名计算与白名单检测）。
    """

    rel_path: str
    line_no: int
    issue_type: str
    detail: str
    source_line: str


def make_signature(rel_path: str, issue_type: str, source_line: str) -> tuple[str, str, str]:
    """构造弱断言签名：(rel_path, issue_type, normalized_source_line)。

    归一化：strip + lower，容忍缩进/大小写差异。行号不参与签名，
    因此代码编辑导致的行号漂移不会误报新增。
    """
    return (rel_path, issue_type, source_line.strip().lower())


def is_whitelisted(source_line: str) -> bool:
    """检测行内 `# noqa: weak-assertion <reason>` 白名单。

    reason 必填（至少一个非空 token），无 reason 的 noqa 不算白名单。
    """
    return bool(WHITELIST_RE.search(source_line))


def _is_weak_assert(node: ast.AST) -> bool:
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


def _is_weak_mock_assert(node: ast.AST) -> bool:
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


def scan_file(filepath: Path, rel_path: str | None = None) -> list[WeakAssertion]:
    """扫描单个测试文件，返回弱断言列表（已过滤白名单）。

    Args:
        filepath: 文件绝对路径。
        rel_path: 相对路径（用于 baseline 索引）；None 时用 filepath 自身。
    """
    rel = rel_path if rel_path is not None else str(filepath)
    issues: list[WeakAssertion] = []
    try:
        source_lines = filepath.read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(source_lines), filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return issues

    for node in ast.walk(tree):
        line_no: int | None = None
        issue_type: str | None = None
        detail: str | None = None

        if _is_weak_assert(node):
            line_no = node.lineno
            issue_type = "weak_assert"
            detail = "assert True / assert 1"
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            if _is_empty_test(node.body):
                line_no = node.lineno
                issue_type = "empty_test"
                detail = f"空测试方法: {node.name}"
        elif _is_weak_mock_assert(node):
            line_no = node.lineno
            issue_type = "weak_mock"
            detail = "Mock 弱断言（不验证参数）"

        if line_no is None or issue_type is None or detail is None:
            continue

        source_line = source_lines[line_no - 1] if 0 < line_no <= len(source_lines) else ""
        if is_whitelisted(source_line):
            continue

        issues.append(
            WeakAssertion(
                rel_path=rel,
                line_no=line_no,
                issue_type=issue_type,
                detail=detail,
                source_line=source_line,
            )
        )

    return issues


def scan_directory(root: Path) -> list[WeakAssertion]:
    """扫描目录下所有 test_*.py 文件，返回 WeakAssertion 列表（rel_path 相对 root）。"""
    results: list[WeakAssertion] = []
    for filepath in root.rglob("test_*.py"):
        try:
            rel = filepath.relative_to(root).as_posix()
        except ValueError:
            rel = str(filepath)
        results.extend(scan_file(filepath, rel_path=rel))
    return results


def load_baseline(path: Path) -> list[WeakAssertion]:
    """加载 baseline 文件。文件不存在或格式非法时返回空列表。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = data.get("entries", []) if isinstance(data, dict) else []
    result: list[WeakAssertion] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result.append(
            WeakAssertion(
                rel_path=str(entry.get("file", "")),
                line_no=int(entry.get("line", 0)),
                issue_type=str(entry.get("type", "")),
                detail=str(entry.get("detail", "")),
                source_line=str(entry.get("source_line", "")),
            )
        )
    return result


def save_baseline(path: Path, entries: list[WeakAssertion]) -> None:
    """序列化 baseline 文件。包含 version/total/entries 三字段。"""
    data = {
        "version": 1,
        "total": len(entries),
        "entries": [
            {
                "file": e.rel_path,
                "line": e.line_no,
                "type": e.issue_type,
                "detail": e.detail,
                "source_line": e.source_line,
            }
            for e in entries
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compute_new_issues(current: list[WeakAssertion], baseline: list[WeakAssertion]) -> list[WeakAssertion]:
    """对比当前扫描结果与 baseline，返回新增弱断言列表。

    新增 = current 中签名不在 baseline 中的条目（白名单已在 scan_file 阶段过滤）。
    """
    baseline_sigs = {make_signature(e.rel_path, e.issue_type, e.source_line) for e in baseline}
    return [e for e in current if make_signature(e.rel_path, e.issue_type, e.source_line) not in baseline_sigs]


def _git_show_file(ref: str, file_rel: str) -> str | None:
    """通过 git show 读取指定 ref 下的文件内容。失败返回 None。"""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{file_rel}"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def check_baseline_shrink(baseline_path: Path, ref: str = "origin/main") -> tuple[bool, int, int]:
    """检查 baseline 条目数只能下降不能上升。

    Returns:
        (ok, current_total, old_total)
        - git 不可用或文件在 ref 中不存在时，跳过检查返回 (True, current_total, -1)。
        - current_total <= old_total 时返回 (True, current_total, old_total)。
        - current_total > old_total 时返回 (False, current_total, old_total)。
    """
    current = load_baseline(baseline_path)
    current_total = len(current)

    # baseline_path 相对仓库根的路径，用于 git show
    try:
        file_rel = baseline_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        file_rel = str(baseline_path)

    old_content = _git_show_file(ref, file_rel)
    if old_content is None:
        return True, current_total, -1

    try:
        old_data = json.loads(old_content)
    except json.JSONDecodeError:
        return True, current_total, -1

    if not isinstance(old_data, dict):
        return True, current_total, -1
    # 优先用 total 字段（权威），fallback entries 长度（防篡改/老文件）
    old_total = int(old_data.get("total", len(old_data.get("entries", []))))
    ok = current_total <= old_total
    return ok, current_total, old_total


def _format_issue(issue: WeakAssertion) -> str:
    return f"  {issue.rel_path}:L{issue.line_no} [{issue.issue_type}] {issue.detail}"


def _run_advisory_or_strict(args: argparse.Namespace) -> int:
    """advisory / --strict 模式（向后兼容原行为）。"""
    root = Path(args.path)
    if not root.exists():
        print(f"错误：路径不存在 {root}")
        return 2

    issues = scan_directory(root)
    if not issues:
        print(f"✓ {root} 下无弱断言")
        return 0

    print(f"发现 {len(issues)} 处弱断言：\n")
    for issue in sorted(issues, key=lambda e: (e.rel_path, e.line_no)):
        print(_format_issue(issue))
        if not args.strict:
            print(f"::warning file={issue.rel_path},line={issue.line_no}::{issue.issue_type}: {issue.detail}")
    print()

    if args.strict:
        print(f"✗ 启用 --strict：发现 {len(issues)} 处弱断言")
        return 1
    return 0


def _run_base_mode(args: argparse.Namespace) -> int:
    """--base 增量门禁模式。"""
    root = Path(args.path)
    if not root.exists():
        print(f"错误：路径不存在 {root}")
        return 2

    baseline_path = Path(args.base) if args.base else None
    if baseline_path is None:
        print("错误：--base 需指定 baseline 文件路径")
        return 2

    baseline = load_baseline(baseline_path)
    current = scan_directory(root)
    new_issues = compute_new_issues(current=current, baseline=baseline)

    # 检查 baseline 数量只能下降不能上升
    ok_shrink, current_total, old_total = check_baseline_shrink(baseline_path, ref=args.baseline_ref)

    failed = False

    if new_issues:
        failed = True
        print(f"✗ 发现 {len(new_issues)} 处新增弱断言：\n")
        for issue in sorted(new_issues, key=lambda e: (e.rel_path, e.line_no)):
            print(_format_issue(issue))
            print(f"::error file={issue.rel_path},line={issue.line_no}::{issue.issue_type}: {issue.detail}")
        print()
        print(
            "修复建议：删除新增弱断言并替换为强断言；或如属充分断言，"
            "添加 `# noqa: weak-assertion <reason>` 行内白名单。"
        )

    if not ok_shrink:
        failed = True
        print(
            f"✗ baseline 数量上升：当前 {current_total} > 旧 {old_total}"
            f"（ref={args.baseline_ref}）。baseline 只能下降不能上升。"
        )

    if not failed:
        print(f"✓ 增量门禁通过：当前 {len(current)} 处弱断言，baseline {len(baseline)} 处，无新增。")
        return 0
    return 1


def _run_update_baseline(args: argparse.Namespace) -> int:
    """--update-baseline 模式：用当前扫描结果覆盖 baseline。"""
    root = Path(args.path)
    if not root.exists():
        print(f"错误：路径不存在 {root}")
        return 2

    baseline_path = Path(args.update_baseline)
    current = scan_directory(root)
    save_baseline(baseline_path, current)
    print(f"✓ 已更新 baseline：{baseline_path}（共 {len(current)} 条）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描测试文件中的弱断言")
    parser.add_argument("--path", default="tests/", help="扫描路径（默认 tests/）")
    parser.add_argument("--strict", action="store_true", help="发现弱断言时返回非零退出码")
    parser.add_argument(
        "--base",
        default=None,
        help="baseline 文件路径，启用增量门禁模式：仅新增弱断言失败",
    )
    parser.add_argument(
        "--update-baseline",
        default=None,
        help="用当前扫描结果覆盖 baseline 文件（开发态使用）",
    )
    parser.add_argument(
        "--baseline-ref",
        default="origin/main",
        help="对比 baseline 数量变化时的 git ref（默认 origin/main）",
    )
    args = parser.parse_args(argv)

    if args.update_baseline:
        return _run_update_baseline(args)
    if args.base:
        return _run_base_mode(args)
    return _run_advisory_or_strict(args)


if __name__ == "__main__":
    sys.exit(main())
