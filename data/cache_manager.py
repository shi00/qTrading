import asyncio
import datetime
import logging
import os
import threading

from sqlalchemy import text, event, Engine
from sqlalchemy.ext.asyncio import create_async_engine

import config
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType
from data.data_dictionary import TABLE_DEFINITIONS
from data.constants import HEALTH_DEPTH_FULL_TRADE_DAYS
# DAOs
from data.daos.base_dao import BaseDao  # Expose static helpers via BaseDao if needed, or keeping usage internal
from data.daos.financial_dao import FinancialDao
from data.daos.market_dao import MarketDao
from data.daos.quote_dao import QuoteDao
from data.daos.screener_dao import ScreenerDao
from data.daos.stock_dao import StockDao
from data.daos.sync_dao import SyncDao
from data.daos.macro_dao import MacroDao
from data.daos.holder_dao import HolderDao

logger = logging.getLogger(__name__)


# --- WAL Mode Enforcement ---
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class CacheManager:
    _instance = None
    _initialized = False
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.db_path = config.DB_PATH
        # DB_PATH check
        if not self.db_path:
            self.db_path = "stock_data.db"  # Fallback

        connection_string = f"sqlite+aiosqlite:///{self.db_path}"

        # Load pool settings from config
        db_pool_size = ConfigHandler.get_db_connection_pool_size()
        
        self.engine = create_async_engine(
            connection_string,
            echo=False,
            # Pool settings for high concurrency
            pool_size=db_pool_size,  # Configurable (default 5)
            max_overflow=max(30, int(db_pool_size * 0.1)),  # Allow 10% overflow or at least 30
            pool_timeout=60,  # Wait up to 60s for connection
            future=True
        )

        self._maintenance_event_lazy = None  # ST-01: Lazy init
        self._init_lock_lazy = None  # Lazy init to avoid cross-loop binding

        # Initialize DAOs
        self.stock_dao = StockDao(self.engine)
        self.quote_dao = QuoteDao(self.engine)
        self.financial_dao = FinancialDao(self.engine)
        self.sync_dao = SyncDao(self.engine)
        self.market_dao = MarketDao(self.engine)
        self.screener_dao = ScreenerDao(self.engine)
        self.macro_dao = MacroDao(self.engine)
        self.holder_dao = HolderDao(self.engine)

        self._initialized = True
        self._schema_initialized = False
        logger.info(f"[CacheManager] Initialized with SQLAlchemy AsyncEngine: {self.db_path}")

    @property
    def _maintenance_event(self):
        """Get or create maintenance event dynamically per event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Event()  # Dummy fallback if no loop

        if not hasattr(current_loop, '_cache_maint_event'):
            evt = asyncio.Event()
            evt.set()  # Default to Set (Not in maintenance)
            setattr(current_loop, '_cache_maint_event', evt)
            
        return getattr(current_loop, '_cache_maint_event')

    @property
    def _init_lock(self):
        """Get or create initialization lock dynamically per event loop to avoid cross-loop binding deadlocks."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[CacheManager] No running event loop for _init_lock. Using dummy lock.")
            class DummyLock:
                async def __aenter__(self): return
                async def __aexit__(self, *args): return
            return DummyLock()

        if not hasattr(current_loop, '_cache_init_lock'):
            setattr(current_loop, '_cache_init_lock', asyncio.Lock())
            
        return getattr(current_loop, '_cache_init_lock')

    async def close(self):
        """Dispose the engine"""
        logger.info("[CacheManager] Disposing engine...")
        await self.engine.dispose()

    # --- Maintenance & Helpers ---
    async def wait_for_maintenance(self):
        if not self._maintenance_event.is_set():
            logger.info("[CacheManager] Waiting for maintenance...")
            await self._maintenance_event.wait()

    @staticmethod
    def _prepare_data_params(df, cols, date_cols=None):
        # Facade: Delegate to BaseDao static method
        return BaseDao._prepare_data_params(df, cols, date_cols)

    @staticmethod
    def normalize_news_item(item, default_source='CLS'):
        """Normalize news item dictionary for DB Insertion"""
        return {
            'content': item.get('content', '').strip(),
            'tags': item.get('tags', ''),
            'publish_time': item.get('time',
                                     item.get('publish_time', datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
            'source': item.get('source', default_source)
        }

    # Backward compatibility for direct SQL usage if any
    async def _write_db(self, sql, params=None, is_many=False):
        # We can implement a temporary BaseDao to run this?
        # Or just instantiate a base dao for ad-hoc queries.
        # Ideally, usages should be migrated, but for now:
        dao = BaseDao(self.engine)
        return await dao._write_db(sql, params, is_many)

    async def _read_db(self, sql, params=None):
        await self.wait_for_maintenance()
        dao = BaseDao(self.engine)
        return await dao._read_db(sql, params)

    # --- Init & Reset ---
    async def init_db(self, force=False):
        """Initialize Tables"""
        async with self._init_lock:
            # Explicit truncation on startup (P0 fix for 800MB WAL)
            try:
                async with self.engine.connect() as raw_conn:
                    conn = await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
                    await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                    logger.info("[CacheManager] WAL file truncated/cleaned.")
            except Exception as e:
                logger.warning(f"[CacheManager] Failed to truncate WAL: {e}")

            if self._schema_initialized and not force:
                return

            logger.info("[CacheManager] Initializing DB Schema...")
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
            if not os.path.exists(schema_path):
                logger.error("Schema file not found!")
                return

            # Offload file read
            def read_schema():
                with open(schema_path, 'r', encoding='utf-8') as f:
                    return f.read()

            try:
                msg = await ThreadPoolManager().run_async(TaskType.IO, read_schema)

                # Split by ; and execute statements individually
                statements = [s.strip() for s in msg.split(';') if s.strip()]

                async with self.engine.begin() as conn:
                    for stmt in statements:
                        await conn.execute(text(stmt))

                self._schema_initialized = True
                logger.info("[CacheManager] DB Init Complete.")

            except Exception as e:
                logger.error(f"[CacheManager] Init DB Failed: {e}", exc_info=True)

        # Run auto-migration check
        await self._check_and_update_schema()

    async def _check_and_update_schema(self):
        """Auto-migrate schema for known missing columns."""
        try:
            async with self.engine.begin() as conn:
                # Check daily_indicators.volume_ratio
                try:
                    await conn.execute(text("ALTER TABLE daily_indicators ADD COLUMN volume_ratio REAL"))
                    logger.info("[Schema] Added volume_ratio to daily_indicators")
                except Exception as e:
                    # Likely "duplicate column name" or table missing. Ignore.
                    pass
        except Exception as e:
             logger.warning(f"[Schema] Auto-migration check failed: {e}")

    async def hard_reset(self):
        """Delete DB and Re-init"""
        self._maintenance_event.clear()
        try:
            await self.engine.dispose()
            await asyncio.sleep(0.5)

            def remove_db_files(base_path):
                for ext in ['', '-shm', '-wal']:
                    f = base_path + ext
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                            logger.info(f"Removed {f}")
                        except Exception as e:
                            logger.warning(f"Failed to remove {f}: {e}")

            # Offload file deletion
            await ThreadPoolManager().run_async(TaskType.IO, remove_db_files, self.db_path)

            # Recreate Engine
            self._schema_initialized = False
            await self.init_db(force=True)

        finally:
            self._maintenance_event.set()

    async def clear_all_cache(self):
        """Drop all user tables and re-initialize schema."""
        self._maintenance_event.clear()
        try:
            # Dynamically query all user tables from sqlite_master
            # so we never miss newly added tables
            async with self.engine.begin() as conn:
                r = await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
                tables = [row[0] for row in r.fetchall()]

                for t in tables:
                    await conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{t}"')
                logger.info(f"[CacheManager] Dropped {len(tables)} tables: {tables}")

            self._schema_initialized = False
            await self.init_db(force=True)
        finally:
            self._maintenance_event.set()

    # --- DELAGATIONS START HERE ---

    # --- Stock Basic ---

    async def save_stock_basic(self, df, priority=None):
        return await self.stock_dao.save_stock_basic(df, priority)

    async def get_stock_basic(self):
        return await self.stock_dao.get_stock_basic()

    async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
        return await self.stock_dao.get_trade_cal(start_date, end_date, is_open)
    # --- Concepts ---
    async def save_concepts(self, df):
        return await self.stock_dao.save_concepts(df)

    async def overwrite_concepts(self, df):
        return await self.stock_dao.overwrite_concepts(df)


    async def get_concepts(self, ts_codes=None):
        """
        Get concepts for stock list.
        Returns: Dict[ts_code, List[concept_name]]
        """
        return await self.stock_dao.get_concepts(ts_codes)

    # --- Daily Quotes ---
    async def save_daily_quotes(self, df, priority=None, suppress_errors=True):
        return await self.quote_dao.save_daily_quotes(df, priority, suppress_errors=suppress_errors)

    async def check_data_exists(self, trade_date: str) -> bool:
        await self.wait_for_maintenance()
        return await self.quote_dao.check_data_exists(trade_date)

    async def get_daily_quotes(self, ts_code=None, start_date=None, end_date=None, ts_code_list=None):
        return await self.quote_dao.get_daily_quotes(ts_code, start_date, end_date, ts_code_list)

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df, suppress_errors=True):
        return await self.market_dao.save_daily_indicators(df, suppress_errors=suppress_errors)

    async def get_daily_indicators(self, ts_code=None, start_date=None, end_date=None, limit=None):
        return await self.market_dao.get_daily_indicators(ts_code, start_date, end_date, limit)

    # --- Adj Factor ---
    async def save_adj_factors(self, df, suppress_errors=True):
        return await self.market_dao.save_adj_factors(df, suppress_errors=suppress_errors)

    async def get_adj_factors(self, ts_code, start_date=None, end_date=None):
        return await self.market_dao.get_adj_factors(ts_code, start_date, end_date)

    async def get_latest_trade_date(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_latest_trade_date()

    async def get_cached_trade_dates(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_cached_trade_dates()

    # --- Indicators ---

    async def get_latest_indicators(self, trade_date=None):
        return await self.financial_dao.get_latest_indicators(trade_date)

    async def get_cached_indicator_dates(self):
        return await self.financial_dao.get_cached_indicator_dates()

    # --- Financial Reports ---
    async def save_financial_reports(self, df):
        return await self.financial_dao.save_financial_reports(df)

    async def get_latest_financials(self):
        return await self.financial_dao.get_latest_financials()

    async def get_cached_financial_records(self, period=None):
        return await self.financial_dao.get_cached_financial_records(period)

    # --- Other Data Types ---
    async def save_moneyflow(self, df):
        return await self.quote_dao.save_moneyflow(df)

    async def save_northbound(self, df):
        return await self.quote_dao.save_northbound(df)

    async def save_market_news(self, news_item, wait=False):
        return await self.market_dao.save_market_news(news_item, wait)

    async def get_market_news(self, limit=50, offset=0, min_publish_time=None):
        return await self.market_dao.get_market_news(limit, offset, min_publish_time)

    # --- Screening Data ---
    async def get_screening_data(self, trade_date=None):
        # Logic slightly complex, delegate to ScreenerDao but pass latest_trade_date func
        return await self.screener_dao.get_screening_data(trade_date, self.get_latest_trade_date)

    # --- Sync Stats & Misc ---
    async def update_sync_status(self, table_name, last_data_date, record_count, status='success'):
        return await self.sync_dao.update_sync_status(table_name, last_data_date, record_count, status)

    async def get_sync_status(self, table_name=None):
        return await self.sync_dao.get_sync_status(table_name)

    async def check_comprehensive_health(self):
        """Check coverage and freshness of all HEALTH_CHECK_TABLES."""
        await self.wait_for_maintenance()
        results = {}
        
        logger.info("[Health] Starting comprehensive data check...")

        async with self.engine.connect() as conn:
            # Total Stocks
            total_stocks = await self.stock_dao.get_active_stock_count()
            total_stocks = total_stocks or 1
            logger.info(f"[Health] Total Active Stocks: {total_stocks}")

            # Tables Check (Dynamic iteration based on registry)
            # Filter for quality monitored tables
            monitored_tables = {k: v for k, v in TABLE_DEFINITIONS.items() 
                               if v.get('quality_config', {}).get('monitor')}

            # === Global baseline precomputation (outside loop, executed once) ===
            global_trade_days = 0
            global_expected_rows = None
            try:
                r_min = await conn.exec_driver_sql("SELECT MIN(trade_date) FROM daily_quotes")
                r_max = await conn.exec_driver_sql("SELECT MAX(trade_date) FROM daily_quotes")
                g_min = r_min.fetchone()[0]
                g_max = r_max.fetchone()[0]
                if g_min and g_max:
                    r_days = await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM trade_cal WHERE is_open=1 AND cal_date >= ? AND cal_date <= ?",
                        (str(g_min), str(g_max))
                    )
                    global_trade_days = r_days.fetchone()[0] or 0
                    # Precise expected rows: sum per-stock trading days using each stock's list_date
                    r_exp = await conn.exec_driver_sql("""
                        SELECT SUM(
                            (SELECT COUNT(*) FROM trade_cal tc
                             WHERE tc.is_open = 1
                               AND tc.cal_date >= MAX(s.list_date, ?)
                               AND tc.cal_date <= ?)
                        ) FROM stock_basic s WHERE s.list_status = 'L'
                    """, (str(g_min), str(g_max)))
                    global_expected_rows = r_exp.fetchone()[0] or 1
                    logger.info(f"[Health] Global baseline: trade_days={global_trade_days}, expected_rows={global_expected_rows}")
            except Exception as e:
                logger.warning(f"[Health] Global baseline calc failed (non-fatal): {e}")

            for table, meta in monitored_tables.items():
                try:
                    # Determine check type from explicit metadata field
                    # Default to 'stock' if not specified
                    table_type = meta.get('type', 'stock')
                    is_stock_table = (table_type != 'global')

                    if not is_stock_table:
                        # Global Check: Just ensure data exists (>0 rows)
                        r = await conn.exec_driver_sql(f"SELECT count(*) FROM {table}")
                        cnt = r.fetchone()[0] or 0
                        ratio = 1.0 if cnt > 0 else 0.0
                        fresh_ratio = ratio  # Global: presence = fresh
                    else:
                        # Stock Coverage Check: Distinct Codes / Total Stocks
                        cols = meta.get('columns', {})
                        keys = meta.get('sync_config', {}).get('keys', [])
                        code_col = 'con_code' if 'con_code' in cols or 'con_code' in keys else 'ts_code'
                        
                        r = await conn.exec_driver_sql(f"SELECT count(DISTINCT {code_col}) FROM {table}")
                        cnt = r.fetchone()[0] or 0
                        ratio = min(1.0, cnt / total_stocks) if total_stocks > 0 else 0
                        
                        # Freshness Check: How recent is the latest data?
                        fresh_ratio = 0.0
                        # Try sync_config.date_col first, then common date columns
                        date_col_candidates = []
                        configured_col = meta.get('sync_config', {}).get('date_col')
                        if configured_col:
                            date_col_candidates.append(configured_col)
                        date_col_candidates.extend(['trade_date', 'end_date', 'ann_date'])
                        try:
                            max_date = None
                            for dc in date_col_candidates:
                                try:
                                    r2 = await conn.exec_driver_sql(f"SELECT MAX({dc}) FROM {table}")
                                    val = r2.fetchone()[0]
                                    if val:
                                        max_date = val
                                        break
                                except Exception:
                                    continue
                            if max_date:
                                max_dt = datetime.datetime.strptime(str(max_date)[:8], '%Y%m%d')
                                age_days = (datetime.datetime.now() - max_dt).days
                                    # Fresh if within 7 days, decay linearly to 30 days
                                if age_days <= 7:
                                    fresh_ratio = 1.0
                                elif age_days <= 30:
                                    fresh_ratio = max(0.0, 1.0 - (age_days - 7) / 23.0)
                                # else: 0.0 (stale)
                            
                            # Penalize freshness if coverage is trivial (< 1%)
                            # This prevents "100% Fresh" when only 1 stock has data.
                            if ratio < 0.01:
                                fresh_ratio = 0.0
                        except Exception:
                            pass  # Column may not exist, fresh_ratio stays 0
                    
                    # --- Depth (all stock tables, based on global trade days) ---
                    depth_ratio = None
                    if is_stock_table and global_trade_days > 0:
                        depth_ratio = min(1.0, global_trade_days / HEALTH_DEPTH_FULL_TRADE_DAYS)

                    # --- Breadth (daily-frequency tables only) ---
                    breadth_ratio = None
                    is_daily_freq = meta.get('quality_config', {}).get('frequency') == 'daily'
                    if is_daily_freq and global_expected_rows and global_expected_rows > 0:
                        try:
                            r_total = await conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}")
                            actual_rows = r_total.fetchone()[0] or 0
                            breadth_ratio = min(1.0, actual_rows / global_expected_rows)
                        except Exception:
                            pass

                    table_type = 'stock' if is_stock_table else 'global'
                    results[table] = {
                        'covered': cnt, 'ratio': ratio, 'fresh_ratio': fresh_ratio,
                        'depth_ratio': depth_ratio, 'breadth_ratio': breadth_ratio,
                        'type': table_type
                    }
                    
                    if ratio < 0.1:
                        if is_stock_table:
                            logger.warning(f"[Health] Table {table} coverage CRITICAL: {cnt}/{total_stocks} ({ratio:.1%})")
                        else:
                            logger.warning(f"[Health] Table {table} (global) CRITICAL: {cnt} records")
                    else:
                        if is_stock_table:
                            d_str = f", depth={depth_ratio:.1%}" if depth_ratio is not None else ""
                            b_str = f", breadth={breadth_ratio:.1%}" if breadth_ratio is not None else ""
                            logger.debug(f"[Health] Table {table}: {cnt}/{total_stocks} ({ratio:.1%}), fresh={fresh_ratio:.0%}{d_str}{b_str}")
                        else:
                            logger.debug(f"[Health] Table {table} (global): {cnt} records")
                except Exception as e:
                    if "no such table" in str(e):
                        logger.warning(f"[Health] Table {table} missing/not created yet.")
                    else:
                        logger.error(f"[Health] Failed to check table {table}: {e}")
                    results[table] = {'covered': 0, 'ratio': 0, 'fresh_ratio': 0, 'depth_ratio': None, 'breadth_ratio': None, 'type': meta.get('type', 'stock')}

        return {'total_stocks': total_stocks, 'tables': results}

    async def get_concept_count(self):
        """Get total count of stock concept mappings."""
        try:
            async with self.engine.connect() as conn:
                r = await conn.exec_driver_sql("SELECT COUNT(*) FROM stock_concepts")
                return r.scalar() or 0
        except Exception as e:
            # logger.warning(f"Failed to count concepts: {e}")
            return 0

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df):
        return await self.financial_dao.save_fina_forecast(df)

    async def save_holder_number(self, df):
        return await self.holder_dao.save_holder_number(df)

    async def save_top10_holders(self, df):
        return await self.holder_dao.save_top10_holders(df)

    async def save_fina_mainbz(self, df):
        return await self.financial_dao.save_fina_mainbz(df)

    async def save_pledge_stat(self, df):
        return await self.financial_dao.save_pledge_stat(df)

    async def save_repurchase(self, df):
        return await self.financial_dao.save_repurchase(df)

    async def save_dividend(self, df):
        return await self.financial_dao.save_dividend(df)

    async def save_index_daily(self, df):
        return await self.quote_dao.save_index_daily(df)

    async def save_index_dailybasic(self, df):
        return await self.quote_dao.save_index_dailybasic(df)

    async def get_index_daily(self, ts_code=None, trade_date=None):
        return await self.quote_dao.get_index_daily(ts_code, trade_date)

    async def save_limit_list(self, df):
        return await self.quote_dao.save_limit_list(df)

    async def save_margin_daily(self, df):
        return await self.quote_dao.save_margin_daily(df)

    async def save_suspend_d(self, df):
        return await self.quote_dao.save_suspend_d(df)

    async def save_fina_audit(self, df):
        return await self.financial_dao.save_fina_audit(df)

    async def save_top_list(self, df):
        return await self.quote_dao.save_top_list(df)

    async def get_top_list(self, trade_date=None):
        return await self.quote_dao.get_top_list(trade_date)

    async def save_block_trade(self, df):
        return await self.quote_dao.save_block_trade(df)

    async def get_block_trade(self, trade_date=None):
        return await self.quote_dao.get_block_trade(trade_date)

    async def get_moneyflow(self, trade_date=None, ts_code=None):
        return await self.quote_dao.get_moneyflow(trade_date, ts_code)

    async def get_northbound(self, trade_date=None, ts_code=None):
        return await self.quote_dao.get_northbound(trade_date, ts_code)

    async def save_moneyflow_hsgt(self, df):
        return await self.quote_dao.save_moneyflow_hsgt(df)

    # --- Screening History ---
    async def get_screening_history(self, strategy_name=None, limit=100):
        return await self.screener_dao.get_screening_history(strategy_name, limit)

    async def get_history_tree(self, offset=0, limit=30):
        return await self.screener_dao.get_history_tree(offset, limit)

    async def get_history_records(self, trade_date, strategy_name=None):
        return await self.screener_dao.get_history_records(trade_date, strategy_name)

    async def get_pending_reviews(self):
        return await self.screener_dao.get_pending_reviews()

    async def update_screening_performance(self, updates):
        return await self.screener_dao.update_screening_performance(updates)

    async def get_learning_examples(self, limit=3):
        return await self.screener_dao.get_learning_examples(limit)

    # --- Sync Status Step 4 ---
    async def get_completed_step4_stocks(self, sync_version=1):
        return await self.sync_dao.get_completed_step4_stocks(sync_version)

    async def mark_stock_step4_completed(self, ts_code, sync_version=1):
        return await self.sync_dao.mark_stock_step4_completed(ts_code, sync_version)

    async def clear_step4_sync_status(self):
        return await self.sync_dao.clear_step4_sync_status()

    # --- Trade Calendar Logic ---
    async def get_trade_cal_range(self):
        """Get the min and max calendar dates from DB"""
        return await self.stock_dao.get_trade_cal_range()

    async def save_trade_cal(self, df):
        return await self.stock_dao.save_trade_cal(df)

    async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
        return await self.stock_dao.get_trade_cal(start_date, end_date, is_open)

    async def get_latest_northbound(self):
        return await self.quote_dao.get_latest_northbound()

    # --- Policy-Driven AI Extensions ---

    # Macro
    async def save_macro_economy(self, df):
        return await self.macro_dao.save_macro_economy(df)

    async def save_shibor_daily(self, df):
        return await self.macro_dao.save_shibor_daily(df)

    # Holders
    async def save_holder_number(self, df):
        return await self.holder_dao.save_holder_number(df)

    async def save_top10_holders(self, df):
        return await self.holder_dao.save_top10_holders(df)

    async def get_holder_data_coverage(self, ts_codes):
        return await self.holder_dao.check_holder_data_coverage(ts_codes)

    # Extended Market (Adj Factor, Index Weight via MarketDao)

    async def save_index_weights(self, df):
        return await self.market_dao.save_index_weights(df)

    async def get_index_weights(self, index_code, trade_date):
        return await self.market_dao.get_index_weights(index_code, trade_date)

    async def get_latest_index_weight_date(self):
        return await self.market_dao.get_latest_index_weight_date()

    async def save_moneyflow_hsgt(self, df):
        return await self.market_dao.save_moneyflow_hsgt(df)

    async def get_moneyflow_hsgt(self, trade_date=None, limit=None):
        return await self.market_dao.get_moneyflow_hsgt(trade_date, limit)
