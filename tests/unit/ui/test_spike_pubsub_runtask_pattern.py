"""Spike: PubSub + run_task 声明式模式单元测试（Phase 3.0.3）。

纯逻辑验证（不依赖 ``ft.run_async``，Windows 可运行）：
- R2 CancelledError 传播：run_task 启动的任务在 cleanup 中取消时必须传播
- grep 守护：spike 组件用 use_effect 订阅/退订，topic 精准退订
- API 验证：page.run_task 返回 Future，pubsub.unsubscribe_topic 带 topic 参数

渲染行为验证在 ``tests/integration/test_spike_pubsub_runtask.py``（Windows skip）。
"""

import asyncio
import inspect

import flet as ft
import pytest

from tests.integration.test_spike_pubsub_runtask import _spike_pubsub_view

pytestmark = pytest.mark.unit


def test_spike_pubsub_view_is_ft_component():
    """DoD 4: spike 组件必须被 @ft.component 装饰。"""
    assert hasattr(_spike_pubsub_view, "__wrapped__"), "spike 必须用 @ft.component 装饰"


def test_spike_pubsub_uses_use_effect_for_subscribe():
    """DoD 5: spike 必须用 use_effect 订阅 pubsub（topic 模式，非命令式 __init__）。"""
    source = inspect.getsource(_spike_pubsub_view)
    assert "ft.use_effect" in source, "spike 必须用 use_effect 订阅 pubsub"
    assert "page.pubsub.subscribe_topic" in source, "spike 必须在 setup 中用 subscribe_topic 订阅"
    assert "page.pubsub.unsubscribe_topic" in source, "spike 必须在 cleanup 中用 unsubscribe_topic 退订"


def test_spike_pubsub_no_imperative_patterns():
    """DoD 6: grep 守护——spike 不使用命令式模式（did_mount/will_unmount）。"""
    source = inspect.getsource(_spike_pubsub_view)
    # 排除 docstring 后检查
    assert "did_mount" not in source, "spike 不应使用 did_mount（命令式）"
    assert "will_unmount" not in source, "spike 不应使用 will_unmount（命令式）"


@pytest.mark.asyncio
async def test_run_task_cancel_propagates_cancelled_error():
    """DoD 3: run_task 启动的任务在 cleanup 中取消时 CancelledError 传播（R2 红线）。

    验证声明式 cleanup 中的标准模式：
    ``task.cancel(); await task`` — CancelledError 必须传播，不能吞没（R2）。
    """
    cancelled = asyncio.Event()

    async def _long_running() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise  # R2: 必须传播

    # 模拟 page.run_task 返回的 Future（用 get_running_loop 替代 deprecated get_event_loop）
    loop = asyncio.get_running_loop()
    task = loop.create_task(_long_running())
    # 让 task 启动并进入 try 块（否则 cancel 在 task 启动前触发，CancelledError 在协程入口抛出）
    await asyncio.sleep(0)

    # 模拟 cleanup: cancel + await（声明式 use_effect cleanup 的标准模式）
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set(), "CancelledError 必须在任务内部传播（R2）"


def test_pubsub_unsubscribe_topic_takes_topic_arg():
    """DoD 7: 验证 PubSubClient.unsubscribe_topic(topic) 带 topic 参数（Flet 0.85.3 API）。

    topic 精准退订避免误伤其他视图订阅（相比零参 unsubscribe 整批退订）。
    """
    from flet.pubsub.pubsub_client import PubSubClient

    sig = inspect.signature(PubSubClient.unsubscribe_topic)
    # 带 topic 参数（除 self 外）
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1, f"unsubscribe_topic 应带 1 个 topic 参数（除 self），实际参数: {params}"
    assert params[0].name == "topic", f"参数名应为 topic，实际: {params[0].name}"


def test_page_run_task_returns_future():
    """DoD 8: 验证 page.run_task 返回 Future（可用于 cleanup 中 cancel）。"""
    sig = inspect.signature(ft.Page.run_task)
    assert "handler" in sig.parameters, "run_task 必须接受 handler 参数"
    # 返回类型为 Future（用于 cleanup 中 cancel + await）
    assert "Future" in str(sig.return_annotation), f"run_task 应返回 Future，实际: {sig.return_annotation}"
