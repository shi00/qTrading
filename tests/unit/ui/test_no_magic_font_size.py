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
