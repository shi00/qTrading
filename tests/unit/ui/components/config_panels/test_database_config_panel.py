"""DatabaseConfigPanel 组件运行时测试 (Task 3.4).

覆盖:
1. 模块级纯函数: _render_message (None/正常翻译/params 替换)
2. 工厂函数: _on_test_click_factory / _on_save_click_factory 的 page 可用/None/RuntimeError 守卫
   + run_task(vm.test_connection) / run_task(vm.save_config) 调用验证
3. 组件运行时: show_header / compact / show_save_button 三 flag 组合
4. 状态显示: status_type (success/error/warning/info) 的图标/颜色映射
   (_STATUS_ICON_MAP / _STATUS_COLOR_MAP)
5. db_info 字段渲染
6. 表单控件 on_change 触发 VM update_*

test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等)
和 _render_message None/default param 路径, 本文件聚焦运行时行为 + factory 函数 +
组件体渲染 + params 替换, 不重复基础契约检查。
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
from ui.components.config_panels import database_config_panel as panel_module
from ui.components.config_panels.database_config_panel import (
    _STATUS_COLOR_MAP,
    _STATUS_ICON_MAP,
    DatabaseConfigPanel,
    _on_save_click_factory,
    _on_test_click_factory,
    _render_message,
)
from ui.theme import AppStyles
from ui.viewmodels import Message
from ui.viewmodels.database_config_panel_view_model import (
    DatabaseConfigPanelViewModel,
    DatabaseConfigState,
)

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 database_config_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归)。
    """
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


def _page_run_task(page: FakePage) -> MagicMock:
    """获取 page.run_task mock (动态注入, pyright safe)。

    FakePage 类不定义 run_task 属性, _render_panel 通过实例属性动态注入 MagicMock。
    用 cast(Any, page) 绕过 reportAttributeAccessIssue (ruff B009 禁止 getattr 常量属性)。
    """
    return cast(MagicMock, cast(Any, page).run_task)


# ============================================================================
# 契约守护测试 (扩展 test_config_panels.py 基础契约)
# ============================================================================


class TestDatabaseConfigPanelContractExtension:
    """DatabaseConfigPanel 契约守护扩展测试。

    test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
    此处补充 factory 函数守卫 + use_viewmodel 外部 VM 模式 + ft.context.page 访问 + 签名。
    """

    def test_is_ft_component(self) -> None:
        """DoD: DatabaseConfigPanel 必须被 @ft.component 装饰。"""
        assert hasattr(DatabaseConfigPanel, "__wrapped__"), "DatabaseConfigPanel 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_external_vm_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(vm=vm) 外部 VM 模式订阅 (CLAUDE.md §3.3)。"""
        source = _read_source()
        assert "use_viewmodel(vm=vm)" in source

    def test_uses_ft_context_page(self) -> None:
        """DoD: page 访问用 ft.context.page, try/except 守卫 RuntimeError。"""
        source = _read_source()
        assert "ft.context.page" in source
        assert "RuntimeError" in source

    def test_factory_functions_defined(self) -> None:
        """DoD: 2 个 factory 函数必须存在。"""
        source = _read_source()
        assert "def _on_test_click_factory(" in source
        assert "def _on_save_click_factory(" in source

    def test_panel_signature_accepts_vm_and_flags(self) -> None:
        """DoD: DatabaseConfigPanel 签名应接收 vm + show_header + compact + show_save_button。"""
        sig = inspect.signature(DatabaseConfigPanel)
        assert "vm" in sig.parameters
        assert "show_header" in sig.parameters
        assert sig.parameters["show_header"].default is True
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True


# ============================================================================
# 模块级常量: _STATUS_ICON_MAP / _STATUS_COLOR_MAP
# ============================================================================


