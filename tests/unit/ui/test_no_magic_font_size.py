"""守护: UI 文件不得使用 size=N / text_size=N / icon_size=N 魔术数字 (P1-1).

P1-1 决策: 所有字号必须引用 ``AppStyles.FONT_SIZE_*`` token, 消除硬编码数值.
本测试动态扫描 ui/ 下所有 .py 文件 (不含 __pycache__ / theme.py), 拦截魔术数字回归.

豁免:
- theme.py (token 定义源头, 合法存在 ``FONT_SIZE_* = 11`` 等)
- 注释/docstring 中的数值说明
- 非字号属性 (width/height/border_radius/spacing 等不在此约束)
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCAN_DIR = _PROJECT_ROOT / "ui"

# 检测 size=N / text_size=N / icon_size=N 中的字面数值
# 允许:
#   size=AppStyles.FONT_SIZE_*  (token)
#   size=<variable>             (变量引用)
#   size=<expr>                 (复杂表达式)
# 禁止:
#   size=<int literal>
_MAGIC_SIZE_PATTERN = re.compile(r"\b(?:size|text_size|icon_size)=(\d+)\b")


def test_no_magic_font_size_in_ui():
    """UI 源代码 (.py) 的 size/text_size/icon_size 不得使用字面数值."""
    offenders: list[str] = []
    for f in sorted(_SCAN_DIR.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        if f.name == "theme.py":
            continue  # token 定义源头
        src = f.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for m in _MAGIC_SIZE_PATTERN.finditer(line):
                num = m.group(1)
                offenders.append(f"{f.relative_to(_PROJECT_ROOT)}:{i}: size={num} in: {line.strip()[:80]}")
    assert not offenders, (
        "UI 源代码的 size/text_size/icon_size 不得使用字面数值 (P1-1). "
        "请改用 AppStyles.FONT_SIZE_CAPTION/BODY_SM/BODY/LG/TITLE/HEADLINE/XL/DISPLAY:\n" + "\n".join(offenders)
    )


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
