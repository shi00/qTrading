"""Flet 私有 API 兼容性验证（spike；文件名中的 "0_86" 为历史命名）。

目的：固化项目深度依赖的 Flet 私有 API 契约。**文件名与历史记录中的 "0.86"
仅是 spike 命名**：本探针当前针对 ``pyproject.toml`` 锁定的 Flet 版本执行
（版本号从 ``pyproject.toml`` 读取，不在此硬编码）。任一断言失败即表示锁定
版本引入了破坏性变更，需记录到对应检视/升级报告并标记升级阻塞。

依赖点（项目内使用位置）：
- ``_context_page`` ContextVar: ``tests/unit/ui/conftest.py`` 的
  ``_reset_context_page`` autouse fixture、``tests/unit/ui/component_renderer.py``
  的 ``attach_fake_page``、多个 contract 测试的 ``test_uses_ft_context_page``
- ``Component, Renderer``: ``tests/unit/ui/component_renderer.py``、
  ``tests/unit/ui/test_system_tab_contract.py``
- ``ft.Control.page.fget``: ``tests/unit/ui/conftest.py`` 与
  ``tests/integration/conftest.py`` 的 V1 page 兼容桩（monkeypatch fget）
- ``Observable, ObservableList``: ``scripts/spike_ui_debt/spike_observable.py``

验证手段：
- 存在性/签名：``import`` + ``hasattr()`` + ``inspect.signature()``
- 行为：真实 ``Session`` 调度器驱动的 mount/re-render/unmount 时序验证
  （见 ``TestRealEffectScheduling`` 与 ``TestSyncSetupCleanupContrast``）
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect

import flet as ft
import pytest
from flet.controls.context import _context_page

from tests.unit.ui.component_renderer import make_component, render_once

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _v1_page_compat():
    """禁用 conftest 的 V1 page 兼容桩。

    本测试需要验证 ``ft.Control.page`` 的原始 property 与 fget 签名，
    conftest 的 ``_v1_page_compat`` autouse fixture 会 monkeypatch
    ``ft.Control.page``，必须在此禁用以访问原始属性。
    """
    yield


def test_context_page_is_contextvar() -> None:
    """``flet.controls.context._context_page`` 仍为 ContextVar。

    conftest 的 ``_reset_context_page`` fixture 与 component_renderer 的
    ``attach_fake_page`` 均依赖 ``_context_page.set/get``，若类型变更将破坏
    全部声明式 UI 测试。
    """
    from flet.controls.context import _context_page

    assert isinstance(_context_page, contextvars.ContextVar)
    # ContextVar 名称用于调试与日志，变更不影响行为但应记录
    assert _context_page.name == "flet_session_page"


def test_component_and_renderer_are_classes() -> None:
    """``flet.components.component.Component`` 与 ``Renderer`` 仍为类。

    component_renderer 的 ``attach_fake_page`` 通过 ``Renderer.render`` 驱动
    声明式组件渲染；``Component`` 是所有声明式组件的基类。
    """
    from flet.components.component import Component, Renderer

    assert inspect.isclass(Component)
    assert inspect.isclass(Renderer)
    # Renderer 必须保留 render 入口（component_renderer 调用链依赖）
    assert hasattr(Renderer, "render"), "Renderer.render 缺失将破坏声明式渲染"
    # Component 必须保留 before_update/did_mount 生命周期钩子
    assert hasattr(Component, "before_update")
    assert hasattr(Component, "did_mount")


def test_control_page_fget_accessible() -> None:
    """``ft.Control.page`` 仍为 property 且 ``fget`` 可访问。

    conftest 的 V1 page 兼容桩通过 ``ft.Control.page.fget`` 保存原始 getter
    并 monkeypatch 为可读写 property；若 fget 不可访问将破坏全部 UI 测试。
    """
    page_attr = getattr(ft.Control, "page", None)
    assert isinstance(page_attr, property), "ft.Control.page 不再是 property"
    fget = page_attr.fget
    assert callable(fget), "ft.Control.page.fget 不可调用"

    # fget 签名应接受 self（单参数）。类型注解可能是字符串前向引用，
    # 不断言精确返回类型字符串，仅断言参数结构。
    sig = inspect.signature(fget)
    params = list(sig.parameters.values())
    assert len(params) == 1, f"Control.page.fget 参数数变化: {params}"
    assert params[0].name == "self"


def test_pubsub_client_class_and_init_signature() -> None:
    """``flet.pubsub.pubsub_client.PubSubClient`` 类与 ``__init__`` 签名兼容。

     R.5.1 调研依赖 ``unsubscribe_topic`` 的 session-scoped 语义，必须确认
    该方法在 0.86.0 仍存在。``__init__`` 接收 ``pubsub`` 与 ``session_id``
     两个参数（项目 spike 脚本按此签名构造）。
    """
    from flet.pubsub.pubsub_client import PubSubClient

    assert inspect.isclass(PubSubClient)

    sig = inspect.signature(PubSubClient.__init__)
    params = list(sig.parameters.values())
    # self + pubsub + session_id
    param_names = [p.name for p in params]
    assert "self" in param_names
    assert "pubsub" in param_names, f"PubSubClient.__init__ 参数变化: {param_names}"
    assert "session_id" in param_names, f"PubSubClient.__init__ 参数变化: {param_names}"

    # R.5.1 关注的 unsubscribe_topic 必须存在
    assert hasattr(PubSubClient, "unsubscribe_topic"), "PubSubClient.unsubscribe_topic 缺失，R.5.1 调研前提失效"
    # 项目使用的其他 PubSub 方法
    for method in ("subscribe_topic", "send_all", "send_others", "send_all_on_topic"):
        assert hasattr(PubSubClient, method), f"PubSubClient.{method} 缺失"


def test_observable_and_observable_list_are_classes() -> None:
    """``flet.components.observable.Observable`` 与 ``ObservableList`` 仍为类。

    spike_observable.py 通过 ``Observable.subscribe/notify`` 与
    ``ObservableList`` 的 list 接口实现可观察集合；i18n/theme 状态驱动也
    依赖 Observable 基类。
    """
    from flet.components.observable import Observable, ObservableList

    assert inspect.isclass(Observable)
    assert inspect.isclass(ObservableList)

    # Observable 必须保留 subscribe/notify（观察者模式入口）
    assert hasattr(Observable, "subscribe")
    assert hasattr(Observable, "notify")

    # ObservableList 必须保留核心 list 接口（项目状态集合操作依赖）
    for method in ("append", "remove", "insert", "pop", "clear", "extend"):
        assert hasattr(ObservableList, method), f"ObservableList.{method} 缺失"


# ===========================================================================
# F09：真实 Flet Session/Renderer 调度时序测试组
#
# 背景：``tests/unit/ui/component_renderer.py`` 的 FakeSession 同步执行
# setup/cleanup，仅验证接线，不验证真实调度语义（F09 检视发现）。
#
# 本测试组用锁定版本的真实 ``flet.messaging.session.Session``（其
# ``__updates_scheduler`` 后台任务）驱动真实 mount/re-render/unmount，验证：
#   1. 显式 ``cleanup=`` 使卸载时调度 cleanup → 调度器 cleanup 分支
#      ``hook.cancel()`` 取消在途 async setup task；
#   2. 无 cleanup 时卸载不调度 cleanup，在途 async setup 不被取消；
#   3. 依赖变化时取消旧 setup 并启动新 setup；
#   4. ``dependencies=[]`` 的 effect 在 re-render 时不重跑；
#   5. 锁定版本对「async setup 返回的 cleanup」不捕获（须用显式 ``cleanup=``）。
#
# 私有框架调用（``Session`` / ``Component._state`` / ``EffectHook._setup_task``）
# 集中在本测试边界内，不泄漏到生产 View/VM（报告 §10.10 步骤 7）。
# 本模块 autouse 的 ``_v1_page_compat`` 覆盖禁用了 conftest 的 V1 page 兼容桩，
# 真实调度测试不加载旧行为 shim。
# ===========================================================================


class _NoopConnection:
    """满足 ``Session.__init__`` 的最小连接桩。

    提供 ``pubsubhub``（构造 ``PubSubClient``）、``send_message``（吞掉出站
    消息）、``dispose``（``disconnect()`` 调用）。
    """

    def __init__(self) -> None:
        from flet.pubsub.pubsub_hub import PubSubHub

        self.pubsubhub = PubSubHub()
        self.sent: list[object] = []

    def send_message(self, message: object) -> None:
        self.sent.append(message)

    def dispose(self) -> None:
        pass


@ft.component
def _AsyncGateEffect(gate: asyncio.Event, log: list[str], deps_value: int, with_cleanup: bool):
    """async setup 阻塞在 ``gate`` 上的探针组件（可控 await 挂起）。

    ``log`` 记录 setup 开始/结束与 cleanup 执行；``with_cleanup`` 决定是否
    传入显式 cleanup 函数（F09 的核心对照变量）。
    """

    async def _setup() -> None:
        log.append(f"setup_start:{deps_value}")
        await gate.wait()
        log.append(f"setup_done:{deps_value}")

    def _cleanup() -> None:
        log.append("cleanup")

    ft.use_effect(_setup, [deps_value], _cleanup if with_cleanup else None)
    return ft.Text("probe")


@ft.component
def _AsyncGateEffectMountOnly(gate: asyncio.Event, log: list[str]):
    """``dependencies=[]`` 的 async setup 探针（验证 re-render 不重跑）。"""

    async def _setup() -> None:
        log.append("setup_start")
        await gate.wait()
        log.append("setup_done")

    def _cleanup() -> None:
        log.append("cleanup")

    ft.use_effect(_setup, [], _cleanup)
    return ft.Text("probe")


@ft.component
def _AsyncSetupReturningCleanup(log: list[str]):
    """async setup 通过返回值提供 cleanup 的探针（版本限制探针）。"""

    async def _setup() -> object:
        log.append("setup")

        def _returned_cleanup() -> None:
            log.append("returned_cleanup")

        return _returned_cleanup

    ft.use_effect(_setup, [])
    return ft.Text("probe")


@ft.component
def _SyncExplicitCleanup(log: list[str], unrelated: int):
    """sync setup + 显式 ``cleanup=`` 探针（``unrelated`` 变化触发无关重渲染）。

    记录显式 ``cleanup=`` 在 mount → 无关重渲染 → unmount 全链路的清理结果。
    """

    def _setup() -> None:
        log.append("setup")

    def _cleanup() -> None:
        log.append("explicit_cleanup")

    ft.use_effect(_setup, [], _cleanup)
    return ft.Text(f"probe:{unrelated}")


@ft.component
def _SyncSetupReturningCleanup(log: list[str], unrelated: int):
    """sync setup 通过返回值提供 cleanup 的探针（对照显式 ``cleanup=``）。"""

    def _setup() -> object:
        log.append("setup")

        def _returned_cleanup() -> None:
            log.append("returned_cleanup")

        return _returned_cleanup

    ft.use_effect(_setup, [])
    return ft.Text(f"probe:{unrelated}")


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """轮询等待 predicate 成立（绑定当前事件循环），超时抛 AssertionError。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0)


