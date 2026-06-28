"""CI 健康度脚本：计算 PR 变更行的覆盖率（diff-coverage）。

对 git diff 检出的新增/修改行计算覆盖率，输出未覆盖行列表。
默认 advisory 模式（exit 0），加 --strict 才在低于阈值时 exit 1。

用法：
    python scripts/check_diff_coverage.py
    python scripts/check_diff_coverage.py --base origin/main
    python scripts/check_diff_coverage.py --coverage-file coverage.json
    python scripts/check_diff_coverage.py --strict --threshold 80

退出码：
    0: advisory 模式（默认），或覆盖率达标
    1: --strict 模式下覆盖率低于阈值
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 与 pyproject.toml [tool.coverage.run] source 保持一致
# "config"/"main" 是顶层模块（config.py/main.py），其余是目录
SOURCE_DIRS = frozenset(("core", "data", "services", "strategies", "utils", "ui", "app"))
SOURCE_FILES = frozenset(("config.py", "main.py"))

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def _is_source_file(path: str) -> bool:
    """判断文件是否在覆盖率源码目录内。"""
    if not path.endswith(".py"):
        return False
    if path in SOURCE_FILES:
        return True
    top = path.split("/", 1)[0]
    return top in SOURCE_DIRS


def parse_diff(diff_text: str) -> dict[str, list[int]]:
    """解析 git diff 输出，提取源码文件的新增行号。

    返回 {filepath: [line_no, ...]}，仅包含源码目录下的 .py 文件。
    """
    added_lines: dict[str, list[int]] = {}
    current_file: str | None = None
    current_line = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            current_file = path if _is_source_file(path) else None
            if current_file:
                added_lines.setdefault(current_file, [])
        elif line.startswith("+++ "):
            current_file = None
        elif current_file and line.startswith("@@"):
            match = _HUNK_RE.match(line)
            current_line = int(match.group(1)) if match else 0
        elif current_file and line.startswith("+"):
            if current_line > 0:
                added_lines[current_file].append(current_line)
            current_line += 1
        elif current_file and line.startswith("-"):
            pass  # 删除行不影响新文件行号
        elif current_file and line.startswith(" "):
            current_line += 1

    return {f: lines for f, lines in added_lines.items() if lines}


def get_diff_added_lines(base: str) -> dict[str, list[int]]:
    """获取 git diff 中新增的行号，返回 {filepath: [line_no, ...]}。"""
    cmd_variants = [
        ["git", "diff", "--unified=0", "--no-color", f"{base}...HEAD"],
        ["git", "diff", "--unified=0", "--no-color", base],
    ]
    last_error = ""
    for cmd in cmd_variants:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        if result.returncode == 0:
            return parse_diff(result.stdout)
        last_error = result.stderr.strip()
    raise RuntimeError(f"git diff 失败（base={base}）: {last_error}")


def load_coverage(coverage_file: Path) -> dict[str, set[int]]:
    """加载 coverage.json，返回 {filepath: set(executed_lines)}。"""
    with open(coverage_file, encoding="utf-8") as f:
        cov = json.load(f)
    return {fp: set(d.get("executed_lines", [])) for fp, d in cov.get("files", {}).items()}


def compute_diff_coverage(
    diff_lines: dict[str, list[int]], coverage: dict[str, set[int]]
) -> tuple[int, int, dict[str, list[int]]]:
    """计算 diff-coverage。

    返回 (covered_count, total_count, {filepath: [uncovered_lines]})。
    """
    covered = 0
    total = 0
    uncovered_by_file: dict[str, list[int]] = {}

    for filepath, lines in diff_lines.items():
        executed = coverage.get(filepath, set())
        line_set = set(lines)
        covered += len(line_set & executed)
        total += len(lines)
        file_uncovered = sorted(line_set - executed)
        if file_uncovered:
            uncovered_by_file[filepath] = file_uncovered

    return covered, total, uncovered_by_file


def main():
    parser = argparse.ArgumentParser(description="计算 PR 变更行的覆盖率（diff-coverage）")
    parser.add_argument("--base", default="origin/main", help="git diff 的 base ref（默认 origin/main）")
    parser.add_argument("--coverage-file", default="coverage.json", help="coverage JSON 文件路径（默认 coverage.json）")
    parser.add_argument("--strict", action="store_true", help="覆盖率低于阈值时返回非零退出码")
    parser.add_argument("--threshold", type=int, default=80, help="diff-coverage 阈值百分比（默认 80）")
    args = parser.parse_args()

    coverage_path = ROOT / args.coverage_file
    if not coverage_path.exists():
        print(f"⚠ coverage 文件不存在: {coverage_path}（advisory 模式，跳过）")
        return 0

    try:
        diff_lines = get_diff_added_lines(args.base)
    except RuntimeError as e:
        print(f"⚠ {e}（advisory 模式，跳过）")
        return 0

    if not diff_lines:
        print("✓ 无源码变更行，跳过 diff-coverage 检查")
        return 0

    coverage = load_coverage(coverage_path)
    covered, total, uncovered_by_file = compute_diff_coverage(diff_lines, coverage)

    pct = (covered / total * 100) if total > 0 else 100.0

    print(f"Diff-coverage 报告（base={args.base}）")
    print(f"  变更源码行: {total}")
    print(f"  已覆盖行: {covered}")
    print(f"  未覆盖行: {total - covered}")
    print(f"  覆盖率: {pct:.1f}%\n")

    if uncovered_by_file:
        print("未覆盖的变更行：")
        for filepath, lines in sorted(uncovered_by_file.items()):
            print(f"  {filepath}:")
            for ln in lines:
                print(f"    L{ln}")
        print()

    if args.strict and pct < args.threshold:
        print(f"✗ 启用 --strict：diff-coverage {pct:.1f}% 低于阈值 {args.threshold}%")
        return 1

    if pct >= args.threshold:
        print(f"✓ diff-coverage {pct:.1f}% >= {args.threshold}%")
    else:
        print(f"⚠ diff-coverage {pct:.1f}% < {args.threshold}%（advisory 模式，不阻塞）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
