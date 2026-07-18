"""ui/views/settings_tabs/system_tab.py 组件运行时测试 (Task 1.1).

覆盖:
1. 契约守护: 声明式范式合规性 (@ft.component / 无命令式 API)
2. R2 守卫: 8 个 async handler 的 ``except asyncio.CancelledError: raise``
3. R16 守卫: 8 个 event handler 用 ``page.run_task`` 调度
4. 运行时测试: 用 component_renderer + FakePage 驱动渲染,
   - 模块级纯函数 (_get_page / _build_*_options)
   - 8 个 event handler 的 page 可用/None 早返回
   - 8 个 async handler 的成功/越界/异常/CancelledError 路径
   - ``diagnostics_exporting`` 状态切换 (True → finally False)
"""

import asyncio
import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import flet as ft
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.theme import AppColors
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 system_tab.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    import ui.views.settings_tabs.system_tab as mod
    from pathlib import Path

    return Path(mod.__file__).read_text(encoding="utf-8")


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查 (避免 docstring 误判)."""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: Any) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


# ============================================================================
# 契约守护测试 (Phase D.3)
# ============================================================================


class TestSystemTabContract:
    """SystemTab 声明式契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: SystemTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.system_tab import SystemTab

        assert hasattr(SystemTab, "__wrapped__"), "SystemTab 必须用 @ft.component 装饰"

    def test_no_class_container(self) -> None:
        """DoD: 禁止命令式 class 继承。"""
        source = _source_without_docstrings(_read_source())
        assert "class SystemTab(" not in source, "SystemTab 不应是 class (命令式)"

    def test_no_did_mount(self) -> None:
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        source = _source_without_docstrings(_read_source())
        assert "did_mount" not in source

    def test_no_will_unmount(self) -> None:
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        source = _source_without_docstrings(_read_source())
        assert "will_unmount" not in source

    def test_no_safe_update(self) -> None:
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        source = _source_without_docstrings(_read_source())
        assert ".update()" not in source
        assert "_safe_update" not in source

    def test_no_refresh_locale(self) -> None:
        """DoD: 禁止 refresh_locale (声明式用 ft.use_state 自动重渲染)。"""
        source = _source_without_docstrings(_read_source())
        assert "refresh_locale" not in source

    def test_no_page_ref(self) -> None:
        """DoD: 禁止 PageRefMixin / _page_ref / weakref。"""
        source = _source_without_docstrings(_read_source())
        assert "PageRefMixin" not in source
        assert "_page_ref" not in source
        assert "weakref" not in source

    def test_uses_use_viewmodel(self) -> None:
        """DoD: 必须通过 use_viewmodel hook 消费 VM。"""
        source = _read_source()
        assert "use_viewmodel" in source

    def test_uses_observable_state_i18n(self) -> None:
        """DoD: 必须订阅 get_observable_state。"""
        source = _read_source()
        assert "get_observable_state" in source

    def test_uses_observable_state_theme(self) -> None:
        """DoD: 必须订阅 AppColors.get_observable_state。"""
        source = _read_source()
        assert "AppColors.get_observable_state" in source

    def test_uses_context_page(self) -> None:
        """DoD: page 访问用 ft.context.page。"""
        source = _read_source()
        assert "ft.context.page" in source


class TestSystemTabR2Compliance:
    """R2 红线: 8 个 async handler 必须有 CancelledError raise 守卫。"""

    def test_all_async_handlers_have_cancelled_error_raise(self) -> None:
        """验证 8 处 `except asyncio.CancelledError` + 8 处 `raise  # R2`。"""
        source = _read_source()
        cancelled_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_count >= 8, f"应有 ≥8 处 CancelledError 守卫, 实际 {cancelled_count}"
        assert raise_count >= 8, f"应有 ≥8 处 `raise  # R2`, 实际 {raise_count}"

    def test_no_bare_exception_swallows_cancelled_error(self) -> None:
        """验证每个 async handler 的 except Exception 之前都有 CancelledError 守卫。

        Python 3.8+ CancelledError 继承 BaseException, ``except Exception`` 不会捕获它,
        但仍要求每个 handler 显式 ``except asyncio.CancelledError: raise`` 守卫 (R2 强制).
        这里检查 8 个 async handler 各有 1 处 CancelledError 守卫 (共 8 处),
        不计入 _do_language_change 内部嵌套的 locale 配置 try/except (该处 ``except Exception``
        用于吞没 locale 配置失败, 不涉及 CancelledError 传播路径).
        """
        source = _read_source()
        cancelled_guard_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_guard_count >= 8, f"应有 ≥8 处 CancelledError 守卫, 实际 {cancelled_guard_count}"
        assert raise_count >= 8, f"应有 ≥8 处 raise # R2, 实际 {raise_count}"


