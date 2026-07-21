"""WCAG 2.1 §1.4.3 对比度门禁脚本。

依据 ui/theme.py 中 4 主题的 Layer 1 SURFACE (THEME_COLOR_SCHEMES) +
Layer 2 业务色 (CUSTOM_COLOR_PRESETS) 计算 WCAG 相对亮度，
对关键色对验证 WCAG 2.1 §1.4.3 对比度阈值：
- 正文文本 ≥ 4.5
- 大字号/图标 ≥ 3.0

零新依赖 — 纯 Python 相对亮度公式 (sRGB gamma 解码)。

退出码：0 通过，1 失败。供 pre-commit `theme-contrast-check` hook 与 pytest 调用。
"""

from __future__ import annotations

import sys
import typing
from io import TextIOWrapper
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui.theme import (  # noqa: E402 - sys.path 注入后导入
    CUSTOM_COLOR_PRESETS,
    THEME_COLOR_SCHEMES,
    ThemeName,
)

# ============================================================================
# WCAG 2.1 §1.4.3 相对亮度算法
# 参考: https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
# ============================================================================


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """将 #RRGGBB 或 #RGB 转换为 (R, G, B) 0-255 整数。"""
    color = hex_color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _channel_to_linear(channel_8bit: int) -> float:
    """sRGB 8-bit channel → linear RGB (WCAG 2.1 §1.4.3 gamma 解码)。"""
    s = channel_8bit / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """计算 WCAG 相对亮度 L = 0.2126*R + 0.7152*G + 0.0722*B (linear RGB)。"""
    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * _channel_to_linear(r) + 0.7152 * _channel_to_linear(g) + 0.0722 * _channel_to_linear(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 对比度 = (L_lighter + 0.05) / (L_darker + 0.05)。"""
    la = relative_luminance(hex_a)
    lb = relative_luminance(hex_b)
    light = max(la, lb)
    dark = min(la, lb)
    return (light + 0.05) / (dark + 0.05)


# ============================================================================
# 色对解析: Layer 1 (ColorScheme) → Layer 2 (CUSTOM_COLOR_PRESETS)
# ============================================================================

# Layer 1 字段映射 (ColorScheme 属性)
_LAYER1_FIELD_MAP: dict[str, str] = {
    "SURFACE": "surface",
    "TEXT_PRIMARY": "on_surface",
    "TEXT_SECONDARY": "on_surface_variant",
    "TEXT_HINT": "on_surface_variant",
    "TEXT_DISABLED": "on_surface_variant",  # 复用 TEXT_HINT
    "ERROR": "error",
    "TEXT_ON_PRIMARY": "on_primary",
}


def _resolve_color(name: str, theme: str) -> str | None:
    """从 Layer 1 → Layer 2 顺序解析颜色 hex 值。"""
    # Layer 1: 从 ColorScheme 取
    layer1_field = _LAYER1_FIELD_MAP.get(name)
    if layer1_field is not None:
        scheme = THEME_COLOR_SCHEMES[theme]
        val = getattr(scheme, layer1_field, None)
        if val is not None:
            return str(val)
    # Layer 2: 从 CUSTOM_COLOR_PRESETS 取
    preset = CUSTOM_COLOR_PRESETS[theme]
    val = preset.get(name)
    return val if val is None else str(val)


# ============================================================================
# 关键色对与阈值 (WCAG 2.1 §1.4.3)
# ============================================================================

# (前景色名, 背景色名, 阈值)
# 正文文本 ≥4.5；大字号/图标/状态色 ≥3.0
_CONTRAST_PAIRS: list[tuple[str, str, float]] = [
    # 正文文本
    ("TEXT_PRIMARY", "SURFACE", 4.5),
    ("TEXT_SECONDARY", "SURFACE", 4.5),
    # 表格文本（正文）
    ("TABLE_HEADER_TEXT", "TABLE_HEADER_BG", 4.5),
    ("TABLE_HEADER_TEXT", "TABLE_ROW_ODD", 4.5),
    ("TABLE_HEADER_TEXT", "TABLE_ROW_EVEN", 4.5),
    ("TABLE_CELL_TEXT", "TABLE_ROW_ODD", 4.5),
    # 状态色（图标 / 大字号）
    ("SUCCESS", "SURFACE", 3.0),
    ("WARNING", "SURFACE", 3.0),
    ("INFO", "SURFACE", 3.0),
    ("ERROR", "SURFACE", 3.0),
    ("TEXT_DISABLED", "SURFACE", 3.0),
    # 涨跌色（图标 / 大字号数值）
    ("UP_RED", "SURFACE", 3.0),
    ("DOWN_GREEN", "SURFACE", 3.0),
]


def check_contrast() -> list[str]:
    """验证 4 主题所有关键色对达到 WCAG 阈值。返回错误列表。"""
    errors: list[str] = []
    themes = [ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA]
    for theme in themes:
        for fg_name, bg_name, threshold in _CONTRAST_PAIRS:
            fg = _resolve_color(fg_name, theme)
            bg = _resolve_color(bg_name, theme)
            if fg is None or bg is None:
                errors.append(
                    f"{theme}: 无法解析色对 {fg_name}/{bg_name} "
                    f"(fg={'<缺失>' if fg is None else fg}, bg={'<缺失>' if bg is None else bg})"
                )
                continue
            try:
                ratio = contrast_ratio(fg, bg)
            except (ValueError, IndexError) as exc:
                errors.append(f"{theme}: {fg_name}/{bg_name} 颜色解析失败 (fg={fg!r}, bg={bg!r}): {exc}")
                continue
            if ratio < threshold:
                errors.append(
                    f"{theme}: {fg_name}/{bg_name} 对比度 {ratio:.2f} 低于阈值 {threshold} (fg={fg}, bg={bg})"
                )
    return errors


# ============================================================================
# CLI 入口
# ============================================================================


def main() -> int:
    """运行 WCAG 对比度检查，返回退出码。"""
    errors = check_contrast()
    if errors:
        print("[FAIL] WCAG 对比度检查失败：", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("[PASS] WCAG 对比度检查通过 (4 主题 × 关键色对)")
    return 0


if __name__ == "__main__":
    # 兜底: Windows GBK 等非 UTF-8 环境下避免 UnicodeEncodeError
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            typing.cast(TextIOWrapper, _stream).reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
