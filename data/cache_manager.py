import aiosqlite
import asyncio
import pandas as pd
import datetime
import os
import config
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType
from utils.log_decorators import log_async_operation, track_performance
import logging
from dataclasses import dataclass, field
from typing import Any

from dataclasses import dataclass, field
from typing import Any
import itertools
from data.constants import HEALTH_CHECK_TABLES

logger = logging.getLogger(__name__)

# Priority Levels (Lower value = Higher Priority)
PRIORITY_CRITICAL = 0   # STOP, INIT, Schema Changes
PRIORITY_HIGH = 10      # User Real-time Queries, Single item fixes
PRIORITY_NORMAL = 50    # Standard Daily Sync (Batch)
PRIORITY_LOW = 100      # Historical Backfill (Background)

@dataclass(order=True)
class PrioritizedTask:
    priority: int
    seq: int
    item: Any = field(compare=False)


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
        self.queue = asyncio.PriorityQueue(maxsize=ConfigHandler.get_db_queue_size()) # Priority Queue
        self._seq_counter = itertools.count() # For stable FIFO ordering
        self.writer_task = None
        self._running = False
        self._closing = False # New flag to prevent restart during close
        
        self._maintenance_event = asyncio.Event()
        self._maintenance_event.set() # Default: Ready (Green light)
        
        self._loop = None # Ensure attribute exists
        self._schema_initialized = False
        self._initialized = True

    async def _enqueue(self, item, priority=PRIORITY_NORMAL):
        """
        Thread-safe helper to enqueue tasks.
        Auto-bridges cross-loop calls to the bound main loop.
        """
        if not self._running or getattr(self, '_loop', None) is None:
            # Fallback for critical tasks if loop not bound (shouldn't happen with explicit start)
            if priority == PRIORITY_CRITICAL:
                 pass 
            else:
                 return

        # Wrap payload
        task = PrioritizedTask(priority, next(self._seq_counter), item)

        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if self._loop and curr_loop == self._loop:
            # Same loop (Main Thread) -> Direct Put
            await self.queue.put(task)
        else:
            # Different loop (Background Thread) -> Bridge
            if self._loop and self._loop.is_running():
                # Use run_coroutine_threadsafe to schedule put() on the bound loop
                # put() is a coroutine, so we run it threadsafe
                asyncio.run_coroutine_threadsafe(self.queue.put(task), self._loop)
            else:
                logger.warning("[CacheManager] Main loop closed, cannot bridge task.")

    async def start(self):
        """Start the background DB writer task on the CURRENT loop (Main Loop)"""
        if self._closing:
            return 
            
        if not self._running:
            self._running = True
            # STRICT BINDING: Capture the loop that calls start() (Main Loop)
            self._loop = asyncio.get_running_loop()
            
            self.writer_task = asyncio.create_task(self._db_writer_loop())
            logger.info(f"[CacheManager] DB Writer started on loop {id(self._loop)}.")

    async def close(self):
        """Stop the writer task and wait for queue to empty"""
        if self._closing:
            return 
            
        self._closing = True
        
        if self._running:
            logger.info("[CacheManager] Stopping DB Writer... waiting for queue to empty.")
            
            if self.writer_task:
                try:
                    task_loop = self.writer_task.get_loop()
                    curr_loop = asyncio.get_running_loop()
                    
                    if task_loop == curr_loop:
                        # Same loop: standard await
                        await self._enqueue(None, PRIORITY_CRITICAL)
                        await self.writer_task
                    else:
                        if task_loop.is_running():
                            logger.info(f"[CacheManager] Closing cross-loop ({id(task_loop)} vs {id(curr_loop)})...")
                            
                            # 1. Send Sentinel safely on the writer's loop
                            future_sentinel = asyncio.run_coroutine_threadsafe(
                                self._enqueue(None, PRIORITY_CRITICAL), 
                                task_loop
                            )
                            await asyncio.wrap_future(future_sentinel)
                            
                            # 2. Wait for writer task
                            future_wait = asyncio.run_coroutine_threadsafe(
                                asyncio.wait_for(self.writer_task, timeout=10.0), 
                                task_loop
                            )
                            await asyncio.wrap_future(future_wait)
                        else:
                            logger.warning("[CacheManager] Writer task loop is already closed. Cannot await.")
                except Exception as e:
                    logger.error(f"[CacheManager] Writer task error during close: {e}")
            else:
                # No writer task running? Just reset.
                self._running = False
            
            self._running = False
            self._closing = False 
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
            # Set busy timeout to 30s to avoid 'database is locked' errors during high concurrency
            await db.execute("PRAGMA busy_timeout = 30000;")
            await db.commit()
            
            while True:
                try:
                    # Get first task
                    task_wrapper = await self.queue.get()
                    task = task_wrapper.item
                    priority = task_wrapper.priority
                    
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
                    # BATCHING STRATEGY FOR PRIORITY QUEUE:
                    # Only batch items with SAME priority.
                    
                    MAX_BATCH_ROWS = ConfigHandler.get_max_batch_rows()
                    current_batch_rows = 0
                    
                    # Calculate rows for the first task
                    current_batch_rows += len(item_args) if item_type is True else 1
                    
                    tasks = [task]
                    
                    # Batch retrieval (drain queue up to limit, BUT respect Priority)
                    try:
                        for _ in range(50): 
                            if current_batch_rows >= MAX_BATCH_ROWS:
                                break
                            
                            # Peek/Get next item
                            # If PriorityQueue, get_nowait returns the highest priority item.
                            # If that item has higher (lower val) or equal priority, we take it.
                            # But if we are processing P2, and a P0 comes, we should ideally put P2 back?
                            # Actually, get_nowait() will return P0 if it exists.
                            # If we are processing P2, and get P0, we should run P0 First!
                            # Current logic: 'tasks' is the batch we are building.
                            # If we encounter a different priority, we should probably stop batching?
                            # Or if it's more critical, we must execute it.
                            
                            # Simplified Logic:
                            # Just drain queue. If we picked up a P0 while building P2 batch,
                            # We can't easily "put back" to front.
                            # So, we just execute what we got. 
                            # Since PriorityQueue guarantees order, if we get P0, it means P0 was at head.
                            # Wait, we already got the first task `task_wrapper`. 
                            # If valid P0 existed, `task_wrapper` would be P0.
                            # So any subsequent `get_nowait` will be >= `priority`.
                            # We only batch if new task has SAME priority.
                            
                            try:
                                next_wrapper = self.queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                                
                            if next_wrapper.priority != priority:
                                # Different priority (must be lower/worse, since we pulled best first)
                                # Actually, it could be same numeric value but different arrival?
                                # No, if priority is different int value, stop batching to allow re-evaluation?
                                # Yes, let's strict batch by priority group.
                                # Put it back!
                                # WARNING: put() might re-order if we are not careful, but heapq is stable-ish for inputs?
                                # No, PriorityQueue is heap.
                                # To be safe, we just process it as a separate batch loop cycle.
                                # But we already took it out. We must put it back.
                                self.queue.put_nowait(next_wrapper)
                                break
                                
                            # Same Priority, add to batch
                            t = next_wrapper.item
                            if t is None: # Sentinel found in batching?
                                tasks.append(t)
                                break
                            
                            # Check compatibility (SQL vs FUNC)
                            t_payload, t_args, t_type = t
                            if t_type == 'FUNC' or t_type == 'SCRIPT':
                                # Don't batch special types with SQL
                                # Put back and stop batching
                                self.queue.put_nowait(next_wrapper)
                                break
                            
                            
                            row_count = len(t_args) if t_type is True else 1
                            current_batch_rows += row_count
                            tasks.append(t)
                            # self.queue.task_done() should NOT be called here, wait for processing!
                            
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
        # 1. Thread-Safe/Loop-Safe Idempotency Check
        # This runs inside the Writer Loop, so it involves no race conditions.
        if self._schema_initialized:
             if future:
                 # Must complete the future in the CALLER's loop, not here.
                 # _execute_schema_internal handles future resolution below safely?
                 # ideally we just let it fall through or return early.
                 # But we need to set the future result!
                 # The Future object was passed from Caller Loop.
                 # We must use call_soon_threadsafe to set it.
                 def _set_res():
                     if not future.done(): future.set_result(True)
                 
                 # future.get_loop() might be needed if we want to be pedantic, 
                 # but since we don't know the future's loop easily (it's not stored on future in all python versions publically).
                 # Wait, future.get_loop() exists.
                 f_loop = future.get_loop()
                 f_loop.call_soon_threadsafe(_set_res)
             return

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
            self._schema_initialized = True 
            logger.info("[CacheManager] Schema executed successfully.")
            
            if future:
                def _safe_set_result():
                    if not future.done(): future.set_result(True)
                future.get_loop().call_soon_threadsafe(_safe_set_result)
                
        except Exception as e:
            logger.error(f"[CacheManager] Schema Error: {e}")
            if future:
                def _safe_set_exception():
                    if not future.done(): future.set_exception(e)
                future.get_loop().call_soon_threadsafe(_safe_set_exception)

    async def init_db(self):
        """Initialize database tables via Queue"""
        # We simply queue the request. 
        # The worker (single-threaded) will handle idempotency checks.
        
        # Create a future to wait for initialization (Caller Loop Bound)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[CacheManager] init_db called without running loop?")
            return

        future = loop.create_future()
        
        # Ensure running before queueing
        if not self._running and not self._closing:
            await self.start()
        
        # Queue the internal function
        await self._enqueue((self._execute_schema_internal, [future], 'FUNC'), PRIORITY_CRITICAL)
        
        # Wait for it to finish
        await future

    async def clear_all_cache(self):
        """Rebuild Database: Drop all tables and Re-initialize"""
        logger.info("[Cache] Queuing Database Rebuild...")
        
        # 1. Purge Pending Writes to speed up
        # Since we are destroying the DB, pending writes are useless.
        # We drain the queue effectively skipping them.
        purged_count = 0
        try:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                    purged_count += 1
                except asyncio.QueueEmpty:
                    break
            if purged_count > 0:
                logger.info(f"[CacheManager] Purged {purged_count} pending tasks before rebuild.")
        except Exception as e:
            logger.warning(f"[CacheManager] Error purging queue: {e}")

        self._schema_initialized = False
        
        # Enter Maintenance Mode (Block readers)
        self._maintenance_event.clear()
        try:
            # 1. Drop Tables
            tables = ["daily_quotes", "daily_indicators", "financial_reports", "moneyflow_daily", 
                      "northbound_holding", "sync_status", "screening_history", "top_list", "block_trade", 
                      "market_news", "trade_cal", "stock_basic"]
            
            drop_script = "\n".join([f"DROP TABLE IF EXISTS {t};" for t in tables])
            
            # Queue Drop Script
            await self._enqueue((drop_script, [], 'SCRIPT'), PRIORITY_CRITICAL)
            
            # 2. Re-Initialize Schema
            # We don't necessarily need to wait for this one to finish synchronously if we don't return anything,
            # but usage usually implies we want fresh state ready.
            # Let's use a future again just to be sure we print "Done" at right time.
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            
            await self._enqueue((self._execute_schema_internal, [future], 'FUNC'), PRIORITY_CRITICAL)
            
            await future
            logger.info("[Cache] Database Rebuild Complete.")
        
        finally:
            # Exit Maintenance Mode (Unblock readers)
            self._maintenance_event.set()

    async def hard_reset(self):
        """
        Destructive Reset: Stop writer, Close DB, Delete File, Restart.
        Faster and more reliable than DROP TABLE when reads are blocking.
        """
        logger.info("[CacheManager] Performing Hard Reset (Deleting DB Files)...")
        
        # 1. Block Readers
        self._maintenance_event.clear()
        
        try:
            # 2. Stop Writer & Close Connection
            await self.close()
            
            # Give OS a moment to release file handles (Windows specific safety)
            await asyncio.sleep(0.1)
            
            # 3. Delete Files (with retry for Windows locking)
            deletion_success = False
            for i in range(3):
                try:
                    if os.path.exists(self.db_path):
                        os.remove(self.db_path)
                    
                    # WAL files
                    if os.path.exists(self.db_path + "-shm"):
                        os.remove(self.db_path + "-shm")
                    if os.path.exists(self.db_path + "-wal"):
                        os.remove(self.db_path + "-wal")
                        
                    logger.info("[CacheManager] DB files deleted.")
                    deletion_success = True
                    break # Success
                except Exception as e:
                    if i < 2:
                        logger.warning(f"[CacheManager] File locked, retrying deletion ({i+1}/3)... error: {e}")
                        await asyncio.sleep(0.5)
                    else:
                        logger.error(f"[CacheManager] Failed to delete DB files after retries: {e}")
            
            if not deletion_success:
                 # If we couldn't delete, we shouldn't pretend we did. 
                 # Raise error so UI shows "Failed".
                 if os.path.exists(self.db_path):
                     raise RuntimeError("Cannot delete database file (locked by system). Please restart application.")

            # 4. Restart (Re-init)
            # Reset state flags just in case
            self._schema_initialized = False
            await self.start()
            
            # 5. Trigger Schema Init explicitly
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            await self._enqueue((self._execute_schema_internal, [future], 'FUNC'), PRIORITY_CRITICAL)
            await future
            logger.info("[CacheManager] Hard Reset Complete.")
            
        finally:
             # Always unblock readers, even if failed, to prevent App Freeze.
             # If failed, they might get DB errors, but that is better than hanging.
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
        # CRITICAL FIX: Replace NaN with None so SQLite receives NULL, not 'nan' string/float.
        # This is essential for COALESCE to work in UPSERTs.
        import numpy as np
        return df[cols].replace({np.nan: None}).values.tolist()

    # ========== Stock Basic ==========
    async def save_stock_basic(self, df, priority=PRIORITY_NORMAL):
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


        sql = '''
                INSERT OR REPLACE INTO stock_basic 
                (ts_code, symbol, name, area, industry, market, list_date, list_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        # Offload heavy conversion
        try:
            loop = asyncio.get_running_loop()
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            # Executor likely shut down
            return 0
        
        if params:
            await self._enqueue((sql, params, True), priority)
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
    async def save_daily_quotes(self, df, priority=PRIORITY_NORMAL):
        """Save daily OHLCV quotes (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        
        cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',  
                'pre_close', 'change', 'pct_chg', 'vol', 'amount', 'adj_factor']
        
        sql = '''
                INSERT OR REPLACE INTO daily_quotes 
                (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except Exception as e:
            logger.error(f"[CacheManager] save_daily_quotes failed: {e}", exc_info=True)
            return 0
        
        if params:
            await self._enqueue((sql, params, True), priority)
            return len(params)
        return 0

    async def check_data_exists(self, trade_date: str) -> bool:
        """
        Fast check if data exists for a given date.
        Uses SELECT 1 LIMIT 1 for minimal overhead.
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT 1 FROM daily_quotes WHERE trade_date=? LIMIT 1", (trade_date,)) as cursor:
                result = await cursor.fetchone()
                return result is not None

    async def get_daily_quotes(self, ts_code=None, start_date=None, end_date=None, ts_code_list=None):
        """
        Get daily quotes from DB.
        Support single code, list of codes, or date range.
        Optimized to use IN (...) query if list provided.
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            base_sql = "SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor FROM daily_quotes WHERE 1=1"
            params = []
            
            # [Safety] Prevent full table scan
            if not any([ts_code, start_date, end_date, ts_code_list]):
                logger.warning("[CacheManager] get_daily_quotes called without filters! returning empty.")
                return pd.DataFrame()
            
            if ts_code:
                base_sql += " AND ts_code = ?"
                params.append(ts_code)
            
            if start_date:
                base_sql += " AND trade_date >= ?"
                params.append(start_date)
            
            if end_date:
                base_sql += " AND trade_date <= ?"
                params.append(end_date)

            if ts_code_list:
                # Optimized IN query with Chunking for SQLite limit (999 variables)
                CHUNK_SIZE = 900 
                all_rows = []
                cols = []
                
                # If list is small, run once
                if len(ts_code_list) <= CHUNK_SIZE:
                    placeholders = ','.join(['?'] * len(ts_code_list))
                    chunk_sql = base_sql + f" AND ts_code IN ({placeholders})"
                    chunk_params = params + ts_code_list
                    async with db.execute(chunk_sql, chunk_params) as cursor:
                         if not cols:
                             cols = [desc[0] for desc in cursor.description]
                         all_rows.extend(await cursor.fetchall())
                else:
                    # Run in chunks
                    for i in range(0, len(ts_code_list), CHUNK_SIZE):
                        chunk = ts_code_list[i:i + CHUNK_SIZE]
                        placeholders = ','.join(['?'] * len(chunk))
                        chunk_sql = base_sql + f" AND ts_code IN ({placeholders})"
                        chunk_params = params + chunk
                        async with db.execute(chunk_sql, chunk_params) as cursor:
                             if not cols:
                                 cols = [desc[0] for desc in cursor.description]
                             all_rows.extend(await cursor.fetchall())
                
                return pd.DataFrame(all_rows, columns=cols)
            
            # Normal single query
            async with db.execute(base_sql, params) as cursor:
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

    async def get_completed_step4_stocks(self, sync_version=1):
        """
        Get set of stock codes that have completed Step 4 sync.
        Uses stock_sync_status table for precise tracking.
        
        :param sync_version: Only return stocks synced with this version
        :return: Set of ts_code strings that completed Step 4
        """
        await self.wait_for_maintenance()
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute(
                    "SELECT ts_code FROM stock_sync_status WHERE sync_version >= ?",
                    (sync_version,)
                )
                rows = await cursor.fetchall()
                return set(row[0] for row in rows)
            except Exception as e:
                # Table might not exist yet on first run
                logger.debug(f"[CacheManager] stock_sync_status query failed (first run?): {e}")
                return set()

    async def mark_stock_step4_completed(self, ts_code, sync_version=1):
        """
        Mark a stock as having completed Step 4 sync.
        Called only after ALL data types for a stock are successfully saved.
        
        :param ts_code: Stock code
        :param sync_version: Sync version number
        """
        import datetime
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sql = '''
            INSERT OR REPLACE INTO stock_sync_status 
            (ts_code, step4_completed_at, sync_version)
            VALUES (?, ?, ?)
        '''
        await self._enqueue((sql, [(ts_code, now, sync_version)], True))

    async def clear_step4_sync_status(self):
        """
        Clear all Step 4 sync status (for forced full resync).
        Waits for completion to ensure consistency.
        """
        logger.info("[CacheManager] Clearing Step 4 sync status for forced resync")
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError: return
            
        future = loop.create_future()
        
        async def _do_delete(db_conn):
            try:
                await db_conn.execute("DELETE FROM stock_sync_status")
                await db_conn.commit()
                # Resolve future in main loop
                loop.call_soon_threadsafe(future.set_result, True)
            except Exception as e:
                loop.call_soon_threadsafe(future.set_exception, e)
                
        # Queue as FUNC (Priority Critical ensures it runs ASAP)
        await self._enqueue((_do_delete, [], 'FUNC'), priority=PRIORITY_CRITICAL)
        
        # Wait for actual execution
        await future

    # ========== Daily Indicators ==========
    async def save_daily_indicators(self, df, priority=PRIORITY_NORMAL):
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
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True), priority)
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

    # ========== New Data Types (Step 4 & Extended) ==========

    async def save_fina_audit(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'ann_date', 'audit_result', 'audit_agency', 'audit_sign']
        sql = "INSERT OR REPLACE INTO financial_reports (ts_code, end_date, ann_date, audit_result) VALUES (?, ?, ?, ?)" # Note: We update existing reports
        # Wait, audit comes from separate API. It should probably UPDATE the financial_reports table if exists, or INSERT.
        # But financial_reports primary key is (ts_code, end_date). 
        # Tushare audit interface returns end_date.
        # Ideally we UPSERT. But here we might just want to update the audit_result column specifically.
        # SQLite doesn't support easy column update via INSERT OR REPLACE without creating new row.
        # Strategy: DB schema has audit_result in financial_reports.
        # We can use INSERT OR IGNORE then UPDATE, or just INSERT OR REPLACE if we have all data.
        # But we only have audit info here. We don't want to wipe other fields.
        # Better strategy: Use specific UPDATE statement.
        
        # But for simplicity and bulk, let's use UPDATE.
        # "UPDATE financial_reports SET audit_result=?, audit_agency=? ... WHERE ts_code=? AND end_date=?"
        # But we handle batch.
        sql = '''
            UPDATE financial_reports 
            SET audit_result = ?
            WHERE ts_code = ? AND end_date = ?
        '''
        # Params need to be (audit_result, ts_code, end_date)
        # Prepare params manually
        t_data = []
        for _, row in df.iterrows():
            t_data.append((row['audit_result'], row['ts_code'], row['end_date']))
            
        if not self._running and not self._closing: await self.start()
        await self._enqueue((sql, t_data, True))
        return len(t_data)

    async def save_financial_reports(self, df):
        if df is None or df.empty: return 0
        
        # Added 'goodwill' and ensuring all schema columns are covered
        cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                'n_income', 'n_income_attr_p', 'total_assets', 'total_liab', 
                'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin', 
                'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy', 'goodwill']
        
        # SAFE UPSERT: Use COALESCE to avoid overwriting existing data with NULLs from partial updates
        # (e.g. valid revenue from Income Statement shouldn't be wiped by Balance Sheet save)
        sql = '''
            INSERT INTO financial_reports 
            (ts_code, end_date, ann_date, report_type, total_revenue, revenue,
             n_income, n_income_attr_p, total_assets, total_liab, 
             total_hldr_eqy_exc_min_int, roe, roe_dt, grossprofit_margin, 
             netprofit_margin, debt_to_assets, or_yoy, netprofit_yoy, goodwill)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ts_code, end_date) DO UPDATE SET
                ann_date = COALESCE(excluded.ann_date, financial_reports.ann_date),
                report_type = COALESCE(excluded.report_type, financial_reports.report_type),
                total_revenue = COALESCE(excluded.total_revenue, financial_reports.total_revenue),
                revenue = COALESCE(excluded.revenue, financial_reports.revenue),
                n_income = COALESCE(excluded.n_income, financial_reports.n_income),
                n_income_attr_p = COALESCE(excluded.n_income_attr_p, financial_reports.n_income_attr_p),
                total_assets = COALESCE(excluded.total_assets, financial_reports.total_assets),
                total_liab = COALESCE(excluded.total_liab, financial_reports.total_liab),
                total_hldr_eqy_exc_min_int = COALESCE(excluded.total_hldr_eqy_exc_min_int, financial_reports.total_hldr_eqy_exc_min_int),
                roe = COALESCE(excluded.roe, financial_reports.roe),
                roe_dt = COALESCE(excluded.roe_dt, financial_reports.roe_dt),
                grossprofit_margin = COALESCE(excluded.grossprofit_margin, financial_reports.grossprofit_margin),
                netprofit_margin = COALESCE(excluded.netprofit_margin, financial_reports.netprofit_margin),
                debt_to_assets = COALESCE(excluded.debt_to_assets, financial_reports.debt_to_assets),
                or_yoy = COALESCE(excluded.or_yoy, financial_reports.or_yoy),
                netprofit_yoy = COALESCE(excluded.netprofit_yoy, financial_reports.netprofit_yoy),
                goodwill = COALESCE(excluded.goodwill, financial_reports.goodwill)
        '''
        
        try:
             params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
             return 0
             
        if params: 
             await self._enqueue((sql, params, True))
             return len(params)
        return 0

    async def save_fina_forecast(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'ann_date', 'type', 'p_change_min', 'p_change_max', 'net_profit_min', 'net_profit_max']
        sql = "INSERT OR REPLACE INTO fina_forecast (ts_code, end_date, ann_date, type, p_change_min, p_change_max, net_profit_min, net_profit_max) VALUES (?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_fina_audit(self, df):
        """Save financial audit opinions"""
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'ann_date', 'audit_result', 'audit_fees', 'audit_agency']
        sql = "INSERT OR REPLACE INTO fina_audit (ts_code, end_date, ann_date, audit_result, audit_fees, audit_agency) VALUES (?,?,?,?,?,?)"
        try:
             params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError: return 0
        if params: await self._enqueue((sql, params, True))
        return len(params)
    async def save_fina_mainbz(self, df):
        if df is None or df.empty: return 0
        # fina_mainbz columns in schema: ts_code, end_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type, update_flag
        # Tushare returns: ts_code, end_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
        # We assume bz_item is unique per date per stock? Yes PK.
        cols = ['ts_code', 'end_date', 'bz_item', 'bz_sales', 'bz_profit', 'bz_cost', 'curr_type']
        sql = "INSERT OR REPLACE INTO fina_mainbz (ts_code, end_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type) VALUES (?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_pledge_stat(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'pledge_count', 'unrest_pledge', 'rest_pledge', 'total_share', 'pledge_ratio']
        sql = "INSERT OR REPLACE INTO pledge_stat (ts_code, end_date, pledge_count, unrest_pledge, rest_pledge, total_share, pledge_ratio) VALUES (?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_repurchase(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'ann_date', 'end_date', 'proc', 'exp_date', 'vol', 'amount', 'high_limit', 'low_limit']
        sql = "INSERT OR REPLACE INTO repurchase (ts_code, ann_date, end_date, proc, exp_date, vol, amount, high_limit, low_limit) VALUES (?,?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_dividend(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'ann_date', 'div_proc', 'stk_div', 'stk_bo_rate', 'stk_co_rate', 'cash_div_tax', 'cash_div_tax_rate', 'record_date', 'ex_date']
        sql = "INSERT OR REPLACE INTO dividend (ts_code, end_date, ann_date, div_proc, stk_div, stk_bo_rate, stk_co_rate, cash_div_tax, cash_div_tax_rate, record_date, ex_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_index_daily(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'trade_date', 'close', 'open', 'high', 'low', 'pre_close', 'change', 'pct_chg', 'vol', 'amount']
        sql = "INSERT OR REPLACE INTO index_daily (ts_code, trade_date, close, open, high, low, pre_close, change, pct_chg, vol, amount) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_index_dailybasic(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'trade_date', 'total_mv', 'float_mv', 'total_share', 'float_share', 'free_share', 'turnover_rate', 'turnover_rate_f', 'pe', 'pe_ttm', 'pb']
        sql = "INSERT OR REPLACE INTO index_dailybasic (ts_code, trade_date, total_mv, float_mv, total_share, float_share, free_share, turnover_rate, turnover_rate_f, pe, pe_ttm, pb) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_limit_list(self, df):
        if df is None or df.empty: return 0
        cols = ['trade_date', 'ts_code', 'name', 'close', 'pct_chg', 'amp', 'fc_ratio', 'fl_ratio', 'fd_amount', 'first_time', 'last_time', 'open_times', 'strth', 'limit_type']
        sql = "INSERT OR REPLACE INTO limit_list (trade_date, ts_code, name, close, pct_chg, amp, fc_ratio, fl_ratio, fd_amount, first_time, last_time, open_times, strth, limit_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_margin_daily(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'trade_date', 'rzye', 'rqye', 'rzmre', 'rqyl', 'rzrqye']
        sql = "INSERT OR REPLACE INTO margin_daily (ts_code, trade_date, rzye, rqye, rzmre, rqyl, rzrqye) VALUES (?,?,?,?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

    async def save_suspend_d(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'trade_date', 'suspend_timing', 'suspend_type_name']
        sql = "INSERT OR REPLACE INTO suspend_d (ts_code, trade_date, suspend_timing, suspend_type_name) VALUES (?,?,?,?)"
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        if params: await self._enqueue((sql, params, True))
        return len(params)

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
        await self._enqueue((sql, params, False))

    # ========== Trade Calendar ==========
    async def ensure_trade_cal(self, end_date, api, required_start_date=None):
        """
        Ensure trade calendar is cached and covers [required_start_date, end_date].
        Uses ThreadPoolManager for API calls.
        
        Args:
            end_date: Target end date (YYYYMMDD format)
            api: TushareClient instance for API calls
            required_start_date: Optional start date; defaults to 4 years before end_date
            
        Returns:
            bool: True if calendar data is available, False otherwise
        """
        try:
            await self.wait_for_maintenance()
            
            # Validate date format (YYYYMMDD)
            if not end_date or len(end_date) != 8 or not end_date.isdigit():
                logger.error(f"[CacheManager] Invalid end_date format: {end_date}")
                return False
            
            # Validate required_start_date if provided
            if required_start_date and (len(required_start_date) != 8 or not required_start_date.isdigit()):
                logger.error(f"[CacheManager] Invalid required_start_date format: {required_start_date}")
                return False
            
            # Check current cache coverage
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT MIN(cal_date), MAX(cal_date) FROM trade_cal") as cursor:
                    row = await cursor.fetchone()
                    min_db = row[0] if row else None
                    max_db = row[1] if row else None

            curr_year = int(end_date[:4])
            target_start = required_start_date if required_start_date else datetime.date(curr_year - 4, 1, 1).strftime('%Y%m%d')

            async def fetch_and_save(s, e):
                # Extend to year end for better caching
                y = int(e[:4])
                real_end = datetime.date(y, 12, 31).strftime('%Y%m%d')
                if e < real_end: 
                    e = real_end
                
                # Use ThreadPoolManager instead of asyncio.to_thread
                df = await ThreadPoolManager().run_async(TaskType.IO, api.get_trade_cal, s, e)
                if df is not None and not df.empty:
                    await self.save_trade_cal(df)
                    logger.info(f"[CacheManager] Trade calendar saved: {s} to {e}, {len(df)} days")
                    return True
                return False

            if not min_db or not max_db:
                # First time: fetch full range
                success = await fetch_and_save(target_start, end_date)
                if not success:
                    logger.error("[CacheManager] Failed to fetch initial trade calendar")
                    return False
            else:
                # Fill gaps if needed
                if target_start < min_db:
                    gap = (datetime.datetime.strptime(min_db, '%Y%m%d') - datetime.datetime.strptime(target_start, '%Y%m%d')).days
                    if gap > 10: 
                        await fetch_and_save(target_start, min_db)
                
                if max_db < end_date:
                    await fetch_and_save(max_db, end_date)
            
            # Ensure all writes are flushed to DB
            logger.debug("[CacheManager] Waiting for DB writer to finish...")
            await self.queue.join()

            # Verify calendar data exists
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM trade_cal WHERE is_open = 1") as cursor:
                    row = await cursor.fetchone()
                    count = row[0] if row else 0
            
            if count == 0:
                logger.error("[CacheManager] No trade calendar data available after sync")
                return False
                
            logger.debug(f"[CacheManager] Trade calendar ready: {count} trading days cached")
            return True

        except Exception as e:
            logger.error(f"[CacheManager] ensure_trade_cal failed: {e}")
            return False

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
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
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

    # ========== Index Daily ==========
    async def save_index_daily(self, df):
        """Save index daily data (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'close', 'open', 'high', 'low', 
                'pre_close', 'change', 'pct_chg', 'vol', 'amount']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO index_daily 
                (ts_code, trade_date, close, open, high, low, pre_close, change, pct_chg, vol, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
            return len(params)
        return 0

    async def save_index_dailybasic(self, df):
        """Save index daily basic indicators (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'total_mv', 'float_mv', 'total_share',
                'float_share', 'free_share', 'turnover_rate', 'turnover_rate_f', 'pe', 'pe_ttm', 'pb']
        
        if not self._running and not self._closing:
            await self.start()

        sql = '''
                INSERT OR REPLACE INTO index_dailybasic 
                (ts_code, trade_date, total_mv, float_mv, total_share, float_share, 
                 free_share, turnover_rate, turnover_rate_f, pe, pe_ttm, pb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            loop = asyncio.get_running_loop()
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
            return len(params)
        return 0

    async def get_index_daily(self, ts_code=None, trade_date=None):
        """Get index daily data from cache"""
        query = "SELECT * FROM index_daily WHERE 1=1"
        params = []
        
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
            
        query += " ORDER BY trade_date DESC"
        
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
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
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
        await self._enqueue((sql, params, False)) # Single execution

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
        Enhanced: Also checks data timeliness (recent 2 quarters).
        Returns:
            dict: stats {total_stocks, with_financials, coverage_ratio, stale_count}
            list: missing_ts_codes (priority: no data > stale data)
        """
        await self.wait_for_maintenance()
        import datetime
        
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Total Stocks (Active)
            # Filter out new stocks (< 6 months / 180 days) as they might not have reports yet
            cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=180)).strftime('%Y%m%d')
            
            async with db.execute(
                "SELECT ts_code, name FROM stock_basic WHERE list_status = 'L' AND list_date <= ?", 
                (cutoff_date,)
            ) as cursor:
                all_stocks = await cursor.fetchall()
                
            if not all_stocks:
                return {'total': 0, 'covered': 0, 'ratio': 0.0}, []
                
            all_codes = {row[0] for row in all_stocks}
            total_count = len(all_codes)
            
            # 2. Calculate recent quarter thresholds (last ~6-9 months)
            # Instead of complex quarter mapping, we simply look for any report ending in the last 270 days.
            # (9 months covers the worst case gap: Q3 end (Sep) -> Annual end (Dec) -> Q1 disclosure (Apr))
            # Actually, standard is:
            # - Annual (12-31) due 04-30 (4 months later)
            # - Q1 (03-31) due 04-30
            # - So in May, we expect 03-31 or 12-31.
            # - In Sep, we expect 06-30.
            # A 9-month rolling window on 'end_date' is safe to catch the "latest" available report.
            
            recent_cutoff = (datetime.datetime.now() - datetime.timedelta(days=270)).strftime('%Y%m%d')
            
            # 3. Stocks with ANY financial record
            async with db.execute("SELECT DISTINCT ts_code FROM financial_reports") as cursor:
                covered_stocks = await cursor.fetchall()
            covered_codes = {row[0] for row in covered_stocks}
            
            # 4. Stocks with RECENT data
            query = "SELECT DISTINCT ts_code FROM financial_reports WHERE end_date >= ?"
            async with db.execute(query, (recent_cutoff,)) as cursor:
                recent_stocks = await cursor.fetchall()
            recent_codes = {row[0] for row in recent_stocks}
            
            # 5. Calculate categories (filter to only stocks in all_codes)
            no_data_codes = list(all_codes - covered_codes)  # Never had any data
            stale_codes = list((covered_codes - recent_codes) & all_codes)  # Has data but outdated, AND still active
            
            # Combine missing = no_data + stale (prioritize no_data)
            missing_codes = no_data_codes + stale_codes
            
            covered_count = len(covered_codes)
            recent_count = len(recent_codes & all_codes)  # Recent AND in stock_basic
            ratio = (covered_count / total_count) if total_count > 0 else 0.0
            recent_ratio = (recent_count / total_count) if total_count > 0 else 0.0
            
            stats = {
                'total': total_count,
                'covered': covered_count,
                'ratio': ratio,
                'recent_count': recent_count,
                'recent_ratio': recent_ratio,
                'stale_count': len(stale_codes),
                'missing_count': len(no_data_codes)
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
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
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
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
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
            records = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_screening_params, df, strategy_name, trade_date)
        except RuntimeError:
            return 0

        sql = '''
                INSERT OR IGNORE INTO screening_history 
                (trade_date, strategy_name, ts_code, name, close, pct_chg)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
        # Note: records is a list of tuples, so we use True for is_many
        if records:
            await self._enqueue((sql, records, True))
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
            
        sql = '''
                UPDATE screening_history 
                SET t1_price = ?, t1_pct = ?, t5_price = ?, t5_pct = ?
                WHERE id = ?
            '''
        await self._enqueue((sql, updates, True))

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
            pass # Explicit start required


        sql = '''
                INSERT OR REPLACE INTO top_list 
                (trade_date, ts_code, name, close, pct_chg, turnover_rate, amount, 
                 l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
        try:
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
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
        """Save Block Trade data (Offloaded to Thread)"""
        if df is None or df.empty:
            return 0
            
        cols = ['trade_date', 'ts_code', 'price', 'vol', 'amount', 'buyer', 'seller']
        
        
        if not self._running and not self._closing:
            pass # Explicit start required


        sql = '''
                INSERT OR REPLACE INTO block_trade 
                (trade_date, ts_code, price, volume, amount, buyer, seller)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            '''
        
        try:
            params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        except RuntimeError:
            return 0
        
        if params:
            await self._enqueue((sql, params, True))
            return len(params)
        return 0

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

    # ========== Market News ==========
    @staticmethod
    def normalize_news_item(raw_item: dict, default_source: str = 'CLS') -> dict:
        """
        统一新闻字段格式的公共方法。
        所有新闻保存路径都应使用此方法来确保字段一致性。
        
        Args:
            raw_item: 原始新闻数据（可能来自不同数据源）
            default_source: 默认来源标识
            
        Returns:
            dict: 标准化后的新闻数据，包含 content, tags, publish_time, source 字段
        """
        return {
            'content': raw_item.get('content', '') or '',
            'tags': raw_item.get('tags', '') or '',
            # 兼容不同数据源：'time' 或 'publish_time'
            'publish_time': raw_item.get('publish_time') or raw_item.get('time', '') or '',
            'source': raw_item.get('source', default_source) or default_source
        }
    
    async def save_market_news(self, news_item, wait=False):
        """
        Save a single news item (dict).
        Args:
            news_item: News data dict
            wait: If True, wait for DB write to complete (prevents stale stale reads in UI)
        """
        if not news_item:
            return 0
            
        cols = ['content', 'tags', 'publish_time', 'source']
        
        if not self._running and not self._closing:
            pass # Explicit start required


        sql = '''
            INSERT INTO market_news 
            (content, tags, publish_time, source, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(content, publish_time) DO UPDATE SET
                tags = COALESCE(excluded.tags, market_news.tags)
        '''
        # Prepare params with type conversion
        params = []
        for c in cols:
            val = news_item.get(c)
            # Fix sqlite3 param binding error for time objects
            if isinstance(val, (datetime.time, datetime.date, datetime.datetime)):
                val = str(val)
            params.append(val)
            
        if wait:
            # Create Future to wait for completion
            try:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                
                async def _exec_write(db, s, p):
                    try:
                        await db.execute(s, p)
                        await db.commit()
                        # Safely resolve future in the original loop
                        loop.call_soon_threadsafe(lambda: future.set_result(True) if not future.done() else None)
                    except Exception as e:
                        loop.call_soon_threadsafe(lambda: future.set_exception(e) if not future.done() else None)

                # Queue as FUNC (Functional Task) which is processed immediately (non-batched)
                await self._enqueue((_exec_write, [sql, params], 'FUNC'))
                await future
                return 1
            except Exception as e:
                logger.error(f"[CacheManager] Save news wait error: {e}")
                return 0
        else:
            # Standard Fire-and-Forget (Batched)
            await self._enqueue((sql, params, False))
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

    async def check_comprehensive_health(self):
        """
        Deep Health Check v2.0:
        1. Coverage (Existence)
        2. Strict Freshness (Dynamic Deadline)
        3. Integrity (Sanity Check)
        4. Continuity (Gap Detection - Full Scan)
        """
        await self.wait_for_maintenance()
        
        today = datetime.datetime.now()
        
        # --- 1. Dynamic Freshness Logic ---
        # Calculate required financial period based on today
        # Rules:
        # May 1 - Aug 30: Need Y-1/Q4 (Annual)
        # Sep 1 - Oct 30: Need Y/Q2 (Semi)
        # Nov 1 - Apr 30: Need Y/Q3
        
        y = today.year
        md = today.month * 100 + today.day
        
        required_period = ""
        deadline_desc = ""
        
        if 501 <= md <= 830:
            required_period = f"{y-1}1231"
            deadline_desc = f"去年年报 ({y-1})"
        elif 901 <= md <= 1030:
            required_period = f"{y}0630"
            deadline_desc = f"中报 ({y})"
        elif md >= 1101 or md <= 430:
            # If Nov-Dec, need this year Q3. If Jan-Apr, need Last Year Q3
            target_y = y if md >= 1101 else y-1
            required_period = f"{target_y}0930"
            deadline_desc = f"三季报 ({target_y})"
        
        # Tables Config: Use Single Source of Truth
        # Map constants to check list
        check_list = []
        for table, cfg in HEALTH_CHECK_TABLES.items():
            # Criterion: 
            # If 'fina_mainbz' -> dynamic (mainbz usually follows report deadline)
            # If 'pledge_stat' -> 90 days? (pledge is snapshot)
            # If 'dividend' -> 365 days (annual event)
            # If 'fina_forecast' -> 180 days?
            
            # We can define default criteria or map specific ones.
            # Ideally, this config should also be in constants.py, but for now we map here.
            crit = 30 # Default 30 days freshness for high freq events
            
            if table == 'fina_mainbz': crit = 'dynamic'
            elif table == 'fina_audit': crit = 'dynamic'
            elif table == 'fina_forecast': crit = 180
            elif table == 'dividend': crit = 365
            elif table == 'repurchase': crit = 365
            elif table == 'pledge_stat': crit = 90
            elif table == 'northbound_holding': crit = 5
            elif table == 'margin_daily': crit = 5
            elif table == 'block_trade': crit = 5
            elif table == 'top_list': crit = 5
            
            check_list.append((table, cfg['date_col'], crit))
        
        # Add legacy/other tables not in FINANCIAL_AUX
        check_list.extend([
            ('northbound_holding', 'trade_date', 5),
            ('margin_daily', 'trade_date', 5),
            ('suspend_d', 'trade_date', 5),
            ('top_list', 'trade_date', 5),
            ('block_trade', 'trade_date', 5),
        ])
        
        # Deduplicate if unified list already covered them? 
        # Actually FINANCIAL_BATCH/STOCK tables might not cover market data like top_list.
        # Let's filter duplicates.
        seen_tables = set()
        final_check_list = []
        for item in check_list:
            if item[0] not in seen_tables:
                final_check_list.append(item)
                seen_tables.add(item[0])
        
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Total Active Stocks
            cursor = await db.execute("SELECT count(*) FROM stock_basic WHERE list_status='L'")
            total_stocks = (await cursor.fetchone())[0] or 1
            
            results = {}
            
            # --- 2. Financial Reports (Special Handling) ---
            try:
                # Coverage
                cursor = await db.execute("SELECT count(DISTINCT ts_code) FROM financial_reports")
                fin_covered = (await cursor.fetchone())[0] or 0
                
                # Strict Freshness
                sql_strict = f"SELECT count(DISTINCT ts_code) FROM financial_reports WHERE end_date >= '{required_period}'"
                cursor = await db.execute(sql_strict)
                fin_fresh = (await cursor.fetchone())[0] or 0
                
                results['financial_reports'] = {
                    'covered': fin_covered,
                    'ratio': fin_covered / total_stocks,
                    'fresh': fin_fresh,
                    'fresh_ratio': fin_fresh / total_stocks,
                    'deadline_desc': deadline_desc
                }
            except Exception as e:
                results['financial_reports'] = {'error': str(e)}

            # --- 3. Other Tables (Standard Logic) ---
            for table, date_col, criterion in final_check_list:
                try:
                    # Coverage
                    cursor = await db.execute(f"SELECT count(DISTINCT ts_code) FROM {table}")
                    covered = (await cursor.fetchone())[0] or 0
                    
                    # Freshness
                    if criterion == 'dynamic':
                        cutoff = required_period
                    else:
                        cutoff = (today - datetime.timedelta(days=criterion)).strftime('%Y%m%d')
                        
                    cursor = await db.execute(f"SELECT count(DISTINCT ts_code) FROM {table} WHERE {date_col} >= '{cutoff}'")
                    fresh = (await cursor.fetchone())[0] or 0
                    
                    # Check for permission denied if count is 0
                    skipped = False
                    if fresh == 0:
                        # Check sync_status for permission_denied
                        # We use a broad check: if ANY recent sync attempt failed due to permission
                        # or if the MOST RECENT entry is permission_denied
                        try:
                            # Check last 3 days
                            check_date = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime('%Y-%m-%d')
                            c2 = await db.execute(
                                "SELECT count(*) FROM sync_status WHERE table_name=? AND status='permission_denied' AND updated_at >= ?", 
                                (table, check_date)
                            )
                            perm_err_count = (await c2.fetchone())[0] or 0
                            if perm_err_count > 0:
                                skipped = True
                        except: pass

                    results[table] = {
                        'covered': covered,
                        'ratio': covered / total_stocks,
                        'fresh': fresh,
                        'fresh_ratio': fresh / total_stocks,
                        'skipped': skipped
                    }
                except:
                    results[table] = {'covered': 0, 'ratio': 0, 'fresh': 0, 'fresh_ratio': 0}

            # --- 4. Sanity Check (Zero Tolerance) ---
            sanity_errors = 0
            try:
                # Price <= 0
                cursor = await db.execute("SELECT count(*) FROM daily_quotes WHERE qfq_close <= 0")
                sanity_errors += (await cursor.fetchone())[0]
                
                # High < Low
                cursor = await db.execute("SELECT count(*) FROM daily_quotes WHERE high < low")
                sanity_errors += (await cursor.fetchone())[0]
            except: pass
            
            # --- 4b. Joint Coverage (Strategy Readiness) ---
            # How many stocks have BOTH Financial Reports AND Main Business data?
            try:
                sql_joint = f"""
                    SELECT count(*) FROM stock_basic sb
                    WHERE sb.list_status='L'
                    AND sb.ts_code IN (SELECT DISTINCT ts_code FROM financial_reports WHERE end_date >= '{required_period}')
                    AND sb.ts_code IN (SELECT DISTINCT ts_code FROM fina_mainbz WHERE end_date >= '{required_period}')
                """
                cursor = await db.execute(sql_joint)
                joint_count = (await cursor.fetchone())[0] or 0
                results['joint_coverage'] = {
                    'count': joint_count,
                    'ratio': joint_count / total_stocks
                }
            except:
                 results['joint_coverage'] = {'count': 0, 'ratio': 0}

            # --- 5. Gap Detection (Smart Fallback) ---
            # Priority 1: Check Index (000001.SH) - Fast & Accurate (No suspensions)
            # Priority 2: Check Bellwether Stock (600519.SH) - If Index data missing
            gap_count = 0
            try:
                # Check Index Data Availability
                cursor = await db.execute("SELECT count(*) FROM index_daily WHERE ts_code='000001.SH' AND trade_date > date('now', '-1 years')")
                idx_cnt = (await cursor.fetchone())[0]
                
                target_table = 'index_daily'
                target_code = '000001.SH'
                
                if idx_cnt < 200:
                    # Fallback to Stock
                    target_table = 'daily_quotes'
                    target_code = '600519.SH' # Moutai (Rarely suspended)
                
                sql_gap = f"""
                SELECT count(*) FROM (
                    SELECT trade_date,
                           LAG(trade_date) OVER (ORDER BY trade_date) as prev_date
                    FROM {target_table}
                    WHERE ts_code = '{target_code}' 
                    AND trade_date > date('now', '-1 years')
                ) WHERE julianday(trade_date) - julianday(prev_date) > 10
                """
                cursor = await db.execute(sql_gap)
                gap_count = (await cursor.fetchone())[0]
            except Exception as e:
                logger.error(f"Gap check failed: {e}")

            # Get Missing Samples
            cursor = await db.execute("""
                SELECT ts_code, name FROM stock_basic 
                WHERE list_status='L' 
                AND ts_code NOT IN (SELECT DISTINCT ts_code FROM financial_reports)
                LIMIT 10
            """)
            missing_samples = [{'code': row[0], 'name': row[1]} for row in await cursor.fetchall()]
            
            return {
                'total_stocks': total_stocks,
                'tables': results,
                'missing_samples': missing_samples,
                'sanity_errors': sanity_errors,
                'gap_count': gap_count,
                'deadline_desc': deadline_desc
            }
            
    # Legacy alias if needed, or remove
    async def check_financial_coverage(self):
        # Redirect to new method but adapt return format to break less code strictly if needed,
        # but better to update caller.
        res = await self.check_comprehensive_health()
        fin = res['tables'].get('financial_reports', {})
        return {
            'total': res['total_stocks'],
            'covered': fin.get('covered', 0),
            'ratio': fin.get('ratio', 0)
        }, [m['code'] for m in res['missing_samples']]
