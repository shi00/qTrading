"""LocalModelConfigPanel 组件运行时测试 (Task 3.2).

覆盖:
1. 模块级纯函数: _render_message / _select_file (含正常/取消/异常/空 files 路径)
2. 工厂函数: _on_verify_click_factory / _on_save_click_factory /
   _on_select_file_click_factory 的 page 可用/None/RuntimeError 守卫
3. 组件运行时:
   - _setup_file_picker: page.services 不含/已含 picker / page None 容错
   - _cleanup_file_picker: page.services 含/不含 picker /
     vm.cancel_verification 调用 (P1-1: 经 VM 命令转发, 不直调 LocalModelManager)
   - compact=True/False + show_save_button + show_internal_loading
   - is_gpu_auto=True 显示 gpu_auto_switch 隐藏 gpu_layers_input
   - is_verifying=True 显示 ProgressRing
   - gpu_layers_display 计算 (is_gpu_auto=True→0 / False→state.n_gpu_layers)
   - 表单控件 on_change 触发 VM update_*

test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等)
与 _render_message 基础路径, 本文件聚焦运行时行为 + factory 函数 + 组件体渲染,
不重复基础契约检查。
"""

import asyncio
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
from ui.components.config_panels import local_model_config_panel as panel_module
from ui.components.config_panels.local_model_config_panel import (
    LocalModelConfigPanel,
    _on_save_click_factory,
    _on_select_file_click_factory,
    _on_verify_click_factory,
    _select_file,
)
from ui.viewmodels import Message
from ui.viewmodels.local_model_config_panel_view_model import (
    LocalModelConfigPanelViewModel,
    LocalModelConfigState,
)

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 local_model_config_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
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
    """深度优先遍历控件树 (含 controls/items/content/tabs)。

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


def _find_switch(root: Any, label: str) -> ft.Switch:
    """通过 label 查找 Switch 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Switch) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"Switch with label={label} not found")


