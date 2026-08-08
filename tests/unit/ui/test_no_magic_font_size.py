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
        # 找同时包含 weight=BOLD 和 FONT_SIZE_HEADLINE 的行
        for i, line in enumerate(src.splitlines(), 1):
            if "weight=ft.FontWeight.BOLD" in line and "FONT_SIZE_HEADLINE" in line:
                # 检查是否是页面主标题 (i18n key 以 _title 结尾)
                key_match = _PAGE_TITLE_KEY_PATTERN.search(line)
                if key_match:
                    i18n_key = key_match.group(1)
                    offenders.append(
                        f"{f.relative_to(_PROJECT_ROOT)}:{i}: "
                        f"页面标题 ({i18n_key}) 使用 FONT_SIZE_HEADLINE 而非 FONT_SIZE_XL: "
                        f"{line.strip()[:80]}"
                    )
    assert not offenders, (
        "视图页面主标题必须使用 FONT_SIZE_XL (24) (Issue #445). "
        "区块标题/对话框标题使用 FONT_SIZE_HEADLINE (20) 是允许的. "
        "违规位置:\n" + "\n".join(offenders)
    )
