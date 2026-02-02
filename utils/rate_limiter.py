import time
import threading
import logging

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
        if self.rate <= 0: self.rate = 1.0 # Avoid division by zero
        
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
        with self.lock:
            while True:
                now = time.monotonic()
                # Add tokens based on time elapsed
                elapsed = max(0, now - self.last_update)
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                else:
                    # Calculate wait time
                    shortage = tokens - self.tokens
                    wait_time = shortage / self.rate
                    # Release lock and sleep
                    self.lock.release()
                    time.sleep(wait_time)
                    self.lock.acquire()
                    # Re-acquire lock and loop to check again (tokens might have been stolen or added)
                    # Note: last_update will be updated in next iteration
