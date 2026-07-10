"""Phase D.2：TierApiPanel 声明式重写契约守护测试。

测试覆盖（参考 test_task_center_view.py / test_failover_config_panel.py 样板）：
- 模块级纯函数（无 self 依赖，便于单测）：
  - _build_tier_options：档位下拉框选项构建
  - _render_probe_status：probe 三态渲染（True/False/None + 独立付费子分支）
  - _build_api_description：三分支（独立付费/积分不足/默认空）
  - _build_api_list_controls：独立付费标记附加
  - _format_last_probe_text：时间格式化（None + datetime）
  - _compute_progress_text：probe 三态文本分派（running/result 4 类型/idle）
  - _compute_list_height：响应式断点高度（lg/md/sm/width=0）
- 契约守护测试（grep 命令式禁止模式 = 0 + 验证声明式 API）

声明式组件 TierApiPanel（@ft.component + use_viewmodel + use_state + use_effect）
是有状态组件，由集成测试（flet_test_page fixture）覆盖，不在本单测范围。
"""

import contextlib
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from data.constants import TUSHARE_POINT_TIERS
from ui.views.settings_tabs import tier_api_panel as panel_module
from ui.views.settings_tabs.tier_api_panel import (
    _build_api_description,
    _build_api_list_controls,
    _build_tier_options,
    _compute_list_height,
    _compute_progress_text,
    _format_last_probe_text,
    _render_probe_status,
    _TIER_PANEL_LG_BREAKPOINT,
    _TIER_PANEL_MD_BREAKPOINT,
)

pytestmark = pytest.mark.unit

PANEL_PATH = Path(panel_module.__file__)


def _make_mock_client():
    """创建 mock TushareClient，覆盖纯函数所需方法。"""
    client = MagicMock()
    client.get_tier_apis.return_value = {"daily", "fina_indicator"}
    client.is_independent_purchase.return_value = False
    client.get_last_probe_time.return_value = None
    return client


# ------------------------------------------------------------------
# _build_tier_options
# ------------------------------------------------------------------


class TestBuildTierOptions:
    """_build_tier_options：档位下拉框选项构建。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_option_for_each_tier(self):
        """返回的 Option 数量与 TUSHARE_POINT_TIERS 一致。"""
        options = _build_tier_options()
        assert len(options) == len(TUSHARE_POINT_TIERS)

    def test_option_key_matches_tier(self):
        """每个 Option 的 key 对应 TUSHARE_POINT_TIERS 中的档位。"""
        options = _build_tier_options()
        keys = [opt.key for opt in options]
        assert keys == list(TUSHARE_POINT_TIERS)

    def test_option_text_uses_i18n_label(self):
        """Option 的 text 来自 I18n.get(f'sys_tier_{tier}_label')。"""
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        options = _build_tier_options()
        for opt, tier in zip(options, TUSHARE_POINT_TIERS, strict=True):
            assert opt.text == f"translated_sys_tier_{tier}_label"


# ------------------------------------------------------------------
# _render_probe_status
# ------------------------------------------------------------------


class TestRenderProbeStatus:
    """_render_probe_status：probe 三态渲染。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_client = _make_mock_client()
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.tier_api_panel.AppColors", self.mock_ac),
            patch("data.external.tushare_client.TushareClient", return_value=self.mock_client),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_available_true_returns_success(self):
        """available=True → SUCCESS 图标 + 'sys_tier_available' 文案。"""
        icon, text, color = _render_probe_status("daily", True)
        assert color == self.mock_ac.SUCCESS
        assert text.value == "sys_tier_available"

    def test_available_false_independent_purchase_returns_warning(self):
        """available=False + 独立付费 → WARNING + 'sys_tier_independent_purchase'。"""
        self.mock_client.is_independent_purchase.return_value = True
        icon, text, color = _render_probe_status("cyq_perf", False)
        assert color == self.mock_ac.WARNING
        assert text.value == "sys_tier_independent_purchase"

    def test_available_false_insufficient_points_returns_error(self):
        """available=False + 非独立付费 → ERROR + 'sys_tier_unavailable'。"""
        icon, text, color = _render_probe_status("daily", False)
        assert color == self.mock_ac.ERROR
        assert text.value == "sys_tier_unavailable"

    def test_available_none_returns_not_probed(self):
        """available=None → HELP_OUTLINE + 'sys_tier_not_probed'。"""
        icon, text, color = _render_probe_status("daily", None)
        assert text.value == "sys_tier_not_probed"
        assert isinstance(color, str)


