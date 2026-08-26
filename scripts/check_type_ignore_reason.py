"""pre-commit hook: R3 type: ignore 原因检查（review07-G5 务实版）。

分级规则：
- 生产代码（core/data/services/strategies/utils/ui/app）：``# type: ignore`` 必须带
  ``[error-code]``（原规则，ERROR；如 ``# type: ignore[attr-defined]``）。
- tests/：``# type: ignore[attr-defined]`` 豁免（mock 替身场景，无需 human reason）；
  其他 ``[error-code]``（arg-type/return-value/assignment 等）建议带 human reason
  （形如 ``# type: ignore[arg-type]  # <原因>``）。

存量策略（渐进部署）：tests/ 下"非 attr-defined 且无 human reason"存量 226 处
（2026-08-26 盘点），>5 处，故本规则以 WARNING 输出不阻断；升级触发条件：
存量 ≤5 处或 2026-11-30 前转为 ERROR（见 docs/debt/known-technical-debt.md 登记）。

用法（由 pre-commit 调用，文件名作为参数传入）::

    python scripts/check_type_ignore_reason.py <file1> <file2> ...

退出码：0 通过（含 WARNING）；1 发现生产代码裸 type: ignore（无 error-code）。
"""

import re
import sys
from pathlib import Path

# 生产目录（R3 原规则强制 ERROR 的目录）
_PROD_PREFIXES = ("core", "data", "services", "strategies", "utils", "ui", "app")
# 裸 type: ignore（无 [error-code]）——违反 R3 原规则
_BARE_RE = re.compile(r"# type:\s?ignore(\s+#|$)")
# 带 error-code 的 type: ignore（[xxx]）
_CODED_RE = re.compile(r"# type:\s?ignore\[([a-z\-]+)\]")
# 带 human reason 的判定：error-code 之后同行有内容非仅结尾（含 `#` + 备注）
_REASON_RE = re.compile(r"# type:\s?ignore\[[a-z\-]+\]\s*#[ \t]+\S")

_TESTS_PREFIX = "tests"


def _is_test_file(path: Path) -> bool:
    parts = path.parts
    return any(p == _TESTS_PREFIX for p in parts) or str(path).replace("\\", "/").startswith("tests/")


def check_file(path: Path) -> tuple[list[str], list[str]]:
    """检查单个文件，返回 (errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return errors, warnings

    is_test = _is_test_file(path)
    for lineno, line in enumerate(lines, 1):
        bare = _BARE_RE.search(line)
        if bare and "# type:" in line:
            if not is_test:
                errors.append(f"{path}:{lineno}: R3 模糊压制 — # type: ignore 必须带 [error-code]")
            continue
        m = _CODED_RE.search(line)
        if m is None:
            continue
        code = m.group(1)
        if is_test and code == "attr-defined":
            # mock 替身场景：attr-defined 免于 human reason（工程决策，报告 G5 务实版）
            continue
        if is_test and not _REASON_RE.search(line):
            warnings.append(
                f"{path}:{lineno}: R3（tests/）— # type: ignore[{code}] 建议带 human reason"
                "（`# type: ignore[code]  # <原因>`）；存量阶段为 WARNING，存量≤5 后转 ERROR"
            )
    return errors, warnings


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = [Path(f) for f in sys.argv[1:] if f.endswith(".py")]
    if not files:
        return 0

    errors: list[str] = []
    warnings: list[str] = []
    for f in files:
        e, w = check_file(f)
        errors.extend(e)
        warnings.extend(w)

    if warnings:
        print("::warning::R3（tests/）存在带 [error-code] 但无 human reason 的 # type: ignore（渐进部署，不阻断）：")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("✗ R3 type: ignore 检查失败：")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
