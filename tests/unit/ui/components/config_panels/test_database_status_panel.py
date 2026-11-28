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
    run_render_effects,
    run_unmount_effects,
)
from ui.components.config_panels import database_status_panel as panel_module
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.viewmodels.database_status_view_model import DatabaseStatusState, DatabaseStatusViewModel

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


def _fake_i18n_get(key: str, **kwargs: Any) -> str:
    """I18n.get 替身: 把 key 与格式化参数一起编码进返回文本 (D21-UIA-008).

    仅返回 key 会丢弃 params，使测试只能验证 key 是否出现：View 写死
    port=5432、漏传 version/path 或参数名错位时仍可通过。编码后实际 Text
    同时携带精确 key 与逐项 kwargs，上述错误都会使断言失败。
    """
    if not kwargs:
        return key
    encoded = "&".join(f"{name}={kwargs[name]!r}" for name in sorted(kwargs))
    return f"{key}[{encoded}]"


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


def _text_values(root: Any) -> list[str]:
    """收集控件树中 ft.Text 的字符串值."""
    values: list[str] = []
    for control in _walk_controls(root):
        if isinstance(control, ft.Text):
            value = getattr(control, "value", None)
            if isinstance(value, str):
                values.append(value)
    return values


def _patch_visuals(stack: contextlib.ExitStack) -> None:
    """mock I18n / AppColors / AppStyles（渲染期 token 与文案）。"""
    mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
    mock_i18n.get.side_effect = _fake_i18n_get
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


