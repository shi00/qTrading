"""
Tests for TokenBucket rate limiter.

验证令牌桶限流器的正确性，包括令牌消费、补充和线程安全。
"""

import asyncio
import threading
import time
import unittest

from utils.rate_limiter import TokenBucket


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
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

        start_time = time.monotonic()
        bucket.consume(5)
        elapsed = time.monotonic() - start_time

        self.assertLess(elapsed, 0.1)
        self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

    def test_consume_insufficient_tokens(self):
        """不足令牌消费 - 需等待"""
        bucket = TokenBucket(start_tokens=1, capacity=10, rate=10.0)

        start_time = time.monotonic()
        bucket.consume(5)
        elapsed = time.monotonic() - start_time

        self.assertGreaterEqual(elapsed, 0.3)
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
        """令牌自动补充"""
        bucket = TokenBucket(start_tokens=0, capacity=100, rate=10.0)

        time.sleep(0.5)

        bucket._consume_reserve(0)
        self.assertGreater(bucket.tokens, 0)

    def test_refill_not_exceed_capacity(self):
        """补充不超过容量"""
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=100.0)

        time.sleep(0.5)

        bucket._consume_reserve(0)
        self.assertLessEqual(bucket.tokens, 20.0)


class TestTokenBucketThreadSafety(unittest.TestCase):
    """测试线程安全"""

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

        async def run_test():
            bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

            start_time = time.monotonic()
            await bucket.consume_async(5)
            elapsed = time.monotonic() - start_time

            self.assertLess(elapsed, 0.1)
            self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

        asyncio.run(run_test())

    def test_consume_async_insufficient(self):
        """异步消费不足令牌"""

        async def run_test():
            bucket = TokenBucket(start_tokens=1, capacity=10, rate=10.0)

            start_time = time.monotonic()
            await bucket.consume_async(5)
            elapsed = time.monotonic() - start_time

            self.assertGreaterEqual(elapsed, 0.3)

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

    def test_consume_more_than_capacity(self):
        """消费超过容量的令牌"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=10.0)

        start_time = time.monotonic()
        bucket.consume(15)
        elapsed = time.monotonic() - start_time

        self.assertGreater(elapsed, 0.5)
        self.assertLess(bucket.tokens, 0)

    def test_consume_zero_tokens(self):
        """消费零令牌"""
        bucket = TokenBucket(start_tokens=5, capacity=10, rate=5.0)

        bucket._consume_reserve(0)

        self.assertAlmostEqual(bucket.tokens, 5.0, places=1)

    def test_fractional_tokens(self):
        """分数令牌"""
        bucket = TokenBucket(start_tokens=5.5, capacity=10, rate=5.0)

        bucket.consume(2.5)  # type: ignore

        self.assertAlmostEqual(bucket.tokens, 3.0, places=1)


if __name__ == "__main__":
    unittest.main()
