"""守护: UI 文件不得包含 emoji / dingbat 字符 (P2-7).

P2-7 决策: UI 显示文本不依赖 emoji 字体, 所有状态/语气通过 ft.Icon / AppColors 表达.
本测试动态扫描 ui/ 下所有 .py 文件 (不含 __pycache__), 拦截 emoji 回归.

豁免:
- docstring / 注释中的 emoji 描述 (如 "P2-7: ⚠️ → 文本符号" 这类历史说明)
- 业务层 services/data 不在此扫描范围 (本测试仅约束 ui/)
"""

from __future__ import annotations

import pathlib
import re

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


def _is_docstring_or_comment_line(line: str) -> bool:
    """判断行是否为注释或 docstring 内部 (简单启发: 行首 # 或在三引号内).

    注意: 此启发不解析完整 AST, 多行 docstring 内的 emoji 仍会被报告.
    但 UI 代码中多行 docstring 通常不含 emoji, 误报率低.
    """
    stripped = line.lstrip()
    return stripped.startswith("#")


def test_no_emoji_in_ui_source():
    """UI 源代码 (.py) 不得包含 emoji / dingbat 字符."""
    offenders: list[str] = []
    for f in sorted(_SCAN_DIR.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        src = f.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if _is_docstring_or_comment_line(line):
                continue
            for m in _EMOJI_PATTERN.finditer(line):
                char = m.group(0)
                offenders.append(
                    f"{f.relative_to(_PROJECT_ROOT)}:{i}: {char!r} (U+{ord(char):04X}) in: {line.strip()[:80]}"
                )
    assert not offenders, (
        "UI 源代码不得包含 emoji / dingbat 字符 (P2-7). 状态/语气请用 ft.Icon / AppColors 表达:\n"
        + "\n".join(offenders)
    )
