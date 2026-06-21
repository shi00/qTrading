"""
Tests for TokenBucket rate limiter.

验证令牌桶限流器的正确性，包括令牌消费、补充和线程安全。
"""

import asyncio
import threading
import unittest
from unittest.mock import patch

import pytest

from tests.virtual_clock import VirtualClock
from utils.rate_limiter import TokenBucket

pytestmark = pytest.mark.unit


class TestTokenBucketCreation(unittest.TestCase):
    """测试令牌桶创建"""

    def test_create_normal(self):
        """正常创建"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

        self.assertEqual(bucket.capacity, 20.0)
        self.assertEqual(bucket.rate, 5.0)
        self.assertEqual(bucket.tokens, 10.0)

    def test_create_zero_capacity(self):
        """零容量自动修正为 1"""
        bucket = TokenBucket(start_tokens=0, capacity=0, rate=5.0)

        self.assertEqual(bucket.capacity, 1.0)

    def test_create_negative_capacity(self):
        """负容量自动修正为 1"""
        bucket = TokenBucket(start_tokens=0, capacity=-10, rate=5.0)

        self.assertEqual(bucket.capacity, 1.0)

    def test_create_zero_rate(self):
        """零速率自动修正为 1"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=0)

        self.assertEqual(bucket.rate, 1.0)

    def test_create_negative_rate(self):
        """负速率自动修正为 1"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=-5.0)

        self.assertEqual(bucket.rate, 1.0)


class TestTokenBucketConsume(unittest.TestCase):
    """测试令牌消费"""

    def test_consume_sufficient_tokens(self):
        """充足令牌消费"""
        clock = VirtualClock()
        with (
            patch("utils.rate_limiter.time.monotonic", clock.now),
            patch("utils.rate_limiter.time.sleep", clock.sleep),
        ):
            bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

            start = clock.now()
            bucket.consume(5)
            elapsed = clock.now() - start

            self.assertLess(elapsed, 0.1)
            self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

    def test_consume_insufficient_tokens(self):
        """不足令牌消费 - 需等待（虚拟时钟，不真实 sleep）。

        覆盖 rate_limiter.py:57-96 的 _consume_reserve + consume：
        - __init__ 调用 time.monotonic() 2 次（line 51, 55）
        - _consume_reserve 调用 time.monotonic() 1 次（line 63）
        - consume 调用 time.sleep(wait_time) 1 次（line 96）

        注意：consume() 的 line 82-92 有 asyncio.get_running_loop() 检查。
        在 sync 测试中（无运行中事件循环），get_running_loop() 抛出
        RuntimeError("no running event loop")，被 except 捕获后 pass，
        不影响测试。session 级事件循环存在但未"运行"，行为一致。
        """
        clock = VirtualClock()
        with (
            patch("utils.rate_limiter.time.monotonic", clock.now),
            patch("utils.rate_limiter.time.sleep", clock.sleep),
        ):
            bucket = TokenBucket(start_tokens=1, capacity=10, rate=10.0)

            bucket.consume(5)

            # 验证虚拟时钟推进（等待令牌补充）
            self.assertGreater(clock.now(), 0)
            self.assertLess(bucket.tokens, 0)

    def test_consume_all_tokens(self):
        """消费所有令牌"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=5.0)

        bucket.consume(5)

        self.assertAlmostEqual(bucket.tokens, 0.0, places=1)

    def test_consume_default_tokens(self):
        """默认消费 1 个令牌"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=5.0)

        bucket.consume()

        self.assertAlmostEqual(bucket.tokens, 4.0, places=1)


class TestTokenBucketRefill(unittest.TestCase):
    """测试令牌补充"""

    def test_refill_tokens(self):
        clock = VirtualClock()
        with patch("utils.rate_limiter.time.monotonic", clock.now):
            bucket = TokenBucket(start_tokens=0, capacity=100, rate=10.0)
            clock.advance(1.0)
            bucket._consume_reserve(0)
            self.assertGreater(bucket.tokens, 0)

    def test_refill_not_exceed_capacity(self):
        clock = VirtualClock()
        with patch("utils.rate_limiter.time.monotonic", clock.now):
            bucket = TokenBucket(start_tokens=10, capacity=20, rate=100.0)
            clock.advance(1.0)
            bucket._consume_reserve(0)
            self.assertLessEqual(bucket.tokens, 20.0)


class TestTokenBucketThreadSafety(unittest.TestCase):
    """测试线程安全（P1 级，标记 slow 可选跳过）"""

    @pytest.mark.slow
    def test_concurrent_consume(self):
        """并发消费"""
        bucket = TokenBucket(start_tokens=100, capacity=100, rate=100.0)

        results = []
        errors = []

        def consumer(amount):
            try:
                bucket.consume(amount)
                results.append(amount)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=consumer, args=(10,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 5)


class TestTokenBucketAsync(unittest.TestCase):
    """测试异步消费"""

    def test_consume_async_sufficient(self):
        """异步消费充足令牌"""
        clock = VirtualClock()

        async def run_test():
            with (
                patch("utils.rate_limiter.time.monotonic", clock.now),
                patch("utils.rate_limiter.asyncio.sleep", clock.async_sleep),
            ):
                bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

                start = clock.now()
                await bucket.consume_async(5)
                elapsed = clock.now() - start

                self.assertLess(elapsed, 0.1)
                self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

        asyncio.run(run_test())

    def test_consume_async_insufficient(self):
        """异步消费不足令牌（虚拟时钟，不真实 sleep）。

        覆盖 rate_limiter.py:57-110 的 _consume_reserve + consume_async：
        - __init__ 调用 time.monotonic() 2 次（line 51, 55）
        - _consume_reserve 调用 time.monotonic() 1 次（line 63）
        - consume_async 调用 asyncio.sleep(wait_time) 1 次（line 105）
        """
        clock = VirtualClock()

        async def run_test():
            with (
                patch("utils.rate_limiter.time.monotonic", clock.now),
                patch("utils.rate_limiter.asyncio.sleep", clock.async_sleep),
            ):
                bucket = TokenBucket(start_tokens=1, capacity=10, rate=10.0)

                await bucket.consume_async(5)

                # 验证虚拟时钟推进（等待令牌补充）
                self.assertGreater(clock.now(), 0)

        asyncio.run(run_test())

    def test_consume_async_default(self):
        """异步默认消费 1 个令牌"""

        async def run_test():
            bucket = TokenBucket(start_tokens=5, capacity=10, rate=5.0)

            await bucket.consume_async()

            self.assertAlmostEqual(bucket.tokens, 4.0, places=1)

        asyncio.run(run_test())


class TestTokenBucketEdgeCases(unittest.TestCase):
    """测试边界条件"""

    def test_consume_more_than_capacity_raises_error(self):
        """消费超过容量的令牌应该抛出异常"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=10.0)

        with self.assertRaises(ValueError) as context:
            bucket.consume(15)

        self.assertIn("exceed bucket capacity", str(context.exception))

    def test_consume_zero_tokens(self):
        """消费零令牌"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=5.0)

        bucket._consume_reserve(0)

        self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

    def test_fractional_tokens(self):
        """分数令牌"""
        bucket = TokenBucket(start_tokens=5.5, capacity=10, rate=5.0)

        bucket.consume(2.5)  # type: ignore[untyped]
        self.assertAlmostEqual(bucket.tokens, 3.0, places=1)


class TestTokenBucketAdaptiveRate(unittest.TestCase):
    """测试自适应速率控制"""

    def test_reduce_rate_halves(self):
        """reduce_rate(0.5) 将速率减半"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.rate, 2.5, places=2)

    def test_reduce_rate_respects_min_rate(self):
        """reduce_rate 不低于 min_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0, min_rate=2.0)
        bucket.reduce_rate(0.1)
        bucket.reduce_rate(0.1)
        bucket.reduce_rate(0.1)
        self.assertGreaterEqual(bucket.rate, 2.0)

    def test_reduce_rate_resets_consecutive_successes(self):
        """reduce_rate 重置连续成功计数"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        for _ in range(15):
            bucket.on_success()
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.rate, 2.5, places=2)

    def test_on_success_no_recovery_below_threshold(self):
        """on_success 在连续成功 < 10 时不恢复速率"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reduce_rate(0.5)
        for _ in range(9):
            bucket.on_success()
        self.assertAlmostEqual(bucket.rate, 2.5, places=2)

    def test_on_success_recovers_after_threshold(self):
        """on_success 在连续成功 >= 10 后逐步恢复速率"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.rate, 2.5, places=2)

        bucket._last_recovery_time = 0
        for _ in range(10):
            bucket.on_success()
        self.assertGreater(bucket.rate, 2.5)
        self.assertLessEqual(bucket.rate, 5.0)

    def test_on_success_never_exceeds_original_rate(self):
        """on_success 恢复速率不超过 original_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket._last_recovery_time = 0
        for _ in range(200):
            bucket.on_success()
        self.assertAlmostEqual(bucket.rate, 5.0, places=2)

    def test_current_rate_per_min(self):
        """current_rate_per_min 属性"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        self.assertAlmostEqual(bucket.current_rate_per_min, 300.0, places=1)

    def test_original_rate_per_min(self):
        """original_rate_per_min 属性"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.original_rate_per_min, 300.0, places=1)

    def test_reconfigure_rate(self):
        """reconfigure 更新速率并重置自适应状态"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.rate, 2.5, places=2)

        bucket.reconfigure(rate=10.0)
        self.assertAlmostEqual(bucket.rate, 10.0, places=2)
        self.assertAlmostEqual(bucket.original_rate, 10.0, places=2)

    def test_reconfigure_capacity(self):
        """reconfigure 更新容量并裁剪 tokens"""
        bucket = TokenBucket(start_tokens=15, capacity=20, rate=5.0)
        bucket.reconfigure(capacity=10)
        self.assertAlmostEqual(bucket.capacity, 10.0, places=1)
        self.assertAlmostEqual(bucket.tokens, 10.0, places=1)

    def test_reconfigure_rejects_zero_rate(self):
        """reconfigure 拒绝零速率，自动修正为 1"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reconfigure(rate=0)
        self.assertAlmostEqual(bucket.rate, 1.0, places=2)

    def test_reconfigure_rejects_zero_capacity(self):
        """reconfigure 拒绝零容量，自动修正为 1"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        bucket.reconfigure(capacity=0)
        self.assertAlmostEqual(bucket.capacity, 1.0, places=1)

    def test_min_rate_default(self):
        """默认 min_rate 为 rate 的 10%"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        self.assertAlmostEqual(bucket.min_rate, 0.5, places=2)

    def test_min_rate_custom(self):
        """自定义 min_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0, min_rate=2.0)
        self.assertAlmostEqual(bucket.min_rate, 2.0, places=2)


