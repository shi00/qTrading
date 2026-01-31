import pandas as pd
import logging
import datetime
from strategies.base_strategy import BaseStrategy
from utils.technical_analysis import TechnicalAnalysis
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class OversoldStrategy(BaseStrategy):
    """
    RSI Oversold Rebound Strategy
    Screening Criteria: RSI(6) < 20 (Configurable)
    Logic:
    1. Fetch historical quotes (30 days) for all candidates.
    2. Calculate RSI(6).
    3. Filter stocks with RSI < 20.
    4. Sort by RSI (ascending), favoring most oversold.
    """
    def __init__(self):
        super().__init__("strategy_oversold_name", "strategy_oversold_desc")
        # RSI Threshold could be configurable in future
        self.rsi_threshold = 20

    async def filter(self, context):
        """
        Execute Strategy Filter
        """
        # 1. Get Base Data (Snapshot) from Context
        snapshot_df = context.get('screening_data')
        dp = context.get('data_processor')
        
        if snapshot_df is None or snapshot_df.empty:
            logger.warning("[OversoldStrategy] No snapshot data available.")
            return pd.DataFrame()

        if dp is None:
            logger.error("[OversoldStrategy] DataProcessor not found in context.")
            return pd.DataFrame()

        # 2. Prepare Date Range for History (RSI needs at least N+1 days)
        # We fetch 30 days to be safe for RSI(6)
        end_date = await dp.get_latest_trade_date()
        start_date = (datetime.datetime.strptime(end_date, "%Y%m%d") - datetime.timedelta(days=45)).strftime("%Y%m%d")
        
        logger.info(f"[OversoldStrategy] Fetching history from {start_date} to {end_date} for RSI calculation...")

        # 3. Fetch Historical Data (Batch) using DataProcessor/Cache
        # Optimization: We only need 'close' and 'adj_factor'
        # We fetch ALL quotes for the period. 
        # Note: If database is large, fetching *all* quotes for 30 days might be heavy (e.g. 5k stocks * 30 rows = 150k rows).
        # SQLite should handle 150k rows in milliseconds.
        
        try:
            history_df = await dp.cache.get_daily_quotes(start_date=start_date, end_date=end_date)
            
            if history_df is None or history_df.empty:
                logger.warning("[OversoldStrategy] No historical data found.")
                return pd.DataFrame()
                
            # 4. Group by Stock and Calculate RSI
            # Vectorized approach or GroupBy apply
            # GroupBy apply is cleaner
            
            # Filter to only stocks currently in snapshot (active stocks)
            valid_codes = set(snapshot_df['ts_code'])
            history_df = history_df[history_df['ts_code'].isin(valid_codes)]
            
            results = []
            
            # Group by ts_code
            grouped = history_df.groupby('ts_code')
            
            for ts_code, group in grouped:
                # Sort by date asc
                group = group.sort_values('trade_date', ascending=True)
                
                # Check minimum length
                if len(group) < 7: # Need at least 7 days for RSI 6
                    continue
                    
                # Calculate RSI
                rsi_val = TechnicalAnalysis.get_rsi(group, period=6)
                
                # Filter
                if rsi_val < self.rsi_threshold:
                    results.append({
                        'ts_code': ts_code,
                        'rsi_6': round(rsi_val, 2)
                    })
            
            if not results:
                logger.info("[OversoldStrategy] No stocks found matching RSI criteria.")
                return pd.DataFrame()
                
            # 5. Merge with Snapshot and Return
            rsi_df = pd.DataFrame(results)
            
            # Right join to keep RSI info and attach snapshot data
            # Inner join guarantees we have snapshot info
            final_df = pd.merge(snapshot_df, rsi_df, on='ts_code', how='inner')
            
            # Sort by RSI ascending (Most oversold first)
            final_df = final_df.sort_values('rsi_6', ascending=True)
            
            logger.info(f"[OversoldStrategy] Found {len(final_df)} oversold stocks.")
            return final_df
            
        except Exception as e:
            logger.error(f"[OversoldStrategy] Error during execution: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return pd.DataFrame()
