import ast
import importlib
import inspect
import textwrap
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


def _theme_module_ast(source: str | None = None) -> ast.Module:
    """解析 ui/theme.py (或合成源码) 的 AST。"""
    if source is None:
        source = Path(inspect.getsourcefile(AppColors)).read_text(encoding="utf-8")
    return ast.parse(source)


def _find_top_level_class(module: ast.Module, class_name: str) -> ast.ClassDef:
    """定位模块顶层的指定类 (不递归, 避免命中嵌套或继承的同名类)。"""
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"未找到顶层类 {class_name}")


def _iter_target_names(target: ast.expr) -> list[str]:
    """展开赋值目标的属性名 (支持元组/列表解包)。"""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_iter_target_names(element))
        return names
    return []


def _class_assignments(class_node: ast.ClassDef) -> list[tuple[str, ast.expr, int]]:
    """收集类体内全部赋值 (含 AnnAssign), 按源码顺序返回 (名称, 值节点, 行号)。

    同名多次赋值会全部保留以供重复定义检测; 仅有注解无值的 AnnAssign 不构成定义。
    """
    assignments: list[tuple[str, ast.expr, int]] = []
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignments.extend((name, node.value, node.lineno) for name in _iter_target_names(target))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.extend((name, node.value, node.lineno) for name in _iter_target_names(node.target))
    return assignments


def _flet_import_aliases(module: ast.Module) -> tuple[set[str], set[str]]:
    """返回 (flet 模块名/别名集合, 直接导入的 Colors 名称集合), 供 ft.Colors 识别使用。"""
    flet_names = {"flet"}
    colors_names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "flet":
                    flet_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "flet":
            for alias in node.names:
                if alias.name == "Colors":
                    colors_names.add(alias.asname or alias.name)
    return flet_names, colors_names


def _is_ft_colors_ref(value: ast.expr, flet_names: set[str], colors_names: set[str]) -> bool:
    """判断右值是否为 flet.Colors.X 引用 (支持 import 别名)。"""
    if not isinstance(value, ast.Attribute):
        return False
    base = value.value
    if isinstance(base, ast.Attribute) and base.attr == "Colors" and isinstance(base.value, ast.Name):
        return base.value.id in flet_names
    return isinstance(base, ast.Name) and base.id in colors_names


def _is_hex_literal(value: ast.expr) -> bool:
    """判断右值是否为 hex 颜色字面量。"""
    return isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith("#")


def _layer_attr_names(
    assignments: list[tuple[str, ast.expr, int]],
    flet_names: set[str],
    colors_names: set[str],
) -> tuple[set[str], set[str]]:
    """按赋值右值的静态形态划分 Layer 1 (ft.Colors.X) / Layer 2 (hex 字面量)。"""
    layer1 = {name for name, value, _ in assignments if _is_ft_colors_ref(value, flet_names, colors_names)}
    layer2 = {name for name, value, _ in assignments if _is_hex_literal(value)}
    return layer1, layer2


def _duplicate_definitions(assignments: list[tuple[str, ast.expr, int]]) -> dict[str, list[int]]:
    """返回类体内被多次赋值的属性名及各次赋值行号 (含跨层与同层重复)。"""
    lines_by_name: dict[str, list[int]] = {}
    for name, _, lineno in assignments:
        lines_by_name.setdefault(name, []).append(lineno)
    return {name: lines for name, lines in lines_by_name.items() if len(lines) > 1}


