import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.cache_manager import CacheManager
import config

async def test_config():
    print(f"Configured Queue Size: {config.DB_QUEUE_SIZE}")
    
    # Initialize CacheManager
    cache = CacheManager()
    
    # Check maxsize
    print(f"Actual Queue Size: {cache.queue.maxsize}")
    
    if cache.queue.maxsize == config.DB_QUEUE_SIZE:
        print("✅ Validation Passed: Queue size matches config.")
    else:
        print(f"❌ Validation Failed: Expected {config.DB_QUEUE_SIZE}, got {cache.queue.maxsize}")
        exit(1)

if __name__ == "__main__":
    asyncio.run(test_config())
