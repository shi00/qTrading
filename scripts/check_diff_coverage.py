"""CI 健康度脚本：计算 PR 变更行的覆盖率（diff-coverage）。

对 git diff 检出的新增/修改行计算覆盖率，输出未覆盖行列表。
输入可计算时默认 advisory 模式（exit 0），加 --strict 才在低于阈值时 exit 1。

用法：
    python scripts/check_diff_coverage.py
    python scripts/check_diff_coverage.py --base origin/main
    python scripts/check_diff_coverage.py --coverage-file coverage.json
    python scripts/check_diff_coverage.py --strict --threshold 80

退出码：
    0: advisory 模式（默认）且覆盖率达标，或无源码变更行，或变更行均为非可执行行
    1: --strict 模式下覆盖率低于阈值
    2: 无法确定覆盖率输入（coverage 文件缺失/不可读/非法、merge-base 不可用、
       git diff 失败），或 diff 含可执行变更行但所属文件未被 coverage 统计
       （未收录且不匹配 [tool.coverage.run].omit），或文件在 coverage 中已记录
       却缺测量数组/测量行数为 0 而源码存在可执行变更——fail-closed，不得静默通过
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 与 pyproject.toml [tool.coverage.run] source 保持一致
# "config"/"main" 是顶层模块（config.py/main.py），其余是目录
SOURCE_DIRS = frozenset(("core", "data", "services", "strategies", "utils", "ui", "app"))
SOURCE_FILES = frozenset(("config.py", "main.py"))

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")

# git 的 C 风格引号（core.quotePath 或路径含制表符/引号/反斜杠时启用）中转义序列
# 对应的单字节值。八进制转义（\ooo）单独处理。
_C_ESCAPE_BYTES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    "\\": 0x5C,
    '"': 0x22,
}
_C_OCTAL_DIGITS = frozenset("01234567")


class DiffCoverageInputError(RuntimeError):
    """无法确定 diff-coverage 输入（fail-closed 信号），由 main() 映射为 exit 2。"""


def _is_source_file(path: str) -> bool:
    """判断文件是否在覆盖率源码目录内。"""
    if not path.endswith(".py"):
        return False
    if path in SOURCE_FILES:
        return True
    top = path.split("/", 1)[0]
    return top in SOURCE_DIRS


def _decode_c_quoted_path(quoted: str, raw_header: str) -> str:
    """完整解码 git C 风格引号路径（含 ``\\ooo`` 八进制字节转义）。

    八进制转义按**字节**累积（如 ``\\344\\270\\255`` 是 UTF-8 的「中」），
    最终整体按 UTF-8 解码；非法 UTF-8 字节降级为替换符（U+FFFD）——该路径必然
    无法匹配 coverage 中的真实条目，下游按「未被统计/不可读」fail-closed，且避免
    surrogate 字符进入日志打印或文件系统操作触发未捕获异常。
    未闭合引号、未知转义、越界八进制一律抛 DiffCoverageInputError（fail-closed）。
    """
    if len(quoted) < 2 or not quoted.endswith('"'):
        raise DiffCoverageInputError(f"+++ 路径引号未闭合，无法解析 diff: {raw_header!r}")
    body = quoted[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        if i + 1 >= len(body):
            raise DiffCoverageInputError(f"+++ 路径以孤立反斜杠结尾，无法解析 diff: {raw_header!r}")
        esc = body[i + 1]
        if esc in _C_ESCAPE_BYTES:
            out.append(_C_ESCAPE_BYTES[esc])
            i += 2
            continue
        if esc in _C_OCTAL_DIGITS:
            digits = body[i + 1 : i + 4]
            if len(digits) != 3 or any(d not in _C_OCTAL_DIGITS for d in digits):
                raise DiffCoverageInputError(f"+++ 路径含非法八进制转义，无法解析 diff: {raw_header!r}")
            value = int(digits, 8)
            if value > 0xFF:
                raise DiffCoverageInputError(f"+++ 路径八进制转义越界（{digits}），无法解析 diff: {raw_header!r}")
            out.append(value)
            i += 4
            continue
        raise DiffCoverageInputError(f"+++ 路径含未知转义 \\{esc}，无法解析 diff: {raw_header!r}")
    return out.decode("utf-8", errors="replace")


def _parse_new_path(rest: str, raw_header: str) -> str | None:
    """解析 ``+++`` 行目标路径；``/dev/null``（删除）返回 None。

    兼容两种合法形态：``b/path``（core.quotePath=false 下的原样路径）与
    ``"b/path"``（含特殊字符时的 C 风格引号路径）。路径含空格时 git 会在末尾
    追加一个制表符作分隔（GNU diff 兼容），此处剥离。
    前缀非 ``b/`` 或引号/转义非法时抛 DiffCoverageInputError，不得静默清空。
    """
    # 删除文件：新侧为 /dev/null，无新增行
    if rest in ("/dev/null", "/dev/null\t"):
        return None
    if rest.startswith('"'):
        if not rest.endswith('"') and not rest.endswith('"\t'):
            raise DiffCoverageInputError(f"+++ 路径引号未闭合，无法解析 diff: {raw_header!r}")
        quoted = rest[:-1] if rest.endswith('"\t') else rest
        path = _decode_c_quoted_path(quoted, raw_header)
    else:
        # 未加引号：末尾制表符是 git 为含空格路径追加的分隔符
        path = rest[:-1] if rest.endswith("\t") else rest
    if not path.startswith("b/"):
        raise DiffCoverageInputError(f"+++ 路径缺少 b/ 前缀，无法解析 diff: {raw_header!r}")
    return path[2:]


def parse_diff(diff_text: str) -> dict[str, list[int]]:
    """解析 git diff 输出，提取源码文件的新增行号。

    返回 {filepath: [line_no, ...]}，仅包含源码目录下的 .py 文件。
    ``+++`` 路径按 git 的 C 风格引号规则完整解码（含 UTF-8 八进制字节），
    未知格式 fail-closed（抛 DiffCoverageInputError），不静默当作「无变更」。
    """
    added_lines: dict[str, list[int]] = {}
    current_file: str | None = None
    current_line = 0
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # 新文件块起始：重置状态，避免上一文件的路径/行号泄漏
            current_file = None
            current_line = 0
            in_hunk = False
        elif line.startswith("+++") and not in_hunk:
            # hunk 外的 +++ 只能是文件头；格式不认识就 fail-closed
            if not line.startswith("+++ "):
                raise DiffCoverageInputError(f"无法识别的 +++ 文件头: {line!r}")
            path = _parse_new_path(line[4:], line)
            current_file = path if path is not None and _is_source_file(path) else None
            if current_file is not None:
                added_lines.setdefault(current_file, [])
            current_line = 0
        elif line.startswith("@@"):
            in_hunk = True
            if current_file is not None:
                match = _HUNK_RE.match(line)
                current_line = int(match.group(1)) if match else 0
        elif in_hunk and current_file is not None and line.startswith("+"):
            if current_line > 0:
                added_lines[current_file].append(current_line)
            current_line += 1
        elif in_hunk and current_file is not None and line.startswith("-"):
            pass  # 删除行不影响新文件行号
        elif in_hunk and current_file is not None and line.startswith(" "):
            current_line += 1

    return {f: lines for f, lines in added_lines.items() if lines}


def _resolve_merge_base(base: str) -> str:
    """解析 base 与 HEAD 的真实 merge-base 提交。

    若以浅克隆方式更新 base（如 `git fetch --depth=1 origin main`），origin/main 会
    变成浅提交，`git diff base...HEAD` 的三点语法无法从无祖先的浅历史计算 merge-base，
    实际退化为两点 diff，把 main 侧并发改动一并计入本分支变更行（误报 diff-coverage）。
    故显式用 `git merge-base` 计算真实基点：成功返回该提交，失败（无共同祖先）返回空串。
    """
    result = subprocess.run(["git", "merge-base", base, "HEAD"], capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_diff_added_lines(base: str) -> dict[str, list[int]]:
    """获取 git diff 中新增的行号，返回 {filepath: [line_no, ...]}。

    以真实 merge-base 计算三点差异（仅本分支相对基点的改动），而非依赖 `...`,
    避免浅克隆下三点退化两点、误把 base 侧并发改动计入。
    """
    merge_base = _resolve_merge_base(base)
    if not merge_base:
        # 无法找到共同祖先（浅克隆/无 merge-base）：三点语义不可用。
        # 不静默退化为两点（会把 base 侧改动误算进来），且返回空 dict 会被误判为
        # 「无源码变更」而放行，故此处 fail-closed。
        raise DiffCoverageInputError(
            f"无法确定 merge-base（base={base}，可能为浅克隆、无共同祖先或非 git 仓库），无法计算 diff-coverage"
        )
    # diff 可能含非 UTF-8 字节（如 GB18030 编码的 HTML fixture 快照），
    # 按 UTF-8 宽松解码：非法字节以替换符兜底。本函数只取 +++/@@ 等 ASCII 结构行，
    # 内容行的替换符不影响行号解析，避免整步因 UnicodeDecodeError 失败。
    # 固定 core.quotePath=false：非 ASCII 路径原样输出 UTF-8，避免被转成
    # 八进制转义后与 coverage.json 中的真实路径不匹配。含制表符/引号/反斜杠的
    # 路径仍会被 git 加引号，由 parse_diff 完整解码兼容。
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--unified=0", "--no-color", merge_base, "HEAD"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise DiffCoverageInputError(f"git diff 失败（base={base}）: {result.stderr.strip()}")
    return parse_diff(result.stdout)


def _parse_line_list(value: object, filepath: str, field: str) -> set[int]:
    """校验 coverage 行号数组：必须是 list，元素为正整数（排除 bool），且无重复。

    以下均视为非法输入并抛 DiffCoverageInputError：数组缺失（由调用方判定）、
    类型错误、``true``/``1.0``/``"1"`` 等错型元素、``0``/负数、重复行号。
    """
    if not isinstance(value, list):
        raise DiffCoverageInputError(f"coverage 条目 {filepath} 的 {field} 必须为数组（实际 {type(value).__name__}）")
    lines: set[int] = set()
    for item in value:
        if type(item) is not int or item < 1:
            raise DiffCoverageInputError(
                f"coverage 条目 {filepath} 的 {field} 含非法行号 {item!r}（须为正整数，不含 bool）"
            )
        if item in lines:
            raise DiffCoverageInputError(f"coverage 条目 {filepath} 的 {field} 含重复行号: {item}")
        lines.add(item)
    return lines


def _check_summary_consistency(filepath: str, summary: object, statement_count: int) -> None:
    """条目含 coverage.py summary 时，num_statements 必须与测量行数一致。

    用于发现「测量数组被清空/截断但 summary 仍声明有语句」的矛盾数据。条目无
    summary 或 summary 无 num_statements（如精简夹具）时不校验。
    """
    if summary is None:
        return
    if not isinstance(summary, dict):
        raise DiffCoverageInputError(f"coverage 条目 {filepath} 的 summary 必须为 JSON 对象")
    if "num_statements" not in summary:
        return
    declared = summary["num_statements"]
    if type(declared) is not int or declared < 0:
        raise DiffCoverageInputError(f"coverage 条目 {filepath} 的 summary.num_statements 必须为非负整数")
    if declared != statement_count:
        raise DiffCoverageInputError(
            f"coverage 条目 {filepath} 的 summary.num_statements={declared} "
            f"与测量行数 {statement_count} 不一致（测量数组缺失或被截断）"
        )


def load_coverage(coverage_file: Path) -> dict[str, dict[str, set[int]]]:
    """加载 coverage.json，返回 {filepath: {"executed": set, "statements": set}}。

    statements = executed_lines ∪ missing_lines，即 coverage.py 识别的所有可执行行
    （含文档字符串，不含空行/纯注释）。用于过滤 diff 中的非可执行行。

    每个文件条目必须为 JSON 对象且同时含 executed_lines / missing_lines 数组
    （缺数组即「已记录但未测量」，不得按空 statements 静默跳过）；数组元素须为
    正整数、无重复、两数组不得相交；summary.num_statements 若存在须与测量行数
    一致。以上任一不满足、或不同原始键归一化后碰撞，均 fail-closed。

    路径规范化：coverage.py 在 Windows 上生成反斜杠路径（``app\\bootstrap.py``），
    而 git diff 始终使用正斜杠（``app/bootstrap.py``）。统一为正斜杠避免跨平台不匹配。
    """
    with open(coverage_file, encoding="utf-8") as f:
        cov = json.load(f)
    if not isinstance(cov, dict) or not isinstance(cov.get("files"), dict):
        raise DiffCoverageInputError(f"coverage 文件缺少合法的 files 字段（无法确定覆盖率输入）: {coverage_file}")
    result: dict[str, dict[str, set[int]]] = {}
    raw_keys: dict[str, str] = {}
    for fp, d in cov["files"].items():
        if not isinstance(d, dict):
            raise DiffCoverageInputError(f"coverage 条目 {fp} 必须为 JSON 对象（实际 {type(d).__name__}）")
        for field in ("executed_lines", "missing_lines"):
            if field not in d:
                raise DiffCoverageInputError(f"coverage 条目 {fp} 缺少 {field}（无法确认该文件已被测量）")
        executed = _parse_line_list(d["executed_lines"], fp, "executed_lines")
        missing = _parse_line_list(d["missing_lines"], fp, "missing_lines")
        overlap = executed & missing
        if overlap:
            raise DiffCoverageInputError(
                f"coverage 条目 {fp} 的行同时出现在 executed_lines 与 missing_lines: {sorted(overlap)[:5]}"
            )
        # 统一为 POSIX 风格路径（正斜杠），与 git diff 输出一致
        normalized_fp = fp.replace("\\", "/")
        if normalized_fp in result:
            raise DiffCoverageInputError(
                f"coverage 路径归一化后冲突: {raw_keys[normalized_fp]!r} 与 {fp!r} 均指向 {normalized_fp}"
            )
        raw_keys[normalized_fp] = fp
        statements = executed | missing
        _check_summary_consistency(fp, d.get("summary"), len(statements))
        result[normalized_fp] = {"executed": executed, "statements": statements}
    return result


def load_coverage_omit_patterns(pyproject_file: Path | None = None) -> tuple[str, ...]:
    """读取 [tool.coverage.run].omit 的 glob 列表。

    omit 文件不会出现在 coverage.json 中，其变更行需要与「未被测试统计」区分开：
    否则无法判定可执行性的变更会被误判为「文件未被覆盖」而 fail-closed。
    pyproject 缺失/不可解析时返回空元组（此时仅按源码内容兜底判定）。
    """
    path = pyproject_file or (ROOT / "pyproject.toml")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    run = data.get("tool", {}).get("coverage", {}).get("run", {})
    omit = run.get("omit", []) if isinstance(run, dict) else []
    if not isinstance(omit, list):
        return ()
    return tuple(str(pattern).replace("\\", "/") for pattern in omit if isinstance(pattern, str))


def _is_omitted(path: str, omit_patterns: Sequence[str]) -> bool:
    """判断文件是否匹配 coverage omit glob（如 main.py、scripts/*）。"""
    return any(fnmatch(path, pattern) for pattern in omit_patterns)


def find_unmeasured_files(
    diff_lines: dict[str, list[int]],
    coverage: dict[str, dict[str, set[int]]],
    omit_patterns: Sequence[str] = (),
) -> list[str]:
    """返回 diff 有新增行、但未被 coverage 统计且不匹配 omit 的文件。

    这些文件既不在 coverage.json 中，也非有意 omit，覆盖率数据无法说明其新增行
    是否可执行——调用方必须按源码内容判定或 fail-closed，不得当作「无变更」放行。
    """
    return sorted(
        filepath for filepath in diff_lines if filepath not in coverage and not _is_omitted(filepath, omit_patterns)
    )


def find_contradictory_measured_files(
    diff_lines: dict[str, list[int]],
    coverage: dict[str, dict[str, set[int]]],
    omit_patterns: Sequence[str] = (),
    root: Path | None = None,
) -> dict[str, list[int]]:
    """返回 coverage 已收录但 statements 为空、而 diff 新增行可执行的文件。

    coverage.py 会为 source 下所有文件报告 statements；条目已存在却没有任何可执行
    行，与源码中存在可执行新增行相矛盾（如测量数组被清空/截断），说明该文件并未
    真正测量，调用方须 fail-closed，不得把「键存在」当作测量完成。合法 0 statement
    文件（空模块/纯注释）的新增行按源码判定后为空，仍正常跳过。
    """
    contradictory: dict[str, list[int]] = {}
    for filepath, lines in diff_lines.items():
        file_cov = coverage.get(filepath)
        if file_cov is None or file_cov["statements"] or _is_omitted(filepath, omit_patterns):
            continue
        executable = find_executable_added_lines(filepath, lines, root=root)
        if executable:
            contradictory[filepath] = executable
    return contradictory


def find_executable_added_lines(filepath: str, lines: list[int], root: Path | None = None) -> list[int]:
    """按源码内容判定未被 coverage 统计文件中的新增行是否可执行。

    coverage.py 的 statements 含 docstring 行、不含空行与纯注释；此处以同一口径近似：
    去除首尾空白后为空或以 ``#`` 开头的行视为非可执行，其余视为可执行。据此区分
    「仅注释/空行改动」（合法跳过）与「真实代码改动」（无法测量，须 fail-closed）。
    文件不可读（缺失/编码异常）或行号越界时按可执行处理（fail-closed）。
    """
    base = root or ROOT
    try:
        content = (base / filepath).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return sorted(lines)
    executable: list[int] = []
    for line_no in lines:
        if line_no < 1 or line_no > len(content):
            executable.append(line_no)
            continue
        text = content[line_no - 1].strip()
        if text and not text.startswith("#"):
            executable.append(line_no)
    return sorted(executable)


def compute_diff_coverage(
    diff_lines: dict[str, list[int]],
    coverage: dict[str, dict[str, set[int]]],
) -> tuple[int, int, dict[str, list[int]]]:
    """计算 diff-coverage。

    只统计 diff 中属于 coverage.py statements 的行（排除空行、纯注释、
    被 omit 的文件）。返回 (covered_count, total_count, {filepath: [uncovered_lines]})。
    """
    covered = 0
    total = 0
    uncovered_by_file: dict[str, list[int]] = {}

    for filepath, lines in diff_lines.items():
        file_cov = coverage.get(filepath, {"executed": set(), "statements": set()})
        executed = file_cov["executed"]
        statements = file_cov["statements"]
        # 文件被 omit 或未被 coverage 统计 → 跳过（不惩罚 omit 文件的新增行）
        if not statements:
            continue
        # 只统计 diff 中属于 statements 的行（排除空行/注释/文档字符串等非可执行行）
        relevant = [ln for ln in lines if ln in statements]
        if not relevant:
            continue
        line_set = set(relevant)
        covered += len(line_set & executed)
        total += len(line_set)
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

    try:
        diff_lines = get_diff_added_lines(args.base)
    except DiffCoverageInputError as e:
        print(f"✗ {e}（fail-closed，exit 2）")
        return 2
    except OSError as e:
        print(f"✗ 无法执行 git 命令（base={args.base}）: {e}（fail-closed，exit 2）")
        return 2

    if not diff_lines:
        print("✓ 无源码变更行，跳过 diff-coverage 检查")
        return 0

    if not coverage_path.is_file():
        print(f"✗ coverage 文件不存在或不可读: {coverage_path}（fail-closed，exit 2）")
        return 2

    try:
        coverage = load_coverage(coverage_path)
    except DiffCoverageInputError as e:
        print(f"✗ {e}（fail-closed，exit 2）")
        return 2
    except (OSError, ValueError) as e:
        print(f"✗ coverage 文件不可读或不是合法 JSON: {coverage_path}: {e}（fail-closed，exit 2）")
        return 2

    if not coverage:
        print(f"✗ coverage 文件不含任何覆盖率数据: {coverage_path}（fail-closed，exit 2）")
        return 2

    covered, total, uncovered_by_file = compute_diff_coverage(diff_lines, coverage)

    # 分母为 0 的两种语义必须区分：仅非可执行行改动（合法跳过）vs 可执行变更
    # 所属文件未被 coverage 统计（无法测量，fail-closed）。对后者按源码内容判定
    # 新增行是否可执行，避免把「文件未收录」一律当作「无变更」而假通过。
    omit_patterns = load_coverage_omit_patterns()
    unmeasured_files = find_unmeasured_files(diff_lines, coverage, omit_patterns)
    executable_unmeasured: dict[str, list[int]] = {}
    for filepath in unmeasured_files:
        executable_lines = find_executable_added_lines(filepath, diff_lines[filepath], root=ROOT)
        if executable_lines:
            executable_unmeasured[filepath] = executable_lines

    if executable_unmeasured:
        print("✗ diff 含可执行变更行，但以下文件未被 coverage 统计（未收录且不匹配 omit）：")
        for filepath, lines in executable_unmeasured.items():
            print(f"  {filepath}:")
            for ln in lines:
                print(f"    L{ln}")
        print()
        print("无法用覆盖率数据判定这些变更行是否被测试覆盖，fail-closed（exit 2）。")
        print("请确认该文件已被测试导入并纳入 coverage，或在 pyproject.toml 的 omit 中显式声明。")
        return 2

    # 文件在 coverage 中「已记录」不等于「已测量」：条目缺测量数组已在 load_coverage
    # 拦截；此处兜底 statements 为空（空数组/空集合）却存在可执行新增行的矛盾数据。
    contradictory = find_contradictory_measured_files(diff_lines, coverage, omit_patterns, root=ROOT)
    if contradictory:
        print("✗ 以下文件在 coverage 中已记录，但无可执行行统计（statements 为空），与 diff 的可执行变更矛盾：")
        for filepath, lines in contradictory.items():
            print(f"  {filepath}:")
            for ln in lines:
                print(f"    L{ln}")
        print()
        print("coverage 数据未能证明这些文件已被测量（测量数组缺失或被截断），fail-closed（exit 2）。")
        print("请重新生成 coverage.json，确认每个文件条目含完整的 executed_lines / missing_lines。")
        return 2

    if total == 0:
        print("✓ diff 仅含非可执行行（相关文件均由 coverage 统计或匹配 omit），跳过 diff-coverage 检查")
        return 0

    pct = covered / total * 100

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
