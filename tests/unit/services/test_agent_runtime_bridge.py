"""Owner-loop 桥单元测试（HANDOFF §8.2 / DD §10.6，D-01）。

真实双事件循环线程验证跨 loop 提交契约（非 mock）：
- loop 登记：``for_running_loop`` 仅可在事件循环内调用
- 提交语义：owner 回调才创建实际 IO 任务（factory 在 owner 线程执行）；
  ``run_owner`` 经 ``run_coroutine_threadsafe`` 提交 + ``shield(wrap_future)``
  等候——waiter 取消只撤 waiter，owner 侧任务继续运行至完成
- owner 不阻塞：桥内不使用阻塞 ``future.result()``，IO 在途时 owner 循环
  仍可处理其他回调
- 强引用：桥持在途任务强引用（shield 文档：loop 仅弱引用任务）
- 稳定 operation：同 id 幂等（重复 start 不重复执行）；重复观察连接原任务；
  观察者取消只撤观察者；失败/取消如实传播；未登记 id 明确拒绝
- 进程内登记不代替持久 operation（HANDOFF §8.2 段 2，仅内存事实）
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine, Iterator
from typing import Any

import pytest

from services.agent_service.runtime import (
    OwnerLoopBridge,
    UnknownOperationError,
)

pytestmark = pytest.mark.unit


class _LoopThread:
    """独立事件循环线程（模拟 owner loop / DBOS loop）。

    loop 在构造线程创建（未运行），``run`` 内 ``run_forever`` 启动；调度一律经
    ``call_soon_threadsafe``，与创建线程无耦合。
    """

    def __init__(self, name: str) -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self.thread_id: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.thread_id = threading.get_ident()
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            finally:
                asyncio.set_event_loop(None)
                self.loop.close()

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(timeout=10), "loop thread failed to start"

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)

    def submit(self, coro: Coroutine[Any, Any, Any], timeout: float = 30.0) -> Any:
        """在自身循环上执行协程并同步等待结果。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """在自身循环上启动协程（不等待），返回 concurrent future。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


@pytest.fixture
def loops() -> Iterator[tuple[_LoopThread, _LoopThread]]:
    owner = _LoopThread("test-owner-loop")
    foreign = _LoopThread("test-foreign-loop")
    owner.start()
    foreign.start()
    yield owner, foreign
    foreign.stop()
    owner.stop()


def _make_bridge(owner_loop: asyncio.AbstractEventLoop) -> OwnerLoopBridge:
    return OwnerLoopBridge(owner_loop)


async def _make_bridge_in_loop() -> OwnerLoopBridge:
    return OwnerLoopBridge.for_running_loop()


async def _make_event() -> asyncio.Event:
    """在当前（owner）循环内创建事件（R11：循环绑定对象在所属循环创建）。"""
    return asyncio.Event()


class _BlockedWork:
    """owner 侧阻塞工厂：started → 等待 release → 返回结果并记录。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.finished = threading.Event()
        self.release: asyncio.Event | None = None
        self.outcomes: list[int] = []

    def bind(self, release: asyncio.Event) -> None:
        self.release = release

    async def __call__(self) -> int:
        self.started.set()
        assert self.release is not None
        await self.release.wait()
        self.outcomes.append(42)
        self.finished.set()
        return 42


async def _wait_thread_event(event: threading.Event, timeout: float = 10.0) -> None:
    ok = await asyncio.to_thread(event.wait, timeout)
    assert ok, f"thread event not set within {timeout}s"


# ---------------------------------------------------------------------------
# loop 登记（§8.2 段 1）
# ---------------------------------------------------------------------------


def test_for_running_loop_requires_event_loop() -> None:
    """非事件循环上下文调用 ``for_running_loop`` 明确拒绝。"""
    with pytest.raises(RuntimeError) as excinfo:
        OwnerLoopBridge.for_running_loop()
    assert "no running event loop" in str(excinfo.value)


