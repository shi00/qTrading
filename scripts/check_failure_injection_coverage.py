"""Failure injection coverage cross-check (P3-Test-Scenario-Cross-Validation).

解析并比较两侧失败注入场景编号：
1. Rust 测试侧：``sidecars/qtrading-pg-sidecar/tests/failure_injection.rs`` 中
   ``fn test_inject_NN_*`` 函数名（NN 为场景编号，可能含 ``b`` 后缀表示对称测试）。
   只认真实代码：注释（行/块/文档）与字符串、字符字面量中的伪声明会被剔除，
   避免 ``// fn test_inject_03_x() {}`` 之类的假绿。
2. 文档矩阵侧：``reviews/pg_plan.md`` §17.6 失败注入测试矩阵中的场景编号。

报告缺失（矩阵有但 Rust 无）与冗余（Rust 有但矩阵无），供 CI/pre-commit 守护
场景编号漂移（如新增 §17.6 场景但遗漏 Rust 集成测试）。

退出码：
- 0：两侧一致（Rust 测试编号归一化后与矩阵场景编号集合相等）
- 1：发现缺失或冗余
- 2：文件不存在 / §17.6 矩阵格式异常

Usage:
    python scripts/check_failure_injection_coverage.py
    python scripts/check_failure_injection_coverage.py --rust-path <path> --pg-plan-path <path>

注：``reviews/pg_plan.md`` 被 ``.gitignore`` 忽略（本地 review 主计划，不入仓），
worktree/CI 中默认路径不存在时脚本以 exit 2 友好报错，需通过 ``--pg-plan-path``
显式指定路径，或由后续 PR 决定是否将 §17.6 矩阵纳入 git 跟踪。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_RUST_PATH = ROOT / "sidecars" / "qtrading-pg-sidecar" / "tests" / "failure_injection.rs"
DEFAULT_PG_PLAN_PATH = ROOT / "reviews" / "pg_plan.md"

# §17.6 章节标题（级别 3）
_SECTION_HEADER_RE = re.compile(r"^###\s+17\.6\b")
# §17.6 矩阵区块结束：下一个同级或更高级标题（### 或 ##）
_SECTION_END_RE = re.compile(r"^#{2,3}\s+(?!17\.6\b)")
# 矩阵行：| # | 测试场景 | ... |
_MATRIX_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")
# Rust 测试函数：fn test_inject_NN_*（NN 含可选 b 后缀）
_RUST_FN_RE = re.compile(r"\bfn\s+test_inject_(\d+[a-z]?)_")
# Rust 字符串字面量前缀（普通 b/c 与 raw r/br/cr）
_STRING_PREFIXES = ("br", "cr", "r", "b", "c")
_RAW_STRING_PREFIXES = frozenset({"r", "br", "cr"})


def _mask_rust_comments_and_strings(content: str) -> str:
    """把 Rust 源码中的注释与字符串/字符字面量替换为空白，保留换行。

    目的：``extract_rust_scenarios`` 只匹配真实代码里的函数声明，避免把注释或
    字符串中的伪 ``fn test_inject_NN_*`` 当作已覆盖场景（D21-UNIT-005）。

    保守实现，不做完整 Rust 词法分析，但覆盖会影响匹配的构造：
    - 行注释 ``//``（含 ``///`` / ``//!`` 文档注释）
    - 块注释 ``/* ... */``（Rust 支持嵌套）
    - 普通字符串 ``"..."``、字节/c 字符串 ``b"..."`` / ``c"..."``（处理转义）
    - raw 字符串 ``r"..."`` / ``r#"..."#`` / ``br#"..."#``（任意 ``#`` 数量）
    - 字符字面量 ``'a'`` / ``'\\n'`` / ``'"'``（生命周期 ``'a`` 不当字面量）

    Args:
        content: Rust 源文件全文。

    Returns:
        等长文本，注释与字面量内容被空格替换（换行保留）。
    """
    out: list[str] = []
    i = 0
    n = len(content)

    def masked(start: int, end: int) -> None:
        for ch in content[start:end]:
            out.append(ch if ch in "\r\n" else " ")

    while i < n:
        ch = content[i]
        next_ch = content[i + 1] if i + 1 < n else ""

        if ch == "/" and next_ch == "/":
            end = content.find("\n", i)
            end = n if end == -1 else end
            masked(i, end)
            i = end
            continue

        if ch == "/" and next_ch == "*":
            # Rust 块注释可嵌套
            depth = 1
            j = i + 2
            while j < n and depth:
                if content.startswith("/*", j):
                    depth += 1
                    j += 2
                elif content.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            masked(i, j)
            i = j
            continue

        # 字符串字面量（含前缀）：前缀必须是独立 token，避免误吞 for/bar 等标识符
        prefix = ""
        if ch in ("b", "c", "r") and (i == 0 or not (content[i - 1].isalnum() or content[i - 1] == "_")):
            for candidate in _STRING_PREFIXES:
                if content.startswith(candidate, i):
                    prefix = candidate
                    break
        if prefix:
            j = i + len(prefix)
            if prefix in _RAW_STRING_PREFIXES:
                hashes = 0
                k = j
                while k < n and content[k] == "#":
                    hashes += 1
                    k += 1
                if k < n and content[k] == '"':
                    terminator = '"' + "#" * hashes
                    end = content.find(terminator, k + 1)
                    end = n if end == -1 else end + len(terminator)
                    masked(i, end)
                    i = end
                    continue
            elif j < n and content[j] == '"':
                end = _scan_escaped_string_end(content, j)
                masked(i, end)
                i = end
                continue

        if ch == '"':
            end = _scan_escaped_string_end(content, i)
            masked(i, end)
            i = end
            continue

        if ch == "'":
            end = _scan_char_literal_end(content, i)
            if end is not None:
                masked(i, end)
                i = end
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def _scan_escaped_string_end(content: str, start: int) -> int:
    """返回普通字符串的结束下标（``start`` 指向起始双引号）。"""
    i = start + 1
    n = len(content)
    while i < n:
        if content[i] == "\\":
            i += 2
            continue
        if content[i] == '"':
            return i + 1
        i += 1
    return n


def _scan_char_literal_end(content: str, start: int) -> int | None:
    """识别字符字面量并返回结束下标；判定为生命周期时返回 None。"""
    n = len(content)
    j = start + 1
    if j >= n:
        return None
    if content[j] == "\\":
        k = j + 2  # 跳过多字节转义的首个字符，如 '\n' / '\u{...}' / '\''
        limit = start + 16
        while k < n and k <= limit:
            if content[k] == "'":
                return k + 1
            k += 1
        return None
    if j + 1 < n and content[j + 1] == "'":
        return j + 2
    return None


def _normalize_rust_scenario(raw: str) -> str:
    """归一化 Rust 场景号：去前导 0，保留字母后缀。

    Examples:
        "03" → "3"
        "28b" → "28b"
        "31" → "31"
    """
    # 分离数字部分与字母后缀
    m = re.fullmatch(r"0*(\d+)([a-z]?)", raw)
    if m is None:
        return raw
    return m.group(1) + m.group(2)


def extract_rust_scenarios(rust_path: Path) -> set[str]:
    """从 failure_injection.rs 提取 test_inject_NN_* 场景号（归一化后）。

    只匹配真实代码中的函数声明：注释（行/块/文档）与普通、raw 字符串及字符
    字面量中的伪声明会被剔除，不当作已覆盖场景。

    Args:
        rust_path: failure_injection.rs 文件路径。

    Returns:
        场景号集合，如 {"3", "4", "8", "9", "23", "24", "26", "27", "28", "28b", "31"}。

    Raises:
        FileNotFoundError: 文件不存在。
    """
    if not rust_path.exists():
        raise FileNotFoundError(f"Rust failure_injection.rs not found: {rust_path}")
    content = rust_path.read_text(encoding="utf-8")
    code = _mask_rust_comments_and_strings(content)
    scenarios: set[str] = set()
    for m in _RUST_FN_RE.finditer(code):
        raw = m.group(1)
        scenarios.add(_normalize_rust_scenario(raw))
    return scenarios


def extract_matrix_scenarios(pg_plan_path: Path) -> set[str]:
    """从 reviews/pg_plan.md §17.6 矩阵提取场景号。

    §17.6 章节界定：从 ``### 17.6`` 行开始，到下一个 ``##`` 或 ``###`` 标题之前。
    矩阵行格式：``| # | 测试场景 | 注入方式 | 预期行为 |``。

    Args:
        pg_plan_path: reviews/pg_plan.md 文件路径。

    Returns:
        场景号集合（数字字符串），如 {"1", "2", "3", ..., "33"}。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: §17.6 章节缺失或矩阵表格为空。
    """
    if not pg_plan_path.exists():
        raise FileNotFoundError(f"pg_plan.md not found: {pg_plan_path}")
    content = pg_plan_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # 定位 §17.6 章节起始行
    section_start = -1
    for i, line in enumerate(lines):
        if _SECTION_HEADER_RE.match(line):
            section_start = i
            break
    if section_start == -1:
        raise ValueError(
            f"§17.6 section not found in {pg_plan_path}; "
            "expected header '### 17.6 ...' (reviews/pg_plan.md §17.6 failure injection matrix)"
        )

    # 收集 §17.6 区块内的矩阵行（到下一个 ## 或 ### 标题之前）
    scenarios: set[str] = set()
    for line in lines[section_start + 1 :]:
        if _SECTION_END_RE.match(line):
            break
        m = _MATRIX_ROW_RE.match(line)
        if m is not None:
            scenarios.add(m.group(1))

    if not scenarios:
        raise ValueError(
            f"§17.6 matrix table is empty or missing in {pg_plan_path}; expected rows like '| # | 测试场景 | ... |'"
        )
    return scenarios


def compare_scenarios(rust_set: set[str], matrix_set: set[str]) -> tuple[set[str], set[str]]:
    """比较 Rust 与矩阵场景号集合，返回（缺失，冗余）。

    - 缺失：矩阵中有但 Rust 中没有（Rust 归一化后去掉 b 后缀比较，#NNb 视为 #NN 的对称测试）。
    - 冗余：Rust 中有但矩阵中没有（#NNb 归一化为 #NN 后比较）。

    Args:
        rust_set: Rust 场景号集合（含可能的 b 后缀）。
        matrix_set: 矩阵场景号集合（纯数字）。

    Returns:
        (missing, extra) 元组，均为场景号字符串集合。
    """
    # Rust 侧归一化：去掉 b 后缀，使 #28b 与矩阵 #28 匹配
    rust_normalized = {s.rstrip("b") if s.endswith("b") else s for s in rust_set}
    missing = matrix_set - rust_normalized
    extra = rust_normalized - matrix_set
    return missing, extra


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-check Rust failure_injection.rs vs reviews/pg_plan.md §17.6 matrix."
    )
    parser.add_argument(
        "--rust-path",
        type=Path,
        default=DEFAULT_RUST_PATH,
        help=f"Path to failure_injection.rs (default: {DEFAULT_RUST_PATH}).",
    )
    parser.add_argument(
        "--pg-plan-path",
        type=Path,
        default=DEFAULT_PG_PLAN_PATH,
        help=f"Path to reviews/pg_plan.md (default: {DEFAULT_PG_PLAN_PATH}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Windows 默认 stdout/stderr 编码可能不支持 § 等 non-ASCII 字符，
    # 显式 reconfigure 为 UTF-8 避免子进程 print 崩溃。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        rust_set = extract_rust_scenarios(args.rust_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Hint: pass --rust-path to specify failure_injection.rs location.",
            file=sys.stderr,
        )
        return 2

    try:
        matrix_set = extract_matrix_scenarios(args.pg_plan_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Hint: reviews/pg_plan.md is .gitignored (local review master plan, not tracked). "
            "Pass --pg-plan-path to specify its location, or check out the file into the worktree.",
            file=sys.stderr,
        )
        return 2
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    missing, extra = compare_scenarios(rust_set, matrix_set)

    # 摘要输出
    print("Failure injection coverage cross-check:")
    print(f"  Rust scenarios   ({len(rust_set)}): {sorted(rust_set, key=_sort_key)}")
    print(f"  §17.6 matrix     ({len(matrix_set)}): {sorted(matrix_set, key=_sort_key)}")

    if not missing and not extra:
        print("  Result: PASS — Rust tests and §17.6 matrix are consistent.")
        return 0

    if missing:
        print(f"  MISSING (in Rust, present in §17.6 matrix): {sorted(missing, key=_sort_key)}")
    if extra:
        print(f"  EXTRA (in Rust, absent from §17.6 matrix): {sorted(extra, key=_sort_key)}")
    print("  Result: FAIL — scenario mismatch detected.")
    return 1


def _sort_key(s: str) -> tuple[int, str]:
    """排序键：数字部分升序，字母后缀次序。

    使 "3" < "4" < "28" < "28b" < "31"。
    """
    m = re.fullmatch(r"(\d+)([a-z]?)", s)
    if m is None:
        return (0, s)
    return (int(m.group(1)), m.group(2))


if __name__ == "__main__":
    sys.exit(main())
