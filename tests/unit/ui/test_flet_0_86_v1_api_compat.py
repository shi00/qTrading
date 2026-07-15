"""Flet 0.86.0 V1 声明式 API 兼容性验证（spike）。

目的：在 spike worktree（flet 0.86.0）中固化项目深度依赖的 V1 声明式 API
契约。任一断言失败即表示 0.86.0 引入了破坏性变更，需记录到 spike 报告并
标记升级阻塞（Plans-flet-0.86.0-upgrade.md Task 0.3 DoD）。

覆盖 9 类 API（spike 任务清单）：
1. ``@ft.component`` 装饰器：声明式组件入口
2. ``ft.use_state(initial)``：有状态 hook，返回 ``(value, setter)`` 二元组
3. ``ft.use_effect(setup, dependencies, cleanup)``：副作用 hook（三参数签名）
4. ``ft.use_ref(initial_value)``：持久化引用 hook（factory 仅首次调用）
5. ``ui.hooks.use_viewmodel``：项目自定义 VM 桥接 hook（非 flet 原生，
   定义于 ``ui/hooks.py``）
6. ``ft.context.page``：渲染上下文 page 访问（上下文外抛 RuntimeError）
7. ``ft.use_dialog``：flet 原生 dialog hook
8. ``page.run_task``：协程调度入口（MockFletPage 上可调用）
9. ``page.window.prevent_close/destroy/center/on_event/min_width``：窗口控制字段

依赖点（项目内使用位置）：
- ``@ft.component`` / ``use_state`` / ``use_effect`` / ``use_ref``：
  所有 ``ui/views`` 声明式组件 + ``ui/hooks.py``
- ``use_viewmodel``：``ui/hooks.py`` 定义，所有 ViewModel 桥接使用
- ``ft.context.page``：``ui/hooks.py`` 内 ``use_dialog`` 调用路径 +
  ``component_renderer.attach_fake_page`` 注入
- ``ft.use_dialog``：声明式 dialog 管理路径
- ``page.run_task``：所有 UI 异步命令调度
- ``page.window.*``：``main.py`` 窗口生命周期管理（prevent_close/on_event/
  destroy/center/min_width）

验证手段：``inspect.signature()`` + ``component_renderer`` 渲染辅助 +
行为断言（不依赖真实 Flet Renderer）。
"""

from __future__ import annotations

import inspect
from typing import Any

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    attach_fake_page,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from tests.unit.ui.mock_flet import MockFletPage

pytestmark = pytest.mark.unit


# ============================================================================
# 1. @ft.component 装饰器
# ============================================================================


def test_component_decorator_is_callable() -> None:
    """``ft.component`` 可作为装饰器使用。"""
    assert callable(ft.component)


def test_component_decorator_exposes_impl_attr() -> None:
    """``@ft.component`` 装饰后函数暴露 ``__component_impl__`` 属性。

    ``component_renderer.render_once`` 通过
    ``getattr(component.fn, "__component_impl__", component.fn)`` 取实际渲染函数，
    该属性缺失将破坏全部声明式 UI 测试基础设施。
    """

    @ft.component
    def MyComp(label: str = "x") -> ft.Text:
        return ft.Text(value=label)

    assert hasattr(MyComp, "__component_impl__")
    assert callable(MyComp.__component_impl__)


def test_component_decorator_renders_control_via_helper() -> None:
    """``@ft.component`` 装饰的组件经 ``render_once`` 返回 ``ft.Control``。"""

    @ft.component
    def HelloView(label: str = "hi") -> ft.Text:
        return ft.Text(value=label)

    component = make_component(HelloView, label="world")
    result = render_once(component)
    assert isinstance(result, ft.Text)
    assert result.value == "world"


# ============================================================================
# 2. ft.use_state(initial)
# ============================================================================


def test_use_state_signature_single_initial_param() -> None:
    """``ft.use_state`` 签名为 ``(initial) -> (value, setter)``。"""
    sig = inspect.signature(ft.use_state)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "initial"
    # initial 无默认值（必传）
    assert params[0].default is inspect.Parameter.empty


def test_use_state_returns_value_setter_pair_in_component() -> None:
    """``use_state`` 在组件内调用返回 ``(value, setter)`` 二元组，setter 可调用。

    setter 签名为 ``(new_value_or_fn)``，接受新值或 updater 函数
    （``StateT | Updater``）。
    """
    captured: dict[str, Any] = {}

    @ft.component
    def Counter(initial: int = 0) -> ft.Text:
        value, setter = ft.use_state(initial)
        captured["value"] = value
        captured["setter"] = setter
        return ft.Text(value=str(value))

    component = make_component(Counter, initial=42)
    render_once(component)
    assert captured["value"] == 42
    assert callable(captured["setter"])
    # setter 签名应接受一个参数（新值或 updater 函数）
    setter_sig = inspect.signature(captured["setter"])
    setter_params = list(setter_sig.parameters.values())
    assert len(setter_params) == 1