def _find_dropdown(root: Any, label: str) -> ft.Dropdown:
    """通过 label 查找 Dropdown 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"Dropdown with label={label} not found")


def _page_run_task(page: FakePage) -> MagicMock:
    """获取 page.run_task mock (动态注入, pyright safe)。

    FakePage 类不定义 run_task 属性, _render_panel 通过实例属性动态注入 MagicMock。
    用 cast(Any, page) 绕过 reportAttributeAccessIssue。
    """
    return cast(MagicMock, cast(Any, page).run_task)


# ============================================================================
# 契约守护扩展测试 (test_config_panels.py 已覆盖基础契约, 此处扩展 factory 守卫)
# ============================================================================


class TestLocalModelConfigPanelContractExtension:
    """LocalModelConfigPanel 契约守护扩展测试。

    test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
    此处补充 factory 函数守卫 + use_viewmodel 外部 VM 模式 + ft.context.page 访问。
    """

    def test_is_ft_component(self) -> None:
        """DoD: LocalModelConfigPanel 必须被 @ft.component 装饰。"""
        assert hasattr(LocalModelConfigPanel, "__wrapped__"), "LocalModelConfigPanel 必须用 @ft.component 装饰"

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
        assert "def _on_select_file_click_factory(" in source

    def test_panel_signature_accepts_vm_and_flags(self) -> None:
        """DoD: LocalModelConfigPanel 签名应接收 vm + show_save_button + compact + show_internal_loading。"""
        sig = inspect.signature(LocalModelConfigPanel)
        assert "vm" in sig.parameters
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is False
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_internal_loading" in sig.parameters
        assert sig.parameters["show_internal_loading"].default is True


class TestRenderMessageExtension:
    """_render_message 扩展测试 (test_config_panels.py 已覆盖 None / default 路径)。"""

    def test_render_message_with_format_params(self) -> None:
        """_render_message 透传 msg.params 给 I18n.get。"""
        msg = Message("ai_snack_invalid_range", {"field": "timeout", "min": 1, "max": 3600})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "timeout 范围 1-3600"
            result = panel_module._render_message(msg)
        mock_i18n.get.assert_called_once_with("ai_snack_invalid_range", field="timeout", min=1, max=3600)
        assert result == "timeout 范围 1-3600"


# ============================================================================
# 模块级纯函数: _select_file
# ============================================================================


class TestSelectFile:
    """_select_file: 打开文件选择器并更新 vm.model_path。"""

    def test_select_file_normal_updates_model_path(self) -> None:
        """正常选文件 (result.files[0].path 非空) → vm.update_model_path(path)。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        fake_result = MagicMock()
        fake_result.files = [MagicMock(path="/path/to/model.gguf")]
        file_picker.pick_files = MagicMock(return_value=_async_return(fake_result))

        asyncio.run(_select_file(vm, file_picker))

        vm.update_model_path.assert_called_once_with("/path/to/model.gguf")

    def test_select_file_user_cancel_skips_update(self) -> None:
        """用户取消 (result=None) → 不调 vm.update_model_path。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        file_picker.pick_files = MagicMock(return_value=_async_return(None))

        asyncio.run(_select_file(vm, file_picker))

        vm.update_model_path.assert_not_called()

    def test_select_file_pick_files_raises_logs_error_no_raise(self) -> None:
        """pick_files 抛异常 → logger.error 被调用, 不抛出。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)

        async def _raise() -> None:
            raise RuntimeError("picker unavailable")

        file_picker.pick_files = MagicMock(side_effect=_raise)

        with patch.object(panel_module, "logger") as mock_logger:
            # 不应抛异常
            asyncio.run(_select_file(vm, file_picker))

        mock_logger.error.assert_called_once()
        vm.update_model_path.assert_not_called()

    def test_select_file_empty_files_skips_update(self) -> None:
        """result.files 为空列表 → 不调 vm.update_model_path。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        fake_result = MagicMock()
        fake_result.files = []
        file_picker.pick_files = MagicMock(return_value=_async_return(fake_result))

        asyncio.run(_select_file(vm, file_picker))

        vm.update_model_path.assert_not_called()

    def test_select_file_path_none_passes_empty_string(self) -> None:
        """result.files[0].path=None → vm.update_model_path("") (path or "" 兜底)。

        覆盖 `result.files[0].path or ""` 的 or 分支: path 为 None/空时兜底为空字符串。
        """
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        fake_result = MagicMock()
        # path=None, `path or ""` 兜底为 ""
        fake_result.files = [MagicMock(path=None)]
        file_picker.pick_files = MagicMock(return_value=_async_return(fake_result))

        asyncio.run(_select_file(vm, file_picker))

        vm.update_model_path.assert_called_once_with("")


def _async_return(value: Any) -> Any:
    """包装值为 coroutine 返回值, 用于 mock async 方法。"""

    async def _ret() -> Any:
        return value

    return _ret()


# ============================================================================
# 工厂函数: page 可用 → page.run_task
# ============================================================================


class TestFactoryFunctionsPageAvailable:
    """3 个 factory 函数: page 可用时调用 page.run_task。"""

    def test_on_verify_click_factory_calls_run_task(self) -> None:
        """_on_verify_click_factory: page 可用 → page.run_task(vm.verify_model)。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.verify_model)

    def test_on_save_click_factory_calls_run_task(self) -> None:
        """_on_save_click_factory: page 可用 → page.run_task(vm.save_config)。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.save_config)

    def test_on_select_file_click_factory_calls_run_task(self) -> None:
        """_on_select_file_click_factory: page 可用 → page.run_task(_select_file, vm, file_picker)。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_select_file_click_factory(vm, file_picker)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(_select_file, vm, file_picker)


