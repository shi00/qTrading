"""NewsSentimentConfigPanel 组件运行时测试 (N5-4 覆盖率补齐).

覆盖:
1. 模块级纯函数: _pick_and_set (正常/取消/空结果/path=None/异常路径)
2. 工厂函数: _on_pick_click_factory 的 page 可用/None/RuntimeError 守卫
3. 组件运行时:
   - _setup_file_picker: page.services 注册/去重/page None 容错
   - _cleanup_file_picker: 移除/不在 services/page RuntimeError 容错
   - 开关渲染 + on_change 触发 VM setter
   - 三路径行: TextField label/value + on_change 触发对应 setter
   - 三选择按钮 on_click → page.run_task(_pick_and_set, ...)
   - on_submit_save 传入/None 时 on_submit 存在性
   - VM 订阅生命周期 (外部 VM 模式: 挂载订阅/卸载退订/不 dispose)

test_news_sentiment_config_panel_contract.py 已覆盖源码契约扫描
(@ft.component / use_ref / use_effect / R23 等), 本文件聚焦运行时行为,
不重复契约检查。范式照 test_local_model_config_panel.py。
"""

import asyncio
import contextlib
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import news_sentiment_config_panel as panel_module
from ui.components.config_panels.news_sentiment_config_panel import (
    _FILE_TARGETS,
    _on_pick_click_factory,
    _pick_and_set,
    NewsSentimentConfigPanel,
)

pytestmark = pytest.mark.unit


def _async_return(value: Any) -> Any:
    """包装值为 coroutine 返回值, 用于 mock async 方法。"""

    async def _ret() -> Any:
        return value

    return _ret()


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe, 绕过 Optional Callable 调用检查)。"""
    handler(*args)


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock (e.control.value 供 get_control_value / lambda 读取)。"""
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    """递归展平渲染树 (Component 递归 render_once, 按 controls/content 遍历)。"""
    from flet.components.component import Component

    if root is None:
        return []
    if isinstance(root, Component):
        return _walk_controls(render_once(root))
    if not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content_attr = getattr(root, "content", None)
    if isinstance(content_attr, (ft.Control, Component)):
        result.extend(_walk_controls(content_attr))
    return result


def _find_switch(root: Any) -> ft.Switch:
    """查找 news sentiment 开关 (唯一 Switch)。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Switch):
            return ctrl
    raise AssertionError("NewsSentimentConfigPanel 渲染树中未找到 Switch")


def _find_text_field(root: Any, label: str) -> ft.TextField:
    """通过 label 查找 TextField 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"TextField with label={label} not found")


def _find_select_buttons(root: Any) -> list[ft.OutlinedButton]:
    """按 FOLDER_OPEN 图标查找三个「选择文件」按钮 (渲染顺序 = _FILE_TARGETS 顺序)。"""
    return [
        ctrl
        for ctrl in _walk_controls(root)
        if isinstance(ctrl, ft.OutlinedButton) and getattr(ctrl, "icon", None) == ft.Icons.FOLDER_OPEN
    ]


# ============================================================================
# 组件运行时测试基础设施: _FakeNewsSentimentVM + _render_panel helper
# ============================================================================


