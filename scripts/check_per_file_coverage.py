import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_threshold():
    pyproject = ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        cfg = tomllib.load(f)
    custom = cfg.get("tool", {}).get("custom_coverage", {})
    return custom.get("per_file_minimum", 80)


def main():
    threshold = load_threshold()
    cov_path = ROOT / "coverage.json"
    if not cov_path.exists():
        print("coverage.json not found. Run pytest with --cov --cov-report=json first.")
        sys.exit(2)

    with open(cov_path, encoding="utf-8") as f:
        cov = json.load(f)

    files = cov.get("files", {})
    below = []
    for fp, d in sorted(files.items()):
        summary = d.get("summary", {})
        pct = summary.get("percent_covered", 0)
        stmts = summary.get("num_statements", 0)
        if stmts > 0 and pct < threshold:
            below.append((fp, pct, stmts, summary.get("missing_lines", 0)))

    if below:
        print(f"FAIL: {len(below)} file(s) below {threshold}% coverage:\n")
        print(f"{'File':<70} {'Cov%':>6} {'Stmts':>6} {'Miss':>6}")
        print("-" * 90)
        for fp, pct, s, m in below:
            print(f"{fp:<70} {pct:>5.1f}% {s:>6} {m:>6}")
        sys.exit(1)
    else:
        print(f"PASS: All source files have coverage >= {threshold}%")
        sys.exit(0)


if __name__ == "__main__":
    main()
