"""ObservableViewModelMixin 单元测试 (P2-4, Phase 2 Task 2.1)。

验证:
- mixin 默认行为: state property / subscribe / _notify / _set_state / dispose
- 子类覆盖 subscribe 保留自定义逻辑 (捕获 _main_loop)
- 子类覆盖 dispose 保留自定义逻辑 (清理额外资源)
- 子类覆盖 _set_state 保留自定义逻辑 (disposed guard)
- 子类覆盖 _notify 保留自定义逻辑 (try/except 包裹)
- mixin 不继承 ft.Observable (F1)
- subscribe 签名 (callback) -> unsub 单参数 (F12)
- hooks.py:112 (new_state) 协议不变
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft
import pytest

from ui.viewmodels.observable_mixin import ObservableViewModelMixin

pytestmark = pytest.mark.unit


# ============================================================
# 测试用 frozen state dataclass
# ============================================================


@dataclass(frozen=True)
class _DummyState:
    """测试用 frozen state (模拟真实 VM 的 state dataclass)。"""

    name: str = ""
    count: int = 0
    tags: tuple[str, ...] = ()


# ============================================================
# 默认实现 VM (使用 mixin 默认行为, 不覆盖任何方法)
# ============================================================


class _DefaultVM(ObservableViewModelMixin[_DummyState]):
    """使用 mixin 默认实现的 VM (验证默认行为)。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []


# ============================================================
# 自定义 subscribe VM (捕获 _main_loop, 模拟 ScreenerViewModel)
# ============================================================


class _CustomSubscribeVM(ObservableViewModelMixin[_DummyState]):
    """覆盖 subscribe 捕获 _main_loop (模拟 ScreenerViewModel.subscribe)。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []
        self._main_loop: Any = None
        self._custom_subscribe_called = False

    def subscribe(self, callback: Callable[[_DummyState], None]) -> Callable[[], None]:
        """覆盖 subscribe: 捕获 main loop + 调默认实现。"""
        self._custom_subscribe_called = True
        self._subscribers.append(callback)
        try:
            import asyncio

            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe


# ============================================================
# 自定义 dispose VM (清理额外资源, 模拟 ScreenerViewModel.dispose)
# ============================================================


class _CustomDisposeVM(ObservableViewModelMixin[_DummyState]):
    """覆盖 dispose 清理额外资源 (模拟 ScreenerViewModel.dispose)。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []
        self._background_tasks: set = set()
        self._disposed = False
        self._custom_cleanup_called = False

    def dispose(self) -> None:
        """覆盖 dispose: 先标记 disposed + 清理 background tasks, 再调 super().dispose()。"""
        self._disposed = True
        self._custom_cleanup_called = True
        for _t in list(self._background_tasks):
            pass  # 模拟 cancel
        self._background_tasks.clear()
        super().dispose()


# ============================================================
# 自定义 _set_state VM (disposed guard, 模拟 ScreenerViewModel._set_state)
# ============================================================


class _CustomSetStateVM(ObservableViewModelMixin[_DummyState]):
    """覆盖 _set_state 加 disposed guard (模拟 ScreenerViewModel._set_state)。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []
        self._disposed = False

    def _set_state(self, **changes: Any) -> None:
        """覆盖 _set_state: disposed 时早返。"""
        if self._disposed:
            return
        super()._set_state(**changes)


# ============================================================
# 自定义 _notify VM (try/except 包裹, 模拟 HomeViewModel._notify)
# ============================================================


class _CustomNotifyVM(ObservableViewModelMixin[_DummyState]):
    """覆盖 _notify 用 try/except 包裹 subscriber 调用 (模拟 HomeViewModel._notify)。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []
        self._subscriber_errors: list[str] = []

    def _notify(self) -> None:
        """覆盖 _notify: 用 try/except 包裹, 不让单个 subscriber 异常中断通知。"""
        snapshot = self._state
        for cb in list(self._subscribers):
            try:
                cb(snapshot)
            except Exception as e:
                self._subscriber_errors.append(str(e))