class _FakeNewsSentimentVM:
    """模拟 AIBrainSettingsViewModel 的 news_sentiment 状态切片。

    满足 use_viewmodel(vm=) 外部 VM 模式契约 (subscribe/state/dispose),
    setter 命令为 MagicMock 便于断言; 其余 VM 能力不进入本组件不模拟。
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        model_path: str = "",
        head_path: str = "",
        tokenizer_path: str = "",
    ) -> None:
        self.news_sentiment_enabled = enabled
        self.news_sentiment_model_path = model_path
        self.news_sentiment_head_path = head_path
        self.news_sentiment_tokenizer_path = tokenizer_path
        self._subscribers: list[Any] = []
        self.set_news_sentiment_enabled = MagicMock()
        self.set_news_sentiment_model_path = MagicMock()
        self.set_news_sentiment_head_path = MagicMock()
        self.set_news_sentiment_tokenizer_path = MagicMock()

    @property
    def state(self) -> Any:
        return self

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _render_panel(
    vm: _FakeNewsSentimentVM | None = None,
    *,
    on_submit_save: Any = None,
    page: FakePage | None = None,
) -> tuple[_FakeNewsSentimentVM, FakePage, Any, Any]:
    """渲染 NewsSentimentConfigPanel, 返回 (vm, page, result, component)。

    Mock panel_module 内 I18n / AppColors / AppStyles (模块级导入);
    SettingRow 保留真实声明式渲染 (conftest mock_i18n_state /
    mock_app_colors_state 提供 observable state)。
    """
    if vm is None:
        vm = _FakeNewsSentimentVM()
    if page is None:
        page = FakePage()
    page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        stack.enter_context(patch.object(panel_module, "AppStyles"))

        component = make_component(
            NewsSentimentConfigPanel,
            vm=vm,
            on_submit_save=on_submit_save,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 模块级纯函数: _pick_and_set
# ============================================================================


class TestPickAndSet:
    """_pick_and_set: 打开文件选择器并经 setter 写入 VM。"""

    def test_normal_selection_calls_setter_with_path(self) -> None:
        """正常选文件 (result[0].path 非空) → vm.set_<setter>(path)。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        fake_file = MagicMock(path="/path/to/model.gguf")
        file_picker.pick_files = MagicMock(return_value=_async_return([fake_file]))

        asyncio.run(_pick_and_set(vm, file_picker, "set_news_sentiment_model_path", "gguf"))

        vm.set_news_sentiment_model_path.assert_called_once_with("/path/to/model.gguf")

    def test_user_cancel_skips_setter(self) -> None:
        """用户取消 (result=None) → 不调 setter。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        file_picker.pick_files = MagicMock(return_value=_async_return(None))

        asyncio.run(_pick_and_set(vm, file_picker, "set_news_sentiment_model_path", "gguf"))

        vm.set_news_sentiment_model_path.assert_not_called()

    def test_empty_result_skips_setter(self) -> None:
        """result 为空列表 → 不调 setter。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        file_picker.pick_files = MagicMock(return_value=_async_return([]))

        asyncio.run(_pick_and_set(vm, file_picker, "set_news_sentiment_head_path", "npz"))

        vm.set_news_sentiment_head_path.assert_not_called()

    def test_path_none_falls_back_to_empty_string(self) -> None:
        """result[0].path=None → setter("") (path or "" 兜底)。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        file_picker.pick_files = MagicMock(return_value=_async_return([MagicMock(path=None)]))

        asyncio.run(_pick_and_set(vm, file_picker, "set_news_sentiment_tokenizer_path", "json"))

        vm.set_news_sentiment_tokenizer_path.assert_called_once_with("")

    def test_pick_files_raises_logs_error_no_raise(self) -> None:
        """pick_files 抛异常 → logger.error 被调用, 不抛出 (R2: CancelledError 不被捕获)。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)

        async def _raise() -> None:
            raise RuntimeError("picker unavailable")

        file_picker.pick_files = MagicMock(side_effect=_raise)

        with patch.object(panel_module, "logger") as mock_logger:
            # 不应抛异常
            asyncio.run(_pick_and_set(vm, file_picker, "set_news_sentiment_model_path", "gguf"))

        mock_logger.error.assert_called_once_with(
            "[NewsSentimentConfigPanel] File pick failed: %s",
            ANY,
            exc_info=True,
        )
        vm.set_news_sentiment_model_path.assert_not_called()


# ============================================================================
# 工厂函数: _on_pick_click_factory
# ============================================================================