def _module_level_overrides(module: ast.Module, class_name: str, class_attrs: set[str]) -> list[tuple[str, int]]:
    """检测模块顶层对已有类属性的覆盖赋值 (AppColors.X = ... 或 setattr(AppColors, "X", ...))。

    只扫描模块顶层语句: 类方法内的 setattr (如 load_theme 的主题切换) 属运行时赋值,
    不是定义重复, 不计入。
    """
    overrides: list[tuple[str, int]] = []

    def add_attribute_target(target: ast.expr, lineno: int) -> None:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == class_name
            and target.attr in class_attrs
        ):
            overrides.append((target.attr, lineno))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add_attribute_target(element, lineno)

    for node in module.body:
        if isinstance(node, ast.ClassDef):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add_attribute_target(target, node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            add_attribute_target(node.target, node.lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "setattr"
                and len(call.args) >= 2
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == class_name
                and isinstance(call.args[1], ast.Constant)
                and isinstance(call.args[1].value, str)
                and call.args[1].value in class_attrs
            ):
                overrides.append((call.args[1].value, node.lineno))
    return overrides


class TestLayer1Layer2NameSeparation:
    """Layer 1 语义 token (ft.Colors.X 引用) 与 Layer 2 业务色 (hex) 不得同名。

    运行时 dir()/getattr 只见最后一次赋值, 无法发现同名覆盖; 因此改为按 AST 静态扫描
    AppColors 类体的全部赋值目标: 重复定义本身即失败 (即使运行时最后值合法)。
    """

    def test_appcolors_has_no_duplicate_attr_definitions(self):
        """AppColors 类体不得重复赋值同一属性 (同名覆盖会静默丢弃前值)。"""
        class_node = _find_top_level_class(_theme_module_ast(), "AppColors")
        assignments = _class_assignments(class_node)
        assert assignments, "AppColors 类体赋值扫描为空, 检测失效"
        duplicates = _duplicate_definitions(assignments)
        assert not duplicates, f"AppColors 存在重复属性定义 (属性 -> 行号): {duplicates}"

    def test_appcolors_has_no_module_level_overrides(self):
        """类定义后不得在模块顶层再次给 AppColors 已有属性赋值或 setattr。"""
        module = _theme_module_ast()
        class_node = _find_top_level_class(module, "AppColors")
        class_attrs = {name for name, _, _ in _class_assignments(class_node)}
        overrides = _module_level_overrides(module, "AppColors", class_attrs)
        assert not overrides, f"AppColors 存在模块顶层覆盖赋值 (属性, 行号): {overrides}"

    def test_layer1_layer2_no_name_overlap(self):
        """Layer 1 token 名与 Layer 2 业务色名不得重叠 (同名禁止)。"""
        module = _theme_module_ast()
        class_node = _find_top_level_class(module, "AppColors")
        flet_names, colors_names = _flet_import_aliases(module)
        layer1, layer2 = _layer_attr_names(_class_assignments(class_node), flet_names, colors_names)
        assert layer1 and layer2, "真实 AppColors 两层集合不得为空 (否则分类逻辑失效)"
        overlap = layer1 & layer2
        assert not overlap, (
            f"Layer 1 / Layer 2 同名禁止违反: {overlap} 既在 Layer 1 (ft.Colors.X) 又在 Layer 2 (hex string) 中定义"
        )

    def test_layer2_business_colors_in_custom_presets(self):
        """Layer 2 业务色必须在 4 主题 CUSTOM_COLOR_PRESETS 中均有定义 (随主题切换)."""
        module = _theme_module_ast()
        class_node = _find_top_level_class(module, "AppColors")
        flet_names, colors_names = _flet_import_aliases(module)
        _, layer2 = _layer_attr_names(_class_assignments(class_node), flet_names, colors_names)
        assert layer2, "Layer 2 赋值扫描为空, 检测失效"
        # 排除 TABLE_GRID_V/TABLE_GRID_H 等别名 (load_theme 中手动同步)
        aliases = {"TABLE_GRID_V", "TABLE_GRID_H"}
        layer2_non_alias = layer2 - aliases
        for theme in (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA):
            preset_keys = set(CUSTOM_COLOR_PRESETS[theme].keys())
            missing = layer2_non_alias - preset_keys
            assert not missing, f"{theme} 主题 CUSTOM_COLOR_PRESETS 缺少 Layer 2 业务色: {missing}"

    def test_same_name_in_both_layers_fails_even_if_last_value_valid(self):
        """合成负例: 先 Layer 1 后 Layer 2 同名, 运行时最后值合法也必须失败。"""
        module = _theme_module_ast(
            textwrap.dedent(
                """
                import flet as ft

                class AppColors:
                    PRIMARY = ft.Colors.PRIMARY
                    PRIMARY = "#FF0000"
                """
            )
        )
        class_node = _find_top_level_class(module, "AppColors")
        assignments = _class_assignments(class_node)
        flet_names, colors_names = _flet_import_aliases(module)

        # 运行时最后一次赋值是合法 hex, 单看值无法发现问题
        assert _is_hex_literal(assignments[-1][1])

        assert "PRIMARY" in _duplicate_definitions(assignments)
        layer1, layer2 = _layer_attr_names(assignments, flet_names, colors_names)
        assert layer1 & layer2 == {"PRIMARY"}

    def test_same_name_twice_in_same_layer_fails(self):
        """合成负例: 同层重复赋值同样静默覆盖, 必须失败。"""
        module = _theme_module_ast(
            textwrap.dedent(
                """
                class AppColors:
                    UP_RED = "#FF0000"
                    UP_RED = "#00FF00"
                """
            )
        )
        assignments = _class_assignments(_find_top_level_class(module, "AppColors"))
        assert "UP_RED" in _duplicate_definitions(assignments)

    def test_module_level_override_detected(self):
        """合成负例: 类体定义后再在模块顶层覆盖同名属性 (赋值/setattr) 必须被识别。"""
        module = _theme_module_ast(
            textwrap.dedent(
                """
                class AppColors:
                    UP_RED = "#FF0000"
                    DOWN_GREEN = "#00FF00"

                AppColors.UP_RED = "#00FF00"
                setattr(AppColors, "DOWN_GREEN", "#0000FF")
                """
            )
        )
        class_node = _find_top_level_class(module, "AppColors")
        class_attrs = {name for name, _, _ in _class_assignments(class_node)}
        overrides = _module_level_overrides(module, "AppColors", class_attrs)
        assert {name for name, _ in overrides} == {"UP_RED", "DOWN_GREEN"}

    def test_inherited_same_name_is_not_a_duplicate(self):
        """合成正例: 基类与子类各自定义同名属性不属同一类体重复, 不应误报。"""
        module = _theme_module_ast(
            textwrap.dedent(
                """
                import flet as ft

                class BaseColors:
                    PRIMARY = ft.Colors.PRIMARY

                class AppColors(BaseColors):
                    PRIMARY = "#FF0000"
                """
            )
        )
        class_node = _find_top_level_class(module, "AppColors")
        assignments = _class_assignments(class_node)
        flet_names, colors_names = _flet_import_aliases(module)
        assert _duplicate_definitions(assignments) == {}
        layer1, layer2 = _layer_attr_names(assignments, flet_names, colors_names)
        assert layer1 & layer2 == set()

    def test_import_aliases_and_attribute_alias_do_not_misclassify(self):
        """合成正例: import 别名可识别 Layer 1; 属性别名不应被误判为第二层定义。"""
        module = _theme_module_ast(
            textwrap.dedent(
                """
                import flet as f
                from flet import Colors as C

                class AppColors:
                    SURFACE = f.Colors.SURFACE
                    PRIMARY = C.PRIMARY
                    BACKGROUND = SURFACE
                """
            )
        )
        class_node = _find_top_level_class(module, "AppColors")
        assignments = _class_assignments(class_node)
        flet_names, colors_names = _flet_import_aliases(module)
        layer1, layer2 = _layer_attr_names(assignments, flet_names, colors_names)
        assert layer1 == {"SURFACE", "PRIMARY"}
        assert layer2 == set()
        assert _duplicate_definitions(assignments) == {}


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
