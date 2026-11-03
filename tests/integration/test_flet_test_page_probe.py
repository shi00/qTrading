"""flet_test_page fixture 契约探针（Task 2.5.3 DoD 验证）。

验证：
1. flet_test_page fixture 可用（启动 Flet app 并返回 page）
2. wait_for_render 正常路径：page.add 后 wait_for_render 返回
3. wait_for_render 超时路径：expected_controls 不可达时抛 TimeoutError

``no_db`` marker: 跳过 ``db_schema_ready`` autouse fixture 的 DB 初始化，
让 probe 测试在 DB 不可用环境也能运行（方案 §3.3.3 DoD）。

运行环境限制（双重 skip）：

1. **Windows skip**: ``ft.run_async`` 的 socket server 不兼容
   ``WindowsSelectorEventLoop``（抛 ``NotImplementedError``），而 pytest-asyncio
   在 Windows 强制 selector policy（``tests/conftest.py`` L25）。

2. **Headless Linux skip**: ``ft.run_async`` 内部调用 ``is_linux_server()``
   检测 ``DISPLAY`` 环境变量——CI ubuntu-latest headless 环境下返回 True，
   强制 ``view=AppView.WEB_BROWSER``（flet app.py L188-190），启动 web server
   等待浏览器连接。无浏览器时 main 回调永不触发，fixture 挂起 120s 超时失败。

   因此 probe 测试在 CI headless Linux 下也 skip。本地 Linux 有 X server
   或用 ``xvfb-run`` 时 ``DISPLAY`` 已设置，可正常运行。本地 Windows 验证
   请用独立 spike：``python -m tests.integration._spike_flet_run_async``。

技术债：CI 完整验证 flet_test_page 需装 ``xvfb`` + ``flet_desktop``（见
``phase-2.5-review.md`` 限制章节）。

D15-F07 扩展（文件末尾区块）：在真实 Flet（锁定版本）session 调度器上观测
``use_viewmodel`` 的 mount → 状态通知 → reconcile → VM 替换 → unmount 序列，
断言订阅归属 / cleanup 次数 / 卸载后迟到通知不再更新；上列 helper 契约用窄
测试（``tests/unit/ui/test_flet_test_page_helpers.py``）保持不变。
"""

import asyncio
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft
import pytest

from ui.hooks import use_viewmodel

# Headless Linux 判定（与 flet.utils.is_linux_server 一致：Linux + 非 WSL + DISPLAY 未设置）
# CI ubuntu-latest 符合此条件；本地 Linux 有 X server 时 DISPLAY 已设置
_IS_HEADLESS_LINUX = sys.platform == "linux" and not os.environ.get("DISPLAY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.no_db,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="ft.run_async socket server 不兼容 WindowsSelectorEventLoop（pytest-asyncio 强制 selector policy）",
    ),
    pytest.mark.skipif(
        _IS_HEADLESS_LINUX,
        reason="ft.run_async 在 headless Linux 被 is_linux_server() 强制切到 WEB_BROWSER，无浏览器则 main 不触发；需 xvfb + flet_desktop（技术债）",
    ),
]


async def test_flet_test_page_available(flet_test_page):
    """DoD 1: flet_test_page fixture 可用。"""
    assert flet_test_page is not None
    assert isinstance(flet_test_page.page, ft.Page)


async def test_wait_for_render_returns_on_control_added(flet_test_page):
    """DoD 2: wait_for_render 正常路径——page.add 后返回。"""
    page = flet_test_page.page
    initial = len(page.controls)
    page.add(ft.Text("probe"))
    flet_test_page.wait_for_render(timeout=2.0, expected_controls=initial + 1)
    assert len(page.controls) == initial + 1


async def test_wait_for_render_raises_timeout(flet_test_page):
    """DoD 3: wait_for_render 超时抛 TimeoutError。"""
    page = flet_test_page.page
    # 不 add 任何控件，期望 controls 数量永远不变
    unreachable_target = len(page.controls) + 100
    with pytest.raises(TimeoutError):
        flet_test_page.wait_for_render(timeout=0.3, expected_controls=unreachable_target)


