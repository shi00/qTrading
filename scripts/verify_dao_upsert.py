import asyncio
import pandas as pd
import datetime
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from data.cache_manager import CacheManager

async def test_upsert():
    print("Initializing CacheManager...")
    cache = CacheManager()
    await cache.init_db()

    # Test 1: Daily Quotes (QuoteDao)
    print("\n--- Testing QuoteDao (daily_quotes) ---")
    df_quotes = pd.DataFrame([{
        'ts_code': '000001.SZ',
        'trade_date': '20990101', # Future date to avoid conflict with real data
        'open': 10.0, 'close': 11.0, 'high': 11.5, 'low': 9.5,
        'vol': 1000, 'amount': 10000, 'adj_factor': 1.0
    }])
    
    # Insert
    count = await cache.save_daily_quotes(df_quotes)
    print(f"Insert count: {count}")
    
    # Verify
    saved = await cache.get_daily_quotes(ts_code='000001.SZ', start_date='20990101', end_date='20990101')
    print(f"Read back close: {saved.iloc[0]['close']}")
    assert saved.iloc[0]['close'] == 11.0

    # Update (Upsert)
    df_quotes['close'] = 12.0
    count = await cache.save_daily_quotes(df_quotes)
    print(f"Update count: {count}")
    
    # Verify Update
    saved = await cache.get_daily_quotes(ts_code='000001.SZ', start_date='20990101', end_date='20990101')
    print(f"Read back updated close: {saved.iloc[0]['close']}")
    assert saved.iloc[0]['close'] == 12.0
    
    # Cleanup
    # await cache._write_db("DELETE FROM daily_quotes WHERE trade_date='20990101'")


    # Test 2: Stock Concepts (StockDao - Composite PK)
    print("\n--- Testing StockDao (stock_concepts) ---")
    df_concepts = pd.DataFrame([{
        'ts_code': '000001.SZ',
        'concept_id': 'TEST_CONCEPT',
        'concept_name': 'Test Concept',
        'updated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }])
    
    await cache.save_concepts(df_concepts)
    
    concepts = await cache.get_concepts(['000001.SZ'])
    print(f"Read back concepts: {concepts.get('000001.SZ')}")
    assert 'Test Concept' in concepts.get('000001.SZ', [])
    
    # Update Concept Name
    df_concepts['concept_name'] = 'Updated Concept'
    await cache.save_concepts(df_concepts)
    
    concepts = await cache.get_concepts(['000001.SZ'])
    print(f"Read back updated concepts: {concepts.get('000001.SZ')}")
    assert 'Updated Concept' in concepts.get('000001.SZ', [])


    print("\n[PASS] Verification Passed!")
    await cache.close()

if __name__ == "__main__":
    asyncio.run(test_upsert())
