import asyncio
import os
import sys
import logging

# Setup path
sys.path.append(os.getcwd())

# Mocking config to avoid loading full app
from utils.config_handler import ConfigHandler
from data.cache_manager import CacheManager
from data.sync_strategies.financial import FinancialSyncStrategy
from data.constants import FINANCIAL_BATCH_TABLES, HEALTH_CHECK_TABLES

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Verification")

async def main():
    print("--- Verifying Hybrid Sync Implementation ---")
    
    # 1. Verify Constants
    print(f"[Check] FINANCIAL_BATCH_TABLES: {list(FINANCIAL_BATCH_TABLES.keys())}")
    assert 'fina_forecast' in FINANCIAL_BATCH_TABLES
    print(f"[Check] HEALTH_CHECK_TABLES: {len(HEALTH_CHECK_TABLES)} tables defined.")
    
    # 2. Verify CacheManager Methods
    cm = CacheManager()
    methods = [
        'save_fina_forecast', 'save_dividend', 'save_repurchase', 
        'save_fina_mainbz', 'save_pledge_stat', 'check_comprehensive_health'
    ]
    for m in methods:
        if hasattr(cm, m):
            print(f"[Check] CacheManager.{m} exists.")
        else:
            print(f"[FAIL] CacheManager.{m} MISSING!")
            return

    # 3. Verify Strategy Methods
    # We can't easily instantiate Strategy without a full Context/API mock, 
    # but we can inspect the class.
    if hasattr(FinancialSyncStrategy, '_sync_corporate_actions_by_date'):
        print("[Check] FinancialSyncStrategy._sync_corporate_actions_by_date exists.")
    else:
        print("[FAIL] _sync_corporate_actions_by_date MISSING!")
        return

    if hasattr(FinancialSyncStrategy, '_fetch_comprehensive_financial_data'):
        print("[Check] FinancialSyncStrategy._fetch_comprehensive_financial_data exists.")
    else:
        print("[FAIL] _fetch_comprehensive_financial_data MISSING!")
        return

    print("--- Verification Successful ---")

if __name__ == "__main__":
    asyncio.run(main())