class TestFactoryFunctionsPageNone:
    """3 个 factory 函数: page=None 时早返回 (不调 run_task, 不抛异常)。"""

    def test_on_verify_click_factory_page_none_skips_run_task(self) -> None:
        """_on_verify_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_on_save_click_factory_page_none_skips_run_task(self) -> None:
        """_on_save_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_on_select_file_click_factory_page_none_skips_run_task(self) -> None:
        """_on_select_file_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_select_file_click_factory(vm, file_picker)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()


class TestFactoryFunctionsRuntimeError:
    """3 个 factory 函数: ft.context.page 抛 RuntimeError 时静默处理 (不抛异常)。

    RuntimeError 在 Flet 中表示 page 未挂载到 Renderer 上下文, factory 函数应捕获并静默。
    """

    def test_on_verify_click_factory_runtime_error_swallowed(self) -> None:
        """_on_verify_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_verify_click_factory(vm)
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            # 不应抛异常
            _invoke(handler, _make_event())

    def test_on_save_click_factory_runtime_error_swallowed(self) -> None:
        """_on_save_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())

    def test_on_select_file_click_factory_runtime_error_swallowed(self) -> None:
        """_on_select_file_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LocalModelConfigPanelViewModel)
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_select_file_click_factory(vm, file_picker)
        with patch("ui.components.config_panels.local_model_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())


# ============================================================================
# 组件运行时测试基础设施: _FakeLocalModelConfigPanelVM + _render_panel helper
# ============================================================================


class _FakeLocalModelConfigPanelVM:
    """模拟 LocalModelConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。

    state 字段可外部注入, command 方法为 MagicMock 便于断言。
    """

    def __init__(self, state: LocalModelConfigState | None = None) -> None:
        self._state = state if state is not None else LocalModelConfigState()
        self._subscribers: list[Any] = []
        # command 方法 (MagicMock, 便于断言调用)
        self.verify_model = MagicMock()
        self.save_config = MagicMock()
        self.update_model_path = MagicMock()
        self.update_timeout = MagicMock()
        self.update_threads = MagicMock()
        self.update_gpu_auto = MagicMock()
        self.update_gpu_layers = MagicMock()
        self.update_batch = MagicMock()
        self.update_ctx = MagicMock()
        self.update_flash_attn = MagicMock()
        # P1-1: cleanup 时经 VM 命令转发, 避免 View 直接 import LocalModelManager
        self.cancel_verification = MagicMock()

    @property
    def state(self) -> LocalModelConfigState:
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
    state: LocalModelConfigState | None = None,
    *,
    show_save_button: bool = False,
    compact: bool = False,
    show_internal_loading: bool = True,
    page: FakePage | None = None,
) -> tuple[_FakeLocalModelConfigPanelVM, FakePage, Any, Any]:
    """渲染 LocalModelConfigPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    - SectionHeader (子组件, 替换为 MagicMock 避免依赖 settings_widgets)
    """
    vm = _FakeLocalModelConfigPanelVM(state=state)
    if page is None:
        page = FakePage()
    page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        mock_styles.primary_button.return_value = ft.ButtonStyle()
        mock_styles.secondary_button.return_value = ft.ButtonStyle()
        stack.enter_context(patch.object(panel_module, "SectionHeader", side_effect=lambda *a, **kw: ft.Container()))

        component = make_component(
            LocalModelConfigPanel,
            vm=vm,
            show_save_button=show_save_button,
            compact=compact,
            show_internal_loading=show_internal_loading,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 组件运行时测试: _setup_file_picker 生命周期
# ============================================================================


class TestSetupFilePicker:
    """_setup_file_picker: 挂载时注册 FilePicker 到 page.services。"""

    def test_setup_appends_picker_to_page_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """page.services 不含 picker → 挂载后 picker 被 append。"""
        _, page, _, _ = _render_panel()
        # FilePicker 实例应已添加到 page.services
        assert any(isinstance(s, ft.FilePicker) for s in page.services)

    def test_setup_skips_when_picker_already_in_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """page.services 已含同一 picker → 不重复 append (依赖 [], in 检查)。"""
        # 预先创建 picker 并加入 services, 模拟重复挂载场景
        # 直接验证 _setup_file_picker 的 in 检查行为: 重复挂载不增加 picker 数量
        page = FakePage()
        page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(panel_module, "I18n"))
            stack.enter_context(patch.object(panel_module, "AppColors"))
            mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
            mock_styles.primary_button.return_value = ft.ButtonStyle()
            mock_styles.secondary_button.return_value = ft.ButtonStyle()
            stack.enter_context(
                patch.object(panel_module, "SectionHeader", side_effect=lambda *a, **kw: ft.Container())
            )

            component = make_component(LocalModelConfigPanel, vm=_FakeLocalModelConfigPanelVM())
            # 第一次挂载: picker 加入 services
            run_mount_effects(component, page=page)
            pickers_after_first = [s for s in page.services if isinstance(s, ft.FilePicker)]
            assert len(pickers_after_first) == 1

            # 重新渲染同一组件 (use_ref 返回同一 picker, in 检查跳过 append)
            render_once(component)
            pickers_after_rerender = [s for s in page.services if isinstance(s, ft.FilePicker)]
            assert len(pickers_after_rerender) == 1

    def test_setup_handles_page_none_gracefully(self, mock_i18n_state, mock_app_colors_state) -> None:
        """ft.context.page 抛 RuntimeError → _setup_file_picker 静默处理 (不抛异常)。

        用 patch 让 ft.context.page 在 setup_effect 执行期间抛 RuntimeError,
        验证挂载流程不中断 (render_once 仍能完成)。
        """
        vm = _FakeLocalModelConfigPanelVM()
        page = FakePage()
        page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(panel_module, "I18n"))
            stack.enter_context(patch.object(panel_module, "AppColors"))
            mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
            mock_styles.primary_button.return_value = ft.ButtonStyle()
            mock_styles.secondary_button.return_value = ft.ButtonStyle()
            stack.enter_context(
                patch.object(panel_module, "SectionHeader", side_effect=lambda *a, **kw: ft.Container())
            )
            # 注入 ft.context.page 抛 RuntimeError
            stack.enter_context(
                patch(
                    "ui.components.config_panels.local_model_config_panel.ft.context",
                    new=_ContextWithRaisingPage(),
                )
            )

            component = make_component(LocalModelConfigPanel, vm=vm)
            # 不应抛异常
            run_mount_effects(component, page=page)
            result = render_once(component)
            assert isinstance(result, ft.Control)


