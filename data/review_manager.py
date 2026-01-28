import pandas as pd
import datetime
import logging
from data.cache_manager import CacheManager
from data.tushare_client import TushareClient

logger = logging.getLogger(__name__)

class ReviewManager:
    """
    Manages the "Self-Optimizing Loop":
    1. Save screening results daily.
    2. Track performance (T+1, T+5 returns).
    3. (Future) Analyze best strategies.
    """
    def __init__(self):
        self.cache = CacheManager()
        self.api = TushareClient()

    async def save_results(self, strategy_name, df):
        """Save today's screening results"""
        if df is None or df.empty:
            return
        
        # Get today's date or the date in the dataframe
        # Assuming df has 'trade_date' or we use current date
        # But for backtesting/manual run, we should probably check if Trade Date is valid
        # For now, let's use the current "Latest Trade Date" from Tushare or system
        
        # In ScreenerView, we might be running for a specific past date? 
        # Usually screener connects to "latest" data.
        
        try:
            # We assume the screening was done on the "latest available trade date"
            # Get latest trade date from cache or API
            trade_date = await self.cache.get_latest_trade_date()
            if not trade_date:
                trade_date = datetime.datetime.now().strftime('%Y%m%d')
            
            count = await self.cache.save_screening_result(df, strategy_name, trade_date)
            logger.info(f"[Review] Saved {count} results for {strategy_name} on {trade_date}")
            return count
        except Exception as e:
            logger.error(f"[Review] Error saving results: {e}")
            return 0

    async def update_performance(self):
        """
        Check pending reviews and update T+1, T+5 prices.
        Should be run daily after data sync.
        """
        pending = await self.cache.get_pending_reviews()
        if not pending:
            return
        
        logger.info(f"[Review] Updating performance for {len(pending)} records...")
        
        # Group by ts_code to batch fetch? Tushare daily api supports single code.
        # Efficient way: Get all relevant trade dates and fetch daily quotes for those dates?
        # Or just loop for now since pending shouldn't be huge (unless historical backfill)
        
        updates = []
        
        # We need to know "Next N Trading Days" for each record
        # This requires calendar
        
        # Cache calendar for performance
        cal_end_date = datetime.datetime.now().strftime('%Y%m%d')
        cal_start_date = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime('%Y%m%d')
        trade_dates = self.api.get_trade_dates(cal_start_date, cal_end_date)
        trade_dates.sort()
        
        for record in pending:
            try:
                record_date = record['trade_date']
                ts_code = record['ts_code']
                
                # Find index of record_date
                if record_date not in trade_dates:
                    continue
                    
                idx = trade_dates.index(record_date)
                
                # Check T+1
                t1_date = trade_dates[idx + 1] if idx + 1 < len(trade_dates) else None
                # Check T+5
                t5_date = trade_dates[idx + 5] if idx + 5 < len(trade_dates) else None
                
                t1_price = record['t1_price']
                t5_price = record['t5_price']
                t1_pct = record['t1_pct']
                t5_pct = record['t5_pct']
                
                changed = False
                
                # Fetch T+1 if needed and available
                if t1_date and t1_price is None:
                    # Check if we have data for t1_date in cache
                    # Optimization: Use cache instead of API if possible
                    df = await self.cache.get_daily_quotes(start_date=t1_date, end_date=t1_date, ts_code=ts_code)
                    if not df.empty:
                        t1_price = df.iloc[0]['close']
                        # Calculate pct change from entry (close)
                        if record['close']:
                            t1_pct = (t1_price - record['close']) / record['close'] * 100
                        changed = True
                
                # Fetch T+5 if needed and available
                if t5_date and t5_price is None:
                    df = await self.cache.get_daily_quotes(start_date=t5_date, end_date=t5_date, ts_code=ts_code)
                    if not df.empty:
                        t5_price = df.iloc[0]['close']
                        if record['close']:
                            t5_pct = (t5_price - record['close']) / record['close'] * 100
                        changed = True
                
                if changed:
                    updates.append((t1_price, t1_pct, t5_price, t5_pct, record['id']))
                    
            except Exception as e:
                logger.error(f"[Review] Error processing record {record['id']}: {e}")
                continue
                
        if updates:
            await self.cache.update_screening_performance(updates)
            logger.info(f"[Review] Updated {len(updates)} records.")
