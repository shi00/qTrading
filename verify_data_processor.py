
import asyncio
import threading
import logging
import sys
import os

sys.path.append(os.getcwd())

from data.data_processor import DataProcessor
from utils.config_handler import ConfigHandler

logging.basicConfig(level=logging.INFO)

async def test_singleton():
    print("--- Testing DataProcessor Singleton ---")
    
    # 1. Sequential Access
    dp1 = DataProcessor()
    dp2 = DataProcessor()
    
    print(f"dp1 ID: {id(dp1)}")
    print(f"dp2 ID: {id(dp2)}")
    
    if dp1 is dp2:
        print("[OK] Singleton identity verified (Sequential).")
    else:
        print("[FAIL] Singleton identity failed!")
        return

    # 2. Threaded Access (Race Condition Simulation)
    print("\n--- Testing Thread Safety ---")
    instances = []
    
    def get_instance():
        inst = DataProcessor()
        instances.append(inst)
        
    threads = [threading.Thread(target=get_instance) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    first = instances[0]
    all_same = all(inst is first for inst in instances)
    if all_same:
         print("[OK] Thread safety verified. All 10 threads got same instance.")
    else:
         print("[FAIL] Race condition detected! Multiple instances created.")
         
    # 3. Check Constants Usage
    print("\n--- Testing Logic ---")
    try:
        # We can't easily check internal constant usage without mocking datetime, 
        # but we can check if method runs without error.
        date = await dp1.get_latest_trade_date()
        print(f"[OK] get_latest_trade_date returned: {date}")
    except Exception as e:
        print(f"[FAIL] get_latest_trade_date failed: {e}")

if __name__ == "__main__":
    # Ensure config ready
    ConfigHandler.ensure_defaults()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_singleton())
