import logging
import threading
import time

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
        if self.capacity <= 0: self.capacity = 1.0
        if self.rate <= 0: self.rate = 1.0  # Avoid division by zero

        self.tokens = float(start_tokens)

        # Use monotonic clock to be immune to system time updates
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def consume(self, tokens=1):
        """
        Consume tokens from the bucket. Blocks if insufficient tokens.
        
        Args:
            tokens (int): Number of tokens to consume.
        """
        wait_time = 0
        with self.lock:
            now = time.monotonic()
            # Add tokens based on time elapsed
            elapsed = max(0, now - self.last_update)
            self.last_update = now

            # Refill tokens, clamped to capacity
            new_tokens = self.tokens + elapsed * self.rate
            self.tokens = min(self.capacity, new_tokens)

            if self.tokens < tokens:
                # Not enough tokens. We "borrow" from the future (Reservation checking).
                # Allows negative tokens (debt).
                deficit = tokens - self.tokens
                wait_time = deficit / self.rate
                self.tokens -= tokens
            else:
                # Enough tokens
                self.tokens -= tokens

        # Sleep outside the lock to avoid blocking other threads/starvation
        # and to avoid dangerous lock release/acquire dances.
        if wait_time > 0:
            time.sleep(wait_time)
