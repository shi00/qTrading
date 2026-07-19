"""TushareConfigPanel 组件运行时测试 (Task 3.3).

覆盖:
1. 模块级纯函数: _build_tier_options / TUSHARE_POINT_TIERS 常量 / _render_message
2. 工厂函数: _on_verify_click_factory / _on_save_click_factory / _on_tier_change_factory
3. _on_register_click: webbrowser.open_new_tab mock
4. 组件运行时: compact 布局 / show_save_button / show_register_link / is_verifying /
   status_icon 隐藏 / token_input password
5. R9 守卫: token 不打印

test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
本文件聚焦运行时行为 + R9 守卫 + factory 函数 + 组件体渲染, 不重复基础契约检查。
"""

import contextlib
import inspect
import re
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
from data.constants import TUSHARE_POINT_TIERS
from ui.components.config_panels import tushare_config_panel as panel_module
from ui.components.config_panels.tushare_config_panel import (
    TushareConfigPanel,
    _build_tier_options,
    _on_register_click,
    _on_save_click_factory,
    _on_tier_change_factory,
    _on_verify_click_factory,
    _render_message,
)
from ui.viewmodels import Message
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel, TushareConfigState

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 tushare_config_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _make_event(value: str | None = None) -> MagicMock:
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


def _find_dropdown(root: Any, label: str) -> ft.Dropdown:
    """通过 label 查找 Dropdown 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"Dropdown with label={label} not found")


def _page_run_task(page: FakePage) -> MagicMock:
    """获取 page.run_task mock (动态注入, pyright safe)。

    FakePage 类不定义 run_task 属性, _render_panel 通过实例属性动态注入 MagicMock。
    用 cast(Any, page) 绕过 reportAttributeAccessIssue (ruff B009 禁止 getattr 常量属性)。
    """
    return cast(MagicMock, cast(Any, page).run_task)


# ============================================================================
# 契约守护测试 (扩展 test_config_panels.py 基础契约)
# ============================================================================


class TestTushareConfigPanelContractExtension:
    """TushareConfigPanel 契约守护扩展测试。

    test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
    此处补充 factory 函数守卫 + use_viewmodel 外部 VM 模式 + ft.context.page 访问。
    """

    def test_is_ft_component(self) -> None:
        """DoD: TushareConfigPanel 必须被 @ft.component 装饰。"""
        assert hasattr(TushareConfigPanel, "__wrapped__"), "TushareConfigPanel 必须用 @ft.component 装饰"

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
        """DoD: 3 个 factory 函数必须存在。"""
        source = _read_source()
        assert "def _on_verify_click_factory(" in source
        assert "def _on_save_click_factory(" in source
        assert "def _on_tier_change_factory(" in source

    def test_panel_signature_accepts_vm_and_flags(self) -> None:
        """DoD: TushareConfigPanel 签名应接收 vm + show_save_button + compact + show_register_link。"""
        sig = inspect.signature(TushareConfigPanel)
        assert "vm" in sig.parameters
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_register_link" in sig.parameters
        assert sig.parameters["show_register_link"].default is True


# ============================================================================
# 模块级常量: TUSHARE_POINT_TIERS
# ============================================================================


class TestTusharePointTiersConstant:
    """TUSHARE_POINT_TIERS 常量正确性测试。"""

    def test_tiers_count_is_five(self) -> None:
        """DoD: TUSHARE_POINT_TIERS 必须包含 5 档。"""
        assert len(TUSHARE_POINT_TIERS) == 5

    def test_tiers_members(self) -> None:
        """DoD: TUSHARE_POINT_TIERS 必须包含 5 个标准档位。"""
        expected = ("points_120", "points_2000", "points_5000", "points_10000", "points_15000")
        assert expected == TUSHARE_POINT_TIERS

    def test_tiers_is_tuple(self) -> None:
        """DoD: TUSHARE_POINT_TIERS 必须是 tuple (不可变)。"""
        assert isinstance(TUSHARE_POINT_TIERS, tuple)


# ============================================================================
# 模块级纯函数: _build_tier_options
# ============================================================================


class TestBuildTierOptions:
    """_build_tier_options: 构建 5 档下拉选项 (P1-1: tier_options 由 VM state 产出, View 接收参数)."""

    def test_returns_five_options(self) -> None:
        """DoD: 返回 5 个 ft.dropdown.Option。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_tier_options(TUSHARE_POINT_TIERS)
        assert len(options) == 5
        assert all(isinstance(o, ft.dropdown.Option) for o in options)

    def test_options_keys_match_tiers(self) -> None:
        """DoD: 选项 key 与 TUSHARE_POINT_TIERS 一一对应。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_tier_options(TUSHARE_POINT_TIERS)
        keys = [o.key for o in options]
        assert keys == list(TUSHARE_POINT_TIERS)

    def test_options_text_uses_i18n(self) -> None:
        """DoD: 选项 text 通过 I18n.get(f"sys_tier_{tier}_label") 翻译。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: f"i18n[{key}]"
            options = _build_tier_options(TUSHARE_POINT_TIERS)
        for opt, tier in zip(options, TUSHARE_POINT_TIERS, strict=True):
            assert opt.text == f"i18n[sys_tier_{tier}_label]"
            mock_i18n.get.assert_any_call(f"sys_tier_{tier}_label")