class TestStatusMapsConstants:
    """_STATUS_ICON_MAP / _STATUS_COLOR_MAP 常量正确性测试。"""

    def test_status_icon_map_has_four_types(self) -> None:
        """DoD: _STATUS_ICON_MAP 必须包含 4 种状态。"""
        assert set(_STATUS_ICON_MAP.keys()) == {"success", "error", "warning", "info"}

    def test_status_color_map_has_four_types(self) -> None:
        """DoD: _STATUS_COLOR_MAP 必须包含 4 种状态。"""
        assert set(_STATUS_COLOR_MAP.keys()) == {"success", "error", "warning", "info"}

    def test_status_icon_map_values_are_valid_icons(self) -> None:
        """DoD: _STATUS_ICON_MAP 值必须是 ft.Icons 常量。"""
        assert _STATUS_ICON_MAP["success"] == ft.Icons.CHECK_CIRCLE
        assert _STATUS_ICON_MAP["error"] == ft.Icons.ERROR
        assert _STATUS_ICON_MAP["warning"] == ft.Icons.WARNING
        assert _STATUS_ICON_MAP["info"] == ft.Icons.INFO


# ============================================================================
# 模块级纯函数: _render_message (扩展 test_config_panels.py 已覆盖的 None/default)
# ============================================================================


class TestRenderMessageExtension:
    """_render_message 扩展测试 (params 替换路径)。

    test_config_panels.py::TestRenderMessage 已覆盖 None 返回空 + default param 路径,
    此处补充正常翻译 (无 params) + 多 params 替换路径。
    """

    def test_render_message_with_format_params(self) -> None:
        """_render_message 透传 msg.params 给 I18n.get。"""
        msg = Message("db_info_format", {"version": "16.0", "size": "10MB", "tables": 5})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "PostgreSQL 16.0 / 10MB / 5 tables"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("db_info_format", version="16.0", size="10MB", tables=5)
        assert result == "PostgreSQL 16.0 / 10MB / 5 tables"

    def test_render_message_with_no_params(self) -> None:
        """_render_message 对无 params 的 Message 透传空 kwargs。"""
        msg = Message("db_test_success")
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "连接成功"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("db_test_success")
        assert result == "连接成功"


# ============================================================================
# 工厂函数: _on_test_click_factory (page 可用/None/RuntimeError 守卫)
# ============================================================================


class TestOnTestClickFactory:
    """_on_test_click_factory: page 可用/None/RuntimeError 守卫。"""

    def test_page_available_calls_run_task(self) -> None:
        """page 可用 → page.run_task(vm.test_connection)。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.test_connection)

    def test_page_none_skips_run_task(self) -> None:
        """page=None → 不调 run_task, 不抛异常。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_runtime_error_swallowed(self) -> None:
        """ft.context.page 抛 RuntimeError → 静默处理, 不抛异常。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())  # 不应抛异常


# ============================================================================
# 工厂函数: _on_save_click_factory (page 可用/None/RuntimeError 守卫)
# ============================================================================


class TestOnSaveClickFactory:
    """_on_save_click_factory: page 可用/None/RuntimeError 守卫。"""

    def test_page_available_calls_run_task(self) -> None:
        """page 可用 → page.run_task(vm.save_config)。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.save_config)

    def test_page_none_skips_run_task(self) -> None:
        """page=None → 不调 run_task, 不抛异常。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_runtime_error_swallowed(self) -> None:
        """ft.context.page 抛 RuntimeError → 静默处理, 不抛异常。"""
        vm = MagicMock(spec=DatabaseConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        with patch("ui.components.config_panels.database_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())  # 不应抛异常


# ============================================================================
# 组件运行时测试基础设施: _FakeDatabaseConfigPanelVM + _render_panel helper
# ============================================================================


class _FakeDatabaseConfigPanelVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。

    state 字段可外部注入, command 方法为 MagicMock 便于断言。
    """

    def __init__(self, state: DatabaseConfigState | None = None) -> None:
        self._state = state if state is not None else DatabaseConfigState()
        self._subscribers: list[Any] = []
        # command 方法 (MagicMock, 便于断言调用)
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