class TestOnPickClickFactory:
    """_on_pick_click_factory: page 守卫 + page.run_task 调度 (R16)。"""

    def test_page_available_runs_pick_and_set(self) -> None:
        """page 可用 → page.run_task(_pick_and_set, vm, picker, setter, extension)。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_pick_click_factory(vm, file_picker, "set_news_sentiment_model_path", "gguf")
        mock_page = MagicMock()
        with patch("ui.components.config_panels.news_sentiment_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(
            _pick_and_set, vm, file_picker, "set_news_sentiment_model_path", "gguf"
        )

    def test_page_none_skips_run_task(self) -> None:
        """page=None → 不调 run_task, 不抛异常。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_pick_click_factory(vm, file_picker, "set_news_sentiment_model_path", "gguf")
        mock_page = MagicMock()
        with patch("ui.components.config_panels.news_sentiment_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_file_picker_none_skips_run_task(self) -> None:
        """file_picker=None → 早返回 (不触碰 ft.context)。"""
        vm = MagicMock()
        handler = _on_pick_click_factory(vm, None, "set_news_sentiment_model_path", "gguf")
        # 不应抛异常
        _invoke(handler, _make_event())

    def test_runtime_error_swallowed(self) -> None:
        """ft.context.page 抛 RuntimeError → logger.debug 静默, 不抛异常。"""
        vm = MagicMock()
        file_picker = MagicMock(spec=ft.FilePicker)
        handler = _on_pick_click_factory(vm, file_picker, "set_news_sentiment_model_path", "gguf")
        with patch("ui.components.config_panels.news_sentiment_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            # 不应抛异常
            _invoke(handler, _make_event())


# ============================================================================
# 组件运行时: FilePicker 生命周期 (setup / cleanup)
# ============================================================================


class TestFilePickerLifecycle:
    """_setup_file_picker / _cleanup_file_picker 生命周期测试。"""

    def test_setup_appends_picker_to_page_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """page.services 不含 picker → 挂载后 picker 被 append。"""
        _, page, _, _ = _render_panel()
        assert any(isinstance(s, ft.FilePicker) for s in page.services)

    def test_setup_skips_when_picker_already_in_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """重复渲染同一组件 (use_ref 复用 picker) → 不重复 append。"""
        page = FakePage()
        page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock
        _, _, _, component = _render_panel(page=page)
        pickers_after_first = [s for s in page.services if isinstance(s, ft.FilePicker)]
        assert len(pickers_after_first) == 1

        # 重新渲染: use_ref 返回同一 picker, in 检查跳过 append
        render_once(component)
        pickers_after_rerender = [s for s in page.services if isinstance(s, ft.FilePicker)]
        assert len(pickers_after_rerender) == 1

    def test_setup_handles_page_runtime_error(self, mock_i18n_state, mock_app_colors_state) -> None:
        """ft.context.page 抛 RuntimeError → 挂载流程不中断。"""
        vm = _FakeNewsSentimentVM()
        page = FakePage()
        page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

        class _ContextWithRaisingPage:
            @property
            def page(self) -> Any:
                raise RuntimeError("no page context")

        with contextlib.ExitStack() as stack:
            mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
            mock_i18n.get.side_effect = lambda key, **kw: key
            stack.enter_context(patch.object(panel_module, "AppColors"))
            stack.enter_context(patch.object(panel_module, "AppStyles"))
            stack.enter_context(
                patch(
                    "ui.components.config_panels.news_sentiment_config_panel.ft.context",
                    new=_ContextWithRaisingPage(),
                )
            )
            component = make_component(NewsSentimentConfigPanel, vm=vm)
            # 不应抛异常
            run_mount_effects(component, page=page)
            result = render_once(component)
            assert isinstance(result, ft.Control)

    def test_cleanup_removes_picker_from_page_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载时 page.services 中的 picker 被 remove。"""
        _, page, _, component = _render_panel()
        assert len([s for s in page.services if isinstance(s, ft.FilePicker)]) == 1

        run_unmount_effects(component)

        assert len([s for s in page.services if isinstance(s, ft.FilePicker)]) == 0

    def test_cleanup_skips_remove_when_picker_not_in_services(self, mock_i18n_state, mock_app_colors_state) -> None:
        """page.services 不含 picker → remove 跳过 (不抛 ValueError)。"""
        _, page, _, component = _render_panel()
        page.services.clear()
        # 不应抛异常
        run_unmount_effects(component)

    def test_cleanup_handles_page_runtime_error(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载时 ft.context.page 抛 RuntimeError → except 捕获, 不抛异常。"""
        _, _, _, component = _render_panel()

        class _ContextWithRaisingPage:
            @property
            def page(self) -> Any:
                raise RuntimeError("no page context")

        with patch(
            "ui.components.config_panels.news_sentiment_config_panel.ft.context",
            new=_ContextWithRaisingPage(),
        ):
            # 不应抛异常
            run_unmount_effects(component)


# ============================================================================
# 组件运行时: 渲染与状态绑定
# ============================================================================


class TestPanelRender:
    """NewsSentimentConfigPanel 渲染与状态绑定测试。"""

    def test_returns_container(self, mock_i18n_state, mock_app_colors_state) -> None:
        """组件返回 ft.Container。"""
        _, _, result, _ = _render_panel()
        assert isinstance(result, ft.Container)

    def test_switch_reflects_state_enabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        """state.news_sentiment_enabled=True → Switch.value=True。"""
        vm = _FakeNewsSentimentVM(enabled=True)
        _, _, result, _ = _render_panel(vm)
        assert _find_switch(result).value is True

    def test_switch_reflects_state_disabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        """state.news_sentiment_enabled=False → Switch.value=False。"""
        _, _, result, _ = _render_panel()
        assert _find_switch(result).value is False

    def test_switch_toggle_calls_vm_setter(self, mock_i18n_state, mock_app_colors_state) -> None:
        """开关 on_change → vm.set_news_sentiment_enabled(value)。"""
        vm, _, result, _ = _render_panel()
        sw = _find_switch(result)

        _invoke(sw.on_change, _make_event(True))

        vm.set_news_sentiment_enabled.assert_called_once_with(True)

    def test_three_path_rows_rendered_with_state_values(self, mock_i18n_state, mock_app_colors_state) -> None:
        """三路径行: TextField label = i18n key, value = 对应 state 字段。"""
        vm = _FakeNewsSentimentVM(
            model_path="/m.gguf",
            head_path="/h.npz",
            tokenizer_path="/t.json",
        )
        _, _, result, _ = _render_panel(vm)
        for state_attr, _setter, _ext, label_key in _FILE_TARGETS:
            field = _find_text_field(result, label_key)
            assert field.value == getattr(vm, state_attr), f"{label_key} 值应绑定 state.{state_attr}"

    def test_three_select_buttons_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        """三个「选择文件」按钮 (gguf/npz/json 各一)。"""
        _, _, result, _ = _render_panel()
        btns = _find_select_buttons(result)
        assert len(btns) == 3


# ============================================================================
# 组件运行时: 事件接线 (on_change / on_click / on_submit)
# ============================================================================


class TestPanelEventWiring:
    """NewsSentimentConfigPanel 事件接线测试。"""

    def test_path_input_on_change_calls_corresponding_setter(self, mock_i18n_state, mock_app_colors_state) -> None:
        """路径输入 on_change → 对应 setter (三行逐一验证)。"""
        vm, _, result, _ = _render_panel()
        for _attr, setter, _ext, label_key in _FILE_TARGETS:
            field = _find_text_field(result, label_key)
            setter_mock = getattr(vm, setter)
            setter_mock.reset_mock()

            _invoke(field.on_change, _make_event(f"/new/{label_key}"))

            setter_mock.assert_called_once_with(f"/new/{label_key}")

    def test_select_button_click_runs_pick_and_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """选择按钮 on_click → page.run_task(_pick_and_set, vm, picker, setter, ext) (三按钮逐一)。"""
        vm, page, result, _ = _render_panel(vm=_FakeNewsSentimentVM())
        btns = _find_select_buttons(result)
        run_task = page.run_task  # type: ignore[reportAttributeAccessIssue]  # reason: _render_panel 动态注入 MagicMock
        for btn, (_attr, setter, ext, _label) in zip(btns, _FILE_TARGETS, strict=True):
            run_task.reset_mock()

            _invoke(btn.on_click, _make_event())

            assert run_task.call_count == 1, f"run_task 应调用一次, 实际 {run_task.call_count}"
            call_args = run_task.call_args
            assert call_args.args[0] == _pick_and_set
            assert call_args.args[1] is vm
            assert isinstance(call_args.args[2], ft.FilePicker)
            assert call_args.args[3] == setter
            assert call_args.args[4] == ext

    def test_on_submit_bound_when_callback_provided(self, mock_i18n_state, mock_app_colors_state) -> None:
        """on_submit_save 传入 → 三路径 TextField on_submit 非空 (UX-09)。"""
        callback = MagicMock()
        _, _, result, _ = _render_panel(on_submit_save=callback)
        for _attr, _setter, _ext, label_key in _FILE_TARGETS:
            field = _find_text_field(result, label_key)
            assert field.on_submit is callback, f"{label_key} on_submit 应绑定传入回调"

    def test_on_submit_none_when_no_callback(self, mock_i18n_state, mock_app_colors_state) -> None:
        """on_submit_save=None → 三路径 TextField on_submit 为 None。"""
        _, _, result, _ = _render_panel()
        for _attr, _setter, _ext, label_key in _FILE_TARGETS:
            field = _find_text_field(result, label_key)
            assert field.on_submit is None, f"{label_key} on_submit 应为 None"


# ============================================================================
# VM 订阅生命周期 (use_viewmodel 外部 VM 模式)
# ============================================================================


class TestVMLifecycle:
    """外部 VM 模式订阅生命周期测试。"""

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
        dispose_called: list[bool] = []
        original_dispose = vm.dispose

        def _spy_dispose() -> None:
            dispose_called.append(True)
            original_dispose()

        vm.dispose = _spy_dispose  # type: ignore[method-assign]  # reason: 测试 spy 注入, VM 为测试私有 Fake 类
        run_unmount_effects(component)
        assert dispose_called == []
