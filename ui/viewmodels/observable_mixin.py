"""ObservableViewModelMixin — 跨线程安全的 VM 可观察协议默认实现 (P2 Segment 2)。

架构约束（来自对抗性评审三维度吸收）：
- 模板方法模式：_notify / dispose / subscribe 为终态骨架方法，子类仅覆盖扩展点
  (_dispatch_notification / _do_notify / _invoke_single_subscriber / _get_loop_or_none)
- HomeVM/DataExplorerVM 禁止再 override _notify（整个跨线程修复不能被绕过）
- dispose 三步原子关闭协议：shutdown_lock 临界区内翻转 _disposed + 取快照，锁外做 cancel
- loop 用强引用（与 5 个 VM 现有代码风格一致，不使用 weakref 引入 NPE 风险）
- _owner_tid 在每次 subscribe 时刷新绑定到最新 subscribe 线程（修正 Skeptic P1 #8）
- _pending_notifications deque 带 maxlen=1000（修正 Skeptic P1 #7）
- _do_notify 默认 per-cb try/except 异常隔离（修正 Skeptic P1 #4）
- closure 内通过默认参数绑定 loop 强引用，不二次解引用（修正 Skeptic P1 #1）
- fire_and_wrap 内每 cb 前再查 _disposed，修正 P0 #3 dispose flag 竞态绕过
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_MAX_PENDING = 1000  # deque 上限，无 loop 场景下 OOM 防护


class ObservableViewModelMixin[T]:
    """ViewModel 可观察协议默认实现 mixin。

    子类需在 __init__ 中初始化：
        - self._state: T            (frozen dataclass)
        - self._subscribers: list[Callable[[T], None]]

    终态骨架方法（子类禁止 override，否则跨线程修复对该子类被绕过）：
        - subscribe(callback) -> unsub
        - _notify()
        - dispose()

    扩展点（子类按需 override）：
        - _invoke_single_subscriber(cb, snap)  ->  per-cb try/except 策略
        - _dispatch_notification(subs_snap, state_snap)  ->  调度策略
        - _do_notify(subs_snap, state_snap)  ->  遍历+调用策略
        - _get_loop_or_none()  ->  lazy loop capture
    """

    _state: T
    _subscribers: list[Callable[[T], None]]
    _subscribers_lock: threading.RLock
    _shutdown_lock: threading.Lock
    _main_loop: asyncio.AbstractEventLoop | None
    _owner_tid: int
    _disposed: bool
    _pending_notify_handle: object | None
    _pending_notifications: deque

    # ------------------------------------------------------------------
    # 初始化辅助：子类 __init__ 末尾调用本方法确保 mixin 字段就绪
    # ------------------------------------------------------------------
    def _init_mixin_fields(self) -> None:
        """初始化 Mixin 所需的实例字段（子类 __init__ 末尾必须调用）。

        采用显式 init 方法而非 __init__ 自动调用，避免侵入子类现有构造签名
        （多继承中 super().__init__ 传参复杂，手动调用更清晰可控）。
        """
        if not hasattr(self, "_subscribers_lock"):
            self._subscribers_lock = threading.RLock()
        if not hasattr(self, "_shutdown_lock"):
            self._shutdown_lock = threading.Lock()
        self._main_loop = getattr(self, "_main_loop", None) or None
        self._owner_tid = getattr(self, "_owner_tid", -1)
        self._disposed = bool(getattr(self, "_disposed", False))
        self._pending_notify_handle = None
        # deque maxlen: 满时自动丢弃左端，OOM 防护（Skeptic P1 #7）
        if not hasattr(self, "_pending_notifications"):
            self._pending_notifications = deque(maxlen=_MAX_PENDING)
        elif getattr(self._pending_notifications, "maxlen", None) != _MAX_PENDING:
            self._pending_notifications = deque(self._pending_notifications, maxlen=_MAX_PENDING)

    # ------------------------------------------------------------------
    # 对外只读属性
    # ------------------------------------------------------------------
    @property
    def state(self) -> T:
        """View 只读 state snapshot, 不可变。"""
        return self._state

    # ==================================================================
    # 终态骨架方法：子类禁止 override（跨线程修复不可被绕过）
    # ==================================================================

    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。

        每次 subscribe 都刷新 _owner_tid + 尝试重新捕获 loop，修正 Skeptic P1 #8：
        跨线程 subscribe 时，绑定到最新 subscribe 线程（通常是 UI 线程），
        避免陈旧的 _owner_tid 导致 cross_thread 误判。
        捕获成功则立即 flush 堆积的 _pending_notifications。

        退订函数是线程安全的，可在 subscriber callback 内部递归调用。
        """
        # 先初始化字段（兼容还没调用 _init_mixin_fields 的子类）
        if not hasattr(self, "_shutdown_lock"):
            self._init_mixin_fields()

        with self._subscribers_lock:
            self._subscribers.append(callback)

        # 每次 subscribe 刷新绑定（Skeptic P1 #8 修复）
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

        # 捕获成功（无论是首次还是刷新）后尝试 flush 堆积的通知
        pending_to_flush: deque | None = None
        if self._main_loop is not None and self._pending_notifications:
            with self._subscribers_lock:
                if self._pending_notifications:
                    # 一次性取走所有 pending，新 append 进入新 deque（QA B-2 竞态修复）
                    pending_to_flush = self._pending_notifications
                    self._pending_notifications = deque(maxlen=_MAX_PENDING)

        if pending_to_flush is not None and captured_first_time:
            # 仅首次捕获时在当前线程同步 flush；刷新时不 flush（避免与当前 notify 乱序）
            for subs_snap, state_snap in pending_to_flush:
                self._dispatch_notification(subs_snap, state_snap)

        def _unsubscribe() -> None:
            # RLock：允许在 callback 内递归 unsubscribe（架构评审 P1-4）
            with self._subscribers_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        """state 变化后通知所有订阅者（终态骨架，子类禁止 override）。

        跨线程调度委托给 _dispatch_notification 扩展点处理。
        在 shutdown 临界区外读取 disposed/loop/subs，避免持锁做重操作。
        """
        # 早短路：disposed 标记可见性依赖 Python object model 的 writes visibility
        if getattr(self, "_disposed", False):
            return

        # 若字段尚未 init（极端时序）
        if not hasattr(self, "_shutdown_lock"):
            self._init_mixin_fields()

        # 一致快照（Skeptic 漏洞 10 修复 + QA 强断言要求）
        with self._shutdown_lock:
            if self._disposed:
                return
            # 读 disposed 与 loop/subs 在同一个临界区（架构 P0-2 TOCTOU 修复）
            with self._subscribers_lock:
                subs_snapshot: list[Callable[[T], None]] = list(self._subscribers)
            state_snapshot: T = self._state
            loop_captured = self._main_loop
            owner_tid_captured = self._owner_tid

        if not subs_snapshot:
            return

        # 无 loop 时的策略：
        #   - owner_tid 仍为默认值（未 subscribe）：subscribe 还未被调用，
        #     可能后续 subscribe 会捕获 loop → 入 _pending_notifications，
        #     subscribe 首次捕获 loop 时会 drain（flushed to drain）。
        #   - owner_tid 已被 subscribe 设定但 loop 仍为 None：subscribe 已
        #     发生但当前处于纯同步上下文（sync tests / 无 loop 环境），
        #     继续入队会导致永远不 drain。→ 直接同步调用 _do_notify。
        #     （修复：同步模式下通知 0 次的失败断言。）
        if loop_captured is None:
            if owner_tid_captured in (None, -1):
                self._buffer_notification(subs_snapshot, state_snapshot)
            else:
                self._do_notify(subs_snapshot, state_snapshot)
            return

        # 扩展点：调度（默认 = 跨线程封送 + 合并节流）
        self._dispatch_notification_impl(subs_snapshot, state_snapshot, loop_captured, owner_tid_captured)

    def dispose(self) -> None:
        """清理资源（终态骨架方法，三步原子关闭协议，架构 P0-2 修复）。

        顺序：
          1. shutdown_lock 临界区：_disposed=True，取 subs/pending 快照，清 subs/deque，断 loop
          2. 锁外：cancel pending notify handle（避免持锁跨线程死锁）
          3. 调用扩展点 _on_after_dispose(subs_snapshot, captured_loop)

        注意：dispose 是幂等的——子类可能先置 _disposed=True（业务短路）再调
        super().dispose()。因此不得在 disposed=True 时早返跳过清理。
        """
        if not hasattr(self, "_shutdown_lock"):
            self._init_mixin_fields()

        # === 锁内：原子翻转 + 快照 = 消除 TOCTOU（架构 P0-2）===
        # 不早返：即使子类已将 _disposed 置 True，仍需完成清理（因为 cleanup
        # 的所有基础操作都是幂等的：clear、cancel、置 None、置 True）。
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

        # === 锁外：重操作 / 跨线程 cancel ===
        if captured_handle is not None and hasattr(captured_handle, "cancel") and not isinstance(captured_handle, bool):
            try:
                captured_handle.cancel()
            except Exception:
                # cancel 失败静默处理：已执行中 cancel 返回 False 时抛错不影响关闭
                # 加 debug 日志以便诊断（P3-M12-008）
                logger.debug("[ObservableMixin] dispose cancel handle failed", exc_info=True)

        # 让子类有机会在 dispose 末尾做额外清理（保留语义兼容）
        self._on_after_dispose(subs_snapshot, pending_snapshot, captured_loop)

    # ==================================================================
    # 扩展点：子类按需 override
    # ==================================================================

    def _invoke_single_subscriber(self, cb: Callable[[T], None], snap: T) -> None:
        """Per-cb 调用策略（扩展点）。

        Mixin 默认：try/except 包裹（Skeptic P1 #4 异常隔离修复）。
        HomeVM / DataExplorerVM 覆盖本方法以注入 VM-specific logging。
        """
        try:
            cb(snap)
        except Exception as e:  # noqa: BLE001  —  intentionally broad catch
            logger.error(
                "[ObservableMixin] subscriber callback failed: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    def _do_notify(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        """遍历策略（扩展点）。默认：逐个调用 + 每 cb 前 disposed 二次检查。

        fire_and_wrap 执行期间 dispose 可能发生，所以每个 cb 前都要重新检查
        disposed flag（Skeptic P0 #3 修复：防止 disposed flag 竞态被绕过）。
        """
        for cb in subs_snap:
            if getattr(self, "_disposed", False):
                return
            self._invoke_single_subscriber(cb, state_snap)

    def _dispatch_notification(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        """调度策略（扩展点）。默认走统一实现：跨线程判定 + 合并节流。

        直接暴露给外部的简单封装：从对象当前状态读 loop/owner_tid。
        _notify 骨架方法走的是带参数的 _dispatch_notification_impl，
        因为它已经在 shutdown 临界区内拿到了一致快照。
        """
        loop = getattr(self, "_main_loop", None)
        owner_tid = getattr(self, "_owner_tid", -1)
        if loop is None:
            # 与 _notify 中逻辑一致：owner_tid 已 set 则同步直调（纯同步环境），
            # 否则 buffer（等待 subscribe 捕获 loop）。
            if owner_tid in (None, -1):
                self._buffer_notification(subs_snap, state_snap)
            else:
                self._do_notify(subs_snap, state_snap)
            return
        self._dispatch_notification_impl(subs_snap, state_snap, loop, owner_tid)

    def _get_loop_or_none(self) -> asyncio.AbstractEventLoop | None:
        """Lazy loop capture getter（扩展点）。

        ScreenerVM persist_splitter_width / _on_ai_result_stream 等
        需要自行创建 task 的场景，统一通过本方法获取 loop，
        不要各自散写 fallback（架构 P1-2 修复）。
        """
        if getattr(self, "_disposed", False):
            return None
        maybe_loop = getattr(self, "_main_loop", None)
        # isinstance 防御（单测中可能手动赋值 string/fake：T11）
        if isinstance(maybe_loop, asyncio.AbstractEventLoop):
            if maybe_loop.is_running():
                return maybe_loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        # Lazy capture 成功则缓存到对象（下次直接命中）
        if getattr(self, "_main_loop", None) is None and not getattr(self, "_disposed", False):
            with self._shutdown_lock:
                if self._main_loop is None and not self._disposed:
                    self._main_loop = loop
                    self._owner_tid = threading.get_ident()
                    # flush 堆积的 pending（若有）
                    if self._pending_notifications:
                        with self._subscribers_lock:
                            pending = self._pending_notifications
                            self._pending_notifications = deque(maxlen=_MAX_PENDING)
                        for s, st in pending:
                            self._dispatch_notification_impl(s, st, loop, self._owner_tid)
        return loop

    # ==================================================================
    # 内部实现（非扩展点，子类不 override）
    # ==================================================================

    def _buffer_notification(self, subs_snap: list[Callable[[T], None]], state_snap: T) -> None:
        """无 loop 时入 _pending_notifications deque（带 maxlen OOM 防护）。"""
        if not hasattr(self, "_pending_notifications"):
            self._init_mixin_fields()
        # deque(maxlen) 已满时自动丢弃最左；临近满时 warning 一次
        dq = self._pending_notifications
        if len(dq) == _MAX_PENDING - 1:
            logger.warning(
                "[ObservableMixin] pending notifications near cap %d; oldest will be dropped",
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
        """统一跨线程调度实现（非扩展点）。

        - 同线程 fast-path：直接调用 _do_notify，零调度开销
        - 跨线程：loop.call_soon_threadsafe + 合并节流（Skeptic 漏洞 6）
          合并节流只保留最新 snapshot，UI 永远渲染最终态（语义正确）
        - closure 通过默认参数绑定 loop 强引用，不二次解引用（Skeptic P1 #1）
        - fire_and_wrap 入口 disposed 二次检查 + 每 cb 前 disposed 再检查
        """
        cross_thread = owner_tid != -1 and threading.get_ident() != owner_tid and loop.is_running()

        if not cross_thread:
            # 同线程：直接同步调用
            self._do_notify(subs_snap, state_snap)
            return

        # ---- 跨线程：call_soon_threadsafe + 合并节流 ----
        # closure 默认参数绑定强引用（Skeptic P1 #1 修复）
        def fire_and_wrap(
            subs_closure: list[Callable[[T], None]] = subs_snap,
            state_closure: T = state_snap,
            loop_closure: asyncio.AbstractEventLoop = loop,
        ) -> None:
            # 入口 disposed 二次检查（Skeptic P0 #3）
            if getattr(self, "_disposed", False):
                return
            # 清理 handle（只清理自己持有的 handle，避免覆盖新创建的 handle）
            # 注意：此处不将 self._pending_notify_handle 置 None，
            # 因为可能有更新的 handle 已经被赋值；采用 identity 比对精确清理
            cur_handle = getattr(self, "_pending_notify_handle", None)
            if cur_handle is handle_ref_holder.get("handle"):
                self._pending_notify_handle = None
            self._do_notify(subs_closure, state_closure)

        # 用可变容器跨闭包共享 handle 引用（避免 chicken-egg 问题）
        handle_ref_holder: dict[str, Any] = {"handle": None}

        # 合并节流：取消前一个未执行的 handle（Skeptic 漏洞 6）
        old_handle = self._pending_notify_handle
        if old_handle is not None and hasattr(old_handle, "cancel") and not isinstance(old_handle, bool):
            try:
                old_handle.cancel()
            except Exception:
                # 加 debug 日志以便诊断（P3-M12-008）
                logger.debug("[ObservableMixin] throttle cancel old handle failed", exc_info=True)

        try:
            handle = loop.call_soon_threadsafe(fire_and_wrap)
        except RuntimeError:
            # QA B-4 / C2 修复：loop 已关闭时静默降级（不向外抛）
            # 可能 call_soon_threadsafe 内部创建了 handle 又失败，稳妥清空引用
            self._pending_notify_handle = None
            return

        handle_ref_holder["handle"] = handle
        self._pending_notify_handle = handle

    def _set_state(self, **changes: Any) -> None:
        """Update state fields (dataclasses.replace) and notify subscribers.

        统一 disposed guard；子类覆盖时应先调用 super()._set_state。
        """
        if getattr(self, "_disposed", False):
            return
        # 若字段未初始化（极端时序）
        if not hasattr(self, "_shutdown_lock"):
            self._init_mixin_fields()
        self._state = replace(cast(Any, self._state), **changes)
        self._notify()

    def _on_after_dispose(
        self,
        subs_snapshot: list[Callable[[T], None]],
        pending_snapshot: deque,
        captured_loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        """子类扩展点：dispose 锁外清理钩子（默认 no-op）。

        子类若需清理自身资源（background tasks / futures / service listeners），
        override 本方法或在自己的 dispose() 开头/末尾调用 super().dispose()。
        """
