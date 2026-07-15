"""ui/views/settings_tabs/automation_tab.py 组件运行时测试 (Task 1.2).

覆盖:
1. 契约守护: 声明式范式合规性 (@ft.component / 无命令式 API)
2. R2 守卫: 7 个 async handler 的 ``except asyncio.CancelledError: raise``
3. R16 守卫: 7 个 event handler 用 ``page.run_task`` 调度
4. 运行时测试: 用 component_renderer + FakePage 驱动渲染,
   - 模块级纯函数 (_get_page / _build_*_options / _get_schedule_status_text)
   - 7 个 event handler 的 page 可用/None 早返回
   - 7 个 async handler 的 成功/异常/回滚/ValueError/CancelledError 路径
   - auto_enabled/news_enabled 切换 disabled 状态
"""

import asyncio
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

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 automation_tab.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    import ui.views.settings_tabs.automation_tab as mod
    from pathlib import Path

    return Path(mod.__file__).read_text(encoding="utf-8")


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码, 用于契约守护检查 (避免 docstring 误判)."""
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


class TestAutomationTabContract:
    """AutomationTab / NotificationsTab 声明式契约守护测试。"""

    def test_automation_tab_is_ft_component(self) -> None:
        """DoD: AutomationTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.automation_tab import AutomationTab

        assert hasattr(AutomationTab, "__wrapped__"), "AutomationTab 必须用 @ft.component 装饰"

    def test_notifications_tab_is_ft_component(self) -> None:
        """DoD: NotificationsTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.automation_tab import NotificationsTab

        assert hasattr(NotificationsTab, "__wrapped__"), "NotificationsTab 必须用 @ft.component 装饰"

    def test_no_class_container(self) -> None:
        """DoD: 禁止命令式 class 继承。"""
        source = _source_without_docstrings(_read_source())
        assert "class AutomationTab(" not in source
        assert "class NotificationsTab(" not in source

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


class TestAutomationTabR2Compliance:
    """R2 红线: 7 个 async handler 必须有 CancelledError raise 守卫。"""

    def test_all_async_handlers_have_cancelled_error_raise(self) -> None:
        """验证 ≥7 处 `except asyncio.CancelledError` + ≥7 处 `raise  # R2`。

        7 个 async handler:
        - AutomationTab: _do_schedule_toggle / _do_schedule_time_change /
          _do_ai_concept_toggle / _do_ai_concept_time_change / _do_ai_concept_engine_change
        - NotificationsTab: _do_news_toggle / _do_interval_change
        """
        source = _read_source()
        cancelled_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_count >= 7, f"应有 ≥7 处 CancelledError 守卫, 实际 {cancelled_count}"
        assert raise_count >= 7, f"应有 ≥7 处 `raise  # R2`, 实际 {raise_count}"


class TestAutomationTabR16Compliance:
    """R16 红线: 同步 event handler 必须用 page.run_task 调度 async handler。"""

    def test_all_event_handlers_use_run_task(self) -> None:
        """验证 ≥7 处 `page.run_task(` 调度。

        7 个 event handler:
        - AutomationTab: _on_schedule_toggle / _on_schedule_time_change /
          _on_ai_concept_toggle / _on_ai_concept_time_change / _on_ai_concept_engine_change
        - NotificationsTab: _on_news_toggle / _on_interval_change
        """
        source = _read_source()
        run_task_count = source.count("page.run_task(")
        assert run_task_count >= 7, f"应有 ≥7 处 page.run_task, 实际 {run_task_count}"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestModulePureFunctions:
    """模块级纯函数测试。"""

    def test_get_page_returns_none_outside_context(self) -> None:
        """_get_page() 在无 Renderer 上下文时返回 None。"""
        from flet.controls.context import _context_page

        from ui.views.settings_tabs.automation_tab import _get_page

        _context_page.set(None)
        assert _get_page() is None

    def test_get_page_returns_page_when_set(self) -> None:
        """_get_page() 在有 Renderer 上下文时返回 page。"""
        from flet.controls.context import _context_page
        from typing import cast

        from ui.views.settings_tabs.automation_tab import _get_page

        fake = FakePage()
        _context_page.set(cast(Any, fake))
        try:
            assert _get_page() is fake
        finally:
            _context_page.set(None)

    def test_build_time_options(self, mock_i18n_state) -> None:
        """_build_time_options 返回 6 个 dropdown.Option。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.automation_tab import _build_time_options

        options = _build_time_options()
        assert len(options) == 6
        assert all(isinstance(o, ft.dropdown.Option) for o in options)

    def test_build_search_engine_options(self, mock_i18n_state) -> None:
        """_build_search_engine_options 返回 2 个 dropdown.Option。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.automation_tab import _build_search_engine_options

        options = _build_search_engine_options()
        assert len(options) == 2
        keys = {o.key for o in options}
        assert keys == {"search_std", "search_pro"}

    def test_build_interval_options(self, mock_i18n_state) -> None:
        """_build_interval_options 返回 4 个 dropdown.Option。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.automation_tab import _build_interval_options

        options = _build_interval_options()
        assert len(options) == 4
        keys = {o.key for o in options}
        assert keys == {"30", "60", "300", "900"}

    def test_get_schedule_status_text(self, mock_i18n_state) -> None:
        """_get_schedule_status_text 根据 enabled 返回不同 i18n key。"""
        from core.i18n import DEFAULT_LOCALE, I18n

        I18n._locale = DEFAULT_LOCALE
        from ui.views.settings_tabs.automation_tab import _get_schedule_status_text

        assert _get_schedule_status_text(True) == I18n.get("settings_status_auto_on")
        assert _get_schedule_status_text(False) == I18n.get("settings_status_auto_off")


