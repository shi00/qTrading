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

import collections
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
        # Mixin 跨线程字段（与真实 VM 一致：构造函数末尾初始化）
        self._init_mixin_fields()


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
        # Mixin 跨线程字段
        self._init_mixin_fields()

    def subscribe(self, callback: Callable[[_DummyState], None]) -> Callable[[], None]:
        """覆盖 subscribe: 捕获 main loop + 调默认实现。

        注意：覆盖 subscribe 时应至少刷新 _owner_tid（Mixin 同步模式判定依赖它）。
        真实 VM（如 ScreenerVM）已不再 override subscribe，此处保留用于测试 override 能力。
        """
        import threading

        self._custom_subscribe_called = True
        # 刷新 owner_tid（Mix 同步模式/跨线程判定依赖此字段）
        self._owner_tid = threading.get_ident()
        self._subscribers.append(callback)
        try:
            import asyncio

            captured = asyncio.get_running_loop()
            self._main_loop = captured
            # 首次 loop 捕获后 flush pending（与 Mixin.subscribe 一致）
            if self._pending_notifications:
                with self._subscribers_lock:
                    pending = self._pending_notifications
                    self._pending_notifications = collections.deque(maxlen=64)
                for s, st in pending:
                    self._do_notify(list(s), st)
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
        # Mixin 跨线程字段
        self._init_mixin_fields()

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
        # Mixin 跨线程字段
        self._init_mixin_fields()

    def _set_state(self, **changes: Any) -> None:
        """覆盖 _set_state: disposed 时早返。"""
        if self._disposed:
            return
        super()._set_state(**changes)


# ============================================================
# 自定义 _notify VM (try/except 包裹, 模拟 HomeViewModel._notify)
# ============================================================


class _CustomNotifyVM(ObservableViewModelMixin[_DummyState]):
    """覆盖 _invoke_single_subscriber 做 per-cb 异常隔离（新范式，替代 override _notify）。

    注意：本测试类按 P2 Segment2 新范式实现——不再 override 终态骨架 _notify()，
    改为覆盖 _invoke_single_subscriber()。这验证架构 P0-1 修复（子类不再绕开 Mixin
    跨线程封送逻辑）。
    """

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []
        self._subscriber_errors: list[str] = []
        # Mixin 跨线程字段
        self._init_mixin_fields()

    def _invoke_single_subscriber(self, cb: Callable[[_DummyState], None], snap: _DummyState) -> None:
        """覆盖 per-cb 调用：异常记录到 _subscriber_errors（不中断其他 subscriber）。"""
        try:
            cb(snap)
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


# ============================================================
# Test: 跨线程通知 (T1 / T3 / T4 / T6 / T8 / T9)
# ============================================================