# ============================================================================
# 3. ft.use_effect(setup, dependencies, cleanup)
# ============================================================================


def test_use_effect_signature_three_params() -> None:
    """``ft.use_effect`` 三参数签名 ``(setup, dependencies, cleanup)``。

    ``ui/hooks.py`` 的 ``use_viewmodel`` 按位置传 ``(setup, [], cleanup)``，
    参数顺序/可选性变化将破坏 VM 桥接 hook。
    """
    sig = inspect.signature(ft.use_effect)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["setup", "dependencies", "cleanup"]
    # setup 必传；dependencies 与 cleanup 可选（默认 None）
    assert params[0].default is inspect.Parameter.empty
    assert params[1].default is None
    assert params[2].default is None


def test_use_effect_setup_cleanup_executed_via_lifecycle_helpers() -> None:
    """``use_effect`` 的 setup/cleanup 在 mount/unmount 时被调用。

    ``ui/hooks.py`` 的 ``use_viewmodel`` 用 ``use_effect(setup, [], cleanup)``
    注册订阅/退订；mount/unmount 触发 setup/cleanup 是该 hook 的行为前提。
    """
    log: list[str] = []

    @ft.component
    def WithEffect() -> ft.Text:
        def _setup() -> None:
            log.append("setup")

        def _cleanup() -> None:
            log.append("cleanup")

        ft.use_effect(_setup, [], _cleanup)
        return ft.Text(value="x")

    component = make_component(WithEffect)
    run_mount_effects(component)
    assert log == ["setup"]
    run_unmount_effects(component)
    assert log == ["setup", "cleanup"]


# ============================================================================
# 4. ft.use_ref(initial_value) —— factory 仅首次调用
# ============================================================================


def test_use_ref_signature_single_initial_value_param() -> None:
    """``ft.use_ref`` 签名为 ``(initial_value=None) -> MutableRef``。

    注意：flet 0.86.0 中参数名是 ``initial_value``（非任务描述中的 ``factory``），
    但接受 ``factory`` 形式的 callable（首次调用取初始值）。
    ``ui/hooks.py`` 的 ``use_viewmodel`` 用 ``ft.use_ref(factory)`` 持久化 VM。
    """
    sig = inspect.signature(ft.use_ref)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "initial_value"
    assert params[0].default is None


def test_use_ref_factory_invoked_once_across_renders() -> None:
    """``use_ref(factory)`` 多次渲染仅首次调用 factory（同一实例复用）。

    ``ui/hooks.py`` 的 ``use_viewmodel`` 依赖此语义避免每次渲染重新实例化 VM
    （spike 项 5 结论：``use_state(factory())`` 陷阱正是 factory 每次调用）。
    """
    factory_call_count: list[int] = []

    @ft.component
    def WithRef() -> ft.Text:
        def _factory() -> dict[str, int]:
            factory_call_count.append(1)
            return {"id": id(_factory)}

        ref = ft.use_ref(_factory)
        # 通过 ref.current 的 id 标识是否同一实例
        return ft.Text(value=str(id(ref.current)))

    component = make_component(WithRef)
    first = render_once(component)
    second = render_once(component)
    third = render_once(component)

    # factory 仅首次调用一次
    assert len(factory_call_count) == 1
    # 三次渲染的 Text.value（基于 ref.current 的 id）应一致
    assert first.value == second.value == third.value


# ============================================================================
# 5. ui.hooks.use_viewmodel —— 项目自定义 hook（非 flet 原生）
# ============================================================================


def test_use_viewmodel_is_project_custom_not_flet_native() -> None:
    """``use_viewmodel`` 是项目自定义 hook（``ui/hooks.py``），不在 flet 原生命名空间。

    若 flet 后续原生提供 ``ft.use_viewmodel``，需评估是否迁移项目 hook；
    当前断言其不在 flet 原生命中以守护项目自定义桥接策略。
    """
    assert not hasattr(ft, "use_viewmodel"), "ft.use_viewmodel 已被 flet 原生支持——应更新本测试与 ui/hooks.py 桥接策略"
    from ui.hooks import use_viewmodel

    assert callable(use_viewmodel)


def test_use_viewmodel_signature_factory_vm_dispose() -> None:
    """``ui.hooks.use_viewmodel`` 签名 ``(factory=None, *, vm=None, dispose_on_unmount=True)``。

    ``factory`` 与 ``vm`` 互斥（必须传入其一）；``vm`` 与 ``dispose_on_unmount``
    为 keyword-only 参数（``ui/hooks.py`` 实现契约）。
    """
    from ui.hooks import use_viewmodel

    sig = inspect.signature(use_viewmodel)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["factory", "vm", "dispose_on_unmount"]
    # factory 为 POSITIONAL_OR_KEYWORD；vm 与 dispose_on_unmount 为 KEYWORD_ONLY
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert params[2].kind == inspect.Parameter.KEYWORD_ONLY
    # 全部有默认值
    assert params[0].default is None
    assert params[1].default is None
    assert params[2].default is True


