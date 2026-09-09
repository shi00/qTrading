"""D1-5 令牌桶原子预留单元测试。

修复点：``TokenBucket._consume_reserve`` 在令牌不足时不再扣减，
余额任意时刻 ``>= 0``；``consume`` / ``consume_async`` 通过等待后重试完成扣减。
"""

import asyncio
from unittest.mock import patch

import pytest

from tests.virtual_clock import VirtualClock
from utils.rate_limiter import TokenBucket


class TestConsumeReserveAtomic:
    """令牌不足时返回等待时长且不扣减（不转负）。"""

    def test_insufficient_returns_wait_but_does_not_borrow(self):
        # 容量 1，初始 1：第一次消费耗尽，第二次不足——旧实现会转负，新实现不扣减
        tb = TokenBucket(start_tokens=1, capacity=1, rate=1.0)
        assert tb._consume_reserve(1) == 0.0
        assert tb.tokens == 0.0

        wait = tb._consume_reserve(1)
        assert wait > 0  # 需等待约 1.0 秒
        # 不扣减：余额仅在真实时间经过下被 refill 微幅补充，绝不因本次请求而转负
        assert 0.0 <= tb.tokens < 1.0

    def test_sufficient_consumes_and_returns_zero(self):
        tb = TokenBucket(start_tokens=10, capacity=10, rate=2.0)
        assert tb._consume_reserve(5) == 0.0
        assert tb.tokens == 5.0  # 扣减但保持非负

    def test_rejects_request_above_capacity(self):
        tb = TokenBucket(start_tokens=10, capacity=10, rate=1.0)
        with pytest.raises(ValueError, match="exceed bucket capacity"):
            tb._consume_reserve(11)


class TestConsumeSyncWait:
    """同步 consume 等待后成功，且任意时刻余额非负。"""

    def test_consume_waits_then_succeeds_without_borrow(self):
        clock = VirtualClock()
        with (
            patch("utils.rate_limiter.time.monotonic", clock.now),
            patch("utils.rate_limiter.time.sleep", clock.sleep),
        ):
            tb = TokenBucket(start_tokens=1, capacity=1, rate=1.0)
            tb.consume(1)
            assert tb.tokens == 0.0

            # 第二次不足：虚拟时钟推进 1.0 秒后重试成功
            tb.consume(1)
            assert clock.now() >= 1.0
            assert tb.tokens >= 0.0  # 关键：余额永不为负


class TestConsumeAsyncConcurrent:
    """并发 consume_async 下余额不为负（D1-5 报告的收敛窗口）。"""

    @pytest.mark.asyncio
    async def test_concurrent_consume_tokens_never_negative(self):
        capacity = 5
        tb = TokenBucket(start_tokens=capacity, capacity=capacity, rate=100.0)
        snapshots = []

        async def acquire() -> None:
            await tb.consume_async(1)
            snapshots.append(tb.tokens)  # 单事件循环 + GIL：float 读取是原子的

        await asyncio.gather(*(acquire() for _ in range(30)))
        assert snapshots  # 全部成功获取
        assert tb.tokens >= -1e-9
        for s in snapshots:
            assert s >= -1e-9


class TestTokenBucketConstruction:
    """容量/速率非正输入的归一化（保持既有契约）。"""

    def test_zero_negative_capacity_and_rate_normalized(self):
        tb = TokenBucket(start_tokens=0, capacity=0, rate=0)
        assert tb.capacity == 1.0
        assert tb.rate == 1.0
        assert tb.min_rate >= 0.5


class TestConsumeContextGuard:
    """consume() 的同步/异步上下文守卫保持既有行为。"""

    @pytest.mark.asyncio
    async def test_consume_raises_in_async_context(self):
        tb = TokenBucket(start_tokens=1, capacity=1, rate=1.0)
        with pytest.raises(RuntimeError, match="from async context"):
            tb.consume(1)

    def test_consume_ok_in_sync_context(self):
        clock = VirtualClock()
        with (
            patch("utils.rate_limiter.time.monotonic", clock.now),
            patch("utils.rate_limiter.time.sleep", clock.sleep),
        ):
            tb = TokenBucket(start_tokens=1, capacity=1, rate=1.0)
            tb.consume(1)  # 同步上下文不抛 RuntimeError
            tb.consume(1)


class TestTokenBucketStableBehavior:
    """既有自适应/reconfigure 行为的回归覆盖（文件被 D1-5 修改，需满足单文件门禁）。"""

    def test_reduce_rate_and_recovery(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb.reduce_rate(factor=0.5)
        assert tb.rate == 5.0
        assert tb.current_rate_per_min == 300.0
        assert tb.original_rate_per_min == 600.0

        tb._last_recovery_time = 0
        for _ in range(10):
            tb.on_success()
        assert tb.rate > 5.0 and tb.rate <= tb.original_rate

    def test_reconfigure_rate_and_capacity(self):
        tb = TokenBucket(start_tokens=10, capacity=10, rate=10.0)
        tb.reconfigure(rate=20.0, capacity=5)
        assert tb.rate == 20.0
        assert tb.capacity == 5.0
        assert tb.tokens <= 5.0

        tb.reconfigure(rate=0, capacity=0)  # 非正输入归一化
        assert tb.rate == 1.0
        assert tb.capacity == 1.0
