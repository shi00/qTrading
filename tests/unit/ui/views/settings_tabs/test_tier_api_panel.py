"""Phase 2A.1 Task 2A.1.13：TierApiPanel UI 交互测试。

测试覆盖：
- handle_resize 响应式断点（lg=300/md=280/sm=240）
- _on_locale_change 重建 options + API 列表文本（i18n 9 条规范）
- dispose 取消 I18n 订阅（生命周期兜底）
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page, wrap_mock_page
from ui.viewmodels.system_viewmodel import SystemViewModel
from ui.views.settings_tabs.tier_api_panel import (
    _TIER_PANEL_LG_BREAKPOINT,
    _TIER_PANEL_MD_BREAKPOINT,
    TierApiPanel,
)

pytestmark = pytest.mark.unit


def _make_mock_client():
    """创建 mock TushareClient，覆盖 _build_api_list_controls 所需方法。"""
    client = MagicMock()
    client.get_tier_apis.return_value = {"daily", "fina_indicator"}
    client.is_independent_purchase.return_value = False
    client.get_capability_cache.return_value = {"daily": True, "fina_indicator": False}
    client._last_probe_time = None
    return client


class TestTierApiPanel:
    """TierApiPanel 响应式 + i18n + dispose 测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.mock_client = _make_mock_client()
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.tier_api_panel.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.tier_api_panel.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.tier_api_panel.SystemViewModel", spec=SystemViewModel),
            patch("data.external.tushare_client.TushareClient", return_value=self.mock_client),
            patch("flet.core.control.Control.update"),  # no-op: Flet update() requires page binding
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_panel(self, mock_page):
        vm = SystemViewModel()
        panel = TierApiPanel(vm)
        set_page(panel, wrap_mock_page(mock_page))
        return panel

    def test_tier_api_panel_handle_resize(self, mock_page):
        """handle_resize 按断点调整 API 列表高度（lg=300/md=280/sm=240）。"""
        panel = self._make_panel(mock_page)

        # lg 断点（>=1200）→ 300
        panel.handle_resize(_TIER_PANEL_LG_BREAKPOINT, 800)
        assert panel.api_list_view.height == 300

        # md 断点（800-1199）→ 280
        panel.handle_resize(_TIER_PANEL_MD_BREAKPOINT, 600)
        assert panel.api_list_view.height == 280

        # sm 断点（<800）→ 240
        panel.handle_resize(_TIER_PANEL_MD_BREAKPOINT - 1, 400)
        assert panel.api_list_view.height == 240

    def test_tier_api_panel_locale_change(self, mock_page):
        """_on_locale_change 重建 options + API 列表文本（i18n 规范 4：options 重建）。"""
        panel = self._make_panel(mock_page)

        # 修改 i18n 返回值模拟 locale 切换
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"

        # _on_locale_change 不应抛异常
        panel._on_locale_change()

        # 验证档位下拉框 options 已重建（text 应为翻译后的文本）
        options = panel.tier_dropdown.options
        assert options is not None
        assert len(options) == 5
        # 第一个 option 的 text 应包含 "translated_" 前缀（i18n.get 返回值）
        # Flet 0.28.3：ft.dropdown.Option(key, text=...) 第二参数为 text
        first_option_text = options[0].text if options[0].text else str(options[0].key)
        assert "translated_" in first_option_text

        # 验证关键文案已更新
        assert panel.points_hint_text.value == "translated_sys_tier_points_hint"
        assert panel.probe_button.text == "translated_sys_tier_probe_button"
        assert panel.panel_title.value == "translated_sys_tier_panel_title"

    def test_tier_api_panel_dispose_unsubscribes(self, mock_page):
        """dispose 取消 I18n 订阅（生命周期兜底，i18n 规范 7）。"""
        panel = self._make_panel(mock_page)

        # __init__ 时已订阅，_locale_sub_id 应非 None
        assert panel._locale_sub_id is not None
        original_sub_id = panel._locale_sub_id

        panel.dispose()

        # 验证 I18n.unsubscribe 被调用
        self.mock_i18n.unsubscribe.assert_called_once_with(original_sub_id)
        # _locale_sub_id 被置 None（避免重复 dispose）
        assert panel._locale_sub_id is None

        # 重复 dispose 容错：不应抛异常
        panel.dispose()
        # unsubscribe 调用次数不应增加（_locale_sub_id 已 None，跳过 unsubscribe）
        assert self.mock_i18n.unsubscribe.call_count == 1
