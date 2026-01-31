
import asyncio
import pandas as pd
import sys
import os
import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from data.cache_manager import CacheManager

async def test_sync_optimization():
    print("Initializing CacheManager...")
    cache = CacheManager("test_sync.db")
    await cache.init_db()
    
    # Create dummy DataFrame
    print("Creating dummy DataFrame (5000 rows)...")
    data = {
        'ts_code': [f'{i:06d}.SH' for i in range(5000)],
        'trade_date': ['20230101'] * 5000,
        'open': [10.0] * 5000,
        'high': [11.0] * 5000,
        'low': [9.0] * 5000,
        'close': [10.5] * 5000,
        'pre_close': [10.0] * 5000,
        'change': [0.5] * 5000,
        'pct_chg': [5.0] * 5000,
        'vol': [1000] * 5000,
        'amount': [10000.0] * 5000,
        'adj_factor': [1.0] * 5000
    }
    df = pd.DataFrame(data)
    
    print("Testing save_daily_quotes (should be offloaded)...")
    start_time = datetime.datetime.now()
    
    # We expect this to return quickly even if processing is heavy (though 5000 rows is fast anyway)
    # The key is functionality: does it save?
    count = await cache.save_daily_quotes(df)
    
    end_time = datetime.datetime.now()
    print(f"Saved {count} rows in {(end_time - start_time).total_seconds():.4f}s")
    
    # Wait for DB writer to catch up (since save is async-queued)
    print("Waiting for DB writer to persist...")
    await asyncio.sleep(2)
    
    # Verify data in DB
    print("Verifying data in DB...")
    saved_df = await cache.get_daily_quotes(start_date='20230101', end_date='20230101')
    print(f"Retrieved {len(saved_df)} rows.")
    
    
    # Sort by ts_code to ensure deterministic order
    saved_df = saved_df.sort_values('ts_code').reset_index(drop=True)
    
    assert len(saved_df) == 5000, "Data mismatch!"
    assert saved_df.iloc[0]['ts_code'] == '000000.SH', f"Content mismatch! Got {saved_df.iloc[0]['ts_code']}"
    
    print("Success! Cleaning up...")
    await cache.close()
    if os.path.exists("test_sync.db"):
        os.remove("test_sync.db")
        os.remove("test_sync.db-wal")
        os.remove("test_sync.db-shm")

if __name__ == "__main__":
    asyncio.run(test_sync_optimization())
