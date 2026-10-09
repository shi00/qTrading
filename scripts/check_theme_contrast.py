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
from typing import NamedTuple

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

# WCAG 2.1 §1.4.3 阈值:
#   正常文字 ≥ 4.5；大字号 (≥24px，或 ≥18.66px 且粗体) 与图形/图标 ≥ 3.0
_THRESHOLD_TEXT = 4.5
_THRESHOLD_LARGE = 3.0


class ContrastPair(NamedTuple):
    """一条对比度验收色对。

    ``purpose`` 标注该色对的真实用途，决定适用阈值（F06：按用途分级，不把所有颜色设同阈值）：

    - ``text``     正文文本（含业务状态/涨跌文字，字号 13/14，非 WCAG 大字号例外）
    - ``icon``     图标 / 图形 / 错误态强调
    - ``disabled`` 禁用或装饰性内容（WCAG §1.4.3 对禁用控件豁免，仍保留可读下限）
    """

    fg: str
    bg: str
    threshold: float
    purpose: str


# 色对按「文本 / 图标 / 禁用」用途分级标注阈值（F06）：
#   业务状态/涨跌文字（SUCCESS/WARNING/INFO/UP_RED/DOWN_GREEN）实际用于 13/14px 正文
#   （Toast、Token 状态文字、Slider 标签、行情单元格、任务/数据源提示等），
#   不属于 WCAG 大字号例外，必须按正文 4.5 验收；仅图标或禁用内容才用 3.0。
#
#   背景建模（消费盘点结论）：
#   - 业务色正文的真实背景 = SURFACE / CARD_BG（面板/卡片/Toast/列表行/DataTable 默认行；
#     4 主题下两 token 同值，仍显式分别验收以防未来分化）。
#   - virtual_table 的 ODD/EVEN 交替行单元格仅使用中性正文色（TABLE_CELL_TEXT 等），
#     业务色无 EVEN 行消费场景，故不为业务色配置 EVEN 行色对。
#
#   已知覆盖边界（如实披露，不扩大为整应用 WCAG AA 认证）：
#   - watchlist 列表行使用 Layer 1 派生色 SURFACE_VARIANT（surface_container_highest，
#     运行时由 Flet/Flutter 派生，无法从主题表精确计算），不在本门禁可验收集合内。
#   - ERROR 亦有正文场景（Toast 错误文字、交易明细 sell/负 pnl），但 Layer 1 error
#     调整会联动 danger_button/on_error 等反色用途，超出 F06 范围，按图标档 3.0 验收，
#     后续如需正文级验收须连同反色用途一并评估。
_CONTRAST_PAIRS: list[ContrastPair] = [
    # --- 正文文本 (4.5) ---
    ContrastPair("TEXT_PRIMARY", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("TEXT_SECONDARY", "SURFACE", _THRESHOLD_TEXT, "text"),
    # 表格文本（正文）：表头文本在表头底色 + 奇偶行底色上的组合
    ContrastPair("TABLE_HEADER_TEXT", "TABLE_HEADER_BG", _THRESHOLD_TEXT, "text"),
    ContrastPair("TABLE_HEADER_TEXT", "TABLE_ROW_ODD", _THRESHOLD_TEXT, "text"),
    ContrastPair("TABLE_HEADER_TEXT", "TABLE_ROW_EVEN", _THRESHOLD_TEXT, "text"),
    # 表格单元格文本（正文）：奇偶行底色
    ContrastPair("TABLE_CELL_TEXT", "TABLE_ROW_ODD", _THRESHOLD_TEXT, "text"),
    ContrastPair("TABLE_CELL_TEXT", "TABLE_ROW_EVEN", _THRESHOLD_TEXT, "text"),
    # 业务状态 / 涨跌文本（正文）：状态消息与行情/涨跌文字，字号 13/14，非大字号
    ContrastPair("SUCCESS", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("SUCCESS", "CARD_BG", _THRESHOLD_TEXT, "text"),
    ContrastPair("WARNING", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("WARNING", "CARD_BG", _THRESHOLD_TEXT, "text"),
    ContrastPair("INFO", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("INFO", "CARD_BG", _THRESHOLD_TEXT, "text"),
    ContrastPair("UP_RED", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("UP_RED", "CARD_BG", _THRESHOLD_TEXT, "text"),
    ContrastPair("DOWN_GREEN", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("DOWN_GREEN", "CARD_BG", _THRESHOLD_TEXT, "text"),
    # --- 图标 / 错误态强调 (3.0；ERROR 亦有正文场景，见上方覆盖边界说明) ---
    ContrastPair("ERROR", "SURFACE", _THRESHOLD_LARGE, "icon"),
    # --- 禁用 / 装饰 (3.0，WCAG §1.4.3 禁用控件豁免，保留可读下限) ---
    ContrastPair("TEXT_DISABLED", "SURFACE", _THRESHOLD_LARGE, "disabled"),
]


def check_contrast() -> list[str]:
    """验证 4 主题所有关键色对达到 WCAG 阈值。返回错误列表。"""
    errors: list[str] = []
    themes = [ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA]
    for theme in themes:
        for pair in _CONTRAST_PAIRS:
            fg = _resolve_color(pair.fg, theme)
            bg = _resolve_color(pair.bg, theme)
            if fg is None or bg is None:
                errors.append(
                    f"{theme}: 无法解析色对 {pair.fg}/{pair.bg} "
                    f"(fg={'<缺失>' if fg is None else fg}, bg={'<缺失>' if bg is None else bg})"
                )
                continue
            try:
                ratio = contrast_ratio(fg, bg)
            except (ValueError, IndexError) as exc:
                errors.append(f"{theme}: {pair.fg}/{pair.bg} 颜色解析失败 (fg={fg!r}, bg={bg!r}): {exc}")
                continue
            # 使用未舍入比值比较，仅在显示时格式化（4.499 不得因显示 4.50 而通过）
            if ratio < pair.threshold:
                errors.append(
                    f"{theme}: {pair.fg}/{pair.bg} [{pair.purpose}] 对比度 {ratio:.2f} "
                    f"低于阈值 {pair.threshold} (fg={fg}, bg={bg})"
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
