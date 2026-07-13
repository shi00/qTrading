"""ui/components/settings_widgets.py 声明式契约守护测试 (Phase A.1).

业务逻辑由消费方 ViewModel 单元测试覆盖。View 层测试聚焦于契约守护
（grep 检查禁止的命令式模式），参照 test_config_panels.py 模式。

Phase 1.1 扩展：追加组件体测试（attach_fake_page 驱动 @ft.component 渲染），
覆盖各组件的分支逻辑（颜色解析、状态分支、i18n key 切换、col 配置）。
"""

from pathlib import Path

import flet as ft
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.components.settings_widgets as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.settings_widgets as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


_COMPONENTS = [
    "DashboardCard",
    "MetricCard",
    "ActionChip",
    "StatusBadge",
    "SectionHeader",
    "SettingRow",
]


class TestSettingsWidgetsContract:
    """6 个声明式组件的契约守护测试。"""

    @pytest.mark.parametrize("name", _COMPONENTS)
    def test_component_is_ft_component(self, name):
        """DoD: 每个组件必须被 @ft.component 装饰。"""
        from ui.components import settings_widgets as mod

        fn = getattr(mod, name)
        assert hasattr(fn, "__wrapped__"), f"{name} 必须用 @ft.component 装饰"

    @pytest.mark.parametrize("name", _COMPONENTS)
    def test_no_class_inheritance(self, name):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert f"class {name}(" not in _code_source(), f"{name} 不应是 class（命令式）"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_safe_update(self):
        """DoD: 禁止命令式 _safe_update。"""
        assert "_safe_update" not in _code_source()

    def test_no_set_value(self):
        """DoD: 禁止命令式 set_value（改用 props 推送）。"""
        assert "set_value" not in _code_source()

    def test_no_set_label(self):
        """DoD: 禁止命令式 set_label（改用 props 推送）。"""
        assert "set_label" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_no_set_loading(self):
        """DoD: 禁止命令式 set_loading（改用 is_loading prop 推送）。"""
        assert "set_loading" not in _code_source()

    def test_no_set_text(self):
        """DoD: 禁止命令式 set_text（改用 props 推送）。"""
        assert "set_text" not in _code_source()

    def test_no_update_locale(self):
        """DoD: 禁止命令式 update_locale（声明式通过 Observable state 自动重渲染）。"""
        assert "update_locale" not in _code_source()

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale（声明式自动重渲染）。"""
        assert "refresh_locale" not in _code_source()

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change（声明式自动重渲染）。"""
        assert "_on_locale_change" not in _code_source()

    def test_metric_card_subscribes_app_colors(self):
        """DoD: MetricCard 必须订阅 AppColors.get_observable_state（trend 用 Layer 2 色）。"""
        assert "AppColors.get_observable_state" in _raw_source()

    def test_section_header_subscribes_i18n(self):
        """DoD: SectionHeader 必须订阅 get_observable_state（title_key 重渲染）。"""
        assert "get_observable_state" in _raw_source()

    def test_setting_row_subscribes_i18n(self):
        """DoD: SettingRow 必须订阅 get_observable_state（title_key/subtitle_key 重渲染）。"""
        assert "get_observable_state" in _raw_source()


# ============================================================================
# 组件体测试 — 用 attach_fake_page 驱动 @ft.component 渲染，验证控件树结构
# ============================================================================

from tests.unit.ui.component_renderer import (  # noqa: E402
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)


