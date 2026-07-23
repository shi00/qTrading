"""单个文件覆盖率门禁 (D37 分层阈值).

读取 pyproject.toml [tool.custom_coverage] 配置:
- per_file_minimum: 默认阈值（未匹配分层路径的文件），始终 enforce
- per_file_minimum_by_path: 按目录分层阈值（最长前缀匹配）
- enforce_layered: True=分层阈值阻断, False=advisory 仅警告

用法::

    python scripts/check_per_file_coverage.py

前置：先跑 ``pytest --cov --cov-report=json`` 生成 coverage.json。

退出码:
    0: 通过（advisory 模式下分层警告不阻断）
    1: 有文件低于默认阈值，或 enforce_layered=true 时低于分层阈值
    2: coverage.json 未找到 / 配置错误
"""

import json
import sys
import tomllib
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parent.parent


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


def main() -> int:
    default_threshold, layered, enforce_layered = load_config()
    cov_path = ROOT / "coverage.json"
    if not cov_path.exists():
        print("coverage.json not found. Run pytest with --cov --cov-report=json first.")
        sys.exit(2)

    with open(cov_path, encoding="utf-8") as f:
        cov = json.load(f)

    files = cov.get("files", {})
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
