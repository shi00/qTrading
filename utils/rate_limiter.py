import logging
import threading
import time
import asyncio

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    A thread-safe Token Bucket rate limiter.

    Attributes:
        rate (float): The rate at which tokens are added to the bucket (tokens per second).
        capacity (float): The maximum number of tokens the bucket can hold.
    """

    def __init__(self, start_tokens, capacity, rate):
        self.capacity = float(capacity)
        self.rate = float(rate)

        # Validation to prevent logic errors
        if self.capacity <= 0:
            self.capacity = 1.0
        if self.rate <= 0:
            self.rate = 1.0  # Avoid division by zero

        self.tokens = float(start_tokens)

        # Use monotonic clock to be immune to system time updates
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def _consume_reserve(self, tokens):
        """Internal method to calculate wait time and update tokens under lock"""
        with self.lock:
            now = time.monotonic()
            elapsed = max(0, now - self.last_update)
            self.last_update = now

            # Refill tokens
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
        """
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
