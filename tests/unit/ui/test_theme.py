import importlib
import inspect
import types
from pathlib import Path
from unittest.mock import patch

import flet as ft
import pytest

from ui.theme import (
    THEME_COLOR_SCHEMES,
    AppColors,
    AppColorsState,
    AppStyles,
    ThemeColors,
    ThemeName,
    CUSTOM_COLOR_PRESETS,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _save_restore_appcolors():
    saved_attrs = {key: getattr(AppColors, key) for key in CUSTOM_COLOR_PRESETS[ThemeName.DARK]}
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
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["UP_RED"] == AppColors.UP_RED
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["DOWN_GREEN"] == AppColors.DOWN_GREEN

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
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["UP_RED"] == AppColors.UP_RED
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["DOWN_GREEN"] == AppColors.DOWN_GREEN

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


class TestAppColorsResetSingleton:
    """AppColors 类级状态单例 reset 契约（R7/R15）：污染后复位 DARK 基线。

    由 tests/unit/conftest.py 的 _reset_all_singletons autouse fixture 间接调用，
    此处显式守卫 _reset_singleton 逻辑，防止未来扩展颜色/别名时遗漏复位。
    """

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_reset_restores_dark_baseline_after_pollution(self):
        # 污染：切换到 LIGHT，触发 Observable state 通知
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors._CURRENT_THEME_NAME == ThemeName.LIGHT
        assert AppColors.get_observable_state().theme_name == ThemeName.LIGHT

        AppColors._reset_singleton()

        # Layer 2 颜色全部复位为 DARK preset
        for key, value in CUSTOM_COLOR_PRESETS[ThemeName.DARK].items():
            assert getattr(AppColors, key) == value
        # 别名与当前主题复位
        assert AppColors.TABLE_GRID_V == AppColors.TABLE_GRID
        assert AppColors.TABLE_GRID_H == AppColors.TABLE_GRID
        assert AppColors._CURRENT_THEME_NAME == ThemeName.DARK
        assert AppColors._CURRENT_THEME_MODE == ft.ThemeMode.DARK
        # Observable state 置空待惰性重建，重建回 DARK 默认
        assert AppColors._state is None
        assert AppColors.get_observable_state().theme_name == ThemeName.DARK
        # 类级实例占位复位（R7 契约）
        assert AppColors._instance is None


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

    def test_hovered_returns_hover_color(self):
        """P2-8: hover 状态返回独立的 TABLE_ROW_HOVER (修复原误返回 ODD 的逻辑漏洞)."""
        result = AppStyles.data_table_row(1, is_hovered=True)
        assert result == AppColors.TABLE_ROW_HOVER


class TestAppStylesPriceChangeColor:
    def test_positive_returns_up(self):
        result = AppStyles.price_change_color(1.5)
        assert result == AppColors.UP_RED

    def test_negative_returns_down(self):
        result = AppStyles.price_change_color(-2.0)
        assert result == AppColors.DOWN_GREEN

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
        # 排除 TABLE_GRID_V/TABLE_GRID_H 等别名 (load_theme 中手动同步)
        aliases = {"TABLE_GRID_V", "TABLE_GRID_H"}
        layer2_non_alias = layer2 - aliases
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            preset_keys = set(CUSTOM_COLOR_PRESETS[theme].keys())
            missing = layer2_non_alias - preset_keys
            assert not missing, f"{theme} 主题 CUSTOM_COLOR_PRESETS 缺少 Layer 2 业务色: {missing}"


class TestAppColorsTokenValidity:
    """AppColors 颜色 token 必须可被 Flet 解析 (hex 或 ft.Colors 成员值)。

    回归守护: BACKGROUND 曾为 V0 残留字符串 "background"——Flet 1.x 既无该颜色名,
    ColorScheme 也无 background 字段 (M3 已并入 surface); 该值既非 hex 又非
    ft.Colors 成员, 会被上方 Layer1/Layer2 分类逻辑静默忽略, 消费方
    (app_layout / 详情对话框 / 向导等) 拿到无法解析的背景色。
    """

    def test_all_public_color_tokens_are_flet_resolvable(self):
        """所有公开颜色 token 必须为合法 hex 或 ft.Colors 成员值。"""
        valid_values = {member.value for member in ft.Colors}
        invalid: dict[str, str] = {}
        for name in vars(AppColors):
            if name.startswith("_"):
                continue
            value = getattr(AppColors, name)
            if callable(value) or not isinstance(value, str):
                continue
            if value.startswith("#") or value.lower() in valid_values:
                continue
            invalid[name] = value
        assert not invalid, f"AppColors 存在 Flet 无法解析的颜色 token: {invalid}"

    def test_background_maps_to_surface_token(self):
        """BACKGROUND 必须映射到 M3 surface 语义 token (Flet 1.x 无 background)。"""
        assert AppColors.BACKGROUND == ft.Colors.SURFACE


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


# ============================================================================
# 涨跌色单轨契约 (P2-1: 禁止 UP/DOWN/RISE/FALL 双轨复活)
# ============================================================================


class TestUpDownSingleTrackContract:
    """涨跌语义色单轨契约 (P2-1): 仅 UP_RED/DOWN_GREEN 单轨，禁止 UP/DOWN/RISE/FALL 复活。"""

    def test_theme_colors_no_up_down_fields(self):
        """ThemeColors TypedDict 不得声明 UP/DOWN 字段 (单轨: 仅 UP_RED/DOWN_GREEN)。"""
        assert "UP" not in ThemeColors.__annotations__
        assert "DOWN" not in ThemeColors.__annotations__
        assert "UP_RED" in ThemeColors.__annotations__
        assert "DOWN_GREEN" in ThemeColors.__annotations__

    def test_appcolors_no_dual_track_attrs(self):
        """AppColors 不得存在 UP/DOWN/RISE/FALL 类属性 (双轨残留)。"""
        for attr in ("UP", "DOWN", "RISE", "FALL"):
            assert not hasattr(AppColors, attr), f"AppColors.{attr} 双轨残留，必须删除"

    def test_presets_no_up_down_keys(self):
        """4 主题 CUSTOM_COLOR_PRESETS 不得含 UP/DOWN key。"""
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            assert "UP" not in CUSTOM_COLOR_PRESETS[theme], f"{theme} preset 含 UP key"
            assert "DOWN" not in CUSTOM_COLOR_PRESETS[theme], f"{theme} preset 含 DOWN key"


# ============================================================================
# get_current_theme_mode 公开访问接口 (P3-M12-009)
# ============================================================================


class TestGetCurrentThemeMode:
    """P3-M12-009: get_current_theme_mode 公开方法避免外部访问 _CURRENT_THEME_MODE。"""

    def test_get_current_theme_mode_returns_loaded_theme(self):
        """get_current_theme_mode 返回 load_theme 设置的 ThemeMode。"""
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.get_current_theme_mode() == ft.ThemeMode.LIGHT
        AppColors.load_theme(ThemeName.DARK)
        assert AppColors.get_current_theme_mode() == ft.ThemeMode.DARK

    def test_get_current_theme_mode_default_is_dark(self):
        """默认 ThemeMode 为 DARK（_CURRENT_THEME_MODE 初始值）。"""
        # fixture 恢复后 _CURRENT_THEME_MODE 回到默认值 DARK
        assert AppColors.get_current_theme_mode() == ft.ThemeMode.DARK


# ============================================================================
# F06: 业务状态/涨跌文字按正文 4.5 验收 (非 WCAG 大字号例外)
# ============================================================================


def _f06_ctc() -> types.ModuleType:
    """加载门禁脚本模块, 复用其对比度算法与色对解析 (避免测试另立一套色表)。"""
    import sys as _sys

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    return importlib.import_module("check_theme_contrast")


class TestF06BusinessTextContrast:
    """F06: 业务状态/涨跌文字用于 13/14px 正文, 必须达 WCAG 正常文字 4.5。

    背景取真实消费布局:
    - SUCCESS/WARNING/INFO: 面板/卡片/Toast/列表行 → SURFACE 与 CARD_BG
      (4 主题下两 token 同值, 仍显式分别验收以防未来分化)。
    - UP_RED/DOWN_GREEN: 除面板背景外, virtual_table ``_build_cells`` 对涨跌列
      (``_TREND_COLS``, 13px 正文) 取 AppColors.UP_RED/DOWN_GREEN, 行底色由
      ``AppStyles.data_table_row`` 在 TABLE_ROW_ODD/EVEN/HOVER 间切换, 故三者均按
      正文 4.5 验收 (历史注释曾误称业务色无 EVEN 行消费场景, 掩盖了该缺陷)。
    """

    _PANEL_BACKGROUNDS = ("SURFACE", "CARD_BG")
    _ROW_BACKGROUNDS = ("TABLE_ROW_ODD", "TABLE_ROW_EVEN", "TABLE_ROW_HOVER")
    # 正文业务色 → 真实背景 token (含 INFO: Toast/任务中心/数据源进度文字)
    _TEXT_CASES = (
        ("SUCCESS", _PANEL_BACKGROUNDS),
        ("WARNING", _PANEL_BACKGROUNDS),
        ("INFO", _PANEL_BACKGROUNDS),
        ("UP_RED", _PANEL_BACKGROUNDS + _ROW_BACKGROUNDS),
        ("DOWN_GREEN", _PANEL_BACKGROUNDS + _ROW_BACKGROUNDS),
    )

    @pytest.mark.parametrize("theme", (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA))
    @pytest.mark.parametrize(("color", "backgrounds"), _TEXT_CASES)
    def test_business_text_color_meets_4_5_on_real_backgrounds(self, theme: str, color: str, backgrounds):
        """业务状态/涨跌正文色在各自真实背景上对比度 ≥ 4.5 (F06)。"""
        ctc = _f06_ctc()

        def resolve_valid_hex(token: str) -> str:
            """解析 token 并强断言结果为合法 7 位 hex 色值 (内容校验, 非仅存在性)。"""
            value = ctc._resolve_color(token, theme)
            assert (
                isinstance(value, str)
                and len(value) == 7
                and value.startswith("#")
                and all(ch in "0123456789abcdefABCDEF" for ch in value[1:])
            ), f"{theme}.{token} 解析结果非合法 hex 色值: {value!r}"
            return value

        fg = resolve_valid_hex(color)
        for bg_token in backgrounds:
            bg = resolve_valid_hex(bg_token)
            ratio = ctc.contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"{theme} {color}({fg}) on {bg_token}({bg}) = {ratio:.3f} < 4.5 (F06)"

    def test_dark_up_red_keeps_headroom_above_threshold(self):
        """深色 UP_RED 在阈值之上留余量 (原 #F44336 仅 4.53 压线, 派生背景微扰即跌破)。"""
        ctc = _f06_ctc()
        ratio = ctc.contrast_ratio(CUSTOM_COLOR_PRESETS[ThemeName.DARK]["UP_RED"], "#1E1E1E")
        assert ratio >= 5.0, f"DARK UP_RED 对比度 {ratio:.3f} 应 ≥ 5.0 以留出派生背景余量 (F06)"

    def test_light_old_business_colors_were_below_4_5_regression(self):
        """回归: 修复前浅色 preset 的旧值确实低于 4.5 (证明本次调色是必要修复)。"""
        # 旧 DOWN_GREEN/SUCCESS #388E3C 在白底 4.12; 旧 WARNING #EF6C00 白底 3.08; 旧 UP_RED #F44336 白底 3.68
        ctc = _f06_ctc()
        assert ctc.contrast_ratio("#388E3C", "#FFFFFF") < 4.5
        assert ctc.contrast_ratio("#EF6C00", "#FFFFFF") < 4.5
        assert ctc.contrast_ratio("#F44336", "#FFFFFF") < 4.5
        # 当前浅色 preset 已不再使用这些不达标值
        light = CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]
        assert light["DOWN_GREEN"] != "#388E3C"
        assert light["WARNING"] != "#EF6C00"
        assert light["UP_RED"] != "#F44336"

    def test_dark_old_info_was_below_4_5_regression(self):
        """回归: 深色 INFO 旧值 #2979FF 在深色 SURFACE 4.19 < 4.5 (证明本次调色必要)。"""
        ctc = _f06_ctc()
        assert ctc.contrast_ratio("#2979FF", "#1E1E1E") < 4.5
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["INFO"] != "#2979FF"

    def test_dracula_old_up_down_were_below_4_5_on_alternating_rows_regression(self):
        """回归: DRACULA 旧 UP_RED #FF5555 / DOWN_GREEN #4CAF50 在 EVEN/HOVER 行底色 < 4.5。

        旧值在 ODD(#282A36) 上恰好达标, 却在 EVEN(#44475A)/HOVER(#3A3C4E) 行上跌破,
        证明按真实交替行背景验收的必要性 (旧门禁只覆盖 SURFACE/CARD_BG)。
        """
        ctc = _f06_ctc()
        drac = CUSTOM_COLOR_PRESETS[ThemeName.DRACULA]
        for old_fg in ("#FF5555", "#4CAF50"):
            assert ctc.contrast_ratio(old_fg, drac["TABLE_ROW_ODD"]) >= 4.5
            assert ctc.contrast_ratio(old_fg, drac["TABLE_ROW_EVEN"]) < 4.5
            assert ctc.contrast_ratio(old_fg, drac["TABLE_ROW_HOVER"]) < 4.5
        assert drac["UP_RED"] != "#FF5555"
        assert drac["DOWN_GREEN"] != "#4CAF50"

    def test_appcolors_defaults_sync_dark_preset(self):
        """AppColors 类属性默认值必须与 DARK preset 全量同步 (DARK 是 _reset_singleton 复位基线)。"""
        dark = CUSTOM_COLOR_PRESETS[ThemeName.DARK]
        mismatched = {
            key: (getattr(AppColors, key), expected)
            for key, expected in dark.items()
            if getattr(AppColors, key) != expected
        }
        assert not mismatched, f"AppColors 默认值与 DARK preset 不一致: {mismatched}"

    @pytest.mark.parametrize(
        ("theme", "up", "down"),
        [
            (ThemeName.DARK, "#FF5A5A", "#4CAF50"),
            (ThemeName.LIGHT, "#C62828", "#2A722D"),
            (ThemeName.NAVY, "#FB8A8A", "#22C55E"),
            (ThemeName.DRACULA, "#FFAAAA", "#7CDF80"),
        ],
    )
    def test_rg_semantics_preserved(self, theme: str, up: str, down: str):
        """保留红涨绿跌语义: 各主题 UP_RED 为红色占优, DOWN_GREEN 为绿色占优 (F06)。"""

        def dominant(hex_color: str) -> str:
            r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
            return "red" if r > g and r > b else "green" if g > r and g > b else "other"

        preset = CUSTOM_COLOR_PRESETS[theme]
        assert preset["UP_RED"] == up
        assert preset["DOWN_GREEN"] == down
        assert dominant(preset["UP_RED"]) == "red", f"{theme} UP_RED 必须红色占优"
        assert dominant(preset["DOWN_GREEN"]) == "green", f"{theme} DOWN_GREEN 必须绿色占优"

    @pytest.mark.parametrize("theme", (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA))
    def test_info_semantics_blue_dominant(self, theme: str):
        """信息蓝语义: INFO 保持蓝色占优 (B 通道为最大分量, F06 调色不得改变色相语义)。"""
        hex_color = CUSTOM_COLOR_PRESETS[theme]["INFO"]
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        assert b > r and b > g, f"{theme} INFO({hex_color}) 必须蓝色占优"