def test_use_viewmodel_renders_state_and_vm_with_lifecycle() -> None:
    """``use_viewmodel(factory=...)`` 在组件内返回 ``(state, vm)`` 二元组。

    验证 VM 桥接 hook 完整生命周期：
    - 首次渲染：``state`` 取自 ``vm.state``，``vm`` 为 factory 实例化结果
    - mount：``vm.subscribe`` 被调用（通过 ``use_effect`` setup）
    - unmount：``vm.dispose`` 被调用（``dispose_on_unmount=True`` 时 cleanup 调用）
    """
    from ui.hooks import use_viewmodel

    class FakeVM:
        def __init__(self) -> None:
            self._state = {"n": 1}
            self._disposed = False
            self._subscribed = False
            self._unsubscribed = False

        @property
        def state(self) -> dict[str, int]:
            return self._state

        def subscribe(self, callback: Any) -> Any:
            self._subscribed = True
            return lambda: setattr(self, "_unsubscribed", True)

        def dispose(self) -> None:
            self._disposed = True

    captured: dict[str, Any] = {}

    @ft.component
    def WithVM() -> ft.Text:
        state, vm = use_viewmodel(factory=FakeVM)
        captured["state"] = state
        captured["vm"] = vm
        return ft.Text(value=str(state["n"]))

    component = make_component(WithVM)
    run_mount_effects(component)
    # 首次渲染：state 来自 vm.state，vm 为 FakeVM 实例
    assert captured["state"] == {"n": 1}
    assert isinstance(captured["vm"], FakeVM)
    # mount 触发 subscribe（use_effect setup 调用 vm.subscribe）
    assert captured["vm"]._subscribed is True
    # 尚未 unmount，不应 dispose
    assert captured["vm"]._disposed is False

    run_unmount_effects(component)
    # unmount 触发 cleanup：先 unsub 再 dispose（dispose_on_unmount=True）
    assert captured["vm"]._disposed is True


# ============================================================================
# 6. ft.context.page
# ============================================================================


def test_context_page_is_property() -> None:
    """``ft.context.page`` 是 property（仅在渲染上下文内可访问）。"""
    ctx_cls = type(ft.context)
    page_attr = getattr(ctx_cls, "page", None)
    assert isinstance(page_attr, property)


def test_context_page_raises_runtime_error_outside_context() -> None:
    """``ft.context.page`` 在渲染上下文外访问抛 ``RuntimeError``。

    ``ui/hooks.py`` 的 ``use_dialog`` 内部调用 ``context.page``，
    上下文外访问应抛 RuntimeError 而非返回 None（flet 0.86.0 行为契约）。
    """
    with pytest.raises(RuntimeError, match="context is not associated"):
        _ = ft.context.page


def test_context_page_returns_page_inside_render_context() -> None:
    """``ft.context.page`` 在渲染上下文内返回注入的 page（FakePage）。

    ``component_renderer.attach_fake_page`` 通过 ``_context_page.set(page)``
    注入 FakePage；``render_once`` 内 ``ft.context.page`` 应返回该实例。
    """
    from flet.controls.context import _context_page

    @ft.component
    def WithContextAccess() -> ft.Text:
        page = ft.context.page
        # 把 page 类型名写入 Text.value 以断言 page 类型
        return ft.Text(value=type(page).__name__)

    component = make_component(WithContextAccess)
    page = attach_fake_page(component)
    result = render_once(component)
    # 渲染上下文内 ft.context.page 应返回注入的 FakePage
    assert result.value == "FakePage"
    # _context_page ContextVar 应被设置为注入的 page
    assert _context_page.get() is page


# ============================================================================
# 7. ft.use_dialog —— flet 原生 hook
# ============================================================================


def test_use_dialog_is_flet_native() -> None:
    """``ft.use_dialog`` 是 flet 原生 hook（非项目自定义）。"""
    assert hasattr(ft, "use_dialog")
    assert callable(ft.use_dialog)


def test_use_dialog_signature_single_dialog_param() -> None:
    """``ft.use_dialog`` 签名为 ``(dialog=None)``。

    ``dialog`` 参数接受 ``DialogControl | None``：传入控件即显示，
    传入 ``None`` 即隐藏/移除（flet 0.86.0 源码契约）。
    """
    sig = inspect.signature(ft.use_dialog)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "dialog"
    assert params[0].default is None


