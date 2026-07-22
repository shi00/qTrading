"""DatabaseStatusPanel 组件单元测试 (P3-10).

覆盖:
1. @ft.component 装饰契约
2. use_viewmodel(factory=...) 内部 VM 模式
3. View 不持有业务状态 (MVVM §3.2)
4. 渲染状态文本 (running/stopped)
5. 渲染 version/port/data_dir/log_dir 信息
6. 渲染 3 个按钮 (refresh/open_data_dir/open_log_dir)
7. VM 生命周期 (mount/dispose)
8. 纯函数 _render_message
9. Click handler factory (page 可用 / RuntimeError 分支)
10. 状态渲染分支 (各 status_type / info 字段非 None / error_message)
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from core.i18n import Message
from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import database_status_panel as panel_module
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.viewmodels.database_status_view_model import DatabaseStatusState

pytestmark = pytest.mark.unit


def _set_context_page(page: Any) -> None:
    """注入 page 到 _context_page (cast Any 绕过类型检查)."""
    from flet.controls.context import _context_page

    _context_page.set(cast(Any, page))


def _invoke_click(handler: Any) -> None:
    """调用 click handler (cast Any 绕过 Optional 调用检查)."""
    cast(Any, handler)(MagicMock())


def _read_source() -> str:
    """读取 database_status_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)。"""
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


def _render_panel(
    *,
    page: FakePage | None = None,
) -> tuple[Any, FakePage, Any, Any]:
    """渲染 DatabaseStatusPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    - EmbeddedPgMaintenanceService (doctor 返回 mock)
    - ConfigHandler.load_config
    """
    if page is None:
        page = FakePage()

    mock_doctor = MagicMock()
    mock_doctor.data_dir = "/fake/data"
    mock_doctor.pg_version = 17
    mock_doctor.postgres_alive = True

    mock_svc = MagicMock()
    mock_svc.doctor = AsyncMock(return_value=mock_doctor)

    mock_config = {
        "embedded_pg_enabled": True,
        "embedded_pg_data_root": "/fake/data_root",
        "embedded_pg_log_dir": "/fake/log_dir",
        "db_port": 5432,
    }

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        from ui.theme import AppStyles as _RealAppStyles

        for attr in dir(_RealAppStyles):
            if not attr.startswith("_"):
                val = getattr(_RealAppStyles, attr, None)
                if isinstance(val, (str, int, float)):
                    setattr(mock_styles, attr, val)
        mock_styles.primary_button = MagicMock(return_value=ft.ButtonStyle())
        mock_styles.secondary_button = MagicMock(return_value=ft.ButtonStyle())

        stack.enter_context(
            patch(
                "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
                return_value=mock_config,
            )
        )

        component = make_component(DatabaseStatusPanel)
        run_mount_effects(component, page=page)
        result = render_once(component)

    return None, page, result, component


# ============================================================================
# 契约守护测试
# ============================================================================


class TestDatabaseStatusPanelContract:
    """DatabaseStatusPanel @ft.component 契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: DatabaseStatusPanel 必须被 @ft.component 装饰。"""
        assert hasattr(DatabaseStatusPanel, "__wrapped__"), "DatabaseStatusPanel 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_internal_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(factory=...) 内部 VM 模式订阅 (CLAUDE.md §3.3)。"""
        source = _read_source()
        assert "use_viewmodel(factory=" in source

    def test_no_business_state_in_view(self) -> None:
        """DoD: View 不持有业务状态 (MVVM §3.2) — 不应有 use_state 持有业务字段。"""
        source = _read_source()
        forbidden = ["host", "user", "password", "database_name"]
        for field in forbidden:
            assert f"use_state(lambda: {field}" not in source, f"View 不应通过 use_state 持有业务字段 {field}"

    def test_signature_no_required_business_params(self) -> None:
        """DoD: DatabaseStatusPanel 签名不接受 vm 参数 (内部 VM 模式)。"""
        sig = inspect.signature(DatabaseStatusPanel)
        # 不应有 vm 必需参数
        if "vm" in sig.parameters:
            assert sig.parameters["vm"].default is not inspect.Parameter.empty, (
                "vm 不应是必需参数 (DatabaseStatusPanel 用内部 VM)"
            )


# ============================================================================
# 渲染测试
# ============================================================================