async def _new_session():
    """构造真实 Session 并启动其 updates 调度器，绑定 ``_context_page``。"""
    from flet.messaging.session import Session

    session = Session(_NoopConnection())
    session.start_updates_scheduler()
    _context_page.set(session.page)
    return session


async def _stop_session(session, gate: asyncio.Event, *tasks) -> None:
    """释放 gate、取消调度器与残留任务并 await，避免测试残留后台工作。

    F10 修复：残留 task 可能挂在与 ``gate`` 无关的 Event 上（如探针 ticker
    的 ``held.wait()``），``gate.set()`` 释放不了它们——await 前必须先逐个
    cancel，否则测试在 finally 无限挂起直至 pytest-timeout 超时。
    """
    gate.set()
    sched = getattr(session, "_Session__updates_task", None)
    for task in (sched, *tasks):
        if task is not None and not task.done():
            task.cancel()
    for task in (sched, *tasks):
        if task is None:
            continue
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


class TestRealEffectScheduling:
    """真实 Session scheduler 驱动的 effect 生命周期时序验证（F09）。"""

    @pytest.mark.asyncio
    async def test_explicit_cleanup_cancels_inflight_async_setup_on_unmount(self) -> None:
        """显式 cleanup → 卸载取消在途 async setup（setup 不走到完成分支）。"""
        gate = asyncio.Event()
        log: list[str] = []
        session = await _new_session()
        tasks: list[asyncio.Task | None] = []
        try:
            component = make_component(_AsyncGateEffect, gate=gate, log=log, deps_value=1, with_cleanup=True)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            tasks.append(hook._setup_task)
            await _wait_until(lambda: "setup_start:1" in log and hook._setup_task is not None)

            component._state.mounted = False
            component._run_unmount_effects()
            await _wait_until(lambda: hook._setup_task.done())

            assert hook._setup_task.cancelled() is True
            assert "setup_done:1" not in log
        finally:
            await _stop_session(session, gate, *tasks)

    @pytest.mark.asyncio
    async def test_missing_cleanup_leaves_inflight_async_setup_running_on_unmount(self) -> None:
        """无 cleanup → 卸载不取消在途 async setup（复现 F09 未清理旧行为）。"""
        gate = asyncio.Event()
        log: list[str] = []
        session = await _new_session()
        tasks: list[asyncio.Task | None] = []
        try:
            component = make_component(_AsyncGateEffect, gate=gate, log=log, deps_value=1, with_cleanup=False)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            tasks.append(hook._setup_task)
            await _wait_until(lambda: "setup_start:1" in log and hook._setup_task is not None)

            component._state.mounted = False
            component._run_unmount_effects()
            # 给调度器若干轮机会：无 cleanup 时不应有任何取消发生
            for _ in range(3):
                await asyncio.sleep(0)

            assert hook._setup_task.done() is False
            assert "setup_done:1" not in log
        finally:
            await _stop_session(session, gate, *tasks)

    @pytest.mark.asyncio
    async def test_dependency_change_cancels_previous_setup_and_starts_new(self) -> None:
        """依赖变化 → 取消旧 setup 并启动新 setup。"""
        gate = asyncio.Event()
        log: list[str] = []
        session = await _new_session()
        tasks: list[asyncio.Task | None] = []
        try:
            component = make_component(_AsyncGateEffect, gate=gate, log=log, deps_value=1, with_cleanup=True)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            await _wait_until(lambda: "setup_start:1" in log)
            old_task = hook._setup_task
            tasks.append(old_task)

            component.kwargs = {"gate": gate, "log": log, "deps_value": 2, "with_cleanup": True}
            render_once(component)
            component._run_render_effects()
            await _wait_until(lambda: "setup_start:2" in log)
            tasks.append(hook._setup_task)

            await _wait_until(lambda: old_task.done())
            assert old_task.cancelled() is True
            assert hook._setup_task is not old_task
            assert "setup_done:2" not in log
        finally:
            await _stop_session(session, gate, *tasks)

    @pytest.mark.asyncio
    async def test_empty_dependencies_do_not_rerun_on_update(self) -> None:
        """``dependencies=[]`` 仅在挂载时运行，re-render 不重跑/不取消。"""
        gate = asyncio.Event()
        log: list[str] = []
        session = await _new_session()
        tasks: list[asyncio.Task | None] = []
        try:
            component = make_component(_AsyncGateEffectMountOnly, gate=gate, log=log)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            tasks.append(hook._setup_task)
            await _wait_until(lambda: "setup_start" in log and hook._setup_task is not None)
            first_task = hook._setup_task

            render_once(component)
            component._run_render_effects()
            for _ in range(3):
                await asyncio.sleep(0)

            assert hook._setup_task is first_task
            assert log == ["setup_start"]
        finally:
            await _stop_session(session, gate, *tasks)

    @pytest.mark.asyncio
    async def test_async_setup_returned_cleanup_is_not_captured(self) -> None:
        """版本限制探针：async setup 的返回 cleanup 在锁定版本不被捕获。

        调度器仅对「同步 setup 的返回可调用对象」赋值 ``hook.cleanup``；
        ``iscoroutinefunction(setup)`` 分支丢弃返回值。故 async 图表加载的
        cleanup 必须经显式 ``cleanup=`` 参数传入（F05 依赖此结论）。
        """
        log: list[str] = []
        gate = asyncio.Event()
        session = await _new_session()
        tasks: list[asyncio.Task | None] = []
        try:
            component = make_component(_AsyncSetupReturningCleanup, log=log)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            tasks.append(hook._setup_task)
            await _wait_until(lambda: hook._setup_task is not None)
            await _wait_until(lambda: hook._setup_task.done())

            assert hook.cleanup is None
            component._state.mounted = False
            component._run_unmount_effects()
            for _ in range(3):
                await asyncio.sleep(0)
            assert "returned_cleanup" not in log
        finally:
            await _stop_session(session, gate, *tasks)


