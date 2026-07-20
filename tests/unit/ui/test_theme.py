import inspect
from pathlib import Path
from unittest.mock import patch

import flet as ft
import pytest

from ui.theme import (
    THEME_COLOR_SCHEMES,
    AppColors,
    AppColorsState,
    AppStyles,
    ThemeName,
    CUSTOM_COLOR_PRESETS,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _save_restore_appcolors():
    saved_attrs = {key: getattr(AppColors, key) for key in CUSTOM_COLOR_PRESETS[ThemeName.DARK]}
    saved_attrs["RISE"] = AppColors.RISE
    saved_attrs["FALL"] = AppColors.FALL
    saved_attrs["TABLE_GRID_V"] = AppColors.TABLE_GRID_V
    saved_attrs["TABLE_GRID_H"] = AppColors.TABLE_GRID_H
    saved_attrs["_CURRENT_THEME_NAME"] = AppColors._CURRENT_THEME_NAME
    saved_attrs["_CURRENT_THEME_MODE"] = AppColors._CURRENT_THEME_MODE
    saved_state = AppColors._state
    yield
    for key, value in saved_attrs.items():
        setattr(AppColors, key, value)
    AppColors._state = saved_state


class TestAppColorsLoadTheme:
    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_sets_theme_name(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors._CURRENT_THEME_NAME == ThemeName.LIGHT

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_sets_theme_mode(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors._CURRENT_THEME_MODE == "LIGHT"

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_applies_dark_preset_colors(self):
        AppColors.load_theme(ThemeName.DARK)
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["UP"] == AppColors.UP
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["DOWN"] == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_applies_light_preset_colors(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["UP"] == AppColors.UP
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["DOWN"] == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_rise_alias(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.RISE == AppColors.UP

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_fall_alias(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.FALL == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_table_grid_v_alias(self):
        AppColors.load_theme(ThemeName.NAVY)
        assert AppColors.TABLE_GRID_V == AppColors.TABLE_GRID

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_table_grid_h_alias(self):
        AppColors.load_theme(ThemeName.NAVY)
        assert AppColors.TABLE_GRID_H == AppColors.TABLE_GRID


class TestAppColorsObservable:
    """AppColorsState Observable 状态源断言（声明式组件自动重渲染基础）。"""

    def test_get_observable_state_returns_singleton(self):
        """多次调用 get_observable_state 返回同一实例（单例）。"""
        state1 = AppColors.get_observable_state()
        state2 = AppColors.get_observable_state()
        assert state1 is state2

    def test_observable_state_is_app_colors_state_type(self):
        """get_observable_state 返回 AppColorsState 实例。"""
        state = AppColors.get_observable_state()
        assert isinstance(state, AppColorsState)

    def test_observable_state_default_theme_name(self):
        """新创建的 Observable state 默认 theme_name 为 DARK。"""
        # _state 在 fixture 中可能被恢复为 None，强制重置以测试默认值
        AppColors._state = None
        state = AppColors.get_observable_state()
        assert state.theme_name == ThemeName.DARK

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_load_theme_updates_observable_state(self):
        """load_theme 同步更新 state.theme_name。"""
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.get_observable_state().theme_name == ThemeName.LIGHT

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_load_theme_triggers_observable_notification(self):
        """load_theme 触发 Observable 通知（state.theme_name 赋值 → __setattr__ → _notify）。"""
        state = AppColors.get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        # 必须保留 disposer，否则 subscribe 弱引用 lambda 会被 GC（spike 项 1.9）
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            AppColors.load_theme(ThemeName.LIGHT)
        finally:
            disposer()
        assert len(notifications) == 1
        assert notifications[0][1] == "theme_name"

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_observable_notification_sender_is_state(self):
        """通知的 sender 是 state 实例本身。"""
        state = AppColors.get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            AppColors.load_theme(ThemeName.LIGHT)
        finally:
            disposer()
        assert notifications[0][0] is state

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_observable_subscribe_disposer_stops_notification(self):
        """disposer 后不再收到通知。"""
        state = AppColors.get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        disposer()
        AppColors.load_theme(ThemeName.LIGHT)
        assert len(notifications) == 0

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_load_theme_same_value_no_notification(self):
        """相同 theme_name 不触发通知（Observable __setattr__ 值相等优化）。"""
        # 先设置为 LIGHT
        AppColors.load_theme(ThemeName.LIGHT)
        state = AppColors.get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            # 再次 load_theme(LIGHT)，theme_name 值未变，不应触发通知
            AppColors.load_theme(ThemeName.LIGHT)
        finally:
            disposer()
        assert len(notifications) == 0


class TestAppStylesCard:
    def test_default_has_border_no_shadow(self):
        style = AppStyles.card()
        assert "border" in style
        assert "shadow" not in style

    def test_with_border_false(self):
        style = AppStyles.card(with_border=False)
        assert "border" not in style

    def test_with_shadow_true(self):
        style = AppStyles.card(with_shadow=True)
        assert "shadow" in style

    def test_with_border_and_shadow(self):
        style = AppStyles.card(with_border=True, with_shadow=True)
        assert "border" in style
        assert "shadow" in style

    def test_always_has_bgcolor_and_padding(self):
        style = AppStyles.card(with_border=False, with_shadow=False)
        assert "bgcolor" in style
        assert "padding" in style


class TestAppStylesDataTableRow:
    def test_even_index_returns_odd_color(self):
        result = AppStyles.data_table_row(0)
        assert result == AppColors.TABLE_ROW_ODD

    def test_odd_index_returns_even_color(self):
        result = AppStyles.data_table_row(1)
        assert result == AppColors.TABLE_ROW_EVEN

    def test_hovered_returns_odd_color(self):
        result = AppStyles.data_table_row(1, is_hovered=True)
        assert result == AppColors.TABLE_ROW_ODD


class TestAppStylesPriceChangeColor:
    def test_positive_returns_up(self):
        result = AppStyles.price_change_color(1.5)
        assert result == AppColors.UP

    def test_negative_returns_down(self):
        result = AppStyles.price_change_color(-2.0)
        assert result == AppColors.DOWN

    def test_zero_returns_on_surface_variant(self):
        result = AppStyles.price_change_color(0.0)
        assert result == ft.Colors.ON_SURFACE_VARIANT


# ============================================================================
# 业务语义色 + 禁用色存在性 + 注释守护 (P2-11)
# ============================================================================


class TestSemanticColorsAnnotation:
    """SUCCESS/WARNING/INFO/TEXT_DISABLED 业务语义色存在性与注释守护 (P2-11)。"""

    def _theme_source(self) -> str:
        return Path(inspect.getsourcefile(AppColors)).read_text(encoding="utf-8")

    def test_success_exists_as_layer2_hex(self):
        """AppColors.SUCCESS 必须存在且为 Layer 2 hex 字符串 (非 ft.Colors.X 引用)。"""
        assert hasattr(AppColors, "SUCCESS")
        assert isinstance(AppColors.SUCCESS, str)
        assert AppColors.SUCCESS.startswith("#"), "SUCCESS 必须是 hex 字符串 (Layer 2 业务色)"

    def test_warning_exists_as_layer2_hex(self):
        """AppColors.WARNING 必须存在且为 Layer 2 hex 字符串。"""
        assert hasattr(AppColors, "WARNING")
        assert isinstance(AppColors.WARNING, str)
        assert AppColors.WARNING.startswith("#"), "WARNING 必须是 hex 字符串 (Layer 2 业务色)"

    def test_info_exists_as_layer2_hex(self):
        """AppColors.INFO 必须存在且为 Layer 2 hex 字符串。"""
        assert hasattr(AppColors, "INFO")
        assert isinstance(AppColors.INFO, str)
        assert AppColors.INFO.startswith("#"), "INFO 必须是 hex 字符串 (Layer 2 业务色)"

    def test_text_disabled_exists_as_layer2_hex(self):
        """AppColors.TEXT_DISABLED 必须存在且为 Layer 2 hex 字符串 (P1-6 #120)。"""
        assert hasattr(AppColors, "TEXT_DISABLED")
        assert isinstance(AppColors.TEXT_DISABLED, str)
        assert AppColors.TEXT_DISABLED.startswith("#"), "TEXT_DISABLED 必须是 hex 字符串"

    def test_text_disabled_in_custom_color_presets(self):
        """TEXT_DISABLED 必须在 4 主题 CUSTOM_COLOR_PRESETS 中均有定义 (随主题切换)。"""
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            assert "TEXT_DISABLED" in CUSTOM_COLOR_PRESETS[theme], (
                f"{theme} 主题 CUSTOM_COLOR_PRESETS 必须包含 TEXT_DISABLED"
            )

    def test_success_warning_info_comment_present(self):
        """SUCCESS/WARNING/INFO 业务语义直通色必须有注释说明用途 (P2-11)."""
        source = self._theme_source()
        # 业务语义直通色 注释行
        assert "业务语义直通色" in source, "SUCCESS/WARNING/INFO 必须有注释说明用途"
        assert "SUCCESS 成功" in source
        assert "WARNING 警告" in source
        assert "INFO 信息状态色" in source

    def test_text_disabled_comment_present(self):
        """TEXT_DISABLED 必须有注释说明与 TEXT_HINT 区分及用途 (P1-6 #120)."""
        source = self._theme_source()
        assert "TEXT_DISABLED" in source
        # 注释需说明与 TEXT_HINT 区分 + 用于 TaskStatus.INTERRUPTED
        text_disabled_line = next(line for line in source.splitlines() if "TEXT_DISABLED" in line and "=" in line)
        # 查找上方注释
        lines = source.splitlines()
        idx = lines.index(text_disabled_line)
        comment_lines = [lines[i] for i in range(max(0, idx - 3), idx) if lines[i].strip().startswith("#")]
        comment_text = "\n".join(comment_lines)
        assert "TEXT_HINT" in comment_text or "TaskStatus" in comment_text, (
            "TEXT_DISABLED 注释必须说明与 TEXT_HINT 区分 或 TaskStatus.INTERRUPTED 用途"
        )


# ============================================================================
# Layer 1 / Layer 2 同名禁止契约 (架构边界守护)
# ============================================================================


class TestLayer1Layer2NameSeparation:
    """Layer 1 语义 token (ft.Colors.X 引用) 与 Layer 2 业务色 (hex) 不得同名。

    Layer 1 token 是 ft.Colors.X 引用 (随主题自动切换), Layer 2 业务色是 hex 字符串
    (load_theme 手动切换). 同名会引发混淆: AppColors.X 到底是 Layer 1 还是 Layer 2?
    """

    def _classify_appcolors_attrs(self) -> tuple[set[str], set[str]]:
        """分类 AppColors 类属性: Layer 1 (ft.Colors.X) / Layer 2 (hex string)."""
        layer1: set[str] = set()
        layer2: set[str] = set()
        for name in dir(AppColors):
            if name.startswith("_"):
                continue
            value = getattr(AppColors, name)
            # 跳过方法/类型/模块
            if callable(value) or not isinstance(value, str):
                continue
            # ft.Colors.X 在 Flet 中是 str (常量化), 但语义上是 Layer 1 引用
            # 通过值是否在 ft.Colors 中来判断
            if hasattr(ft.Colors, name) and getattr(ft.Colors, name) == value:
                layer1.add(name)
            elif value.startswith("#"):
                layer2.add(name)
        return layer1, layer2

    def test_layer1_layer2_no_name_overlap(self):
        """Layer 1 token 名与 Layer 2 业务色名不得重叠 (同名禁止)。"""
        layer1, layer2 = self._classify_appcolors_attrs()
        overlap = layer1 & layer2
        assert not overlap, (
            f"Layer 1 / Layer 2 同名禁止违反: {overlap} 既在 Layer 1 (ft.Colors.X) 又在 Layer 2 (hex string) 中定义"
        )

    def test_layer2_business_colors_in_custom_presets(self):
        """Layer 2 业务色必须在 4 主题 CUSTOM_COLOR_PRESETS 中均有定义 (随主题切换)."""
        _, layer2 = self._classify_appcolors_attrs()
        # 排除 RISE/FALL/TABLE_GRID_V/TABLE_GRID_H 等别名 (load_theme 中手动同步)
        aliases = {"RISE", "FALL", "TABLE_GRID_V", "TABLE_GRID_H", "CARD_BG"}
        # TABLE_ROW_HOVER 4 主题 preset 补值属于批次 3 P2-8, 批次 1 暂排除
        pending_batch3 = {"TABLE_ROW_HOVER"}
        layer2_non_alias = layer2 - aliases - pending_batch3
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            preset_keys = set(CUSTOM_COLOR_PRESETS[theme].keys())
            missing = layer2_non_alias - preset_keys
            assert not missing, f"{theme} 主题 CUSTOM_COLOR_PRESETS 缺少 Layer 2 业务色: {missing}"


# ============================================================================
# WCAG 2.1 §1.4.3 对比度契约 (4 主题关键色对)
# ============================================================================


class TestWCAGContrastCompliance:
    """WCAG 2.1 §1.4.3 对比度契约: 4 主题所有关键色对必须达到阈值 (P1-6)."""

    def test_contrast_check_passes(self):
        """调用 scripts/check_theme_contrast.py 的 check_contrast 验证 4 主题对比度。"""
        import sys
        from pathlib import Path as _Path

        scripts_dir = _Path(__file__).resolve().parents[3] / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from check_theme_contrast import check_contrast
        finally:
            if str(scripts_dir) in sys.path:
                sys.path.remove(str(scripts_dir))

        errors = check_contrast()
        assert errors == [], "WCAG 对比度未达标:\n  " + "\n  ".join(errors)

    def test_4_themes_present_in_color_schemes(self):
        """4 主题 (Dark/Light/Navy/Dracula) 必须在 THEME_COLOR_SCHEMES 中均有定义。"""
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            assert theme in THEME_COLOR_SCHEMES, f"{theme} 主题缺失于 THEME_COLOR_SCHEMES"

    def test_4_themes_present_in_custom_color_presets(self):
        """4 主题必须在 CUSTOM_COLOR_PRESETS 中均有定义。"""
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            assert theme in CUSTOM_COLOR_PRESETS, f"{theme} 主题缺失于 CUSTOM_COLOR_PRESETS"
