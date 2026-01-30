from strategies.all_strategies import BaseStrategy
import pandas as pd
import asyncio
import logging
import json
from data.news_fetcher import NewsFetcher
from data.ai_client import AIClient
from utils.technical_analysis import TechnicalAnalysis
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class AISelectionStrategy(BaseStrategy):
    def __init__(self):
        # Description clearly states it uses 30 candidates
        # Description clearly states it uses Top N candidates
        super().__init__("strategy_ai_active_name", "strategy_ai_active_desc")
        self.ai_client = AIClient()
        self.limit = ConfigHandler.get_ai_max_candidates()
        
    async def filter(self, context):
        if context is None:
            return pd.DataFrame()
        
        df = context.get('data')
        dp = context.get('data_processor')
        
        if df is None or df.empty:
            logger.warning("[AIStrategy] No data provided in context")
            return pd.DataFrame()

        # --- Step 1: Pre-Filter (The Sieve) ---
        # Logic: We want "Trade-able" stocks.
        min_turnover = ConfigHandler.get_strategy_min_turnover()
        
        # Rule: Listed, Not ST, Price > 2, Turnover > X% (Active), MA Trend Up?
        # choosing: Top N by 'turnover_rate' where 'pe_ttm' > 0 (profitable)
        
        # Filter profitable and active
        mask = (df['pe_ttm'] > 0) & (df['turnover_rate'] > min_turnover) & (df['list_status'] == 'L')
        candidates = df[mask].copy()
        
        # Sort by turnover_rate desc (Most active)
        candidates = candidates.sort_values('turnover_rate', ascending=False).head(self.limit)
        
        if candidates.empty:
            return pd.DataFrame()

        # --- Step 2: Parallel Analysis (The Feature Enrichment) ---
        
        # Fetch Global Context ONCE (Shadow Strategy)
        global_context = await NewsFetcher.get_us_major_moves()
        logger.info(f"[AIStrategy] Global Context: {global_context}")
        
        # Run all analysis in parallel with progress tracking
        logger.info(f"[AIStrategy] Analyzing {len(candidates)} stocks...")
        
        # Callbacks
        on_progress = context.get('on_progress')
        on_result = context.get('on_stream_result') or context.get('on_result')
        
        total_tasks = len(candidates)
        completed_count = 0
        
        # Initial Progress
        if on_progress:
            on_progress(0, total_tasks, "Initializing AI...")

        tasks = []
        # Map task to row to know which stock it is
        # But for as_completed we get futures.
        
        # Helper wrapper to return row with result
        async def analyze_wrapper(row_data):
            res = await self._analyze_single_stock(row_data, dp, global_context)
            return res, row_data

        for _, row in candidates.iterrows():
             tasks.append(analyze_wrapper(row))

        final_rows = []
        
        for future in asyncio.as_completed(tasks):
            try:
                res, row = await future
                completed_count += 1
                
                # Default "Thinking..." message or stock name
                stock_name = row['name']
                
                if isinstance(res, Exception) or res is None or res.get('score', 0) == 0:
                     if on_progress: on_progress(completed_count, total_tasks, f"Skipped {stock_name}")
                     continue
                
                # Valid Result
                row_dict = row.to_dict()
                row_dict['ai_score'] = res.get('score', 0)
                row_dict['ai_reason'] = res.get('summary', '')
                row_dict['thinking'] = res.get('thinking', '') # Pass thinking to UI
                
                final_rows.append(row_dict)
                
                # Trigger Stream Callback
                if on_result:
                    on_result(row_dict)
                    
                # Update Progress
                if on_progress:
                    on_progress(completed_count, total_tasks, f"Analyzed {stock_name} (Score: {row_dict['ai_score']})")

            except Exception as e:
                logger.error(f"Task error: {e}")
                completed_count += 1
        
        # Reconstruct DataFrame (redundant if streamed, but required for return)
        if not final_rows:
            return pd.DataFrame()
            
        result_df = pd.DataFrame(final_rows)
        return result_df.sort_values('ai_score', ascending=False)

    async def _analyze_single_stock(self, row, dp, global_context=""):
        """
        Helper for analysis
        """
        try:
            ts_code = row['ts_code']
            
            # 1. Get History (Last 100 days)
            # data_processor.get_stock_history is async? No, it uses cache/tushare sync mostly but wrapped?
            # Let's check data_processor signature.
            # get_stock_history calls tushare/cache sync methods usually, but let's check.
            # It was 'async def get_stock_history'.
            history_df = await dp.get_stock_history(ts_code, days=60)
            
            # 2. Calc Tech indicators
            # macd_signal, k, d, j
            trend_signal, _, _ = TechnicalAnalysis.get_macd(history_df)
            kdj_signal, k, d, j = TechnicalAnalysis.get_kdj(history_df)
            
            tech_context = {
                "macd_signal": trend_signal,
                "kdj_signal": kdj_signal,
                "k": round(k, 1),
                "j": round(j, 1)
            }
            
            # 3. Get News
            news = await NewsFetcher.get_stock_news(ts_code, limit=3)
            
            # 4. AI Inference
            # row is a Series, valid info
            stock_info = row.to_dict()
            
            ai_result = await self.ai_client.analyze_stock(stock_info, tech_context, news, global_context)
            return ai_result # {score, summary, ...}
            
        except Exception as e:
            logger.error(f"Single stock analysis failed for {row['ts_code']}: {e}")
            return None