# ============================================================================
# 运行时测试基础设施: 通用辅助函数 + automation_tab_env / notifications_tab_env fixture
# ============================================================================


def _walk_all_controls(root: Any) -> list:
    """递归返回所有 ft.Control 与 Component (用于搜索 dropdown / switch / textfield / button)。"""
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


def _get_switches(env: dict) -> list[ft.Switch]:
    """按出现顺序返回 Switch 列表。"""
    switches: list[ft.Switch] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Switch) and ctrl not in switches:
            switches.append(ctrl)
    return switches


def _get_dropdowns(env: dict) -> list[ft.Dropdown]:
    """按出现顺序返回 Dropdown 列表。"""
    dropdowns: list[ft.Dropdown] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Dropdown) and ctrl not in dropdowns:
            dropdowns.append(ctrl)
    return dropdowns


def _find_switch_by_label(env: dict, label_key: str) -> ft.Switch:
    """通过 i18n label key 查找 Switch (稳健, 不依赖遍历顺序)。

    fixture 中 mock_i18n.get 返回 ``f"i18n[{key}]"``, 故 label 为 ``f"i18n[{label_key}]"``。
    """
    expected_label = f"i18n[{label_key}]"
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Switch) and getattr(ctrl, "label", None) == expected_label:
            return ctrl
    raise AssertionError(f"Switch with label={expected_label} not found")


def _find_dropdown_by_label(env: dict, label_key: str) -> ft.Dropdown:
    """通过 i18n label key 查找 Dropdown (稳健, 不依赖遍历顺序)。

    fixture 中 mock_i18n.get 返回 ``f"i18n[{key}]"``, 故 label 为 ``f"i18n[{label_key}]"``。
    """
    expected_label = f"i18n[{label_key}]"
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == expected_label:
            return ctrl
    raise AssertionError(f"Dropdown with label={expected_label} not found")


def _make_event(value: Any = None) -> MagicMock:
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
    assert page.run_task.called, "page.run_task 未被调用"
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


def _make_fake_page() -> FakePage:
    """创建带 run_task 的 fake page。"""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    return page