# ============================================================
# Test: Mixin 不继承 ft.Observable (F1)
# ============================================================


class TestMixinNotObservable:
    """F1: mixin 不继承 ft.Observable, 不破坏 hooks.py:112 (new_state) 单参数协议。"""

    def test_mixin_does_not_inherit_ft_observable(self):
        """mixin 的 MRO 中不应包含 ft.Observable。"""
        for klass in ObservableViewModelMixin.__mro__:
            assert klass is not ft.Observable, "mixin 不应继承 ft.Observable"

    def test_mixin_is_generic(self):
        """mixin 应继承 Generic[T] 以保留类型安全。"""
        from typing import Generic

        assert Generic in ObservableViewModelMixin.__mro__ or any(
            issubclass(klass, Generic) for klass in ObservableViewModelMixin.__mro__ if klass is not object
        )


# ============================================================
# Test: 默认行为 (state / subscribe / _notify / _set_state / dispose)
# ============================================================


class TestDefaultBehavior:
    """验证 mixin 默认实现的行为。"""

    def test_state_property_returns_initial_state(self):
        vm = _DefaultVM()
        assert vm.state is vm._state
        assert isinstance(vm.state, _DummyState)

    def test_state_property_returns_updated_state_after_set_state(self):
        vm = _DefaultVM()
        vm._set_state(name="hello")
        assert vm.state.name == "hello"

    def test_subscribe_returns_unsubscribe_callable(self):
        vm = _DefaultVM()
        unsub = vm.subscribe(lambda s: None)
        assert callable(unsub)

    def test_subscribe_callback_invoked_on_notify(self):
        vm = _DefaultVM()
        snapshots: list[_DummyState] = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(name="first")
        assert len(snapshots) == 1
        assert snapshots[0].name == "first"

    def test_unsubscribe_stops_notifications(self):
        vm = _DefaultVM()
        snapshots: list[_DummyState] = []
        unsub = vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(name="first")
        unsub()
        vm._set_state(name="second")
        assert len(snapshots) == 1
        assert snapshots[0].name == "first"

    def test_unsubscribe_idempotent(self):
        """重复调用 unsub 不应抛异常 (callback 已移除时 no-op)。"""
        vm = _DefaultVM()
        unsub = vm.subscribe(lambda s: None)
        unsub()
        unsub()  # 不应抛异常
        assert len(vm._subscribers) == 0

    def test_notify_uses_subscriber_snapshot(self):
        """_notify 用 list 快照, 迭代中订阅者修改列表不影响当前通知循环。"""
        vm = _DefaultVM()

        def _adding_callback(s: _DummyState) -> None:
            # 模拟订阅者在回调中再订阅 (不应影响当前 _notify 循环)
            if len(vm._subscribers) < 3:
                vm.subscribe(lambda s2: None)

        vm.subscribe(_adding_callback)
        vm._set_state(name="trigger")
        # 原始 1 个 + 回调中新增的 1 个 = 2 个
        assert len(vm._subscribers) == 2

    def test_set_state_uses_dataclasses_replace(self):
        """_set_state 用 dataclasses.replace 创建新 frozen 实例, 不修改原实例。"""
        vm = _DefaultVM()
        original_state = vm._state
        vm._set_state(name="changed", count=42, tags=("a", "b"))
        assert vm.state.name == "changed"
        assert vm.state.count == 42
        assert vm.state.tags == ("a", "b")
        # 原实例不变 (frozen dataclass 不可变)
        assert original_state.name == ""
        assert original_state.count == 0

    def test_set_state_notifies_with_new_snapshot(self):
        """_set_state 通知的 snapshot 应是新 state (不是旧 state)。"""
        vm = _DefaultVM()
        received: list[_DummyState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._set_state(name="new")
        assert len(received) == 1
        assert received[0] is vm._state  # 同一实例
        assert received[0].name == "new"

    def test_multiple_subscribers_all_invoked(self):
        vm = _DefaultVM()
        calls_a: list[_DummyState] = []
        calls_b: list[_DummyState] = []
        vm.subscribe(lambda s: calls_a.append(s))
        vm.subscribe(lambda s: calls_b.append(s))
        vm._set_state(name="x")
        assert len(calls_a) == 1
        assert len(calls_b) == 1
        assert calls_a[0] is calls_b[0]

    def test_dispose_clears_subscribers(self):
        vm = _DefaultVM()
        vm.subscribe(lambda s: None)
        vm.subscribe(lambda s: None)
        assert len(vm._subscribers) == 2
        vm.dispose()
        assert len(vm._subscribers) == 0

    def test_dispose_idempotent(self):
        """重复 dispose 不应抛异常。"""
        vm = _DefaultVM()
        vm.subscribe(lambda s: None)
        vm.dispose()
        vm.dispose()  # 不应抛异常


# ============================================================
# Test: hooks.py:112 (new_state) 单参数协议不变
# ============================================================


class TestHooksProtocol:
    """F1/F12: hooks.py:112 ``callback(new_state)`` 单参数协议不变。"""

    def test_subscribe_callback_receives_single_state_arg(self):
        """订阅者回调应接收单个 new_state 参数 (hooks.py:112 协议)。"""
        vm = _DefaultVM()
        received_args: list[tuple] = []

        def _callback(*args) -> None:
            received_args.append(args)

        vm.subscribe(_callback)
        vm._set_state(name="trigger")
        assert len(received_args) == 1
        assert len(received_args[0]) == 1  # 单参数
        assert isinstance(received_args[0][0], _DummyState)

    def test_subscribe_signature_single_param(self):
        """F12: subscribe 签名保持 (callback) -> unsub 单参数。"""
        import inspect

        sig = inspect.signature(ObservableViewModelMixin.subscribe)
        params = list(sig.parameters.keys())
        # self + callback = 2 个参数 (self 不算)
        assert len(params) == 2
        assert "callback" in params
        assert "self" in params


# ============================================================
# Test: 子类覆盖 subscribe (模拟 ScreenerViewModel)
# ============================================================


class TestCustomSubscribe:
    """子类覆盖 subscribe 保留自定义逻辑 (F12)。"""

    def test_custom_subscribe_called(self):
        vm = _CustomSubscribeVM()
        vm.subscribe(lambda s: None)
        assert vm._custom_subscribe_called is True

    def test_custom_subscribe_still_appends_to_subscribers(self):
        """覆盖 subscribe 仍应将 callback 加入 _subscribers (调默认实现或自行 append)。"""
        vm = _CustomSubscribeVM()
        vm.subscribe(lambda s: None)
        assert len(vm._subscribers) == 1

    def test_custom_subscribe_unsub_works(self):
        vm = _CustomSubscribeVM()
        snapshots: list[_DummyState] = []
        unsub = vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(name="first")
        unsub()
        vm._set_state(name="second")
        assert len(snapshots) == 1

    def test_custom_subscribe_returns_callable(self):
        vm = _CustomSubscribeVM()
        unsub = vm.subscribe(lambda s: None)
        assert callable(unsub)


# ============================================================
# Test: 子类覆盖 dispose (模拟 ScreenerViewModel)
# ============================================================


class TestCustomDispose:
    """子类覆盖 dispose 保留自定义逻辑。"""

    def test_custom_dispose_called(self):
        vm = _CustomDisposeVM()
        vm.dispose()
        assert vm._custom_cleanup_called is True

    def test_custom_dispose_clears_subscribers(self):
        """覆盖 dispose 应在末尾调 super().dispose() 清理订阅者。"""
        vm = _CustomDisposeVM()
        vm.subscribe(lambda s: None)
        vm.subscribe(lambda s: None)
        assert len(vm._subscribers) == 2
        vm.dispose()
        assert len(vm._subscribers) == 0

    def test_custom_dispose_clears_background_tasks(self):
        """自定义清理逻辑应执行 (清理 _background_tasks)。"""
        vm = _CustomDisposeVM()
        vm._background_tasks.add("fake_task_1")
        vm._background_tasks.add("fake_task_2")
        vm.dispose()
        assert len(vm._background_tasks) == 0
        assert vm._disposed is True


# ============================================================
# Test: 子类覆盖 _set_state (disposed guard, 模拟 ScreenerViewModel)
# ============================================================


class TestCustomSetState:
    """子类覆盖 _set_state 加 disposed guard。"""

    def test_set_state_before_dispose_works(self):
        vm = _CustomSetStateVM()
        vm._set_state(name="before")
        assert vm.state.name == "before"

    def test_set_state_after_dispose_noop(self):
        """disposed 后 _set_state 应早返, 不更新 state, 不通知。"""
        vm = _CustomSetStateVM()
        snapshots: list[_DummyState] = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(name="before")
        assert len(snapshots) == 1

        vm._disposed = True
        vm._set_state(name="after_dispose")
        assert vm.state.name == "before"  # 未更新
        assert len(snapshots) == 1  # 未通知


# ============================================================
# Test: 子类覆盖 _notify (try/except, 模拟 HomeViewModel)
# ============================================================


class TestCustomNotify:
    """子类覆盖 _notify 用 try/except 包裹 subscriber 调用。"""

    def test_subscriber_exception_does_not_propagate(self):
        """单个 subscriber 异常不应中断其他 subscriber 通知。"""
        vm = _CustomNotifyVM()
        good_calls: list[_DummyState] = []

        def _bad_callback(s: _DummyState) -> None:
            raise ValueError("subscriber boom")

        vm.subscribe(_bad_callback)
        vm.subscribe(lambda s: good_calls.append(s))
        vm._set_state(name="trigger")

        # 好的 subscriber 仍被调用
        assert len(good_calls) == 1
        # 异常被捕获记录
        assert len(vm._subscriber_errors) == 1
        assert "subscriber boom" in vm._subscriber_errors[0]


# ============================================================
# Test: 与真实 VM 集成 (HomeViewModel 迁移后验证)
# ============================================================


class TestRealVMIntegration:
    """迁移真实 VM 后, 验证 mixin 兼容性 (DoD Task 2.1: 至少迁移 1 个简单 VM)。"""

    def test_home_view_model_uses_mixin(self):
        """HomeViewModel 应继承 ObservableViewModelMixin。"""
        from ui.viewmodels.home_view_model import HomeViewModel

        assert issubclass(HomeViewModel, ObservableViewModelMixin)

    def test_home_view_model_state_property(self):
        """HomeViewModel.state 应返回 HomeState (mixin state property 被 Generic 类型具体化)。"""
        from unittest.mock import patch

        with (
            patch("ui.viewmodels.home_view_model.DataProcessor"),
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService"),
            patch("ui.viewmodels.home_view_model.MarketDataService"),
        ):
            from ui.viewmodels.home_view_model import HomeState, HomeViewModel

            vm = HomeViewModel()
            assert isinstance(vm.state, HomeState)

    def test_home_view_model_subscribe_and_notify(self):
        """HomeViewModel subscribe/_notify 行为不变 (mixin 默认实现 + 子类 _notify 覆盖)。"""
        from unittest.mock import patch

        with (
            patch("ui.viewmodels.home_view_model.DataProcessor"),
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService"),
            patch("ui.viewmodels.home_view_model.MarketDataService"),
        ):
            from ui.viewmodels.home_view_model import HomeViewModel

            vm = HomeViewModel()
            snapshots: list = []
            vm.subscribe(lambda s: snapshots.append(s))
            vm._set_state(news_page=5)
            assert len(snapshots) == 1
            assert vm.state.news_page == 5