class TestTokenBucketConcurrent(unittest.TestCase):
    """测试 TokenBucket 并发安全性（P1 级，标记 slow 可选跳过）"""

    @pytest.mark.slow
    def test_concurrent_reduce_rate_and_on_success(self):
        """多线程同时调用 reduce_rate 和 on_success 不崩溃"""
        bucket = TokenBucket(start_tokens=50, capacity=100, rate=10.0)
        errors = []

        def worker_reduce():
            try:
                for _ in range(50):
                    bucket.reduce_rate(0.9)
            except Exception as e:
                errors.append(e)

        def worker_success():
            try:
                for _ in range(50):
                    bucket.on_success()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_reduce),
            threading.Thread(target=worker_success),
            threading.Thread(target=worker_reduce),
            threading.Thread(target=worker_success),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertGreaterEqual(bucket.rate, bucket.min_rate)
        self.assertLessEqual(bucket.rate, bucket.original_rate)

    @pytest.mark.slow
    def test_concurrent_consume_and_reduce_rate(self):
        """多线程同时消费令牌和降低速率不崩溃"""
        bucket = TokenBucket(start_tokens=50, capacity=100, rate=10.0)
        errors = []

        def worker_consume():
            try:
                for _ in range(20):
                    bucket.consume(1)
            except Exception as e:
                errors.append(e)

        def worker_reduce():
            try:
                for _ in range(20):
                    bucket.reduce_rate(0.8)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_consume),
            threading.Thread(target=worker_reduce),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    @pytest.mark.slow
    def test_concurrent_reconfigure_and_consume(self):
        """多线程同时 reconfigure 和消费不崩溃"""
        bucket = TokenBucket(start_tokens=50, capacity=100, rate=10.0)
        errors = []

        def worker_reconfigure():
            try:
                for i in range(10):
                    bucket.reconfigure(rate=5.0 + i)
            except Exception as e:
                errors.append(e)

        def worker_consume():
            try:
                for _ in range(10):
                    bucket.consume(1)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_reconfigure),
            threading.Thread(target=worker_consume),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