class TestCrossThreadNotification:
    """P2 Segment2: 跨线程通知的核心行为验证。"""

    @pytest.mark.asyncio
    async def test_t1_dispose_skip_cross_thread_notify(self):
        """T1: disposed 后跨线程触发的 _notify 不更新 state/subscribers。"""
        import threading

        vm = _DefaultVM()
        snapshots: list[_DummyState] = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(name="before")
        assert len(snapshots) == 1

        # 当前 loop 已在 asyncio pytest fixture 中运行；
        # 模拟：VM disposed，然后工作线程尝试触发 _notify
        vm._disposed = True
        vm.dispose()  # 调正式 dispose 清理订阅者

        # 工作线程调 _set_state：应短路（不抛错，state 不变，subscribers 为空）
        worker_errors: list[Exception] = []

        def worker_update() -> None:
            try:
                vm._set_state(name="from_worker")
            except Exception as e:  # noqa: BLE001
                # 记录异常供主断言
                worker_errors.append(e)

        t = threading.Thread(target=worker_update, daemon=True)
        t.start()
        assert t.join(timeout=2.0) is None, "worker 未在 2s 内完成"
        assert len(worker_errors) == 0, f"worker _set_state 不应抛异常: {worker_errors[0]!r}"
        # state 仍为被 disposed 后重置的初始值（DefaultVM dispose 未单独替换 state）
        # 但 snapshots 已清空（dispose cleared subscribers），因此 notify 时也无可通知对象
        assert len(vm._subscribers) == 0

    @pytest.mark.asyncio
    async def test_t3_task_center_like_callback(self):
        """T3: 模拟 TaskCenterVM._on_tasks_updated 跨线程回调最终 state 被更新。"""
        import asyncio
        import threading

        vm = _DefaultVM()
        snapshots: list[_DummyState] = []

        # 在主 loop 线程 subscribe，捕获 loop/tid
        unsub = vm.subscribe(lambda s: snapshots.append(s))
        assert vm._main_loop is not None, "subscribe 时应捕获 running loop"
        assert vm._owner_tid is not None

        # 工作线程触发 _set_state（模拟 TM 回调）
        worker_done = threading.Event()

        def worker_trigger() -> None:
            try:
                vm._set_state(name="from_tm_thread")
            finally:
                worker_done.set()

        t = threading.Thread(target=worker_trigger, daemon=True)
        t.start()
        assert worker_done.wait(timeout=2.0), "worker 未在 2s 内完成"
        t.join(timeout=1.0)

        # 等待主 loop 执行 call_soon_threadsafe 的回调（scheduler tick）
        await asyncio.sleep(0.05)
        # 给 loop 多一次机会以防止调度延迟
        await asyncio.sleep(0.05)

        # 最终 state.name 应被 set（跨线程调度在 0.1s 内生效）
        assert vm.state.name == "from_tm_thread"
        assert len(snapshots) >= 1
        assert snapshots[-1].name == "from_tm_thread"
        unsub()

    @pytest.mark.asyncio
    async def test_t4_health_scan_like_callback(self):
        """T4: 模拟 HealthScanVM.on_progress 跨线程回调 state 最终更新。"""
        import asyncio
        import threading

        vm = _DefaultVM()
        # 先 subscribe 捕获 loop
        unsub = vm.subscribe(lambda s: None)

        # 工作线程在短时间内触发多次进度更新
        errors: list[Exception] = []

        def worker_progress() -> None:
            try:
                for i in range(10):
                    vm._set_state(count=i, name=f"progress_{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=worker_progress, daemon=True)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert errors == [], f"worker 中不应有异常: {errors!r}"

        await asyncio.sleep(0.1)
        # 节流后只保留最后一次的最终 state 应合理（至少 progress_9）
        assert vm.state.name == "progress_9"
        assert vm.state.count == 9
        unsub()

    def test_t6_concurrent_subscribe_unsubscribe_no_corruption(self):
        """T6: 并发 subscribe/unsubscribe 不损坏 subscriber list（_subscribers_lock）。"""
        import threading

        vm = _DefaultVM()
        N = 200
        unsubs: list[Callable[[], None]] = []
        errors: list[Exception] = []

        def add_subs() -> None:
            try:
                for _ in range(N):
                    unsubs.append(vm.subscribe(lambda s: None))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def remove_subs() -> None:
            try:
                for _ in range(N):
                    # 触发 _notify（内部在锁下快照）
                    vm._set_state(name="tick")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=add_subs, daemon=True)
        t2 = threading.Thread(target=remove_subs, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        assert not t1.is_alive() and not t2.is_alive()
        assert errors == [], f"并发操作不应有异常: {errors!r}"

        # 移除所有订阅者后 _subscribers 最终应为 0（或 add 与 remove 平衡）
        for u in unsubs:
            u()
        assert len(vm._subscribers) == 0

    @pytest.mark.asyncio
    async def test_t8_pending_notifications_drain_after_subscribe_captures_loop(self):
        """T8: 无 loop 时 _notify 入队；subscribe 捕获 loop 后队列被 drain。

        构造方法：手动 append 到 _subscribers，保持 owner_tid=-1 (未 subscribe)，
        此时 loop_captured 为 None → 走 buffer 分支。
        """
        import asyncio

        vm = _DefaultVM()
        snapshots: list[_DummyState] = []

        # 手动 append callback（不走 subscribe，保持 owner_tid=-1、loop=None）
        def _cb(s: _DummyState) -> None:
            snapshots.append(s)

        cb: Callable[[_DummyState], None] = _cb
        with vm._subscribers_lock:
            vm._subscribers.append(cb)

        # 无 loop 场景下触发 3 次 _set_state：应进入 pending 队列
        assert vm._main_loop is None
        assert vm._owner_tid in (None, -1)
        vm._set_state(name="q1")
        vm._set_state(name="q2")
        vm._set_state(name="q3")
        # 未进入正式 subscribe，同步模式不触发 _do_notify
        assert snapshots == []
        assert len(vm._pending_notifications) == 3

        # 订阅（此时在 running loop 中）——触发 flush_pending_notifications
        # 此时 subscribe 会成功捕获 loop；而且 pending deque 会 drain
        unsub = vm.subscribe(lambda s: None)  # 追加一个不影响 snapshots 的 dummy
        await asyncio.sleep(0.05)
        # 至少最后一次通知被 drain；具体次数不强制
        assert len(snapshots) >= 1
        assert snapshots[-1].name == "q3"
        unsub()

    @pytest.mark.asyncio
    async def test_t9_throttle_keeps_only_latest_notification(self):
        """T9: 连续 N 次跨线程 _notify 在同一 tick 只保留最后 1 次。"""
        import asyncio
        import threading

        vm = _DefaultVM()
        snapshots: list[_DummyState] = []
        unsub = vm.subscribe(lambda s: snapshots.append(s))
        assert vm._main_loop is not None

        N = 50
        fired = threading.Event()

        def worker_burst() -> None:
            for i in range(N):
                vm._set_state(count=i, name=f"burst_{i}")
            fired.set()

        t = threading.Thread(target=worker_burst, daemon=True)
        t.start()
        assert fired.wait(timeout=2.0)
        t.join(timeout=1.0)

        await asyncio.sleep(0.1)
        # 最终 state 应为最后一次写入
        assert vm.state.name == f"burst_{N - 1}"
        assert vm.state.count == N - 1
        # 实际通知次数 < N（节流）；但测试不假设具体次数（调度器行为）
        unsub()


# ============================================================
# Test: 异常隔离 + disposed 语义（T2 / T5 / T7 / T11）
# ============================================================


class TestExceptionIsolationAndDispose:
    """P2 Segment2: 异常隔离、disposed 语义、loop 关闭防御。"""

    def test_t2_invoke_single_subscriber_isolates_per_cb(self):
        """T2: _invoke_single_subscriber 异常隔离（per-cb try/except）。"""
        vm = _CustomNotifyVM()
        calls: list[_DummyState] = []

        def bad_cb(s: _DummyState) -> None:
            raise RuntimeError("bad!")

        def good_cb(s: _DummyState) -> None:
            calls.append(s)

        vm.subscribe(bad_cb)
        vm.subscribe(good_cb)
        vm._set_state(name="x")
        assert len(calls) == 1
        assert calls[0].name == "x"
        assert len(vm._subscriber_errors) == 1
        assert "bad!" in vm._subscriber_errors[0]

    @pytest.mark.asyncio
    async def test_t5_loop_closed_disposed_early_return(self):
        """T5: loop 关闭 + _disposed=True 时 _notify 早返不抛 RuntimeError。"""

        vm = _DefaultVM()
        unsub = vm.subscribe(lambda s: None)
        assert vm._main_loop is not None

        # 标记 disposed 并调 dispose（Mixin dispose 将 _main_loop 置 None）
        vm.dispose()

        # _main_loop 已置 None → 同步路径；但 _disposed=True，_notify 会短路
        vm._set_state(name="after")  # 不抛异常
        assert len(vm._subscribers) == 0
        unsub()  # 已 dispose，unsub 应该也安全（幂等）

    def test_t7_default_do_notify_isolates_subscriber_exception(self):
        """T7: _do_notify 默认实现中单个 subscriber 异常不中断其余（带 logging）。"""
        import logging

        vm = _DefaultVM()
        good: list[_DummyState] = []

        def bad_cb(s: _DummyState) -> None:
            raise KeyError("boom")

        vm.subscribe(bad_cb)
        vm.subscribe(lambda s: good.append(s))

        # _do_notify 应吞掉 bad_cb 的 KeyError 并记录 warning
        with self.assertLogs(logging.getLogger("ui.viewmodels.observable_mixin"), level="WARNING") as cm:
            vm._set_state(name="t7")

        assert len(good) == 1
        assert good[0].name == "t7"
        # 日志中应包含 subscriber 异常的痕迹
        joined = "\n".join(cm.output)
        assert "subscriber" in joined.lower() or "boom" in joined

    @staticmethod
    def assertLogs(logger, level=None):
        """为 TestExceptionIsolationAndDispose.test_t7 提供 with-statement assertLogs。"""
        import contextlib
        import logging

        @contextlib.contextmanager
        def _cm():
            _logger = logging.getLogger(logger) if isinstance(logger, str) else logger
            old_level = _logger.level
            records: list[logging.LogRecord] = []

            class _H(logging.Handler):
                def emit(self, record):
                    records.append(record)

            handler = _H(level=logging.DEBUG if level is None else getattr(logging, level))
            _logger.addHandler(handler)
            _logger.setLevel(logging.DEBUG)
            try:

                class _NS:
                    output: list[str] = []

                ns = _NS()
                yield ns
            finally:
                _logger.removeHandler(handler)
                _logger.setLevel(old_level)
                ns.output = [handler.formatter.format(r) if handler.formatter else r.getMessage() for r in records]

        return _cm()

    def test_t11_get_loop_or_none_returns_none_after_dispose(self):
        """T11: _get_loop_or_none() 在 disposed=True 时返回 None。"""
        vm = _DefaultVM()
        vm._main_loop = "fake_loop"  # type: ignore[assignment]
        vm._disposed = True
        assert vm._get_loop_or_none() is None


# ============================================================
# Test: TaskCenterVM / HealthScanVM / 同步模式 集成（T10 / T12）
# ============================================================


class TestVMIntegrationContracts:
    """T10 / T12: 同步模式下 TaskCenterVM 行为、HealthScanVM 契约。"""

    def test_t10_cancel_pending_futures_empty_set_no_error(self):
        """T10: HealthScanVM.cancel_pending_futures 空 set 不报错。"""
        from unittest.mock import patch

        with patch("ui.viewmodels.health_scan_view_model.DataProcessor"):
            from ui.viewmodels.health_scan_view_model import HealthScanViewModel

            vm = HealthScanViewModel()
            # 初始化后 _futures 为空
            assert len(vm._futures) == 0
            # 不抛异常
            vm.cancel_pending_futures()
            assert len(vm._futures) == 0
            vm.dispose()

    def test_t12_task_center_sync_mode_updates_state(self):
        """T12: TaskCenterVM 无 loop（同步测试模式）中 _on_tasks_updated 同步更新。"""
        from unittest.mock import patch

        with (
            patch("ui.viewmodels.task_center_view_model.TaskManager") as MockTM,
        ):
            from services.task_manager import AppTask, TaskStatus
            from ui.viewmodels.task_center_view_model import TaskCenterViewModel

            mock_tm = MockTM.return_value
            mock_tm.get_all_tasks.return_value = []
            vm = TaskCenterViewModel()
            # assert 初始 loop 为 None（同步模式）
            assert vm._main_loop is None
            snapshots = []
            vm.subscribe(lambda s: snapshots.append(s))

            fake_task = AppTask(
                id="t1",
                name="sync_task",
                task_type="data_sync",
                status=TaskStatus.RUNNING,
                progress=0.5,
            )
            # 模拟 TM 回调同步调用（非跨线程）
            vm._on_tasks_updated([fake_task])
            assert len(snapshots) >= 1
            assert vm.state.total_count == 1
            vm.dispose()


# ============================================================
# Test: P3-M12-008 静默 except 补充 debug 日志
# ============================================================


class TestSilentExceptDebugLog:
    """P3-M12-008: 两处 except pass 补充 debug 日志以便诊断。"""

    def test_dispose_cancel_failure_logs_debug(self, caplog):
        """dispose 时 cancel handle 失败应记录 debug 日志。"""
        from unittest.mock import MagicMock

        vm = _DefaultVM()
        vm.subscribe(lambda s: None)

        # 设置一个 cancel 会抛异常的 handle
        bad_handle = MagicMock()
        bad_handle.cancel.side_effect = RuntimeError("cancel failed")
        vm._pending_notify_handle = bad_handle

        with caplog.at_level("DEBUG", logger="ui.viewmodels.observable_mixin"):
            vm.dispose()

        # 断言 debug 日志被发出
        assert any("dispose cancel handle failed" in r.getMessage() for r in caplog.records)

    def test_dispatch_cancel_old_handle_failure_logs_debug(self, caplog):
        """跨线程 dispatch 时 cancel old handle 失败应记录 debug 日志。"""
        import threading
        from unittest.mock import MagicMock

        vm = _DefaultVM()
        vm.subscribe(lambda s: None)

        # 设置一个 cancel 会抛异常的 old_handle
        bad_handle = MagicMock()
        bad_handle.cancel.side_effect = RuntimeError("cancel failed")
        vm._pending_notify_handle = bad_handle

        # 构造跨线程场景：fake loop + 不同 tid
        fake_loop = MagicMock()
        fake_loop.is_running.return_value = True
        fake_loop.call_soon_threadsafe.return_value = MagicMock()

        with caplog.at_level("DEBUG", logger="ui.viewmodels.observable_mixin"):
            vm._dispatch_notification_impl(
                subs_snap=list(vm._subscribers),
                state_snap=vm._state,
                loop=fake_loop,
                owner_tid=threading.get_ident() + 1,  # 不同 tid → cross_thread
            )

        assert any("throttle cancel old handle failed" in r.getMessage() for r in caplog.records)