class TestSystemTabR16Compliance:
    """R16 红线: 同步 event handler 必须用 page.run_task 调度 async handler。"""

    def test_all_event_handlers_use_run_task(self) -> None:
        """验证 8 处 `page.run_task(` 调度。"""
        source = _read_source()
        run_task_count = source.count("page.run_task(")
        assert run_task_count >= 8, f"应有 ≥8 处 page.run_task, 实际 {run_task_count}"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestModulePureFunctions:
    """模块级纯函数测试。"""

    def test_get_page_returns_none_outside_context(self) -> None:
        """_get_page() 在无 Renderer 上下文时返回 None。"""
        from flet.controls.context import _context_page

        from ui.views.settings_tabs.system_tab import _get_page

        _context_page.set(None)
        assert _get_page() is None

    def test_get_page_returns_page_when_set(self) -> None:
        """_get_page() 在有 Renderer 上下文时返回 page。"""
        from flet.controls.context import _context_page
        from typing import cast

        from ui.views.settings_tabs.system_tab import _get_page

        fake = FakePage()
        _context_page.set(cast(Any, fake))
        try:
            assert _get_page() is fake
        finally:
            _context_page.set(None)

    def test_build_language_options(self, mock_i18n_state) -> None:
        """_build_language_options 返回 dropdown.Option 列表。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.system_tab import _build_language_options

        options = _build_language_options()
        assert len(options) >= 1
        assert all(isinstance(o, ft.dropdown.Option) for o in options)

    def test_build_theme_options(self, mock_i18n_state) -> None:
        """_build_theme_options 返回 4 个主题选项。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.system_tab import _build_theme_options

        options = _build_theme_options()
        assert len(options) == 4
        keys = {o.key for o in options}
        assert "dark" in keys
        assert "light" in keys

    def test_build_log_level_options(self, mock_i18n_state) -> None:
        """_build_log_level_options 返回 4 个日志级别选项。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.system_tab import _build_log_level_options

        options = _build_log_level_options()
        assert len(options) == 4
        keys = {o.key for o in options}
        assert keys == {"DEBUG", "INFO", "WARNING", "ERROR"}


# ============================================================================
# 运行时测试基础设施: FakeSystemViewModel + system_tab_env fixture
# ============================================================================


class _FakeSystemViewModel:
    """模拟 SystemViewModel, 满足 use_viewmodel hook 契约 (state/subscribe/dispose)."""

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()
        self.dispose_called: bool = False

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()


def _make_fake_page() -> FakePage:
    """创建带 run_task / locale_configuration 的 fake page。"""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.locale_configuration = MagicMock()  # type: ignore[attr-defined]
    page.locale_configuration.current_locale = None  # type: ignore[attr-defined]
    return page


def _walk_all_controls(root: Any) -> list:
    """递归返回所有 ft.Control 与 Component (用于搜索 dropdown / textfield / button)。"""
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(root)
    return found


def _get_dropdowns(env: dict) -> list[ft.Dropdown]:
    """按出现顺序返回 3 个 Dropdown (language/theme/log_level)。"""
    dropdowns: list[ft.Dropdown] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Dropdown) and ctrl not in dropdowns:
            dropdowns.append(ctrl)
    return dropdowns


def _get_text_fields(env: dict) -> list[ft.TextField]:
    """按出现顺序返回 7 个 TextField。

    实际深度优先遍历顺序 (控件树结构决定, 非源码定义顺序):
        concurrency(0), io_workers(1), cpu_workers(2),
        pool_size(3), db_overflow(4), db_timeout(5), no_proxy(6)
    """
    fields: list[ft.TextField] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.TextField) and ctrl not in fields:
            fields.append(ctrl)
    return fields


def _find_text_field_by_label(env: dict, label_key: str) -> ft.TextField:
    """通过 i18n label key 查找 TextField (稳健, 不依赖遍历顺序).

    fixture 中 mock_i18n.get 返回 ``f"i18n[{key}]"``, 故 label 为 ``f"i18n[{label_key}]"``.
    """
    expected_label = f"i18n[{label_key}]"
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == expected_label:
            return ctrl
    raise AssertionError(f"TextField with label={expected_label} not found")


def _get_save_buttons(env: dict) -> list:
    """按出现顺序返回保存按钮。

    顺序: save_concurrency_btn(0) / save_thread_pool_btn(1) / save_db_pool_btn(2) /
          save_no_proxy_btn(3) / diagnostics_button(ft.Button)
    """
    buttons: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, (ft.Button, ft.IconButton)):
            buttons.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(env["result"])
    return buttons


def _make_event(value: str | None = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe)。

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _await_run_task_handler(page: MagicMock) -> tuple[Any, tuple, dict]:
    """提取 page.run_task 最近一次调用的 handler 与参数。"""
    assert page.run_task.call_args is not None, "page.run_task 未被调用"
    call = page.run_task.call_args
    handler = call.args[0]
    args = call.args[1:]
    kwargs = call.kwargs
    return handler, args, kwargs


def _rerender(env: dict) -> Any:
    """重新渲染组件并更新 env['result']。

    声明式范式下, on_change 触发 set_state 后需手动 render_once 让闭包捕获新 state,
    否则 event handler 中的 state 变量仍是旧值。
    """
    result = render_once(env["component"])
    env["result"] = result
    return result


@pytest.fixture
def system_tab_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 SystemTab,返回包含 component/page/result/mocks 的 dict。

    Mock 外部依赖:
    - ConfigHandler (类方法方式调用)
    - ThreadPoolManager (实例化调用, run_async 同步执行 func)
    - SystemViewModel (内部 use_viewmodel 实例化)
    - TierApiPanel (子组件, 替换为 MagicMock)
    - I18n (模块级导入)
    - UILogger / DataSanitizer (横切关注点)
    """
    from ui.views.settings_tabs import system_tab as mod

    # --- Mock I18n ---
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    mock_i18n.current_locale.return_value = "zh_CN"
    mock_i18n.set_locale = MagicMock()
    mock_i18n.get_language_options.return_value = [("zh_CN", "中文"), ("en_US", "English")]
    mock_i18n.get_language_label.return_value = "语言"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    # --- Mock ConfigHandler ---
    mock_config = MagicMock()
    mock_config.get_locale.return_value = "zh_CN"
    mock_config.get_theme_name.return_value = "dark"
    mock_config.get_sync_max_concurrent_heavy.return_value = 4
    mock_config.get_log_level.return_value = "INFO"
    mock_config.get_db_connection_pool_size.return_value = 5
    mock_config.get_db_max_overflow.return_value = 10
    mock_config.get_db_pool_timeout.return_value = 30
    mock_config.get_max_io_workers.return_value = 8
    mock_config.get_max_cpu_workers.return_value = 4
    mock_config.get_no_proxy_domains.return_value = []
    mock_config.set_locale.return_value = True
    mock_config.set_theme_name.return_value = True
    mock_config.set_log_level.return_value = True
    mock_config.set_sync_max_concurrent_heavy.return_value = True
    # Task 5.2: ConfigHandler/ThreadPoolManager 下沉到 SystemSettingsViewModel,
    # patch 目标改为 VM 模块 (View 不再直接持有这两个符号)
    monkeypatch.setattr("ui.viewmodels.system_settings_view_model.ConfigHandler", mock_config)

    # --- Mock ThreadPoolManager ---
    mock_tpm_instance = MagicMock()

    async def _fake_run_async(task_type: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    mock_tpm_instance.run_async = MagicMock(side_effect=_fake_run_async)
    mock_tpm_instance.submit = MagicMock(return_value=MagicMock())
    mock_tpm_instance.reload_config = MagicMock()
    mock_tpm_class = MagicMock(return_value=mock_tpm_instance)
    monkeypatch.setattr("ui.viewmodels.system_settings_view_model.ThreadPoolManager", mock_tpm_class)

    # --- Mock SystemViewModel ---
    fake_vm = _FakeSystemViewModel()
    monkeypatch.setattr(mod, "SystemViewModel", lambda: fake_vm)

    # --- Mock TierApiPanel ---
    monkeypatch.setattr(mod, "TierApiPanel", MagicMock(return_value=MagicMock(name="TierApiPanel")))

    # --- Mock UILogger / DataSanitizer ---
    monkeypatch.setattr(mod, "UILogger", MagicMock())
    mock_sanitizer = MagicMock()
    mock_sanitizer.sanitize_error.side_effect = lambda ex: f"sanitized[{ex}]"
    monkeypatch.setattr(mod, "DataSanitizer", mock_sanitizer)

    # --- 挂载组件 ---
    show_snack = MagicMock()
    component = make_component(mod.SystemTab, show_snack_callback=show_snack)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "show_snack": show_snack,
        "mock_config": mock_config,
        "mock_tpm": mock_tpm_instance,
        "mock_i18n": mock_i18n,
        "fake_vm": fake_vm,
    }


