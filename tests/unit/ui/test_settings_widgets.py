"""ui/components/settings_widgets.py 单元测试"""

import contextlib
import logging
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.conftest import set_page

pytestmark = pytest.mark.unit


# 公共 fixture：patch I18n / AppColors / AppStyles
@pytest.fixture
def _patch_deps(mock_i18n, mock_app_colors, mock_app_styles):
    # 覆盖 card 返回值以匹配 DashboardCard 期望的 keys（含 border_radius/bgcolor）
    mock_app_styles.card.return_value = {
        "bgcolor": ft.Colors.SURFACE,
        "border_radius": 4,
        "padding": 15,
        "border": ft.Border.all(1, ft.Colors.OUTLINE),
    }
    patches = [
        patch("ui.components.settings_widgets.I18n", mock_i18n),
        patch("ui.components.settings_widgets.AppColors", mock_app_colors),
        patch("ui.components.settings_widgets.AppStyles", mock_app_styles),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


# ---------------------------------------------------------------------------
# DashboardCard
# ---------------------------------------------------------------------------
class TestDashboardCard:
    def test_init_basic(self, _patch_deps):
        from ui.components.settings_widgets import DashboardCard

        content = ft.Text("hello")
        card = DashboardCard(content=content)
        assert card.content is content
        assert card.padding == 20
        assert card.expand is False

    def test_init_custom_padding_expand(self, _patch_deps):
        from ui.components.settings_widgets import DashboardCard

        card = DashboardCard(content=ft.Text("x"), padding=10, expand=True)
        assert card.padding == 10
        assert card.expand is True

    def test_init_uses_app_styles_card(self, _patch_deps, mock_app_styles):
        from ui.components.settings_widgets import DashboardCard

        DashboardCard(content=ft.Text("x"))
        mock_app_styles.card.assert_called_once()


# ---------------------------------------------------------------------------
# MetricCard
# ---------------------------------------------------------------------------
class TestMetricCard:
    def test_init_defaults(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="市值", value="100亿")
        assert card.label_text == "市值"
        assert card.value_text == "100亿"
        assert card.icon_name is None
        assert card.status_color_val is None
        assert card.trend_text is None
        assert card.trend_up_val is True
        assert card.expand is True

    def test_init_with_icon_and_trend_up(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(
            label="涨幅",
            value="5%",
            icon=ft.Icons.TRENDING_UP,
            status_color="red",
            trend="+5%",
            trend_up=True,
        )
        assert card.icon_name == ft.Icons.TRENDING_UP
        assert card.status_color_val == "red"
        assert card.trend_text == "+5%"
        assert card.trend_up_val is True
        # status_row 应有 2 个控件：icon + trend text
        assert len(card.status_row_view.controls) == 2

    def test_init_with_trend_down(self, _patch_deps, mock_app_colors):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="跌幅", value="-3%", trend="-3%", trend_up=False)
        assert card.trend_up_val is False
        # 仅 trend text，无 icon
        assert len(card.status_row_view.controls) == 1

    def test_init_no_icon_no_trend_uses_empty_container(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="L", value="V")
        # 无 icon/trend 时使用占位 Container
        assert len(card.status_row_view.controls) == 1
        assert isinstance(card.status_row_view.controls[0], ft.Container)

    def test_set_value_updates_value_text(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="L", value="old")
        set_page(card, mock_page)
        card.update = MagicMock()
        card.set_value("new", icon=ft.Icons.CHECK, status_color="green")
        assert card.value_text == "new"
        assert card.value_view.value == "new"
        assert card.icon_name == ft.Icons.CHECK
        assert card.status_color_val == "green"
        card.update.assert_called_once()

    def test_set_value_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="L", value="old")
        # page 未设置，不应抛出
        card.set_value("new")
        assert card.value_text == "new"

    def test_set_label_updates_label_text(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="old", value="v")
        set_page(card, mock_page)
        card.update = MagicMock()
        card.set_label("NEW")
        assert card.label_text == "NEW"
        assert card.label_view.value == "NEW"
        card.update.assert_called_once()

    def test_set_label_empty_string(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="old", value="v")
        card.set_label("")
        assert card.label_text == ""
        assert card.label_view.value == ""

    def test_set_label_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="old", value="v")
        # page 未设置，不应抛出
        card.set_label("NEW")
        assert card.label_text == "NEW"

    def test_update_theme_rebuilds_status_row(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="L", value="V", trend="+1%", trend_up=True)
        set_page(card, mock_page)
        card.update = MagicMock()
        card.update_theme()
        # 重建后内容刷新，update 被调用
        card.update.assert_called_once()

    def test_update_theme_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import MetricCard

        card = MetricCard(label="L", value="V")
        # page 未设置，不应抛出
        card.update_theme()


