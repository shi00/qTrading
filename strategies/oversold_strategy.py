import pandas as pd
import polars as pl
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
        Execute Strategy Filter (Polars Optimized)
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
        # We fetch 45 days to be safe for RSI(6) and ensure enough data for smoothing
        end_date = await dp.get_latest_trade_date()
        start_date = (datetime.datetime.strptime(end_date, "%Y%m%d") - datetime.timedelta(days=45)).strftime("%Y%m%d")
        
        logger.info(f"[OversoldStrategy] Fetching history from {start_date} to {end_date} for RSI calculation...")

        # 3. Fetch Historical Data (Pandas format from CacheManager)
        try:
            valid_codes = snapshot_df['ts_code'].tolist()
            history_pdf = await dp.cache.get_daily_quotes(start_date=start_date, end_date=end_date, ts_code_list=valid_codes)
            
            if history_pdf is None or history_pdf.empty:
                logger.warning("[OversoldStrategy] No historical data found.")
                return pd.DataFrame()

            # 4. Polars Vectorized Calculation
            # Convert to Polars
            df = pl.from_pandas(history_pdf)
            
            # Helper: Calculate QFQ Close if available
            # Logic: close_qfq = close * (adj_factor / last_adj_factor)
            if 'adj_factor' in df.columns:
                # Calculate QFQ Close per group
                qfq_expr = (
                    pl.col('close') * 
                    (pl.col('adj_factor') / pl.col('adj_factor').last().over('ts_code'))
                ).alias('qfq_close')
                
                # Apply QFQ and sorting
                df = df.lazy().with_columns(qfq_expr)
            else:
                df = df.lazy().with_columns(pl.col('close').alias('qfq_close'))

            # Sort and Calculate RSI
            # We filter only active stocks (though cache fetch already did, strict equality check is good)
            # Calculate RSI over 'ts_code' window
            rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name='qfq_close', period=6, alias='rsi_6')
            
            # Execution Pipeline
            result_df = (
                df
                .sort(["ts_code", "trade_date"])
                .with_columns(rsi_expr.over("ts_code"))
                .filter(pl.col("trade_date") == end_date) # Take only the latest day's RSI
                .filter(pl.col("rsi_6") < self.rsi_threshold) # Filter threshold
                .collect() # Execute
            )
            
            if result_df.height == 0:
                logger.info("[OversoldStrategy] No stocks found matching RSI criteria.")
                return pd.DataFrame()
                
            # 5. Join with Snapshot and Return
            # Convert result back to Pandas for compatibility with UI
            rsi_pdf = result_df.select(['ts_code', 'rsi_6']).to_pandas()
            
            # Merge with snapshot
            final_df = pd.merge(snapshot_df, rsi_pdf, on='ts_code', how='inner')
            
            # Sort by RSI ascending (Most oversold first)
            final_df = final_df.sort_values('rsi_6', ascending=True)
            
            logger.info(f"[OversoldStrategy] Found {len(final_df)} oversold stocks.")
            return final_df
            
        except Exception as e:
            logger.error(f"[OversoldStrategy] Error during execution: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return pd.DataFrame()
