"""F04 主题热切换颜色动态化契约测试.

F04 缺陷: 多个消费模块在导入期把 ``AppColors`` 当时的 Hex 值复制进模块级字典,
``AppColors.load_theme`` 只更新类属性, 消费方渲染仍使用冻结的旧 Hex —— 深色切浅色后
成功/警告状态色不随之更新.

本测试守护两点:
1. 消费模块的颜色映射必须保存 ``AppColors`` 语义名 (而非导入期 Hex 快照);
2. 渲染路径在每次渲染/toast 创建时读取当期主题色, 主题切换后随之变化.
"""

import importlib
import inspect
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
)
from ui.components import toast_manager as tm_module
from ui.components.toast_manager import (
    ToastCard,
    ToastData,
    _resolve_color,
    _resolve_icon,
)
from ui.theme import CUSTOM_COLOR_PRESETS, AppColors, ThemeName
from ui.views.backtest_view import _resolve_credibility_accent, _resolve_status_color as _bt_status
from ui.views.screener_view import _resolve_status_color as _screener_status
from ui.views.task_center_view import _get_status_color

pytestmark = pytest.mark.unit

_THEMES = (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA)

# F04 报告点名的全部状态色消费模块 → 其颜色映射变量名.
_CONSUMER_MAPS: list[tuple[str, str]] = [
    ("ui.views.screener_view", "_STATUS_COLOR_MAP"),
    ("ui.views.backtest_view", "_STATUS_COLOR_MAP"),
    ("ui.views.backtest_view", "_CREDIBILITY_ACCENT_MAP"),
    ("ui.views.task_center_view", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.tushare_config_panel", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.local_model_config_panel", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.llm_config_panel", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.database_status_panel", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.embedded_status_card", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.external_pg_form", "_STATUS_COLOR_MAP"),
    ("ui.components.config_panels.failover_config_panel", "_STATUS_COLOR_MAP"),
]

# 直接使用自定义 Hex 的 config_panels 组件 → 必须订阅 AppColors 主题状态以触发重渲染.
_PANEL_MODULES: list[str] = sorted(
    {module_path for module_path, _ in _CONSUMER_MAPS if module_path.startswith("ui.components.config_panels")}
)


# ============================================================================
# 0. 直接使用自定义 Hex 的组件须订阅主题 (F04 步骤 3: 只改 getattr 不足以热切换)
# ============================================================================


@pytest.mark.parametrize("module_path", _PANEL_MODULES)
def test_panel_component_subscribes_appcolors_state(module_path: str) -> None:
    """直接使用自定义 Hex 的面板组件须订阅 AppColors 可观察状态 (F04 步骤 3)."""
    source = inspect.getsource(importlib.import_module(module_path))
    assert "AppColors.get_observable_state" in source, (
        f"{module_path} 未订阅 AppColors 主题状态; 主题切换不会触发其重渲染 (F04)"
    )


# ============================================================================
# 1. 颜色映射不得固化导入期 Hex 快照
# ============================================================================


@pytest.mark.parametrize(("module_path", "map_name"), _CONSUMER_MAPS)
def test_color_map_stores_semantic_appcolors_names(module_path: str, map_name: str) -> None:
    """消费模块的颜色映射须存 AppColors 语义名, 而非导入期 Hex/token 快照."""
    module = importlib.import_module(module_path)
    color_map = getattr(module, map_name)
    assert color_map, f"{module_path}.{map_name} 不应为空"
    for key, value in color_map.items():
        assert isinstance(value, str), f"{module_path}.{map_name}[{key!r}] 必须是字符串语义名"
        assert not value.startswith("#"), (
            f"{module_path}.{map_name}[{key!r}] = {value!r} 固化了 Hex 快照; 必须存语义名 (F04)"
        )
        assert hasattr(AppColors, value), f"{module_path}.{map_name}[{key!r}] = {value!r} 不是 AppColors 语义属性名"


# ============================================================================
# 2. 状态色解析函数随主题变化 (status 文本消费路径)
# ============================================================================


@pytest.mark.parametrize("theme", _THEMES)
def test_screener_status_color_follows_current_theme(theme: str) -> None:
    AppColors.load_theme(theme)
    assert _screener_status("success") == CUSTOM_COLOR_PRESETS[theme]["SUCCESS"]
    assert _screener_status("warning") == CUSTOM_COLOR_PRESETS[theme]["WARNING"]
    assert _screener_status("info") == CUSTOM_COLOR_PRESETS[theme]["INFO"]