def _patch_automation_common_mocks(mod, monkeypatch) -> dict:
    """注入 AutomationTab/NotificationsTab 共用的外部依赖 mock。

    Mock:
    - I18n (模块级导入)
    - ConfigHandler (类方法调用)
    - ThreadPoolManager (实例化 + run_async 同步执行 func)
    - UILogger / DataSanitizer (横切关注点)
    """
    # --- Mock I18n ---
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    mock_i18n.current_locale.return_value = "zh_CN"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    # --- Mock ConfigHandler ---
    mock_config = MagicMock()
    mock_config.is_auto_update_enabled.return_value = False
    mock_config.get_auto_update_time.return_value = "16:30"
    mock_config.is_ai_concept_schedule_enabled.return_value = False
    mock_config.get_ai_concept_schedule_time.return_value = "20:00"
    mock_config.get_ai_concept_search_engine.return_value = "search_std"
    mock_config.get_config.side_effect = lambda key, default=None: {
        "enable_news_alerts": True,
        "news_poll_interval": 60,
    }.get(key, default)
    mock_config.save_config.return_value = True
    mock_config.set_ai_concept_schedule_enabled.return_value = True
    mock_config.set_ai_concept_schedule_time.return_value = True
    mock_config.set_ai_concept_search_engine.return_value = True
    monkeypatch.setattr(mod, "ConfigHandler", mock_config)

    # --- Mock ThreadPoolManager ---
    mock_tpm_instance = MagicMock()

    async def _fake_run_async(task_type: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    mock_tpm_instance.run_async = MagicMock(side_effect=_fake_run_async)
    mock_tpm_class = MagicMock(return_value=mock_tpm_instance)
    monkeypatch.setattr(mod, "ThreadPoolManager", mock_tpm_class)

    # --- Mock UILogger / DataSanitizer (模块级 logger 不直接 mock, 仅留空) ---
    mock_sanitizer = MagicMock()
    mock_sanitizer.sanitize_error.side_effect = lambda ex: f"sanitized[{ex}]"
    monkeypatch.setattr(mod, "DataSanitizer", mock_sanitizer, raising=False)

    return {
        "mock_config": mock_config,
        "mock_tpm": mock_tpm_instance,
        "mock_i18n": mock_i18n,
    }


@pytest.fixture
def automation_tab_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 AutomationTab, 返回包含 component/page/result/mocks 的 dict。"""
    from ui.views.settings_tabs import automation_tab as mod

    mocks = _patch_automation_common_mocks(mod, monkeypatch)

    show_snack = MagicMock()
    component = make_component(mod.AutomationTab, show_snack_callback=show_snack)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "show_snack": show_snack,
        **mocks,
    }


@pytest.fixture
def notifications_tab_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 NotificationsTab, 返回包含 component/page/result/mocks 的 dict。"""
    from ui.views.settings_tabs import automation_tab as mod

    mocks = _patch_automation_common_mocks(mod, monkeypatch)

    show_snack = MagicMock()
    component = make_component(mod.NotificationsTab, show_snack_callback=show_snack)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "show_snack": show_snack,
        **mocks,
    }


# ============================================================================
# 组件挂载/渲染基础测试
# ============================================================================


class TestAutomationTabMount:
    """AutomationTab 挂载/渲染基础测试。"""

    def test_mount_returns_container(self, automation_tab_env) -> None:
        """挂载返回 ft.Container, content 为 ListView。"""
        result = automation_tab_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_render_includes_switches(self, automation_tab_env) -> None:
        """渲染含 2 个 Switch (schedule / ai_concept)。"""
        switches = _get_switches(automation_tab_env)
        assert len(switches) >= 2

    def test_render_includes_dropdowns(self, automation_tab_env) -> None:
        """渲染含 3 个 Dropdown (schedule_time / ai_concept_time / ai_concept_engine)。"""
        dropdowns = _get_dropdowns(automation_tab_env)
        assert len(dropdowns) >= 3

    def test_unmount_does_not_raise(self, automation_tab_env) -> None:
        """卸载组件不抛异常。"""
        component = automation_tab_env["component"]
        run_unmount_effects(component)


class TestNotificationsTabMount:
    """NotificationsTab 挂载/渲染基础测试。"""

    def test_mount_returns_container(self, notifications_tab_env) -> None:
        """挂载返回 ft.Container, content 为 ListView。"""
        result = notifications_tab_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_render_includes_one_switch(self, notifications_tab_env) -> None:
        """渲染含 1 个 Switch (news_alerts)。"""
        switches = _get_switches(notifications_tab_env)
        assert len(switches) >= 1

    def test_render_includes_interval_dropdown(self, notifications_tab_env) -> None:
        """渲染含 1 个 Dropdown (interval)。"""
        dropdowns = _get_dropdowns(notifications_tab_env)
        assert len(dropdowns) >= 1

    def test_unmount_does_not_raise(self, notifications_tab_env) -> None:
        """卸载组件不抛异常。"""
        component = notifications_tab_env["component"]
        run_unmount_effects(component)