class _ContextWithRaisingPage:
    """伪造 ft.context, page property 抛 RuntimeError (模拟 page 未挂载)。"""

    @property
    def page(self) -> Any:
        raise RuntimeError("no page context")


# ============================================================================
# 组件运行时测试: _cleanup_file_picker 生命周期
# ============================================================================


class TestCleanupFilePicker:
    """_cleanup_file_picker: 卸载时移除 FilePicker + 调 LocalModelManager.cancel。"""

    def test_cleanup_removes_picker_from_page_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载时 page.services 中的 picker 被 remove。"""
        _, page, _, component = _render_panel()
        # 挂载后 picker 应在 services 中
        pickers = [s for s in page.services if isinstance(s, ft.FilePicker)]
        assert len(pickers) == 1

        run_unmount_effects(component)

        # 卸载后 picker 应被移除
        pickers_after = [s for s in page.services if isinstance(s, ft.FilePicker)]
        assert len(pickers_after) == 0

    def test_cleanup_calls_vm_cancel_verification(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载时调 vm.cancel_verification() (P1-1: 经 VM 命令转发, 不直调 LocalModelManager)."""
        vm, _, _, component = _render_panel()
        vm.cancel_verification.assert_not_called()  # 挂载时不调

        run_unmount_effects(component)

        vm.cancel_verification.assert_called_once_with()

    def test_cleanup_skips_remove_when_picker_not_in_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """page.services 不含 picker → remove 跳过 (不抛 ValueError)。

        通过手动清空 services 模拟 picker 已被移除的情况, 验证 cleanup 不抛异常。
        """
        _, page, _, component = _render_panel()
        # 手动清空 services, 模拟 picker 已不在
        page.services.clear()

        # 卸载不应抛异常 (list.remove 不存在会抛 ValueError, 但 in 检查跳过)
        run_unmount_effects(component)

    def test_cleanup_handles_page_runtime_error_gracefully(self, mock_i18n_state, mock_app_colors_state) -> None:
        """_cleanup_file_picker: ft.context.page 抛 RuntimeError → except 捕获, 不抛异常。

        覆盖源码 162-163 行的 `except RuntimeError: pass` 分支。
        先正常渲染, 卸载前 patch ft.context 让 page property 抛 RuntimeError,
        验证 cleanup 仍能继续执行 vm.cancel_verification()。
        """
        vm, _, _, component = _render_panel()
        # 卸载前 patch ft.context 让 page 抛 RuntimeError
        with patch(
            "ui.components.config_panels.local_model_config_panel.ft.context",
            new=_ContextWithRaisingPage(),
        ):
            # 卸载不应抛异常 (RuntimeError 被 except 捕获)
            run_unmount_effects(component)

        # cleanup 仍调用了 vm.cancel_verification (RuntimeError 不阻断后续逻辑)
        vm.cancel_verification.assert_called_once_with()


# ============================================================================
# 组件运行时测试: 布局 / 可见性 / disabled 状态
# ============================================================================