@pytest.mark.parametrize("theme", _THEMES)
def test_backtest_status_color_follows_current_theme(theme: str) -> None:
    AppColors.load_theme(theme)
    assert _bt_status("success") == CUSTOM_COLOR_PRESETS[theme]["SUCCESS"]
    assert _bt_status("warning") == CUSTOM_COLOR_PRESETS[theme]["WARNING"]


@pytest.mark.parametrize("theme", _THEMES)
def test_backtest_credibility_accent_follows_current_theme(theme: str) -> None:
    AppColors.load_theme(theme)
    assert _resolve_credibility_accent("degraded") == CUSTOM_COLOR_PRESETS[theme]["WARNING"]
    assert _resolve_credibility_accent("ok") == CUSTOM_COLOR_PRESETS[theme]["INFO"]


def test_task_center_status_color_follows_current_theme() -> None:
    from ui.viewmodels.task_center_view_model import TaskStatus

    AppColors.load_theme(ThemeName.DARK)
    dark_success = _get_status_color(TaskStatus.COMPLETED)
    AppColors.load_theme(ThemeName.LIGHT)
    light_success = _get_status_color(TaskStatus.COMPLETED)
    assert dark_success == CUSTOM_COLOR_PRESETS[ThemeName.DARK]["SUCCESS"]
    assert light_success == CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["SUCCESS"]
    assert dark_success != light_success, "切主题后任务状态色必须变化 (F04)"


# ============================================================================
# 3. Toast 颜色动态化 (新 toast + 已显示 toast + 前景/背景配对)
# ============================================================================


def _find_icon(root: Any, name: str) -> ft.Icon:
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Icon) and ctrl.icon == name:
            return ctrl
    raise AssertionError(f"Icon {name!r} not found")


def _walk_controls(root: Any) -> list[Any]:
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
            children = getattr(c, "content", None)
            if children is not None:
                _walk(children)
            for x in getattr(c, "controls", None) or []:
                if x is not None:
                    _walk(x)
        from flet.components.component import Component

        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)

    _walk(root)
    return found


def _make_card(toast_type: str = "success") -> tuple[ToastData, Any]:
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    data = ToastData(id=1, message="msg", icon=_resolve_icon(toast_type), toast_type=toast_type, duration=10)
    component = make_component(ToastCard, data=data, on_dismiss=MagicMock())
    run_mount_effects(component, page=page)
    return data, component


@pytest.mark.parametrize("theme", _THEMES)
def test_new_toast_uses_current_theme_color(theme: str) -> None:
    """新 toast 渲染取当期主题色 (而非导入期快照)."""
    AppColors.load_theme(theme)
    data, component = _make_card("success")
    ctrl = render_once(component)
    icon = _find_icon(ctrl, data.icon)
    assert icon.color == CUSTOM_COLOR_PRESETS[theme]["SUCCESS"]


def test_existing_toast_recomputes_color_after_theme_switch() -> None:
    """已显示 toast 在主题切换后重算颜色 (F04 关键验收)."""
    AppColors.load_theme(ThemeName.DARK)
    data, component = _make_card("warning")

    ctrl = render_once(component)
    assert _find_icon(ctrl, data.icon).color == CUSTOM_COLOR_PRESETS[ThemeName.DARK]["WARNING"]

    AppColors.load_theme(ThemeName.LIGHT)
    ctrl = render_once(component)
    assert _find_icon(ctrl, data.icon).color == CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["WARNING"], (
        "已显示 toast 必须随主题切换重算颜色 (F04)"
    )


@pytest.mark.parametrize("theme", _THEMES)
def test_toast_foreground_and_background_pair(theme: str) -> None:
    """toast 强调色 (业务色, 随主题) 与背景 (语义 token) 成对渲染."""
    AppColors.load_theme(theme)
    data, component = _make_card("success")
    ctrl = render_once(component)
    expected = CUSTOM_COLOR_PRESETS[theme]["SUCCESS"]

    icon = _find_icon(ctrl, data.icon)
    assert icon.color == expected
    assert ctrl.border.left.color == expected  # 左侧强调边框与图标同源
    assert ctrl.bgcolor == AppColors.SURFACE  # 背景走语义 token, 由 Flet 解释
    # 前景正文色走语义 token (TEXT_PRIMARY), 与背景配对
    text = next(c for c in _walk_controls(ctrl) if isinstance(c, ft.Text) and c.value == "msg")
    assert text.color == AppColors.TEXT_PRIMARY
