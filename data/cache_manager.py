import aiosqlite
import asyncio
import pandas as pd
import datetime
import os
import config
from utils.config_handler import ConfigHandler
import logging

logger = logging.getLogger(__name__)

class CacheManager:
    _instance = None
    
    def __new__(cls, db_path=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path=None):
        if self._initialized:
            return
            
        self.db_path = db_path or config.DB_PATH
        self.queue = asyncio.Queue(maxsize=ConfigHandler.get_db_queue_size()) # Ring buffer / Queue
        self.writer_task = None
        self._running = False
        self._closing = False # New flag to prevent restart during close
        
        # Maintenance Mode Control
        self._maintenance_event = asyncio.Event()
        self._maintenance_event.set() # Default: Ready (Green light)
        
        self._initialized = True

    async def start(self):
        """Start the background DB writer task"""
        if self._closing:
            return # Don't start if we are closing
            
        if not self._running:
            self._running = True
            self.writer_task = asyncio.create_task(self._db_writer_loop())
            logger.info("[CacheManager] DB Writer started.")

    async def close(self):
        """Stop the writer task and wait for queue to empty"""
        if self._closing:
            return 
            
        self._closing = True
        
        if self._running:
            logger.info("[CacheManager] Stopping DB Writer... waiting for queue to empty.")
            # We assume the producer has stopped producing before calling close()
            # or we accept that some late items might be rejected if we enforced it strictly.
            await self.queue.put(None) # Sentinel
            
            if self.writer_task:
                try:
                    await self.writer_task
                except Exception as e:
                    logger.error(f"[CacheManager] Writer task error during close: {e}")
            
            self._running = False
            self._closing = False # Reset if we want to restart later? Or keep closed.
            self._running = False
            self._closing = False # Reset if we want to restart later? Or keep closed.
            logger.info("[CacheManager] DB Writer stopped.")

    async def wait_for_maintenance(self):
        """Wait if database is in maintenance mode"""
        if not self._maintenance_event.is_set():
            # Only log if we actually have to wait, to reduce noise
            logger.info("[CacheManager] Waiting for maintenance to complete...")
            await self._maintenance_event.wait()

    async def _db_writer_loop(self):
        """Background loop to consume SQL tasks and write to DB"""
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrency
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.commit()
            
            while True:
                try:
                    # Get first task
                    task = await self.queue.get()
                    
                    if task is None: # Sentinel
                        self.queue.task_done()
                        break
                    
                    # Task structure: (sql_or_func, params_or_args, task_type)
                    # task_type: True (executemany), False (execute), 'SCRIPT' (executescript), 'FUNC' (function(db, *args))
                    
                    # Check for Special Tasks First (Non-batched)
                    item_payload, item_args, item_type = task
                    
                    if item_type == 'FUNC':
                        # Execute python function with DB connection
                        # Payload is callable, args is list/tuple
                        try:
                            # Note: We pass 'db' as first arg, then unpacking item_args
                            func = item_payload
                            if asyncio.iscoroutinefunction(func):
                                await func(db, *item_args)
                            else:
                                func(db, *item_args)
                        except Exception as e:
                            logger.error(f"[CacheManager] Func execution failed: {e}")
                        
                        self.queue.task_done()
                        continue
                        
                    if item_type == 'SCRIPT':
                        # Execute SQL Script
                        try:
                            await db.executescript(item_payload)
                            await db.commit()
                        except Exception as e:
                            logger.error(f"[CacheManager] Script execution failed: {e}")
                        
                        self.queue.task_done()
                        continue

                    # === Normal Batch Processing (INSERT/UPDATE/DELETE) ===
                    MAX_BATCH_ROWS = 20000
                    current_batch_rows = 0
                    
                    # Calculate rows for the first task
                    current_batch_rows += len(item_args) if item_type is True else 1
                    
                    tasks = [task]
                    
                    # Batch retrieval (drain queue up to limit)
                    try:
                        for _ in range(50): 
                            if current_batch_rows >= MAX_BATCH_ROWS:
                                break
                            
                            t = self.queue.get_nowait()
                            if t is None:
                                tasks.append(t)
                                break
                            
                            # Check if next task is compatible with batching
                            # If it's a special task, we shouldn't batch it with SQLs generally, or we just execute batch first then special.
                            # For simplicity, if we encounter a special task (FUNC/SCRIPT), we put it back or handle it separately?
                            # Better: execute current batch, then loop back.
                            t_payload, t_args, t_type = t
                            if t_type == 'FUNC' or t_type == 'SCRIPT':
                                # Push back to front of queue? No, asyncio.Queue doesn't support push_front.
                                # So we MUST process it now or just add to 'tasks' list but execute differently?
                                # Let's handle mixed batch by iterating.
                                pass 
                            
                            # Just add to tasks list and iterate logic handles types
                            # But row count logic only applies to SQL
                            row_count = len(t_args) if t_type is True else 1
                            current_batch_rows += row_count
                            tasks.append(t)
                            
                    except asyncio.QueueEmpty:
                        pass
                    
                    # Execute batch
                    transaction_active = False
                    try:
                        for item in tasks:
                            if item is None: continue
                            
                            sql, params, task_type = item
                            
                            if task_type == 'FUNC':
                                if transaction_active: 
                                    await db.commit()
                                    transaction_active = False
                                if asyncio.iscoroutinefunction(sql):
                                    await sql(db, *params)
                                else:
                                    sql(db, *params)
                            
                            elif task_type == 'SCRIPT':
                                if transaction_active: 
                                    await db.commit()
                                    transaction_active = False
                                await db.executescript(sql)
                                await db.commit()
                                
                            elif task_type is True: # executemany
                                await db.executemany(sql, params)
                                transaction_active = True
                                
                            else: # execute (False)
                                await db.execute(sql, params)
                                transaction_active = True
                        
                        if transaction_active:
                            await db.commit()
                            
                    except Exception as e:
                        logger.error(f"[CacheManager] Batch Error: {e}")
                        # Rollback active transaction
                        if transaction_active:
                            try:
                                await db.rollback()
                            except: pass
                        
                        # Fallback: Retry items one by one (simplistic)
                        # NOTE: For FUNC/SCRIPT, re-running might be side-effect heavy, but usually safe if idempotent.
                        # We stick to simple retry for SQLs mostly.
                        # For now, just log drop.
 
                    # Mark all as done
                    for _ in tasks:
                        self.queue.task_done()
                        
                    # Handle stop signal
                    if any(t is None for t in tasks):
                        break
                        
                except Exception as e:
                    logger.error(f"[CacheManager] Critical Loop Error: {e}")
                    await asyncio.sleep(1)

    async def _execute_schema_internal(self, db, future=None):
        """Internal method to execute schema on existing connection"""
        logger.info(f"[CacheManager] Initializing database from schema (Internal)...")
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()
            
            await db.executescript(schema_sql)
            
            # Migrations
            try:
                await db.execute("ALTER TABLE stock_basic ADD COLUMN list_status TEXT")
            except Exception: pass
            
            try:
                await db.execute("ALTER TABLE daily_quotes ADD COLUMN adj_factor REAL")
            except Exception: pass
            
            try:
                await db.execute("ALTER TABLE screening_history ADD COLUMN ai_score INTEGER")
                await db.execute("ALTER TABLE screening_history ADD COLUMN ai_reason TEXT")
                await db.execute("ALTER TABLE screening_history ADD COLUMN prediction_result TEXT")
            except Exception: pass
            
            await db.commit()
            logger.info("[CacheManager] Schema executed successfully.")
            
            if future:
                future.set_result(True)
                
        except Exception as e:
            logger.error(f"[CacheManager] Schema Error: {e}")
            if future:
                future.set_exception(e)

    async def init_db(self):
        """Initialize database tables via Queue"""
        await self.start()
        
        # Create a future to wait for initialization
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        
        # Queue the internal function
        # Task: (Function, [args...], 'FUNC')
        # Arg 1 is always 'db' passed by worker, so we pass extra args here
        await self.queue.put((self._execute_schema_internal, [future], 'FUNC'))
        
        # Wait for it to finish
        await future

    async def clear_all_cache(self):
        """Rebuild Database: Drop all tables and Re-initialize"""
        print("[Cache] Queuing Database Rebuild...")
        
        # Enter Maintenance Mode (Block readers)
        self._maintenance_event.clear()
        try:
            # 1. Drop Tables
            tables = ["daily_quotes", "daily_indicators", "financial_reports", "moneyflow_daily", 
                      "northbound_holding", "sync_status", "screening_history", "top_list", "block_trade", 
                      "market_news", "trade_cal", "stock_basic"]
            
            drop_script = "\n".join([f"DROP TABLE IF EXISTS {t};" for t in tables])
            
            # Queue Drop Script
            await self.queue.put((drop_script, [], 'SCRIPT'))
            
            # 2. Re-Initialize Schema
            # We don't necessarily need to wait for this one to finish synchronously if we don't return anything,
            # but usage usually implies we want fresh state ready.
            # Let's use a future again just to be sure we print "Done" at right time.
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            
            await self.queue.put((self._execute_schema_internal, [future], 'FUNC'))
            
            await future
            print("[Cache] Database Rebuild Complete.")
        
        finally:
            # Exit Maintenance Mode (Unblock readers)
            self._maintenance_event.set()

    # ========== Review System ==========
    async def get_pending_reviews(self):
        """Get predictions that need T+1 review (no result yet)"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            # Fetch recrods where prediction_result is NULL and crated > 1 day ago
            # Actually, we just fetch all NULLs and let logic filter by date
            query = "SELECT * FROM screening_history WHERE prediction_result IS NULL"
            async with db.execute(query) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_learning_examples(self, limit=3):
        """
        Get best wins and worst losses for Few-Shot Learning.
        Returns: (wins_df, losses_df)
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            # Get Wins
            async with db.execute(
                "SELECT * FROM screening_history WHERE prediction_result='WIN' ORDER BY t1_pct DESC LIMIT ?", 
                (limit,)
            ) as cursor:
                cols = [desc[0] for desc in cursor.description]
                wins = await cursor.fetchall()
                wins_df = pd.DataFrame(wins, columns=cols)
                
            # Get Losses
            async with db.execute(
                "SELECT * FROM screening_history WHERE prediction_result='LOSS' ORDER BY t1_pct ASC LIMIT ?", 
                (limit,)
            ) as cursor:
                cols = [desc[0] for desc in cursor.description]
                losses = await cursor.fetchall()
                losses_df = pd.DataFrame(losses, columns=cols)
                
            return wins_df, losses_df

    # ========== Stock Basic ==========
    # ========== Helper for Thread Offloading ==========
    @staticmethod
    def _prepare_data_params(df, cols, date_cols=None):
        """
        Prepare DataFrame for SQL insertion.
        This method is CPU-intensive (copy, filling, tolist) so it should run in a thread.
        """
        if df is None or df.empty:
            return None
            
        # Create a copy to avoid SettingWithCopyWarning on original df
        df = df.copy()
        
        # Ensure all required columns exist
        for col in cols:
            if col not in df.columns:
                df[col] = None
                
        # Handle date columns if needed (e.g. ensure string format)
        if date_cols:
            for col in date_cols:
                if col in df.columns:
                    # Generic safety: ensure it's string
                    df[col] = df[col].astype(str)

        # Convert to list of lists (heavy operation)
        return df[cols].values.tolist()

    # ========== Stock Basic ==========
    async def save_stock_basic(self, df):
        """Save stock basic info (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'symbol', 'name', 'area', 'industry', 'market', 'list_date', 'list_status', 'updated_at']
        
        # Add timestamp
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # We need to add updated_at to df before offloading? 
        # No, DFs are mutable but we shouldn't mute input df. 
        # Better to let helper handle it? Or start thread with copy.
        # Actually simplest is: assign constant columns here (fast), then offload heavy stuff.
        # But `df['updated_at']` modifies df. 
        # Let's do a lightweight copy here? Or just let thread do it.
        # We can pass `now` to helper? No, helper is generic.
        # We can just do `df['updated_at'] = now` here. It's fast (broadcasting).
        # The expensive part is `values.tolist()` and `copy` of full data if strict.
        
        # Safe strategy:
        # 1. Modify DF here (it's fast)
        df = df.copy() # Shallow copy of index/columns
        df['updated_at'] = now
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO stock_basic 
                (ts_code, symbol, name, area, industry, market, list_date, list_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        # Offload heavy conversion
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            # Executor likely shut down
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_stock_basic(self):
        """Get all stock basic info"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM stock_basic") as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Daily Quotes ==========
    async def save_daily_quotes(self, df):
        """Save daily OHLCV quotes (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 
                'pre_close', 'change', 'pct_chg', 'vol', 'amount', 'adj_factor']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO daily_quotes 
                (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_daily_quotes(self, start_date=None, end_date=None, ts_code=None):
        """Get daily quotes with optional filters"""
        await self.wait_for_maintenance()
        query = "SELECT * FROM daily_quotes WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND trade_date <= ?"
            params.append(end_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY trade_date DESC"
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_latest_trade_date(self):
        """Get the most recent trade date in cache"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM daily_quotes")
            result = await cursor.fetchone()
            return result[0] if result else None

    async def get_cached_trade_dates(self):
        """Get all trade dates that already exist in cache (for incremental sync)"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date")
            rows = await cursor.fetchall()
            return set(row[0] for row in rows)

    async def get_cached_indicator_dates(self):
        """Get all dates that have indicator data (for integrity check)"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT DISTINCT trade_date FROM daily_indicators")
            rows = await cursor.fetchall()
            return set(row[0] for row in rows)

    async def get_sync_stats(self):
        """Get sync statistics for UI display"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}
            
            # Quotes count and date range
            cursor = await db.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_quotes"
            )
            row = await cursor.fetchone()
            stats['quotes_count'] = row[0] or 0
            stats['quotes_min_date'] = row[1]
            stats['quotes_max_date'] = row[2]
            
            # Unique dates count
            cursor = await db.execute("SELECT COUNT(DISTINCT trade_date) FROM daily_quotes")
            row = await cursor.fetchone()
            stats['quotes_dates'] = row[0] or 0
            
            # Stock count
            cursor = await db.execute("SELECT COUNT(*) FROM stock_basic")
            row = await cursor.fetchone()
            stats['stock_count'] = row[0] or 0
            
            return stats

    # ========== Daily Indicators ==========
    async def save_daily_indicators(self, df):
        """Save daily valuation indicators (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
                'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv', 'total_share',
                'float_share', 'free_share', 'turnover_rate', 'turnover_rate_f']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO daily_indicators 
                (ts_code, trade_date, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
                 total_mv, circ_mv, total_share, float_share, free_share, turnover_rate, turnover_rate_f)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        # Offload heavy conversion
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_latest_indicators(self, trade_date=None):
        """Get latest indicators for all stocks"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            if trade_date is None:
                cursor = await db.execute("SELECT MAX(trade_date) FROM daily_indicators")
                result = await cursor.fetchone()
                trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            async with db.execute("SELECT * FROM daily_indicators WHERE trade_date = ?", (trade_date,)) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def save_market_news(self, news_item):
        """
        Save a single news item to DB
        news_item: dict with content, tags, publish_time, source
        """
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        sql = '''
            INSERT OR IGNORE INTO market_news 
            (content, tags, publish_time, source, created_at)
            VALUES (?, ?, ?, ?, ?)
        '''
        params = (
            news_item.get('content'),
            news_item.get('tags'),
            news_item.get('publish_time'),
            news_item.get('source', 'Sina'),
            now
        )
        await self.queue.put((sql, params, False))

    # ========== Trade Calendar ==========
    async def save_trade_cal(self, df):
        """Save trade calendar dataframe (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['cal_date', 'exchange', 'is_open', 'pretrade_date']
        
        # Ensure correct types can be done here or in helper
        # Let's do a quick column fix here if needed, or helper
        # Specifically 'is_open' to int. 
        # Helper is generic. We better do specific type fixes here if simple.
        # But 'astype' IS pandas op. 
        # We can do: 
        df = df.copy() 
        if 'is_open' in df.columns:
            df['is_open'] = df['is_open'].astype(int)

        if not self._running and not self._closing:
            await self.start()
            
        sql = '''
            INSERT OR REPLACE INTO trade_cal
            (cal_date, exchange, is_open, pretrade_date)
            VALUES (?, ?, ?, ?)
        '''
        
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
        """Get trade calendar from cache"""
        await self.wait_for_maintenance()
        query = "SELECT * FROM trade_cal WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND cal_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND cal_date <= ?"
            params.append(end_date)
        if is_open is not None:
              query += " AND is_open = ?"
              params.append(int(is_open))
              
        query += " ORDER BY cal_date ASC"
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Financial Reports ==========
    async def save_financial_reports(self, df):
        """Save quarterly financial reports (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue',
                'revenue', 'n_income', 'n_income_attr_p', 'total_assets', 'total_liab',
                'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin',
                'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO financial_reports 
                (ts_code, end_date, ann_date, report_type, total_revenue, revenue,
                 n_income, n_income_attr_p, total_assets, total_liab,
                 total_hldr_eqy_exc_min_int, roe, roe_dt, grossprofit_margin,
                 netprofit_margin, debt_to_assets, or_yoy, netprofit_yoy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_latest_financials(self):
        """Get latest financial report for each stock"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            # Get latest report per stock
            query = '''
                SELECT f.* FROM financial_reports f
                INNER JOIN (
                    SELECT ts_code, MAX(end_date) as max_date 
                    FROM financial_reports 
                    GROUP BY ts_code
                ) latest ON f.ts_code = latest.ts_code AND f.end_date = latest.max_date
            '''
            async with db.execute(query) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_cached_financial_records(self, period=None):
        """
        Get all cached financial records (ts_code, end_date pairs) for breakpoint resume.
        
        :param period: Optional specific end_date to filter by
        :return: Set of (ts_code, end_date) tuples that already exist in cache
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            if period:
                cursor = await db.execute(
                    "SELECT ts_code, end_date FROM financial_reports WHERE end_date = ?",
                    (period,)
                )
            else:
                cursor = await db.execute(
                    "SELECT ts_code, end_date FROM financial_reports"
                )
            rows = await cursor.fetchall()
            return set((row[0], row[1]) for row in rows)

    # ========== Sync Status ==========
    async def update_sync_status(self, table_name, last_data_date, record_count, status='success'):
        """Update sync status for a table"""
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if not self._running and not self._closing:
            await self.start()
            
        sql = '''
                INSERT OR REPLACE INTO sync_status 
                (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
        params = (table_name, now, last_data_date, record_count, status, now)
        await self.queue.put((sql, params, False)) # Single execution

    async def get_sync_status(self, table_name=None):
        """Get sync status for tables"""
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            if table_name:
                async with db.execute("SELECT * FROM sync_status WHERE table_name = ?", (table_name,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        cols = [desc[0] for desc in cursor.description]
                        return dict(zip(cols, row))
                    return None
            else:
                async with db.execute("SELECT * FROM sync_status") as cursor:
                    cols = [desc[0] for desc in cursor.description]
                    rows = await cursor.fetchall()
                    return pd.DataFrame(rows, columns=cols)

    async def check_financial_coverage(self):
        """
        Audit data integrity: Check how many stocks have financial reports.
        Returns:
            dict: stats {total_stocks, with_financials, coverage_ratio}
            list: missing_ts_codes
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Total Stocks (Active)
            # Assuming stock_basic has all stocks we care about
            async with db.execute("SELECT ts_code, name FROM stock_basic") as cursor:
                all_stocks = await cursor.fetchall()
                
            if not all_stocks:
                return {'total': 0, 'covered': 0, 'ratio': 0.0}, []
                
            all_codes = {row[0] for row in all_stocks}
            total_count = len(all_codes)
            
            # 2. Stocks with ANY financial record
            async with db.execute("SELECT DISTINCT ts_code FROM financial_reports") as cursor:
                covered_stocks = await cursor.fetchall()
                
            covered_codes = {row[0] for row in covered_stocks}
            covered_count = len(covered_codes)
            
            # 3. Calculate Diff
            missing_codes = list(all_codes - covered_codes)
            ratio = (covered_count / total_count) if total_count > 0 else 0.0
            
            stats = {
                'total': total_count,
                'covered': covered_count,
                'ratio': ratio,
                'missing_count': len(missing_codes)
            }
            
            return stats, missing_codes

    # ========== Screening Query ==========
    async def get_screening_data(self, trade_date=None):
        """
        Get merged data for screening: quotes + indicators + financials
        This is the main method used by screening strategies.
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            # Determine trade date
            if trade_date is None:
                cursor = await db.execute("SELECT MAX(trade_date) FROM daily_indicators")
                result = await cursor.fetchone()
                trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            # Join quotes, indicators, and latest financials
            query = '''
                SELECT 
                    b.ts_code, b.name, b.industry, b.list_date, b.list_status,
                    q.trade_date, q.close, q.pct_chg, q.vol, q.amount,
                    i.pe_ttm, i.pb, i.ps_ttm, i.dv_ttm, i.total_mv, i.circ_mv, i.turnover_rate,
                    f.roe, f.grossprofit_margin, f.debt_to_assets, f.or_yoy, f.netprofit_yoy
                FROM stock_basic b
                LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = ?
                LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = ?
                LEFT JOIN (
                    SELECT f1.* FROM financial_reports f1
                    INNER JOIN (
                        SELECT ts_code, MAX(end_date) as max_date 
                        FROM financial_reports GROUP BY ts_code
                    ) f2 ON f1.ts_code = f2.ts_code AND f1.end_date = f2.max_date
                ) f ON b.ts_code = f.ts_code
                WHERE q.close IS NOT NULL
            '''
            
            async with db.execute(query, (trade_date, trade_date)) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Money Flow ==========
    async def save_moneyflow(self, df):
        """Save daily money flow data (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'buy_sm_vol', 'buy_sm_amount', 'sell_sm_amount',
                'buy_md_amount', 'sell_md_amount', 'buy_lg_amount', 'sell_lg_amount',
                'buy_elg_amount', 'sell_elg_amount', 'net_mf_vol', 'net_mf_amount']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO moneyflow_daily 
                (ts_code, trade_date, buy_sm_vol, buy_sm_amount, sell_sm_amount,
                 buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount,
                 buy_elg_amount, sell_elg_amount, net_mf_vol, net_mf_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_moneyflow(self, trade_date=None, ts_code=None):
        """Get money flow data"""
        query = "SELECT * FROM moneyflow_daily WHERE 1=1"
        params = []
        
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY trade_date DESC"
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Northbound Holding ==========
    async def save_northbound(self, df):
        """Save northbound (HK Connect) holding data (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'name', 'vol', 'ratio', 'exchange']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO northbound_holding 
                (ts_code, trade_date, name, vol, ratio, exchange)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_northbound(self, trade_date=None, ts_code=None):
        """Get northbound holding data"""
        query = "SELECT * FROM northbound_holding WHERE 1=1"
        params = []
        
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY ratio DESC"  # Order by holding ratio
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Screening History (Review System) ==========
    
    async def save_screening_result(self, df, strategy_name, trade_date):
        """Save screening results to history (Offloaded to Thread... wait, records is manual list loop?)"""
        if df is None or df.empty:
            return 0
            
        # This method iterates df rows manually. 
        # This IS heavy if df is large.
        
        async def _prepare_screening_records():
             # Inner helper or use default executor
             records = []
             # We can't pickle async def inner logic easily for executor if not picklable.
             # But df iteration is sync.
             # Let's define a separate static helper if we want to offload.
             # Or just inline it if it's small.
             pass
        
        # Current logic:
        # records = []
        # for _, row in df.iterrows(): ...
        # If screening results are small (e.g. 50 stocks), strictly not needed.
        # But for consistency, let's offload it.
        
        # New Helper: _prepare_screening_params(df, strategy_name, trade_date)
        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, self._prepare_screening_params, df, strategy_name, trade_date)
        except RuntimeError:
            return 0

        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR IGNORE INTO screening_history 
                (trade_date, strategy_name, ts_code, name, close, pct_chg)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
        # Note: records is a list of tuples, so we use True for is_many
        if records:
            await self.queue.put((sql, records, True))
            return len(records)
        return 0
        
    @staticmethod
    def _prepare_screening_params(df, strategy_name, trade_date):
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            records.append((
                trade_date,
                strategy_name,
                row['ts_code'],
                row.get('name'),
                row.get('close'),
                row.get('pct_chg')
            ))
        return records

    async def get_pending_reviews(self):
        """Get screening records that need performance update (T+1 or T+5 missing)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM screening_history 
                WHERE t1_price IS NULL OR t5_price IS NULL
            ''') as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_screening_performance(self, updates):
        """
        Update performance metrics for screening records.
        :param updates: List of tuples (t1_price, t1_pct, t5_price, t5_pct, id)
        """
        if not updates:
            return
            
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                UPDATE screening_history 
                SET t1_price = ?, t1_pct = ?, t5_price = ?, t5_pct = ?
                WHERE id = ?
            '''
        await self.queue.put((sql, updates, True))

    async def get_screening_history(self, strategy_name=None, limit=100):
        """Get screening history for UI"""
        query = "SELECT * FROM screening_history WHERE 1=1"
        params = []
        
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
            
        query += " ORDER BY trade_date DESC LIMIT ?"
        params.append(limit)
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)


    async def get_latest_northbound(self):
        """Get latest northbound holding for all stocks"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM northbound_holding")
            result = await cursor.fetchone()
            trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            return await self.get_northbound(trade_date=trade_date)

    # ========== Dragon Tiger Board (LHB) ==========
    async def save_top_list(self, df):
        """Save Dragon Tiger Board data (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
            
        cols = ['trade_date', 'ts_code', 'name', 'close', 'pct_chg', 'turnover_rate', 
                'amount', 'l_sell', 'l_buy', 'l_amount', 'net_amount', 
                'net_rate', 'amount_rate', 'float_values', 'reason']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO top_list 
                (trade_date, ts_code, name, close, pct_chg, turnover_rate, amount, 
                 l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await loop.run_in_executor(None, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self.queue.put((sql, params, True))
            return len(params)
        return 0

    async def get_top_list(self, trade_date=None):
        """Get Dragon Tiger Board data"""
        query = "SELECT * FROM top_list WHERE 1=1"
        params = []
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Block Trade ==========
    async def save_block_trade(self, df):
        """Save Block Trade data"""
        if df is None or df.empty:
            return 0
            
        cols = ['trade_date', 'ts_code', 'price', 'vol', 'amount', 'buyer', 'seller']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO block_trade 
                (trade_date, ts_code, price, volume, amount, buyer, seller)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            '''
        params = df[cols].values.tolist()
        await self.queue.put((sql, params, True))
        
        return len(df)

    async def get_block_trade(self, trade_date=None):
        """Get Block Trade data"""
        query = "SELECT * FROM block_trade WHERE 1=1"
        params = []
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
            
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_latest_northbound(self):
        """Get latest northbound holding for all stocks"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM northbound_holding")
            result = await cursor.fetchone()
            trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            return await self.get_northbound(trade_date=trade_date)

    # ========== Market News ==========
    async def save_market_news(self, news_item):
        """Save a single news item (dict)"""
        if not news_item:
            return 0
            
        cols = ['content', 'tags', 'publish_time', 'source']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR IGNORE INTO market_news 
                (content, tags, publish_time, source, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            '''
        # Prepare params with type conversion
        params = []
        for c in cols:
            val = news_item.get(c)
            # Fix sqlite3 param binding error for time objects
            if isinstance(val, (datetime.time, datetime.date, datetime.datetime)):
                val = str(val)
            params.append(val)
            
        await self.queue.put((sql, params, False))
        return 1

    async def get_market_news(self, limit=50, offset=0, min_publish_time=None):
        """
        Get latest market news with pagination and optional time filter.
        :param min_publish_time: Filter news published after this time (string)
        """
        query = "SELECT * FROM market_news WHERE 1=1"
        params = []
        
        if min_publish_time:
            query += " AND publish_time >= ?"
            params.append(min_publish_time)
            
        query += " ORDER BY publish_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Backward Compatibility ==========
    async def save_daily_data(self, df):
        """Backward compatibility: save to both quotes and indicators"""
        if df is None or df.empty:
            return
        await self.save_daily_quotes(df)
        await self.save_daily_indicators(df)

    async def get_latest_daily(self):
        """Backward compatibility: get latest merged daily data"""
        return await self.get_screening_data()

    async def save_financials(self, df):
        """Backward compatibility wrapper"""
        return await self.save_financial_reports(df)
