"""Unit tests for tests.e2e.helpers.anchor_page.retry_until_triggered.

覆盖"确认触发 + N 次重试"的步骤级重试语义（抗 headless CanvasKit 渲染吞点击）：
- confirm 首次即 True → interact 只调用 1 次，函数正常返回
- confirm 前 N-1 次 False、末次 True → interact 调用 attempts 次，函数正常返回
- confirm 持续 False → 抛 RuntimeError（无异常路径）
- interact 抛异常 → 重试耗尽后抛 RuntimeError 并保留异常链（from last_exc）

纯 asyncio 逻辑，不依赖 Playwright/Page，无需真实浏览器。测试传很小的
interval_ms 避免真实等待。
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.anchor_page import retry_until_triggered

pytestmark = pytest.mark.unit


async def test_confirm_true_on_first_attempt_calls_interact_once() -> None:
    """confirm 首次即 True → interact 只被调用 1 次，函数正常返回。"""
    calls = 0

    async def interact() -> None:
        nonlocal calls
        calls += 1

    async def confirm() -> bool:
        return True

    await retry_until_triggered(interact, confirm, attempts=3, interval_ms=1)

    assert calls == 1


async def test_confirm_true_on_third_attempt_calls_interact_three_times() -> None:
    """confirm 前 2 次 False、第 3 次 True → interact 调用 3 次，函数正常返回。"""
    calls = 0

    async def interact() -> None:
        nonlocal calls
        calls += 1

    async def confirm() -> bool:
        return calls >= 3

    await retry_until_triggered(interact, confirm, attempts=3, interval_ms=1)

    assert calls == 3


async def test_confirm_always_false_raises_runtime_error() -> None:
    """confirm 3 次都 False → 抛 RuntimeError，消息含 after N attempts 与 not triggered。"""
    calls = 0

    async def interact() -> None:
        nonlocal calls
        calls += 1

    async def confirm() -> bool:
        return False

    with pytest.raises(RuntimeError, match="after 3 attempts") as exc_info:
        await retry_until_triggered(interact, confirm, attempts=3, interval_ms=1)

    assert calls == 3
    assert "not triggered" in str(exc_info.value)


async def test_exhausted_attempts_with_exception_keeps_chain() -> None:
    """interact 抛异常 → 重试后仍耗尽，抛 RuntimeError 并保留异常链（from last_exc）。"""
    calls = 0

    async def interact() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    async def confirm() -> bool:
        return False

    with pytest.raises(RuntimeError, match="after 3 attempts") as exc_info:
        await retry_until_triggered(interact, confirm, attempts=3, interval_ms=1)

    assert calls == 3
    assert isinstance(exc_info.value.__cause__, ValueError)