def _render_panel(
    state: DatabaseConfigState | None = None,
    *,
    show_header: bool = True,
    compact: bool = False,
    show_save_button: bool = True,
    page: FakePage | None = None,
) -> tuple[_FakeDatabaseConfigPanelVM, FakePage, Any, Any]:
    """渲染 DatabaseConfigPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    """
    vm = _FakeDatabaseConfigPanelVM(state=state)
    if page is None:
        page = FakePage()
    # FakePage 不定义 run_task 属性, 测试动态注入 MagicMock
    cast(Any, page).run_task = MagicMock()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        mock_styles.primary_button.return_value = ft.ButtonStyle()
        mock_styles.secondary_button.return_value = ft.ButtonStyle()
        # P1-1: 字号 token 用真实数值 (create_autospec/patch 不会保留类属性 int 值)
        from ui.theme import AppStyles as _RealAppStyles

        mock_styles.FONT_SIZE_TITLE = _RealAppStyles.FONT_SIZE_TITLE
        mock_styles.FONT_SIZE_BODY_SM = _RealAppStyles.FONT_SIZE_BODY_SM
        mock_styles.FONT_SIZE_CAPTION = _RealAppStyles.FONT_SIZE_CAPTION
        mock_styles.FONT_SIZE_LG = _RealAppStyles.FONT_SIZE_LG

        component = make_component(
            DatabaseConfigPanel,
            vm=vm,
            show_header=show_header,
            compact=compact,
            show_save_button=show_save_button,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 组件运行时测试: 三 flag 组合 (show_header / compact / show_save_button)
# ============================================================================


class TestDatabaseConfigPanelFlags:
    """DatabaseConfigPanel 三 flag 组合测试 (show_header / compact / show_save_button)。"""

    def test_returns_container_with_column_content(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认渲染返回 ft.Container, content 为 ft.Column。"""
        _, _, result, _ = _render_panel()
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_show_header_true_includes_connection_settings_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=True → 控件树含 db_connection_settings 文本。"""
        _, _, result, _ = _render_panel(show_header=True)
        ctrls = _walk_controls(result)
        header_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_connection_settings"
        ]
        assert len(header_texts) == 1

    def test_show_header_false_excludes_connection_settings_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=False → 控件树不含 db_connection_settings / db_info 文本。"""
        _, _, result, _ = _render_panel(show_header=False)
        ctrls = _walk_controls(result)
        header_texts = [
            c
            for c in ctrls
            if isinstance(c, ft.Text) and getattr(c, "value", None) in ("db_connection_settings", "db_info")
        ]
        assert len(header_texts) == 0

    def test_show_header_true_includes_db_info_section(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=True → 控件树含 db_info 标题 + db_info_text 控件。"""
        _, _, result, _ = _render_panel(show_header=True)
        ctrls = _walk_controls(result)
        info_titles = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_info"]
        assert len(info_titles) == 1

    def test_show_header_false_excludes_db_info_section(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_header=False → 控件树不含 db_info 标题。"""
        _, _, result, _ = _render_panel(show_header=False)
        ctrls = _walk_controls(result)
        info_titles = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "db_info"]
        assert len(info_titles) == 0

    def test_compact_true_still_renders_container(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=True 仍渲染为 ft.Container (源码注释: compact 保留参数不影响布局)。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_compact_false_renders_container(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=False 渲染为 ft.Container。"""
        _, _, result, _ = _render_panel(compact=False)
        assert isinstance(result, ft.Container)

    def test_compact_true_includes_all_form_fields(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=True 仍包含所有表单字段 (host/port/user/password/database)。"""
        _, _, result, _ = _render_panel(compact=True)
        for label in ("db_host", "db_port", "db_user", "db_password", "db_name"):
            assert _find_text_field(result, label) is not None  # noqa: weak-assertion UI 契约测试验证多 label 循环内 text_field 存在性

    def test_show_save_button_true_save_visible(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=True → 保存按钮 visible=True。"""
        _, _, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is True

    def test_show_save_button_false_save_hidden(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False → 保存按钮 visible=False (源码用 visible= 而非 if)。"""
        _, _, result, _ = _render_panel(show_save_button=False)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is False

    def test_show_save_button_false_test_button_still_visible(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False → 测试按钮仍可见。"""
        _, _, result, _ = _render_panel(show_save_button=False)
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.POWER]
        assert len(test_btns) == 1
        assert test_btns[0].visible is True


# ============================================================================
# 组件运行时测试: status_type → 图标/颜色映射
# ============================================================================


class TestDatabaseConfigPanelStatusDisplay:
    """DatabaseConfigPanel 状态显示测试 (status_type 图标/颜色映射)。"""

    def test_status_icon_visible_when_status_message_present(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message 非空 → status_icon.visible=True。"""
        state = DatabaseConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_hidden_when_status_message_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message=None → status_icon.visible=False。"""
        state = DatabaseConfigState(status_message=None, status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1
        assert icons[0].visible is False

    def test_status_icon_success_uses_check_circle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success → CHECK_CIRCLE icon。"""
        state = DatabaseConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1

    def test_status_icon_error_uses_error_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error → ERROR icon。"""
        state = DatabaseConfigState(status_message=Message("err"), status_type="error")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR]
        assert len(icons) == 1

    def test_status_icon_warning_uses_warning_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning → WARNING icon。"""
        state = DatabaseConfigState(status_message=Message("warn"), status_type="warning")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.WARNING]
        assert len(icons) == 1

    def test_status_icon_info_uses_info_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=info → INFO icon。"""
        state = DatabaseConfigState(status_message=Message("info"), status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1

    def test_status_text_color_success(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success → status_text.color=_STATUS_COLOR_MAP['success']。"""
        state = DatabaseConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert icons[0].color == _STATUS_COLOR_MAP["success"]

    def test_status_text_color_error(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error → status_icon.color=_STATUS_COLOR_MAP['error']。"""
        state = DatabaseConfigState(status_message=Message("err"), status_type="error")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR]
        assert icons[0].color == _STATUS_COLOR_MAP["error"]

    def test_status_text_color_warning(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning → status_icon.color=_STATUS_COLOR_MAP['warning']。"""
        state = DatabaseConfigState(status_message=Message("warn"), status_type="warning")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.WARNING]
        assert icons[0].color == _STATUS_COLOR_MAP["warning"]

    def test_status_text_color_info(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=info → status_icon.color=_STATUS_COLOR_MAP['info']。"""
        state = DatabaseConfigState(status_message=Message("info"), status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert icons[0].color == _STATUS_COLOR_MAP["info"]

    def test_status_text_size_is_12(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_text_ctrl.size=FONT_SIZE_BODY_SM (P1-1: 12 → token)。"""
        state = DatabaseConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        # status_text_ctrl size=FONT_SIZE_BODY_SM (=12), 与 db_info_text (FONT_SIZE_CAPTION=11) 区分
        status_texts = [
            c
            for c in ctrls
            if isinstance(c, ft.Text) and getattr(c, "size", None) == AppStyles.FONT_SIZE_BODY_SM and c.value == "ok"
        ]
        assert len(status_texts) == 1

    def test_status_icon_size_is_16(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_icon.size=FONT_SIZE_TITLE (P1-1: 16 → token)。"""
        state = DatabaseConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert icons[0].size == AppStyles.FONT_SIZE_TITLE


# ============================================================================
# 组件运行时测试: db_info 字段渲染
# ============================================================================


class TestDatabaseConfigPanelDbInfo:
    """DatabaseConfigPanel db_info 字段渲染测试。"""

    def test_db_info_text_renders_message_key(self, mock_i18n_state, mock_app_colors_state) -> None:
        """db_info Message 渲染为 _render_message 输出 (I18n.get 返回 key)。"""
        state = DatabaseConfigState(db_info=Message("db_info_format", {"version": "16.0", "size": "10MB", "tables": 5}))
        _, _, result, _ = _render_panel(state=state, show_header=True)
        ctrls = _walk_controls(result)
        # db_info_text_ctrl size=FONT_SIZE_CAPTION (=11, P1-1), value=I18n.get(key, **params) → mock 返回 key
        info_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "size", None) == AppStyles.FONT_SIZE_CAPTION
        ]
        assert len(info_texts) == 1
        assert info_texts[0].value == "db_info_format"

    def test_db_info_text_empty_when_db_info_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """db_info=None → db_info_text_ctrl.value=""."""
        state = DatabaseConfigState(db_info=None)
        _, _, result, _ = _render_panel(state=state, show_header=True)
        ctrls = _walk_controls(result)
        info_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "size", None) == AppStyles.FONT_SIZE_CAPTION
        ]
        assert len(info_texts) == 1
        assert info_texts[0].value == ""

    def test_db_info_text_ctrl_text_align_center(self, mock_i18n_state, mock_app_colors_state) -> None:
        """db_info_text_ctrl.text_align=ft.TextAlign.CENTER。"""
        _, _, result, _ = _render_panel(show_header=True)
        ctrls = _walk_controls(result)
        info_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "size", None) == AppStyles.FONT_SIZE_CAPTION
        ]
        assert len(info_texts) == 1
        assert info_texts[0].text_align == ft.TextAlign.CENTER


# ============================================================================
# 组件运行时测试: 表单控件 on_change 触发 VM update_*
# ============================================================================


class TestDatabaseConfigPanelFormBindings:
    """DatabaseConfigPanel 表单控件值绑定 + on_change 触发 VM update_* 测试。"""

    def test_host_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """host_input.value 绑定到 state.host。"""
        state = DatabaseConfigState(host="db.example.com")
        _, _, result, _ = _render_panel(state=state)
        host_input = _find_text_field(result, "db_host")
        assert host_input.value == "db.example.com"

    def test_port_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """port_input.value 绑定到 state.port (字符串)。"""
        state = DatabaseConfigState(port="6543")
        _, _, result, _ = _render_panel(state=state)
        port_input = _find_text_field(result, "db_port")
        assert port_input.value == "6543"

    def test_user_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """user_input.value 绑定到 state.user。"""
        state = DatabaseConfigState(user="admin")
        _, _, result, _ = _render_panel(state=state)
        user_input = _find_text_field(result, "db_user")
        assert user_input.value == "admin"

    def test_password_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """password_input.value 绑定到 state.password, password=True。"""
        state = DatabaseConfigState(password="secret-pwd")
        _, _, result, _ = _render_panel(state=state)
        pwd_input = _find_text_field(result, "db_password")
        assert pwd_input.value == "secret-pwd"
        assert pwd_input.password is True
        assert pwd_input.can_reveal_password is True

    def test_database_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """database_input.value 绑定到 state.database。"""
        state = DatabaseConfigState(database="mydb")
        _, _, result, _ = _render_panel(state=state)
        db_input = _find_text_field(result, "db_name")
        assert db_input.value == "mydb"

    def test_create_checkbox_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """create_checkbox.value 绑定到 state.create_if_not_exists。"""
        state = DatabaseConfigState(create_if_not_exists=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        checkboxes = [c for c in ctrls if isinstance(c, ft.Checkbox)]
        assert len(checkboxes) == 1
        assert checkboxes[0].value is True

    def test_create_checkbox_value_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """create_checkbox.value=False 当 state.create_if_not_exists=False。"""
        state = DatabaseConfigState(create_if_not_exists=False)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        checkboxes = [c for c in ctrls if isinstance(c, ft.Checkbox)]
        assert len(checkboxes) == 1
        assert checkboxes[0].value is False


class TestDatabaseConfigPanelFormOnChanges:
    """DatabaseConfigPanel 表单 on_change → vm.update_* 测试 (同步命令)。"""

    def test_host_change_calls_vm_update_host(self, mock_i18n_state, mock_app_colors_state) -> None:
        """host input on_change → vm.update_host(value)。"""
        vm, _, result, _ = _render_panel()
        host_input = _find_text_field(result, "db_host")
        _invoke(host_input.on_change, _make_event("new-host"))
        vm.update_host.assert_called_once_with("new-host")

    def test_port_change_calls_vm_update_port(self, mock_i18n_state, mock_app_colors_state) -> None:
        """port input on_change → vm.update_port(value)。"""
        vm, _, result, _ = _render_panel()
        port_input = _find_text_field(result, "db_port")
        _invoke(port_input.on_change, _make_event("5433"))
        vm.update_port.assert_called_once_with("5433")

    def test_user_change_calls_vm_update_user(self, mock_i18n_state, mock_app_colors_state) -> None:
        """user input on_change → vm.update_user(value)。"""
        vm, _, result, _ = _render_panel()
        user_input = _find_text_field(result, "db_user")
        _invoke(user_input.on_change, _make_event("new-user"))
        vm.update_user.assert_called_once_with("new-user")

    def test_password_change_calls_vm_update_password(self, mock_i18n_state, mock_app_colors_state) -> None:
        """password input on_change → vm.update_password(value)。"""
        vm, _, result, _ = _render_panel()
        pwd_input = _find_text_field(result, "db_password")
        _invoke(pwd_input.on_change, _make_event("new-pwd"))
        vm.update_password.assert_called_once_with("new-pwd")

    def test_database_change_calls_vm_update_database(self, mock_i18n_state, mock_app_colors_state) -> None:
        """database input on_change → vm.update_database(value)。"""
        vm, _, result, _ = _render_panel()
        db_input = _find_text_field(result, "db_name")
        _invoke(db_input.on_change, _make_event("newdb"))
        vm.update_database.assert_called_once_with("newdb")

    def test_checkbox_change_calls_vm_update_create_if_not_exists(self, mock_i18n_state, mock_app_colors_state) -> None:
        """create_checkbox on_change → vm.update_create_if_not_exists(value)。"""
        vm, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        checkbox = next(c for c in ctrls if isinstance(c, ft.Checkbox))
        _invoke(checkbox.on_change, _make_event(True))
        vm.update_create_if_not_exists.assert_called_once_with(True)


# ============================================================================
# 组件运行时测试: 按钮事件 → page.run_task
# ============================================================================


class TestDatabaseConfigPanelButtonHandlers:
    """DatabaseConfigPanel 按钮事件 → page.run_task 测试。"""

    def test_test_click_calls_page_run_task_with_vm_test_connection(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """test button on_click → page.run_task(vm.test_connection)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.POWER]
        assert len(test_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(test_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.test_connection)

    def test_save_click_calls_page_run_task_with_vm_save_config(self, mock_i18n_state, mock_app_colors_state) -> None:
        """save button on_click → page.run_task(vm.save_config)。"""
        vm, page, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(save_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.save_config)

    def test_test_button_disabled_when_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=True → test button disabled。"""
        state = DatabaseConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.POWER]
        assert len(test_btns) == 1
        assert test_btns[0].disabled is True

    def test_save_button_disabled_when_saving(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_saving=True → save button disabled。"""
        state = DatabaseConfigState(is_saving=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].disabled is True


# ============================================================================
# 组件挂载/卸载 + VM 订阅生命周期
# ============================================================================


class TestDatabaseConfigPanelVMLifecycle:
    """DatabaseConfigPanel VM 订阅生命周期测试 (use_viewmodel 外部 VM 模式)。"""

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后 use_viewmodel 注册 subscribe 到 VM。"""
        vm, _, _, _ = _render_panel()
        assert len(vm._subscribers) > 0

    def test_unmount_unsubscribes_from_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载后退订 VM (use_viewmodel cleanup 调用 unsub)。"""
        vm, _, _, component = _render_panel()
        assert len(vm._subscribers) > 0
        run_unmount_effects(component)
        assert len(vm._subscribers) == 0

    def test_external_vm_not_disposed_on_unmount(self, mock_i18n_state, mock_app_colors_state) -> None:
        """外部 VM 模式: 卸载不调 vm.dispose() (生命周期由消费方管理)。"""
        vm, _, _, component = _render_panel()
        original_dispose = vm.dispose
        dispose_called: list[bool] = []

        def _spy_dispose() -> None:
            dispose_called.append(True)
            original_dispose()

        vm.dispose = _spy_dispose  # type: ignore[method-assign]
        run_unmount_effects(component)
        # 外部 VM 模式不调 dispose
        assert dispose_called == []


# ============================================================================
# 测试隔离守卫 (R7: 单例未污染)
# ============================================================================


class TestDatabaseConfigPanelIsolation:
    """R7 守卫: 测试间无单例状态污染 (由 conftest _reset_all_singletons autouse 保证)。"""

    def test_no_singleton_state_leakage_between_tests(self, mock_i18n_state, mock_app_colors_state) -> None:
        """连续渲染两个 panel, 第二个不受第一个影响 (VM 独立)。"""
        vm1, _, result1, _ = _render_panel(state=DatabaseConfigState(host="host-a", port="1111"))
        vm2, _, result2, _ = _render_panel(state=DatabaseConfigState(host="host-b", port="2222"))

        # 两个 VM 应是独立实例
        assert vm1 is not vm2
        # 两个 result 应反映各自 state
        host_input1 = _find_text_field(result1, "db_host")
        host_input2 = _find_text_field(result2, "db_host")
        assert host_input1.value == "host-a"
        assert host_input2.value == "host-b"
        port_input1 = _find_text_field(result1, "db_port")
        port_input2 = _find_text_field(result2, "db_port")
        assert port_input1.value == "1111"
        assert port_input2.value == "2222"