class TestTokenBucketAdaptiveEdgeCases(unittest.TestCase):
    """测试自适应速率边界条件（P1 级）"""

    def test_reduce_rate_converges_to_min(self):
        """连续 reduce_rate 收敛到 min_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0, min_rate=1.0)
        for _ in range(20):
            bucket.reduce_rate(0.5)

        self.assertGreaterEqual(bucket.rate, 1.0)
        self.assertAlmostEqual(bucket.rate, 1.0, places=2)

    def test_on_success_recovery_step(self):
        """on_success 恢复步长为 _RECOVERY_STEP * original_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0)
        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.rate, 5.0, places=2)

        bucket._consecutive_successes = 9
        bucket._last_recovery_time = 0
        bucket.on_success()

        expected_rate = 5.0 + bucket._RECOVERY_STEP * 10.0
        self.assertAlmostEqual(bucket.rate, expected_rate, places=2)

    def test_on_success_recovery_interval_guard(self):
        """_RECOVERY_INTERVAL 内不重复恢复"""
        clock = VirtualClock()
        with patch("utils.rate_limiter.time.monotonic", clock.now):
            bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0)
            bucket.reduce_rate(0.5)
            self.assertAlmostEqual(bucket.rate, 5.0, places=2)

            bucket._consecutive_successes = 10
            bucket._last_recovery_time = clock.now()
            bucket.on_success()

            self.assertAlmostEqual(bucket.rate, 5.0, places=2)

    def test_on_success_no_recovery_above_original(self):
        """恢复速率不超过 original_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0)
        bucket.reduce_rate(0.9)
        self.assertAlmostEqual(bucket.rate, 9.0, places=2)

        for _ in range(100):
            bucket._consecutive_successes = 10
            bucket._last_recovery_time = 0
            bucket.on_success()

        self.assertLessEqual(bucket.rate, bucket.original_rate)

    def test_reduce_rate_resets_consecutive_successes(self):
        """reduce_rate 重置连续成功计数"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0)
        for _ in range(5):
            bucket.on_success()
        self.assertEqual(bucket._consecutive_successes, 5)

        bucket.reduce_rate(0.5)
        self.assertEqual(bucket._consecutive_successes, 0)

    def test_reconfigure_resets_min_rate(self):
        """reconfigure 更新 min_rate"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        self.assertAlmostEqual(bucket.min_rate, 0.5, places=2)

        bucket.reconfigure(rate=10.0)
        self.assertAlmostEqual(bucket.min_rate, 1.0, places=2)

    def test_reconfigure_trims_tokens_to_capacity(self):
        """reconfigure 后 tokens 不超过新 capacity"""
        bucket = TokenBucket(start_tokens=15, capacity=20, rate=5.0)
        bucket.reconfigure(capacity=8)
        self.assertAlmostEqual(bucket.tokens, 8.0, places=1)

    def test_current_rate_per_min_reflects_adaptive(self):
        """current_rate_per_min 反映自适应后的速率"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10.0)
        self.assertAlmostEqual(bucket.current_rate_per_min, 600.0, places=1)

        bucket.reduce_rate(0.5)
        self.assertAlmostEqual(bucket.current_rate_per_min, 300.0, places=1)


if __name__ == "__main__":
    unittest.main()