# ---------------------------------------------------------------------------
# run_owner 提交语义（§8.2 段 1）
# ---------------------------------------------------------------------------


def test_run_owner_executes_in_owner_loop(loops: tuple[_LoopThread, _LoopThread]) -> None:
    """owner 回调才创建实际 IO 任务：factory 在 owner 线程/owner 循环内执行。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    seen: dict[str, Any] = {}

    async def factory() -> str:
        seen["loop_is_owner"] = asyncio.get_running_loop() is owner.loop
        seen["thread_id"] = threading.get_ident()
        return "done"

    result = foreign.submit(bridge.run_owner(factory))
    assert result == "done"
    assert seen["loop_is_owner"] is True
    assert seen["thread_id"] == owner.thread_id


def test_run_owner_waiter_cancel_does_not_cancel_owner_io(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """waiter 取消只撤 waiter：owner 侧任务继续运行至完成并记录结果（§8.2）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    release = owner.submit(_make_event())
    work = _BlockedWork()
    work.bind(release)

    async def scenario() -> None:
        waiter = asyncio.ensure_future(bridge.run_owner(work))
        await _wait_thread_event(work.started)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await waiter
        assert excinfo.type is asyncio.CancelledError

    foreign.submit(scenario())
    # waiter 已撤：释放 owner 侧阻塞，实际 IO 应继续完成
    owner.loop.call_soon_threadsafe(release.set)
    assert work.finished.wait(timeout=10), "owner IO did not complete after waiter cancel"
    assert work.outcomes == [42]


def test_run_owner_owner_loop_stays_responsive(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """owner 不阻塞：run_owner 在途时 owner 循环仍可处理其他回调（无阻塞 result()）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    release = owner.submit(_make_event())
    work = _BlockedWork()
    work.bind(release)
    processed = threading.Event()

    def owner_callback() -> None:
        processed.set()

    async def scenario() -> None:
        waiter = asyncio.ensure_future(bridge.run_owner(work))
        await _wait_thread_event(work.started)
        # IO 在途：owner 循环回调应立即得到处理（不等待 IO 完成）
        owner.loop.call_soon_threadsafe(owner_callback)
        await _wait_thread_event(processed)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await waiter
        assert excinfo.type is asyncio.CancelledError

    foreign.submit(scenario())
    owner.loop.call_soon_threadsafe(release.set)
    assert work.finished.wait(timeout=10)


def test_bridge_holds_strong_reference_to_in_flight_task(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """桥持在途任务强引用（shield 文档：loop 仅弱引用任务，未持引用任务可被 GC）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    release = owner.submit(_make_event())
    work = _BlockedWork()
    work.bind(release)

    in_flight_seen: list[int] = []

    async def scenario() -> None:
        waiter = asyncio.ensure_future(bridge.run_owner(work))
        await _wait_thread_event(work.started)
        in_flight_seen.append(len(bridge._tasks))
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await waiter
        assert excinfo.type is asyncio.CancelledError

    foreign.submit(scenario())
    owner.loop.call_soon_threadsafe(release.set)
    assert work.finished.wait(timeout=10)
    assert in_flight_seen == [1]
    owner.submit(_assert_tasks_empty(bridge))


async def _assert_tasks_empty(bridge: OwnerLoopBridge) -> None:
    assert len(bridge._tasks) == 0


# ---------------------------------------------------------------------------
# 稳定 operation 登记（§8.2 段 2）
# ---------------------------------------------------------------------------


def test_start_operation_is_idempotent(loops: tuple[_LoopThread, _LoopThread]) -> None:
    """同 id 重复 start 不重复执行：连接原任务（factory 调用一次）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    calls: list[int] = []

    async def factory() -> int:
        calls.append(1)
        await asyncio.sleep(0.05)
        return 7

    async def scenario() -> None:
        first = bridge.start_operation("op-1", factory)
        second = bridge.start_operation("op-1", factory)
        assert first.operation_id == "op-1"
        assert second.state == "in_flight"
        result = await bridge.observe_operation("op-1")
        assert result == 7
        settled = bridge.start_operation("op-1", factory)
        assert settled.state == "completed"
        # 已结算后重复观察仍返回原结果（登记保留完成事实）
        assert await bridge.observe_operation("op-1") == 7

    foreign.submit(scenario())
    owner.submit(_wait_calls_reach(calls, 1))
    assert calls == [1]


async def _wait_calls_reach(calls: list[int], count: int) -> None:
    for _ in range(200):
        if len(calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"factory call count did not reach {count}")


def test_observe_unknown_operation_raises(loops: tuple[_LoopThread, _LoopThread]) -> None:
    """未登记 id 明确拒绝（不静默等待）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)

    async def scenario() -> None:
        with pytest.raises(UnknownOperationError) as excinfo:
            await bridge.observe_operation("op-missing")
        assert excinfo.value.operation_id == "op-missing"

    foreign.submit(scenario())


