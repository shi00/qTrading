"""守护: UI 文件不得使用 size=N / text_size=N / icon_size=N 魔术数字 (P1-1).

P1-1 决策: 所有字号必须引用 ``AppStyles.FONT_SIZE_*`` token, 消除硬编码数值.
本测试动态扫描 ui/ 下所有 .py 文件 (不含 __pycache__ / theme.py), 拦截魔术数字回归.

豁免 (由 AST 判定保证, 不依赖行首启发):
- theme.py (token 定义源头, 合法存在 ``FONT_SIZE_* = 11`` 等)
- 注释/docstring 中的数值说明 (注释不在 AST 中; docstring 不是调用参数)
- 非字号属性 (width/height/border_radius/spacing 等不在此约束)
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

# 字号/图标尺寸关键字参数或属性
_FONT_SIZE_KEYWORDS = frozenset({"size", "text_size", "icon_size"})

# ``size`` 在非字号控件上可能表示无关语义 (如分页大小), 仅在真实字号控件调用上判定;
# ``text_size`` / ``icon_size`` 名称本身即字号/图标尺寸语义, 任意调用均判定.
_SIZE_KEYWORD_CONTROLS = frozenset({"Text", "TextStyle", "Icon"})


def _callee_name(node: ast.Call) -> str | None:
    """返回调用名 (``ft.Text(...)`` -> ``"Text"``), 无法静态确定时返回 None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_numeric_literal(node: ast.expr) -> bool:
    """判断节点是否为数值字面量 (排除 bool: bool 是 int 子类但非尺寸语义)."""
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def _find_magic_font_size_violations(source: str) -> list[tuple[int, str]]:
    """AST 扫描源码中的字号魔术数字, 返回 ``(行号, 描述)`` 列表.

    - 仅匹配关键字参数/属性赋值中的真实字号参数 (跨行调用天然支持);
    - 注释与 docstring 在 AST 中不是调用参数, 天然排除;
    - 数值字面量才违规, token/变量/复杂表达式放行.
    """
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            is_size_control = _callee_name(node) in _SIZE_KEYWORD_CONTROLS
            for kw in node.keywords:
                if kw.arg not in _FONT_SIZE_KEYWORDS:
                    continue
                if kw.arg == "size" and not is_size_control:
                    continue
                if not _is_numeric_literal(kw.value):
                    continue
                violations.append((kw.value.lineno, f"{kw.arg}={kw.value.value}"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None or not _is_numeric_literal(value):
                continue
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in _FONT_SIZE_KEYWORDS:
                    violations.append((target.lineno, f".{target.attr}={value.value}"))
    return sorted(violations)


def _scan_file_magic_font_size(f: pathlib.Path) -> list[str]:
    """扫描单个 UI 源文件, 返回带相对路径与行号的违规描述."""
    src = f.read_text(encoding="utf-8")
    lines = src.splitlines()
    rel = f.relative_to(_PROJECT_ROOT)
    offenders: list[str] = []
    for line_no, expr in _find_magic_font_size_violations(src):
        snippet = lines[line_no - 1].strip()[:80] if line_no <= len(lines) else ""
        offenders.append(f"{rel}:{line_no}: {expr} in: {snippet}")
    return offenders


def test_no_magic_font_size_in_ui():
    """UI 源代码 (.py) 的 size/text_size/icon_size 不得使用字面数值."""
    offenders: list[str] = []
    for f in sorted(_SCAN_DIR.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        if f.name == "theme.py":
            continue  # token 定义源头
        offenders.extend(_scan_file_magic_font_size(f))
    assert not offenders, (
        "UI 源代码的 size/text_size/icon_size 不得使用字面数值 (P1-1). "
        "请改用 AppStyles.FONT_SIZE_CAPTION/BODY_SM/BODY/LG/TITLE/HEADLINE/XL/DISPLAY:\n" + "\n".join(offenders)
    )


def _violations(source: str) -> list[tuple[int, str]]:
    """测试辅助: 对受控源码片段运行 AST 扫描."""
    return _find_magic_font_size_violations(textwrap.dedent(source).strip("\n"))


def test_detects_spaced_cross_line_and_attribute_size_literals():
    """紧贴/等号空白/跨行/属性赋值写法必须等价报出, 且行号精确."""
    source = """
        ft.Text(label, size = 16)
        ft.Text(
            label,
            size=13,
        )
        ft.Icon(icon, size=20)
        ft.TextStyle(size=12)
        ft.TextField(text_size=14)
        icon.size = 18
    """
    assert _violations(source) == [
        (1, "size=16"),
        (4, "size=13"),
        (6, "size=20"),
        (7, "size=12"),
        (8, "text_size=14"),
        (9, ".size=18"),
    ]


def test_ignores_docstrings_trailing_comments_and_tokens():
    """模块/嵌套函数 docstring、行尾注释、token/变量与无关参数均不误报."""
    source = '''
        """模块说明: size=13 仅为文档示例."""
        # 行首注释 size=13

        def build(label):
            """函数说明: size=13."""
            return ft.Text(label, size=AppStyles.FONT_SIZE_BODY)  # size=13

        api.request(size=10)
        ft.Text(label, max_lines=16, width=200)
        ft.Text(label, size=some_variable)
        ft.Text(**kwargs)
        container.width = 200
    '''
    assert _violations(source) == []


# 页面主标题必须使用 FONT_SIZE_XL (Issue #445)
_VIEWS_DIR = _PROJECT_ROOT / "ui" / "views"

# 页面主标题 i18n key 模式 (以 _title 结尾的通常是页面主标题)
_PAGE_TITLE_KEY_PATTERN = re.compile(r'I18n\.get\("([^"]*_title)"')


def _extract_text_blocks(src: str) -> list[tuple[str, int]]:
    """用括号计数提取源码中所有 ft.Text(...) 完整块及其起始行号.

    ft.Text(...) 参数可跨多行且可能含嵌套括号 (如 I18n.get("...")),
    因此不能用简单正则 `[^)]*` 匹配, 需括号计数定位配对的右括号.
    """
    blocks: list[tuple[str, int]] = []
    search_from = 0
    while True:
        idx = src.find("ft.Text(", search_from)
        if idx == -1:
            break
        depth = 1
        # 指向 '(' 之后第一个字符, 避免将 ft.Text( 自身的左括号重复计数
        j = idx + len("ft.Text(")
        while j < len(src):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        line_no = src[:idx].count("\n") + 1
        blocks.append((src[idx : j + 1], line_no))
        search_from = j + 1
    return blocks


def _is_page_title_block(block: str) -> str | None:
    """判断一个 ft.Text(...) 块是否为页面主标题, 是则返回 i18n key, 否则返回 None.

    页面主标题判定: 同时使用 weight=BOLD + FONT_SIZE_HEADLINE, 且 i18n key 以 _title 结尾.
    """
    if "weight=ft.FontWeight.BOLD" not in block:
        return None
    if "FONT_SIZE_HEADLINE" not in block:
        return None
    key_match = _PAGE_TITLE_KEY_PATTERN.search(block)
    return key_match.group(1) if key_match else None


def test_page_title_uses_xl_size():
    """各视图页面主标题必须使用 FONT_SIZE_XL (24), 不得使用 FONT_SIZE_HEADLINE (20).

    规范见 ui/theme.py AppStyles 类注释 (Issue #445).
    仅检查 i18n key 以 _title 结尾的页面主标题.
    """
    offenders: list[str] = []
    for f in sorted(_VIEWS_DIR.glob("*.py")):
        if "__pycache__" in f.parts:
            continue
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8")
        for block, line_no in _extract_text_blocks(src):
            i18n_key = _is_page_title_block(block)
            if i18n_key:
                offenders.append(
                    f"{f.relative_to(_PROJECT_ROOT)}:{line_no}: "
                    f"页面标题 ({i18n_key}) 使用 FONT_SIZE_HEADLINE 而非 FONT_SIZE_XL"
                )
    assert not offenders, (
        "视图页面主标题必须使用 FONT_SIZE_XL (24) (Issue #445). "
        "区块标题/对话框标题使用 FONT_SIZE_HEADLINE (20) 是允许的. "
        "违规位置:\n" + "\n".join(offenders)
    )
