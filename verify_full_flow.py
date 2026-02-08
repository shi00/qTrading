import asyncio
import os
import sys
import logging
import pandas as pd
from datetime import datetime
import aiosqlite

# Setup path
sys.path.append(os.getcwd())

from utils.config_handler import ConfigHandler
from data.cache_manager import CacheManager
from data.tushare_client import TushareClient
from data.sync_strategies.financial import FinancialSyncStrategy
from data.sync_strategies.base import SyncContext

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FullVerify")

async def main():
    print("--- Starting End-to-End Hybrid Sync Verification ---")
    
    # 1. Initialize Components
    config = ConfigHandler()
    cache = CacheManager()
    await cache.init_db() # Ensure DB is ready
    
    # Use the correct static method to get the token
    token = ConfigHandler.get_token()
    if not token:
        print("❌ Error: Tushare token not found in settings!")
        return

    api = TushareClient(token=token)
    context = SyncContext(api=api, cache=cache, config=config)
    strategy = FinancialSyncStrategy(context)
    
    # Test Parameters
    TEST_DATE = "20231225" # A recent trading day with expected announcements
    TEST_STOCK = "000001.SZ" # Ping An Bank (Reliable data)
    TEST_PERIOD = "20230630" # Semi-annual report
    
    # --- Test 1: Batch Sync (Dividend, Repurchase, Forecast) ---
    print(f"\n[Test 1] Running Batch Sync for {TEST_DATE}...")
    try:
        await strategy._sync_corporate_actions_by_date([TEST_DATE])
        print("[OK] Batch Sync Call Completed.")
        
        # Verify Data in DB
        async with aiosqlite.connect(cache.db_path) as db:
            # Check Dividend
            c = await db.execute("SELECT count(*) FROM dividend WHERE ann_date=?", (TEST_DATE,))
            div_count = (await c.fetchone())[0]
            print(f"   -> Dividend Records: {div_count}")
            
            # Check Repurchase
            c = await db.execute("SELECT count(*) FROM repurchase WHERE ann_date=?", (TEST_DATE,))
            rep_count = (await c.fetchone())[0]
            print(f"   -> Repurchase Records: {rep_count}")
            
    except Exception as e:
        print(f"[FAIL] Batch Sync Failed: {e}")

    # --- Test 2: Stock Sync (MainBz, Audit, Pledge) ---
    print(f"\n[Test 2] Running Stock Sync for {TEST_STOCK} (Period: {TEST_PERIOD})...")
    try:
        # We invoke the internal helper directly
        df_merged = await strategy._fetch_comprehensive_financial_data(TEST_STOCK, period=TEST_PERIOD)
        
        if df_merged is not None:
             print(f"[OK] Core Financial Data Fetched: {len(df_merged)} rows (Merged).")
        else:
             print("[WARN] Core Financial Data is Empty (might be expected if already synced or no report).")

        # Allow time for background writer to save data
        print("   -> Waiting for DB writer...")
        await asyncio.sleep(2)

        # Verify Aux Data in DB
        async with aiosqlite.connect(cache.db_path) as db:
            # Check MainBz
            # MainBz doesn't utilize 'period' column in Tushare, it uses end_date
            c = await db.execute("SELECT count(*) FROM fina_mainbz WHERE ts_code=? AND end_date=?", (TEST_STOCK, TEST_PERIOD))
            bz_count = (await c.fetchone())[0]
            print(f"   -> MainBz Records: {bz_count}")
            
            # Check Audit
            c = await db.execute("SELECT count(*) FROM fina_audit WHERE ts_code=? AND end_date=?", (TEST_STOCK, TEST_PERIOD))
            audit_count = (await c.fetchone())[0]
            print(f"   -> Audit Records: {audit_count}")

            # Check Pledge (Snapshot, check if any exists for this stock recently)
            c = await db.execute("SELECT count(*) FROM pledge_stat WHERE ts_code=?", (TEST_STOCK,))
            pledge_count = (await c.fetchone())[0]
            print(f"   -> Pledge Records: {pledge_count}")

    except Exception as e:
        print(f"[FAIL] Stock Sync Failed: {e}")

    # --- Test 3: Health Check ---
    print("\n[Test 3] Running Health Check...")
    try:
        health = await cache.check_comprehensive_health()
        print("[OK] Health Check Result:")
        if 'tables' in health:
            for table, stats in health['tables'].items():
                skipped_str = " (SKIPPED)" if stats.get('skipped') else ""
                print(f"   - {table}: Covered={stats.get('covered')}, Fresh={stats.get('fresh')} {skipped_str}")
                
            # Specific Check
            if health['tables'].get('fina_mainbz', {}).get('fresh') > 0:
                print("   -> fina_mainbz is ACTIVE.")
            else:
                print("   -> fina_mainbz is EMPTY.")
        else:
            print(f"[WARN] Unexpected Health Check Structure: {health.keys()}")

    except Exception as e:
        print(f"[FAIL] Health Check Failed: {e}")

    print("\n--- Verification Finished ---")

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        # Just use asyncio.run which handles policy internally in newer python versions, 
        # or set policy if needed but carefully.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Refatal Error: {e}")