# ============================================================================
# Event handler 测试: page 可用 → page.run_task (R16 守卫)
# ============================================================================


class TestEventHandlersPageAvailable:
    """验证 7 个 event handler 在 page 可用时调用 run_task。"""

    def test_on_schedule_toggle_invokes_run_task(self, automation_tab_env) -> None:
        """_on_schedule_toggle: page 可用 → page.run_task(_do_schedule_toggle, new_enabled)。"""
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_auto_update")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(switch.on_change, _make_event(True))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == (True,)

    def test_on_schedule_time_change_invokes_run_task(self, automation_tab_env) -> None:
        """_on_schedule_time_change: page 可用 → page.run_task(_do_schedule_time_change, new_time)。"""
        env = automation_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_update_time")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdown.on_select, _make_event("17:00"))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == ("17:00",)

    def test_on_ai_concept_toggle_invokes_run_task(self, automation_tab_env) -> None:
        """_on_ai_concept_toggle: page 可用 → page.run_task(_do_ai_concept_toggle, new_enabled)。"""
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_ai_concept_update")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(switch.on_change, _make_event(True))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == (True,)

    def test_on_ai_concept_time_change_invokes_run_task(self, automation_tab_env) -> None:
        """_on_ai_concept_time_change: page 可用 → page.run_task(_do_ai_concept_time_change, new_time)。"""
        env = automation_tab_env
        dropdowns = _get_dropdowns(env)
        # ai_concept_time_dropdown 的 label 也是 settings_update_time,
        # 与 schedule_time_dropdown 同 i18n key (源码设计如此), 用出现顺序区分 (第 2 个)
        ai_time_dropdown = dropdowns[1]
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(ai_time_dropdown.on_select, _make_event("20:00"))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == ("20:00",)

    def test_on_ai_concept_engine_change_invokes_run_task(self, automation_tab_env) -> None:
        """_on_ai_concept_engine_change: page 可用 → page.run_task(_do_ai_concept_engine_change, new_engine)。"""
        env = automation_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_ai_concept_search_engine")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdown.on_select, _make_event("search_pro"))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == ("search_pro",)

    def test_on_news_toggle_invokes_run_task(self, notifications_tab_env) -> None:
        """_on_news_toggle: page 可用 → page.run_task(_do_news_toggle, new_enabled)。"""
        env = notifications_tab_env
        switch = _find_switch_by_label(env, "settings_news_alerts")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(switch.on_change, _make_event(False))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == (False,)

    def test_on_interval_change_invokes_run_task(self, notifications_tab_env) -> None:
        """_on_interval_change: page 可用 → page.run_task(_do_interval_change, new_val)。"""
        env = notifications_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_news_interval")
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(dropdown.on_select, _make_event("60"))
        handler, args, _ = _await_run_task_handler(page)
        assert asyncio.iscoroutinefunction(handler)
        assert args == ("60",)


# ============================================================================
# Event handler 测试: page=None 早返回 (R16 守卫)
# ============================================================================


class TestEventHandlersPageNoneEarlyReturn:
    """验证 event handler 在 page=None 时早返回 (不调 run_task, 不抛异常)。

    通过 patch ``_get_page`` 返回 None 模拟 page 不可用。
    """

    def test_on_schedule_toggle_page_none_no_run_task(self, automation_tab_env) -> None:
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_auto_update")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(switch.on_change, _make_event(True))
        assert not page.run_task.called

    def test_on_schedule_time_change_page_none_no_run_task(self, automation_tab_env) -> None:
        env = automation_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_update_time")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(dropdown.on_select, _make_event("17:00"))
        assert not page.run_task.called

    def test_on_ai_concept_toggle_page_none_no_run_task(self, automation_tab_env) -> None:
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_ai_concept_update")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(switch.on_change, _make_event(True))
        assert not page.run_task.called

    def test_on_ai_concept_time_change_page_none_no_run_task(self, automation_tab_env) -> None:
        env = automation_tab_env
        dropdowns = _get_dropdowns(env)
        ai_time_dropdown = dropdowns[1]
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(ai_time_dropdown.on_select, _make_event("20:00"))
        assert not page.run_task.called

    def test_on_ai_concept_engine_change_page_none_no_run_task(self, automation_tab_env) -> None:
        env = automation_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_ai_concept_search_engine")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(dropdown.on_select, _make_event("search_pro"))
        assert not page.run_task.called

    def test_on_news_toggle_page_none_no_run_task(self, notifications_tab_env) -> None:
        env = notifications_tab_env
        switch = _find_switch_by_label(env, "settings_news_alerts")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(switch.on_change, _make_event(False))
        assert not page.run_task.called

    def test_on_interval_change_page_none_no_run_task(self, notifications_tab_env) -> None:
        env = notifications_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_news_interval")
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.automation_tab._get_page", return_value=None):
            _invoke(dropdown.on_select, _make_event("60"))
        assert not page.run_task.called


