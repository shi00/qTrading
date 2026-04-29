import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    A thread-safe Token Bucket rate limiter with adaptive rate control.

    When the server signals rate-limiting (429), call reduce_rate() to slow down.
    After consecutive successes, the rate gradually recovers toward original_rate.

    Attributes:
        rate (float): Current rate (tokens/sec). Adjusted adaptively.
        original_rate (float): The initial configured rate (tokens/sec).
        capacity (float): Maximum tokens the bucket can hold.
        min_rate (float): Floor rate to prevent over-reduction.
    """

    _RECOVERY_STEP = 0.1
    _RECOVERY_INTERVAL = 30.0

    def __init__(self, start_tokens, capacity, rate, min_rate=None):
        self.capacity = float(capacity)
        self.original_rate = float(rate)
        self.rate = float(rate)

        if self.capacity <= 0:
            self.capacity = 1.0
        if self.rate <= 0:
            self.rate = 1.0

        self.min_rate = float(min_rate) if min_rate else max(0.5, self.rate * 0.1)

        self.tokens = float(start_tokens)

        self.last_update = time.monotonic()
        self.lock = threading.Lock()

        self._consecutive_successes = 0
        self._last_recovery_time = time.monotonic()

    def _consume_reserve(self, tokens):
        """Internal method to calculate wait time and update tokens under lock"""
        if tokens > self.capacity:
            raise ValueError(f"Requested tokens ({tokens}) exceed bucket capacity ({self.capacity})")

        with self.lock:
            now = time.monotonic()
            elapsed = max(0, now - self.last_update)
            self.last_update = now

            new_tokens = self.tokens + elapsed * self.rate
            self.tokens = min(self.capacity, new_tokens)

            wait_time = 0
            if self.tokens < tokens:
                wait_time = (tokens - self.tokens) / self.rate

            self.tokens -= tokens
            return wait_time

    def consume(self, tokens=1):
        """
        Consume tokens from the bucket. Blocks thread if insufficient tokens.
        S3-2 fix: Warn if called from async context (should use consume_async instead).
        """
        try:
            loop = asyncio.get_running_loop()
            if loop is not None:
                logger.warning(
                    "[TokenBucket] consume() called from async context. "
                    "Use consume_async() instead to avoid blocking the event loop."
                )
        except RuntimeError:
            pass

        wait_time = self._consume_reserve(tokens)
        if wait_time > 0:
            time.sleep(wait_time)

    async def consume_async(self, tokens=1):
        """
        Consume tokens from the bucket. Suspends coroutine if insufficient tokens.
        ST-04: Non-blocking equivalent of consume.
        """
        wait_time = self._consume_reserve(tokens)
        if wait_time > 0:
            await asyncio.sleep(wait_time)

    def reduce_rate(self, factor=0.5):
        """
        Reduce the current rate by the given factor (adaptive backoff).
        Called when the server signals rate-limiting (429 / 频次超限).

        Args:
            factor: Multiplier to reduce rate by. 0.5 = halve the rate.
        """
        with self.lock:
            old_rate = self.rate
            self.rate = max(self.min_rate, self.rate * factor)
            self._consecutive_successes = 0
            if old_rate != self.rate:
                logger.info(
                    f"[RateLimiter] Rate reduced: {old_rate:.2f} -> {self.rate:.2f} req/s "
                    f"({self.rate * 60:.0f}/min, was {old_rate * 60:.0f}/min)",
                )

    def on_success(self):
        """
        Called after a successful API response.
        Tracks consecutive successes and gradually recovers rate.
        """
        with self.lock:
            self._consecutive_successes += 1
            now = time.monotonic()

            if (
                self.rate < self.original_rate
                and self._consecutive_successes >= 10
                and (now - self._last_recovery_time) >= self._RECOVERY_INTERVAL
            ):
                old_rate = self.rate
                self.rate = min(self.original_rate, self.rate + self._RECOVERY_STEP * self.original_rate)
                self._last_recovery_time = now
                self._consecutive_successes = 0
                if old_rate != self.rate:
                    logger.debug(
                        f"[RateLimiter] Rate recovered: {old_rate:.2f} -> {self.rate:.2f} req/s "
                        f"({self.rate * 60:.0f}/min)",
                    )

    @property
    def current_rate_per_min(self) -> float:
        with self.lock:
            return self.rate * 60

    @property
    def original_rate_per_min(self) -> float:
        return self.original_rate * 60

    def reconfigure(self, rate=None, capacity=None):
        """
        Safely update rate/capacity at runtime (e.g. when user changes config).
        Resets adaptive state so the new rate takes effect immediately.
        """
        with self.lock:
            if rate is not None:
                new_rate = float(rate)
                if new_rate <= 0:
                    new_rate = 1.0
                self.rate = new_rate
                self.original_rate = new_rate
                self.min_rate = max(0.5, new_rate * 0.1)
            if capacity is not None:
                new_cap = float(capacity)
                if new_cap <= 0:
                    new_cap = 1.0
                self.capacity = new_cap
                self.tokens = min(self.tokens, new_cap)
            self._consecutive_successes = 0
            self._last_recovery_time = time.monotonic()
