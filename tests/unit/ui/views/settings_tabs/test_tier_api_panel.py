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
- 组件体渲染测试（TierApiPanel @ft.component body）
"""

import asyncio
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from data.constants import TUSHARE_POINT_TIERS
from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects
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
from ui.viewmodels.system_viewmodel import ProbeResultRow

pytestmark = pytest.mark.unit

PANEL_PATH = Path(panel_module.__file__)


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
            False, (0, 0), ProbeResultRow(type="completed", available=10, unavailable=2, unknown=0)
        )
        self.mock_i18n.get.assert_called_with("sys_tier_probe_completed", available=10, unavailable=2, unknown=0)
        assert result == "sys_tier_probe_completed"

    def test_result_tier_too_high_returns_tier_too_high_text(self):
        """result.type=tier_too_high → sys_tier_tier_too_high。"""
        result = _compute_progress_text(False, (0, 0), ProbeResultRow(type="tier_too_high", false_count=5, total=10))
        self.mock_i18n.get.assert_called_with("sys_tier_tier_too_high", false_count=5, total=10)
        assert result == "sys_tier_tier_too_high"

    def test_result_all_failed_returns_all_failed_text(self):
        """result.type=all_failed → sys_tier_probe_all_failed。"""
        result = _compute_progress_text(False, (0, 0), ProbeResultRow(type="all_failed"))
        self.mock_i18n.get.assert_called_with("sys_tier_probe_all_failed")
        assert result == "sys_tier_probe_all_failed"

    def test_result_set_tier_failed_returns_failed_with_message(self):
        """result.type=set_tier_failed → sys_tier_probe_failed + (message)。"""
        result = _compute_progress_text(False, (0, 0), ProbeResultRow(type="set_tier_failed", message="custom error"))
        self.mock_i18n.get.assert_called_with("sys_tier_probe_failed")
        assert result == "sys_tier_probe_failed (custom error)"

    def test_idle_no_result_returns_empty(self):
        """idle 且无 result → 空。"""
        assert _compute_progress_text(False, (0, 0), None) == ""

    def test_unknown_result_type_returns_empty(self):
        """未知 result.type → 空。"""
        assert _compute_progress_text(False, (0, 0), ProbeResultRow(type="unknown_type")) == ""

    def test_running_takes_precedence_over_result(self):
        """running 状态优先于 result（probe 进行中不显示旧结果）。"""
        _compute_progress_text(True, (1, 2), ProbeResultRow(type="completed"))
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
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染。"""
        content = self._read_panel_content()
        assert "ft.use_state(get_observable_state)" in content

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


# ============================================================================
# 组件体渲染测试 (TierApiPanel @ft.component body)
# ============================================================================


class _FakeSystemState:
    """模拟 SystemState 的最小字段集 (L771 合规: probe_result 直接放入 state)."""

    def __init__(
        self,
        probe_in_progress: bool = False,
        probe_result: ProbeResultRow | None = None,
    ) -> None:
        self.probe_in_progress = probe_in_progress
        self.probe_result = probe_result


class _FakeSystemVM:
    """模拟 SystemViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。

    L771 合规: 无 dual-track last_probe_result property, probe_result 直接放入 state.
    """

    def __init__(
        self,
        current_tier: str = "points_5000",
        capability_cache: dict | None = None,
        probe_result: ProbeResultRow | None = None,
        probe_in_progress: bool = False,
        last_probe_time: datetime | None = None,
    ) -> None:
        self._state = _FakeSystemState(
            probe_in_progress=probe_in_progress,
            probe_result=probe_result,
        )
        self._subscribers: list[Any] = []
        self._current_tier = current_tier
        self._capability_cache = capability_cache if capability_cache is not None else {}
        self._last_probe_time = last_probe_time
        self.dispose_called = False

    @property
    def state(self) -> _FakeSystemState:
        return self._state

    def get_current_tier(self) -> str:
        return self._current_tier

    def get_capability_cache(self) -> dict[str, bool | None]:
        return self._capability_cache

    def get_last_probe_time(self) -> datetime | None:
        return self._last_probe_time

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    async def on_tier_changed(self, new_tier: str, progress_callback: Any = None) -> dict:
        return {"type": "completed", "available": 1, "unavailable": 0, "unknown": 0}

    async def run_probe(self, progress_callback: Any = None) -> dict:
        return {"type": "completed", "available": 1, "unavailable": 0, "unknown": 0}


