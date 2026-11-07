"""单个文件覆盖率门禁 (D37 分层阈值).

读取 pyproject.toml [tool.custom_coverage] 配置:
- per_file_minimum: 默认阈值（未匹配分层路径的文件），始终 enforce
- per_file_minimum_by_path: 按目录分层阈值（最长前缀匹配）
- enforce_layered: True=分层阈值阻断, False=advisory 仅警告

用法::

    python scripts/check_per_file_coverage.py
    python scripts/check_per_file_coverage.py --report   # 分层达标率统计（不阻断，review07-G14 测量）

前置：先跑 ``pytest --cov --cov-report=json`` 生成 coverage.json。

退出码:
    0: 通过（advisory 模式下分层警告不阻断）；或所有文件均无可执行语句（SKIP，不代表达标）
    1: 有文件低于默认阈值，或 enforce_layered=true 时低于分层阈值
    2: coverage.json 未找到 / 不可读取 / 结构非法 / 配置错误
"""

import argparse
import json
import math
import sys
import tomllib
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parent.parent


class CoverageDataError(ValueError):
    """coverage.json 结构非法（fail-closed：输入不可信时不得静默判 PASS）。"""


def load_config() -> tuple[int, list[tuple[str, int]], bool]:
    """读取 [tool.custom_coverage] 配置.

    返回 (default_threshold, layered_sorted, enforce_layered):
    - layered_sorted 按 prefix 长度降序，保证最长前缀优先匹配
    """
    pyproject = ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        cfg = tomllib.load(f)
    custom = cfg.get("tool", {}).get("custom_coverage", {})
    default_threshold = custom.get("per_file_minimum", 80)
    by_path = custom.get("per_file_minimum_by_path", {})
    # 按前缀长度降序，最长前缀优先
    layered_sorted = sorted(by_path.items(), key=lambda kv: len(kv[0]), reverse=True)
    enforce_layered = custom.get("enforce_layered", True)
    return default_threshold, layered_sorted, enforce_layered


def match_threshold(file_path: str, default_threshold: int, layered: list[tuple[str, int]]) -> tuple[int, str | None]:
    """按最长前缀匹配返回文件应适用的阈值.

    路径归一化为 / 分隔后匹配，前缀去掉尾部 / 后做 startswith 判断。
    """
    normalized = PurePath(file_path.replace("\\", "/")).as_posix().lstrip("./")
    for prefix, threshold in layered:
        prefix_clean = prefix.rstrip("/")
        if prefix_clean and normalized.startswith(prefix_clean + "/"):
            return threshold, prefix
    return default_threshold, None


def _validate_file_entry(file_path: object, entry: object) -> None:
    """校验单个文件的 summary 结构，非法时抛 CoverageDataError。

    num_statements / missing_lines 用 ``type(...) is int`` 而非 isinstance：
    JSON 中 bool 是 int 子类，``True`` 不得被当作合法语句数。
    """
    if not isinstance(file_path, str):
        raise CoverageDataError(f"files 键必须为字符串: {file_path!r}")
    if not isinstance(entry, dict):
        raise CoverageDataError(f"{file_path}: 文件条目必须为 JSON 对象")
    summary = entry.get("summary")
    if not isinstance(summary, dict):
        raise CoverageDataError(f"{file_path}: summary 必须为 JSON 对象")

    stmts = summary.get("num_statements")
    if type(stmts) is not int or stmts < 0:
        raise CoverageDataError(f"{file_path}: num_statements 必须为非负整数")

    missing = summary.get("missing_lines")
    if type(missing) is not int or not 0 <= missing <= stmts:
        raise CoverageDataError(f"{file_path}: missing_lines 必须为 [0, {stmts}] 内整数")

    pct = summary.get("percent_covered")
    if isinstance(pct, bool) or not isinstance(pct, int | float) or not math.isfinite(pct) or not 0 <= pct <= 100:
        raise CoverageDataError(f"{file_path}: percent_covered 必须为 [0, 100] 内有限数值")


def _load_validated_files(cov_path: Path) -> dict[str, dict]:
    """读取并校验 coverage.json，返回 files 映射；任何异常结构均抛 CoverageDataError。

    缺 files / files 为空 / 条目结构损坏都视为输入不可信，由调用方以退出码 2 失败，
    避免「无输入」被误判为「全部达标」（fail-closed）。
    """
    try:
        with open(cov_path, encoding="utf-8") as f:
            cov = json.load(f)
    except (OSError, ValueError) as exc:
        raise CoverageDataError(f"无法读取 {cov_path}: {exc}") from exc

    if not isinstance(cov, dict):
        raise CoverageDataError("coverage.json 顶层必须为 JSON 对象")
    files = cov.get("files")
    if not isinstance(files, dict) or not files:
        raise CoverageDataError("coverage.json 缺少非空 files 对象")

    for file_path, entry in files.items():
        _validate_file_entry(file_path, entry)
    return files