class TestSyncSetupCleanupContrast:
    """mount → 无关重渲染 → unmount：显式 ``cleanup=`` 与 setup 返回值的清理结果对照（F12）。

    锁定版本源码决定两种写法的差异：
    - ``flet/components/hooks/use_effect.py`` 的 ``use_effect`` 每次渲染末尾执行
      ``hook.cleanup = cleanup``（以函数实参重写 hook 状态）；
    - ``flet/messaging/session.py`` 的调度器仅在执行 **同步** setup 后，将其返回
      的可调用对象暂存到 ``hook.cleanup``（``iscoroutinefunction`` 分支丢弃返回值）。

    因此：显式 ``cleanup=`` 每次渲染被重写为同一函数引用，卸载时可靠执行；setup 返回值
    在 mount 时被调度器暂存，任意后续（含无依赖变化、不触发 cleanup 重跑的 ``[]``）渲染
    都会被 ``use_effect`` 的 ``hook.cleanup = cleanup``（None）清空，卸载时静默跳过。
    """

    @pytest.mark.asyncio
    async def test_explicit_cleanup_runs_after_unrelated_rerender_and_unmount(self) -> None:
        """显式 cleanup=：无关重渲染后 hook.cleanup 保持，卸载时执行 cleanup。"""
        log: list[str] = []
        session = await _new_session()
        gate = asyncio.Event()
        try:
            component = make_component(_SyncExplicitCleanup, log=log, unrelated=1)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            await _wait_until(lambda: "setup" in log)
            assert callable(hook.cleanup)

            component.kwargs = {"log": log, "unrelated": 2}
            render_once(component)
            component._run_render_effects()
            assert callable(hook.cleanup)

            component._state.mounted = False
            component._run_unmount_effects()
            await _wait_until(lambda: "explicit_cleanup" in log)
            assert log.count("setup") == 1
        finally:
            await _stop_session(session, gate)

    @pytest.mark.asyncio
    async def test_setup_returned_cleanup_cleared_by_unrelated_rerender(self) -> None:
        """setup 返回值：无关重渲染清空 hook.cleanup，卸载时静默跳过 cleanup。"""
        log: list[str] = []
        session = await _new_session()
        gate = asyncio.Event()
        try:
            component = make_component(_SyncSetupReturningCleanup, log=log, unrelated=1)
            render_once(component)
            component._run_mount_effects()
            hook = component._state.hooks[0]
            await _wait_until(lambda: "setup" in log and callable(hook.cleanup))

            component.kwargs = {"log": log, "unrelated": 2}
            render_once(component)
            component._run_render_effects()
            assert hook.cleanup is None

            component._state.mounted = False
            component._run_unmount_effects()
            for _ in range(3):
                await asyncio.sleep(0)
            assert "returned_cleanup" not in log
        finally:
            await _stop_session(session, gate)


