"""ui/components/market_dashboard.py 声明式契约守护测试 (Phase B.1).

业务逻辑由消费方 ViewModel 单元测试覆盖。View 层测试聚焦于契约守护
（grep 检查禁止的命令式模式），参照 test_settings_widgets.py 模式。

Phase 1.2 扩展：追加纯函数测试 + 组件体测试，覆盖 _resolve_color /
_build_index_card / _build_hsgt_card / _build_concept_card 分支逻辑
及 MarketDashboard 组件体渲染。
"""

import ast
from pathlib import Path

import flet as ft
import pytest

from ui.viewmodels.home_view_model import HotConceptRow, HsgtRow, MarketIndexRow

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
    ) -> None:
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
    import ui.components.market_dashboard as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.market_dashboard as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


class TestMarketDashboardContract:
    """MarketDashboard 声明式组件契约守护测试。"""

    def test_component_is_ft_component(self):
        """DoD: MarketDashboard 必须被 @ft.component 装饰。"""
        from ui.components.market_dashboard import MarketDashboard

        assert hasattr(MarketDashboard, "__wrapped__"), "MarketDashboard 必须用 @ft.component 装饰"

    def test_no_class_inheritance(self):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert "class MarketDashboard(" not in _code_source()

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_update_data(self):
        """DoD: 禁止命令式数据推送 API（改用 data prop 推送）。"""
        assert "update_data" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式主题刷新（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_no_update_locale(self):
        """DoD: 禁止命令式语言刷新（声明式通过 Observable state 自动重渲染）。"""
        assert "update_locale" not in _code_source()

    def test_no_last_data_cache(self):
        """DoD: 禁止数据缓存（声明式 state 驱动渲染）。"""
        assert "_last_data" not in _code_source()

    def test_no_concept_skeleton_pool(self):
        """DoD: 禁止概念卡回收池（skeleton + update 模式，改 state 驱动渲染）。"""
        assert "_build_concept_card_skeleton" not in _code_source()
        assert "_update_concept_card" not in _code_source()

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin/_page_ref。"""
        assert "PageRefMixin" not in _code_source()
        assert "_page_ref" not in _code_source()

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state（i18n 自动重渲染）。"""
        assert "get_observable_state" in _raw_source()

    def test_subscribes_app_colors(self):
        """DoD: 必须订阅 AppColors.get_observable_state（theme 自动重渲染）。"""
        assert "AppColors.get_observable_state" in _raw_source()


# ============================================================================
# 纯函数测试 — _resolve_color / _build_index_card / _build_hsgt_card / _build_concept_card
# ============================================================================


class TestResolveColor:
    """_resolve_color 纯函数测试：验证 RED/GREEN/其他 颜色映射。"""

    def test_red_maps_to_up(self, mock_i18n_state, mock_app_colors_state) -> None:
        """'RED' → AppColors.UP（A股红涨）。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color("RED") == AppColors.UP

    def test_green_maps_to_down(self, mock_i18n_state, mock_app_colors_state) -> None:
        """'GREEN' → AppColors.DOWN（A股绿跌）。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color("GREEN") == AppColors.DOWN

    def test_none_returns_text_secondary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """None → AppColors.TEXT_SECONDARY（中性灰）。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color(None) == AppColors.TEXT_SECONDARY

    def test_empty_string_returns_text_secondary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """'' → AppColors.TEXT_SECONDARY。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color("") == AppColors.TEXT_SECONDARY

    def test_other_color_returns_text_secondary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """未识别的颜色名 → AppColors.TEXT_SECONDARY。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color("BLUE") == AppColors.TEXT_SECONDARY

    def test_case_insensitive(self, mock_i18n_state, mock_app_colors_state) -> None:
        """大小写不敏感：'red' 与 'RED' 等价。"""
        from ui.components.market_dashboard import _resolve_color
        from ui.theme import AppColors

        assert _resolve_color("red") == AppColors.UP
        assert _resolve_color("green") == AppColors.DOWN


class TestBuildIndexCard:
    """_build_index_card 纯函数测试：验证指数卡片渲染。"""

    def test_full_info_renders_value_and_change(self, mock_i18n_state, mock_app_colors_state) -> None:
        """完整 info：value/change 显示，color 解析为 AppColors token。"""
        from ui.components.market_dashboard import _build_index_card
        from ui.i18n import I18n
        from ui.theme import AppColors

        info = MarketIndexRow(value="3000.50", change="+1.2%", color="RED")
        card = _build_index_card("home_index_sh", info)

        assert isinstance(card, ft.Container)
        col = card.content
        # 第 1 项：title = I18n.get(title_key)（不依赖具体翻译值）
        assert col.controls[0].value == I18n.get("home_index_sh")
        # 第 2 项：value
        assert col.controls[1].value == "3000.50"
        # 第 3 项：change
        assert col.controls[2].value == "+1.2%"
        # color 解析为 UP
        assert col.controls[2].color == AppColors.UP

    def test_empty_info_falls_back_to_dash(self, mock_i18n_state, mock_app_colors_state) -> None:
        """空 info：value/change 显示 '--'。"""
        from ui.components.market_dashboard import _build_index_card

        card = _build_index_card("home_index_sh", MarketIndexRow())

        col = card.content
        assert col.controls[1].value == "--"
        assert col.controls[2].value == "--"

    def test_missing_color_uses_text_secondary(self, mock_i18n_state, mock_app_colors_state) -> None:
        """info 无 color 字段 → _resolve_color(None) = TEXT_SECONDARY。"""
        from ui.components.market_dashboard import _build_index_card
        from ui.theme import AppColors

        card = _build_index_card("home_index_sh", MarketIndexRow(value="1", change="0%"))

        col = card.content
        assert col.controls[2].color == AppColors.TEXT_SECONDARY

    def test_col_config_is_4_per_row(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卡片 col 配置：xs/sm=6, md/lg=3（4 列布局）。"""
        from ui.components.market_dashboard import _build_index_card

        card = _build_index_card("home_index_sh", MarketIndexRow())

        assert card.col == {"xs": 6, "sm": 6, "md": 3, "lg": 3}