# ============================================================================
# Phase 3.0.1 扩展：wait_for_condition / find_control
# ============================================================================


async def test_wait_for_condition_returns_when_true(flet_test_page):
    """DoD 4: wait_for_condition 正常路径——predicate 返回 True 时立即返回。"""
    page = flet_test_page.page
    page.add(ft.Text("condition-probe"))
    # predicate 检查控件内容已渲染
    flet_test_page.wait_for_condition(
        predicate=lambda: any(getattr(c, "value", None) == "condition-probe" for c in page.controls),
        timeout=2.0,
    )


async def test_wait_for_condition_raises_timeout(flet_test_page):
    """DoD 5: wait_for_condition 超时抛 TimeoutError。"""
    # predicate 永远返回 False
    with pytest.raises(TimeoutError):
        flet_test_page.wait_for_condition(predicate=lambda: False, timeout=0.3)


async def test_find_control_returns_matching_control(flet_test_page):
    """DoD 6: find_control 深度优先查找满足谓词的控件。"""
    page = flet_test_page.page
    target_text = ft.Text("find-me", key="target")
    container = ft.Container(content=ft.Column([ft.Text("other"), target_text]))
    page.add(container)
    # 深度优先：page → Container → Column → Text("other") / Text("find-me")
    found = flet_test_page.find_control(
        predicate=lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "find-me"
    )
    assert found is target_text


async def test_find_control_returns_none_when_not_found(flet_test_page):
    """DoD 7: find_control 未找到时返回 None。"""
    page = flet_test_page.page
    page.add(ft.Text("exists"))
    found = flet_test_page.find_control(
        predicate=lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "nonexistent"
    )
    assert found is None


# ============================================================================
# D15-F07：真实 Flet 调度下的 use_viewmodel 生命周期观测
# ============================================================================
#
# 背景：``tests/unit/ui/test_hooks.py`` 的 ``FakeSession.schedule_effect`` 同步执行
# effect，只能证明 hook 的窄契约（订阅/dispose 被调用）；不能证明真实 Flet
# 异步调度器下 mount → 状态通知 → reconcile → VM 替换 → unmount 序列等价。
# 本组用例复用 ``flet_test_page``（真实 Flet，锁定版本）在 session 调度器上产生该
# 序列，直接观测三项：
#   ① 订阅归属：组件实例挂载后对应 VM 恰有 1 个订阅，且订阅数不随 reconcile 漂移；
#   ② cleanup 次数：卸载后 effect cleanup 恰执行 1 次（内部模式另 dispose 1 次）；
#   ③ 卸载后迟到通知不再更新：捕获卸载前的订阅回调并「迟到投递」，组件不重渲染
#      （Flet ``mounted`` 守卫），VM 自身通知也不再投递（退订生效）。
#
# 观测操作经 ``page.run_task`` 执行：``Component._schedule_effect`` 依赖
# ``ft.context.page``，而该 context 仅在 Flet app 回调上下文有效；
# ``flet_test_page`` fixture 的 ``app_main`` 只捕获 page 后即返回。
# 轮询用 ``await asyncio.sleep``，不得用 ``time.sleep``：Flet 更新调度器运行在同一
# 事件循环上，同步 sleep 会阻塞调度器使断言恒超时。


class _ProbeViewModel:
    """``use_viewmodel`` 契约的最小探针实现：记录订阅归属与生命周期调用次数。"""

    def __init__(self, initial: int) -> None:
        self._state = initial
        self._subscribers: list[Callable[[int], None]] = []
        self.subscribe_calls = 0
        self.unsubscribe_calls = 0
        self.dispose_calls = 0
        self.delivered = 0

    @property
    def state(self) -> Any:
        return self._state

    def subscribe(self, callback: Any) -> Callable[[], None]:
        self.subscribe_calls += 1
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            self.unsubscribe_calls += 1
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def notify(self, value: int) -> None:
        self._state = value
        for callback in list(self._subscribers):
            self.delivered += 1
            callback(self._state)

    def capture_stale_callback(self) -> Callable[[int], None]:
        """返回当前订阅回调的快照，用于模拟卸载后的迟到投递。"""
        assert self._subscribers, "capture_stale_callback 需在存在订阅时调用"
        return self._subscribers[0]

    def dispose(self) -> None:
        self.dispose_calls += 1
        self._subscribers.clear()