# ===========================================================================
# F10：真实调度器驱动的 active 门控 ticker 启停验证
#
# 生产实现（ui/views/task_center_view.py 的 _setup_ticker/_cleanup_ticker）结构：
#   - effect deps=[active, has_running]，任一变化经统一 cleanup 取消旧 ticker；
#   - setup 守卫仅 ``active and has_running`` 时创建 ticker；
#   - cleanup 显式 cancel 并置 None（切离页面/任务归零/卸载同一路径）。
#
# 探针 _ActiveGatedTicker 完全复刻该结构；ticker 循环体改 await 可控 ``held``
# Event（真实 Page.run_task 依赖 session.connection.loop，探针环境不可用，
# 故以 loop.create_task 等价承载调度语义）。真实每秒 tick 语义由
# test_task_center_view 的 _fake_sleep 模式覆盖，此处验证调度启停时序。
# ===========================================================================


@ft.component
def _ActiveGatedTicker(active: bool, has_running: bool, log: list[str], tasks: list[asyncio.Task], held: asyncio.Event):
    """F10 探针：复刻任务中心 ticker 的 active 门控 effect 结构。"""

    ticker_ref = ft.use_ref(None)

    def _setup() -> None:
        if not (active and has_running):
            return

        async def _tick() -> None:
            try:
                while True:
                    await held.wait()  # 可控挂起点，永不 set；取消经 CancelledError 传播
            except asyncio.CancelledError:
                raise  # R2: 必须传播

        task = asyncio.get_running_loop().create_task(_tick())
        ticker_ref.current = task
        tasks.append(task)
        log.append("start")

    def _cleanup() -> None:
        if ticker_ref.current is not None:
            ticker_ref.current.cancel()
            ticker_ref.current = None
            log.append("cancel")

    ft.use_effect(_setup, [active, has_running], _cleanup)
    return ft.Text("probe")