# ---------------------------------------------------------------------------
# ActionChip
# ---------------------------------------------------------------------------
class TestActionChip:
    def test_init_primary(self, _patch_deps):
        from ui.components.settings_widgets import ActionChip

        on_click = MagicMock()
        chip = ActionChip(
            icon=ft.Icons.ADD,
            title="新增",
            subtitle="创建新项",
            on_click=on_click,
            is_primary=True,
        )
        assert chip.icon_name == ft.Icons.ADD
        assert chip.title_text == "新增"
        assert chip.subtitle_text == "创建新项"
        assert chip.is_primary is True
        assert chip.on_click is on_click
        # primary 配色
        assert chip.bgcolor == ft.Colors.PRIMARY

    def test_init_non_primary(self, _patch_deps):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(
            icon=ft.Icons.EDIT,
            title="编辑",
            subtitle="修改",
            on_click=MagicMock(),
            is_primary=False,
        )
        assert chip.is_primary is False
        assert chip.bgcolor == ft.Colors.SURFACE

    def test_set_loading_true_replaces_last_control(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="t", subtitle="s", on_click=MagicMock(), is_primary=True)
        set_page(chip, mock_page)
        chip.update = MagicMock()
        chip.set_loading(True)
        assert chip.disabled is True
        assert chip.opacity == 0.8
        assert isinstance(chip.content.controls[-1], ft.ProgressRing)
        chip.update.assert_called_once()

    def test_set_loading_false_restores_icon(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="t", subtitle="s", on_click=MagicMock(), is_primary=False)
        set_page(chip, mock_page)
        chip.update = MagicMock()
        # 先 loading 再恢复
        chip.set_loading(True)
        chip.set_loading(False)
        assert chip.disabled is False
        assert chip.opacity == 1.0
        assert isinstance(chip.content.controls[-1], ft.Icon)

    def test_set_loading_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="t", subtitle="s", on_click=MagicMock(), is_primary=True)
        # page 未设置，不应抛出
        chip.set_loading(True)
        assert chip.disabled is True

    def test_set_loading_update_exception_swallowed(self, _patch_deps, mock_page, caplog):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="t", subtitle="s", on_click=MagicMock(), is_primary=True)
        set_page(chip, mock_page)
        chip.update = MagicMock(side_effect=RuntimeError("ui boom"))
        with caplog.at_level(logging.DEBUG, logger="ui.components.settings_widgets"):
            # 不应抛出异常
            chip.set_loading(True)
        assert any("UI update skipped" in r.message and "ui boom" in r.message for r in caplog.records)

    def test_set_text_updates_title_only(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="old", subtitle="sub", on_click=MagicMock(), is_primary=True)
        set_page(chip, mock_page)
        chip.update = MagicMock()
        chip.set_text("new_title")
        assert chip.title_text == "new_title"
        assert chip.subtitle_text == "sub"  # 未变
        chip.update.assert_called_once()

    def test_set_text_updates_title_and_subtitle(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="old", subtitle="old_sub", on_click=MagicMock(), is_primary=False)
        set_page(chip, mock_page)
        chip.update = MagicMock()
        chip.set_text("new_title", "new_sub")
        assert chip.title_text == "new_title"
        assert chip.subtitle_text == "new_sub"

    def test_set_text_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="old", subtitle="sub", on_click=MagicMock(), is_primary=True)
        # page 未设置，不应抛出
        chip.set_text("new")
        assert chip.title_text == "new"

    def test_set_text_update_exception_swallowed(self, _patch_deps, mock_page, caplog):
        from ui.components.settings_widgets import ActionChip

        chip = ActionChip(icon=ft.Icons.ADD, title="old", subtitle="sub", on_click=MagicMock(), is_primary=True)
        set_page(chip, mock_page)
        chip.update = MagicMock(side_effect=RuntimeError("ui boom"))
        with caplog.at_level(logging.DEBUG, logger="ui.components.settings_widgets"):
            chip.set_text("new")
        assert any("UI update skipped" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# StatusBadge
# ---------------------------------------------------------------------------
class TestStatusBadge:
    def test_init_without_icon(self, _patch_deps):
        from ui.components.settings_widgets import StatusBadge

        badge = StatusBadge(text="在线", color="green")
        assert badge.badge_text == "在线"
        assert badge.badge_color == "green"
        assert badge.badge_icon is None
        # 仅文本控件
        assert len(badge.content.controls) == 1
        assert isinstance(badge.content.controls[0], ft.Text)

    def test_init_with_icon(self, _patch_deps):
        from ui.components.settings_widgets import StatusBadge

        badge = StatusBadge(text="同步中", color="blue", icon=ft.Icons.SYNC)
        assert badge.badge_icon == ft.Icons.SYNC
        # icon + text
        assert len(badge.content.controls) == 2
        assert isinstance(badge.content.controls[0], ft.Icon)

    def test_set_text_rebuilds_content(self, _patch_deps, mock_page):
        from ui.components.settings_widgets import StatusBadge

        badge = StatusBadge(text="old", color="green", icon=ft.Icons.CHECK)
        set_page(badge, mock_page)
        badge.update = MagicMock()
        badge.set_text("new")
        assert badge.badge_text == "new"
        # 重建后内容应包含新文本
        text_controls = [c for c in badge.content.controls if isinstance(c, ft.Text)]
        assert text_controls[0].value == "new"
        badge.update.assert_called_once()

    def test_set_text_without_page_no_update(self, _patch_deps):
        from ui.components.settings_widgets import StatusBadge

        badge = StatusBadge(text="old", color="green")
        # page 未设置，不应抛出
        badge.set_text("new")
        assert badge.badge_text == "new"

    def test_set_text_update_exception_swallowed(self, _patch_deps, mock_page, caplog):
        from ui.components.settings_widgets import StatusBadge

        badge = StatusBadge(text="old", color="green")
        set_page(badge, mock_page)
        badge.update = MagicMock(side_effect=RuntimeError("ui boom"))
        with caplog.at_level(logging.DEBUG, logger="ui.components.settings_widgets"):
            badge.set_text("new")
        assert any("UI update skipped" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SectionHeader
# ---------------------------------------------------------------------------
class TestSectionHeader:
    def test_init_without_action(self, _patch_deps):
        from ui.components.settings_widgets import SectionHeader

        header = SectionHeader(title="基本设置")
        assert header.title_view.value == "基本设置"
        assert header.title_key is None
        # 仅左侧 row（无 action）
        assert len(header.controls) == 1

    def test_init_with_action(self, _patch_deps):
        from ui.components.settings_widgets import SectionHeader

        action = ft.TextButton("点击")
        header = SectionHeader(title="T", action=action)
        assert len(header.controls) == 2
        assert header.controls[1] is action

    def test_init_with_title_key(self, _patch_deps):
        from ui.components.settings_widgets import SectionHeader

        header = SectionHeader(title="T", title_key="section_basic")
        assert header.title_key == "section_basic"

    def test_update_locale_no_key_no_change(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SectionHeader

        header = SectionHeader(title="static")
        header.update_locale()
        # title_key 为 None，不应调用 I18n.get
        mock_i18n.get.assert_not_called()

    def test_update_locale_with_key_updates_title(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SectionHeader

        header = SectionHeader(title="static", title_key="section_basic")
        header.update_locale()
        mock_i18n.get.assert_called_once_with("section_basic")
        # title_view.value 应被替换为 I18n.get 返回值（mock 返回 key 本身）
        assert header.title_view.value == "section_basic"


# ---------------------------------------------------------------------------
# SettingRow
# ---------------------------------------------------------------------------
class TestSettingRow:
    def test_init_basic(self, _patch_deps):
        from ui.components.settings_widgets import SettingRow

        control = ft.Switch()
        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="启用通知",
            subtitle="开启后接收推送",
            control=control,
        )
        assert row.title_view.value == "启用通知"
        assert row.subtitle_view.value == "开启后接收推送"
        assert row.title_key is None
        assert row.subtitle_key is None
        # 默认 icon color
        assert row.icon_view.color == ft.Colors.PRIMARY
        # 两个子 Container
        assert len(row.controls) == 2

    def test_init_with_custom_icon_color(self, _patch_deps):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.WARNING,
            title="T",
            subtitle="S",
            control=ft.Switch(),
            icon_color="orange",
        )
        assert row.icon_view.color == "orange"

    def test_init_with_keys(self, _patch_deps):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
            title_key="title.k",
            subtitle_key="sub.k",
        )
        assert row.title_key == "title.k"
        assert row.subtitle_key == "sub.k"

    def test_init_with_custom_col_dicts(self, _patch_deps):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
            left_col={"xs": 12, "md": 6},
            right_col={"xs": 12, "md": 6},
        )
        # 验证传入的 col 被使用（不抛异常即可）
        left_container = row.controls[0]
        right_container = row.controls[1]
        assert left_container.col == {"xs": 12, "md": 6}
        assert right_container.col == {"xs": 12, "md": 6}

    def test_init_default_col_dicts(self, _patch_deps):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
        )
        left_container = row.controls[0]
        right_container = row.controls[1]
        assert left_container.col == {"xs": 12, "sm": 7, "md": 7}
        assert right_container.col == {"xs": 12, "sm": 5, "md": 5}

    def test_update_locale_no_keys_no_change(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(icon=ft.Icons.SETTINGS, title="t", subtitle="s", control=ft.Switch())
        row.update_locale()
        mock_i18n.get.assert_not_called()

    def test_update_locale_with_title_key_only(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
            title_key="t.k",
        )
        row.update_locale()
        mock_i18n.get.assert_called_once_with("t.k")
        assert row.title_view.value == "t.k"

    def test_update_locale_with_subtitle_key_only(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
            subtitle_key="s.k",
        )
        row.update_locale()
        mock_i18n.get.assert_called_once_with("s.k")
        assert row.subtitle_view.value == "s.k"

    def test_update_locale_with_both_keys(self, _patch_deps, mock_i18n):
        from ui.components.settings_widgets import SettingRow

        row = SettingRow(
            icon=ft.Icons.SETTINGS,
            title="t",
            subtitle="s",
            control=ft.Switch(),
            title_key="t.k",
            subtitle_key="s.k",
        )
        row.update_locale()
        assert mock_i18n.get.call_count == 2
        assert row.title_view.value == "t.k"
        assert row.subtitle_view.value == "s.k"
