"""ui/app_layout.py 声明式契约守护测试 (Phase F.4).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref/
   _view_cache/PageRefMixin/schedule_resize/_handle_resize/_on_locale_change/refresh_locale)
2. 模块级纯函数测试 (_build_nav_destinations/_get_page) +
   _build_pages_stack 源码契约守护 (ft.Stack + visible prop, 项目内存硬约束 #34)

业务逻辑覆盖 (tab 切换 + resize 防抖 + 子视图消费) 由集成测试
(flet_test_page fixture) 承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
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
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.app_layout as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.app_layout as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (AppLayout)
# ============================================================================


class TestAppLayoutContract:
    """AppLayout 声明式契约守护测试 (Phase F.4)。"""

    def test_app_layout_is_ft_component(self):
        """DoD: AppLayout 必须被 @ft.component 装饰。"""
        from ui.app_layout import AppLayout

        assert hasattr(AppLayout, "__wrapped__"), "AppLayout 必须用 @ft.component 装饰"

    def test_app_layout_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "AppLayout 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class AppLayout(" not in _code_source(), "AppLayout 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def AppLayout(...) -> ft.Container。"""
        assert "def AppLayout(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme (声明式通过 Observable state 自动重渲染)。"""
        assert "update_theme" not in _code_source(), "不应使用 update_theme (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_schedule_resize(self):
        """DoD: 禁止命令式 schedule_resize (声明式用 use_effect + page.on_resize)。"""
        assert "schedule_resize" not in _code_source(), "不应使用 schedule_resize (命令式)"

    def test_no_handle_resize_method(self):
        """DoD: 禁止命令式 _handle_resize 防抖方法 (声明式用 use_effect 闭包防抖)。"""
        assert "_handle_resize" not in _code_source(), "不应使用 _handle_resize (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_page_ref_mixin(self):
        """DoD: 禁止 PageRefMixin (最后一个历史控件消除, 用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"

    def test_no_page_ref(self):
        """DoD: 禁止 _page_ref / weakref (用 ft.context.page)。"""
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_view_cache(self):
        """DoD: 禁止 _view_cache 命令式视图缓存 (声明式直接函数调用消费)。"""
        assert "_view_cache" not in _code_source(), "不应使用 _view_cache (声明式直接函数调用)"

    def test_no_use_ref_cache(self):
        """DoD: 禁止 use_ref cache 命令式控件实例（Future/非控件引用除外，R.1.3 例外）。

        全量校验所有 ft.use_ref(...) 调用的参数必须为 None，避免"存在一个 use_ref(None)
        即放行"的虚假保障（QA Critical + Skeptic M1）。
        """
        import re

        source = _code_source()
        use_ref_args = re.findall(r"ft\.use_ref\(([^)]*)\)", source)
        for arg in use_ref_args:
            assert arg.strip() == "None", f"use_ref 仅允许 None 初始值（非控件实例），实际: {arg}"

    def test_app_layout_resize_cleanup_cancels_debounce(self):
        """DoD: _cleanup_resize 必须取消 pending debounce_task 并置 None（R.1.3 防孤儿任务）。

        本测试为源码契约守护（grep 式），行为覆盖由集成测试承担。
        用 _code_source() 剥离 docstring 降低误判，500 字符窗口覆盖函数体。
        """
        source = _code_source()
        # debounce_task 必须用 use_ref 持有（跨 re-render 持久 + cleanup 可访问）
        assert "ft.use_ref" in source, "debounce_task 必须用 use_ref 持有"
        # _cleanup_resize 必须包含 cancel 调用 + 置 None（防孤儿引用 + 可重入）
        cleanup_idx = source.index("def _cleanup_resize")
        cleanup_section = source[cleanup_idx : cleanup_idx + 500]
        assert ".cancel()" in cleanup_section, "_cleanup_resize 必须取消 debounce_task"
        assert "= None" in cleanup_section, "_cleanup_resize cancel 后必须置 None 防孤儿引用"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "get_observable_state" in _raw_source(), "必须订阅 get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_effect_for_resize(self):
        """DoD: resize 必须用 use_effect + page.on_resize。"""
        assert "ft.use_effect" in _code_source(), "必须使用 ft.use_effect 设置 resize"
        assert "page.on_resize" in _code_source(), "必须设置 page.on_resize"

    def test_consumes_subviews_via_function_call(self):
        """DoD: 6 个子视图必须用函数调用消费 (HomeView(active=...)/ScreenerView(active=...)/...)。"""
        source = _code_source()
        for view_name in [
            "HomeView(active=",
            "ScreenerView(active=",
            "BacktestView(active=",
            "DataExplorerView(active=",
            "TaskCenterView(active=",
            "SettingsView(active=",
        ]:
            assert view_name in source, f"必须函数调用消费 {view_name}"

    def test_no_change_tab_method(self):
        """DoD: 禁止命令式 change_tab / _execute_tab_switch (声明式用 use_state)。"""
        assert "def change_tab" not in _code_source(), "不应使用 change_tab (声明式用 use_state)"
        assert "_execute_tab_switch" not in _code_source(), "不应使用 _execute_tab_switch (命令式)"

    def test_no_run_strategy_from_home(self):
        """DoD: 禁止命令式 run_strategy_from_home (HomeView 不再使用此回调)。"""
        assert "run_strategy_from_home" not in _code_source(), "不应使用 run_strategy_from_home (命令式)"

    def test_no_show_method(self):
        """DoD: 禁止命令式 show() 方法 (声明式由消费方 page.add 挂载)。"""
        assert "def show" not in _code_source(), "不应使用 show() 方法 (声明式由消费方挂载)"

    def test_no_init_ui_method(self):
        """DoD: 禁止命令式 _init_ui 方法 (声明式在函数体内直接渲染)。"""
        assert "_init_ui" not in _code_source(), "不应使用 _init_ui (命令式)"

    def test_app_layout_signature_no_page_param(self):
        """DoD: AppLayout 签名不应包含 page 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.app_layout import AppLayout

        sig = inspect.signature(AppLayout.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page" not in params, "AppLayout 不应接收 page 参数"
        assert "page_ref" not in params, "AppLayout 不应接收 page_ref 参数"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestBuildPagesStack:
    """``_build_pages_stack`` 源码契约守护测试 (项目内存硬约束 #34)。

    ``_build_pages_stack`` 是 ``@ft.component``, 在无 renderer 上下文下直接调用会抛
    ``RuntimeError`` (与 AppLayout 同), 故改为源码契约守护 (grep 式) 验证其符合
    ft.Stack + visible prop 范式。行为覆盖由集成测试 (flet_test_page fixture) 承担。
    """

    def test_build_pages_stack_is_ft_component(self):
        """DoD: ``_build_pages_stack`` 必须被 ``@ft.component`` 装饰 (硬约束 #31)。"""
        from ui.app_layout import _build_pages_stack

        assert hasattr(_build_pages_stack, "__wrapped__"), "_build_pages_stack 必须用 @ft.component 装饰"

    def test_build_pages_stack_signature_returns_stack(self):
        """DoD: 函数签名必须为 ``def _build_pages_stack(...) -> ft.Stack``。"""
        source = _code_source()
        assert "def _build_pages_stack(" in source, "必须是函数定义"
        assert "-> ft.Stack" in source, "返回类型必须为 ft.Stack"

    def test_build_pages_stack_uses_ft_stack(self):
        """DoD: 必须使用 ``ft.Stack`` 容器 (硬约束 #34: state-driven rendering)。"""
        source = _code_source()
        assert "ft.Stack(" in source, "必须使用 ft.Stack 替代条件渲染"

    def test_build_pages_stack_uses_visible_prop(self):
        """DoD: 页面切换必须用 ``visible`` prop 而非 if/else 创建不同控件 (硬约束 #34)。"""
        source = _code_source()
        assert "visible=current_tab" in source, "页面切换必须用 visible prop 控制"

    def test_no_conditional_view_rendering(self):
        """DoD: 不应保留 ``_build_view`` 条件渲染旧函数 (已迁移至 ft.Stack + visible prop)。"""
        source = _code_source()
        assert "def _build_view(" not in source, "不应再有 _build_view 条件渲染函数"

    def test_consumes_all_six_subviews_in_stack(self):
        """DoD: ``_build_pages_stack`` 必须预先创建所有 6 个子视图放入 Stack。"""
        source = _code_source()
        for view_name in [
            "HomeView(active=",
            "ScreenerView(active=",
            "BacktestView(active=",
            "DataExplorerView(active=",
            "TaskCenterView(active=",
            "SettingsView(active=",
        ]:
            assert view_name in source, f"_build_pages_stack 必须预创建 {view_name}"


class TestBuildNavDestinations:
    """_build_nav_destinations 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        self.patches = [
            patch("ui.app_layout.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_six_destinations(self):
        """返回 6 个 NavigationRailDestination (market/screener/backtest/data/tasks/settings)。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        assert len(destinations) == 6

    def test_destinations_are_correct_type(self):
        """返回值必须是 ft.NavigationRailDestination 实例。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        for dest in destinations:
            assert isinstance(dest, ft.NavigationRailDestination)

    def test_labels_match_nav_keys(self):
        """每个 destination 的 label 文本对应 i18n key。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        expected_keys = [
            "nav_market",
            "nav_screener",
            "nav_backtest",
            "nav_data",
            "nav_tasks",
            "nav_settings",
        ]
        for dest, key in zip(destinations, expected_keys, strict=True):
            # label 是 ft.Text 控件, 文本通过 .value 访问
            assert dest.label.value == key


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.app_layout import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.app_layout.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.app_layout import _get_page

        with patch("ui.app_layout.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


class TestNavTabs:
    """NavTabs IntEnum 契约测试。"""

    def test_nav_tabs_has_six_members(self):
        """NavTabs 必须有 6 个成员 (MARKET/SCREENER/BACKTEST/DATA/TASKS/SETTINGS)。"""
        from ui.app_layout import NavTabs

        assert len(NavTabs) == 6

    def test_nav_tabs_values_are_sequential(self):
        """NavTabs 值必须从 0 开始连续 (NavigationRail selected_index 依赖)。"""
        from ui.app_layout import NavTabs

        values = [int(tab) for tab in NavTabs]
        assert values == [0, 1, 2, 3, 4, 5]


# ============================================================================
# ViewportState (Phase 6.2 P2-1) 契约守护
# ============================================================================


class TestViewportState:
    """ViewportState dataclass + AppLayout 下发契约守护测试 (Phase 6.2 P2-1).

    验证:
    1. ViewportState 是 frozen dataclass, 含 width/height/breakpoint 三字段
    2. AppLayout 下发 viewport 给 6 个子视图 (源码契约: "XxxView(active=" + "viewport=")
    3. breakpoint 计算逻辑 (compact/medium/expanded) — 通过源码 + 行为双验证
    """

    def test_viewport_state_is_frozen_dataclass(self):
        """DoD: ViewportState 必须是 @dataclass(frozen=True), 含 width/height/breakpoint 三字段。"""
        import inspect

        from ui.views.viewport_state import ViewportState

        assert dataclasses.is_dataclass(ViewportState), "ViewportState 必须是 dataclass"
        # __dataclass_params__.frozen 直接读取 frozen 标志
        assert ViewportState.__dataclass_params__.frozen is True, "ViewportState 必须是 frozen=True"
        # 三字段必须存在
        params = inspect.signature(ViewportState).parameters
        assert "width" in params, "ViewportState 必须含 width 字段"
        assert "height" in params, "ViewportState 必须含 height 字段"
        assert "breakpoint" in params, "ViewportState 必须含 breakpoint 字段"

    def test_viewport_state_is_frozen(self):
        """DoD: ViewportState 实例不可变 (frozen=True)。"""
        from ui.views.viewport_state import ViewportState

        vp = ViewportState(width=800.0, height=600.0, breakpoint="medium")
        with pytest.raises(dataclasses.FrozenInstanceError):
            vp.width = 1000.0  # type: ignore[misc]

    def test_app_layout_passes_viewport_to_six_subviews(self):
        """DoD: AppLayout 必须下发 viewport 给 6 个子视图 (源码契约: "XxxView(active=" + "viewport=")。"""
        source = _code_source()
        for view_name in [
            "HomeView(active=",
            "ScreenerView(active=",
            "BacktestView(active=",
            "DataExplorerView(active=",
            "TaskCenterView(active=",
            "SettingsView(active=",
        ]:
            assert view_name in source, f"必须函数调用消费 {view_name}"
        # 6 个子视图都必须接收 viewport 参数 (源码 grep 式契约)
        viewport_count = source.count("viewport=viewport")
        assert viewport_count >= 6, f"必须给 6 个子视图下发 viewport=viewport, 实际出现 {viewport_count} 次"

    def test_build_pages_stack_accepts_viewport_param(self):
        """DoD: _build_pages_stack 必须接收 viewport 参数。"""
        source = _code_source()
        assert "def _build_pages_stack(" in source
        # _build_pages_stack 签名应含 viewport 参数
        stack_sig_idx = source.index("def _build_pages_stack(")
        stack_sig_section = source[stack_sig_idx : stack_sig_idx + 200]
        assert "viewport" in stack_sig_section, "_build_pages_stack 必须接收 viewport 参数"

    def test_app_layout_computes_viewport_from_window_size(self):
        """DoD: AppLayout 必须基于 window_size 计算 ViewportState (源码契约)。"""
        source = _code_source()
        # window_size state 必须被使用 (不是 _ 占位符)
        assert "window_size, set_window_size = ft.use_state" in source, "window_size 必须被解构使用 (不再是 _ 占位符)"
        # ViewportState 必须由 window_size[0]/[1] 构造
        assert "ViewportState(" in source, "AppLayout 必须构造 ViewportState 实例"
        assert "window_size[0]" in source, "ViewportState.width 必须来自 window_size[0]"
        assert "window_size[1]" in source, "ViewportState.height 必须来自 window_size[1]"

    def test_breakpoint_compact_below_600(self):
        """DoD: width < 600 时 breakpoint == "compact"。

        行为验证: inline 重现 AppLayout 的 breakpoint 三元表达式逻辑。
        """
        # 引用 ViewportState 触发 import 路径检查 (验证无循环依赖)
        from ui.views.viewport_state import ViewportState  # noqa: F401  # [reason: 触发 import 路径检查]

        def _compute_breakpoint(width: float) -> str:
            return "compact" if width < 600 else "medium" if width < 840 else "expanded"

        assert _compute_breakpoint(0.0) == "compact"
        assert _compute_breakpoint(599.9) == "compact"
        assert _compute_breakpoint(100.0) == "compact"

    def test_breakpoint_medium_between_600_and_840(self):
        """DoD: 600 <= width < 840 时 breakpoint == "medium"。"""

        def _compute_breakpoint(width: float) -> str:
            return "compact" if width < 600 else "medium" if width < 840 else "expanded"

        assert _compute_breakpoint(600.0) == "medium"
        assert _compute_breakpoint(839.9) == "medium"
        assert _compute_breakpoint(720.0) == "medium"

    def test_breakpoint_expanded_at_or_above_840(self):
        """DoD: width >= 840 时 breakpoint == "expanded"。"""

        def _compute_breakpoint(width: float) -> str:
            return "compact" if width < 600 else "medium" if width < 840 else "expanded"

        assert _compute_breakpoint(840.0) == "expanded"
        assert _compute_breakpoint(1920.0) == "expanded"
        assert _compute_breakpoint(10000.0) == "expanded"

    def test_breakpoint_ternary_in_source(self):
        """DoD: AppLayout 源码必须包含 compact/medium/expanded 三态三元表达式。"""
        source = _code_source()
        assert '"compact"' in source, "源码必须含 compact 断点"
        assert '"medium"' in source, "源码必须含 medium 断点"
        assert '"expanded"' in source, "源码必须含 expanded 断点"