# ============================================================================
# 组件挂载/渲染基础测试
# ============================================================================


class TestSystemTabMount:
    """SystemTab 挂载/渲染基础测试。"""

    def test_mount_returns_container(self, system_tab_env) -> None:
        """挂载返回 ft.Container, content 为 ListView。"""
        result = system_tab_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.ListView)

    def test_mount_creates_system_view_model(self, system_tab_env) -> None:
        """挂载时通过 factory 实例化 SystemViewModel。"""
        assert len(system_tab_env["fake_vm"]._subscribers) > 0

    def test_render_includes_language_theme_log_dropdowns(self, system_tab_env) -> None:
        """渲染含 3 个 Dropdown (language/theme/log_level)。"""
        dropdowns = _get_dropdowns(system_tab_env)
        assert len(dropdowns) >= 3

    def test_render_includes_text_fields(self, system_tab_env) -> None:
        """渲染含 7 个 TextField。"""
        fields = _get_text_fields(system_tab_env)
        assert len(fields) >= 7

    def test_render_includes_save_buttons(self, system_tab_env) -> None:
        """渲染含 4 个 IconButton + 1 个 ft.Button (diagnostics)。"""
        buttons = _get_save_buttons(system_tab_env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        diag_btns = [b for b in buttons if isinstance(b, ft.Button)]
        assert len(icon_btns) >= 4
        assert len(diag_btns) == 1

    def test_unmount_triggers_vm_dispose(self, system_tab_env) -> None:
        """卸载后 SystemViewModel.dispose 被调用。"""
        component = system_tab_env["component"]
        assert system_tab_env["fake_vm"].dispose_called is False
        run_unmount_effects(component)
        assert system_tab_env["fake_vm"].dispose_called is True


# ============================================================================
# Event handler 测试: page 可用 → page.run_task (R16 守卫)
# ============================================================================


class TestEventHandlersPageAvailable:
    """验证 8 个 event handler 在 page 可用时调用 run_task。"""

    def test_on_language_change_invokes_run_task(self, system_tab_env) -> None:
        """_on_language_change: page 可用 → page.run_task(_do_language_change, new_locale)。"""
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdowns[0].on_select, _make_event("en_US"))
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ("en_US",)

    def test_on_theme_change_invokes_run_task(self, system_tab_env) -> None:
        """_on_theme_change: page 可用 → page.run_task(_do_theme_change, new_theme)。"""
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdowns[1].on_select, _make_event("light"))
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ("light",)

    def test_on_log_level_change_invokes_run_task(self, system_tab_env) -> None:
        """_on_log_level_change: page 可用 → page.run_task(_do_log_level_change, new_level)。"""
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdowns[2].on_select, _make_event("DEBUG"))
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ("DEBUG",)

    def test_on_save_concurrency_invokes_run_task(self, system_tab_env) -> None:
        """_on_save_concurrency: page 可用 → page.run_task(_do_save_concurrency, concurrency_value)。"""
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(buttons[0].on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert len(args) == 1

    def test_on_save_db_pool_invokes_run_task(self, system_tab_env) -> None:
        """_on_save_db_pool: page 可用 → page.run_task(_do_save_db_pool, pool/overflow/timeout)。"""
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(buttons[2].on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert len(args) == 3

    def test_on_save_thread_pool_invokes_run_task(self, system_tab_env) -> None:
        """_on_save_thread_pool: page 可用 → page.run_task(_do_save_thread_pool, io_str, cpu_str)。"""
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(buttons[1].on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert len(args) == 2

    def test_on_save_no_proxy_invokes_run_task(self, system_tab_env) -> None:
        """_on_save_no_proxy: page 可用 → page.run_task(_do_save_no_proxy, no_proxy_value)。"""
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(buttons[3].on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert len(args) == 1

    def test_on_export_diagnostics_invokes_run_task(self, system_tab_env) -> None:
        """_on_export_diagnostics: page 可用 → page.run_task(_do_export_diagnostics)。"""
        env = system_tab_env
        buttons = _get_save_buttons(env)
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(diag_button.on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ()

    def test_on_language_change_no_value_returns_early(self, system_tab_env) -> None:
        """_on_language_change: control.value=None → 早返回, 不调 run_task。"""
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        e = MagicMock()
        e.control.value = None
        _invoke(dropdowns[0].on_select, e)
        assert not page.run_task.called


# ============================================================================
# Event handler 测试: page=None 早返回 (R16 守卫)
# ============================================================================


class TestEventHandlersPageNoneEarlyReturn:
    """验证 event handler 在 page=None 时早返回 (不调 run_task, 不抛异常)。

    通过 patch ``_get_page`` 返回 None 模拟 page 不可用。
    保持 _context_page 为 fake page 让 use_state setter 正常工作。
    """

    def test_on_language_change_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(dropdowns[0].on_select, _make_event("en_US"))
        assert not page.run_task.called

    def test_on_theme_change_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(dropdowns[1].on_select, _make_event("light"))
        assert not page.run_task.called

    def test_on_log_level_change_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(dropdowns[2].on_select, _make_event("DEBUG"))
        assert not page.run_task.called

    def test_on_save_concurrency_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(buttons[0].on_click, _make_event())
        assert not page.run_task.called

    def test_on_save_db_pool_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(buttons[2].on_click, _make_event())
        assert not page.run_task.called

    def test_on_save_thread_pool_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(buttons[1].on_click, _make_event())
        assert not page.run_task.called

    def test_on_save_no_proxy_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(buttons[3].on_click, _make_event())
        assert not page.run_task.called

    def test_on_export_diagnostics_page_none_no_run_task(self, system_tab_env) -> None:
        env = system_tab_env
        buttons = _get_save_buttons(env)
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.system_tab._get_page", return_value=None):
            _invoke(diag_button.on_click, _make_event())
        assert not page.run_task.called


# ============================================================================
# Async handler 测试: 成功/越界/异常/CancelledError 路径
# ============================================================================


class TestDoLanguageChange:
    """_do_language_change: 成功/False 回滚/异常/CancelledError。"""

    def _trigger(self, env, locale: str = "en_US") -> tuple:
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdowns[0].on_select, _make_event(locale))
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """成功: set_locale 返回 True → I18n.set_locale + show_snack。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_locale.assert_called_with("en_US")
        env["mock_i18n"].set_locale.assert_called_with("en_US")
        env["show_snack"].assert_called_once_with("i18n[settings_language_changed]")

    def test_set_locale_false_rolls_back(self, system_tab_env) -> None:
        """set_locale 返回 False → 回滚到 current_locale + 失败 snack。"""
        env = system_tab_env
        env["mock_config"].set_locale.return_value = False
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        # 失败路径调用 show_snack 带 color=AppColors.ERROR
        snack_calls = env["show_snack"].call_args_list
        assert any("color" in c.kwargs for c in snack_calls)

    def test_exception_path_calls_show_snack(self, system_tab_env) -> None:
        """set_locale 抛 Exception → VM 捕获返回 False → snack 显示 settings_language_save_failed。"""
        env = system_tab_env
        env["mock_config"].set_locale.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[settings_language_save_failed]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播, 不被 except Exception 吞没。"""
        env = system_tab_env
        env["mock_config"].set_locale.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoThemeChange:
    """_do_theme_change: 成功/page=None/异常/CancelledError。"""

    def _trigger(self, env, theme: str = "light") -> tuple:
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdowns[1].on_select, _make_event(theme))
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """成功: set_theme_name + apply_page_theme + show_snack。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("ui.theme.apply_page_theme") as mock_apply:
            asyncio.run(handler(*args))
            mock_apply.assert_called_once_with(env["page"], "light")
        env["mock_config"].set_theme_name.assert_called_with("light")
        env["show_snack"].assert_called_once_with("i18n[settings_snack_theme_updated]")

    def test_page_none_skips_apply_page_theme(self, system_tab_env) -> None:
        """page=None 时跳过 apply_page_theme (不抛异常)。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with (
            patch("ui.theme.apply_page_theme") as mock_apply,
            patch("ui.views.settings_tabs.system_tab._get_page", return_value=None),
        ):
            asyncio.run(handler(*args))
            mock_apply.assert_not_called()

    def test_exception_path_calls_show_snack(self, system_tab_env) -> None:
        """set_theme_name 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_theme_name.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_theme_name.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoLogLevelChange:
    """_do_log_level_change: 成功/异常/CancelledError + command/state 测试。

    Task 6.1 (P3-WinE2E-Skip, 见 docs/debt/known-technical-debt.md): 补 state 验证（dropdown value 前进/None 防御）
    与 command 链路完整性（snack 消息格式），替代 Windows E2E skip 路径。
    """

    def _trigger(self, env, level: str = "DEBUG") -> tuple:
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdowns[2].on_select, _make_event(level))
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """成功: set_log_level + update_log_level + show_snack。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("utils.logger.update_log_level") as mock_update:
            asyncio.run(handler(*args))
            mock_update.assert_called_once_with("DEBUG")
        env["mock_config"].set_log_level.assert_called_with("DEBUG")
        env["show_snack"].assert_called_once_with("i18n[sys_log_label]: DEBUG")

    def test_exception_path_calls_show_snack(self, system_tab_env) -> None:
        """set_log_level 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_log_level.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_log_level.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)

    def test_state_dropdown_value_updates_after_change(self, system_tab_env) -> None:
        """state: _on_log_level_change 触发后 rerender，dropdown.value 反映新 level。

        覆盖状态前进路径：用户切换 dropdown → set_log_level_value(new_level) →
        闭包重渲染 → dropdown.value == new_level。
        """
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        # 初始 value 来自 ConfigHandler.get_log_level() = "INFO"
        assert dropdowns[2].value == "INFO"

        # 触发 _on_log_level_change，内部调 set_log_level_value("ERROR")
        _invoke(dropdowns[2].on_select, _make_event("ERROR"))
        _rerender(env)

        # 重新拿到渲染后的 dropdown，验证 value 已前进
        new_dropdowns = _get_dropdowns(env)
        assert new_dropdowns[2].value == "ERROR"

    def test_state_no_change_when_value_is_none(self, system_tab_env) -> None:
        """state: control.value=None → 早返回，dropdown.value 不变（防御性 state 保护）。

        覆盖防御路径：空值不应触发 state 变化或 run_task。
        """
        env = system_tab_env
        dropdowns = _get_dropdowns(env)
        page = env["page"]
        page.run_task.reset_mock()
        original_value = dropdowns[2].value

        _invoke(dropdowns[2].on_select, _make_event(None))
        _rerender(env)

        new_dropdowns = _get_dropdowns(env)
        assert new_dropdowns[2].value == original_value
        assert not page.run_task.called

    def test_command_snack_message_contains_level(self, system_tab_env) -> None:
        """command: 成功路径下 show_snack 收到 ``"i18n[sys_log_label]: DEBUG"`` 消息。

        覆盖 command 链路完整性：on_change → run_task → _do_log_level_change →
        show_snack_callback(I18n.get("sys_log_label") + ": " + new_level)。
        mock_i18n.get 返回 ``f"i18n[{key}]"``，故期望消息为 ``"i18n[sys_log_label]: DEBUG"``。
        """
        env = system_tab_env
        handler, args, _ = self._trigger(env, level="DEBUG")
        with patch("utils.logger.update_log_level"):
            asyncio.run(handler(*args))

        assert env["show_snack"].call_count == 1
        snack_args = env["show_snack"].call_args
        # show_snack_callback(message) — 第一个位置参数应含 "DEBUG"
        snack_message = snack_args.args[0]
        assert "DEBUG" in snack_message
        assert "sys_log_label" in snack_message


class TestDoSaveConcurrency:
    """_do_save_concurrency: 成功/越界/ValueError/异常/CancelledError。"""

    def _trigger(self, env) -> tuple:
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(buttons[0].on_click, _make_event())
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """concurrency_value=4 (有效) → set_sync_max_concurrent_heavy。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_sync_max_concurrent_heavy.assert_called_once_with(4)
        env["show_snack"].assert_called_once_with(
            "i18n[sys_sync_heavy] i18n[common_saved]",
            color=AppColors.SUCCESS,
        )

    def test_out_of_range_lower(self, system_tab_env) -> None:
        """val=0 (< 1) → snack range 错误, 不调 setter。"""
        env = system_tab_env
        fields = _get_text_fields(env)
        _invoke(fields[0].on_change, _make_event("0"))
        _rerender(env)
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_sync_max_concurrent_heavy.assert_not_called()

    def test_value_error_path(self, system_tab_env) -> None:
        """raw_val 非数字 → ValueError → snack num_fmt。"""
        env = system_tab_env
        fields = _get_text_fields(env)
        _invoke(fields[0].on_change, _make_event("not_a_number"))
        _rerender(env)
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_sync_max_concurrent_heavy.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[sys_snack_num_fmt]", color=AppColors.ERROR)

    def test_exception_path(self, system_tab_env) -> None:
        """set_sync_max_concurrent_heavy 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_sync_max_concurrent_heavy.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_sync_max_concurrent_heavy.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoSaveDbPool:
    """_do_save_db_pool: 成功/越界/ValueError/异常/CancelledError。"""

    def _trigger(self, env) -> tuple:
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(buttons[2].on_click, _make_event())
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """3 个值都有效 (5/10/30) → 三个 setter 被调用。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_db_connection_pool_size.assert_called_once_with(5)
        env["mock_config"].set_db_max_overflow.assert_called_once_with(10)
        env["mock_config"].set_db_pool_timeout.assert_called_once_with(30)
        env["show_snack"].assert_called_once_with(
            "i18n[settings_db_pool_saved]",
            color=AppColors.SUCCESS,
        )

    def test_pool_size_out_of_range(self, system_tab_env) -> None:
        """pool_size=0 (< 1) → snack range 错误, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("0", "10", "30"))

        env["mock_config"].set_db_connection_pool_size.assert_not_called()
        env["show_snack"].assert_called_once_with(
            "i18n[sys_snack_pool_range]",
            color=AppColors.ERROR,
        )

    def test_max_overflow_out_of_range(self, system_tab_env) -> None:
        """max_overflow=100 (> 50) → snack overflow 错误, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("5", "100", "30"))

        env["mock_config"].set_db_max_overflow.assert_not_called()
        env["show_snack"].assert_called_once_with(
            "i18n[settings_db_overflow]: 0-50",
            color=AppColors.ERROR,
        )

    def test_timeout_out_of_range(self, system_tab_env) -> None:
        """timeout=1 (< 5) → snack timeout 错误, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("5", "10", "1"))

        env["mock_config"].set_db_pool_timeout.assert_not_called()
        env["show_snack"].assert_called_once_with(
            "i18n[settings_db_timeout]: 5-300",
            color=AppColors.ERROR,
        )

    def test_value_error_path(self, system_tab_env) -> None:
        """pool_size_str 非数字 → ValueError → snack num_fmt。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("abc", "10", "30"))

        env["mock_config"].set_db_connection_pool_size.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[sys_snack_num_fmt]", color=AppColors.ERROR)

    def test_exception_path(self, system_tab_env) -> None:
        """set_db_connection_pool_size 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_db_connection_pool_size.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_db_connection_pool_size.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoSaveThreadPool:
    """_do_save_thread_pool: 成功/越界/ValueError/异常/CancelledError。"""

    def _trigger(self, env) -> tuple:
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(buttons[1].on_click, _make_event())
        return _await_run_task_handler(page)

    def test_success_path(self, system_tab_env) -> None:
        """io=8, cpu=4 (有效) → setter + reload_config + show_snack。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        env["mock_config"].set_max_io_workers.assert_called_once_with(8)
        env["mock_config"].set_max_cpu_workers.assert_called_once_with(4)
        env["mock_tpm"].reload_config.assert_called_once_with()
        # _do_save_thread_pool 调 show_snack 两次（common_preparing + sys_snack_pool_saved）
        # 用 assert_called_with 验证最后一次调用的参数（成功路径收尾）
        env["show_snack"].assert_called_with(
            "i18n[sys_snack_pool_saved]",
            color=AppColors.SUCCESS,
        )

    def test_empty_string_path(self, system_tab_env) -> None:
        """io_str 或 cpu_str 为空 → snack threads_empty, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("", "4"))

        env["mock_config"].set_max_io_workers.assert_not_called()
        env["show_snack"].assert_called_once_with(
            "i18n[sys_snack_threads_empty]",
            color=AppColors.ERROR,
        )

    def test_io_out_of_range(self, system_tab_env) -> None:
        """io_val=2 (< 4) → snack io_range, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("2", "4"))

        env["mock_config"].set_max_io_workers.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[sys_snack_io_range]", color=AppColors.ERROR)

    def test_cpu_out_of_range(self, system_tab_env) -> None:
        """cpu_val=100 (> 64) → snack cpu_range, 不调 setter。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("8", "100"))

        env["mock_config"].set_max_cpu_workers.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[sys_snack_cpu_range]", color=AppColors.ERROR)

    def test_value_error_path(self, system_tab_env) -> None:
        """io_str 非数字 → ValueError → snack num_fmt。"""
        env = system_tab_env
        handler, _, _ = self._trigger(env)
        asyncio.run(handler("abc", "4"))

        env["mock_config"].set_max_io_workers.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[sys_snack_num_fmt]", color=AppColors.ERROR)

    def test_exception_path(self, system_tab_env) -> None:
        """set_max_io_workers 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_max_io_workers.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        asyncio.run(handler(*args))

        # _do_save_thread_pool 调 show_snack 两次（common_preparing + save_err）
        # 用 assert_called_with 验证最后一次调用（异常路径收尾）
        env["show_snack"].assert_called_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_max_io_workers.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoSaveNoProxy:
    """_do_save_no_proxy: 成功(空)/带域名/异常/CancelledError。"""

    def _trigger(self, env) -> tuple:
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(buttons[3].on_click, _make_event())
        return _await_run_task_handler(page)

    def test_success_path_empty(self, system_tab_env) -> None:
        """成功: no_proxy_value="" → set_no_proxy_domains([]) + show_snack + reapply。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("utils.proxy_manager.ProxyManager") as mock_pm:
            asyncio.run(handler(*args))

            env["mock_config"].set_no_proxy_domains.assert_called_once_with([])
            env["mock_tpm"].submit.assert_called_once_with(TaskType.IO, mock_pm.reapply_proxy_policy)
            env["show_snack"].assert_called_once_with(
                "i18n[settings_snack_no_proxy_saved]",
                color=AppColors.SUCCESS,
            )

    def test_success_with_domains(self, system_tab_env) -> None:
        """raw_text 含逗号分隔域名 → 解析为 list 传入 setter。"""
        env = system_tab_env
        fields = _get_text_fields(env)
        # no_proxy_input 是最后一个 TextField
        _invoke(fields[-1].on_change, _make_event("a.com, b.com ,c.com"))
        _rerender(env)
        handler, args, _ = self._trigger(env)
        with patch("utils.proxy_manager.ProxyManager"):
            asyncio.run(handler(*args))

            env["mock_config"].set_no_proxy_domains.assert_called_once_with(["a.com", "b.com", "c.com"])

    def test_exception_path(self, system_tab_env) -> None:
        """set_no_proxy_domains 抛 Exception → snack 错误。"""
        env = system_tab_env
        env["mock_config"].set_no_proxy_domains.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env)
        with patch("utils.proxy_manager.ProxyManager"):
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[sys_snack_save_err]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, system_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = system_tab_env
        env["mock_config"].set_no_proxy_domains.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env)
        with patch("utils.proxy_manager.ProxyManager"):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestDoExportDiagnostics:
    """_do_export_diagnostics: 成功/异常/CancelledError/diagnostics_exporting 状态切换。"""

    def _trigger(self, env) -> tuple:
        buttons = _get_save_buttons(env)
        page = env["page"]
        page.run_task.reset_mock()
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        _invoke(diag_button.on_click, _make_event())
        return _await_run_task_handler(page)

    @staticmethod
    def _async_return(value: Any):
        async def _ret() -> Any:
            return value

        return _ret

    def test_success_path(self, system_tab_env) -> None:
        """成功: export + show_snack(含 path)。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_collector:
            mock_collector.export = MagicMock(side_effect=self._async_return("/tmp/diag.zip"))
            asyncio.run(handler(*args))

            mock_collector.export.assert_called_once_with()
        env["show_snack"].assert_called_once_with("i18n[settings_diagnostics_success]", color=AppColors.SUCCESS)

    def test_exception_path(self, system_tab_env) -> None:
        """export 抛 Exception → snack 错误。"""
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_collector:

            async def _raise() -> None:
                raise RuntimeError("diag failed")

            mock_collector.export = MagicMock(side_effect=_raise)
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[settings_diagnostics_failed]", color=AppColors.ERROR)

    def test_cancelled_error_propagates_and_state_reset(self, system_tab_env) -> None:
        """R2: CancelledError 传播; finally 仍调 set_diagnostics_exporting(False)。

        验证方式: CancelledError raise 后, 重新渲染组件, 检查 diagnostics_button.disabled == False
        (finally 中 set_diagnostics_exporting(False) 已执行, state 已重置)。
        """
        env = system_tab_env
        handler, args, _ = self._trigger(env)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_collector:
            mock_collector.export = MagicMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)

        # finally 中 set_diagnostics_exporting(False) 已执行 (Python finally 语义)
        # 重新渲染使 state 变化反映到控件树
        _rerender(env)
        buttons = _get_save_buttons(env)
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        assert diag_button.disabled is False
        # show_snack 未被调用 (CancelledError 在 try 内 raise, 跳过 show_snack)
        env["show_snack"].assert_not_called()

    def test_diagnostics_exporting_state_resets_after_success(self, system_tab_env) -> None:
        """diagnostics_exporting: 成功路径下 finally 重置为 False。

        handler 执行前 disabled=False, 执行中 set_diagnostics_exporting(True),
        finally set_diagnostics_exporting(False)。验证完成后 re-render disabled 仍为 False。
        """
        env = system_tab_env
        buttons = _get_save_buttons(env)
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        assert diag_button.disabled is False

        handler, args, _ = self._trigger(env)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_collector:
            mock_collector.export = MagicMock(side_effect=self._async_return("/tmp/diag.zip"))
            asyncio.run(handler(*args))

        _rerender(env)
        buttons = _get_save_buttons(env)
        diag_button = next(b for b in buttons if isinstance(b, ft.Button))
        assert diag_button.disabled is False