# ============================================================================
# Async handler 测试: AutomationTab (5 个)
# ============================================================================


class TestDoScheduleToggle:
    """_do_schedule_toggle: 成功/异常回滚/CancelledError。"""

    def _trigger(self, env, new_enabled: bool = True) -> tuple:
        switch = _find_switch_by_label(env, "settings_auto_update")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(switch.on_change, _make_event(new_enabled))
        return _await_run_task_handler(page)

    def test_success_path(self, automation_tab_env) -> None:
        """成功: save_config + show_snack(开启/关闭文案)。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, True)
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"auto_update_enabled": True})
        env["show_snack"].assert_called()

    def test_success_off_path(self, automation_tab_env) -> None:
        """关闭路径: 同样调 save_config + show_snack。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, False)
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"auto_update_enabled": False})
        env["show_snack"].assert_called()

    def test_exception_path_rolls_back(self, automation_tab_env) -> None:
        """save_config 抛 Exception → set_auto_enabled(not new_enabled) 回滚 + show_snack 错误。"""
        env = automation_tab_env
        env["mock_config"].save_config.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, True)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()
        # 验证回滚: render_once 后 switch.value 应为 False (回滚后 not True=False)
        _rerender(env)
        switch = _find_switch_by_label(env, "settings_auto_update")
        assert switch.value is False

    def test_cancelled_error_propagates(self, automation_tab_env) -> None:
        """R2: CancelledError 必须传播, 不被 except Exception 吞没。"""
        env = automation_tab_env
        env["mock_config"].save_config.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, True)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