@ft.observable
@dataclass
class _ProbeHost(ft.Observable):
    """父级 Observable：字段变更驱动 Flet 对子组件的 reconcile / 移除。"""

    vm: _ProbeViewModel | None = None
    factory: Callable[[], _ProbeViewModel] | None = None
    show_child: bool = True


@ft.component
def _ExternalProbeChild(vm: _ProbeViewModel):
    state, _ = use_viewmodel(vm=vm)
    return ft.Text(f"value={state}")


@ft.component
def _InternalProbeChild(factory: Callable[[], _ProbeViewModel]):
    state, _ = use_viewmodel(factory=factory)
    return ft.Text(f"value={state}")


@ft.component
def _ProbeHostView(host: _ProbeHost):
    if not host.show_child:
        return ft.Text("no-child")
    if host.vm is not None:
        return _ExternalProbeChild(host.vm)
    factory = host.factory
    assert factory is not None
    return _InternalProbeChild(factory)


async def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0, interval: float = 0.02) -> None:
    """异步轮询直到 ``predicate`` 为 True，超时抛 ``TimeoutError``（不阻塞事件循环）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"条件未在 {timeout}s 内满足")


def _rendered_text(root: ft.BaseControl) -> str:
    """读取组件渲染输出的文本。

    ``FletTestPage.find_control`` 只遍历 ``content``/``controls``，不进入 Component
    的 ``_b`` body，故此处沿 Component 链读取 body。
    """
    body: object = getattr(root, "_b", None)
    while body is not None and not isinstance(body, ft.Text):
        body = getattr(body, "_b", None)
    if not isinstance(body, ft.Text):
        raise AssertionError(f"组件 body 未渲染 ft.Text: {type(getattr(root, '_b', None))}")
    return str(body.value)


async def test_use_viewmodel_internal_mode_real_flet_mount_notify_unmount(flet_test_page):
    """D15-F07 内部 VM 模式：真实 Flet 下 mount → notify → reconcile → unmount 观测。"""
    page = flet_test_page.page

    async def _scenario() -> None:
        created: list[_ProbeViewModel] = []

        def _factory() -> _ProbeViewModel:
            vm = _ProbeViewModel(0)
            created.append(vm)
            return vm

        host = _ProbeHost(factory=_factory)
        page.render(_ProbeHostView, host)
        root = page.views[0].controls

        # mount：订阅归属——组件实例恰订阅 1 次，VM 仅持有 1 个订阅者
        await _wait_until(lambda: len(created) == 1 and created[0].subscribe_calls == 1)
        vm = created[0]
        assert vm.subscribe_calls == 1
        assert len(vm._subscribers) == 1
        assert _rendered_text(root) == "value=0"

        # 状态通知 → reconcile：VM 通知后组件重渲染，且 reconcile 不重建 VM、不重复订阅
        vm.notify(2)
        await _wait_until(lambda: _rendered_text(root) == "value=2")
        assert vm.delivered == 1
        assert len(created) == 1
        assert vm.subscribe_calls == 1
        assert vm.unsubscribe_calls == 0

        # 卸载前捕获订阅回调与子组件实例（强引用持有，排除弱引用被 GC 释放的干扰），
        # 先在挂载态直接投递该回调，证明它是活的 set_state（断言非恒真）
        child = root._b
        stale_callback = vm.capture_stale_callback()
        stale_callback(3)
        await _wait_until(lambda: _rendered_text(child) == "value=3")

        # unmount：cleanup 次数恰为 1，内部模式 dispose 恰为 1 次
        host.show_child = False
        await _wait_until(lambda: vm.unsubscribe_calls == 1 and vm.dispose_calls == 1)
        assert vm.unsubscribe_calls == 1
        assert vm.dispose_calls == 1
        assert vm._subscribers == []
        assert _rendered_text(root) == "no-child"
        assert getattr(child, "_b", None) is None

        # 卸载后迟到通知不再更新：即使调用方仍持有组件引用（弱引用存活），
        # 订阅回调迟到投递与 VM 自身通知都不重建 body（Flet mounted 守卫），
        # VM 通知也因退订不再投递
        delivered_before_late = vm.delivered
        stale_callback(999)
        vm.notify(999)
        await asyncio.sleep(0.1)
        assert getattr(child, "_b", None) is None
        assert vm.delivered == delivered_before_late
        assert _rendered_text(root) == "no-child"

    future = page.run_task(_scenario)
    await asyncio.wrap_future(future)


async def test_use_viewmodel_external_vm_swap_real_flet_reconcile_and_lifetime(flet_test_page):
    """D15-F07 外部 VM 模式：真实 Flet 下 VM 替换 → 退订/重订阅 → unmount 观测。"""
    page = flet_test_page.page

    async def _scenario() -> None:
        vm1 = _ProbeViewModel(10)
        vm2 = _ProbeViewModel(20)
        host = _ProbeHost(vm=vm1)
        page.render(_ProbeHostView, host)
        root = page.views[0].controls

        # mount：订阅归属——旧 VM 恰 1 个订阅者
        await _wait_until(lambda: vm1.subscribe_calls == 1)
        assert vm1.subscribe_calls == 1
        assert len(vm1._subscribers) == 1
        assert _rendered_text(root) == "value=10"

        # 状态通知 → reconcile
        vm1.notify(11)
        await _wait_until(lambda: _rendered_text(root) == "value=11")
        assert vm1.delivered == 1
        assert vm1.unsubscribe_calls == 0

        # VM 替换（外部模式）：旧 VM 恰退订 1 次且永不 dispose，新 VM 恰订阅 1 次，
        # 状态经 UIX-04 补偿收敛到新实例快照
        host.vm = vm2
        await _wait_until(lambda: vm1.unsubscribe_calls == 1 and vm2.subscribe_calls == 1)
        assert vm1.unsubscribe_calls == 1
        assert vm1.dispose_calls == 0
        assert vm1._subscribers == []
        assert vm2.subscribe_calls == 1
        assert len(vm2._subscribers) == 1
        assert _rendered_text(root) == "value=20"

        # 订阅归属：旧 VM 的通知不再影响组件（订阅已转移到新 VM）
        vm1.notify(24)
        await asyncio.sleep(0.05)
        assert _rendered_text(root) == "value=20"

        # reconcile 后订阅不漂移（deps 未变化，不重复 cleanup/订阅）
        vm2.notify(22)
        await _wait_until(lambda: _rendered_text(root) == "value=22")
        assert vm2.unsubscribe_calls == 0
        assert vm2.subscribe_calls == 1

        # 卸载前捕获订阅回调与子组件实例（强引用持有，排除弱引用被 GC 释放的干扰），
        # 挂载态直接投递该回调证明它是活的 set_state（断言非恒真）
        child = root._b
        stale_callback = vm2.capture_stale_callback()
        stale_callback(23)
        await _wait_until(lambda: _rendered_text(child) == "value=23")

        # unmount：外部模式 cleanup 恰 1 次退订且永不 dispose
        host.show_child = False
        await _wait_until(lambda: vm2.unsubscribe_calls == 1)
        assert vm2.unsubscribe_calls == 1
        assert vm2.dispose_calls == 0
        assert vm2._subscribers == []
        assert _rendered_text(root) == "no-child"
        assert getattr(child, "_b", None) is None

        # 卸载后迟到通知不再更新（外部模式同样受 mounted 守卫保护）
        delivered_before_late = vm2.delivered
        stale_callback(999)
        vm2.notify(999)
        await asyncio.sleep(0.1)
        assert getattr(child, "_b", None) is None
        assert vm2.delivered == delivered_before_late
        assert _rendered_text(root) == "no-child"

    future = page.run_task(_scenario)
    await asyncio.wrap_future(future)