def _render_panel(
    *,
    page: FakePage | None = None,
    doctor_result: Any = None,
    rerender: bool = False,
) -> tuple[DatabaseStatusViewModel, FakePage, Any, Any]:
    """渲染 DatabaseStatusPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    - EmbeddedPgMaintenanceService (doctor 返回 mock)
    - ConfigHandler.load_config

    use_viewmodel 以真实 VM + DI mock service 替换，使挂载期 use_effect 触发的
    refresh_status 走 mock doctor（不触碰真实 sidecar）。
    """
    if page is None:
        # execute_tasks=True：挂载期 use_effect 提交的 refresh_status 需真实执行，
        # 才能断言医生结果落到 state（该异步命令有限，不会阻塞）。
        page = FakePage(execute_tasks=True)

    if doctor_result is None:
        doctor_result = MagicMock()
        doctor_result.data_dir = "/fake/data"
        doctor_result.pg_version = 16
        doctor_result.postgres_alive = True
        doctor_result.port = 55432
        doctor_result.issues = []
        doctor_result.restore_residuals = []
        doctor_result.dump_partials = []

    mock_svc = MagicMock()
    mock_svc.doctor = AsyncMock(return_value=doctor_result)

    mock_config = {
        "embedded_pg_enabled": True,
        "embedded_pg_data_root": "/fake/data_root",
        "embedded_pg_log_dir": "/fake/log_dir",
        "db_port": 5432,
    }

    vm = DatabaseStatusViewModel(maintenance_service=mock_svc)

    with contextlib.ExitStack() as stack:
        _patch_visuals(stack)
        stack.enter_context(
            patch(
                "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
                return_value=mock_config,
            )
        )
        stack.enter_context(patch.object(panel_module, "use_viewmodel", return_value=(vm.state, vm)))

        component = make_component(DatabaseStatusPanel)
        run_mount_effects(component, page=page)
        result = render_once(component)
        if rerender:
            run_render_effects(component)

    return vm, page, result, component


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
        assert callable(getattr(component, "fn", None)), "Component 应已实例化并暴露可调用的 fn"

    def test_unmount_disposes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 卸载时 dispose 内部 VM (use_viewmodel factory 模式默认 dispose_on_unmount=True)。"""
        page = FakePage()
        with contextlib.ExitStack() as stack:
            _patch_visuals(stack)
            stack.enter_context(
                patch(
                    "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
                    return_value={
                        "embedded_pg_enabled": True,
                        "embedded_pg_data_root": "/fake/data_root",
                        "embedded_pg_log_dir": "/fake/log_dir",
                        "db_port": 5432,
                    },
                )
            )
            stack.enter_context(
                patch(
                    "ui.viewmodels.database_status_view_model.DatabaseStatusViewModel.refresh_status",
                    new_callable=AsyncMock,
                )
            )
            mock_dispose = stack.enter_context(
                patch("ui.components.config_panels.database_status_panel.DatabaseStatusViewModel.dispose")
            )
            component = make_component(DatabaseStatusPanel)
            run_mount_effects(component, page=page)
            run_unmount_effects(component)
        mock_dispose.assert_called_once_with()


# ============================================================================
# 挂载期自动刷新 (D1-12 ②)
# ============================================================================


class TestDatabaseStatusPanelAutoRefresh:
    """挂载期必须自动刷新一次状态，重渲染不得重复触发。"""

    def test_mount_triggers_initial_refresh(self, mock_i18n_state, mock_app_colors_state) -> None:
        """use_effect(dependencies=[]) 在挂载后提交 vm.refresh_status。"""
        vm, page, _, _ = _render_panel()

        assert page.scheduled_tasks.count(vm.refresh_status) == 1, (
            f"挂载应触发一次 vm.refresh_status，实际: {page.scheduled_tasks}"
        )
        # 刷新已执行：首次打开设置页即拿到真实状态（不再空白禁用）
        assert vm.state.status_message is not None
        assert vm.state.data_dir == "/fake/data"

    def test_rerender_does_not_repeat_refresh(self, mock_i18n_state, mock_app_colors_state) -> None:
        """dependencies=[] 语义：重渲染不得重复调用 doctor。"""
        vm, page, _, _ = _render_panel(rerender=True)

        assert page.scheduled_tasks.count(vm.refresh_status) == 1, f"重渲染不应重复刷新，实际: {page.scheduled_tasks}"


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

    def test_message_params_forwarded_to_i18n(self, mock_i18n_state) -> None:
        """Message.params 原样透传给 I18n.get (不丢弃 / 不改名)."""
        msg = Message("db_status_port", {"port": 55432})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = _fake_i18n_get
            result = panel_module._render_message(msg)
        assert result == "db_status_port[port=55432]"
        mock_i18n.get.assert_called_once_with("db_status_port", port=55432)


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
        _patch_visuals(stack)
        stack.enter_context(patch.object(panel_module, "use_viewmodel", return_value=(state, mock_vm)))

        component = make_component(DatabaseStatusPanel)
        run_mount_effects(component, page=page)
        result = render_once(component)

    return mock_vm, page, result, component


class TestDatabaseStatusPanelStateRendering:
    """DatabaseStatusPanel 各 state 渲染分支测试."""

    @pytest.mark.parametrize(
        ("state", "expected_text"),
        [
            pytest.param(
                DatabaseStatusState(pg_version="17.4"),
                "db_status_version[version='17.4']",
                id="version",
            ),
            pytest.param(
                DatabaseStatusState(port=55432),
                "db_status_port[port=55432]",
                id="port",
            ),
            pytest.param(
                DatabaseStatusState(data_dir="/srv/pg data/用户#1"),
                "db_status_data_dir[path='/srv/pg data/用户#1']",
                id="data_dir",
            ),
            pytest.param(
                DatabaseStatusState(log_dir="/var/log/pg/用户#1"),
                "db_status_log_dir[path='/var/log/pg/用户#1']",
                id="log_dir",
            ),
        ],
    )
    def test_info_line_forwards_exact_key_and_kwarg(
        self,
        state: DatabaseStatusState,
        expected_text: str,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """信息行以精确 key + 该字段的 state 值输出 (写死值 / 漏传参数必须失败)."""
        _, _, result, _ = _render_panel_with_state(state)
        assert expected_text in _text_values(result)

    def test_info_lines_keep_distinct_values_when_all_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """四项信息同时存在时各自携带自己的值 (参数错位必须失败)."""
        state = DatabaseStatusState(
            pg_version="17.4",
            port=55432,
            data_dir="/srv/pg-a",
            log_dir="/var/log/pg-b",
        )
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)
        assert "db_status_version[version='17.4']" in text_values
        assert "db_status_port[port=55432]" in text_values
        assert "db_status_data_dir[path='/srv/pg-a']" in text_values
        assert "db_status_log_dir[path='/var/log/pg-b']" in text_values

    def test_port_line_absent_when_port_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """port 为 None 时不生成端口行 (不得以配置端口 5432 补位)."""
        state = DatabaseStatusState(pg_version="17.4", port=None)
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)
        info_lines = [value for value in text_values if value.startswith(("db_status_version", "db_status_port"))]
        assert info_lines == ["db_status_version[version='17.4']"]

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


# ============================================================================
# 诊断渠道渲染 (D1-12 ①, R21)
# ============================================================================


class TestDiagnosticsRendering:
    """issues / restore_residuals / dump_partials 三态渲染：未知 / 无 / 有。"""

    def test_unknown_when_fields_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """三项为 None 时渲染「未知」，不得渲染成「无」 (R21)。"""
        state = DatabaseStatusState(status_message=Message("db_status_running"), status_type="success")
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)

        for label in (
            "db_status_issues_label",
            "db_status_restore_residuals_label",
            "db_status_dump_partials_label",
        ):
            assert f"{label}[value='db_status_value_unknown']" in text_values
            assert f"{label}[value='db_status_value_none']" not in text_values

    def test_none_when_empty_lists(self, mock_i18n_state, mock_app_colors_state) -> None:
        """空列表表示 sidecar 已确认无异常，渲染「无」。"""
        state = DatabaseStatusState(
            status_message=Message("db_status_running"),
            status_type="success",
            issues=[],
            restore_residuals=[],
            dump_partials=[],
        )
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)

        for label in (
            "db_status_issues_label",
            "db_status_restore_residuals_label",
            "db_status_dump_partials_label",
        ):
            assert f"{label}[value='db_status_value_none']" in text_values

    def test_issues_rendered_with_count_and_messages(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非空 issues 渲染数量与逐条 message。"""
        state = DatabaseStatusState(
            status_message=Message("db_status_running"),
            status_type="warning",
            issues=[
                {"code": "disk_low", "severity": "warning", "message": "剩余磁盘空间不足"},
                {"code": "pg_control_unreadable", "severity": "error", "message": "pg_control 不可读"},
            ],
            restore_residuals=[],
            dump_partials=[],
        )
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)

        # 数量文案经 I18n.get("db_status_value_count", count=N) 产生，嵌入 label 值
        assert any(v.startswith("db_status_issues_label[value='db_status_value_count[count=2]") for v in text_values)
        assert "db_status_issue_item[message='剩余磁盘空间不足']" in text_values
        assert "db_status_issue_item[message='pg_control 不可读']" in text_values

    def test_residuals_rendered_with_paths_and_scope_note(self, mock_i18n_state, mock_app_colors_state) -> None:
        """残留物渲染路径明细，并注明扫描范围仅数据目录同级。"""
        state = DatabaseStatusState(
            status_message=Message("db_status_running"),
            status_type="warning",
            issues=[],
            restore_residuals=["/fake/data.restore-20260101"],
            dump_partials=["/fake/dump.partial"],
        )
        _, _, result, _ = _render_panel_with_state(state)
        text_values = _text_values(result)

        assert any(
            v.startswith("db_status_restore_residuals_label[value='db_status_value_count[count=1]") for v in text_values
        )
        assert any(
            v.startswith("db_status_dump_partials_label[value='db_status_value_count[count=1]") for v in text_values
        )
        assert "db_status_residual_item[path='/fake/data.restore-20260101']" in text_values
        assert "db_status_residual_item[path='/fake/dump.partial']" in text_values
        assert "db_status_residuals_scope_note" in text_values