class TestDoScheduleTimeChange:
    """_do_schedule_time_change: 成功/异常/CancelledError。"""

    def _trigger(self, env, new_time: str = "17:00") -> tuple:
        dropdown = _find_dropdown_by_label(env, "settings_update_time")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdown.on_select, _make_event(new_time))
        return _await_run_task_handler(page)

    def test_success_path(self, automation_tab_env) -> None:
        """成功: save_config + show_snack(含 time)。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, "17:00")
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"auto_update_time": "17:00"})
        env["show_snack"].assert_called()

    def test_exception_path_calls_show_snack(self, automation_tab_env) -> None:
        """save_config 抛 Exception → snack 错误 (无回滚, 仅日志)。"""
        env = automation_tab_env
        env["mock_config"].save_config.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, "17:00")
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()

    def test_cancelled_error_propagates(self, automation_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = automation_tab_env
        env["mock_config"].save_config.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, "17:00")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


class TestDoAiConceptToggle:
    """_do_ai_concept_toggle: 成功/异常回滚/CancelledError。"""

    def _trigger(self, env, new_enabled: bool = True) -> tuple:
        switch = _find_switch_by_label(env, "settings_ai_concept_update")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(switch.on_change, _make_event(new_enabled))
        return _await_run_task_handler(page)

    def test_success_path(self, automation_tab_env) -> None:
        """成功: set_ai_concept_schedule_enabled + show_snack。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, True)
        asyncio.run(handler(*args))

        env["mock_config"].set_ai_concept_schedule_enabled.assert_called_once_with(True)
        env["show_snack"].assert_called()

    def test_exception_path_rolls_back(self, automation_tab_env) -> None:
        """set_ai_concept_schedule_enabled 抛 Exception → set_ai_enabled 回滚 + show_snack 错误。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_schedule_enabled.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, True)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()
        _rerender(env)
        switch = _find_switch_by_label(env, "settings_ai_concept_update")
        assert switch.value is False

    def test_cancelled_error_propagates(self, automation_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_schedule_enabled.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, True)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


class TestDoAiConceptTimeChange:
    """_do_ai_concept_time_change: 成功/异常/CancelledError。"""

    def _trigger(self, env, new_time: str = "20:00") -> tuple:
        dropdowns = _get_dropdowns(env)
        ai_time_dropdown = dropdowns[1]
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(ai_time_dropdown.on_select, _make_event(new_time))
        return _await_run_task_handler(page)

    def test_success_path(self, automation_tab_env) -> None:
        """成功: set_ai_concept_schedule_time + show_snack(含 time)。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, "20:00")
        asyncio.run(handler(*args))

        env["mock_config"].set_ai_concept_schedule_time.assert_called_once_with("20:00")
        env["show_snack"].assert_called()

    def test_exception_path_calls_show_snack(self, automation_tab_env) -> None:
        """set_ai_concept_schedule_time 抛 Exception → snack 错误。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_schedule_time.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, "20:00")
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()

    def test_cancelled_error_propagates(self, automation_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_schedule_time.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, "20:00")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


class TestDoAiConceptEngineChange:
    """_do_ai_concept_engine_change: 成功/异常/CancelledError。"""

    def _trigger(self, env, new_engine: str = "search_pro") -> tuple:
        dropdown = _find_dropdown_by_label(env, "settings_ai_concept_search_engine")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdown.on_select, _make_event(new_engine))
        return _await_run_task_handler(page)

    def test_success_path(self, automation_tab_env) -> None:
        """成功: set_ai_concept_search_engine + show_snack(common_saved)。"""
        env = automation_tab_env
        handler, args, _ = self._trigger(env, "search_pro")
        asyncio.run(handler(*args))

        env["mock_config"].set_ai_concept_search_engine.assert_called_once_with("search_pro")
        env["show_snack"].assert_called()

    def test_exception_path_calls_show_snack(self, automation_tab_env) -> None:
        """set_ai_concept_search_engine 抛 Exception → snack 错误。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_search_engine.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, "search_pro")
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()

    def test_cancelled_error_propagates(self, automation_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = automation_tab_env
        env["mock_config"].set_ai_concept_search_engine.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, "search_pro")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


# ============================================================================
# Async handler 测试: NotificationsTab (2 个)
# ============================================================================


class TestDoNewsToggle:
    """_do_news_toggle: 成功(开启)/成功(关闭)/异常回滚/CancelledError。"""

    def _trigger(self, env, new_enabled: bool = True) -> tuple:
        switch = _find_switch_by_label(env, "settings_news_alerts")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(switch.on_change, _make_event(new_enabled))
        return _await_run_task_handler(page)

    def test_success_on_path(self, notifications_tab_env) -> None:
        """开启路径: save_config + show_snack(news_on)。"""
        env = notifications_tab_env
        handler, args, _ = self._trigger(env, True)
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"enable_news_alerts": True})
        env["show_snack"].assert_called()

    def test_success_off_path(self, notifications_tab_env) -> None:
        """关闭路径: save_config + show_snack(news_off)。"""
        env = notifications_tab_env
        handler, args, _ = self._trigger(env, False)
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"enable_news_alerts": False})
        env["show_snack"].assert_called()

    def test_exception_path_rolls_back(self, notifications_tab_env) -> None:
        """save_config 抛 Exception → set_news_enabled(not new_enabled) 回滚 + show_snack 错误。"""
        env = notifications_tab_env
        env["mock_config"].save_config.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, False)
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()
        _rerender(env)
        switch = _find_switch_by_label(env, "settings_news_alerts")
        # 初始 news_enabled=True, toggle 到 False 失败 → 回滚到 True
        assert switch.value is True

    def test_cancelled_error_propagates(self, notifications_tab_env) -> None:
        """R2: CancelledError 必须传播。"""
        env = notifications_tab_env
        env["mock_config"].save_config.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, True)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