def test_use_dialog_appends_dialog_to_page_dialogs_in_component() -> None:
    """``ft.use_dialog(dialog)`` 在组件内调用将 dialog 追加到 ``page._dialogs.controls``。

    ``component_renderer.FakePage`` 已提供 ``_dialogs`` 容器与 ``_prepare_dialog``
    吸收方法，支持声明式 dialog hook 测试。本测试验证 dialog 控件实际被追加。
    """

    @ft.component
    def WithDialog() -> ft.Text:
        ft.use_dialog(ft.AlertDialog(content=ft.Text(value="hi")))
        return ft.Text(value="x")

    component = make_component(WithDialog)
    page = attach_fake_page(component)
    render_once(component)
    # use_dialog 同步将 dialog 追加到 page._dialogs.controls
    assert len(page._dialogs.controls) == 1
    appended = page._dialogs.controls[0]
    assert isinstance(appended, ft.AlertDialog)
    # use_dialog 内部将 dialog.open 置为 True
    assert appended.open is True


# ============================================================================
# 8. page.run_task —— MockFletPage 上可调用
# ============================================================================


def test_page_run_task_exists_on_real_page_class() -> None:
    """``ft.Page.run_task`` 在真实 ``ft.Page`` 类上存在。"""
    assert hasattr(ft.Page, "run_task")


def test_page_run_task_callable_on_mock_page() -> None:
    """``MockFletPage.run_task`` 可调用（项目测试基础设施契约）。

    ``wrap_mock_page`` 与 ``MockFletPage.__init__`` 均依赖此方法存在。
    """
    page = MockFletPage()
    assert callable(page.run_task)


def test_page_run_task_returns_cancelable_on_mock() -> None:
    """``MockFletPage.run_task`` 调用后返回带 ``cancel`` 方法的对象。

    MockFletPage.run_task 桩语义：同步调用 func，若是协程则关闭它，
    返回 MagicMock 含 cancel 方法（用于测试断言 task 取消场景）。
    """
    page = MockFletPage()

    async def _coro() -> int:
        return 42

    result = page.run_task(_coro)
    assert hasattr(result, "cancel")
    assert callable(result.cancel)


# ============================================================================
# 9. page.window.prevent_close / destroy / center / on_event / min_width
# ============================================================================


def test_window_class_has_required_five_fields() -> None:
    """``ft.Window`` 类上有 5 个窗口控制字段（prevent_close/destroy/center/on_event/min_width）。

    ``main.py`` 通过 ``page.window`` 访问这些字段管理窗口生命周期
    （prevent_close 拦截关闭、on_event 监听窗口事件、min_width 限制最小宽度、
    destroy/center 主动控制窗口）。
    """
    assert inspect.isclass(ft.Window)
    for field in (
        "prevent_close",
        "destroy",
        "center",
        "on_event",
        "min_width",
    ):
        assert hasattr(ft.Window, field), f"ft.Window.{field} 缺失"


def test_window_prevent_close_is_bool_dataclass_field() -> None:
    """``ft.Window.prevent_close`` 是 bool dataclass 字段（默认 False）。"""
    fields = ft.Window.__dataclass_fields__
    assert "prevent_close" in fields
    assert fields["prevent_close"].default is False


def test_window_destroy_and_center_are_methods() -> None:
    """``ft.Window.destroy`` 与 ``ft.Window.center`` 是方法（仅 self 参数）。"""
    assert callable(ft.Window.destroy)
    assert callable(ft.Window.center)
    sig_destroy = inspect.signature(ft.Window.destroy)
    sig_center = inspect.signature(ft.Window.center)
    assert list(sig_destroy.parameters) == ["self"]
    assert list(sig_center.parameters) == ["self"]


def test_window_on_event_and_min_width_are_dataclass_fields() -> None:
    """``ft.Window.on_event`` 与 ``ft.Window.min_width`` 是 dataclass 字段（默认 None）。

    ``on_event`` 接受 ``Callable[[], Any] | Callable[[WindowEvent], Any] | None``；
    ``min_width`` 接受 ``int | float | None``。
    """
    fields = ft.Window.__dataclass_fields__
    assert "on_event" in fields
    assert "min_width" in fields
    assert fields["on_event"].default is None
    assert fields["min_width"].default is None


def test_mock_page_window_supports_required_five_fields() -> None:
    """``MockFletPage.window``（``spec=ft.Window``）5 个字段均可访问。

    ``mock_flet.py`` 用 ``MagicMock(spec=ft.Window)`` 约束 window 字段，
    字段漂移会导致 ``AttributeError``，确保 mock 与真实 Window 接口同步。
    """
    page = MockFletPage()
    for field in (
        "prevent_close",
        "destroy",
        "center",
        "on_event",
        "min_width",
    ):
        assert hasattr(page.window, field), f"MockFletPage.window.{field} 不可访问——mock_flet.py spec 与 ft.Window 漂移"