# ============================================================================
# 模块级纯函数: _render_message + R9 守卫
# ============================================================================


class TestRenderMessageR9Guard:
    """R9 红线: _render_message 不应接触 token (View 层不主动泄露敏感信息)。"""

    def test_render_message_none_returns_empty(self) -> None:
        """_render_message(None) 返回空字符串。"""
        assert _render_message(None) == ""

    def test_render_message_with_default_param(self) -> None:
        """_render_message(Message) 调 I18n.get 返回本地化文本。"""
        msg = Message("_raw_msg_", {"default": "raw error text"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "raw error text"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("_raw_msg_", default="raw error text")
        assert result == "raw error text"

    def test_render_message_with_format_params(self) -> None:
        """_render_message 透传 msg.params 给 I18n.get。"""
        msg = Message("tushare_verify_success", {"count": 5})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "验证成功 5 个 API"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("tushare_verify_success", count=5)
        assert result == "验证成功 5 个 API"

    def test_render_message_function_body_has_no_token_reference(self) -> None:
        """R9: _render_message 函数源码不应直接引用 token 字段。

        _render_message 只调 I18n.get(msg.key, **msg.params), 不应:
        - 显式访问 state.token
        - 将 token 作为参数传给 I18n.get
        - 在日志中记录 token
        View 透传 msg.params, VM 负责避免将 token 放入 params。
        """
        import ast

        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_render_message":
                func_start = node.lineno
                func_end = node.end_lineno or node.lineno
                func_lines = source.splitlines()[func_start - 1 : func_end]
                func_body = "\n".join(func_lines)
                assert "token" not in func_body.lower(), (
                    "_render_message 不应直接引用 token (R9): token 应由 VM 管理, 不应进入 View 层"
                )
                return
        raise AssertionError("_render_message 函数未找到")

    def test_panel_source_no_token_in_logger_calls(self) -> None:
        """R9: panel 源码中 logger 调用不应记录 token 值。

        仅检查 state.token / token= 等明显值引用, 排除 verify_token / update_tier /
        save_token 等方法名 (这些是函数标识符, 不是 token 值)。
        """
        source = _read_source()
        logger_calls = re.findall(r"logger\.\w+\([^)]*\)", source)
        for call in logger_calls:
            # state.token 直接访问 token 值字段
            assert "state.token" not in call, f"logger 调用不应记录 state.token 值 (R9): {call}"
            # token= 作为 kwarg 传递 (排除 != / == 等比较运算符)
            assert re.search(r"\btoken\s*=(?!=)", call) is None, f"logger 调用不应将 token 作为 kwarg 传递 (R9): {call}"


# ============================================================================
# 工厂函数: _on_verify_click_factory
# ============================================================================


class TestOnVerifyClickFactory:
    """_on_verify_click_factory: page 可用/None/RuntimeError 守卫。"""

    def test_page_available_calls_run_task(self) -> None:
        """page 可用 → page.run_task(vm.verify_token)。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.verify_token)

    def test_page_none_skips_run_task(self) -> None:
        """page=None → 不调 run_task, 不抛异常。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_runtime_error_swallowed(self) -> None:
        """ft.context.page 抛 RuntimeError → 静默处理, 不抛异常。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())  # 不应抛异常


# ============================================================================
# 工厂函数: _on_save_click_factory (同步, 不通过 run_task)
# ============================================================================


class TestOnSaveClickFactory:
    """_on_save_click_factory: vm.save() 同步调用, 不通过 run_task。"""

    def test_calls_vm_save_directly(self) -> None:
        """DoD: 直接调 vm.save(), 不通过 page.run_task。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        vm.save.assert_called_once()
        mock_page.run_task.assert_not_called()

    def test_save_called_without_args(self) -> None:
        """vm.save() 无参数调用。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        _invoke(handler, _make_event())
        vm.save.assert_called_once_with()


# ============================================================================
# 工厂函数: _on_tier_change_factory
# ============================================================================


class TestOnTierChangeFactory:
    """_on_tier_change_factory: new_tier 空早返回 + page 可用/None/RuntimeError 守卫。"""

    def test_page_available_calls_run_task(self) -> None:
        """page 可用 + new_tier 非空 → page.run_task(vm.update_tier, new_tier)。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_tier_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event("points_2000"))
        mock_page.run_task.assert_called_once_with(vm.update_tier, "points_2000")

    def test_new_tier_none_skips_run_task(self) -> None:
        """new_tier=None → 早返回, 不调 run_task。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_tier_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event(None))
        mock_page.run_task.assert_not_called()

    def test_new_tier_empty_string_skips_run_task(self) -> None:
        """new_tier='' → 早返回, 不调 run_task。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_tier_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event(""))
        mock_page.run_task.assert_not_called()

    def test_page_none_skips_run_task(self) -> None:
        """page=None → 不调 run_task, 不抛异常。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_tier_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event("points_2000"))
        mock_page.run_task.assert_not_called()

    def test_runtime_error_swallowed(self) -> None:
        """ft.context.page 抛 RuntimeError → 静默处理, 不抛异常。"""
        vm = MagicMock(spec=TushareConfigPanelViewModel)
        handler = _on_tier_change_factory(vm)
        with patch("ui.components.config_panels.tushare_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event("points_2000"))  # 不应抛异常


# ============================================================================
# _on_register_click: webbrowser.open_new_tab mock
# ============================================================================


class TestOnRegisterClick:
    """_on_register_click: 打开 Tushare 注册页面。"""

    def test_opens_browser_with_register_url(self) -> None:
        """DoD: 调 webbrowser.open_new_tab(_TUSHARE_REGISTER_URL)。"""
        with patch.object(panel_module, "webbrowser") as mock_webbrowser:
            _invoke(_on_register_click, _make_event())
        mock_webbrowser.open_new_tab.assert_called_once_with(panel_module._TUSHARE_REGISTER_URL)

    def test_register_url_is_tushare_pro(self) -> None:
        """DoD: _TUSHARE_REGISTER_URL 是 tushare.pro 注册页。"""
        assert "tushare.pro/register" in panel_module._TUSHARE_REGISTER_URL


# ============================================================================
# 组件运行时测试基础设施: _FakeTushareConfigPanelVM + _render_panel helper
# ============================================================================


class _FakeTushareConfigPanelVM:
    """模拟 TushareConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。

    state 字段可外部注入, command 方法为 MagicMock 便于断言。
    """

    def __init__(self, state: TushareConfigState | None = None) -> None:
        self._state = state if state is not None else TushareConfigState()
        self._subscribers: list[Any] = []
        # command 方法 (MagicMock, 便于断言调用)
        self.verify_token = MagicMock()
        self.update_tier = MagicMock()
        self.save = MagicMock()
        self.update_token = MagicMock()

    @property
    def state(self) -> TushareConfigState:
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
    state: TushareConfigState | None = None,
    *,
    show_save_button: bool = True,
    compact: bool = False,
    show_register_link: bool = True,
    page: FakePage | None = None,
) -> tuple[_FakeTushareConfigPanelVM, FakePage, Any, Any]:
    """渲染 TushareConfigPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    """
    vm = _FakeTushareConfigPanelVM(state=state)
    if page is None:
        page = FakePage()
    # FakePage 不定义 run_task 属性, 测试动态注入 MagicMock
    cast(Any, page).run_task = MagicMock()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        mock_styles.secondary_button.return_value = ft.ButtonStyle()

        component = make_component(
            TushareConfigPanel,
            vm=vm,
            show_save_button=show_save_button,
            compact=compact,
            show_register_link=show_register_link,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 组件运行时测试: 布局 (compact / standard)
# ============================================================================


class TestTushareConfigPanelLayout:
    """TushareConfigPanel 布局测试 (compact / standard)。"""

    def test_returns_column_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=True → 返回 ft.Column。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Column)

    def test_returns_row_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=False → 返回 ft.Row。"""
        _, _, result, _ = _render_panel(compact=False)
        assert isinstance(result, ft.Row)

    def test_compact_column_has_center_alignment(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=True → Column.horizontal_alignment = CENTER。"""
        _, _, result, _ = _render_panel(compact=True)
        assert result.horizontal_alignment == ft.CrossAxisAlignment.CENTER

    def test_standard_row_has_start_alignment(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=False → Row.alignment = START。"""
        _, _, result, _ = _render_panel(compact=False)
        assert result.alignment == ft.MainAxisAlignment.START

    def test_compact_includes_token_input_and_tier_dropdown(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=True 布局含 token_input + tier_dropdown。"""
        _, _, result, _ = _render_panel(compact=True)
        token_input = _find_text_field(result, "tushare_token_label")
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")
        assert token_input is not None
        assert tier_dd is not None

    def test_standard_includes_token_input_and_tier_dropdown(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact=False 布局含 token_input + tier_dropdown。"""
        _, _, result, _ = _render_panel(compact=False)
        token_input = _find_text_field(result, "tushare_token_label")
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")
        assert token_input is not None
        assert tier_dd is not None


# ============================================================================
# 组件运行时测试: 可见性 (show_save_button / show_register_link)
# ============================================================================


class TestTushareConfigPanelVisibility:
    """TushareConfigPanel 可见性测试 (show_save_button / show_register_link)。"""

    def test_save_button_visible_when_show_save_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=True → save_button.visible=True。"""
        _, _, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE_OUTLINED
        ]
        assert len(save_btns) == 1
        assert save_btns[0].visible is True

    def test_save_button_hidden_when_show_save_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False → save_button 完全不入控件树 (源码用 if show_save_button: buttons.append)。"""
        _, _, result, _ = _render_panel(show_save_button=False)
        ctrls = _walk_controls(result)
        save_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE_OUTLINED
        ]
        assert len(save_btns) == 0

    def test_register_link_present_when_show_register_true_compact(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_register_link=True + compact=True → register_link 在控件树中。"""
        _, _, result, _ = _render_panel(compact=True, show_register_link=True)
        ctrls = _walk_controls(result)
        register_links = [
            c for c in ctrls if isinstance(c, ft.TextButton) and getattr(c, "icon", None) == ft.Icons.OPEN_IN_NEW
        ]
        assert len(register_links) == 1

    def test_register_link_absent_when_show_register_false_compact(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_register_link=False + compact=True → register_link 不在控件树中。"""
        _, _, result, _ = _render_panel(compact=True, show_register_link=False)
        ctrls = _walk_controls(result)
        register_links = [
            c for c in ctrls if isinstance(c, ft.TextButton) and getattr(c, "icon", None) == ft.Icons.OPEN_IN_NEW
        ]
        assert len(register_links) == 0


# ============================================================================
# 组件运行时测试: 状态绑定 (is_verifying / status_icon / token_input password)
# ============================================================================


class TestTushareConfigPanelStateBinding:
    """TushareConfigPanel 状态绑定测试 (is_verifying / status_icon / token_input password)。"""

    def test_token_input_is_password_and_can_reveal(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: token_input.password=True, can_reveal_password=True。"""
        _, _, result, _ = _render_panel()
        token_input = _find_text_field(result, "tushare_token_label")
        assert token_input.password is True
        assert token_input.can_reveal_password is True

    def test_token_input_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """token_input.value 绑定到 state.token。"""
        state = TushareConfigState(token="my-token-value")
        _, _, result, _ = _render_panel(state=state)
        token_input = _find_text_field(result, "tushare_token_label")
        assert token_input.value == "my-token-value"

    def test_tier_dropdown_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """tier_dropdown.value 绑定到 state.tier。"""
        state = TushareConfigState(tier="points_2000")
        _, _, result, _ = _render_panel(state=state)
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")
        assert tier_dd.value == "points_2000"

    def test_tier_dropdown_has_five_options(self, mock_i18n_state, mock_app_colors_state) -> None:
        """tier_dropdown.options 含 5 档。"""
        _, _, result, _ = _render_panel()
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")
        assert len(tier_dd.options) == 5

    def test_verify_button_disabled_when_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=True → verify_button.disabled=True。"""
        state = TushareConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.VERIFIED_USER_OUTLINED
        ]
        assert len(verify_btns) == 1
        assert verify_btns[0].disabled is True

    def test_verify_button_enabled_when_not_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=False → verify_button.disabled=False。"""
        state = TushareConfigState(is_verifying=False)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.VERIFIED_USER_OUTLINED
        ]
        assert len(verify_btns) == 1
        assert verify_btns[0].disabled is False

    def test_save_button_disabled_when_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=True → save_button.disabled=True。"""
        state = TushareConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE_OUTLINED
        ]
        assert len(save_btns) == 1
        assert save_btns[0].disabled is True

    def test_tier_dropdown_disabled_when_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=True → tier_dropdown.disabled=True。"""
        state = TushareConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state)
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")
        assert tier_dd.disabled is True

    def test_status_icon_hidden_when_status_text_empty(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: status_text 空 → status_icon.visible=False。"""
        state = TushareConfigState(status_message=None, status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        # status_icon size=12, 用于区分其他 Icon
        status_icons = [c for c in ctrls if isinstance(c, ft.Icon) and getattr(c, "size", None) == 12]
        assert len(status_icons) == 1
        assert status_icons[0].visible is False

    def test_status_icon_visible_when_status_text_present(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_text 非空 → status_icon.visible=True。"""
        state = TushareConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_success_uses_check_circle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success → CHECK_CIRCLE icon。"""
        state = TushareConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1

    def test_status_icon_error_uses_error_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error → ERROR icon。"""
        state = TushareConfigState(status_message=Message("err"), status_type="error")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR]
        assert len(icons) == 1

    def test_status_icon_warning_uses_warning_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning → WARNING icon。"""
        state = TushareConfigState(status_message=Message("warn"), status_type="warning")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.WARNING]
        assert len(icons) == 1

    def test_status_icon_info_uses_info_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=info → INFO icon。"""
        state = TushareConfigState(status_message=Message("info"), status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1


# ============================================================================
# 组件事件处理器测试: 触发 on_click/on_select/on_change 验证 VM 调用
# ============================================================================


class TestTushareConfigPanelEventHandlers:
    """TushareConfigPanel 事件处理器测试 (page 可用 → page.run_task)。"""

    def test_verify_click_calls_page_run_task_with_vm_verify_token(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """verify button on_click → page.run_task(vm.verify_token)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.VERIFIED_USER_OUTLINED
        ]
        assert len(verify_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(verify_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.verify_token)

    def test_save_click_calls_vm_save_directly(self, mock_i18n_state, mock_app_colors_state) -> None:
        """save button on_click → vm.save() (同步, 不通过 run_task)。"""
        vm, page, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE_OUTLINED
        ]
        assert len(save_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(save_btns[0].on_click, _make_event())
        vm.save.assert_called_once()
        run_task.assert_not_called()

    def test_tier_change_calls_page_run_task_with_vm_update_tier(self, mock_i18n_state, mock_app_colors_state) -> None:
        """tier dropdown on_select → page.run_task(vm.update_tier, new_tier)。"""
        vm, page, result, _ = _render_panel()
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(tier_dd.on_select, _make_event("points_2000"))
        run_task.assert_called_once_with(vm.update_tier, "points_2000")

    def test_tier_change_empty_value_skips_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        """tier dropdown on_select value=None → 不调 run_task。"""
        _, page, result, _ = _render_panel()
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(tier_dd.on_select, _make_event(None))
        run_task.assert_not_called()

    def test_tier_change_empty_string_skips_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        """tier dropdown on_select value='' → 不调 run_task。"""
        _, page, result, _ = _render_panel()
        tier_dd = _find_dropdown(result, "sys_tier_label_in_token_panel")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(tier_dd.on_select, _make_event(""))
        run_task.assert_not_called()

    def test_token_change_calls_vm_update_token(self, mock_i18n_state, mock_app_colors_state) -> None:
        """token input on_change → vm.update_token(value)。"""
        vm, _, result, _ = _render_panel()
        token_input = _find_text_field(result, "tushare_token_label")

        _invoke(token_input.on_change, _make_event("new-token-xxx"))
        vm.update_token.assert_called_once_with("new-token-xxx")

    def test_register_click_calls_webbrowser_open_new_tab(self, mock_i18n_state, mock_app_colors_state) -> None:
        """register link on_click → webbrowser.open_new_tab(url)。"""
        _, _, result, _ = _render_panel(compact=True, show_register_link=True)
        ctrls = _walk_controls(result)
        register_links = [
            c for c in ctrls if isinstance(c, ft.TextButton) and getattr(c, "icon", None) == ft.Icons.OPEN_IN_NEW
        ]
        assert len(register_links) == 1

        with patch.object(panel_module, "webbrowser") as mock_webbrowser:
            _invoke(register_links[0].on_click, _make_event())
        mock_webbrowser.open_new_tab.assert_called_once_with(panel_module._TUSHARE_REGISTER_URL)


# ============================================================================
# 组件挂载/卸载 + VM 订阅生命周期
# ============================================================================


class TestTushareConfigPanelVMLifecycle:
    """TushareConfigPanel VM 订阅生命周期测试 (use_viewmodel 外部 VM 模式)。"""

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


class TestTushareConfigPanelIsolation:
    """R7 守卫: 测试间无单例状态污染 (由 conftest _reset_all_singletons autouse 保证)。"""

    def test_no_singleton_state_leakage_between_tests(self, mock_i18n_state, mock_app_colors_state) -> None:
        """连续渲染两个 panel, 第二个不受第一个影响 (VM 独立)。"""
        vm1, _, result1, _ = _render_panel(state=TushareConfigState(token="token-a", tier="points_120"))
        vm2, _, result2, _ = _render_panel(state=TushareConfigState(token="token-b", tier="points_2000"))

        # 两个 VM 应是独立实例
        assert vm1 is not vm2
        # 两个 result 应反映各自 state
        token_input1 = _find_text_field(result1, "tushare_token_label")
        token_input2 = _find_text_field(result2, "tushare_token_label")
        assert token_input1.value == "token-a"
        assert token_input2.value == "token-b"
        tier_dd1 = _find_dropdown(result1, "sys_tier_label_in_token_panel")
        tier_dd2 = _find_dropdown(result2, "sys_tier_label_in_token_panel")
        assert tier_dd1.value == "points_120"
        assert tier_dd2.value == "points_2000"