# ------------------------------------------------------------------
# _build_api_description
# ------------------------------------------------------------------


class TestBuildApiDescription:
    """_build_api_description：三分支（独立付费/积分不足/默认空）。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_client = _make_mock_client()
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.tier_api_panel.AppColors", self.mock_ac),
            patch("data.external.tushare_client.TushareClient", return_value=self.mock_client),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_independent_purchase_returns_warning_text(self):
        """独立付费 API → WARNING italic 文案。"""
        self.mock_client.is_independent_purchase.return_value = True
        desc = _build_api_description("cyq_perf", None)
        assert desc.value == "sys_tier_independent_purchase"

    def test_available_false_returns_insufficient_points(self):
        """available=False + 非独立付费 → ERROR 'insufficient_points' 文案。"""
        desc = _build_api_description("daily", False)
        assert desc.value == "sys_tier_insufficient_points"

    def test_available_true_returns_empty(self):
        """available=True + 非独立付费 → 空文案。"""
        desc = _build_api_description("daily", True)
        assert desc.value == ""

    def test_independent_purchase_takes_precedence_over_available_false(self):
        """独立付费标记优先于 available=False（独立付费 API 不显示积分不足）。"""
        self.mock_client.is_independent_purchase.return_value = True
        desc = _build_api_description("cyq_perf", False)
        assert desc.value == "sys_tier_independent_purchase"


# ------------------------------------------------------------------
# _build_api_list_controls
# ------------------------------------------------------------------


class TestBuildApiListControls:
    """_build_api_list_controls：独立付费标记附加。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_client = _make_mock_client()
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.tier_api_panel.AppColors", self.mock_ac),
            patch("data.external.tushare_client.TushareClient", return_value=self.mock_client),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_one_control_per_api(self):
        """每个 API 生成一个 ResponsiveRow 控件。"""
        self.mock_client.get_tier_apis.return_value = {"daily", "fina_indicator"}
        controls = _build_api_list_controls("points_5000", {})
        assert len(controls) == 2

    def test_independent_purchase_adds_badge(self):
        """独立付费 API 在列表行中附加 ATTACH_MONEY_ROUNDED 图标。"""
        self.mock_client.is_independent_purchase.side_effect = lambda api: api == "cyq_perf"
        self.mock_client.get_tier_apis.return_value = {"cyq_perf"}
        controls = _build_api_list_controls("points_5000", {})
        assert len(controls) == 1
        # ResponsiveRow.controls[0] 是 Container，content 是 Row，Row.controls 含 [Text, Icon]
        api_name_container = controls[0].controls[0]
        api_row = api_name_container.content
        has_badge = any(getattr(c, "icon", None) == ft.Icons.ATTACH_MONEY_ROUNDED for c in api_row.controls)
        assert has_badge

    def test_non_independent_purchase_no_badge(self):
        """非独立付费 API 不附加 ATTACH_MONEY_ROUNDED 图标。"""
        self.mock_client.is_independent_purchase.return_value = False
        self.mock_client.get_tier_apis.return_value = {"daily"}
        controls = _build_api_list_controls("points_5000", {})
        api_name_container = controls[0].controls[0]
        api_row = api_name_container.content
        has_badge = any(getattr(c, "icon", None) == ft.Icons.ATTACH_MONEY_ROUNDED for c in api_row.controls)
        assert not has_badge

    def test_empty_tier_apis_returns_empty_list(self):
        """档位无 API 覆盖时返回空列表。"""
        self.mock_client.get_tier_apis.return_value = set()
        controls = _build_api_list_controls("points_120", {})
        assert controls == []


# ------------------------------------------------------------------
# _format_last_probe_text
# ------------------------------------------------------------------