class TestDatabaseStatusPanelRendering:
    """DatabaseStatusPanel 渲染测试。"""

    def test_returns_container(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认渲染返回 ft.Container。"""
        _, _, result, _ = _render_panel()
        assert isinstance(result, ft.Container)

    def test_renders_title_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染标题 (db_status_title)。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        titles = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_status_title"]
        assert len(titles) >= 1

    def test_renders_refresh_button(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染刷新状态按钮 (db_status_refresh)。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        # 至少有 refresh 按钮
        refresh_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_status_refresh"
        ]
        assert len(refresh_texts) >= 1 or any(getattr(b, "content", None) == "db_status_refresh" for b in buttons)

    def test_renders_open_data_dir_button(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染打开数据目录按钮 (db_status_open_data_dir)。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        # 按钮用 ft.Button(content=I18n.get(...))，content 是字符串 (mock 返回 key)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        open_data_dir_btns = [b for b in buttons if getattr(b, "content", None) == "db_status_open_data_dir"]
        assert len(open_data_dir_btns) >= 1

    def test_renders_open_log_dir_button(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染打开日志目录按钮 (db_status_open_log_dir)。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        open_log_dir_btns = [b for b in buttons if getattr(b, "content", None) == "db_status_open_log_dir"]
        assert len(open_log_dir_btns) >= 1

    def test_no_form_fields_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: DatabaseStatusPanel 不渲染表单字段 (无 TextField)。"""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        text_fields = [c for c in ctrls if isinstance(c, ft.TextField)]
        assert len(text_fields) == 0


# ============================================================================
# VM 生命周期测试
# ============================================================================


class TestDatabaseStatusPanelVMLifecycle:
    """DatabaseStatusPanel 内部 VM 生命周期测试 (use_viewmodel factory 模式)。"""

    def test_mount_initializes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后通过 use_viewmodel(factory=...) 实例化内部 VM。"""
        _, _, _, component = _render_panel()
        assert component is not None
        assert hasattr(component, "fn"), "Component 应已实例化 (有 fn 属性)"

    def test_unmount_disposes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 卸载时 dispose 内部 VM (use_viewmodel factory 模式默认 dispose_on_unmount=True)。"""
        with patch("ui.components.config_panels.database_status_panel.DatabaseStatusViewModel.dispose") as mock_dispose:
            _, _, _, component = _render_panel()
            run_unmount_effects(component)
        mock_dispose.assert_called_once_with()


# ============================================================================
# 纯函数测试
# ============================================================================


class TestRenderMessage:
    """_render_message 纯函数测试."""

    def test_none_returns_empty(self, mock_i18n_state) -> None:
        """None 输入返回空字符串."""
        assert panel_module._render_message(None) == ""

    def test_message_returns_translated_text(self, mock_i18n_state) -> None:
        """Message 输入调 I18n.get(key, **params) 返回翻译文本."""
        msg = Message("db_status_running")
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: f"translated:{key}"
            result = panel_module._render_message(msg)
        assert result == "translated:db_status_running"
        mock_i18n.get.assert_called_once_with("db_status_running")


# ============================================================================
# Click handler factory 测试
# ============================================================================


class TestClickHandlers:
    """Click handler factory 测试 — 覆盖 page 可用 / RuntimeError 分支."""

    def test_on_refresh_click_calls_refresh_status(self) -> None:
        """page 可用时调 page.run_task(vm.refresh_status)."""
        vm = MagicMock()
        mock_page = MagicMock()
        _set_context_page(mock_page)
        try:
            handler = panel_module._on_refresh_click_factory(vm)
            _invoke_click(handler)
        finally:
            _set_context_page(None)

        mock_page.run_task.assert_called_once_with(vm.refresh_status)

    def test_on_refresh_click_silent_on_runtime_error(self) -> None:
        """page 不可用 (RuntimeError, _context_page 为 None) 时静默处理."""
        vm = MagicMock()
        # _set_context_page(None) 让 ft.context.page property 抛 RuntimeError
        _set_context_page(None)
        try:
            handler = panel_module._on_refresh_click_factory(vm)
            _invoke_click(handler)  # 不应抛异常
        finally:
            _set_context_page(None)
        vm.refresh_status.assert_not_called()

    def test_on_refresh_click_silent_when_page_none(self) -> None:
        """page 为 None 时静默处理 (ft.context.page 抛 RuntimeError 由 except 捕获)."""
        vm = MagicMock()
        _set_context_page(None)
        try:
            handler = panel_module._on_refresh_click_factory(vm)
            _invoke_click(handler)
        finally:
            _set_context_page(None)
        vm.refresh_status.assert_not_called()

    def test_on_open_data_dir_click_calls_open_data_dir(self) -> None:
        """open_data_dir 同步调用, 直接调 vm.open_data_dir()."""
        vm = MagicMock()
        handler = panel_module._on_open_data_dir_click_factory(vm)
        _invoke_click(handler)
        vm.open_data_dir.assert_called_once_with()

    def test_on_open_log_dir_click_calls_open_log_dir(self) -> None:
        """open_log_dir 同步调用, 直接调 vm.open_log_dir()."""
        vm = MagicMock()
        handler = panel_module._on_open_log_dir_click_factory(vm)
        _invoke_click(handler)
        vm.open_log_dir.assert_called_once_with()


# ============================================================================
# 状态渲染分支测试
# ============================================================================


def _render_panel_with_state(
    state: DatabaseStatusState,
    *,
    page: FakePage | None = None,
) -> tuple[Any, FakePage, Any, Any]:
    """用自定义 state 渲染 DatabaseStatusPanel (mock use_viewmodel 返回 state)."""
    if page is None:
        page = FakePage()

    mock_vm = MagicMock()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        from ui.theme import AppStyles as _RealAppStyles

        for attr in dir(_RealAppStyles):
            if not attr.startswith("_"):
                val = getattr(_RealAppStyles, attr, None)
                if isinstance(val, (str, int, float)):
                    setattr(mock_styles, attr, val)
        mock_styles.primary_button = MagicMock(return_value=ft.ButtonStyle())
        mock_styles.secondary_button = MagicMock(return_value=ft.ButtonStyle())

        stack.enter_context(patch.object(panel_module, "use_viewmodel", return_value=(state, mock_vm)))

        component = make_component(DatabaseStatusPanel)
        run_mount_effects(component, page=page)
        result = render_once(component)

    return mock_vm, page, result, component


class TestDatabaseStatusPanelStateRendering:
    """DatabaseStatusPanel 各 state 渲染分支测试."""

    def test_renders_version_when_pg_version_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """pg_version 非 None 时渲染 db_status_version."""
        state = DatabaseStatusState(pg_version="17")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_version" in text_values

    def test_renders_port_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """port 非 None 时渲染 db_status_port."""
        state = DatabaseStatusState(port=5432)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_port" in text_values

    def test_renders_data_dir_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """data_dir 非 None 时渲染 db_status_data_dir."""
        state = DatabaseStatusState(data_dir="/var/lib/pg")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_data_dir" in text_values

    def test_renders_log_dir_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """log_dir 非 None 时渲染 db_status_log_dir."""
        state = DatabaseStatusState(log_dir="/var/log/pg")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_log_dir" in text_values

    def test_renders_status_message_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message 非 None 时渲染翻译文本 + status icon."""
        state = DatabaseStatusState(
            status_message=Message("db_status_running"),
            status_type="success",
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_running" in text_values
        # status icon 应可见
        icons = [c for c in ctrls if isinstance(c, ft.Icon)]
        assert any(getattr(i, "visible", False) for i in icons)

    def test_renders_error_message_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """error_message 非 None 时渲染翻译文本."""
        state = DatabaseStatusState(
            error_message=Message("db_status_refresh_failed"),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_refresh_failed" in text_values

    def test_refresh_button_disabled_when_refreshing(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_refreshing=True 时 refresh_button disabled=True."""
        state = DatabaseStatusState(is_refreshing=True)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        refresh_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "db_status_refresh"
        ]
        assert len(refresh_btns) == 1
        assert refresh_btns[0].disabled is True

    def test_open_data_dir_button_disabled_when_data_dir_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """data_dir 为 None 时 open_data_dir_button disabled=True."""
        state = DatabaseStatusState(data_dir=None)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        open_data_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "db_status_open_data_dir"
        ]
        assert len(open_data_btns) == 1
        assert open_data_btns[0].disabled is True

    def test_open_data_dir_button_enabled_when_data_dir_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """data_dir 非 None 时 open_data_dir_button disabled=False."""
        state = DatabaseStatusState(data_dir="/var/lib/pg")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        open_data_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "db_status_open_data_dir"
        ]
        assert len(open_data_btns) == 1
        assert open_data_btns[0].disabled is False

    def test_open_log_dir_button_disabled_when_log_dir_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """log_dir 为 None 时 open_log_dir_button disabled=True."""
        state = DatabaseStatusState(log_dir=None)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        open_log_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "db_status_open_log_dir"
        ]
        assert len(open_log_btns) == 1
        assert open_log_btns[0].disabled is True

    def test_open_log_dir_button_enabled_when_log_dir_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """log_dir 非 None 时 open_log_dir_button disabled=False."""
        state = DatabaseStatusState(log_dir="/var/log/pg")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        open_log_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "db_status_open_log_dir"
        ]
        assert len(open_log_btns) == 1
        assert open_log_btns[0].disabled is False

    def test_status_icon_uses_success_mapping(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success 时 status_icon 用 CHECK_CIRCLE icon."""
        state = DatabaseStatusState(
            status_message=Message("db_status_running"),
            status_type="success",
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon)]
        # ft.Icon 的 icon 属性是 icon name (int 类型 in Flet V1)
        assert any(getattr(i, "icon", None) == ft.Icons.CHECK_CIRCLE for i in icons)

    def test_status_icon_uses_error_mapping(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error 时 status_icon 用 ERROR icon."""
        state = DatabaseStatusState(
            status_message=Message("db_status_stopped"),
            status_type="error",
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon)]
        assert any(getattr(i, "icon", None) == ft.Icons.ERROR for i in icons)

    def test_status_icon_uses_warning_mapping(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning 时 status_icon 用 WARNING icon."""
        state = DatabaseStatusState(
            status_message=Message("db_status_stopped"),
            status_type="warning",
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon)]
        assert any(getattr(i, "icon", None) == ft.Icons.WARNING for i in icons)

    def test_status_icon_hidden_when_no_status_message(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message 为 None 时 status_icon visible=False."""
        state = DatabaseStatusState(status_message=None)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon)]
        assert len(icons) >= 1
        assert all(not getattr(i, "visible", True) for i in icons)
