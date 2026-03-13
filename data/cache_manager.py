import asyncio
import datetime
import logging
import os
import threading

import re
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

import config
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType
from data.data_dictionary import TABLE_DEFINITIONS
from data.constants import get_health_depth_full_trade_days

# DAOs
from data.daos.base_dao import (
    BaseDao,
)  # Expose static helpers via BaseDao if needed, or keeping usage internal
from data.daos.financial_dao import FinancialDao
from data.daos.market_dao import MarketDao
from data.daos.quote_dao import QuoteDao
from data.daos.screener_dao import ScreenerDao
from data.daos.stock_dao import StockDao
from data.daos.sync_dao import SyncDao
from data.daos.macro_dao import MacroDao
from data.daos.holder_dao import HolderDao
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


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

        connection_string = (
            ConfigHandler.get_db_url()
            if hasattr(ConfigHandler, "get_db_url")
            else config.DB_URL
        )

        # Load pool settings from config
        try:
            db_pool_size = int(ConfigHandler.get_db_connection_pool_size())
        except (TypeError, ValueError):
            db_pool_size = 5  # Safe fallback

        self.engine = create_async_engine(
            connection_string,
            echo=False,
            pool_size=db_pool_size,
            max_overflow=max(5, int(db_pool_size * 0.5)),
            pool_timeout=60,
            pool_recycle=1800,  # Recycle connections after 30 minutes
            pool_pre_ping=True,  # Pessimistic disconnect handling (crucial for protecting against WinError 10054)
            future=True,
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
        logger.debug(
            f"[CacheManager] State | Initialized with AsyncEngine: {connection_string}"
        )

    @property
    def _maintenance_event(self):
        """Get or create maintenance event dynamically per event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Event()  # Dummy fallback if no loop

        if not hasattr(current_loop, "_cache_maint_event"):
            evt = asyncio.Event()
            evt.set()  # Default to Set (Not in maintenance)
            setattr(current_loop, "_cache_maint_event", evt)

        return getattr(current_loop, "_cache_maint_event")

    @property
    def _init_lock(self):
        """Get or create initialization lock dynamically per event loop to avoid cross-loop binding deadlocks."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "[CacheManager] Config | ⚠️ No running event loop for _init_lock. Using dummy."
            )

            class DummyLock:
                async def __aenter__(self):
                    return

                async def __aexit__(self, *args):
                    return

            return DummyLock()

        if not hasattr(current_loop, "_cache_init_lock"):
            setattr(current_loop, "_cache_init_lock", asyncio.Lock())

        return getattr(current_loop, "_cache_init_lock")

    async def close(self):
        """Dispose the engine"""
        logger.debug("[CacheManager] State | Disposing engine...")
        # Unblock any coroutines waiting on maintenance before disposing
        self._maintenance_event.set()
        try:
            from data.daos.base_dao import BaseDao

            BaseDao._get_maintenance_event().set()
        except Exception:
            pass
        await self.engine.dispose()
        
        # Cleanup loop-bound locks to prevent cross-test contamination in isolated async environments
        try:
            current_loop = asyncio.get_running_loop()
            if hasattr(current_loop, "_cache_maint_event"):
                delattr(current_loop, "_cache_maint_event")
            if hasattr(current_loop, "_cache_init_lock"):
                delattr(current_loop, "_cache_init_lock")
        except RuntimeError:
            pass

    # --- Maintenance & Helpers ---
    async def wait_for_maintenance(self):
        if not self._maintenance_event.is_set():
            pass  # logger.info("[CacheManager] Waiting for maintenance...") removed
            await self._maintenance_event.wait()

    @staticmethod
    def _prepare_data_params(df, cols, date_cols=None):
        # Facade: Delegate to BaseDao static method
        return BaseDao._prepare_data_params(df, cols, date_cols)

    @staticmethod
    def normalize_news_item(item, default_source="CLS"):
        """Normalize news item dictionary for DB Insertion"""
        return {
            "content": item.get("content", "").strip(),
            "tags": item.get("tags", ""),
            "publish_time": item.get(
                "time",
                item.get("publish_time", get_now().strftime("%Y-%m-%d %H:%M:%S")),
            ),
            "source": item.get("source", default_source),
        }

    # Backward compatibility for direct SQL usage if any
    async def _write_db(self, sql, params=None, is_many=False):
        # We can implement a temporary BaseDao to run this?
        # Or just instantiate a base dao for ad-hoc queries.
        # Ideally, usages should be migrated, but for now:
        dao = BaseDao(self.engine)
        return await dao._write_db(sql, params, is_many)

    async def _read_db(self, sql, params=None):
        dao = BaseDao(self.engine)
        return await dao._read_db(sql, params)


    # --- Init & Reset ---
    async def init_db(self, force=False):
        """Initialize Tables"""
        async with self._init_lock:
            if self._schema_initialized and not force:
                return

            logger.debug("[CacheManager] Schema | Initializing via Alembic...")

            # Check DB schema state to handle legacy users transitioning to Alembic
            has_alembic, has_old_schema = False, False
            try:
                from sqlalchemy import inspect

                async with self.engine.connect() as conn:

                    def _sync_check(c):
                        inspector = inspect(c)
                        tables = inspector.get_table_names()
                        return "alembic_version" in tables, "stock_basic" in tables

                    has_alembic, has_old_schema = await conn.run_sync(_sync_check)
            except Exception as e:
                logger.error(
                    f"[CacheManager] Schema | ❌ Table inspection failed: {e}",
                    exc_info=True,
                )

            def run_alembic_upgrade():
                from alembic.config import Config
                from alembic import command

                alembic_ini_path = os.path.join(
                    os.path.dirname(__file__), "..", "alembic.ini"
                )
                alembic_cfg = Config(alembic_ini_path)
                alembic_cfg.attributes["configure_logger"] = False
                # Set the base directory so alembic can find its scripts
                alembic_cfg.set_main_option(
                    "script_location",
                    os.path.join(os.path.dirname(__file__), "..", "alembic"),
                )

                # If the legacy database exists but Alembic doesn't, stamp it with the baseline
                # (which exactly matches the old schema.sql) to prevent 'table already exists' errors.
                if has_old_schema and not has_alembic:
                    logger.debug(
                        "[CacheManager] Schema | Legacy database detected. Stamped baseline."
                    )
                    command.stamp(alembic_cfg, "367c382dbf28")

                command.upgrade(alembic_cfg, "head")

            try:
                # Run alembic synchronously in thread pool
                await ThreadPoolManager().run_async(TaskType.IO, run_alembic_upgrade)

                self._schema_initialized = True
                logger.debug("[CacheManager] Schema | Init completed without errors.")

            except Exception as e:
                logger.error(
                    f"[CacheManager] Schema | ❌ Init failed critically: {e}",
                    exc_info=True,
                )

    async def hard_reset(self):
        """Hard reset by clearing all tables (dropping them) and reinitializing via Alembic."""
        try:
            await self.clear_all_cache()
            logger.info("[CacheManager] Wipe | Hard reset completed.")
        except Exception as e:
            logger.error(
                f"[CacheManager] Wipe | ❌ Error during hard reset: {e}", exc_info=True
            )
            raise

    async def clear_all_cache(self):
        """Drop all user tables and re-initialize schema."""
        self._maintenance_event.clear()
        from data.daos.base_dao import BaseDao

        BaseDao._get_maintenance_event().clear()
        try:
            # Dynamically query all user tables from PostgreSQL catalog
            async with self.engine.begin() as conn:
                r = await conn.exec_driver_sql(
                    "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
                )
                tables = [row[0] for row in r.fetchall()]

                for t in tables:
                    if not re.match(r"^[a-zA-Z0-9_]+$", t):
                        logger.warning(
                            f"[CacheManager] Wipe | ⚠️ Malformed table name skipped: {t}"
                        )
                        continue
                    await conn.execute(sa.text(f'DROP TABLE IF EXISTS "{t}"'))
                logger.debug(f"[CacheManager] Wipe | Dropped {len(tables)} tables.")

            self._schema_initialized = False
            await self.init_db(force=True)
        finally:
            try:
                from data.daos.base_dao import BaseDao

                BaseDao._get_maintenance_event().set()
            except Exception:
                pass
            self._maintenance_event.set()

    # --- DELAGATIONS START HERE ---

    # --- Stock Basic ---

    async def save_stock_basic(self, df, priority=None):
        return await self.stock_dao.save_stock_basic(df, priority)

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
    async def save_daily_quotes(self, df, priority=None, suppress_errors=True):
        return await self.quote_dao.save_daily_quotes(
            df, priority, suppress_errors=suppress_errors
        )

    async def check_data_exists(self, trade_date: str) -> bool:
        await self.wait_for_maintenance()
        return await self.quote_dao.check_data_exists(trade_date)

    async def get_daily_quotes(
        self, ts_code=None, start_date=None, end_date=None, ts_code_list=None
    ):
        return await self.quote_dao.get_daily_quotes(
            ts_code, start_date, end_date, ts_code_list
        )

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df, suppress_errors=True):
        return await self.market_dao.save_daily_indicators(
            df, suppress_errors=suppress_errors
        )

    async def get_daily_indicators(
        self, ts_code=None, start_date=None, end_date=None, limit=None
    ):
        return await self.market_dao.get_daily_indicators(
            ts_code, start_date, end_date, limit
        )

    # --- Adj Factor ---
    async def save_adj_factors(self, df, suppress_errors=True):
        return await self.market_dao.save_adj_factors(
            df, suppress_errors=suppress_errors
        )

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
        # P0-2: DAO now self-resolves latest_trade_date internally (Defense in Depth)
        return await self.screener_dao.get_screening_data(trade_date)

    # --- Sync Stats & Misc ---
    async def update_sync_status(
        self, table_name, last_data_date, record_count, status="success"
    ):
        return await self.sync_dao.update_sync_status(
            table_name, last_data_date, record_count, status
        )

    async def get_sync_status(self, table_name=None):
        return await self.sync_dao.get_sync_status(table_name)

    async def check_comprehensive_health(self):
        """Check coverage and freshness of all HEALTH_CHECK_TABLES."""
        await self.wait_for_maintenance()
        results = {}

        logger.debug(f"[CacheManager] Health | Starting comprehensive check...")

        async with self.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            # Total Stocks
            total_stocks = await self.stock_dao.get_active_stock_count()
            total_stocks = total_stocks or 1
            logger.debug(
                f"[CacheManager] Health | Active stocks baseline: {total_stocks}"
            )

            # Tables Check (Dynamic iteration based on registry)
            # Filter for quality monitored tables
            monitored_tables = {
                k: v
                for k, v in TABLE_DEFINITIONS.items()
                if v.get("quality_config", {}).get("monitor")
            }

            # === Global baseline precomputation (outside loop, executed once) ===
            global_trade_days = 0
            global_expected_rows = None
            try:
                r_min = await conn.exec_driver_sql(
                    "SELECT MIN(trade_date) FROM daily_quotes"
                )
                r_max = await conn.exec_driver_sql(
                    "SELECT MAX(trade_date) FROM daily_quotes"
                )
                row_min = r_min.fetchone()
                row_max = r_max.fetchone()
                g_min = row_min[0] if row_min else None
                g_max = row_max[0] if row_max else None
                if g_min and g_max:
                    r_days = await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2",
                        (str(g_min), str(g_max)),
                    )
                    row_days = r_days.fetchone()
                    global_trade_days = (row_days[0] if row_days else 0) or 0
                    # Precise expected rows: sum per-stock trading days using each stock's list_date
                    r_exp = await conn.exec_driver_sql(
                        """
                        SELECT SUM(
                            (SELECT COUNT(*) FROM trade_cal tc
                             WHERE tc.is_open = 1
                               AND tc.cal_date >= GREATEST(s.list_date, $1)
                               AND tc.cal_date <= $2)
                        ) FROM stock_basic s
                        WHERE s.list_status = 'L'
                        """,
                        (str(g_min), str(g_max)),
                    )
                    row_exp = r_exp.fetchone()
                    global_expected_rows = (row_exp[0] if row_exp else 1) or 1
                    logger.debug(
                        f"[CacheManager] Health | Baseline: trade_days={global_trade_days}, expected_rows={global_expected_rows}"
                    )
            except Exception as e:
                logger.warning(
                    f"[CacheManager] Health | ⚠️ Baseline calc failed (non-fatal): {e}"
                )

            for table, meta in monitored_tables.items():
                try:
                    # Determine check type from explicit metadata field
                    # Default to 'stock' if not specified
                    table_type = meta.get("type", "stock")
                    is_stock_table = table_type != "global"

                    if not is_stock_table:
                        # Global Check: Just ensure data exists (>0 rows)
                        tbl = sa.table(table)
                        r = await conn.execute(
                            sa.select(sa.func.count()).select_from(tbl)
                        )
                        cnt = r.scalar() or 0
                        ratio = 1.0 if cnt > 0 else 0.0
                        fresh_ratio = ratio  # Global: presence = fresh
                    else:
                        # Stock Coverage Check: Distinct Codes / Total Stocks
                        cols = meta.get("columns", {})
                        keys = meta.get("sync_config", {}).get("keys", [])
                        code_col = (
                            "con_code"
                            if "con_code" in cols or "con_code" in keys
                            else "ts_code"
                        )

                        tbl = sa.table(table)
                        r = await conn.execute(
                            sa.select(
                                sa.func.count(sa.distinct(sa.column(code_col)))
                            ).select_from(tbl)
                        )
                        cnt = r.scalar() or 0
                        ratio = min(1.0, cnt / total_stocks) if total_stocks > 0 else 0

                        # Freshness Check: How recent is the latest data?
                        fresh_ratio = 0.0
                        # Try sync_config.date_col first, then common date columns
                        date_col_candidates = []
                        configured_col = meta.get("sync_config", {}).get("date_col")
                        if configured_col:
                            date_col_candidates.append(configured_col)
                        date_col_candidates.extend(
                            ["trade_date", "end_date", "ann_date"]
                        )
                        try:
                            max_date = None
                            for dc in date_col_candidates:
                                try:
                                    tbl_d = sa.table(table)
                                    r2 = await conn.execute(
                                        sa.select(
                                            sa.func.max(sa.column(dc))
                                        ).select_from(tbl_d)
                                    )
                                    val = r2.scalar()
                                    if val:
                                        max_date = val
                                        break
                                except Exception:
                                    continue  # Column doesn't exist in this table
                            if max_date:
                                max_dt = datetime.datetime.strptime(
                                    str(max_date)[:8], "%Y%m%d"
                                )
                                age_days = (get_now() - max_dt).days
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
                            logger.debug(
                                f"[CacheManager] Health | Freshness check skipped for {table}: no valid date column"
                            )

                    # --- Depth (all stock tables, based on global trade days) ---
                    depth_ratio = None
                    if is_stock_table and global_trade_days > 0:
                        depth_ratio = min(
                            1.0, global_trade_days / get_health_depth_full_trade_days()
                        )

                    # --- Breadth (daily-frequency tables only) ---
                    breadth_ratio = None
                    is_daily_freq = (
                        meta.get("quality_config", {}).get("frequency") == "daily"
                    )
                    if (
                        is_daily_freq
                        and global_expected_rows
                        and global_expected_rows > 0
                    ):
                        try:
                            tbl_b = sa.table(table)
                            r_total = await conn.execute(
                                sa.select(sa.func.count()).select_from(tbl_b)
                            )
                            actual_rows = r_total.scalar() or 0
                            breadth_ratio = min(1.0, actual_rows / global_expected_rows)
                        except Exception:
                            logger.debug(
                                f"[CacheManager] Health | Breadth calc failed for {table}"
                            )

                    table_type = "stock" if is_stock_table else "global"
                    results[table] = {
                        "covered": cnt,
                        "ratio": ratio,
                        "fresh_ratio": fresh_ratio,
                        "depth_ratio": depth_ratio,
                        "breadth_ratio": breadth_ratio,
                        "type": table_type,
                    }

                    if ratio < 0.1:
                        if is_stock_table:
                            logger.warning(
                                f"[CacheManager] Health | ⚠️ Table {table} coverage CRITICAL: {cnt}/{total_stocks} ({ratio:.1%})"
                            )
                        else:
                            logger.warning(
                                f"[CacheManager] Health | ⚠️ Table {table} (global) CRITICAL: {cnt} records"
                            )
                    else:
                        if is_stock_table:
                            d_str = (
                                f", depth={depth_ratio:.1%}"
                                if depth_ratio is not None
                                else ""
                            )
                            b_str = (
                                f", breadth={breadth_ratio:.1%}"
                                if breadth_ratio is not None
                                else ""
                            )
                            logger.debug(
                                f"[CacheManager] Health | Table {table}: {cnt}/{total_stocks} ({ratio:.1%}), fresh={fresh_ratio:.0%}{d_str}{b_str}"
                            )
                        else:
                            logger.debug(
                                f"[CacheManager] Health | Table {table} (global): {cnt} records"
                            )
                except Exception as e:
                    if "no such table" in str(e):
                        logger.warning(
                            f"[CacheManager] Health | ⚠️ Table {table} missing/not created yet."
                        )
                    else:
                        logger.error(
                            f"[CacheManager] Health | ❌ Failed to check table {table}: {e}",
                            exc_info=True,
                        )
                    results[table] = {
                        "covered": 0,
                        "ratio": 0,
                        "fresh_ratio": 0,
                        "depth_ratio": None,
                        "breadth_ratio": None,
                        "type": meta.get("type", "stock"),
                    }

        return {"total_stocks": total_stocks, "tables": results}

    async def get_concept_count(self):
        """Get total count of stock concept mappings."""
        return await self.stock_dao.get_concept_count()

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