class TestFormatLastProbeText:
    """_format_last_probe_text：时间格式化。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_none_returns_dash(self):
        """last_probe_time=None → ': -'。"""
        assert _format_last_probe_text(None) == "sys_tier_last_probe_time: -"

    def test_datetime_returns_formatted(self):
        """last_probe_time=datetime → ': 2024-01-01 12:00'。"""
        result = _format_last_probe_text(datetime(2024, 1, 1, 12, 0))
        assert result == "sys_tier_last_probe_time: 2024-01-01 12:00"


# ------------------------------------------------------------------
# _compute_progress_text
# ------------------------------------------------------------------


class TestComputeProgressText:
    """_compute_progress_text：probe 三态文本分派。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.patches = [
            patch("ui.views.settings_tabs.tier_api_panel.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_running_with_progress_returns_count_text(self):
        """running 且 total>0 → sys_tier_probe_in_progress_with_count。"""
        _compute_progress_text(True, (3, 5), None)
        self.mock_i18n.get.assert_called_with("sys_tier_probe_in_progress_with_count", completed=3, total=5)

    def test_running_without_progress_returns_in_progress(self):
        """running 且 total=0 → sys_tier_probe_in_progress。"""
        result = _compute_progress_text(True, (0, 0), None)
        self.mock_i18n.get.assert_called_with("sys_tier_probe_in_progress")
        assert result == "sys_tier_probe_in_progress"

    def test_result_completed_returns_completed_text(self):
        """result.type=completed → sys_tier_probe_completed。"""
        result = _compute_progress_text(
            False, (0, 0), {"type": "completed", "available": 10, "unavailable": 2, "unknown": 0}
        )
        self.mock_i18n.get.assert_called_with("sys_tier_probe_completed", available=10, unavailable=2, unknown=0)
        assert result == "sys_tier_probe_completed"

    def test_result_tier_too_high_returns_tier_too_high_text(self):
        """result.type=tier_too_high → sys_tier_tier_too_high。"""
        result = _compute_progress_text(False, (0, 0), {"type": "tier_too_high", "false_count": 5, "total": 10})
        self.mock_i18n.get.assert_called_with("sys_tier_tier_too_high", false_count=5, total=10)
        assert result == "sys_tier_tier_too_high"

    def test_result_all_failed_returns_all_failed_text(self):
        """result.type=all_failed → sys_tier_probe_all_failed。"""
        result = _compute_progress_text(False, (0, 0), {"type": "all_failed"})
        self.mock_i18n.get.assert_called_with("sys_tier_probe_all_failed")
        assert result == "sys_tier_probe_all_failed"

    def test_result_set_tier_failed_returns_failed_with_message(self):
        """result.type=set_tier_failed → sys_tier_probe_failed + (message)。"""
        result = _compute_progress_text(False, (0, 0), {"type": "set_tier_failed", "message": "custom error"})
        self.mock_i18n.get.assert_called_with("sys_tier_probe_failed")
        assert result == "sys_tier_probe_failed (custom error)"

    def test_idle_no_result_returns_empty(self):
        """idle 且无 result → 空。"""
        assert _compute_progress_text(False, (0, 0), None) == ""

    def test_unknown_result_type_returns_empty(self):
        """未知 result.type → 空。"""
        assert _compute_progress_text(False, (0, 0), {"type": "unknown_type"}) == ""

    def test_running_takes_precedence_over_result(self):
        """running 状态优先于 result（probe 进行中不显示旧结果）。"""
        _compute_progress_text(True, (1, 2), {"type": "completed"})
        self.mock_i18n.get.assert_called_with("sys_tier_probe_in_progress_with_count", completed=1, total=2)


# ------------------------------------------------------------------
# _compute_list_height
# ------------------------------------------------------------------


class TestComputeListHeight:
    """_compute_list_height：响应式断点高度计算。"""

    def test_width_zero_returns_default(self):
        """width=0（初始未知）→ 300（默认）。"""
        assert _compute_list_height(0) == 300

    def test_lg_breakpoint_returns_300(self):
        """width >= lg(1200) → 300。"""
        assert _compute_list_height(_TIER_PANEL_LG_BREAKPOINT) == 300
        assert _compute_list_height(_TIER_PANEL_LG_BREAKPOINT + 100) == 300

    def test_md_breakpoint_returns_280(self):
        """md(800-1199) → 280。"""
        assert _compute_list_height(_TIER_PANEL_MD_BREAKPOINT) == 280
        assert _compute_list_height(_TIER_PANEL_LG_BREAKPOINT - 1) == 280

    def test_sm_breakpoint_returns_240(self):
        """sm(<800) → 240。"""
        assert _compute_list_height(_TIER_PANEL_MD_BREAKPOINT - 1) == 240
        assert _compute_list_height(100) == 240


# ------------------------------------------------------------------
# 契约守护测试：声明式组件禁止命令式模式
# ------------------------------------------------------------------


class TestTierApiPanelContract:
    """契约守护测试：声明式组件禁止命令式模式（参考 test_failover_config_panel.py 样板）。"""

    def _read_panel_content(self) -> str:
        return PANEL_PATH.read_text(encoding="utf-8")

    def test_no_imperative_patterns(self) -> None:
        """grep 命令式禁止模式 = 0（DoD #1）。"""
        content = self._read_panel_content()
        forbidden_patterns = [
            "def did_mount",
            "def will_unmount",
            "def dispose",
            "def _on_locale_change",
            "def _on_vm_state_changed",
            "_prev_probe_result_version",
            "def handle_resize",
            "self.update()",
            "class TierApiPanel(ft.Column)",
            "class TierApiPanel(ft.Container)",
            "class TierApiPanel(ft.UserControl)",
            "class TierApiPanel(PageRefMixin",
            "PageRefMixin",
            "_page_ref",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"禁止命令式模式: {pattern}"

    def test_is_declarative_component(self) -> None:
        """验证 TierApiPanel 是 @ft.component 声明式组件。"""
        content = self._read_panel_content()
        assert "@ft.component" in content
        assert "def TierApiPanel(" in content

    def test_uses_use_viewmodel_external_vm_mode(self) -> None:
        """验证通过 use_viewmodel(vm=system_vm) 外部 VM 模式订阅（CLAUDE.md §3.3）。"""
        content = self._read_panel_content()
        assert "use_viewmodel(vm=" in content

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 自动重渲染。"""
        content = self._read_panel_content()
        assert "ft.use_state(I18n.get_observable_state)" in content

    def test_uses_ft_context_page(self) -> None:
        """验证通过 ft.context.page 访问 page（try/except RuntimeError 守卫）。"""
        content = self._read_panel_content()
        assert "ft.context.page" in content
        assert "RuntimeError" in content

    def test_no_use_ref_caching_imperative_instances(self) -> None:
        """验证 use_ref 仅用于链式保留 prev on_resize（非缓存命令式实例）。

        本组件使用 _prev_resize_ref 保存前一个 on_resize handler 以支持链式调用，
        这是响应式 hook 合理用法，不属于"缓存命令式实例"。
        """
        content = self._read_panel_content()
        # use_ref 允许出现，但仅用于 _prev_resize_ref（链式 on_resize）
        assert "use_ref" in content
        assert "_prev_resize_ref" in content

    def test_pure_helper_functions_exported(self) -> None:
        """验证模块级纯函数保留导出。"""
        content = self._read_panel_content()
        assert "def _build_tier_options(" in content
        assert "def _render_probe_status(" in content
        assert "def _build_api_description(" in content
        assert "def _build_api_list_controls(" in content
        assert "def _format_last_probe_text(" in content
        assert "def _compute_progress_text(" in content
        assert "def _compute_list_height(" in content

    def test_responsive_breakpoint_constants_preserved(self) -> None:
        """验证响应式断点常量保留。"""
        content = self._read_panel_content()
        assert "_TIER_PANEL_LG_BREAKPOINT" in content
        assert "_TIER_PANEL_MD_BREAKPOINT" in content

    def test_no_stale_hint_text_dead_code(self) -> None:
        """验证死代码 _stale_apis/stale_hint_text 已移除（YAGNI）。

        检查代码级模式（属性访问/方法定义/变量赋值），允许 docstring 中的变更说明提及。
        """
        content = self._read_panel_content()
        # 检查实际代码引用（self._stale_apis 属性访问）
        assert "self._stale_apis" not in content
        # 检查方法定义
        assert "def _format_stale_hint_text" not in content
        # 检查变量赋值/属性赋值（= stale_hint_text 或 .stale_hint_text =）
        assert "stale_hint_text =" not in content
        assert ".stale_hint_text" not in content
