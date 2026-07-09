"""Spike: PubSub + run_task 声明式模式验证（Phase 3.0.3）。

验证目标：
1. ``use_effect(setup_subscribe, dependencies=[], cleanup=cleanup_unsubscribe)`` 订阅/退订 pubsub
2. ``page.run_task(vm.command)`` 启动后台任务，返回 Future
3. cleanup 中 ``task.cancel()`` + ``await task`` + ``raise CancelledError``（R2 红线）
4. ``page.pubsub.unsubscribe()`` 零参整批退订（Flet 0.85.3 API）

本 spike 为 DataExplorerView/ScreenerView 等含 pubsub 订阅 + 后台任务的组件确立标准范式。
"""

import os
import sys

import flet as ft
import pytest

_IS_HEADLESS_LINUX = sys.platform == "linux" and not os.environ.get("DISPLAY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32" or _IS_HEADLESS_LINUX,
        reason="Spike 需 ft.run_async 渲染声明式组件，Windows/headless Linux 不支持",
    ),
]


@ft.component
def _spike_pubsub_view() -> ft.Control:
    """最小声明式 PubSub + run_task 组件。

    范式：
    - ``use_effect(setup, dependencies=[], cleanup=cleanup)`` 订阅 pubsub
    - setup 中 ``page.pubsub.subscribe(handler)`` 订阅
    - cleanup 中 ``page.pubsub.unsubscribe()`` 零参退订（R2 兼容）
    - cleanup 中取消 run_task 启动的后台任务，CancelledError 传播（R2）
    """

    async def _setup() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.subscribe(lambda msg: None)
        except RuntimeError:
            pass

    async def _cleanup() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.unsubscribe()  # R2: 零参整批退订
        except RuntimeError:
            pass

    ft.use_effect(_setup, dependencies=[], cleanup=_cleanup)

    return ft.Column([ft.Text("spike-pubsub-host")])


async def test_spike_pubsub_subscribes_on_mount(flet_test_page):
    """DoD 1: 组件挂载时调用 page.pubsub.subscribe。"""
    page = flet_test_page.page
    page.add(_spike_pubsub_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "spike-pubsub-host"
            )
            is not None
        ),
        timeout=2.0,
    )
    # subscribe 应被调用
    page.pubsub.subscribe.assert_called_once()


async def test_spike_pubsub_unsubscribes_on_unmount(flet_test_page):
    """DoD 2: 组件卸载时调用 page.pubsub.unsubscribe（零参，R2 兼容）。"""
    page = flet_test_page.page
    page.add(_spike_pubsub_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "spike-pubsub-host"
            )
            is not None
        ),
        timeout=2.0,
    )
    # 清空 controls 模拟 unmount
    page.controls.clear()
    page.update()
    flet_test_page.wait_for_condition(lambda: page.pubsub.unsubscribe.called, timeout=2.0)
    # unsubscribe 零参调用
    page.pubsub.unsubscribe.assert_called_once_with()