class TestDoIntervalChange:
    """_do_interval_change: 成功/ValueError/异常/CancelledError。"""

    def _trigger(self, env, new_val: str = "60") -> tuple:
        dropdown = _find_dropdown_by_label(env, "settings_news_interval")
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(dropdown.on_select, _make_event(new_val))
        return _await_run_task_handler(page)

    def test_success_path(self, notifications_tab_env) -> None:
        """成功: int(new_val) + save_config + show_snack(含 interval)。"""
        env = notifications_tab_env
        handler, args, _ = self._trigger(env, "60")
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with({"news_poll_interval": 60})
        env["show_snack"].assert_called()

    def test_value_error_path_calls_show_snack(self, notifications_tab_env) -> None:
        """int(new_val) 抛 ValueError → snack num_fmt, 不调 save_config。

        用 ``not_a_number`` 触发 int() 抛 ValueError。
        """
        env = notifications_tab_env
        handler, args, _ = self._trigger(env, "not_a_number")
        asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_not_called()
        env["show_snack"].assert_called()

    def test_exception_path_calls_show_snack(self, notifications_tab_env) -> None:
        """save_config 抛 Exception → snack 错误。"""
        env = notifications_tab_env
        env["mock_config"].save_config.side_effect = RuntimeError("boom")
        handler, args, _ = self._trigger(env, "60")
        asyncio.run(handler(*args))

        env["show_snack"].assert_called()

    def test_cancelled_error_propagates(self, notifications_tab_env) -> None:
        """R2: CancelledError 必须传播。

        int(new_val) 成功, save_config 抛 CancelledError, 应传播而非被 except Exception 吞没。
        """
        env = notifications_tab_env
        env["mock_config"].save_config.side_effect = asyncio.CancelledError()
        handler, args, _ = self._trigger(env, "60")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler(*args))


# ============================================================================
# disabled 状态切换测试
# ============================================================================


class TestDisabledState:
    """auto_enabled / news_enabled 切换 disabled 状态验证。"""

    def test_auto_disabled_initial(self, automation_tab_env) -> None:
        """初始 auto_enabled=False → schedule_time_dropdown.disabled=True。"""
        env = automation_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_update_time")
        # 默认 fixture 设置 auto_enabled=False
        assert dropdown.disabled is True

    def test_auto_enabled_toggles_dropdown_disabled(self, automation_tab_env) -> None:
        """toggle schedule switch → set_auto_enabled(True) → dropdown.disabled=False。"""
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_auto_update")
        _invoke(switch.on_change, _make_event(True))
        _rerender(env)

        dropdown = _find_dropdown_by_label(env, "settings_update_time")
        assert dropdown.disabled is False

    def test_ai_concept_disabled_initial(self, automation_tab_env) -> None:
        """初始 ai_enabled=False → ai_time/engine dropdowns.disabled=True。"""
        env = automation_tab_env
        dropdowns = _get_dropdowns(env)
        # ai_concept_time_dropdown = dropdowns[1], ai_concept_engine_dropdown = dropdowns[2]
        assert dropdowns[1].disabled is True
        assert dropdowns[2].disabled is True

    def test_ai_concept_enabled_toggles_dropdowns_disabled(self, automation_tab_env) -> None:
        """toggle ai_concept switch → set_ai_enabled(True) → 两个 ai dropdowns.disabled=False。"""
        env = automation_tab_env
        switch = _find_switch_by_label(env, "settings_ai_concept_update")
        _invoke(switch.on_change, _make_event(True))
        _rerender(env)

        dropdowns = _get_dropdowns(env)
        assert dropdowns[1].disabled is False
        assert dropdowns[2].disabled is False

    def test_news_disabled_initial(self, notifications_tab_env) -> None:
        """初始 news_enabled=True → interval_dropdown.disabled=False。"""
        env = notifications_tab_env
        dropdown = _find_dropdown_by_label(env, "settings_news_interval")
        # 默认 fixture 设置 news_enabled=True
        assert dropdown.disabled is False

    def test_news_disabled_toggles_dropdown_disabled(self, notifications_tab_env) -> None:
        """toggle news switch → set_news_enabled(False) → interval_dropdown.disabled=True。"""
        env = notifications_tab_env
        switch = _find_switch_by_label(env, "settings_news_alerts")
        _invoke(switch.on_change, _make_event(False))
        _rerender(env)

        dropdown = _find_dropdown_by_label(env, "settings_news_interval")
        assert dropdown.disabled is True
