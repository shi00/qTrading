"""Phase 2A.1 Task 2A.1.13：TierApiPanel UI 交互测试。

测试覆盖：
- handle_resize 响应式断点（lg=300/md=280/sm=240）
- _on_locale_change 重建 options + API 列表文本（i18n 9 条规范）+ 异常降级
- dispose 取消 I18n 订阅（生命周期兜底）+ 异常容错
- _render_probe_status 三态渲染（True/False/None + 独立付费子分支）
- _build_api_description 三分支（独立付费/积分不足/默认空）
- _format_last_probe_text / _format_stale_hint_text 格式化
- _build_api_list_controls 独立付费标记附加
- _on_probe_result 4 类型分派 + 未知类型 + 异常降级
- _refresh_api_list 正常 + 异常路径
"""

import contextlib
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.conftest import wrap_mock_page
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
    """TierApiPanel 响应式 + i18n + dispose + 三态渲染 + probe 分派 + 缓存刷新测试。"""

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
            patch("flet.controls.control.Control.update"),  # no-op: Flet update() requires page binding
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_panel(self, mock_page):
        vm = SystemViewModel()
        panel = TierApiPanel(vm)
        panel.page = wrap_mock_page(mock_page)
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
        # Flet 0.85.3：ft.dropdown.Option(key, text=...) 第二参数为 text
        first_option_text = options[0].text if options[0].text else str(options[0].key)
        assert "translated_" in first_option_text

        # 验证关键文案已更新
        assert panel.points_hint_text.value == "translated_sys_tier_points_hint"
        assert panel.probe_button.content == "translated_sys_tier_probe_button"
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

    # ------------------------------------------------------------------
    # _render_probe_status 三态渲染（lines 241-267）
    # ------------------------------------------------------------------

    def test_render_probe_status_available_true(self, mock_page):
        """available=True → SUCCESS 图标 + 'sys_tier_available' 文案（lines 241-245）。"""
        panel = self._make_panel(mock_page)
        icon, text, color = panel._render_probe_status("daily", True)
        assert color == self.mock_ac.SUCCESS
        assert text.value == "sys_tier_available"

    def test_render_probe_status_available_false_independent_purchase(self, mock_page):
        """available=False + 独立付费 → WARNING 图标 + 'sys_tier_independent_purchase'（lines 248-256）。"""
        self.mock_client.is_independent_purchase.return_value = True
        panel = self._make_panel(mock_page)
        icon, text, color = panel._render_probe_status("cyq_perf", False)
        assert color == self.mock_ac.WARNING
        assert text.value == "sys_tier_independent_purchase"

    def test_render_probe_status_available_false_insufficient_points(self, mock_page):
        """available=False + 非独立付费 → ERROR 图标 + 'sys_tier_unavailable'（lines 257-261）。"""
        panel = self._make_panel(mock_page)
        icon, text, color = panel._render_probe_status("daily", False)
        assert color == self.mock_ac.ERROR
        assert text.value == "sys_tier_unavailable"

    def test_render_probe_status_available_none(self, mock_page):
        """available=None → HELP_OUTLINE + 'sys_tier_not_probed'（lines 263-267）。"""
        panel = self._make_panel(mock_page)
        icon, text, color = panel._render_probe_status("daily", None)
        assert text.value == "sys_tier_not_probed"

    # ------------------------------------------------------------------
    # _build_api_description 三分支（lines 275-288）
    # ------------------------------------------------------------------

    def test_build_api_description_independent_purchase(self, mock_page):
        """独立付费 API → WARNING italic 文案（line 275）。"""
        self.mock_client.is_independent_purchase.return_value = True
        panel = self._make_panel(mock_page)
        desc = panel._build_api_description("cyq_perf", None)
        assert desc.value == "sys_tier_independent_purchase"

    def test_build_api_description_available_false(self, mock_page):
        """available=False + 非独立付费 → ERROR 'insufficient_points' 文案（line 282）。"""
        panel = self._make_panel(mock_page)
        desc = panel._build_api_description("daily", False)
        assert desc.value == "sys_tier_insufficient_points"

    def test_build_api_description_available_true(self, mock_page):
        """available=True + 非独立付费 → 空文案（line 288）。"""
        panel = self._make_panel(mock_page)
        desc = panel._build_api_description("daily", True)
        assert desc.value == ""

    # ------------------------------------------------------------------
    # _format_last_probe_text（lines 293-294）
    # ------------------------------------------------------------------

    def test_format_last_probe_text_none_returns_dash(self, mock_page):
        """_last_probe_time=None → ': -'（line 293）。"""
        panel = self._make_panel(mock_page)
        assert panel._format_last_probe_text() == "sys_tier_last_probe_time: -"

    def test_format_last_probe_text_with_time(self, mock_page):
        """_last_probe_time=datetime → ': 2024-01-01 12:00'（line 294）。"""
        panel = self._make_panel(mock_page)
        panel._last_probe_time = datetime(2024, 1, 1, 12, 0)
        assert panel._format_last_probe_text() == "sys_tier_last_probe_time: 2024-01-01 12:00"

    # ------------------------------------------------------------------
    # _format_stale_hint_text（lines 299-301）
    # ------------------------------------------------------------------

    def test_format_stale_hint_text_empty(self, mock_page):
        """_stale_apis=[] → ''（line 299）。"""
        panel = self._make_panel(mock_page)
        assert panel._format_stale_hint_text() == ""

    def test_format_stale_hint_text_non_empty(self, mock_page):
        """_stale_apis 非空 → hint + ', '连接的 API 列表（lines 300-301）。"""
        panel = self._make_panel(mock_page)
        panel._stale_apis = ["api1", "api2"]
        assert panel._format_stale_hint_text() == "sys_tier_stale_apis_hint: api1, api2"

    # ------------------------------------------------------------------
    # _build_api_list_controls 独立付费标记（line 197）
    # ------------------------------------------------------------------

    def test_build_api_list_controls_independent_purchase_adds_badge(self, mock_page):
        """独立付费 API 在列表行中附加 ATTACH_MONEY_ROUNDED 图标（line 197）。"""
        self.mock_client.is_independent_purchase.side_effect = lambda api: api == "cyq_perf"
        self.mock_client.get_tier_apis.return_value = {"cyq_perf"}
        panel = self._make_panel(mock_page)
        controls = panel._build_api_list_controls()
        assert len(controls) == 1
        # ResponsiveRow.controls[0] 是 Container，content 是 Row，Row.controls 含 [Text, Icon]
        api_name_container = controls[0].controls[0]
        api_row = api_name_container.content
        # V1: ft.Icon 用 icon 属性存储图标（V0 用 name）
        has_badge = any(getattr(c, "icon", None) == ft.Icons.ATTACH_MONEY_ROUNDED for c in api_row.controls)
        assert has_badge

    # ------------------------------------------------------------------
    # _on_probe_result 分派（lines 465-487）
    # ------------------------------------------------------------------

    def test_on_probe_result_completed_dispatches_notify_completed(self, mock_page):
        """type='completed' → _notify_probe_completed(tier, available, unavailable, unknown)（lines 467-473）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_notify_probe_completed") as mock_notify:
            panel._on_probe_result(
                {
                    "type": "completed",
                    "tier": "points_5000",
                    "available": 10,
                    "unavailable": 2,
                    "unknown": 0,
                }
            )
        mock_notify.assert_called_once_with("points_5000", 10, 2, 0)

    def test_on_probe_result_tier_too_high_dispatches_notify_tier_too_high(self, mock_page):
        """type='tier_too_high' → _notify_tier_too_high(tier, false_count, total)（lines 474-479）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_notify_tier_too_high") as mock_notify:
            panel._on_probe_result(
                {
                    "type": "tier_too_high",
                    "tier": "points_10000",
                    "false_count": 5,
                    "total": 10,
                }
            )
        mock_notify.assert_called_once_with("points_10000", 5, 10)

    def test_on_probe_result_all_failed_dispatches_notify_all_failed(self, mock_page):
        """type='all_failed' → _notify_probe_all_failed(tier)（lines 480-481）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_notify_probe_all_failed") as mock_notify:
            panel._on_probe_result({"type": "all_failed", "tier": "points_120"})
        mock_notify.assert_called_once_with("points_120")

    def test_on_probe_result_set_tier_failed_dispatches_notify_failed(self, mock_page):
        """type='set_tier_failed' → _notify_probe_failed(message)（lines 482-483）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_notify_probe_failed") as mock_notify:
            panel._on_probe_result({"type": "set_tier_failed", "message": "custom error"})
        mock_notify.assert_called_once_with("custom error")

    def test_on_probe_result_unknown_type_logs_warning(self, mock_page, caplog):
        """未知 type → 记录 warning 日志（lines 484-485）。"""
        panel = self._make_panel(mock_page)
        with caplog.at_level(logging.WARNING, logger="ui.views.settings_tabs.tier_api_panel"):
            panel._on_probe_result({"type": "unknown_type"})
        assert any("Unknown probe result type" in r.message for r in caplog.records)

    def test_on_probe_result_dispatch_exception_logs_warning(self, mock_page, caplog):
        """分派过程抛异常 → 记录 warning 日志（lines 486-487）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_notify_probe_completed", side_effect=Exception("dispatch error")):
            with caplog.at_level(logging.WARNING, logger="ui.views.settings_tabs.tier_api_panel"):
                panel._on_probe_result({"type": "completed"})
        assert any("_on_probe_result dispatch failed" in r.message for r in caplog.records)

    # ------------------------------------------------------------------
    # _refresh_api_list（lines 599-605）
    # ------------------------------------------------------------------

    def test_refresh_api_list_updates_probe_status_and_controls(self, mock_page):
        """_refresh_api_list 正常路径：更新 _probe_status + 重建控件（lines 599-603）。"""
        panel = self._make_panel(mock_page)
        new_cache = {"daily": False, "fina_indicator": True}
        panel._refresh_api_list(new_cache)
        assert panel._probe_status == new_cache
        assert len(panel.api_list_view.controls) > 0

    def test_refresh_api_list_exception_logs_warning(self, mock_page, caplog):
        """_refresh_api_list 异常路径：记录 warning 日志（lines 604-605）。"""
        panel = self._make_panel(mock_page)
        with patch.object(panel, "_build_api_list_controls", side_effect=Exception("build error")):
            with caplog.at_level(logging.WARNING, logger="ui.views.settings_tabs.tier_api_panel"):
                panel._refresh_api_list({"daily": True})
        assert any("_refresh_api_list failed" in r.message for r in caplog.records)

    # ------------------------------------------------------------------
    # _on_locale_change 异常降级（lines 354-355）
    # ------------------------------------------------------------------

    def test_on_locale_change_exception_logs_warning(self, mock_page, caplog):
        """_on_locale_change 异常路径：记录 warning 日志，不影响主流程（lines 354-355）。"""
        panel = self._make_panel(mock_page)
        with patch("ui.i18n.refresh_dropdown_options", side_effect=Exception("locale error")):
            with caplog.at_level(logging.WARNING, logger="ui.views.settings_tabs.tier_api_panel"):
                panel._on_locale_change()
        assert any("_on_locale_change failed" in r.message for r in caplog.records)

    # ------------------------------------------------------------------
    # dispose 异常容错（lines 367-368）
    # ------------------------------------------------------------------

    def test_dispose_exception_logs_warning(self, mock_page, caplog):
        """dispose 时 I18n.unsubscribe 抛异常 → 记录 warning 日志（lines 367-368）。"""
        panel = self._make_panel(mock_page)
        self.mock_i18n.unsubscribe.side_effect = Exception("unsubscribe error")
        with caplog.at_level(logging.WARNING, logger="ui.views.settings_tabs.tier_api_panel"):
            panel.dispose()
        assert any("dispose: I18n.unsubscribe failed" in r.message for r in caplog.records)
