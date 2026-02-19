
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.data_processor import DataProcessor
from utils.config_handler import ConfigHandler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Data Quality Verification...")
    
    dp = DataProcessor()
    
    # 1. Quick Check (Registry based)
    logger.info("--- 1. Running Quick Health Check (Tier 1) ---")
    quick_res = await dp.check_data_health()
    logger.info(f"Quick Check Status: {quick_res['status']}")
    logger.info(f"Tables Checked: {len(quick_res['fundamentals']['tables'])}")
    
    # Verify a few known tables
    tables = quick_res['fundamentals']['tables']
    if 'daily_quotes' in tables:
        logger.info(f"daily_quotes: {tables['daily_quotes']}")
    else:
        logger.error("daily_quotes MISSING from check result!")
        
    if 'macro_economy' in tables:
        logger.info(f"macro_economy: {tables['macro_economy']}")
    else:
        logger.error("macro_economy MISSING from check result!")

    # 2. Deep Scan (Tier 2/3)
    logger.info("\n--- 2. Running Deep Quality Scan (Tier 2/3) ---")
    
    def progress(curr, total, msg):
        print(f"\rProgress: {curr}/{total} - {msg}", end="")
        
    deep_res = await dp.run_quality_scan(sample_size=3, progress_callback=progress)
    print("\n")
    logger.info(f"Deep Scan Result: {deep_res}")
    
    if deep_res['score'] > 0:
        logger.info("✅ Deep Scan returned valid score.")
    else:
        logger.warning("⚠️ Deep Scan returned 0 score (might be empty DB or error).")

    await dp.close()

if __name__ == "__main__":
    asyncio.run(main())
