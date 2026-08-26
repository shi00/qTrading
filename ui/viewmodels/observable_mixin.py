"""ObservableViewModelMixin — 跨线程安全的 VM 可观察协议（review01-A10 组合化）。

架构演进（review01-A10）：
- 原实现将全部跨线程通知状态机（subscribers/locks/loop/disposed/pending）作为
  mixin 字段混入宿主 VM 命名空间，宿主 __init__ 必须记得调用 `_init_mixin_fields()`，
  漏调即产生运行时 AttributeError。
- 现拆为 `ViewModelNotifier`（组合内核）承载全部状态机，mixin 变为薄门面委托
  notifier。通知逻辑状态封闭在 Notifier 内，不再污染 VM 命名空间，
  "忘记初始化 mixin 字段"的失败模式被消除（notifier 在 __init__ 自建）。
- VM 代码零改动：`self._set_state(...)` / `self.subscribe(...)` / `self.dispose()`
  仍可用，由 mixin 委托 notifier。VM 覆盖的扩展点（_invoke_single_subscriber /
  _do_notify / _dispatch_notification / _on_after_dispose / _set_state）通过
  notifier 调回 owner 实现；`_get_loop_or_none` 由 mixin 直接委托
  notifier.get_loop_or_none()（当前无 VM override，调用面不变）。

保留的架构约束（自原 mixin 吸收）：
- subscribe / dispose / _set_state / _notify 为骨架，跨线程修复不可被绕过
- dispose 三步原子关闭协议：shutdown_lock 临界区内翻转 _disposed + 取快照，锁外做 cancel
- loop 用强引用；_owner_tid 每次 subscribe 刷新绑定到最新线程（Skeptic P1 #8）
- _pending_notifications deque maxlen=1000（Skeptic P1 #7）
- _do_notify 默认 per-cb try/except 异常隔离（Skeptic P1 #4）
- closure 默认参数绑定 loop 强引用（Skeptic P1 #1）；fire_and_wrap 每 cb 前查 disposed（P0 #3）
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar, cast

from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_MAX_PENDING = 1000  # deque 上限，无 loop 场景下 OOM 防护

T = TypeVar("T")


class ViewModelNotifier[T]:
    """跨线程安全的 VM 可观察状态机（review01-A10 组合内核）。

    承载原 ObservableViewModelMixin 的全部通知状态（subscribers/锁/loop/disposed/pending），
    通过 ``owner`` 引用回读宿主扩展点（``_invoke_single_subscriber`` / ``_do_notify`` /
    ``_dispatch_notification`` / ``_get_loop_or_none``）与 ``_state``。

    状态封闭在本类内，不污染宿主 VM 命名空间；宿主在 __init__ 中创建 notifier 实例。
    """

    def __init__(self, owner: object) -> None:
        self._owner = owner
        self._subscribers: list[Callable[[T], None]] = []
        self._subscribers_lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._owner_tid: int = -1
        self._disposed = False
        self._pending_notify_handle: object | None = None
        self._pending_notifications: deque = deque(maxlen=_MAX_PENDING)

    # ------------------------------------------------------------------
    # 外部 API
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。

        每次 subscribe 刷新 _owner_tid + 尝试捕获 loop（Skeptic P1 #8）。
        捕获成功则立即 flush 堆积的 pending 通知。
        """
        with self._subscribers_lock:
            self._subscribers.append(callback)

        self._owner_tid = threading.get_ident()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        captured_first_time = False
        if loop is not None:
            with self._shutdown_lock:
                if self._main_loop is None and not self._disposed:
                    self._main_loop = loop
                    captured_first_time = True

        pending_to_flush: deque | None = None
        if self._main_loop is not None and self._pending_notifications:
            with self._subscribers_lock:
                if self._pending_notifications:
                    pending_to_flush = self._pending_notifications
                    self._pending_notifications = deque(maxlen=_MAX_PENDING)

        if pending_to_flush is not None and captured_first_time:
            for subs_snap, state_snap in pending_to_flush:
                self._dispatch_notification(subs_snap, state_snap)

        def _unsubscribe() -> None:
            with self._subscribers_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def notify(self, state: T) -> None:
        """state 变化后通知所有订阅者（骨架，不可绕过）。"""
        if self._disposed:
            return

        with self._shutdown_lock:
            if self._disposed:
                return
            with self._subscribers_lock:
                subs_snapshot: list[Callable[[T], None]] = list(self._subscribers)
            state_snapshot: T = state
            loop_captured = self._main_loop
            owner_tid_captured = self._owner_tid

        if not subs_snapshot:
            return

        if loop_captured is None:
            if owner_tid_captured in (None, -1):
                self._buffer_notification(subs_snapshot, state_snapshot)
            else:
                self._do_notify(subs_snapshot, state_snapshot)
            return

        self._dispatch_notification_impl(subs_snapshot, state_snapshot, loop_captured, owner_tid_captured)

    def dispose(self) -> None:
        """清理资源（三步原子关闭协议）。

        1. shutdown_lock 临界区：_disposed=True，取 subs/pending 快照，清 subs/deque，断 loop
        2. 锁外：cancel pending notify handle
        3. 调用宿主扩展点 _on_after_dispose(subs_snapshot, captured_loop)

        幂等：即使宿主已置 _disposed 仍需完成清理（基础操作均幂等）。
        """
        with self._shutdown_lock:
            self._disposed = True
            with self._subscribers_lock:
                subs_snapshot: list[Callable[[T], None]] = list(self._subscribers)
                pending_snapshot: deque = self._pending_notifications
                self._subscribers.clear()
                self._pending_notifications = deque(maxlen=_MAX_PENDING)
            captured_handle = self._pending_notify_handle
            self._pending_notify_handle = None
            captured_loop = self._main_loop
            self._main_loop = None
            self._owner_tid = -1

        if captured_handle is not None and hasattr(captured_handle, "cancel") and not isinstance(captured_handle, bool):
            try:
                cast(Any, captured_handle).cancel()
            except Exception:
                logger.debug("[ViewModelNotifier] dispose cancel handle failed", exc_info=True)

        self._on_after_dispose(subs_snapshot, pending_snapshot, captured_loop)

    def get_loop_or_none(self) -> asyncio.AbstractEventLoop | None:
        """Lazy loop capture getter（宿主创建 task 场景统一入口）。"""
        if self._disposed:
            return None
        maybe_loop = self._main_loop
        if isinstance(maybe_loop, asyncio.AbstractEventLoop):
            if maybe_loop.is_running():
                return maybe_loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        if self._main_loop is None and not self._disposed:
            with self._shutdown_lock:
                if self._main_loop is None and not self._disposed:
                    self._main_loop = loop
                    self._owner_tid = threading.get_ident()
                    if self._pending_notifications:
                        with self._subscribers_lock:
                            pending = self._pending_notifications
                            self._pending_notifications = deque(maxlen=_MAX_PENDING)
                        for s, st in pending:
                            self._dispatch_notification_impl(s, st, loop, self._owner_tid)
        return loop

    @property
    def disposed(self) -> bool:
        return self._disposed

    # ------------------------------------------------------------------
    # 扩展点：委托回宿主（mixin 默认实现，VM 可覆盖）
    # ------------------------------------------------------------------

    def _invoke_single_subscriber(self, cb: Callable[[T], None], snap: T) -> None:
        owner = self._owner
        hook = getattr(owner, "_invoke_single_subscriber", None)
        if hook is not None and hook.__func__ is not ViewModelNotifier._invoke_single_subscriber:
            hook(cb, snap)
            return
        try:
            cb(snap)
        except Exception as e:  # noqa: BLE001  —  intentionally broad catch
            logger.error(
                "[ViewModelNotifier] subscriber callback failed: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    def _do_notify(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        owner = self._owner
        hook = getattr(owner, "_do_notify", None)
        if hook is not None and hook.__func__ is not ViewModelNotifier._do_notify:
            hook(subs_snap, state_snap)
            return
        for cb in subs_snap:
            if self._disposed:
                return
            self._invoke_single_subscriber(cb, state_snap)

    def _dispatch_notification(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        owner = self._owner
        hook = getattr(owner, "_dispatch_notification", None)
        if hook is not None and hook.__func__ is not ViewModelNotifier._dispatch_notification:
            hook(subs_snap, state_snap)
            return
        loop = self._main_loop
        owner_tid = self._owner_tid
        if loop is None:
            if owner_tid in (None, -1):
                self._buffer_notification(subs_snap, state_snap)
            else:
                self._do_notify(subs_snap, state_snap)
            return
        self._dispatch_notification_impl(subs_snap, state_snap, loop, owner_tid)

    def _on_after_dispose(
        self,
        subs_snapshot: list[Callable[[T], None]],
        pending_snapshot: deque,
        captured_loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        owner = self._owner
        hook = getattr(owner, "_on_after_dispose", None)
        if hook is not None and hook.__func__ is not ViewModelNotifier._on_after_dispose:
            hook(subs_snapshot, pending_snapshot, captured_loop)

    # ------------------------------------------------------------------
    # 内部实现（非扩展点）
    # ------------------------------------------------------------------

    def _buffer_notification(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        dq = self._pending_notifications
        if len(dq) == _MAX_PENDING - 1:
            logger.warning(
                "[ViewModelNotifier] pending notifications near cap %d; oldest will be dropped",
                _MAX_PENDING,
            )
        with self._subscribers_lock:
            dq.append((subs_snap, state_snap))

    def _dispatch_notification_impl(
        self,
        subs_snap: list[Callable[[T], None]],
        state_snap: T,
        loop: asyncio.AbstractEventLoop,
        owner_tid: int,
    ) -> None:
        """统一跨线程调度：同线程 fast-path / 跨线程 call_soon_threadsafe + 合并节流。"""
        cross_thread = owner_tid != -1 and threading.get_ident() != owner_tid and loop.is_running()

        if not cross_thread:
            self._do_notify(subs_snap, state_snap)
            return

        def fire_and_wrap(
            subs_closure: list[Callable[[T], None]] = subs_snap,
            state_closure: T = state_snap,
            loop_closure: asyncio.AbstractEventLoop = loop,
        ) -> None:
            if self._disposed:
                return
            cur_handle = self._pending_notify_handle
            if cur_handle is handle_ref_holder.get("handle"):
                self._pending_notify_handle = None
            self._do_notify(subs_closure, state_closure)

        handle_ref_holder: dict[str, Any] = {"handle": None}

        old_handle = self._pending_notify_handle
        if old_handle is not None and hasattr(old_handle, "cancel") and not isinstance(old_handle, bool):
            try:
                cast(Any, old_handle).cancel()
            except Exception:
                logger.debug("[ViewModelNotifier] throttle cancel old handle failed", exc_info=True)

        try:
            handle = loop.call_soon_threadsafe(fire_and_wrap)
        except RuntimeError:
            self._pending_notify_handle = None
            return

        handle_ref_holder["handle"] = handle
        self._pending_notify_handle = handle


class ObservableViewModelMixin[T]:
    """ViewModel 可观察协议薄门面（review01-A10 组合化）。

    内部持有 ``ViewModelNotifier`` 实例承载全部通知状态机；本 mixin 仅做委托，
    保持 ``subscribe`` / ``_set_state`` / ``dispose`` / ``state`` / ``_get_loop_or_none``
    的既有调用面不变（VM 代码零改动）。子类需在 __init__ 中初始化 ``self._state``，
    并调用 ``_init_mixin_fields()``（创建 notifier 并绑定宿主）。
    """

    _state: T
    _notifier: ViewModelNotifier[T]

    # ------------------------------------------------------------------
    # 兼容代理属性：VM/测试对原 mixin 字段的直接访问委托 notifier。
    # 状态仍封闭在 ViewModelNotifier 内（组合内核），property 仅作透明桥，
    # 避免 VM 的 `self._subscribers = []` 等初始化覆盖 notifier 内部状态。
    # ------------------------------------------------------------------
    @property
    def _subscribers(self) -> list[Callable[[T], None]]:
        self._ensure_notifier()
        return self._notifier._subscribers

    @_subscribers.setter
    def _subscribers(self, value: list[Callable[[T], None]]) -> None:
        self._ensure_notifier()
        self._notifier._subscribers = value

    @property
    def _subscribers_lock(self) -> threading.RLock:
        self._ensure_notifier()
        return self._notifier._subscribers_lock

    @_subscribers_lock.setter
    def _subscribers_lock(self, value: threading.RLock) -> None:
        self._ensure_notifier()
        self._notifier._subscribers_lock = value

    @property
    def _shutdown_lock(self) -> threading.Lock:
        self._ensure_notifier()
        return self._notifier._shutdown_lock

    @_shutdown_lock.setter
    def _shutdown_lock(self, value: threading.Lock) -> None:
        self._ensure_notifier()
        self._notifier._shutdown_lock = value

    @property
    def _main_loop(self) -> asyncio.AbstractEventLoop | None:
        self._ensure_notifier()
        return self._notifier._main_loop

    @_main_loop.setter
    def _main_loop(self, value: asyncio.AbstractEventLoop | None) -> None:
        self._ensure_notifier()
        self._notifier._main_loop = value

    @property
    def _owner_tid(self) -> int:
        self._ensure_notifier()
        return self._notifier._owner_tid

    @_owner_tid.setter
    def _owner_tid(self, value: int) -> None:
        self._ensure_notifier()
        self._notifier._owner_tid = value

    @property
    def _disposed(self) -> bool:
        self._ensure_notifier()
        return self._notifier._disposed

    @_disposed.setter
    def _disposed(self, value: bool) -> None:
        self._ensure_notifier()
        self._notifier._disposed = value

    @property
    def _pending_notifications(self) -> deque:
        self._ensure_notifier()
        return self._notifier._pending_notifications

    @_pending_notifications.setter
    def _pending_notifications(self, value: deque) -> None:
        self._ensure_notifier()
        self._notifier._pending_notifications = value

    @property
    def _pending_notify_handle(self) -> object | None:
        self._ensure_notifier()
        return self._notifier._pending_notify_handle

    @_pending_notify_handle.setter
    def _pending_notify_handle(self, value: object | None) -> None:
        self._ensure_notifier()
        self._notifier._pending_notify_handle = value

    # ------------------------------------------------------------------
    # 初始化辅助：子类 __init__ 末尾调用本方法
    # ------------------------------------------------------------------
    def _init_mixin_fields(self) -> None:
        """初始化 Notifier 实例（幂等）。"""
        if not hasattr(self, "_notifier"):
            self._notifier = ViewModelNotifier[T](self)

    def _ensure_notifier(self) -> None:
        """确保 notifier 已初始化（惰性）。

        兼容宿主 __init__ 中在 ``_init_mixin_fields()`` 之前对
        ``self._subscribers = []`` / ``self._main_loop = None`` 等字段的初始化赋值：
        此时 notifier 尚未创建，property 桥需先惰性建好再透传，避免 AttributeError。
        """
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 对外只读属性
    # ------------------------------------------------------------------
    @property
    def state(self) -> T:
        """View 只读 state snapshot, 不可变。"""
        return self._state

    # ==================================================================
    # 骨架方法：委托 notifier（不可被绕过）
    # ==================================================================

    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        return self._notifier.subscribe(callback)

    def _notify(self) -> None:
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        self._notifier.notify(self._state)

    def dispose(self) -> None:
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        self._notifier.dispose()

    def _get_loop_or_none(self) -> asyncio.AbstractEventLoop | None:
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        return self._notifier.get_loop_or_none()

    def _dispatch_notification_impl(
        self,
        subs_snap: list[Callable[[T], None]],
        state_snap: T,
        loop: asyncio.AbstractEventLoop,
        owner_tid: int,
    ) -> None:
        """统一跨线程调度实现（代理 notifier；测试/VM 可直接调用）。"""
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        self._notifier._dispatch_notification_impl(subs_snap, state_snap, loop, owner_tid)

    # ==================================================================
    # 内部实现（非扩展点，子类不 override；兼容宿主 _disposed 读取）
    # ==================================================================

    def _set_state(self, **changes: Any) -> None:
        """Update state fields (dataclasses.replace) and notify subscribers.

        统一 disposed guard；子类覆盖时应先调用 super()._set_state。
        """
        if not hasattr(self, "_notifier"):
            self._init_mixin_fields()
        if self._notifier.disposed:
            return
        self._state = replace(cast(Any, self._state), **changes)
        self._notify()
