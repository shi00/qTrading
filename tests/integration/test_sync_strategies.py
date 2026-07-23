"""
Tests for sync strategies and rate limiter adaptive behavior.

S2-1: TokenBucket reduces rate on consecutive failures (adaptive backoff).
S2-2: TokenBucket recovers rate after consecutive successes.
S2-3: Sync strategies use rate limiter for API calls.
"""

from unittest.mock import patch

import pytest

from utils.rate_limiter import TokenBucket

pytestmark = pytest.mark.integration


class TestTokenBucketAdaptiveBackoff:
    """S2-1: TokenBucket must reduce rate on server rate-limit signals"""

    def test_reduce_rate_halves_on_429(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        assert tb.rate == 10.0

        tb.reduce_rate(factor=0.5)
        assert tb.rate == 5.0

    def test_reduce_rate_respects_min_rate(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=2.0)
        tb.reduce_rate(factor=0.1)
        assert tb.rate >= tb.min_rate

    def test_multiple_reductions_cannot_go_below_min_rate(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        for _ in range(20):
            tb.reduce_rate(factor=0.5)
        assert tb.rate >= tb.min_rate

    def test_failure_counter_resets_on_reduce(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb._consecutive_successes = 5
        tb.reduce_rate(factor=0.5)
        assert tb._consecutive_successes == 0


class TestTokenBucketAutoRecovery:
    """S2-2: TokenBucket must auto-recover rate after consecutive successes"""

    def test_consecutive_successes_increment(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb.reduce_rate(factor=0.5)
        for i in range(5):
            tb.on_success()
            assert tb._consecutive_successes == i + 1

    def test_rate_recovers_after_enough_successes(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb.reduce_rate(factor=0.5)
        assert tb.rate == 5.0

        tb._last_recovery_time = 0
        for _ in range(10):
            tb.on_success()
        assert tb.rate > 5.0

    def test_rate_does_not_exceed_original(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb.reduce_rate(factor=0.5)
        tb._last_recovery_time = 0
        for _ in range(100):
            tb.on_success()
        assert tb.rate <= tb.original_rate

    def test_original_rate_preserved(self):
        tb = TokenBucket(start_tokens=100, capacity=100, rate=10.0)
        tb.reduce_rate(factor=0.5)
        assert tb.original_rate == 10.0


class TestSyncStrategiesUseRateLimiter:
    """S2-3: Sync strategies must use rate limiter for API calls"""

    def test_tushare_client_has_rate_limiter(self):
        from data.external.tushare_client import TushareClient

        TushareClient._reset_singleton()
        with (
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
            patch("utils.thread_pool.ThreadPoolManager"),
        ):
            mock_ch.get_token.return_value = "test-token"
            mock_ch.get_tushare_api_limit.return_value = 120
            mock_ch.get_setting.return_value = False
            client = TushareClient()
            assert hasattr(client, "_rate_limiter") or hasattr(client, "_api_limiters")
        TushareClient._reset_singleton()

    @pytest.mark.asyncio
    async def test_rate_limiter_async_consume(self):
        tb = TokenBucket(start_tokens=10, capacity=10, rate=100.0)
        await tb.consume_async(tokens=1)
        assert tb.tokens < 10

    @pytest.mark.asyncio
    async def test_rate_limiter_raises_in_async_context(self):
        tb = TokenBucket(start_tokens=10, capacity=10, rate=100.0)
        with pytest.raises(RuntimeError, match="TokenBucket.consume\\(\\)"):
            tb.consume(tokens=1)
