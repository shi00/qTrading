"""ui/views/settings_tabs/data_source_tab.py 声明式契约守护测试 (Phase E.2).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref)
2. 模块级纯函数测试 (_get_page/_build_history_years_options/_render_message/
   _resolve_snack_color/_build_health_summary_content)

业务逻辑覆盖（健康检查 + 同步任务 + 配置保存 + Dialog 流程）由集成测试
（flet_test_page fixture）承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
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
    import ui.views.settings_tabs.data_source_tab as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.settings_tabs.data_source_tab as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (DataSourceTab)
# ============================================================================


class TestDataSourceTabContract:
    """DataSourceTab 声明式契约守护测试 (Phase E.2)。"""

    def test_data_source_tab_is_ft_component(self):
        """DoD: DataSourceTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.data_source_tab import DataSourceTab

        assert hasattr(DataSourceTab, "__wrapped__"), "DataSourceTab 必须用 @ft.component 装饰"

    def test_data_source_tab_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "DataSourceTab 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class DataSourceTab(" not in _code_source(), "DataSourceTab 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def DataSourceTab(...) -> ft.Container。"""
        assert "def DataSourceTab(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

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

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_use_ref_cache(self):
        """DoD: 禁止 use_ref cache 命令式实例。

        NOTE: data_source_tab 使用 use_ref 持久化 tushare_vm 和 task_update_cb,
        但这些是 hook 内部状态持久化, 不是命令式控件实例缓存。
        本测试检查 use_ref 不用于缓存 ft.Control 实例 (命令式模式)。
        """
        # use_ref 用于 VM 持久化是合法的 (hook 模式), 不应禁止
        # 但不应有 use_ref(ft.Container) / use_ref(MetricCard) 等控件缓存
        source = _code_source()
        assert "use_ref(ft." not in source, "不应使用 use_ref 缓存 ft 控件实例"
        assert "use_ref(MetricCard" not in source, "不应使用 use_ref 缓存 MetricCard"
        assert "use_ref(ActionChip" not in source, "不应使用 use_ref 缓存 ActionChip"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "get_observable_state" in _raw_source(), "必须订阅 get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_viewmodel(self):
        """DoD: 必须通过 use_viewmodel hook 消费 DataSourceViewModel。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"
        assert "DataSourceViewModel" in _raw_source(), "必须消费 DataSourceViewModel"

    def test_consumes_tushare_config_panel(self):
        """DoD: 必须函数调用消费 TushareConfigPanel (props 推送)。"""
        assert "TushareConfigPanel(" in _code_source(), "必须函数调用 TushareConfigPanel(vm=...)"

    def test_consumes_health_report_dialog(self):
        """DoD: 必须函数调用消费 HealthReportDialog (props 推送)。"""
        assert "HealthReportDialog(" in _code_source(), "必须函数调用 HealthReportDialog"

    def test_consumes_health_scan_dialog(self):
        """DoD: 必须函数调用消费 HealthScanDialog (props 推送)。"""
        assert "HealthScanDialog(" in _code_source(), "必须函数调用 HealthScanDialog"

    def test_no_on_vm_dispatch_methods(self):
        """DoD: 禁止 _on_vm_* 命令式 dispatch 方法 (VM subscribe 自动重渲染替代)。"""
        source = _code_source()
        assert "_on_vm_state_changed" not in source, "不应使用 _on_vm_state_changed (声明式自动重渲染)"
        assert "_on_vm_show_snack" not in source, "不应使用 _on_vm_show_snack"
        assert "_on_vm_sync_busy_changed" not in source, "不应使用 _on_vm_sync_busy_changed"
        assert "_on_vm_health_checking" not in source, "不应使用 _on_vm_health_checking"
        assert "_on_vm_health_result" not in source, "不应使用 _on_vm_health_result"
        assert "_on_vm_health_error" not in source, "不应使用 _on_vm_health_error"
        assert "_on_vm_health_cancelled" not in source, "不应使用 _on_vm_health_cancelled"
        assert "_on_vm_health_finished" not in source, "不应使用 _on_vm_health_finished"
        assert "_on_vm_init_sync_started" not in source, "不应使用 _on_vm_init_sync_started"
        assert "_on_vm_init_sync_reset" not in source, "不应使用 _on_vm_init_sync_reset"
        assert "_on_vm_progress_update" not in source, "不应使用 _on_vm_progress_update"
        assert "_on_vm_cache_cleared" not in source, "不应使用 _on_vm_cache_cleared"

    def test_no_set_value_set_label_set_text_set_loading(self):
        """DoD: 禁止命令式 set_value/set_label/set_text/set_loading (props 推送替代)。"""
        source = _code_source()
        assert ".set_value(" not in source, "不应使用 set_value (声明式用 props 推送)"
        assert ".set_label(" not in source, "不应使用 set_label (声明式用 props 推送)"
        assert ".set_text(" not in source, "不应使用 set_text (声明式用 props 推送)"
        assert ".set_loading(" not in source, "不应使用 set_loading (声明式用 is_loading prop)"

    def test_no_show_dialog_pop_dialog(self):
        """DoD: 禁止命令式 page.show_dialog/pop_dialog (用 ft.use_dialog 条件渲染)。"""
        source = _code_source()
        assert ".show_dialog(" not in source, "不应使用 page.show_dialog (声明式用 ft.use_dialog)"
        assert ".pop_dialog(" not in source, "不应使用 page.pop_dialog (声明式用条件渲染)"

    def test_uses_use_dialog(self):
        """DoD: 必须使用 ft.use_dialog() 声明式挂载 dialog。"""
        assert "ft.use_dialog" in _code_source(), "必须使用 ft.use_dialog() 声明式挂载 dialog"

    def test_uses_use_effect_for_dual_track(self):
        """DoD: 必须使用 use_effect 处理双轨字段 (version 依赖)。"""
        source = _code_source()
        assert "use_effect" in source, "必须使用 use_effect 处理双轨字段"
        assert "state.snack_version" in source, "必须订阅 snack_version"
        assert "state.health_result_version" in source, "必须订阅 health_result_version"
        assert "state.cache_cleared_version" in source, "必须订阅 cache_cleared_version"
        assert "state.health_error_version" in source, "必须订阅 health_error_version"

    def test_no_page_ref_param(self):
        """DoD: DataSourceTab 签名不应包含 page_ref 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.settings_tabs.data_source_tab import DataSourceTab

        sig = inspect.signature(DataSourceTab.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page_ref" not in params, "DataSourceTab 不应接收 page_ref 参数"
        assert "show_snack_callback" in params, "DataSourceTab 必须接收 show_snack_callback"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.views.settings_tabs.data_source_tab import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.settings_tabs.data_source_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)  # noqa: B010
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.views.settings_tabs.data_source_tab import _get_page

        with patch("ui.views.settings_tabs.data_source_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))  # noqa: B010
            assert _get_page() is None


class TestBuildHistoryYearsOptions:
    """_build_history_years_options 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.settings_tabs.data_source_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_five_options(self):
        """返回 5 个年限选项 (1-5 年)。"""
        from ui.views.settings_tabs.data_source_tab import _build_history_years_options

        options = _build_history_years_options()
        assert len(options) == 5

    def test_option_keys_match_years(self):
        """每个 Option 的 key 对应年限数字。"""
        from ui.views.settings_tabs.data_source_tab import _build_history_years_options

        options = _build_history_years_options()
        keys = [opt.key for opt in options]
        assert keys == ["1", "2", "3", "4", "5"]

    def test_options_are_dropdown_option_instances(self):
        """返回值必须是 ft.dropdown.Option 实例。"""
        from ui.views.settings_tabs.data_source_tab import _build_history_years_options

        options = _build_history_years_options()
        for opt in options:
            assert isinstance(opt, ft.dropdown.Option)


class TestRenderMessage:
    """_render_message 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.settings_tabs.data_source_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_empty_for_none(self):
        """None message 返回空字符串。"""
        from ui.views.settings_tabs.data_source_tab import _render_message

        assert _render_message(None) == ""

    def test_returns_translated_text_for_message(self):
        """Message 返回 I18n.get 翻译文本。"""
        from ui.views.settings_tabs.data_source_tab import _render_message
        from ui.viewmodels import Message

        msg = Message("test_key", {"param": "value"})
        result = _render_message(msg)
        assert "translated_test_key" in result


class TestResolveSnackColor:
    """_resolve_snack_color 模块级纯函数测试。"""

    def test_returns_success_color(self):
        """success 映射到 AppColors.SUCCESS。"""
        from ui.theme import AppColors
        from ui.views.settings_tabs.data_source_tab import _resolve_snack_color

        assert _resolve_snack_color("success") == AppColors.SUCCESS

    def test_returns_error_color(self):
        """error 映射到 AppColors.ERROR。"""
        from ui.theme import AppColors
        from ui.views.settings_tabs.data_source_tab import _resolve_snack_color

        assert _resolve_snack_color("error") == AppColors.ERROR

    def test_returns_warning_color(self):
        """warning 映射到 AppColors.WARNING。"""
        from ui.theme import AppColors
        from ui.views.settings_tabs.data_source_tab import _resolve_snack_color

        assert _resolve_snack_color("warning") == AppColors.WARNING

    def test_returns_info_color_for_unknown(self):
        """未知 color_name 映射到 AppColors.INFO (默认)。"""
        from ui.theme import AppColors
        from ui.views.settings_tabs.data_source_tab import _resolve_snack_color

        assert _resolve_snack_color("unknown") == AppColors.INFO
        assert _resolve_snack_color("") == AppColors.INFO


class TestBuildHealthSummaryContent:
    """_build_health_summary_content 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.settings_tabs.data_source_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_column_control(self):
        """返回 ft.Column 实例。"""
        from ui.views.settings_tabs.data_source_tab import _build_health_summary_content

        result = _build_health_summary_content({"market": {}, "details": {}})
        assert isinstance(result, ft.Column)

    def test_includes_sys_text_row(self):
        """包含系统状态文本行。"""
        from ui.views.settings_tabs.data_source_tab import _build_health_summary_content

        result = _build_health_summary_content(
            {
                "market": {"lag_days": 2},
                "details": {"financial_coverage": 85.5},
            }
        )
        # Column 应包含至少 2 个 Row (sys_text + integrity_items)
        assert len(result.controls) >= 2

    def test_shows_critical_warning_when_missing_critical(self):
        """missing_critical > 0 时显示警告。"""
        from ui.views.settings_tabs.data_source_tab import _build_health_summary_content

        result = _build_health_summary_content(
            {
                "market": {"lag_days": 0},
                "details": {
                    "missing_critical": 3,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                    "financial_coverage": 50.0,
                },
            }
        )
        # 第二个 Row 是 integrity_items
        integrity_row = result.controls[1]
        assert isinstance(integrity_row, ft.Row)
        # 应包含图标 + 文本 (critical warning)
        assert len(integrity_row.controls) >= 2
