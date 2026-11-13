"""pre-commit hook: R3 type: ignore 原因检查（review07-G5 务实版）。

分级规则：
- 裸 ``# type: ignore``（无 ``[error-code]``，含裸 ignore 加 human reason）一律
  ERROR：生产代码（core/data/services/strategies/utils/ui/app）与 tests/ 同判
  （R3 为 INVARIANT；review07-G5 的豁免仅限带码 ``attr-defined`` 的 human
  reason 要求，不豁免裸 ignore）。
- tests/：``# type: ignore[attr-defined]`` 豁免（mock 替身场景，无需 human reason）；
  其他 ``[error-code]``（arg-type/return-value/assignment 等）建议带 human reason
  （形如 ``# type: ignore[arg-type]  # <原因>``）。

存量策略（渐进部署）：tests/ 下"非 attr-defined 且无 human reason"存量 226 处
（2026-08-26 盘点），>5 处，故该子项以 WARNING 输出不阻断；升级触发条件：
存量 ≤5 处或 2026-11-30 前转为 ERROR（见 docs/debt/known-technical-debt.md 登记）。
裸 ignore 不在此渐进范围内（生产与 tests/ 均为 ERROR）。

检查面：仅检查 tokenize 产出的 COMMENT token（字符串/文档字符串中的示例文本不计入）；
文件无法被 tokenize 解析时 fail-closed 报错，不静默放行。

检测边界（C11/F16 回归锚定，coverage 保持 partial 不签 full）：
- `.py` 与 `.pyi` 均受检（入口不过滤 stub）；
- `# type: ignore` 行尾 / `# 理由` / 裸文本（无码无理由）→ ERROR；
- `# type: ignore[code]` 与 `# type: ignore [code]`（空格分隔）→ 带码形态；
- 错误码须为合法形态：kebab-case（mypy：attr-defined）或 camelCase（pyright 诊断规则名：
  reportAttributeAccessIssue）；大写开头/下划线/数字/空括号伪错误码 → ERROR；
- 错误码是否为 pyright 真实支持的诊断码由 pyright 运行层报警，本检查不冒充（F21）。

用法（由 pre-commit 调用，文件名作为参数传入）::

    python scripts/check_type_ignore_reason.py <file1> <file2> ...

退出码：0 通过（含 WARNING）；1 发现裸 type: ignore（无 error-code，生产与
tests/ 同判）。
"""

import io
import re
import sys
import tokenize
from pathlib import Path

# 生产目录（R3 原规则强制 ERROR 的目录）
_PROD_PREFIXES = ("core", "data", "services", "strategies", "utils", "ui", "app")
# （C11/F16 边界扩展）任意形态 type-ignore 注释定位：type 冒号 ignore 后跟行尾 / # 理由 /
# 裸文本 / `[code]`（含空格分隔形态）。旧 _BARE_RE/_CODED_RE 双正则对
# 「空格分隔带码」「后接裸文本」「伪错误码」三种形态是盲区。
# （本注释避免写出该注释字面形态——本检查器会扫描本文件自身注释，自指即拦截。）
_ANY_IGNORE_RE = re.compile(r"# type:\s?ignore(?![a-zA-Z])")
# 合法 pyright/mypy 错误码形态（首字符小写字母，后续小写/大写字母与连字符）：
# - kebab-case（mypy 形态）：attr-defined / import-untyped 等；
# - camelCase（pyright 诊断规则名形态）：reportMissingImports / reportAttributeAccessIssue 等
#   （仓库既有用法，kebab-only 校验会误杀全仓既有 ignore——C11/F16 回归锚定）。
# 伪错误码（大写开头/下划线/数字/空括号）视同「未提供有效 error-code」→ R3 ERROR；
# 错误码全集合法性由 pyright/mypy 运行层报警，本检查只守护形态（不冒充，F21 语义）。
_VALID_ERROR_CODE_RE = re.compile(r"[a-z][a-zA-Z\-]*")
# 方括号错误码提取（允许 `ignore [code]` 的空格分隔——pyright 合法形态）
_BRACKET_RE = re.compile(r"\s*\[([^\]]*)\]")
# 带 human reason 的判定：error-code 之后同行有 `#` + 内容
_REASON_RE = re.compile(r"#[ \t]+\S")

_TESTS_PREFIX = "tests"


def _is_test_file(path: Path) -> bool:
    parts = path.parts
    return any(p == _TESTS_PREFIX for p in parts) or str(path).replace("\\", "/").startswith("tests/")


def _iter_comments(lines: list[str]) -> list[tuple[int, str]]:
    """用 tokenize 提取注释 token，返回 (行号, 注释文本)；字符串/文档字符串不计入。"""
    reader = io.StringIO("\n".join(lines)).readline
    return [
        (token.start[0], token.string) for token in tokenize.generate_tokens(reader) if token.type == tokenize.COMMENT
    ]


def check_file(path: Path) -> tuple[list[str], list[str]]:
    """检查单个文件，返回 (errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return errors, warnings

    try:
        comments = _iter_comments(lines)
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as exc:
        # fail-closed：无法解析则无法确认是否存在裸 ignore，必须报错而非静默放行
        errors.append(f"{path}: 无法完成 type: ignore 注释检查（tokenize 解析失败：{exc.__class__.__name__}）")
        return errors, warnings

    is_test = _is_test_file(path)
    for lineno, comment in comments:
        for match in _ANY_IGNORE_RE.finditer(comment):
            rest = comment[match.end() :]
            code_match = _BRACKET_RE.match(rest)
            if code_match is None:
                # 裸 ignore（无 error-code）一律 ERROR：R3 为 INVARIANT，tests/ 不豁免。
                # 覆盖行尾 / `# 理由` / ignore 后接裸文本（无码无理由）三种形态。
                errors.append(f"{path}:{lineno}: R3 模糊压制 — # type: ignore 必须带 [error-code]")
                continue
            code = code_match.group(1)
            if not _VALID_ERROR_CODE_RE.fullmatch(code):
                # 伪错误码（大写/下划线/数字/空括号）= 未提供有效 error-code（R3 ERROR）
                errors.append(
                    f"{path}:{lineno}: R3 模糊压制 — 伪错误码 `[{code}]` 须为合法形态（mypy kebab-case 或 pyright camelCase）"
                    "（视同未提供有效 [error-code]）"
                )
                continue
            if is_test and code == "attr-defined":
                # mock 替身场景：attr-defined 免于 human reason（工程决策，报告 G5 务实版）
                continue
            if is_test and not _REASON_RE.search(rest[code_match.end() :]):
                warnings.append(
                    f"{path}:{lineno}: R3（tests/）— # type: ignore[{code}] 建议带 human reason"
                    "（`# type: ignore[code]  # <原因>`）；存量阶段为 WARNING，存量≤5 后转 ERROR"
                )
    return errors, warnings


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # pre-commit 的 types_or 含 python/pyi，stub（.pyi）同样受 R3 约束，不可在入口过滤掉
    files = [Path(f) for f in sys.argv[1:] if f.endswith((".py", ".pyi"))]
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
