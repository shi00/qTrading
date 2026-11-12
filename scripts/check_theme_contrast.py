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
    STATUS_TINT_ALPHA,
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


def blend_alpha_over(hex_overlay: str, hex_base: str, alpha: float) -> str:
    """把 overlay 色以 alpha 叠在 base 色上, 返回混合后的不透明 hex (底纹背景建模)。

    对应 Flet ``ft.Colors.with_opacity(alpha, color)`` 叠加在 parent bgcolor 上的效果,
    用于验收「状态色正文叠在同色弱底纹上」的真实背景 (派生色无法直接从主题表读取)。
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha 必须在 [0, 1] 区间, 实际 {alpha!r}")
    overlay = _hex_to_rgb(hex_overlay)
    base = _hex_to_rgb(hex_base)
    mixed = tuple(round(o * alpha + b * (1.0 - alpha)) for o, b in zip(overlay, base, strict=True))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


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
    "ON_ERROR": "on_error",
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

    - ``text``     正文文本（含业务状态/涨跌/错误文字与反色文字，字号 12~14，非 WCAG 大字号例外）
    - ``icon``     图标 / 图形 / 错误态强调
    - ``disabled`` 禁用或装饰性内容（WCAG §1.4.3 对禁用控件豁免，仍保留可读下限）

    ``bg_tint`` 用于「底纹背景」：``(token, alpha)`` 表示背景 = ``bg`` 上叠加 ``token`` 色
    （alpha 不透明度）后的混合色，对应 ``ft.Colors.with_opacity(alpha, token)`` 容器
    （如 health_report_dialog 的错误弱底纹）。``None`` 表示 ``bg`` 即真实不透明背景。
    """

    fg: str
    bg: str
    threshold: float
    purpose: str
    bg_tint: tuple[str, float] | None = None