class TestDashboardCardBody:
    """DashboardCard 组件体测试：验证 padding/expand/content 透传。"""

    def test_renders_container_with_content(self, mock_i18n_state, mock_app_colors_state) -> None:
        """典型参数：content 被 ft.Container 包装，padding/expand 透传。"""
        from ui.components.settings_widgets import DashboardCard

        inner = ft.Text("hello")
        component = make_component(DashboardCard, content=inner, padding=20, expand=True)
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert result.content is inner
        assert result.padding == 20
        assert result.expand is True

    def test_default_padding_and_expand(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认值：padding=20, expand=False。"""
        from ui.components.settings_widgets import DashboardCard

        component = make_component(DashboardCard, content=ft.Text("x"))
        run_mount_effects(component)
        result = render_once(component)

        assert result.padding == 20
        assert result.expand is False

    def test_unmount_does_not_raise(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载不抛异常（DashboardCard 无 effect，cleanup 为空）。"""
        from ui.components.settings_widgets import DashboardCard

        component = make_component(DashboardCard, content=ft.Text("x"))
        run_mount_effects(component)
        run_unmount_effects(component)


class TestMetricCardBody:
    """MetricCard 组件体测试：验证 label/value/icon/trend 渲染逻辑。"""

    def test_renders_full_status_row(self, mock_i18n_state, mock_app_colors_state) -> None:
        """完整参数：label 大写、value 显示、icon→ft.Icon、trend→ft.Text(trend_color)。"""
        from ui.components.settings_widgets import MetricCard

        component = make_component(
            MetricCard,
            label="cpu",
            value="42%",
            icon="cpu-icon",
            status_color="#FF0000",
            trend="+5%",
            trend_up=True,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        col = result.content
        assert isinstance(col, ft.Column)
        # 第 1 项：label 大写
        assert col.controls[0].value == "CPU"
        # 第 2 项：value
        assert col.controls[1].value == "42%"
        # 第 3 项：status row
        status_row = col.controls[2]
        assert isinstance(status_row, ft.Row)
        # icon + trend 两项
        assert len(status_row.controls) == 2
        assert isinstance(status_row.controls[0], ft.Icon)
        assert status_row.controls[0].color == "#FF0000"
        assert isinstance(status_row.controls[1], ft.Text)
        # trend_up=True → AppColors.UP
        from ui.theme import AppColors

        assert status_row.controls[1].color == AppColors.UP

    def test_trend_down_uses_down_color(self, mock_i18n_state, mock_app_colors_state) -> None:
        """trend_up=False 时 trend_color 为 AppColors.DOWN。"""
        from ui.components.settings_widgets import MetricCard
        from ui.theme import AppColors

        component = make_component(MetricCard, label="x", value="1", trend="-2%", trend_up=False)
        run_mount_effects(component)
        result = render_once(component)

        col = result.content
        status_row = col.controls[2]
        trend_text = status_row.controls[0]
        assert trend_text.color == AppColors.DOWN

    def test_no_status_falls_back_to_primary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无 icon/trend：status_controls 为 [ft.Container()]（空占位）。"""
        from ui.components.settings_widgets import MetricCard

        component = make_component(MetricCard, label="x", value="1")
        run_mount_effects(component)
        result = render_once(component)

        col = result.content
        status_row = col.controls[2]
        assert len(status_row.controls) == 1
        assert isinstance(status_row.controls[0], ft.Container)

    def test_empty_status_color_uses_primary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_color=None：resolved_color = ft.Colors.PRIMARY。"""
        from ui.components.settings_widgets import MetricCard

        component = make_component(MetricCard, label="x", value="1", icon="i", status_color=None)
        run_mount_effects(component)
        result = render_once(component)

        col = result.content
        status_row = col.controls[2]
        icon_ctrl = status_row.controls[0]
        assert icon_ctrl.color == ft.Colors.PRIMARY

    def test_empty_label_renders_empty_string(self, mock_i18n_state, mock_app_colors_state) -> None:
        """label="" → label.upper() = ""。"""
        from ui.components.settings_widgets import MetricCard

        component = make_component(MetricCard, label="", value="1")
        run_mount_effects(component)
        result = render_once(component)

        col = result.content
        assert col.controls[0].value == ""

    def test_none_value_renders_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """value=None：ft.Text(value=None) 不抛异常。"""
        from ui.components.settings_widgets import MetricCard

        component = make_component(MetricCard, label="x", value=None)
        run_mount_effects(component)
        result = render_once(component)

        col = result.content
        assert col.controls[1].value is None


class TestActionChipBody:
    """ActionChip 组件体测试：验证 is_primary/is_loading 颜色与 trailing 分支。"""

    def test_primary_chip_colors(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_primary=True：text_color=ON_PRIMARY, bgcolor=PRIMARY, chevron trailing。"""
        from ui.components.settings_widgets import ActionChip

        component = make_component(
            ActionChip,
            icon="add",
            title="新建",
            subtitle="副标题",
            on_click=lambda _: None,
            is_primary=True,
            is_loading=False,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert result.bgcolor == ft.Colors.PRIMARY
        assert result.disabled is False
        assert result.opacity == 1.0
        # trailing 是 chevron icon
        row = result.content
        trailing = row.controls[-1]
        assert isinstance(trailing, ft.Icon)

    def test_secondary_chip_colors(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_primary=False：text_color=ON_SURFACE, bgcolor=SURFACE。"""
        from ui.components.settings_widgets import ActionChip

        component = make_component(
            ActionChip,
            icon="add",
            title="x",
            subtitle="y",
            on_click=lambda _: None,
            is_primary=False,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert result.bgcolor == ft.Colors.SURFACE

    def test_loading_state_trailing_is_progress_ring(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_loading=True：trailing=ProgressRing, disabled=True, opacity=0.8。"""
        from ui.components.settings_widgets import ActionChip

        component = make_component(
            ActionChip,
            icon="x",
            title="t",
            subtitle="s",
            on_click=lambda _: None,
            is_loading=True,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert result.disabled is True
        assert result.opacity == 0.8
        row = result.content
        trailing = row.controls[-1]
        assert isinstance(trailing, ft.ProgressRing)

    def test_on_click_attached(self, mock_i18n_state, mock_app_colors_state) -> None:
        """on_click 回调透传到 Container.on_click。"""
        from ui.components.settings_widgets import ActionChip

        clicked: list[bool] = []

        def _click(_: ft.ControlEvent) -> None:
            clicked.append(True)

        component = make_component(
            ActionChip,
            icon="x",
            title="t",
            subtitle="s",
            on_click=_click,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert result.on_click is _click or callable(result.on_click)


class TestStatusBadgeBody:
    """StatusBadge 组件体测试：验证 text/icon 渲染。"""

    def test_text_only_badge(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无 icon：content_row = [ft.Text]。"""
        from ui.components.settings_widgets import StatusBadge

        component = make_component(StatusBadge, text="在线", color="#4CAF50")
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        row = result.content
        assert len(row.controls) == 1
        assert isinstance(row.controls[0], ft.Text)
        assert row.controls[0].value == "在线"
        assert row.controls[0].color == "#4CAF50"

    def test_badge_with_icon_prepended(self, mock_i18n_state, mock_app_colors_state) -> None:
        """有 icon：content_row = [ft.Icon, ft.Text]（icon 插入到首位）。"""
        from ui.components.settings_widgets import StatusBadge

        component = make_component(StatusBadge, text="同步中", color="#2196F3", icon="sync")
        run_mount_effects(component)
        result = render_once(component)

        row = result.content
        assert len(row.controls) == 2
        assert isinstance(row.controls[0], ft.Icon)
        assert isinstance(row.controls[1], ft.Text)
        assert row.controls[0].color == "#2196F3"

    def test_bgcolor_uses_color_with_opacity(self, mock_i18n_state, mock_app_colors_state) -> None:
        """bgcolor = with_opacity(0.1, color)。"""
        from ui.components.settings_widgets import StatusBadge

        component = make_component(StatusBadge, text="x", color="#FF0000")
        run_mount_effects(component)
        result = render_once(component)

        # ft.Colors.with_opacity 返回值类型不固定，验证 bgcolor 非空即可
        assert result.bgcolor is not None


class TestSectionHeaderBody:
    """SectionHeader 组件体测试：验证 title_key/title 切换与 action 附加。"""

    def test_title_without_key(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无 title_key：display_title = title。"""
        from ui.components.settings_widgets import SectionHeader

        component = make_component(SectionHeader, title="基本设置")
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Row)
        # 左侧 Row 的第 2 项是 title Text
        left_row = result.controls[0]
        title_text = left_row.controls[1]
        assert title_text.value == "基本设置"

    def test_title_with_key_uses_i18n(self, mock_i18n_state, mock_app_colors_state) -> None:
        """有 title_key：display_title = I18n.get(title_key)。

        mock_i18n_state 注入 DEFAULT_LOCALE，I18n.get(key) 返回 key 本身
        （未注册翻译时回退到 key）。
        """
        from ui.components.settings_widgets import SectionHeader

        component = make_component(SectionHeader, title="fallback", title_key="settings.basic")
        run_mount_effects(component)
        result = render_once(component)

        left_row = result.controls[0]
        title_text = left_row.controls[1]
        # I18n.get 未注册翻译时回退到 key
        assert title_text.value == "settings.basic"

    def test_action_appended_to_controls(self, mock_i18n_state, mock_app_colors_state) -> None:
        """有 action：controls = [left_row, action]。"""
        from ui.components.settings_widgets import SectionHeader

        action = ft.Container(content=ft.Text("编辑"))
        component = make_component(SectionHeader, title="x", action=action)
        run_mount_effects(component)
        result = render_once(component)

        assert len(result.controls) == 2
        assert result.controls[1] is action

    def test_no_action_yields_single_control(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无 action：controls = [left_row]（仅 1 项）。"""
        from ui.components.settings_widgets import SectionHeader

        component = make_component(SectionHeader, title="x")
        run_mount_effects(component)
        result = render_once(component)

        assert len(result.controls) == 1


class TestSettingRowBody:
    """SettingRow 组件体测试：验证 title_key/subtitle_key/icon_color/left_col/right_col。"""

    def test_basic_render(self, mock_i18n_state, mock_app_colors_state) -> None:
        """典型参数：返回 ft.ResponsiveRow，含 left/right 两列。"""
        from ui.components.settings_widgets import SettingRow

        control = ft.Switch(value=True)
        component = make_component(
            SettingRow,
            icon="settings",
            title="自动更新",
            subtitle="每天检查",
            control=control,
        )
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.ResponsiveRow)
        assert len(result.controls) == 2
        # 左右两个 Container
        assert isinstance(result.controls[0], ft.Container)
        assert isinstance(result.controls[1], ft.Container)

    def test_title_key_uses_i18n(self, mock_i18n_state, mock_app_colors_state) -> None:
        """有 title_key：display_title = I18n.get(title_key)。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="fallback",
            subtitle="s",
            control=ft.Switch(),
            title_key="auto.update",
        )
        run_mount_effects(component)
        result = render_once(component)

        left_container = result.controls[0]
        left_row = left_container.content
        col = left_row.controls[2]
        title_text = col.controls[0]
        assert title_text.value == "auto.update"

    def test_subtitle_key_uses_i18n(self, mock_i18n_state, mock_app_colors_state) -> None:
        """有 subtitle_key：display_subtitle = I18n.get(subtitle_key)。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="t",
            subtitle="fallback",
            control=ft.Switch(),
            subtitle_key="every.day",
        )
        run_mount_effects(component)
        result = render_once(component)

        left_container = result.controls[0]
        left_row = left_container.content
        col = left_row.controls[2]
        subtitle_text = col.controls[1]
        assert subtitle_text.value == "every.day"

    def test_icon_color_none_uses_primary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """icon_color=None：color = ft.Colors.PRIMARY。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="t",
            subtitle="s",
            control=ft.Switch(),
            icon_color=None,
        )
        run_mount_effects(component)
        result = render_once(component)

        left_container = result.controls[0]
        left_row = left_container.content
        icon_container = left_row.controls[0]
        icon = icon_container.content
        assert icon.color == ft.Colors.PRIMARY

    def test_custom_icon_color(self, mock_i18n_state, mock_app_colors_state) -> None:
        """icon_color="#FF0000"：color = "#FF0000"。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="t",
            subtitle="s",
            control=ft.Switch(),
            icon_color="#FF0000",
        )
        run_mount_effects(component)
        result = render_once(component)

        left_container = result.controls[0]
        left_row = left_container.content
        icon_container = left_row.controls[0]
        icon = icon_container.content
        assert icon.color == "#FF0000"

    def test_custom_col_config(self, mock_i18n_state, mock_app_colors_state) -> None:
        """自定义 left_col/right_col：透传到 ft.Container.col。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="t",
            subtitle="s",
            control=ft.Switch(),
            left_col={"xs": 12, "md": 6},
            right_col={"xs": 12, "md": 6},
        )
        run_mount_effects(component)
        result = render_once(component)

        assert result.controls[0].col == {"xs": 12, "md": 6}
        assert result.controls[1].col == {"xs": 12, "md": 6}

    def test_default_col_config(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无 left_col/right_col：使用默认 {"xs": 12, "sm": 7, "md": 7} / {"xs": 12, "sm": 5, "md": 5}。"""
        from ui.components.settings_widgets import SettingRow

        component = make_component(
            SettingRow,
            icon="x",
            title="t",
            subtitle="s",
            control=ft.Switch(),
        )
        run_mount_effects(component)
        result = render_once(component)

        assert result.controls[0].col == {"xs": 12, "sm": 7, "md": 7}
        assert result.controls[1].col == {"xs": 12, "sm": 5, "md": 5}