def _report_layered(files: dict, default_threshold: int, layered: list[tuple[str, int]]) -> None:
    """review07-G14: 输出各分层前缀的达标率统计（不阻断，供 CI 观测分层门禁差距）。

    按 match_threshold 分组，统计每前缀：文件总数 / 达标数 / 平均覆盖率 / 未达标文件样例。
    未匹配分层的文件归入 "(default)"。输出到 stderr（::warning:: 语义，不改变退出码）。
    """
    groups: dict[str, dict] = {}
    for fp, d in sorted(files.items()):
        summary = d.get("summary", {})
        stmts = summary.get("num_statements", 0)
        if stmts == 0:
            continue
        pct = summary.get("percent_covered", 0)
        threshold, matched_prefix = match_threshold(fp, default_threshold, layered)
        key = matched_prefix or "(default)"
        g = groups.setdefault(
            key,
            {"threshold": threshold, "total": 0, "pass": 0, "sum_pct": 0.0, "below": []},
        )
        g["total"] += 1
        g["sum_pct"] += pct
        if pct >= threshold:
            g["pass"] += 1
        else:
            g["below"].append(f"{fp}({pct:.0f}%)")

    lines = ["::warning::[分层覆盖率] 达标率统计（review07-G14 测量，不阻断）："]
    for key, g in sorted(groups.items()):
        avg = g["sum_pct"] / g["total"] if g["total"] else 0.0
        status = f"{g['pass']}/{g['total']} 达标" if g["pass"] == g["total"] else f"{g['pass']}/{g['total']} 达标 ⚠"
        lines.append(
            f"  - {key:<12} threshold={g['threshold']}%  {status}  平均 {avg:.1f}%"
            + (f"  未达标: {', '.join(g['below'][:5])}" if g["below"] else "")
        )
    print("\n".join(lines), file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-file coverage gate (D37).")
    parser.add_argument(
        "--report",
        action="store_true",
        help="输出分层达标率统计（不阻断，用于 review07-G14 测量分层门禁差距）",
    )
    args = parser.parse_args()

    default_threshold, layered, enforce_layered = load_config()
    cov_path = ROOT / "coverage.json"

    # 输入校验必须先于 --report 分支：两种模式都不得在损坏/空输入上静默返回 0
    try:
        files = _load_validated_files(cov_path)
    except CoverageDataError as exc:
        print(f"ERROR: {exc}")
        return 2

    # 所有文件均无可执行语句：既非达标也非失败，显式 SKIP（不得宣称覆盖达标）
    if all(entry.get("summary", {}).get("num_statements", 0) == 0 for entry in files.values()):
        print("SKIP: coverage.json 中所有文件 num_statements 均为 0，无可执行语句可统计，不构成覆盖通过结论。")
        return 0

    # --report 模式：输出各分层前缀的达标率统计（不阻断），用于判断是否可开启 enforce_layered
    if args.report:
        _report_layered(files, default_threshold, layered)
        return 0

    below_default: list[tuple[str, float, int, int, int, str | None]] = []
    below_layered: list[tuple[str, float, int, int, int, str | None]] = []

    for fp, d in sorted(files.items()):
        summary = d.get("summary", {})
        pct = summary.get("percent_covered", 0)
        stmts = summary.get("num_statements", 0)
        if stmts == 0:
            continue
        threshold, matched_prefix = match_threshold(fp, default_threshold, layered)
        miss = summary.get("missing_lines", 0)
        if pct < default_threshold:
            below_default.append((fp, pct, stmts, miss, threshold, matched_prefix))
        elif pct < threshold:
            # 仅当分层阈值 > 默认阈值时才会进入此分支
            below_layered.append((fp, pct, stmts, miss, threshold, matched_prefix))

    # 默认阈值始终 enforce
    if below_default:
        print(f"FAIL: {len(below_default)} file(s) below {default_threshold}% (default threshold):\n")
        print(f"{'File':<70} {'Cov%':>6} {'Thresh':>7} {'Stmts':>6} {'Miss':>6}")
        print("-" * 98)
        for fp, pct, s, m, t, _ in below_default:
            print(f"{fp:<70} {pct:>5.1f}% {t:>6}% {s:>6} {m:>6}")
        return 1

    # 分层阈值：enforce 阻断，advisory 仅警告
    if below_layered:
        mode = "ENFORCE" if enforce_layered else "ADVISORY"
        print(f"[{mode}] {len(below_layered)} file(s) below layered threshold:\n")
        print(f"{'File':<70} {'Cov%':>6} {'Thresh':>7} {'Prefix':>14} {'Stmts':>6} {'Miss':>6}")
        print("-" * 112)
        for fp, pct, s, m, t, p in below_layered:
            print(f"{fp:<70} {pct:>5.1f}% {t:>6}% {(p or '-'):>14} {s:>6} {m:>6}")
        if enforce_layered:
            return 1
        print(f"\nPASS: All files >= {default_threshold}% (default). {len(below_layered)} advisory warnings above.")
        return 0

    print(
        f"PASS: All source files have coverage >= required threshold (default {default_threshold}%, layered enforce={enforce_layered})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