# 色对按「文本 / 图标 / 禁用」用途分级标注阈值（F06）：
#   业务状态/涨跌文字（SUCCESS/WARNING/INFO/UP_RED/DOWN_GREEN）实际用于 13/14px 正文
#   （Toast、Token 状态文字、Slider 标签、行情单元格、任务/数据源提示等），
#   不属于 WCAG 大字号例外，必须按正文 4.5 验收；仅图标或禁用内容才用 3.0。
#
#   背景建模（消费盘点结论）：
#   - 业务状态/涨跌正文的真实背景 = SURFACE / CARD_BG（面板/卡片/Toast/列表行/DataTable
#     默认行；4 主题下两 token 同值，仍显式分别验收以防未来分化）。
#   - virtual_table 的涨跌单元格（_TREND_COLS，13px 正文）直接使用 UP_RED/DOWN_GREEN，
#     行底色由 AppStyles.data_table_row 在 TABLE_ROW_ODD / TABLE_ROW_EVEN /
#     TABLE_ROW_HOVER 之间切换，故涨跌色必须同时在这三种真实行底色上按正文 4.5 验收
#     （原注释曾误称业务色无 EVEN 行消费场景，掩盖了该缺陷）。
#   - 回测结果面板的成交明细 / 月度统计两张表（M30-C3）同样用 UP_RED/DOWN_GREEN 表达
#     盈亏与收益涨跌（默认 13/14px 正文），行底色为 SURFACE（默认行）与 TABLE_ROW_HOVER
#     （hover）；与 virtual_table 一致按 ODD/EVEN/HOVER 全矩阵保守验收。
#   - DataTable 的 hover 行也承载中性正文：未显式指定颜色的单元格取主题默认
#     TABLE_CELL_TEXT（如数据出口审计表），回测两张表的中性单元格显式取 TEXT_PRIMARY，
#     故补齐二者 × TABLE_ROW_HOVER 色对。
#   - SUCCESS/WARNING/INFO 在 virtual_table / 回测表格单元格中无消费（单元格中性正文用
#     TABLE_CELL_TEXT / TABLE_CELL_NUMERIC）。
#   - M30-UIB-THEME-03: ERROR 是 13/14px 正文色（校验文字、健康报告问题列表、数据源/
#     模型/启动失败提示等），不是「仅图标」色；同时承担两类衍生用途，三者一并验收：
#       ① 正文: ERROR × SURFACE / CARD_BG (4.5)
#       ② 反色: ON_ERROR × ERROR (danger_button / app_layout 导航角标, 4.5)
#       ③ 弱底纹: ERROR 正文叠在同色 STATUS_TINT_ALPHA 底纹上 (4.5, 底纹背景按
#          CARD_BG 混合建模, 对应 health_report_dialog 问题列表与覆盖徽标)
#     回测结果面板的 ERROR 仅用于 20px 粗体指标数值与图表图形（大字号档），已被 4.5 覆盖。
#
#   DataTable 消费盘点（C3 已把回测成交/月度两表迁到 UP_RED/DOWN_GREEN）：
#   ui/components/virtual_table.py 与回测两张表均无 AppColors.ERROR 单元格正文，
#   故不新增 ERROR × TABLE_ROW_* 色对；若未来新增须补齐并要求 ON_ERROR 反色同步复核。
#
#   已知覆盖边界（如实披露，不扩大为整应用 WCAG AA 认证）：
#   - watchlist 列表行与 metric tile 使用 Layer 1 派生色 SURFACE_VARIANT
#     （surface_container_highest，运行时由 Flet/Flutter 派生，无法从主题表精确计算），
#     不在本门禁可验收集合内；底纹验收因此按 CARD_BG 建模（四主题下 CARD_BG == SURFACE）。
#   - ColorScheme.error_container 当前在 UI 层无消费（仅主题表定义），不纳入验收；
#     若将来启用 error_container 背景 + ERROR 文字，须补对应色对。
#   - Toast/SnackBar 正文取 TEXT_PRIMARY（错误色仅用于图标与动作按钮文本），
#     其错误图标用途由 ① 的正文级 4.5 覆盖（严于图标 3.0）。
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
    # 涨跌文字在 virtual_table 交替行 / hover 行底色上的真实消费（_build_cells 涨跌列）
    ContrastPair("UP_RED", "TABLE_ROW_ODD", _THRESHOLD_TEXT, "text"),
    ContrastPair("UP_RED", "TABLE_ROW_EVEN", _THRESHOLD_TEXT, "text"),
    ContrastPair("UP_RED", "TABLE_ROW_HOVER", _THRESHOLD_TEXT, "text"),
    ContrastPair("DOWN_GREEN", "TABLE_ROW_ODD", _THRESHOLD_TEXT, "text"),
    ContrastPair("DOWN_GREEN", "TABLE_ROW_EVEN", _THRESHOLD_TEXT, "text"),
    ContrastPair("DOWN_GREEN", "TABLE_ROW_HOVER", _THRESHOLD_TEXT, "text"),
    # 中性正文在 hover 行底色上的真实消费（DataTable 默认单元格 / 回测表中性单元格）
    ContrastPair("TABLE_CELL_TEXT", "TABLE_ROW_HOVER", _THRESHOLD_TEXT, "text"),
    ContrastPair("TEXT_PRIMARY", "TABLE_ROW_HOVER", _THRESHOLD_TEXT, "text"),
    # --- 错误色正文 (4.5；ERROR 真实用于 13/14px 校验/错误提示文字) ---
    ContrastPair("ERROR", "SURFACE", _THRESHOLD_TEXT, "text"),
    ContrastPair("ERROR", "CARD_BG", _THRESHOLD_TEXT, "text"),
    # 错误弱底纹背景 (ERROR 正文叠在同色 STATUS_TINT_ALPHA 底纹上)
    ContrastPair("ERROR", "CARD_BG", _THRESHOLD_TEXT, "text", ("ERROR", STATUS_TINT_ALPHA)),
    # --- 反色 (4.5；danger_button / 导航角标: ON_ERROR 文字叠 ERROR 底) ---
    ContrastPair("ON_ERROR", "ERROR", _THRESHOLD_TEXT, "text"),
    # --- 禁用 / 装饰 (3.0，WCAG §1.4.3 禁用控件豁免，保留可读下限) ---
    ContrastPair("TEXT_DISABLED", "SURFACE", _THRESHOLD_LARGE, "disabled"),
]


def _resolve_pair_colors(pair: ContrastPair, theme: str) -> tuple[str | None, str | None]:
    """解析色对的前景/背景 hex；底纹色对返回混合后的不透明背景色。"""
    fg = _resolve_color(pair.fg, theme)
    bg = _resolve_color(pair.bg, theme)
    if pair.bg_tint is not None:
        tint_token, alpha = pair.bg_tint
        tint = _resolve_color(tint_token, theme)
        if bg is None or tint is None:
            return fg, None
        bg = blend_alpha_over(tint, bg, alpha)
    return fg, bg


def check_contrast() -> list[str]:
    """验证 4 主题所有关键色对达到 WCAG 阈值。返回错误列表。"""
    errors: list[str] = []
    themes = [ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA]
    for theme in themes:
        for pair in _CONTRAST_PAIRS:
            fg, bg = _resolve_pair_colors(pair, theme)
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
