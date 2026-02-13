import asyncio
import datetime
import logging
import os

from sqlalchemy import text, event, Engine
from sqlalchemy.ext.asyncio import create_async_engine

import config
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType
from data.constants import HEALTH_CHECK_TABLES
# DAOs
from data.daos.base_dao import BaseDao  # Expose static helpers via BaseDao if needed, or keeping usage internal
from data.daos.financial_dao import FinancialDao
from data.daos.market_dao import MarketDao
from data.daos.quote_dao import QuoteDao
from data.daos.screener_dao import ScreenerDao
from data.daos.stock_dao import StockDao
from data.daos.sync_dao import SyncDao

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

    def __new__(cls):
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
        db_pool_size = ConfigHandler.get_db_queue_size()
        
        self.engine = create_async_engine(
            connection_string,
            echo=False,
            # Pool settings for high concurrency
            pool_size=db_pool_size,  # Configurable (default 1024)
            max_overflow=max(30, int(db_pool_size * 0.1)),  # Allow 10% overflow or at least 30
            pool_timeout=60,  # Wait up to 60s for connection
            future=True
        )

        self._maintenance_event = asyncio.Event()
        self._maintenance_event.set()
        self._init_lock = asyncio.Lock()

        # Initialize DAOs
        self.stock_dao = StockDao(self.engine)
        self.quote_dao = QuoteDao(self.engine)
        self.financial_dao = FinancialDao(self.engine)
        self.sync_dao = SyncDao(self.engine)
        self.market_dao = MarketDao(self.engine)
        self.screener_dao = ScreenerDao(self.engine)

        self._initialized = True
        self._schema_initialized = False
        logger.info(f"[CacheManager] Initialized with SQLAlchemy AsyncEngine: {self.db_path}")

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
        """Drop tables approach"""
        self._maintenance_event.clear()
        try:
            tables = ["daily_quotes", "daily_indicators", "financial_reports", "moneyflow_daily",
                      "northbound_holding", "sync_status", "screening_history", "top_list", "block_trade",
                      "market_news", "trade_cal", "stock_basic"]

            # Using BaseDao for ad-hoc execution
            dao = BaseDao(self.engine)
            async with self.engine.begin() as conn:
                for t in tables:
                    await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")

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

    async def get_stock_basic(self):
        return await self.stock_dao.get_stock_basic()

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
    async def save_daily_quotes(self, df, priority=None):
        return await self.quote_dao.save_daily_quotes(df, priority)

    async def check_data_exists(self, trade_date: str) -> bool:
        await self.wait_for_maintenance()
        return await self.quote_dao.check_data_exists(trade_date)

    async def get_daily_quotes(self, ts_code=None, start_date=None, end_date=None, ts_code_list=None):
        return await self.quote_dao.get_daily_quotes(ts_code, start_date, end_date, ts_code_list)

    async def get_latest_trade_date(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_latest_trade_date()

    async def get_cached_trade_dates(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_cached_trade_dates()

    # --- Indicators ---
    async def save_daily_indicators(self, df, priority=None):
        return await self.financial_dao.save_daily_indicators(df, priority)

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
        # This was simplified in plan, but keeping original logic structure via direct SQL is easiest for now
        # Or move to new HealthDao?
        # Let's keep it here but clean it up using helper
        await self.wait_for_maintenance()
        results = {}

        async with self.engine.connect() as conn:
            # Total Stocks
            r = await conn.exec_driver_sql("SELECT count(*) FROM stock_basic WHERE list_status='L'")
            total_stocks = r.fetchone()[0] or 1

            # Tables
            for table in HEALTH_CHECK_TABLES:
                r = await conn.exec_driver_sql(f"SELECT count(DISTINCT ts_code) FROM {table}")
                cnt = r.fetchone()[0] or 0
                results[table] = {'covered': cnt, 'ratio': cnt / total_stocks}

        return {'total_stocks': total_stocks, 'tables': results}

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df):
        return await self.financial_dao.save_fina_forecast(df)

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

    # --- Screening History ---
    async def save_screening_result(self, df, strategy_name, trade_date):
        return await self.screener_dao.save_screening_result(df, strategy_name, trade_date)

    async def get_screening_history(self, strategy_name=None, limit=100):
        return await self.screener_dao.get_screening_history(strategy_name, limit)

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