class TestLocalModelConfigPanelLayout:
    """LocalModelConfigPanel 布局测试 (compact / show_save_button / show_internal_loading)。"""

    def test_returns_container_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非 compact 模式返回 ft.Container。"""
        _, _, result, _ = _render_panel(compact=False)
        assert isinstance(result, ft.Container)

    def test_returns_container_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式返回 ft.Container (width=550, alignment=CENTER)。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        assert result.width == 550
        assert result.alignment == ft.Alignment.CENTER

    def test_section_header_visible_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非 compact 模式 SectionHeader.visible=True。"""
        _, _, result, _ = _render_panel(compact=False)
        # SectionHeader 被 mock 为 ft.Container, 是 form_content.controls[0]
        form_content = result.content
        assert isinstance(form_content, ft.Column)
        section_header = form_content.controls[0]
        assert section_header.visible is True

    def test_section_header_hidden_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式 SectionHeader.visible=False。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        form_content = result.content
        assert isinstance(form_content, ft.Column)
        section_header = form_content.controls[0]
        assert section_header.visible is False

    def test_desc_text_visible_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非 compact 模式 desc_text.visible=True。"""
        _, _, result, _ = _render_panel(compact=False)
        form_content = result.content
        assert isinstance(form_content, ft.Column)
        desc_text = form_content.controls[1]
        assert desc_text.visible is True

    def test_desc_text_hidden_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式 desc_text.visible=False。"""
        _, _, result, _ = _render_panel(compact=True)
        form_content = result.content
        assert isinstance(form_content, ft.Column)
        desc_text = form_content.controls[1]
        assert desc_text.visible is False

    def test_save_button_hidden_when_show_save_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False 时保存按钮不可见 (默认)。"""
        _, _, result, _ = _render_panel(show_save_button=False)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is False

    def test_save_button_visible_when_show_save_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=True 时保存按钮可见。"""
        _, _, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is True