class TestActiveGatedTickerScheduling:
    """真实 Session scheduler 下 active 门控 ticker 的启停时序（F10）。"""

    @staticmethod
    def _kwargs(active: bool, has_running: bool, log: list[str], tasks: list[asyncio.Task], held: asyncio.Event):
        return {"active": active, "has_running": has_running, "log": log, "tasks": tasks, "held": held}

    async def _drive(self, component, **kwargs) -> None:
        """以新 props 重渲染并触发 render effects（deps 变化经真实调度器执行）。"""
        component.kwargs = kwargs
        render_once(component)
        component._run_render_effects()

    @pytest.mark.asyncio
    async def test_active_toggle_stops_and_restarts_ticker(self) -> None:
        """运行中切离页面取消 ticker，切回只重建一个新 ticker。"""
        log: list[str] = []
        tasks: list[asyncio.Task] = []
        held = asyncio.Event()
        stop_gate = asyncio.Event()
        session = await _new_session()
        try:
            component = make_component(_ActiveGatedTicker, **self._kwargs(True, True, log, tasks, held))
            render_once(component)
            component._run_mount_effects()
            await _wait_until(lambda: log.count("start") == 1)
            assert len(tasks) == 1
            old_task = tasks[0]

            await self._drive(component, **self._kwargs(False, True, log, tasks, held))
            await _wait_until(lambda: log.count("cancel") == 1)
            await _wait_until(lambda: old_task.cancelled())

            await self._drive(component, **self._kwargs(True, True, log, tasks, held))
            await _wait_until(lambda: log.count("start") == 2)
            assert log.count("cancel") == 1
            assert len(tasks) == 2
            assert tasks[1] is not old_task
            assert not tasks[1].done()
        finally:
            await _stop_session(session, stop_gate, *tasks)

    @pytest.mark.asyncio
    async def test_inactive_running_flip_does_not_spawn_ticker(self) -> None:
        """页面 inactive 下 has_running 抖动（0→1→0）不创建任何 ticker。"""
        log: list[str] = []
        tasks: list[asyncio.Task] = []
        held = asyncio.Event()
        stop_gate = asyncio.Event()
        session = await _new_session()
        try:
            component = make_component(_ActiveGatedTicker, **self._kwargs(False, False, log, tasks, held))
            render_once(component)
            component._run_mount_effects()
            await self._drive(component, **self._kwargs(False, True, log, tasks, held))
            await self._drive(component, **self._kwargs(False, False, log, tasks, held))
            for _ in range(5):
                await asyncio.sleep(0)
            assert log == [], "inactive 期间不应有任何 ticker 创建"
            assert tasks == []
        finally:
            await _stop_session(session, stop_gate, *tasks)

    @pytest.mark.asyncio
    async def test_unmount_cancels_running_ticker(self) -> None:
        """卸载组件时 cleanup 取消在跑 ticker，无遗留循环。"""
        log: list[str] = []
        tasks: list[asyncio.Task] = []
        held = asyncio.Event()
        stop_gate = asyncio.Event()
        session = await _new_session()
        try:
            component = make_component(_ActiveGatedTicker, **self._kwargs(True, True, log, tasks, held))
            render_once(component)
            component._run_mount_effects()
            await _wait_until(lambda: len(tasks) == 1)
            ticker_task = tasks[0]

            component._state.mounted = False
            component._run_unmount_effects()
            await _wait_until(lambda: ticker_task.cancelled())
            assert log.count("cancel") == 1
        finally:
            await _stop_session(session, stop_gate, *tasks)

    @pytest.mark.asyncio
    async def test_repeated_cycles_spawn_one_ticker_per_activation(self) -> None:
        """反复往返切页（True→False→True→False→True），每个 active 周期只建一个 ticker。"""
        log: list[str] = []
        tasks: list[asyncio.Task] = []
        held = asyncio.Event()
        stop_gate = asyncio.Event()
        session = await _new_session()
        try:
            component = make_component(_ActiveGatedTicker, **self._kwargs(True, True, log, tasks, held))
            render_once(component)
            component._run_mount_effects()
            await _wait_until(lambda: log.count("start") == 1)
            for _ in range(2):
                await self._drive(component, **self._kwargs(False, True, log, tasks, held))
                await _wait_until(lambda: log[-1] == "cancel")
                await self._drive(component, **self._kwargs(True, True, log, tasks, held))
                await _wait_until(lambda: log.count("start") == log.count("cancel") + 1)
            assert log.count("start") == 3
            assert log.count("cancel") == 2
            assert len(tasks) == 3
            assert not tasks[-1].done()
        finally:
            await _stop_session(session, stop_gate, *tasks)
