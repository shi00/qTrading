"""ui/views/settings_tabs/system_tab.py 声明式契约守护测试 (Phase D.3).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref)
2. 模块级纯函数测试 (_build_language_options/_build_theme_options/
   _build_log_level_options/_get_page)
3. 组件体渲染测试 (控件树结构 + TierApiPanel 消费)
4. 事件处理器测试 (on_select/on_change/on_click → page.run_task)
5. async handler 测试 (ConfigHandler 读写 + 异常路径 + R2 CancelledError)
"""

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    attach_fake_page,
    make_component,
    render_once,
)
from flet.components.component import Component

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
    import ui.views.settings_tabs.system_tab as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.settings_tabs.system_tab as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (SystemTab)
# ============================================================================


class TestSystemTabContract:
    """SystemTab 声明式契约守护测试 (Phase D.3)。"""

    def test_system_tab_is_ft_component(self):
        """DoD: SystemTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.system_tab import SystemTab

        assert hasattr(SystemTab, "__wrapped__"), "SystemTab 必须用 @ft.component 装饰"

    def test_system_tab_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "SystemTab 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class SystemTab(" not in _code_source(), "SystemTab 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def SystemTab(...) -> ft.Container。"""
        assert "def SystemTab(" in _code_source(), "必须是函数定义"
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
        """DoD: 禁止 use_ref cache 命令式实例。"""
        assert "ft.use_ref" not in _code_source(), "不应直接使用 ft.use_ref"

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
        """DoD: 必须通过 use_viewmodel hook 消费 SystemViewModel。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"
        assert "SystemViewModel" in _raw_source(), "必须消费 SystemViewModel"

    def test_consumes_tier_api_panel(self):
        """DoD: 必须函数调用消费 TierApiPanel (props 推送)。"""
        assert "TierApiPanel(" in _code_source(), "必须函数调用 TierApiPanel(system_vm)"

    def test_no_page_ref_param(self):
        """DoD: SystemTab 签名不应包含 page_ref 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.settings_tabs.system_tab import SystemTab

        sig = inspect.signature(SystemTab.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page_ref" not in params, "SystemTab 不应接收 page_ref 参数"
        assert "show_snack_callback" in params, "SystemTab 必须接收 show_snack_callback"


# ============================================================================
# R2 CancelledError 传播契约 (CLAUDE.md §3 红线 R2)
# ============================================================================


class TestR2CancelledErrorPropagation:
    """R2 红线: asyncio.CancelledError 必须显式 raise, 不被 except Exception 吞没。"""

    def test_has_cancelled_error_guard(self):
        """DoD: 含 await 的 async handler 必须有 except asyncio.CancelledError: raise。"""
        code = _raw_source()
        assert "except asyncio.CancelledError:" in code, "必须有 CancelledError 捕获"
        assert "raise  # R2" in code, "CancelledError 必须 raise (R2)"

    def test_cancelled_error_guard_count_meets_threshold(self):
        """DoD: system_tab 含 8 个 async handler, CancelledError 守卫应 >= 8 处。"""
        code = _raw_source()
        guard_count = code.count("except asyncio.CancelledError:")
        assert guard_count >= 8, f"R2 违规: system_tab 应至少 8 处 CancelledError 守卫, 实际 {guard_count}"

    def test_no_bare_exception_swallows_cancelled_error(self):
        """DoD: 外层 async handler 的 except Exception 前必须有 CancelledError 守卫。

        system_tab 有 1 个内层同步 except Exception (locale_configuration 设置, 无 await),
        不需要 CancelledError 守卫。因此守卫数 >= except Exception 数 - 1。
        """
        code = _raw_source()
        except_exception_count = code.count("except Exception")
        cancelled_guard_count = code.count("except asyncio.CancelledError")
        # 允许 1 个内层同步 except Exception (无 await, 不需守卫)
        assert cancelled_guard_count >= except_exception_count - 1, (
            f"R2 违规: {except_exception_count} 处 except Exception 但仅 {cancelled_guard_count} 处 CancelledError 守卫"
        )


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestBuildLanguageOptions:
    """_build_language_options 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get_language_options.return_value = [
            ("zh_CN", "中文"),
            ("en_US", "English"),
        ]
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_option_per_language(self):
        """返回的 Option 数量与 I18n.get_language_options 一致。"""
        from ui.views.settings_tabs.system_tab import _build_language_options

        options = _build_language_options()
        assert len(options) == 2

    def test_option_keys_match_locale_codes(self):
        """每个 Option 的 key 对应 locale code。"""
        from ui.views.settings_tabs.system_tab import _build_language_options

        options = _build_language_options()
        keys = [opt.key for opt in options]
        assert keys == ["zh_CN", "en_US"]

    def test_options_are_dropdown_option_instances(self):
        """返回值必须是 ft.dropdown.Option 实例。"""
        from ui.views.settings_tabs.system_tab import _build_language_options

        options = _build_language_options()
        for opt in options:
            assert isinstance(opt, ft.dropdown.Option)


class TestBuildThemeOptions:
    """_build_theme_options 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_four_themes(self):
        """返回 4 个主题选项 (dark/light/navy/dracula)。"""
        from ui.views.settings_tabs.system_tab import _build_theme_options

        options = _build_theme_options()
        assert len(options) == 4

    def test_option_keys_match_theme_names(self):
        """每个 Option 的 key 对应 ThemeName 常量。"""
        from ui.theme import ThemeName

        from ui.views.settings_tabs.system_tab import _build_theme_options

        options = _build_theme_options()
        keys = [opt.key for opt in options]
        assert keys == [ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA]

    def test_option_text_uses_i18n_label(self):
        """Option 的 text 来自 I18n.get(f'theme_{name}')。"""
        from ui.views.settings_tabs.system_tab import _build_theme_options

        options = _build_theme_options()
        expected_keys = ["theme_dark", "theme_light", "theme_navy", "theme_dracula"]
        for opt, key in zip(options, expected_keys, strict=True):
            assert opt.text == f"translated_{key}"


class TestBuildLogLevelOptions:
    """_build_log_level_options 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_four_levels(self):
        """返回 4 个日志级别选项 (DEBUG/INFO/WARNING/ERROR)。"""
        from ui.views.settings_tabs.system_tab import _build_log_level_options

        options = _build_log_level_options()
        assert len(options) == 4

    def test_option_keys_match_log_levels(self):
        """每个 Option 的 key 对应日志级别常量。"""
        from ui.views.settings_tabs.system_tab import _build_log_level_options

        options = _build_log_level_options()
        keys = [opt.key for opt in options]
        assert keys == ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_option_text_uses_i18n_label(self):
        """Option 的 text 来自 I18n.get(f'sys_opt_{level_lower}')。"""
        from ui.views.settings_tabs.system_tab import _build_log_level_options

        options = _build_log_level_options()
        expected_keys = ["sys_opt_debug", "sys_opt_info", "sys_opt_warn", "sys_opt_error"]
        for opt, key in zip(options, expected_keys, strict=True):
            assert opt.text == f"translated_{key}"


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.views.settings_tabs.system_tab import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.settings_tabs.system_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.views.settings_tabs.system_tab import _get_page

        with patch("ui.views.settings_tabs.system_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


# ============================================================================
# 组件体渲染测试 (SystemTab @ft.component body)
# ============================================================================


class _FakeSystemVM:
    """模拟 SystemViewModel, 满足 use_viewmodel hook 契约。"""

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树, 返回所有控件列表。

    遇到 @ft.component 子组件实例 (如 DashboardCard) 时, 调 render_once
    渲染后深入 (子组件调用返回 Component 实例, 需渲染才能获取内部控件)。
    """
    result: list[Any] = []
    if root is None:
        return result
    if isinstance(root, Component):
        try:
            root = render_once(root)
        except Exception:  # noqa: BLE001
            return result
    if not isinstance(root, ft.Control):
        return result
    result.append(root)
    for attr in ("controls", "content"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for c in children:
                if c is not None:
                    result.extend(_walk_controls(c))
        elif isinstance(children, ft.Control):
            result.extend(_walk_controls(children))
    return result


def _make_change_event(value: str) -> Any:
    """构造 on_change 事件 mock。"""
    mock_event = MagicMock()
    mock_event.control.value = value
    return mock_event


def _configure_config_handler(mock_ch: Any) -> None:
    """设置 ConfigHandler mock 的默认返回值。"""
    mock_ch.get_locale.return_value = "zh_CN"
    mock_ch.get_theme_name.return_value = "dark"
    mock_ch.get_sync_max_concurrent_heavy.return_value = 4
    mock_ch.get_log_level.return_value = "INFO"
    mock_ch.get_db_connection_pool_size.return_value = 5
    mock_ch.get_db_max_overflow.return_value = 10
    mock_ch.get_db_pool_timeout.return_value = 30
    mock_ch.get_max_io_workers.return_value = 8
    mock_ch.get_max_cpu_workers.return_value = 4
    mock_ch.get_no_proxy_domains.return_value = []


def _configure_i18n(mock_i18n: Any) -> None:
    """设置 I18n mock 的默认返回值。"""
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    mock_i18n.get_language_options.return_value = [("zh_CN", "中文"), ("en_US", "English")]
    mock_i18n.get_language_label.return_value = "语言"
    mock_i18n.current_locale.return_value = "zh_CN"
    mock_i18n.set_locale = MagicMock()


@pytest.fixture
def system_tab_env(mock_i18n, mock_app_colors_state):
    """渲染 SystemTab 所需环境: mock ConfigHandler/SystemViewModel/TierApiPanel/I18n/ThreadPoolManager。

    Yields:
        (module, mock_config_handler, mock_i18n, fake_vm)
    """
    from ui.views.settings_tabs import system_tab as mod

    fake_vm = _FakeSystemVM()
    _configure_i18n(mock_i18n)
    # Task 5.2: ConfigHandler/ThreadPoolManager 下沉到 SystemSettingsViewModel,
    # patch 目标改为 VM 模块 (View 不再直接持有这两个符号)
    from ui.viewmodels import system_settings_view_model as settings_vm_mod

    mock_config = MagicMock()
    mock_tpm_class = MagicMock()
    _configure_config_handler(mock_config)
    patches = [
        patch.object(mod, "SystemViewModel", return_value=fake_vm),
        patch.object(mod, "TierApiPanel", return_value=MagicMock(name="TierApiPanel")),
        patch.object(mod, "I18n", mock_i18n),
        patch.object(mod, "UILogger"),
        patch.object(mod, "DataSanitizer"),
        patch.object(settings_vm_mod, "ConfigHandler", mock_config),
        patch.object(settings_vm_mod, "ThreadPoolManager", mock_tpm_class),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield mod, mock_config, mock_i18n, fake_vm


def _render_with_page(mod: Any, page: Any, show_snack: Any = None) -> Any:
    """渲染 SystemTab 组件, 返回控件树根节点。

    只渲染一次 (run_mount_effects 内部已调 render_once), 避免重复渲染
    导致子组件函数调用 (如 TierApiPanel) 被执行多次。
    """
    if show_snack is None:
        show_snack = MagicMock()
    component = make_component(mod.SystemTab, show_snack_callback=show_snack)
    page = attach_fake_page(component, page)
    result = render_once(component)
    component._state.mounted = True
    component._run_mount_effects()
    return result


def _get_tpm_mock(mod: Any) -> Any:
    """获取 ThreadPoolManager mock 实例 (mod.ThreadPoolManager 的返回值)。"""
    return mod.ThreadPoolManager()


class TestSystemTabComponentBody:
    """SystemTab 组件体渲染测试: 验证控件树结构 + VM 生命周期。"""

    def test_mount_returns_container(self, system_tab_env):
        """挂载 SystemTab 返回 ft.Container。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        result = _render_with_page(mod, page)
        assert isinstance(result, ft.Container)

    def test_render_contains_language_dropdown(self, system_tab_env):
        """渲染的控件树含语言 Dropdown (value=zh_CN)。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        result = _render_with_page(mod, page)
        dropdowns = [c for c in _walk_controls(result) if isinstance(c, ft.Dropdown)]
        # 语言/主题/日志级别 3 个 dropdown
        assert len(dropdowns) >= 3
        lang_dropdowns = [d for d in dropdowns if d.value == "zh_CN"]
        assert len(lang_dropdowns) == 1

    def test_render_contains_concurrency_input(self, system_tab_env):
        """渲染的控件树含并发数 TextField (value='4')。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        result = _render_with_page(mod, page)
        text_fields = [c for c in _walk_controls(result) if isinstance(c, ft.TextField)]
        concurrency_inputs = [t for t in text_fields if t.value == "4"]
        assert len(concurrency_inputs) >= 1

    def test_render_contains_diagnostics_button(self, system_tab_env):
        """渲染的控件树含诊断导出 Button (DOWNLOAD_ROUNDED icon)。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        result = _render_with_page(mod, page)
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        diag_buttons = [b for b in buttons if getattr(b, "icon", None) == ft.Icons.DOWNLOAD_ROUNDED]
        assert len(diag_buttons) == 1
        assert diag_buttons[0].disabled is False

    def test_render_contains_save_icon_buttons(self, system_tab_env):
        """渲染的控件树含 4 个保存 IconButton (SAVE_ROUNDED icon)。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        result = _render_with_page(mod, page)
        ctrls = _walk_controls(result)
        save_btns = [
            c for c in ctrls if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.SAVE_ROUNDED
        ]
        # 并发/线程池/DB池/no-proxy 4 个保存按钮
        assert len(save_btns) == 4

    def test_consumes_tier_api_panel_with_system_vm(self, system_tab_env):
        """TierApiPanel 接收 system_vm 实例。"""
        mod, _, _, fake_vm = system_tab_env
        page = FakePage()
        _render_with_page(mod, page)
        mod.TierApiPanel.assert_called_once_with(fake_vm)

    def test_creates_system_vm_via_factory(self, system_tab_env):
        """挂载时通过 factory 实例化 SystemViewModel。"""
        mod, _, _, _ = system_tab_env
        page = FakePage()
        _render_with_page(mod, page)
        mod.SystemViewModel.assert_called_once()