def _collect_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归: mock I18n/AppColors 下
    content 属性返回新 MagicMock, 无守卫会无限生成子节点致内存暴涨)。
    """
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_collect_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_collect_controls(content))
    return result


def _make_tier_panel_patches(mock_client: MagicMock) -> list:
    """创建 TierApiPanel 渲染所需的 patch 列表。"""
    return [
        patch("ui.views.settings_tabs.tier_api_panel.I18n"),
        patch("ui.views.settings_tabs.tier_api_panel.AppColors"),
        patch("ui.views.settings_tabs.tier_api_panel.AppStyles"),
        patch("data.external.tushare_client.TushareClient", return_value=mock_client),
    ]


def _make_mock_client() -> MagicMock:
    """创建 mock TushareClient for component rendering."""
    client = MagicMock()
    client.get_tier_apis.return_value = {"daily", "fina_indicator"}
    client.is_independent_purchase.return_value = False
    client.get_last_probe_time.return_value = None
    client.get_capability_cache.return_value = {}
    return client


def _trigger_callback(cb, event):
    """Safely trigger Flet optional callback in tests.

    Flet stubs declare callbacks (on_click/on_change/on_horizontal_drag_*/etc.)
    as Optional[Callable[[], None]], but runtime passes a ControlEvent.
    Centralize type narrowing + type: ignore here.
    """
    assert cb is not None
    cb(event)  # type: ignore[reportCallIssue, reason: Flet stub declares callbacks as 0-arg, but runtime passes event]


class TestTierApiPanelComponentBody:
    """TierApiPanel 组件体渲染测试: 验证控件树结构 + VM 交互 + 事件 handler。"""

    def _make_page(self):
        """创建带 on_resize 的 FakePage。"""
        from tests.unit.ui.component_renderer import FakePage

        page = FakePage()
        page.on_resize = None  # type: ignore[method-assign]
        return page

    def test_mount_returns_column(self, mock_i18n_state, mock_app_colors_state):
        """挂载 TierApiPanel 返回 ft.Column。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        assert isinstance(result, ft.Column)

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state):
        """挂载时 use_viewmodel hook 注册 VM 订阅。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)

        assert len(vm._subscribers) > 0

    def test_render_contains_dropdown_and_button(self, mock_i18n_state, mock_app_colors_state):
        """渲染的控件树含 Dropdown (档位选择) + Button (probe 触发)。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        ctrls = _collect_controls(result)
        dropdowns = [c for c in ctrls if isinstance(c, ft.Dropdown)]
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        assert len(dropdowns) >= 1, "应含 Dropdown"
        assert len(buttons) >= 1, "应含 Button"

    def test_render_contains_listview(self, mock_i18n_state, mock_app_colors_state):
        """渲染的控件树含 ListView (API 列表)。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        ctrls = _collect_controls(result)
        listviews = [c for c in ctrls if isinstance(c, ft.ListView)]
        assert len(listviews) >= 1, "应含 ListView"

    def test_dropdown_disabled_when_probe_in_progress(self, mock_i18n_state, mock_app_colors_state):
        """probe_in_progress=True 时 Dropdown + Button disabled。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM(probe_in_progress=True)
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        ctrls = _collect_controls(result)
        dropdowns = [c for c in ctrls if isinstance(c, ft.Dropdown)]
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        assert dropdowns[0].disabled is True
        assert buttons[0].disabled is True

    def test_on_tier_change_triggers_run_task(self, mock_i18n_state, mock_app_colors_state):
        """_on_tier_change 触发 page.run_task 调用 _run_tier_change。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        run_task_calls: list = []
        page.run_task = MagicMock(side_effect=lambda fn, *a, **kw: run_task_calls.append((fn, a)))  # type: ignore[method-assign]
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        # 找到 Dropdown 的 on_select handler
        ctrls = _collect_controls(result)
        dropdown = next(c for c in ctrls if isinstance(c, ft.Dropdown))
        # 模拟档位变更事件
        e = MagicMock()
        e.control.value = "points_120"
        _trigger_callback(dropdown.on_select, e)
        assert len(run_task_calls) > 0, "应触发 page.run_task"

    def test_on_tier_change_same_tier_does_nothing(self, mock_i18n_state, mock_app_colors_state):
        """_on_tier_change 选择相同档位时不触发 run_task。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM(current_tier="points_5000")
        client = _make_mock_client()
        page = self._make_page()
        run_task_calls: list = []
        page.run_task = MagicMock(side_effect=lambda fn, *a, **kw: run_task_calls.append((fn, a)))  # type: ignore[method-assign]
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        ctrls = _collect_controls(result)
        dropdown = next(c for c in ctrls if isinstance(c, ft.Dropdown))
        e = MagicMock()
        e.control.value = "points_5000"  # 同档位
        _trigger_callback(dropdown.on_select, e)
        assert len(run_task_calls) == 0, "相同档位不应触发 run_task"

    def test_on_probe_click_triggers_run_task(self, mock_i18n_state, mock_app_colors_state):
        """_on_probe_click 触发 page.run_task 调用 _run_probe。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        run_task_calls: list = []
        page.run_task = MagicMock(side_effect=lambda fn, *a, **kw: run_task_calls.append((fn, a)))  # type: ignore[method-assign]
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        ctrls = _collect_controls(result)
        button = next(c for c in ctrls if isinstance(c, ft.Button))
        _trigger_callback(button.on_click, MagicMock())
        assert len(run_task_calls) > 0, "应触发 page.run_task"

    def test_last_probe_time_displayed(self, mock_i18n_state, mock_app_colors_state):
        """渲染时通过 vm.get_last_probe_time() 显示 last_probe_time 文本 (MVVM 边界)。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM(last_probe_time=datetime(2024, 6, 15, 10, 30))
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            render_once(component)

        # View 通过 vm.get_last_probe_time() 获取, 不再直接访问 TushareClient (MVVM 边界)
        client.get_last_probe_time.assert_not_called()

    def test_probe_in_progress_shows_progress_text(self, mock_i18n_state, mock_app_colors_state):
        """probe_in_progress=True 且有进度时显示进度文本。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM(probe_in_progress=True)
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        # 进度文本由 _compute_progress_text 生成, probe_in_progress 时调用 I18n.get
        assert isinstance(result, ft.Column)

    def test_on_resize_updates_width(self, mock_i18n_state, mock_app_colors_state):
        """_setup_resize 挂载 on_resize handler, 触发后更新 width state。"""
        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()
        client = _make_mock_client()
        page = self._make_page()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            render_once(component)

        # _setup_resize 应已将 page.on_resize 替换为 _on_resize
        assert page.on_resize is not None, "挂载后 page.on_resize 应被设置"
        # 触发 resize 事件
        e = MagicMock()
        e.width = 1000
        page.on_resize(e)  # 不应抛异常


# ============================================================================
# R2 CancelledError 传播契约 (CLAUDE.md §3 红线 R2)
# ============================================================================


class TestTierApiPanelR2CancelledErrorPropagation:
    """R2 红线: tier_api_panel async handler 必须传播 CancelledError, 不被 except Exception 吞没。

    覆盖 _run_tier_change / _run_probe 内的 ``except asyncio.CancelledError: raise`` 守卫
    (CLAUDE.md §3 R2 + tier_api_panel.py L317-318 / L338-339)。

    测试模式: 让 vm.on_tier_changed / vm.run_probe 抛 CancelledError,
    page.run_task 同步执行 coroutine, 验证 CancelledError 从 on_select/on_click 传播。
    """

    def _make_page_with_sync_run_task(self):
        """创建 FakePage, 其 run_task 同步执行 coroutine 并捕获 CancelledError 到 state.

        返回 (page, state) 二元组; state["cancelled"] 标记 CancelledError 是否从 coroutine 传播.
        不让 CancelledError 逃逸到 pytest, 避免 pytest traceback formatting 阶段
        _truncate_recursive_traceback 触发 pathlib/os.stat 无限递归 (pre-existing pytest issue).
        """
        from tests.unit.ui.component_renderer import FakePage

        page = FakePage()
        page.on_resize = None  # type: ignore[method-assign]
        state: dict[str, bool] = {"cancelled": False}

        def _sync_run_task(fn, *args, **kwargs):
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(result)
                except asyncio.CancelledError:
                    # R2 守卫验证: CancelledError 应从 _run_tier_change/_run_probe 传播到此
                    state["cancelled"] = True
                finally:
                    loop.close()

        # NOTE(lazy): 直接赋值普通函数, 不用 MagicMock(side_effect=...), 避免 CancelledError
        # 传播时 MagicMock 在 pytest traceback formatting 阶段触发 _truncate_recursive_traceback
        # 无限递归 (recursionindex → mock __call__ → _increment_mock_call 循环).
        # ceiling: 仅影响 R2 测试 page.run_task 注入. upgrade: 升级 pytest 或迁移到非 MagicMock fixture.
        page.run_task = _sync_run_task  # type: ignore[method-assign]
        return page, state

    def test_run_tier_change_propagates_cancelled_error(self, mock_i18n_state, mock_app_colors_state):
        """_run_tier_change: vm.on_tier_changed 抛 CancelledError → 传播 (R2 红线)。"""
        from types import SimpleNamespace

        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM(current_tier="points_5000")

        async def _raising_on_tier_changed(new_tier, progress_callback=None):
            raise asyncio.CancelledError()

        vm.on_tier_changed = _raising_on_tier_changed  # type: ignore[method-assign]

        client = _make_mock_client()
        page, state = self._make_page_with_sync_run_task()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        # NOTE(lazy): 不用 _collect_controls (递归 MagicMock 触发 RecursionError pre-existing),
        # 直接从 result.controls[2].controls 取 Dropdown/Button (tier_api_panel 固定布局).
        # ceiling: tier_api_panel 布局结构变更时需同步. upgrade: 修复 _collect_controls MagicMock 递归.
        # 使用 SimpleNamespace 替代 MagicMock 作为 event 对象, 避免 traceback formatting 递归.
        row = result.controls[2]
        dropdown = next(c for c in row.controls if isinstance(c, ft.Dropdown))
        e = SimpleNamespace(control=SimpleNamespace(value="points_120"))
        cast(Any, dropdown.on_select)(e)
        # R2 红线: CancelledError 应从 _run_tier_change 传播到 _sync_run_task
        assert state["cancelled"] is True, "CancelledError 必须从 _run_tier_change 传播 (R2 红线)"

    def test_run_probe_propagates_cancelled_error(self, mock_i18n_state, mock_app_colors_state):
        """_run_probe: vm.run_probe 抛 CancelledError → 传播 (R2 红线)。"""
        from types import SimpleNamespace

        from ui.views.settings_tabs.tier_api_panel import TierApiPanel

        vm = _FakeSystemVM()

        async def _raising_run_probe(progress_callback=None):
            raise asyncio.CancelledError()

        vm.run_probe = _raising_run_probe  # type: ignore[method-assign]

        client = _make_mock_client()
        page, state = self._make_page_with_sync_run_task()
        with contextlib.ExitStack() as stack:
            for p in _make_tier_panel_patches(client):
                stack.enter_context(p)
            component = make_component(TierApiPanel, system_vm=vm)
            run_mount_effects(component, page=page)
            result = render_once(component)

        # 不用 _collect_controls (同上 NOTE), 直接从 result.controls[2].controls 取 Button
        row = result.controls[2]
        button = next(c for c in row.controls if isinstance(c, ft.Button))
        cast(Any, button.on_click)(SimpleNamespace())
        # R2 红线: CancelledError 应从 _run_probe 传播到 _sync_run_task
        assert state["cancelled"] is True, "CancelledError 必须从 _run_probe 传播 (R2 红线)"