class TestBuildHsgtCard:
    """_build_hsgt_card 纯函数测试：验证北向资金卡片渲染。"""

    def test_full_info_renders_value_and_sub(self, mock_i18n_state, mock_app_colors_state) -> None:
        """完整 info：value/sub 显示，color 解析。"""
        from ui.components.market_dashboard import _build_hsgt_card
        from ui.i18n import I18n
        from ui.theme import AppColors

        info = HsgtRow(value="100亿", sub="净流入", color="RED")
        card = _build_hsgt_card(info)

        assert isinstance(card, ft.Container)
        col = card.content
        # 第 1 项：title = I18n.get("home_northbound")
        assert col.controls[0].value == I18n.get("home_northbound")
        # 第 2 项：value
        assert col.controls[1].value == "100亿"
        # 第 3 项：sub
        assert col.controls[2].value == "净流入"
        # value 的 color 解析为 UP
        assert col.controls[1].color == AppColors.UP

    def test_empty_info_falls_back_to_dash(self, mock_i18n_state, mock_app_colors_state) -> None:
        """空 info：value/sub 显示 '--'。"""
        from ui.components.market_dashboard import _build_hsgt_card

        card = _build_hsgt_card(HsgtRow())

        col = card.content
        assert col.controls[1].value == "--"
        assert col.controls[2].value == "--"