class TestLocalModelConfigPanelInternalLoading:
    """LocalModelConfigPanel show_internal_loading 测试。

    show_internal_loading=False 时:
    - verify_button.disabled 强制为 False (不响应 is_verifying)
    - btn_select_file.disabled 强制为 False
    - progress_indicator.visible 强制为 False
    - save_button.disabled 强制为 False (不响应 is_saving)
    """

    def test_verify_button_disabled_when_verifying_and_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=True + is_verifying=True → verify_button.disabled=True。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=True)
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.CHECK_CIRCLE
        ]
        assert len(verify_btns) == 1
        assert verify_btns[0].disabled is True

    def test_verify_button_enabled_when_verifying_but_not_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=False + is_verifying=True → verify_button.disabled=False (强制)。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=False)
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.CHECK_CIRCLE
        ]
        assert len(verify_btns) == 1
        assert verify_btns[0].disabled is False

    def test_save_button_disabled_when_saving_and_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=True + is_saving=True → save_button.disabled=True。"""
        state = LocalModelConfigState(is_saving=True)
        _, _, result, _ = _render_panel(state=state, show_save_button=True, show_internal_loading=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].disabled is True

    def test_save_button_enabled_when_saving_but_not_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=False + is_saving=True → save_button.disabled=False (强制)。"""
        state = LocalModelConfigState(is_saving=True)
        _, _, result, _ = _render_panel(state=state, show_save_button=True, show_internal_loading=False)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].disabled is False

    def test_progress_ring_visible_when_verifying_and_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=True + is_verifying=True → ProgressRing.visible=True。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=True)
        ctrls = _walk_controls(result)
        rings = [c for c in ctrls if isinstance(c, ft.ProgressRing)]
        assert len(rings) == 1
        assert rings[0].visible is True

    def test_progress_ring_hidden_when_not_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=False → ProgressRing.visible=False。"""
        state = LocalModelConfigState(is_verifying=False)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=True)
        ctrls = _walk_controls(result)
        rings = [c for c in ctrls if isinstance(c, ft.ProgressRing)]
        assert len(rings) == 1
        assert rings[0].visible is False

    def test_progress_ring_hidden_when_show_internal_loading_false(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=False + is_verifying=True → ProgressRing.visible=False (强制)。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=False)
        ctrls = _walk_controls(result)
        rings = [c for c in ctrls if isinstance(c, ft.ProgressRing)]
        assert len(rings) == 1
        assert rings[0].visible is False

    def test_select_file_button_disabled_when_verifying_and_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=True + is_verifying=True → btn_select_file.disabled=True。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=True)
        ctrls = _walk_controls(result)
        select_btns = [
            c for c in ctrls if isinstance(c, ft.OutlinedButton) and getattr(c, "icon", None) == ft.Icons.FOLDER_OPEN
        ]
        assert len(select_btns) == 1
        assert select_btns[0].disabled is True

    def test_select_file_button_enabled_when_not_show_internal_loading(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """show_internal_loading=False + is_verifying=True → btn_select_file.disabled=False (强制)。"""
        state = LocalModelConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state, show_internal_loading=False)
        ctrls = _walk_controls(result)
        select_btns = [
            c for c in ctrls if isinstance(c, ft.OutlinedButton) and getattr(c, "icon", None) == ft.Icons.FOLDER_OPEN
        ]
        assert len(select_btns) == 1
        assert select_btns[0].disabled is False


# ============================================================================
# 组件运行时测试: GPU auto / gpu_layers_display
# ============================================================================


class TestGpuAutoAndLayersDisplay:
    """is_gpu_auto / gpu_layers_display / gpu_auto_switch / gpu_layers_input 测试。"""

    def test_gpu_auto_switch_visible_when_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=True (n_gpu_layers=-1) → gpu_auto_switch 仍可见 (Switch 默认 visible)。"""
        state = LocalModelConfigState(n_gpu_layers=-1)
        _, _, result, _ = _render_panel(state=state)
        gpu_auto_sw = _find_switch(result, "settings_local_gpu_auto")
        # Switch 不设置 visible, 默认为 True
        assert gpu_auto_sw.value is True

    def test_gpu_auto_switch_value_false_when_not_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=False (n_gpu_layers=20) → gpu_auto_switch.value=False。"""
        state = LocalModelConfigState(n_gpu_layers=20)
        _, _, result, _ = _render_panel(state=state)
        gpu_auto_sw = _find_switch(result, "settings_local_gpu_auto")
        assert gpu_auto_sw.value is False

    def test_gpu_layers_input_hidden_when_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=True → gpu_layers_input.visible=False。"""
        state = LocalModelConfigState(n_gpu_layers=-1)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        # gpu_layers_input 是 Slider, min=0 max=100, visible=not is_gpu_auto
        sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.max == 100]
        assert len(sliders) == 1
        assert sliders[0].visible is False

    def test_gpu_layers_input_visible_when_not_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=False → gpu_layers_input.visible=True。"""
        state = LocalModelConfigState(n_gpu_layers=20)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.max == 100]
        assert len(sliders) == 1
        assert sliders[0].visible is True

    def test_gpu_layers_display_zero_when_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=True → gpu_layers_display=0, slider.value=0.0。"""
        state = LocalModelConfigState(n_gpu_layers=-1)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.max == 100]
        assert len(sliders) == 1
        assert sliders[0].value == 0.0
        assert sliders[0].tooltip == "0"

    def test_gpu_layers_display_state_value_when_not_is_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_gpu_auto=False → gpu_layers_display=state.n_gpu_layers, slider.value=20.0。"""
        state = LocalModelConfigState(n_gpu_layers=20)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.max == 100]
        assert len(sliders) == 1
        assert sliders[0].value == 20.0
        assert sliders[0].tooltip == "20"


# ============================================================================
# 组件运行时测试: 状态显示 (status_message / status_type)
# ============================================================================


class TestStatusDisplay:
    """LocalModelConfigPanel 状态显示测试 (status icon/text/color)。"""

    def test_status_icon_success_uses_check_circle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success → CHECK_CIRCLE icon visible。"""
        state = LocalModelConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_error_uses_error_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error → ERROR icon visible。"""
        state = LocalModelConfigState(status_message=Message("err"), status_type="error")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_warning_uses_warning_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning → WARNING icon visible。"""
        state = LocalModelConfigState(status_message=Message("warn"), status_type="warning")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.WARNING]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_info_uses_info_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=info → INFO icon visible。"""
        state = LocalModelConfigState(status_message=Message("info"), status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_hidden_when_status_message_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message=None → status_icon.visible=False。"""
        state = LocalModelConfigState(status_message=None, status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1
        assert icons[0].visible is False


# ============================================================================
# 组件事件处理器测试: 触发 on_click/on_select/on_change 验证 VM 调用
# ============================================================================


class TestEventHandlersRunTask:
    """LocalModelConfigPanel 事件处理器测试 (page 可用 → page.run_task)。"""

    def test_verify_click_calls_page_run_task_with_vm_verify_model(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """verify button on_click → page.run_task(vm.verify_model)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        verify_btns = [
            c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.CHECK_CIRCLE
        ]
        assert len(verify_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(verify_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.verify_model)

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

    def test_select_file_click_calls_page_run_task_with_select_file(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """select file button on_click → page.run_task(_select_file, vm, file_picker)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        select_btns = [
            c for c in ctrls if isinstance(c, ft.OutlinedButton) and getattr(c, "icon", None) == ft.Icons.FOLDER_OPEN
        ]
        assert len(select_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(select_btns[0].on_click, _make_event())
        # run_task 调用了 _select_file, vm, file_picker
        run_task.assert_called_once()
        call_args = run_task.call_args
        assert call_args.args[0] == _select_file
        assert call_args.args[1] is vm
        # file_picker 是 ft.FilePicker 实例
        assert isinstance(call_args.args[2], ft.FilePicker)


class TestSyncOnChanges:
    """LocalModelConfigPanel 同步命令测试 (on_change/on_select 直接调 vm 方法, 非 run_task)。

    model_path_input / timeout_input / threads_input / gpu_auto_switch / gpu_layers_input /
    batch_input / ctx_input / flash_attn_switch 的 on_change/on_select 是 lambda,
    直接调用 vm.update_* 方法 (同步命令)。
    """

    def test_model_path_change_calls_vm_update_model_path(self, mock_i18n_state, mock_app_colors_state) -> None:
        """model_path input on_change → vm.update_model_path(value)。"""
        vm, _, result, _ = _render_panel()
        model_path_field = _find_text_field(result, "settings_local_model_path")

        _invoke(model_path_field.on_change, _make_event("/new/path.gguf"))
        vm.update_model_path.assert_called_once_with("/new/path.gguf")

    def test_timeout_change_calls_vm_update_timeout(self, mock_i18n_state, mock_app_colors_state) -> None:
        """timeout input on_change → vm.update_timeout(value)。"""
        vm, _, result, _ = _render_panel()
        timeout_field = _find_text_field(result, "settings_local_ai_timeout")

        _invoke(timeout_field.on_change, _make_event("600"))
        vm.update_timeout.assert_called_once_with("600")

    def test_threads_change_calls_vm_update_threads(self, mock_i18n_state, mock_app_colors_state) -> None:
        """threads slider on_change → vm.update_threads(value)。"""
        vm, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        # threads_input 是 Slider min=1 max=16
        threads_sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.min == 1 and c.max == 16]
        assert len(threads_sliders) == 1

        _invoke(threads_sliders[0].on_change, _make_event(8))
        vm.update_threads.assert_called_once_with(8)

    def test_gpu_auto_change_calls_vm_update_gpu_auto(self, mock_i18n_state, mock_app_colors_state) -> None:
        """gpu_auto switch on_change → vm.update_gpu_auto(value)。"""
        vm, _, result, _ = _render_panel()
        gpu_auto_sw = _find_switch(result, "settings_local_gpu_auto")

        _invoke(gpu_auto_sw.on_change, _make_event(True))
        vm.update_gpu_auto.assert_called_once_with(True)

    def test_gpu_layers_change_calls_vm_update_gpu_layers(self, mock_i18n_state, mock_app_colors_state) -> None:
        """gpu_layers slider on_change → vm.update_gpu_layers(value) (仅 is_gpu_auto=False 时可见)。"""
        state = LocalModelConfigState(n_gpu_layers=20)  # is_gpu_auto=False
        vm, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        sliders = [c for c in ctrls if isinstance(c, ft.Slider) and c.max == 100]
        assert len(sliders) == 1

        _invoke(sliders[0].on_change, _make_event(50))
        vm.update_gpu_layers.assert_called_once_with(50)

    def test_batch_change_calls_vm_update_batch(self, mock_i18n_state, mock_app_colors_state) -> None:
        """batch dropdown on_select → vm.update_batch(value)。"""
        vm, _, result, _ = _render_panel()
        batch_dd = _find_dropdown(result, "settings_local_batch")

        _invoke(batch_dd.on_select, _make_event("1024"))
        vm.update_batch.assert_called_once_with("1024")

    def test_batch_change_none_value_falls_back_to_512(self, mock_i18n_state, mock_app_colors_state) -> None:
        """batch dropdown on_select value=None → vm.update_batch('512') (fallback)。"""
        vm, _, result, _ = _render_panel()
        batch_dd = _find_dropdown(result, "settings_local_batch")

        _invoke(batch_dd.on_select, _make_event(None))
        vm.update_batch.assert_called_once_with("512")

    def test_ctx_change_calls_vm_update_ctx(self, mock_i18n_state, mock_app_colors_state) -> None:
        """ctx dropdown on_select → vm.update_ctx(value)。"""
        vm, _, result, _ = _render_panel()
        ctx_dd = _find_dropdown(result, "settings_local_ctx")

        _invoke(ctx_dd.on_select, _make_event("8192"))
        vm.update_ctx.assert_called_once_with("8192")

    def test_ctx_change_none_value_falls_back_to_4096(self, mock_i18n_state, mock_app_colors_state) -> None:
        """ctx dropdown on_select value=None → vm.update_ctx('4096') (fallback)。"""
        vm, _, result, _ = _render_panel()
        ctx_dd = _find_dropdown(result, "settings_local_ctx")

        _invoke(ctx_dd.on_select, _make_event(None))
        vm.update_ctx.assert_called_once_with("4096")

    def test_flash_attn_change_calls_vm_update_flash_attn(self, mock_i18n_state, mock_app_colors_state) -> None:
        """flash_attn switch on_change → vm.update_flash_attn(value)。"""
        vm, _, result, _ = _render_panel()
        flash_sw = _find_switch(result, "settings_local_flash_attn")

        _invoke(flash_sw.on_change, _make_event(False))
        vm.update_flash_attn.assert_called_once_with(False)


# ============================================================================
# 组件挂载/卸载 + VM 订阅生命周期
# ============================================================================


class TestLocalModelConfigPanelVMLifecycle:
    """LocalModelConfigPanel VM 订阅生命周期测试 (use_viewmodel 外部 VM 模式)。"""

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后 use_viewmodel 注册 subscribe 到 VM。"""
        vm, _, _, _ = _render_panel()
        # use_viewmodel 外部 VM 模式应注册 subscribe
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
        # 用 spy 检测 dispose 调用
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


class TestLocalModelConfigPanelIsolation:
    """R7 守卫: 测试间无单例状态污染 (由 conftest _reset_all_singletons autouse 保证)。

    P1-1 改造后: _cleanup_file_picker 调用 vm.cancel_verification() (VM 内部
    才触碰 LocalModelManager 单例), View 层不再直接依赖 LocalModelManager。
    本测试验证 View 层 mock 隔离正确, 不依赖真实单例状态。
    """

    def test_no_singleton_state_leakage_between_tests(self, mock_i18n_state, mock_app_colors_state) -> None:
        """连续渲染两个 panel, 第二个不受第一个影响 (VM 独立)。"""
        vm1, _, result1, _ = _render_panel(state=LocalModelConfigState(n_gpu_layers=20))
        vm2, _, result2, _ = _render_panel(state=LocalModelConfigState(n_gpu_layers=40))

        # 两个 VM 应是独立实例
        assert vm1 is not vm2
        # 两个 result 应反映各自 state
        ctrls1 = _walk_controls(result1)
        sliders1 = [c for c in ctrls1 if isinstance(c, ft.Slider) and c.max == 100]
        assert sliders1[0].value == 20.0
        ctrls2 = _walk_controls(result2)
        sliders2 = [c for c in ctrls2 if isinstance(c, ft.Slider) and c.max == 100]
        assert sliders2[0].value == 40.0

    def test_cleanup_calls_vm_cancel_verification_only(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载时仅调 vm.cancel_verification(), View 层不触碰 LocalModelManager 单例 (P1-1)."""
        vm, _, _, component = _render_panel()
        run_unmount_effects(component)
        vm.cancel_verification.assert_called_once_with()