# ============================================================================
# M30-UIB-THEME-03: ERROR 正文 / ON_ERROR 反色 / ERROR 弱底纹三类用途达 4.5
# ============================================================================


class TestErrorTextContrast:
    """ERROR 在 4 主题下同时承担三类用途, 均必须达 WCAG 正文 4.5。

    已确认消费点: 校验/错误提示文字 (BODY_SM/BODY)、错误图标、危险按钮反色
    (``AppStyles.danger_button``)、导航角标 (``ui/app_layout.py``)、
    ``health_report_dialog`` 问题列表 (叠 ERROR 弱底纹) 与覆盖徽标。
    """

    _THEMES = (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA)

    @staticmethod
    def _resolve(ctc: types.ModuleType, token: str, theme: str) -> str:
        value = ctc._resolve_color(token, theme)
        assert isinstance(value, str) and len(value) == 7 and value.startswith("#"), (
            f"{theme}.{token} 解析结果非合法 hex 色值: {value!r}"
        )
        return value

    @pytest.mark.parametrize("theme", _THEMES)
    @pytest.mark.parametrize("bg_token", ("SURFACE", "CARD_BG"))
    def test_error_text_meets_4_5(self, theme: str, bg_token: str):
        """ERROR 作为 13/14px 正文: 面板/卡片背景上对比度 ≥ 4.5。"""
        ctc = _f06_ctc()
        fg = self._resolve(ctc, "ERROR", theme)
        bg = self._resolve(ctc, bg_token, theme)
        ratio = ctc.contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"{theme} ERROR({fg}) on {bg_token}({bg}) = {ratio:.3f} < 4.5"

    @pytest.mark.parametrize("theme", _THEMES)
    def test_on_error_over_error_meets_4_5(self, theme: str):
        """危险按钮/角标反色: ON_ERROR 叠 ERROR 对比度 ≥ 4.5。"""
        ctc = _f06_ctc()
        fg = self._resolve(ctc, "ON_ERROR", theme)
        bg = self._resolve(ctc, "ERROR", theme)
        ratio = ctc.contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"{theme} ON_ERROR({fg}) on ERROR({bg}) = {ratio:.3f} < 4.5"

    @pytest.mark.parametrize("theme", _THEMES)
    def test_error_text_on_status_tint_meets_4_5(self, theme: str):
        """ERROR 正文叠 STATUS_TINT_ALPHA 弱底纹 (health_report_dialog) 时 ≥ 4.5。"""
        ctc = _f06_ctc()
        from ui.theme import STATUS_TINT_ALPHA

        fg = self._resolve(ctc, "ERROR", theme)
        base = self._resolve(ctc, "CARD_BG", theme)
        tint_bg = ctc.blend_alpha_over(fg, base, STATUS_TINT_ALPHA)
        ratio = ctc.contrast_ratio(fg, tint_bg)
        assert ratio >= 4.5, f"{theme} ERROR({fg}) on tint({tint_bg}) = {ratio:.3f} < 4.5"

    @pytest.mark.parametrize(
        ("theme", "error", "on_error"),
        [
            (ThemeName.DARK, "#FF5252", "#1E1E1E"),
            (ThemeName.LIGHT, "#D32F2F", "#FFFFFF"),
            (ThemeName.NAVY, "#F87171", "#0F172A"),
            (ThemeName.DRACULA, "#FF6E6E", "#282A36"),
        ],
    )
    def test_error_palette_values(self, theme: str, error: str, on_error: str):
        """锁定 4 主题 ERROR / ON_ERROR 取值 (方案 A 定案, 变更须同步复算三类用途)。"""
        scheme = THEME_COLOR_SCHEMES[theme]
        assert str(scheme.error) == error
        assert str(scheme.on_error) == on_error

    def test_dark_on_error_must_be_dark(self):
        """DARK: 白字反色与 ERROR 正文两约束无交集, on_error 必须是深色。"""
        ctc = _f06_ctc()
        scheme = THEME_COLOR_SCHEMES[ThemeName.DARK]
        assert ctc.contrast_ratio("#FFFFFF", str(scheme.error)) < 4.5, "白字反色在 DARK 下不可能达标"
        assert ctc.relative_luminance(str(scheme.on_error)) < ctc.relative_luminance(str(scheme.error))

    @pytest.mark.parametrize("theme", _THEMES)
    def test_error_semantics_red_dominant(self, theme: str):
        """ERROR 必须保持红色占优 (调色不得改变错误语义色相)。"""
        hex_color = str(THEME_COLOR_SCHEMES[theme].error)
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        assert r > g and r > b, f"{theme} ERROR({hex_color}) 必须红色占优"

    def test_dark_old_error_was_below_4_5_regression(self):
        """回归: DARK 旧 ERROR #FF1744 在 #1E1E1E 上仅 4.33 < 4.5 (证明调色必要)。"""
        ctc = _f06_ctc()
        assert ctc.contrast_ratio("#FF1744", "#1E1E1E") < 4.5
        assert str(THEME_COLOR_SCHEMES[ThemeName.DARK].error) != "#FF1744"

    def test_dracula_old_error_was_below_4_5_on_tint_regression(self):
        """回归: DRACULA 旧 ERROR #FF5555 叠弱底纹后 < 4.5 (证明调色必要)。"""
        ctc = _f06_ctc()
        from ui.theme import STATUS_TINT_ALPHA

        old = "#FF5555"
        tint_bg = ctc.blend_alpha_over(old, "#282A36", STATUS_TINT_ALPHA)
        assert ctc.contrast_ratio(old, tint_bg) < 4.5
        assert str(THEME_COLOR_SCHEMES[ThemeName.DRACULA].error) != old

    def test_light_error_at_10_percent_tint_would_fail(self):
        """回归: LIGHT 在 10% 底纹上 4.28 < 4.5, 故 STATUS_TINT_ALPHA 必须低于 0.1。"""
        ctc = _f06_ctc()
        from ui.theme import STATUS_TINT_ALPHA

        error = str(THEME_COLOR_SCHEMES[ThemeName.LIGHT].error)
        assert ctc.contrast_ratio(error, ctc.blend_alpha_over(error, "#FFFFFF", 0.1)) < 4.5
        assert STATUS_TINT_ALPHA < 0.1