class TestBuildConceptCard:
    """_build_concept_card 纯函数测试：验证热门概念卡片渲染。"""

    def test_red_color_uses_up_and_trending_up_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """color 含 'red' → is_up=True, color=UP, icon=TRENDING_UP。"""
        from ui.components.market_dashboard import _build_concept_card
        from ui.theme import AppColors

        item = HotConceptRow(name="AI", change="+3.5%", color="red")
        card = _build_concept_card(item)

        assert isinstance(card, ft.Container)
        col = card.content
        # 第 1 项：name
        assert col.controls[0].value == "AI"
        # 第 2 项：Row(Icon, Text)
        row = col.controls[1]
        icon = row.controls[0]
        text = row.controls[1]
        assert icon.icon == ft.Icons.TRENDING_UP
        assert icon.color == AppColors.UP
        assert text.value == "+3.5%"
        assert text.color == AppColors.UP

    def test_non_red_color_uses_down_and_trending_down_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """color 不含 'red' → is_up=False, color=DOWN, icon=TRENDING_DOWN。"""
        from ui.components.market_dashboard import _build_concept_card
        from ui.theme import AppColors

        item = HotConceptRow(name="新能源", change="-2.1%", color="green")
        card = _build_concept_card(item)

        col = card.content
        row = col.controls[1]
        icon = row.controls[0]
        assert icon.icon == ft.Icons.TRENDING_DOWN
        assert icon.color == AppColors.DOWN

    def test_missing_color_defaults_to_empty(self, mock_i18n_state, mock_app_colors_state) -> None:
        """item 无 color → color_str='' → is_up=False → DOWN。"""
        from ui.components.market_dashboard import _build_concept_card
        from ui.theme import AppColors

        item = HotConceptRow(name="x", change="0%")
        card = _build_concept_card(item)

        col = card.content
        row = col.controls[1]
        assert row.controls[0].color == AppColors.DOWN

    def test_missing_name_falls_back_to_dash(self, mock_i18n_state, mock_app_colors_state) -> None:
        """item 无 name → 显示 '--'。"""
        from ui.components.market_dashboard import _build_concept_card

        card = _build_concept_card(HotConceptRow(change="0%"))

        col = card.content
        assert col.controls[0].value == "--"

    def test_missing_change_falls_back_to_default(self, mock_i18n_state, mock_app_colors_state) -> None:
        """item 无 change → 显示 '0.00%'。"""
        from ui.components.market_dashboard import _build_concept_card

        card = _build_concept_card(HotConceptRow(name="x"))

        col = card.content
        row = col.controls[1]
        assert row.controls[1].value == "0.00%"

    def test_col_config_is_6_per_row_on_mobile(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卡片 col 配置：xs=6, sm=4, md=3, lg=2。"""
        from ui.components.market_dashboard import _build_concept_card

        card = _build_concept_card(HotConceptRow(name="x"))

        assert card.col == {"xs": 6, "sm": 4, "md": 3, "lg": 2}


# ============================================================================
# 组件体测试 — 用 attach_fake_page 驱动 MarketDashboard 渲染
# ============================================================================


from tests.unit.ui.component_renderer import (  # noqa: E402
    make_component,
    render_once,
    run_mount_effects,
)


class TestMarketDashboardBody:
    """MarketDashboard 组件体测试：验证 data 解析与布局。"""

    def test_none_data_renders_empty_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """无参数 → indices/hsgt 为空，hot_concepts 显示 empty 提示。"""
        from ui.components.market_dashboard import MarketDashboard

        component = make_component(MarketDashboard)
        run_mount_effects(component)
        result = render_once(component)

        assert isinstance(result, ft.Column)
        # 第 1 项：indices_row（4 张空卡片）
        indices_row = result.controls[0]
        assert isinstance(indices_row, ft.ResponsiveRow)
        assert len(indices_row.controls) == 4
        # 第 3 项：concepts_section（含 empty 提示）
        concepts_section = result.controls[2]
        concepts_row = concepts_section.controls[1]
        assert len(concepts_row.controls) == 1  # empty 提示

    def test_full_data_renders_indices_and_concepts(self, mock_i18n_state, mock_app_colors_state) -> None:
        """完整 data：3 indices + hsgt + 多个 hot_concepts。"""
        from ui.components.market_dashboard import MarketDashboard

        indices = (
            MarketIndexRow(value="3000", change="+1%", color="RED"),
            MarketIndexRow(value="10000", change="-0.5%", color="GREEN"),
            MarketIndexRow(value="2000", change="+0.3%", color="RED"),
        )
        hsgt = HsgtRow(value="50亿", sub="净流入", color="RED")
        hot_concepts = (
            HotConceptRow(name="AI", change="+3%", color="red"),
            HotConceptRow(name="新能源", change="-1%", color="green"),
        )
        component = make_component(MarketDashboard, indices=indices, hsgt=hsgt, hot_concepts=hot_concepts)
        run_mount_effects(component)
        result = render_once(component)

        indices_row = result.controls[0]
        assert len(indices_row.controls) == 4  # 3 indices + 1 hsgt
        concepts_section = result.controls[2]
        concepts_row = concepts_section.controls[1]
        assert len(concepts_row.controls) == 2  # 2 concept cards

    def test_empty_hot_concepts_shows_empty_hint(self, mock_i18n_state, mock_app_colors_state) -> None:
        """hot_concepts=() → 显示 empty 提示卡片。"""
        from ui.components.market_dashboard import MarketDashboard
        from ui.i18n import I18n

        component = make_component(MarketDashboard, hot_concepts=())
        run_mount_effects(component)
        result = render_once(component)

        concepts_section = result.controls[2]
        concepts_row = concepts_section.controls[1]
        assert len(concepts_row.controls) == 1
        # empty 提示的 Text = I18n.get("home_hot_concepts_empty")
        empty_card = concepts_row.controls[0]
        empty_text = empty_card.content
        assert empty_text.value == I18n.get("home_hot_concepts_empty")

    def test_partial_indices_fills_empty_cards(self, mock_i18n_state, mock_app_colors_state) -> None:
        """indices 不足 3 个：缺失部分用 MarketIndexRow() 填充（仍渲染 4 张卡片）。"""
        from ui.components.market_dashboard import MarketDashboard

        indices = (MarketIndexRow(value="3000", change="+1%", color="RED"),)
        component = make_component(MarketDashboard, indices=indices)
        run_mount_effects(component)
        result = render_once(component)

        indices_row = result.controls[0]
        assert len(indices_row.controls) == 4

    def test_none_hsgt_renders_dash(self, mock_i18n_state, mock_app_colors_state) -> None:
        """hsgt=None → HsgtRow() 默认值，渲染 '--'。"""
        from ui.components.market_dashboard import MarketDashboard

        component = make_component(MarketDashboard, hsgt=None)
        run_mount_effects(component)
        result = render_once(component)

        indices_row = result.controls[0]
        # hsgt 卡片渲染默认值 '--'
        hsgt_card = indices_row.controls[3]
        col = hsgt_card.content
        assert col.controls[1].value == "--"

    def test_empty_indices_renders_4_cards(self, mock_i18n_state, mock_app_colors_state) -> None:
        """indices=() → 3 张空 index 卡片 + 1 张 hsgt 卡片（共 4 张）。"""
        from ui.components.market_dashboard import MarketDashboard

        component = make_component(MarketDashboard, indices=())
        run_mount_effects(component)
        result = render_once(component)

        indices_row = result.controls[0]
        assert len(indices_row.controls) == 4
