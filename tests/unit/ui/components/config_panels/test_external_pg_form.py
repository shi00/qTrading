"""ExternalPgForm 组件单元测试 (P3-9).

覆盖:
1. @ft.component 装饰契约
2. 渲染返回 ft.Container with ft.Column
3. 包含所有表单字段 (host/port/user/password/database/checkbox)
4. 包含 test/save 按钮
5. 使用 use_viewmodel(vm=vm) 外部 VM 模式
6. 表单控件 on_change 触发 VM update_*
7. View 不持有业务状态 (MVVM §3.2)

测试基础设施复用 test_database_config_panel.py 的 _FakeDatabaseConfigPanelVM 模式。
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import external_pg_form as form_module
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigState

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 external_pg_form.py 源码。"""
    return Path(form_module.__file__).read_text(encoding="utf-8")


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe)."""
    handler(*args)


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树。"""
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_walk_controls(content))
    return result


def _find_text_field(root: Any, label: str) -> ft.TextField:
    """通过 label 查找 TextField 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"TextField with label={label} not found")


class _FakeDatabaseConfigPanelVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。"""

    def __init__(self, state: DatabaseConfigState | None = None) -> None:
        self._state = state if state is not None else DatabaseConfigState()
        self._subscribers: list[Any] = []
        self.test_connection = MagicMock()
        self.save_config = MagicMock()
        self.update_host = MagicMock()
        self.update_port = MagicMock()
        self.update_user = MagicMock()
        self.update_password = MagicMock()
        self.update_database = MagicMock()
        self.update_create_if_not_exists = MagicMock()

    @property
    def state(self) -> DatabaseConfigState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _render_form(
    state: DatabaseConfigState | None = None,
    *,
    show_header: bool = True,
    compact: bool = False,
    show_save_button: bool = True,
    page: FakePage | None = None,
) -> tuple[_FakeDatabaseConfigPanelVM, FakePage, Any, Any]:
    """渲染 ExternalPgForm, 返回 (vm, page, result, component)。"""
    vm = _FakeDatabaseConfigPanelVM(state=state)
    if page is None:
        page = FakePage()
    cast(Any, page).run_task = MagicMock()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(form_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(form_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(form_module, "AppStyles"))
        mock_styles.primary_button.return_value = ft.ButtonStyle()
        mock_styles.secondary_button.return_value = ft.ButtonStyle()
        from ui.theme import AppStyles as _RealAppStyles

        mock_styles.FONT_SIZE_TITLE = _RealAppStyles.FONT_SIZE_TITLE
        mock_styles.FONT_SIZE_BODY_SM = _RealAppStyles.FONT_SIZE_BODY_SM
        mock_styles.FONT_SIZE_CAPTION = _RealAppStyles.FONT_SIZE_CAPTION
        mock_styles.FONT_SIZE_LG = _RealAppStyles.FONT_SIZE_LG

        component = make_component(
            ExternalPgForm,
            vm=vm,
            show_header=show_header,
            compact=compact,
            show_save_button=show_save_button,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 契约守护测试
# ============================================================================


class TestExternalPgFormContract:
    """ExternalPgForm @ft.component 契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: ExternalPgForm 必须被 @ft.component 装饰。"""
        assert hasattr(ExternalPgForm, "__wrapped__"), "ExternalPgForm 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_external_vm_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(vm=vm) 外部 VM 模式订阅 (CLAUDE.md §3.3)。"""
        source = _read_source()
        assert "use_viewmodel(vm=vm)" in source

    def test_no_business_state_in_view(self) -> None:
        """DoD: View 不持有业务状态 — 不应通过 use_state 持有业务字段。"""
        source = _read_source()
        forbidden = ["host", "port", "user", "password", "database"]
        for field in forbidden:
            assert f"use_state(lambda: {field}" not in source, f"View 不应通过 use_state 持有业务字段 {field}"

    def test_signature_accepts_vm_and_flags(self) -> None:
        """DoD: ExternalPgForm 签名接收 vm + show_header + compact + show_save_button。"""
        sig = inspect.signature(ExternalPgForm)
        assert "vm" in sig.parameters
        assert "show_header" in sig.parameters
        assert sig.parameters["show_header"].default is True
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True


# ============================================================================
# 渲染测试: 表单字段 + 按钮
# ============================================================================


class TestExternalPgFormRendering:
    """ExternalPgForm 渲染测试。"""

    def test_returns_container_with_column_content(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认渲染返回 ft.Container, content 为 ft.Column。"""
        _, _, result, _ = _render_form()
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_renders_all_form_fields(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染所有 5 个表单字段 (host/port/user/password/database)。"""
        _, _, result, _ = _render_form()
        for label in ("db_host", "db_port", "db_user", "db_password", "db_name"):
            assert _find_text_field(result, label) is not None

    def test_renders_create_checkbox(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染 create_if_not_exists checkbox。"""
        _, _, result, _ = _render_form()
        ctrls = _walk_controls(result)
        checkboxes = [c for c in ctrls if isinstance(c, ft.Checkbox)]
        assert len(checkboxes) == 1

    def test_renders_test_and_save_buttons(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染 test (POWER icon) + save (SAVE icon) 按钮。"""
        _, _, result, _ = _render_form()
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.POWER]
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(test_btns) == 1
        assert len(save_btns) == 1

    def test_show_save_button_false_hides_save(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False → 保存按钮 visible=False。"""
        _, _, result, _ = _render_form(show_save_button=False)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is False

    def test_show_header_true_includes_connection_settings(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=True → 控件树含 db_connection_settings 文本。"""
        _, _, result, _ = _render_form(show_header=True)
        ctrls = _walk_controls(result)
        headers = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_connection_settings"]
        assert len(headers) == 1

    def test_show_header_false_excludes_connection_settings(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=False → 不含 db_connection_settings / db_info 文本。"""
        _, _, result, _ = _render_form(show_header=False)
        ctrls = _walk_controls(result)
        headers = [
            c
            for c in ctrls
            if isinstance(c, ft.Text) and getattr(c, "value", None) in ("db_connection_settings", "db_info")
        ]
        assert len(headers) == 0


# ============================================================================
# 表单控件值绑定 + on_change 触发 VM update_*
# ============================================================================


class TestExternalPgFormBindings:
    """ExternalPgForm 表单值绑定 + on_change → vm.update_* 测试。"""

    def test_host_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = DatabaseConfigState(host="db.example.com")
        _, _, result, _ = _render_form(state=state)
        assert _find_text_field(result, "db_host").value == "db.example.com"

    def test_host_change_calls_vm_update_host(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        _invoke(_find_text_field(result, "db_host").on_change, _make_event("new-host"))
        vm.update_host.assert_called_once_with("new-host")

    def test_port_change_calls_vm_update_port(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        _invoke(_find_text_field(result, "db_port").on_change, _make_event("5433"))
        vm.update_port.assert_called_once_with("5433")

    def test_user_change_calls_vm_update_user(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        _invoke(_find_text_field(result, "db_user").on_change, _make_event("new-user"))
        vm.update_user.assert_called_once_with("new-user")

    def test_password_change_calls_vm_update_password(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        _invoke(_find_text_field(result, "db_password").on_change, _make_event("new-pwd"))
        vm.update_password.assert_called_once_with("new-pwd")

    def test_database_change_calls_vm_update_database(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        _invoke(_find_text_field(result, "db_name").on_change, _make_event("newdb"))
        vm.update_database.assert_called_once_with("newdb")

    def test_checkbox_change_calls_vm_update_create_if_not_exists(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form()
        ctrls = _walk_controls(result)
        checkbox = next(c for c in ctrls if isinstance(c, ft.Checkbox))
        _invoke(checkbox.on_change, _make_event(True))
        vm.update_create_if_not_exists.assert_called_once_with(True)


# ============================================================================
# 按钮事件 → page.run_task
# ============================================================================


class TestExternalPgFormButtonHandlers:
    """ExternalPgForm 按钮事件 → page.run_task 测试。"""

    def test_test_click_calls_page_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, page, result, _ = _render_form()
        ctrls = _walk_controls(result)
        test_btn = next(c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.POWER)
        run_task = cast(MagicMock, cast(Any, page).run_task)
        run_task.reset_mock()
        _invoke(test_btn.on_click, _make_event())
        run_task.assert_called_once_with(vm.test_connection)

    def test_save_click_calls_page_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, page, result, _ = _render_form(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btn = next(c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE)
        run_task = cast(MagicMock, cast(Any, page).run_task)
        run_task.reset_mock()
        _invoke(save_btn.on_click, _make_event())
        run_task.assert_called_once_with(vm.save_config)


# ============================================================================
# VM 生命周期测试
# ============================================================================


class TestExternalPgFormVMLifecycle:
    """ExternalPgForm 外部 VM 生命周期测试 (use_viewmodel vm 模式)。"""

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, _, _ = _render_form()
        assert len(vm._subscribers) > 0

    def test_unmount_unsubscribes_from_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, _, component = _render_form()
        assert len(vm._subscribers) > 0
        run_unmount_effects(component)
        assert len(vm._subscribers) == 0

    def test_external_vm_not_disposed_on_unmount(self, mock_i18n_state, mock_app_colors_state) -> None:
        """外部 VM 模式: 卸载不调 vm.dispose()。"""
        vm, _, _, component = _render_form()
        original_dispose = vm.dispose
        dispose_called: list[bool] = []

        def _spy_dispose() -> None:
            dispose_called.append(True)
            original_dispose()

        vm.dispose = _spy_dispose  # type: ignore[method-assign]
        run_unmount_effects(component)
        assert dispose_called == []
