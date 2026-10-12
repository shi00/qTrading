"""R02 page.run_task 卸载归属验证测试（reviews/flet-review.md §5 R02 / §10.14）。

验证三类任务在「组件卸载 / 离页」时的退出行为判据：

1. **短 UI 请求（use_viewmodel 路径）**：慢 fake 命令在途时组件卸载，命令完成后
   VM._notify 不触达已卸载组件的 set_state（use_viewmodel cleanup 退订 + dispose），
   state 不回填、不调度重渲染。
2. **短 UI 请求（页面级协程闭包捕获 hook setter 路径）**：卸载后迟到结果直接调用
   set_state 安全——Flet 锁定版本 Component._schedule_update 对 unmounted 组件
   跳过 session 调度（flet/components/component.py「must not resurrect a zombie
   control」），不重渲染。
3. **长期业务任务（回测 / 同步）**：页面级调度协程以 fire-and-forget 启动（与生产
   page.run_task 不持有 Future 的调用方式一致），组件卸载不取消之；业务任务实际由
   TaskManager.submit_task 拥有（backtest_view_model.run_backtest /
   data_source_view_model.execute_full_daily_sync / execute_init_historical_data
   均经 submit_task 提交，cancel_task 仅由用户显式取消入口触发），离页不中断。

owner 归属结论（§10.14 停止条件判据）：
- 短请求：owner = 组件（卸载时经 use_viewmodel cleanup 失效；框架对 unmounted
  组件跳过调度兜底）；不持有 run_task Future 是安全的。
- 长任务：owner = TaskManager（页面只调度提交协程、不持有业务生命周期）。
- 会话任务（窗口关闭 / 退出升级）：owner = 应用生命周期，页面存活至会话结束，
  无离页语义。
- 定时器类（startup / task_center ticker / toast）：owner = 组件，显式持有
  Future（use_ref）并在 use_effect cleanup 取消。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    run_mount_effects,
    run_unmount_effects,
)
from ui.hooks import use_viewmodel

pytestmark = pytest.mark.unit


# ============================================================================
# 慢 fake ViewModel（短请求验证用：命令挂在 gate 上，可手动放行制造"迟到"）
# ============================================================================


@dataclass(frozen=True)
class _SlowState:
    value: int = 0


class SlowGateViewModel:
    """符合 VM 契约的慢命令 ViewModel（结构对齐 tests/unit/ui/test_hooks.py）。"""

    def __init__(self) -> None:
        self._state: Any = _SlowState()
        self._subscribers: list[Any] = []
        self.dispose_called = False
        self.command_started = asyncio.Event()
        self.release_gate = asyncio.Event()

    @property
    def state(self) -> Any:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        for cb in list(self._subscribers):
            cb(self._state)

    def set_value(self, new_value: int) -> None:
        self._state = replace(self._state, value=new_value)
        self._notify()

    async def slow_command(self, new_value: int) -> None:
        """慢 fake 命令：挂起至 release_gate 放行后提交结果（模拟慢 IO 请求）。"""
        self.command_started.set()
        await self.release_gate.wait()
        self.set_value(new_value)

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()


# ============================================================================
# 测试用组件
# ============================================================================


def _make_slow_vm() -> SlowGateViewModel:
    return SlowGateViewModel()


@ft.component
def ShortRequestComponent():
    """use_viewmodel 路径组件（生产短请求 View 的同构最小模型）。"""
    state, _vm = use_viewmodel(_make_slow_vm)
    return ft.Text(value=f"value={state.value}")


_SETTER_STORE: dict[str, Any] = {}


@ft.component
def DirectSetterComponent():
    """页面级协程闭包捕获 hook setter 的同构最小模型（data_view 模式）。"""
    value, set_value = ft.use_state(0)
    _SETTER_STORE["set_value"] = set_value
    return ft.Text(value=f"v={value}")


_LAUNCH_STORE: dict[str, Any] = {}


@ft.component
def FireAndForgetLaunchComponent():
    """mount 时以 fire-and-forget 启动长任务（模拟 page.run_task 不持有 Future）。

    组件不持有任务句柄（与生产短请求/长任务的 page.run_task 调用一致）；
    _LAUNCH_STORE 仅供测试观察，不构成生命周期 owner。
    """

    async def _long_business_task() -> None:
        try:
            await _LAUNCH_STORE["release"].wait()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        finally:
            _LAUNCH_STORE["finished"].set()

    _LAUNCH_STORE["task"] = asyncio.create_task(_long_business_task())
    return ft.Text(value="running")


# ============================================================================
# 测试用例
# ============================================================================


class TestShortRequestUnmountInvalidation:
    """短 UI 请求：卸载后迟到结果被失效（R02 判据 1）。"""

    async def test_late_result_not_backfilled_after_unmount(self) -> None:
        """慢请求在途时组件卸载，命令完成后的 _notify 不回填、不重渲染。

        对照段：挂载期同路径 notify 会更新 state 并调度重渲染（证明断言
        机制有效）；卸载后同路径 notify 被退订 + dispose 双重失效。
        """
        component = make_component(ShortRequestComponent)
        page = run_mount_effects(component)

        hooks: Any = component._state.hooks
        vm: SlowGateViewModel = hooks[0].ref.current
        state_hook: Any = hooks[1]
        assert state_hook.value.value == 0

        # 对照段：挂载期命令完成 → set_state 生效 + 调度重渲染
        vm.set_value(1)
        assert state_hook.value.value == 1
        updates_while_mounted = len(page.session.scheduled_updates)
        assert updates_while_mounted > 0

        # 慢请求在途
        command_task = asyncio.create_task(vm.slow_command(42))
        await asyncio.wait_for(vm.command_started.wait(), timeout=1)

        # 请求在途时组件卸载（切页卸载 / dispose 路径）
        run_unmount_effects(component)
        assert vm.dispose_called is True
        assert vm._subscribers == []

        # 迟到结果到达：命令完成并 _notify
        vm.release_gate.set()
        await asyncio.wait_for(command_task, timeout=1)

        # 迟到结果被失效：state 不回填为 42，无新增重渲染调度
        assert state_hook.value.value == 1
        assert len(page.session.scheduled_updates) == updates_while_mounted


class TestDirectSetterUnmountSafety:
    """短 UI 请求（闭包捕获 setter 路径）：卸载后调用 set_state 安全（R02 判据 2）。"""

    def test_late_set_state_after_unmount_does_not_reschedule(self) -> None:
        """卸载后迟到结果直接调用 hook setter：不抛异常、不调度重渲染。

        生产对应：页面级 run_task 协程闭包捕获组件内 use_state setter
        （如 data_view._do_show_health_report 的 set_health_report_data），
        Flet 对 unmounted 组件在 _schedule_update 入口跳过 session 调度。
        """
        _SETTER_STORE.clear()
        component = make_component(DirectSetterComponent)
        page = run_mount_effects(component)

        set_value = _SETTER_STORE["set_value"]
        state_hook: Any = component._state.hooks[0]

        # 对照段：挂载期 setter 生效并调度重渲染
        set_value(1)
        assert state_hook.value == 1
        updates_while_mounted = len(page.session.scheduled_updates)
        assert updates_while_mounted > 0

        # 卸载后迟到结果直接写 setter（闭包仍可达）
        run_unmount_effects(component)
        set_value(2)  # 不应抛异常

        # 不复活僵尸控件：无新增重渲染调度
        assert len(page.session.scheduled_updates) == updates_while_mounted


class TestLongTaskSurvivesUnmount:
    """长期业务任务：组件卸载不取消页面级调度协程（R02 判据 3）。"""

    async def test_fire_and_forget_task_not_cancelled_by_unmount(self) -> None:
        """fire-and-forget 长任务在组件卸载后继续运行至完成。

        生产对应：回测 / 同步经 page.run_task 调度的提交协程无人持有 Future、
        无 cleanup 取消；业务生命周期由 TaskManager.submit_task 拥有。本测试
        固化「卸载不得取消页面级任务」契约，防止未来误加全量任务取消。
        """
        _LAUNCH_STORE.clear()
        release = asyncio.Event()
        finished = asyncio.Event()
        _LAUNCH_STORE["release"] = release
        _LAUNCH_STORE["finished"] = finished

        component = make_component(FireAndForgetLaunchComponent)
        run_mount_effects(component)

        long_task: asyncio.Task[Any] = _LAUNCH_STORE["task"]
        # 短暂等待确认任务挂起（未完成）而非报错——任务在等 release 信号
        done, pending = await asyncio.wait({long_task}, timeout=0.2)
        assert long_task in pending and not done

        # 组件卸载：无任何 cleanup 路径取消页面级任务
        run_unmount_effects(component)

        # 业务完成信号放行 → 任务正常完成（未被取消）
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert long_task.cancelled() is False
        assert long_task.exception() is None
