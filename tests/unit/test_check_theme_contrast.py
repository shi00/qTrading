"""Tests for scripts/check_theme_contrast.py WCAG 2.1 §1.4.3 对比度门禁。

验证:
- WCAG 相对亮度算法 (sRGB gamma 解码)
- 对比度阈值判定
- 4 主题关键色对 (Layer 1 SURFACE + Layer 2 业务色) 达标
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_theme_contrast import (  # noqa: E402 - sys.path 注入后导入
    _channel_to_linear,
    _CONTRAST_PAIRS,
    _hex_to_rgb,
    _LAYER1_FIELD_MAP,
    _resolve_color,
    check_contrast,
    contrast_ratio,
    main,
    relative_luminance,
)


# ============================================================================
# WCAG 相对亮度算法纯函数测试
# ============================================================================


class TestHexToRgb:
    """_hex_to_rgb: hex 字符串 → (R, G, B) 0-255 整数。"""

    def test_full_hex(self):
        assert _hex_to_rgb("#FF0000") == (255, 0, 0)

    def test_hex_without_hash(self):
        assert _hex_to_rgb("00FF00") == (0, 255, 0)

    def test_short_hex_expanded(self):
        """3 位短 hex (#RGB) 展开为 6 位 (#RRGGBB)。"""
        assert _hex_to_rgb("#0F0") == (0, 255, 0)
        assert _hex_to_rgb("#F00") == (255, 0, 0)
        assert _hex_to_rgb("#FFF") == (255, 255, 255)

    def test_mixed_case(self):
        assert _hex_to_rgb("#aBcDeF") == (171, 205, 239)


class TestChannelToLinear:
    """_channel_to_linear: sRGB 8-bit channel → linear RGB (WCAG gamma 解码)。"""

    def test_zero_returns_zero(self):
        """0 输入返回 0.0 (≤0.03928 分支)。"""
        assert _channel_to_linear(0) == 0.0

    def test_threshold_value_uses_linear_branch(self):
        """9 (≈ 0.0353) ≤ 0.03928 走 s/12.92 线性分支。"""
        # 9 / 255 / 12.92 ≈ 0.00274
        assert abs(_channel_to_linear(9) - (9 / 255) / 12.92) < 1e-9

    def test_255_returns_one(self):
        """255 输入返回 1.0 (linear)。"""
        assert abs(_channel_to_linear(255) - 1.0) < 1e-9

    def test_mid_value_uses_gamma_branch(self):
        """128 (>0.03928) 走 ((s+0.055)/1.055)^2.4 gamma 分支。"""
        expected = ((128 / 255 + 0.055) / 1.055) ** 2.4
        assert abs(_channel_to_linear(128) - expected) < 1e-9


class TestRelativeLuminance:
    """relative_luminance: WCAG L = 0.2126*R + 0.7152*G + 0.0722*B (linear RGB)。"""

    def test_black_returns_zero(self):
        """纯黑 #000000 相对亮度为 0.0。"""
        assert relative_luminance("#000000") == 0.0

    def test_white_returns_one(self):
        """纯白 #FFFFFF 相对亮度为 1.0。"""
        assert abs(relative_luminance("#FFFFFF") - 1.0) < 1e-9

    def test_red_dominates_green_blue(self):
        """纯红 #FF0000 相对亮度 = 0.2126 (R 满分)。"""
        assert abs(relative_luminance("#FF0000") - 0.2126) < 1e-9

    def test_green_dominates_red_blue(self):
        """纯绿 #00FF00 相对亮度 = 0.7152 (G 满分)。"""
        assert abs(relative_luminance("#00FF00") - 0.7152) < 1e-9

    def test_blue_dominates_red_green(self):
        """纯蓝 #0000FF 相对亮度 = 0.0722 (B 满分)。"""
        assert abs(relative_luminance("#0000FF") - 0.0722) < 1e-9


# ============================================================================
# 对比度算法纯函数测试
# ============================================================================


class TestContrastRatio:
    """contrast_ratio: WCAG 对比度 = (L_lighter + 0.05) / (L_darker + 0.05)。"""

    def test_black_on_white_returns_21(self):
        """黑底白字对比度 = 21.0 (WCAG 最大值)。"""
        ratio = contrast_ratio("#000000", "#FFFFFF")
        assert abs(ratio - 21.0) < 1e-6

    def test_same_color_returns_one(self):
        """相同颜色对比度 = 1.0 (最小值, 无对比)。"""
        ratio = contrast_ratio("#FF0000", "#FF0000")
        assert abs(ratio - 1.0) < 1e-6

    def test_order_independent(self):
        """对比度计算与颜色顺序无关 (lighter/darker 自动判断)。"""
        r1 = contrast_ratio("#000000", "#FFFFFF")
        r2 = contrast_ratio("#FFFFFF", "#000000")
        assert abs(r1 - r2) < 1e-9

    def test_red_on_white_meets_large_text_threshold(self):
        """红字白底对比度 ≥ 3.0 (大字号阈值)。"""
        ratio = contrast_ratio("#FF0000", "#FFFFFF")
        assert ratio >= 3.0

    def test_light_gray_on_white_low_contrast(self):
        """浅灰字白底对比度 < 4.5 (正文文本不达标)。"""
        ratio = contrast_ratio("#CCCCCC", "#FFFFFF")
        assert ratio < 4.5


# ============================================================================
# 关键色对配置测试
# ============================================================================


class TestContrastPairsConfig:
    """_CONTRAST_PAIRS: 关键色对配置完整性。"""

    def test_pairs_not_empty(self):
        """必须配置至少 1 个色对。"""
        assert len(_CONTRAST_PAIRS) > 0

    def test_pairs_have_3_tuple_structure(self):
        """每个色对必须是 (fg, bg, threshold) 三元组。"""
        for pair in _CONTRAST_PAIRS:
            assert len(pair) == 3
            fg, bg, threshold = pair
            assert isinstance(fg, str)
            assert isinstance(bg, str)
            assert isinstance(threshold, float)

    def test_text_pairs_use_4_5_threshold(self):
        """正文文本色对 (TEXT_PRIMARY/TEXT_SECONDARY) 阈值为 4.5。"""
        text_pairs = [(fg, bg, t) for fg, bg, t in _CONTRAST_PAIRS if fg in ("TEXT_PRIMARY", "TEXT_SECONDARY")]
        assert text_pairs, "必须包含正文文本色对"
        for fg, bg, t in text_pairs:
            assert t == 4.5, f"正文文本色对 {fg}/{bg} 阈值必须为 4.5, 实际 {t}"

    def test_status_pairs_use_3_0_threshold(self):
        """状态色色对 (SUCCESS/WARNING/INFO/ERROR) 阈值为 3.0 (大字号/图标)。"""
        status_pairs = [(fg, bg, t) for fg, bg, t in _CONTRAST_PAIRS if fg in ("SUCCESS", "WARNING", "INFO", "ERROR")]
        assert status_pairs, "必须包含状态色色对"
        for fg, bg, t in status_pairs:
            assert t == 3.0, f"状态色色对 {fg}/{bg} 阈值必须为 3.0, 实际 {t}"


class TestLayer1FieldMap:
    """_LAYER1_FIELD_MAP: Layer 1 字段映射配置。"""

    def test_surface_mapped_to_color_scheme(self):
        """SURFACE 必须映射到 ColorScheme.surface。"""
        assert _LAYER1_FIELD_MAP["SURFACE"] == "surface"

    def test_text_primary_mapped_to_on_surface(self):
        """TEXT_PRIMARY 必须映射到 ColorScheme.on_surface。"""
        assert _LAYER1_FIELD_MAP["TEXT_PRIMARY"] == "on_surface"

    def test_text_secondary_mapped_to_on_surface_variant(self):
        """TEXT_SECONDARY 必须映射到 ColorScheme.on_surface_variant。"""
        assert _LAYER1_FIELD_MAP["TEXT_SECONDARY"] == "on_surface_variant"

    def test_error_mapped_to_color_scheme(self):
        """ERROR 必须映射到 ColorScheme.error。"""
        assert _LAYER1_FIELD_MAP["ERROR"] == "error"


# ============================================================================
# 色彩解析测试
# ============================================================================


class TestResolveColor:
    """_resolve_color: Layer 1 → Layer 2 顺序解析颜色 hex 值。"""

    def test_resolve_layer1_surface_returns_hex(self):
        """SURFACE (Layer 1) 从 ColorScheme 解析, 返回 hex 字符串。"""
        from ui.theme import THEME_COLOR_SCHEMES, ThemeName

        val = _resolve_color("SURFACE", ThemeName.DARK)
        expected = THEME_COLOR_SCHEMES[ThemeName.DARK].surface
        assert val == str(expected)

    def test_resolve_layer2_success_returns_hex(self):
        """SUCCESS (Layer 2) 从 CUSTOM_COLOR_PRESETS 解析, 返回 hex 字符串。"""
        from ui.theme import CUSTOM_COLOR_PRESETS, ThemeName

        val = _resolve_color("SUCCESS", ThemeName.DARK)
        assert val == CUSTOM_COLOR_PRESETS[ThemeName.DARK]["SUCCESS"]

    def test_resolve_unknown_returns_none(self):
        """未知名返回 None (Layer 1 + Layer 2 均未定义)。"""
        val = _resolve_color("UNKNOWN_COLOR", "dark")
        assert val is None

    def test_resolve_layer2_table_header_text(self):
        """TABLE_HEADER_TEXT (Layer 2) 解析返回 hex。"""
        from ui.theme import CUSTOM_COLOR_PRESETS, ThemeName

        val = _resolve_color("TABLE_HEADER_TEXT", ThemeName.LIGHT)
        assert val == CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["TABLE_HEADER_TEXT"]


# ============================================================================
# 4 主题关键色对集成测试
# ============================================================================


class TestFourThemesContrastIntegration:
    """4 主题 (Dark/Light/Navy/Dracula) 关键色对集成测试。"""

    def test_check_contrast_returns_empty_for_compliant_themes(self):
        """check_contrast 在 4 主题合规时应返回空列表。"""
        errors = check_contrast()
        assert errors == [], "WCAG 对比度未达标:\n  " + "\n  ".join(errors)

    def test_dark_theme_text_primary_on_surface_meets_4_5(self):
        """Dark 主题: TEXT_PRIMARY/SURFACE 对比度 ≥ 4.5 (正文文本)。"""
        from ui.theme import ThemeName

        fg = _resolve_color("TEXT_PRIMARY", ThemeName.DARK)
        bg = _resolve_color("SURFACE", ThemeName.DARK)
        assert fg is not None and bg is not None
        ratio = contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Dark TEXT_PRIMARY/SURFACE 对比度 {ratio:.2f} < 4.5"

    def test_light_theme_text_primary_on_surface_meets_4_5(self):
        """Light 主题: TEXT_PRIMARY/SURFACE 对比度 ≥ 4.5。"""
        from ui.theme import ThemeName

        fg = _resolve_color("TEXT_PRIMARY", ThemeName.LIGHT)
        bg = _resolve_color("SURFACE", ThemeName.LIGHT)
        assert fg is not None and bg is not None
        ratio = contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Light TEXT_PRIMARY/SURFACE 对比度 {ratio:.2f} < 4.5"

    def test_navy_theme_text_primary_on_surface_meets_4_5(self):
        """Navy 主题: TEXT_PRIMARY/SURFACE 对比度 ≥ 4.5。"""
        from ui.theme import ThemeName

        fg = _resolve_color("TEXT_PRIMARY", ThemeName.NAVY)
        bg = _resolve_color("SURFACE", ThemeName.NAVY)
        assert fg is not None and bg is not None
        ratio = contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Navy TEXT_PRIMARY/SURFACE 对比度 {ratio:.2f} < 4.5"

    def test_dracula_theme_text_primary_on_surface_meets_4_5(self):
        """Dracula 主题: TEXT_PRIMARY/SURFACE 对比度 ≥ 4.5。"""
        from ui.theme import ThemeName

        fg = _resolve_color("TEXT_PRIMARY", ThemeName.DRACULA)
        bg = _resolve_color("SURFACE", ThemeName.DRACULA)
        assert fg is not None and bg is not None
        ratio = contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Dracula TEXT_PRIMARY/SURFACE 对比度 {ratio:.2f} < 4.5"

    def test_dark_theme_status_colors_meet_3_0(self):
        """Dark 主题: SUCCESS/WARNING/INFO/ERROR 与 SURFACE 对比度 ≥ 3.0。"""
        from ui.theme import ThemeName

        bg = _resolve_color("SURFACE", ThemeName.DARK)
        for status in ("SUCCESS", "WARNING", "INFO", "ERROR"):
            fg = _resolve_color(status, ThemeName.DARK)
            assert fg is not None and bg is not None
            ratio = contrast_ratio(fg, bg)
            assert ratio >= 3.0, f"Dark {status}/SURFACE 对比度 {ratio:.2f} < 3.0"

    def test_light_theme_status_colors_meet_3_0(self):
        """Light 主题: SUCCESS/WARNING/INFO/ERROR 与 SURFACE 对比度 ≥ 3.0。"""
        from ui.theme import ThemeName

        bg = _resolve_color("SURFACE", ThemeName.LIGHT)
        for status in ("SUCCESS", "WARNING", "INFO", "ERROR"):
            fg = _resolve_color(status, ThemeName.LIGHT)
            assert fg is not None and bg is not None
            ratio = contrast_ratio(fg, bg)
            assert ratio >= 3.0, f"Light {status}/SURFACE 对比度 {ratio:.2f} < 3.0"


# ============================================================================
# CLI 入口集成测试
# ============================================================================


class TestCheckContrastMainEntry:
    """main() CLI 入口集成测试。"""

    def test_main_returns_zero_when_contrast_compliant(self):
        """当前代码库 4 主题对比度合规时, main() 返回 0。"""
        assert main() == 0
