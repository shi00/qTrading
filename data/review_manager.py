import logging
import pandas as pd
import datetime
import aiosqlite
from data.cache_manager import CacheManager
from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class ReviewManager:
    """
    Manages the 'Verification' and 'Correction' phases of the AI loop.
    1. Calculates Actual Returns (T+1, T+5).
    2. Labels predictions (Win/Loss).
    3. Extracts 'Lessons' for Prompt Context.
    """

    def __init__(self):
        self.cache = CacheManager()
        self.api = TushareClient()
        self.config = ConfigHandler()

    async def run_review(self):
        """
        Main entry point: Review all pending predictions.
        Should be run daily after 16:00.
        """
        logger.info("[Review] Starting daily review...")
        
        # 1. Get all recent predictions without results
        pending_df = await self._get_pending_predictions()
        if pending_df.empty:
            logger.info("[Review] No pending predictions to review.")
            return

        current_date = datetime.datetime.now().strftime('%Y%m%d')
        updated_count = 0

        # 2. Check each prediction
        for _, row in pending_df.iterrows():
            ts_code = row['ts_code']
            pred_date = row['trade_date'] # The date the prediction was made (Close)
            
            # We need prices for T+1, T+2...
            # Get next trading days from Tushare
            # Since we assume 'pred_date' is the date of analysis (after close),
            # T+1 is the NEXT trading day.
            
            # Fetch prices since pred_date
            df_quotes = await self.cache.get_daily_quotes(start_date=pred_date, ts_code=ts_code)
            if df_quotes.empty:
                # Try fetching from API if not in cache (e.g. today's close)
                # In a real system, we assume sync_daily_market_snapshot has run.
                continue
                
            df_quotes = df_quotes.sort_values('trade_date')
            
            # Identify T+0 (Analysis Day), T+1, T+2...
            # Note: df_quotes includes pred_date (T+0)
            
            try:
                # Find the index of prediction date
                t0_row = df_quotes[df_quotes['trade_date'] == pred_date]
                if t0_row.empty:
                    continue
                    
                t0_idx = df_quotes.index.get_loc(t0_row.index[0])
                
                # Check T+1
                t1_close = None
                t1_pct = None
                if len(df_quotes) > t0_idx + 1:
                    t1_row = df_quotes.iloc[t0_idx + 1]
                    t1_close = t1_row['close']
                    t1_pct = t1_row['pct_chg']
                    
                # Check T+5 (optional, simpler logic here just for T+1 focus first)
                
                if t1_pct is not None:
                    # Determine Result (Relative Return)
                    # We need Index Return for this date to calculate Alpha.
                    # Default benchmark: 000300.SH (CSI 300) or 000001.SH (Shanghai Composite)
                    index_code = '000001.SH' 
                    
                    # Fetch Index Quote for T+1
                    # Since we don't cache index daily quotes in the same efficient way yet (or handled by quotes table?),
                    # We might need to fetch it dynamically or ensure we sync benchmarks.
                    # For now, let's fetch on demand via API if missing.
                    
                    index_pct = 0.0
                    try:
                        # Tushare index_daily
                        df_idx = await self.api.get_index_daily(ts_code=index_code, start_date=t1_row['trade_date'], end_date=t1_row['trade_date'])
                        if not df_idx.empty:
                            index_pct = float(df_idx.iloc[0]['pct_chg'])
                    except:
                        pass # Network fail, assume 0 benchmark
                    
                    # Alpha Calculation
                    alpha = t1_pct - index_pct
                    
                    label = "DRAW"
                    # Win Condition: Alpha > 0 (Outperform Marker) AND Absolute > -2% (Avoid disaster)
                    # Strict: Must make money OR outperform significantly
                    
                    if alpha > 0.5:
                        label = "WIN"
                    elif alpha < -0.5:
                        label = "LOSS"
                    
                    # Log it
                    await self._update_result(row['id'], t1_pct, label, index_pct)
                    updated_count += 1
                    logger.info(f"[Review] {ts_code}: Stock {t1_pct}% vs Index {index_pct}% = Alpha {alpha:.2f}% -> {label}")
                    
            except Exception as e:
                logger.error(f"[Review] Error reviewing {ts_code}: {e}")

        logger.info(f"[Review] Completed. Updated {updated_count} records.")

    async def _get_pending_predictions(self):
        """
        Get predictions from last 10 days that have no result yet.
        Corner cases handled:
        - Empty DB: Returns empty DataFrame
        - Missing columns: Uses safe column access
        - Date edge cases: Uses 10-day lookback window
        """
        date_threshold = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime('%Y%m%d')
        
        try:
            # Query screening_history for pending reviews
            sql = '''
                SELECT id, trade_date, ts_code, ai_score, ai_reason 
                FROM screening_history 
                WHERE trade_date >= ? 
                  AND prediction_result IS NULL
                  AND ai_score > 0
                ORDER BY trade_date DESC
            '''
            
            async with aiosqlite.connect(self.cache.db_path) as db:
                async with db.execute(sql, (date_threshold,)) as cursor:
                    rows = await cursor.fetchall()
                    if not rows:
                        return pd.DataFrame()
                    
                    cols = ['id', 'trade_date', 'ts_code', 'ai_score', 'ai_reason']
                    return pd.DataFrame(rows, columns=cols)
                    
        except Exception as e:
            logger.error(f"[Review] Error fetching pending predictions: {e}")
            return pd.DataFrame()

    async def get_learning_context(self, limit=3):
        """
        Extract 'Best Wins' and 'Worst Losses' for Prompt Injection.
        Returns formatted XML string for few-shot learning.
        
        Corner cases:
        - No history: Returns minimal XML
        - All wins/no losses: Handles gracefully
        - DB errors: Returns empty context (non-blocking)
        """
        wins = []
        losses = []
        
        try:
            # Query top wins (highest positive alpha)
            sql_wins = '''
                SELECT ts_code, name, t1_pct, ai_score, ai_reason
                FROM screening_history 
                WHERE prediction_result = 'WIN' AND t1_pct IS NOT NULL
                ORDER BY t1_pct DESC
                LIMIT ?
            '''
            
            sql_losses = '''
                SELECT ts_code, name, t1_pct, ai_score, ai_reason
                FROM screening_history 
                WHERE prediction_result = 'LOSS' AND t1_pct IS NOT NULL
                ORDER BY t1_pct ASC
                LIMIT ?
            '''
            
            async with aiosqlite.connect(self.cache.db_path) as db:
                # Fetch wins
                async with db.execute(sql_wins, (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        wins.append({
                            'code': row[0],
                            'name': row[1],
                            'pct': row[2],
                            'score': row[3],
                            'reason': row[4][:50] if row[4] else ''
                        })
                
                # Fetch losses
                async with db.execute(sql_losses, (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        losses.append({
                            'code': row[0],
                            'name': row[1],
                            'pct': row[2],
                            'score': row[3],
                            'reason': row[4][:50] if row[4] else ''
                        })
                        
        except Exception as e:
            logger.warning(f"[Review] Error fetching learning context: {e}")
            # Non-blocking: return empty context on error
        
        # Build XML
        xml = "<history_context>\n"
        
        if wins:
            xml += "  [Success Examples - Learn from these]\n"
            for w in wins:
                xml += f"  - {w['code']} ({w['name']}): Predicted score {w['score']}, actual +{w['pct']:.1f}%\n"
                
        if losses:
            xml += "  [Mistakes to Avoid - Do NOT repeat]\n"
            for l in losses:
                xml += f"  - {l['code']} ({l['name']}): Predicted score {l['score']}, actual {l['pct']:.1f}%\n"
        
        if not wins and not losses:
            xml += "  No historical data available yet.\n"
                
        xml += "</history_context>"
        return xml

    async def _update_result(self, record_id, pct, label, index_pct=0.0):
        """Update DB with result"""
        # We could store index_pct if schema supported it, for now just use it for logging above
        sql = "UPDATE screening_history SET t1_pct=?, prediction_result=? WHERE id=?"
        await self.cache.queue.put((sql, (pct, label, record_id), False))

    async def save_results(self, strategy_name, df):
        """
        Save screening results to history for future review.
        """
        if df is None or df.empty:
            return

        current_date = datetime.datetime.now().strftime('%Y%m%d')
        
        # Prepare data for bulk insert
        # Schema: trade_date, strategy_name, ts_code, name, close, pct_chg, ai_score, ai_reason
        
        records = []
        for _, row in df.iterrows():
            ts_code = row.get('ts_code')
            if not ts_code: continue
            
            # Extract AI fields if available
            ai_score = row.get('ai_score', 0)
            # Handle NaN/None for Score
            try:
                ai_score = int(ai_score) if pd.notnull(ai_score) else 0
            except:
                ai_score = 0
                
            ai_reason = row.get('ai_reason', '')
            if pd.isnull(ai_reason): ai_reason = ''
            
            records.append((
                current_date, 
                strategy_name, 
                ts_code,
                row.get('name', ''),
                row.get('close', 0),
                row.get('pct_chg', 0),
                ai_score,
                str(ai_reason)
            ))
            
        if not records:
            return

        sql = '''
            INSERT OR REPLACE INTO screening_history 
            (trade_date, strategy_name, ts_code, name, close, pct_chg, ai_score, ai_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        '''
        
        # We need to access CacheManager queue
        # CacheManager queue items: (sql, params, is_many)
        await self.cache.queue.put((sql, records, True))
        logger.info(f"[Review] Saved {len(records)} predictions for {strategy_name}")
