"""守护: UI 文件不得包含 emoji / dingbat 字符 (P2-7).

P2-7 决策: UI 显示文本不依赖 emoji 字体, 所有状态/语气通过 ft.Icon / AppColors 表达.
本测试动态扫描 ui/ 下所有 .py 文件 (不含 __pycache__), 拦截 emoji 回归.

豁免 (由 AST 判定保证, 不依赖行首启发):
- docstring 中的 emoji 描述 (如 "P2-7: ⚠️ → 文本符号" 这类历史说明)
- 注释中的 emoji (注释不进入 AST)
- 业务层 services/data 不在此扫描范围 (本测试仅约束 ui/)
"""

from __future__ import annotations

import ast
import pathlib
import re
import textwrap

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCAN_DIR = _PROJECT_ROOT / "ui"

# Emoji / Dingbat Unicode 范围 (基本块 + 扩展块)
# U+2600-U+26FF  Miscellaneous Symbols (✓✗⚠☀☂ etc.)
# U+2700-U+27BF  Dingbats (✀-➿)
# U+1F300-U+1F9FF  Emoji & Pictographs (📈📅💡📰 etc.)
# U+1FA00-U+1FAFF  Emoji Extended-A
# U+FE0F         Variation Selector-16 (emoji 修饰符)
_EMOJI_PATTERN = re.compile(
    "["
    "\u2600-\u26ff"  # Misc Symbols (含 ✓✗⚠)
    "\u2700-\u27bf"  # Dingbats
    "\U0001f300-\U0001f9ff"  # Emoji & Pictographs
    "\U0001fa00-\U0001faff"  # Emoji Extended-A
    "\ufe0f"  # Variation Selector-16
    "]"
)

_DOCSTRING_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """收集模块/函数/类 docstring 字符串常量节点的 id, 供 AST 扫描排除."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            ids.add(id(value))
    return ids


def _find_emoji_violations(source: str) -> list[tuple[int, str]]:
    """AST 扫描源码中的字符串字面量, 返回 ``(行号, 描述)`` 列表.

    只检查字符串字面值: docstring 与注释不参与判定 (注释不进入 AST).
    f-string 的常量片段同样检查; 行号按 emoji 实际所在行计算.
    """
    tree = ast.parse(source)
    docstrings = _docstring_constant_ids(tree)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        for m in _EMOJI_PATTERN.finditer(node.value):
            char = m.group(0)
            line_no = node.lineno + node.value[: m.start()].count("\n")
            violations.append((line_no, f"{char!r} (U+{ord(char):04X})"))
    return sorted(violations)


def _scan_file_emoji(f: pathlib.Path) -> list[str]:
    """扫描单个 UI 源文件, 返回带相对路径与行号的违规描述."""
    src = f.read_text(encoding="utf-8")
    lines = src.splitlines()
    rel = f.relative_to(_PROJECT_ROOT)
    offenders: list[str] = []
    for line_no, desc in _find_emoji_violations(src):
        snippet = lines[line_no - 1].strip()[:80] if line_no <= len(lines) else ""
        offenders.append(f"{rel}:{line_no}: {desc} in: {snippet}")
    return offenders


def test_no_emoji_in_ui_source():
    """UI 源代码 (.py) 不得包含 emoji / dingbat 字符."""
    offenders: list[str] = []
    for f in sorted(_SCAN_DIR.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        offenders.extend(_scan_file_emoji(f))
    assert not offenders, (
        "UI 源代码不得包含 emoji / dingbat 字符 (P2-7). 状态/语气请用 ft.Icon / AppColors 表达:\n"
        + "\n".join(offenders)
    )


def _violations(source: str) -> list[tuple[int, str]]:
    """测试辅助: 对受控源码片段运行 AST 扫描."""
    return _find_emoji_violations(textwrap.dedent(source).strip("\n"))


def test_ignores_docstrings_and_comments():
    """模块/类/函数 (含多行) docstring 与行首/行尾注释中的 emoji 不误报."""
    source = '''
        """模块说明 ⚠️ 仅为文档."""

        # 行首注释 ✓

        class C:
            """类说明 📈."""

            def render(self):
                """函数说明:
                ✗ 多行 docstring
                """
                # 行尾注释中的 📈 不报
                return "纯文本"

        ft.Text("无表情")
    '''
    assert _violations(source) == []


def test_detects_emoji_in_real_string_literals():
    """真实显示字符串 (含 f-string 常量片段) 中的 emoji 必须报出精确行号."""
    source = """
        ft.Text("📈 涨幅")
        ft.Text(f"✓ {value}")
        value = "普通字符串 ✅"
        # ft.Text("注释中的 📈 不报")
    """
    assert _violations(source) == [
        (1, "'📈' (U+1F4C8)"),
        (2, "'✓' (U+2713)"),
        (3, "'✅' (U+2705)"),
    ]