def test_repeat_observers_connect_to_original_task(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """重复观察连接原任务；单观察者取消不影响其他观察者与 IO。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    release = owner.submit(_make_event())
    work = _BlockedWork()
    work.bind(release)

    async def scenario() -> None:
        bridge.start_operation("op-1", work)
        observer_a = asyncio.ensure_future(bridge.observe_operation("op-1"))
        observer_b = asyncio.ensure_future(bridge.observe_operation("op-1"))
        await _wait_thread_event(work.started)
        observer_a.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await observer_a
        assert excinfo.type is asyncio.CancelledError
        # 观察者 B 不受 A 取消影响，仍连接原任务取得结果
        owner.loop.call_soon_threadsafe(release.set)
        assert await observer_b == 42

    foreign.submit(scenario())
    assert work.finished.wait(timeout=10)
    assert work.outcomes == [42]


def test_failed_operation_propagates_exception(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """factory 失败：观察者如实收到原异常（不吞不换形，R2/R21）。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)

    async def failing() -> int:
        raise ValueError("boom")

    async def scenario() -> None:
        handle = bridge.start_operation("op-1", failing)
        with pytest.raises(ValueError) as excinfo:
            await bridge.observe_operation("op-1")
        assert str(excinfo.value) == "boom"
        assert handle.state == "in_flight"  # start 时快照；结算后登记内为 failed
        settled = bridge.start_operation("op-1", failing)
        assert settled.state == "failed"

    foreign.submit(scenario())


def test_cancelled_operation_notifies_observers(
    loops: tuple[_LoopThread, _LoopThread],
) -> None:
    """operation 任务被取消：观察者收到 CancelledError，登记状态为 cancelled。"""
    owner, foreign = loops
    bridge = _make_bridge(owner.loop)
    release = owner.submit(_make_event())
    work = _BlockedWork()
    work.bind(release)

    async def scenario() -> None:
        bridge.start_operation("op-1", work)
        observer = asyncio.ensure_future(bridge.observe_operation("op-1"))
        await _wait_thread_event(work.started)
        # 经 owner 循环取消原 operation 任务（Task.cancel 非线程安全，不跨线程直调）
        cancel_done = threading.Event()

        def do_cancel() -> None:
            for task in list(bridge._tasks):
                task.cancel()
            cancel_done.set()

        owner.loop.call_soon_threadsafe(do_cancel)
        await _wait_thread_event(cancel_done)
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await observer
        assert excinfo.type is asyncio.CancelledError
        settled = bridge.start_operation("op-1", work)
        assert settled.state == "cancelled"

    foreign.submit(scenario())
    owner.loop.call_soon_threadsafe(release.set)
    # 原 IO 已被取消：结果未产出（unknown/取消路径不伪造产出，R21）
    assert work.outcomes == []
