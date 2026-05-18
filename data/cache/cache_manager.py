import asyncio
import datetime
import logging
import re
import threading
import typing

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import config
from data.constants import get_health_depth_full_trade_days
from data.data_dictionary import TABLE_DEFINITIONS

# DAOs
from data.persistence.daos.base_dao import (
    BaseDao,
)  # Expose static helpers via BaseDao if needed, or keeping usage internal
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.macro_dao import MacroDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.stock_dao import StockDao
from data.persistence.daos.sync_dao import SyncDao
from utils.config_handler import ConfigHandler
from utils.loop_local import del_loop_local, get_loop_local
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


from utils.singleton_registry import register_singleton


@register_singleton
class CacheManager:
    _instance = None
    _initialized = False
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def __init__(self):
        if self._initialized:
            return

        connection_string = self._get_connection_string()

        self._maintenance_event_lazy = None
        self._init_lock_lazy = None

        self.engine: AsyncEngine | None = None
        self._disposed = False

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

        if not connection_string:
            logger.debug(
                "[CacheManager] No DB_URL configured, skipping engine creation. "
                "Engine will be created after onboarding wizard completes."
            )
            return

        self._create_engine(connection_string)
        logger.debug("[CacheManager] Initialized with AsyncEngine: %s", self._sanitize_url(connection_string))

    def _get_connection_string(self) -> str | None:
        """Get database connection string from config."""
        if hasattr(ConfigHandler, "get_db_url"):
            url = ConfigHandler.get_db_url()
            if url:
                return url
        return config.DB_URL

    def _sanitize_url(self, url: str) -> str:
        """Sanitize URL for logging (hide password)."""
        if not url:
            return "None"
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)

    def _create_engine(self, connection_string: str):
        """Create async engine and update DAO references."""
        try:
            db_pool_size = int(ConfigHandler.get_db_connection_pool_size())
        except (TypeError, ValueError):
            db_pool_size = 10

        try:
            db_max_overflow = int(ConfigHandler.get_db_max_overflow())
        except (TypeError, ValueError):
            db_max_overflow = 5

        try:
            db_pool_timeout = int(ConfigHandler.get_db_pool_timeout())
        except (TypeError, ValueError):
            db_pool_timeout = 30

        try:
            db_pool_recycle = int(ConfigHandler.get_db_pool_recycle())
        except (TypeError, ValueError):
            db_pool_recycle = 1800

        try:
            db_pool_pre_ping = ConfigHandler.get_db_pool_pre_ping()
        except (TypeError, ValueError):
            db_pool_pre_ping = True

        self.engine = create_async_engine(
            connection_string,
            echo=False,
            pool_size=db_pool_size,
            max_overflow=db_max_overflow,
            pool_timeout=db_pool_timeout,
            pool_recycle=db_pool_recycle,
            pool_pre_ping=db_pool_pre_ping,
            future=True,
        )

        self.stock_dao.engine = self.engine
        self.quote_dao.engine = self.engine
        self.financial_dao.engine = self.engine
        self.sync_dao.engine = self.engine
        self.market_dao.engine = self.engine
        self.screener_dao.engine = self.engine
        self.macro_dao.engine = self.engine
        self.holder_dao.engine = self.engine

        logger.debug("[CacheManager] Engine created: %s", self._sanitize_url(connection_string))

    @property
    def _maintenance_event(self):
        """Get or create maintenance event dynamically per event loop."""

        def _factory():
            evt = asyncio.Event()
            evt.set()
            return evt

        return get_loop_local("cache_maint_event", _factory)

    @property
    def _init_lock(self):
        """Get or create initialization lock dynamically per event loop to avoid cross-loop binding deadlocks."""

        def _factory():
            return asyncio.Lock()

        return get_loop_local("cache_init_lock", _factory)

    async def close(self):
        """Dispose the engine"""
        logger.debug("[CacheManager] State | Disposing engine...")
        self._disposed = True
        if self.engine is not None:
            await self.engine.dispose()
        try:
            from data.persistence.daos.base_dao import BaseDao

            BaseDao._get_maintenance_event().set()
        except Exception as e:
            logger.debug("[CacheManager] Maintenance event set failed during dispose: %s", e)
        self._maintenance_event.set()

        # Cleanup loop-bound locks to prevent cross-test contamination in isolated async environments
        del_loop_local("cache_maint_event")
        del_loop_local("cache_init_lock")

    # --- Maintenance & Helpers ---
    async def wait_for_maintenance(self):
        if not self._maintenance_event.is_set():
            pass  # logger.info("[CacheManager] Waiting for maintenance...") removed
            await self._maintenance_event.wait()

    @staticmethod
    def _prepare_data_params(df: pd.DataFrame, cols: list, table_name: str | None = None):
        # Facade: Delegate to BaseDao static method
        return BaseDao._prepare_data_params(df, cols, table_name)

    @staticmethod
    def normalize_news_item(item: dict, default_source: typing.Any = "CLS"):
        """Normalize news item dictionary for DB Insertion"""
        publish_time = item.get("time", item.get("publish_time"))
        if publish_time is None:
            publish_time = get_now().replace(tzinfo=None)
        elif isinstance(publish_time, str):
            try:
                publish_time = pd.to_datetime(publish_time).to_pydatetime()
            except (ValueError, TypeError) as e:
                logger.debug("[CacheManager] Failed to parse publish_time '%s': %s", publish_time, e)
                publish_time = get_now().replace(tzinfo=None)

        return {
            "content": item.get("content", "").strip(),
            "tags": item.get("tags", ""),
            "publish_time": publish_time,
            "source": item.get("source", default_source),
        }

    # Backward compatibility for direct SQL usage if any
    async def write_db(self, sql: typing.Any, params: typing.Any = None, is_many: typing.Any = False):
        dao = BaseDao(self.engine)
        return await dao._write_db(sql, params, is_many, suppress_errors=True)

    async def read_db(self, sql: typing.Any, params: typing.Any = None):
        dao = BaseDao(self.engine)
        return await dao._read_db(sql, params, suppress_errors=True)

    _write_db = write_db
    _read_db = read_db

    # --- Init & Reset ---
    async def init_db(self, force: bool = False):
        """Initialize Tables"""
        async with self._init_lock:
            if self._schema_initialized and not force:
                return

            if self.engine is None:
                connection_string = self._get_connection_string()
                if not connection_string:
                    raise RuntimeError("Database URL not configured. Please complete the onboarding wizard first.")
                self._create_engine(connection_string)

            logger.debug("[CacheManager] Schema | Delegating to DatabaseMigrator...")

            from data.persistence.db_migrator import DatabaseMigrator, DatabaseMigrationNeeded

            try:
                await DatabaseMigrator.init_db(self.engine)

                self._schema_initialized = True
                logger.debug("[CacheManager] Schema | Init completed without errors.")
            except DatabaseMigrationNeeded:
                self._schema_initialized = True
                logger.info(
                    "[CacheManager] Schema | Database needs migration but AUTO_MIGRATE is disabled. "
                    "Propagating to caller for UI handling."
                )
                raise
            except Exception as e:
                logger.error(
                    f"[CacheManager] Schema | Init failed critically: {e}",
                    exc_info=True,
                )
                raise

    async def hard_reset(self):
        """Hard reset by clearing all tables (dropping them) and reinitializing via Alembic."""
        try:
            await self.clear_all_cache()
            logger.info("[CacheManager] Wipe | Hard reset completed.")
        except Exception as e:
            logger.error(
                f"[CacheManager] Wipe | ❌ Error during hard reset: {e}",
                exc_info=True,
            )
            raise

    async def clear_all_cache(self):
        """Drop all user tables using SQLAlchemy DDL API and re-initialize schema."""
        self._maintenance_event.clear()
        from data.persistence.daos.base_dao import BaseDao
        from data.persistence.models import metadata

        BaseDao._get_maintenance_event().clear()
        try:
            if self.engine is None:
                raise RuntimeError("Database engine not initialized")
            async with self.engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)
                await conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
                logger.debug("[CacheManager] Wipe | Dropped all tables via SQLAlchemy DDL API.")

            self._schema_initialized = False
            await self.init_db(force=True)
        finally:
            try:
                from data.persistence.daos.base_dao import BaseDao

                BaseDao._get_maintenance_event().set()
            except Exception as e:
                logger.debug("[CacheManager] Failed to set BaseDao maintenance event: %s", e)
            self._maintenance_event.set()

    # --- DELAGATIONS START HERE ---

    # --- Stock Basic ---

    async def save_stock_basic(self, df: pd.DataFrame, priority: int | None = None):
        return await self.stock_dao.save_stock_basic(df, priority)

    async def get_stock_basic(self):
        return await self.stock_dao.get_stock_basic()

    # --- Concepts ---
    async def save_concepts(self, df: pd.DataFrame):
        return await self.stock_dao.save_concepts(df)

    async def overwrite_concepts(self, df: pd.DataFrame):
        return await self.stock_dao.overwrite_concepts(df)

    async def get_concepts(self, ts_codes: list | None = None):
        """
        Get concepts for stock list.
        Returns: Dict[ts_code, List[concept_name]]
        """
        return await self.stock_dao.get_concepts(ts_codes)  # type: ignore[return-value]  # DAO return type may vary from expected dict structure

    # --- Daily Quotes ---
    async def save_daily_quotes(
        self,
        df: pd.DataFrame,
        priority: int | None = None,
        suppress_errors: bool = False,
    ):
        return await self.quote_dao.save_daily_quotes(
            df,
            priority,
            suppress_errors=suppress_errors,
        )

    async def check_data_exists(self, trade_date: str) -> bool:
        await self.wait_for_maintenance()
        return await self.quote_dao.check_data_exists(trade_date)

    async def get_daily_quotes(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        ts_code_list: list | None = None,
        suppress_errors: bool = True,
    ):
        return await self.quote_dao.get_daily_quotes(
            ts_code,
            start_date,
            end_date,
            ts_code_list,
            suppress_errors=suppress_errors,
        )

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df: pd.DataFrame, suppress_errors: bool = False):
        return await self.market_dao.save_daily_indicators(
            df,
            suppress_errors=suppress_errors,
        )

    async def get_daily_indicators(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        limit: int | None = None,
    ):
        return await self.market_dao.get_daily_indicators(
            ts_code,
            start_date,
            end_date,
            limit,
        )

    async def get_daily_indicators_bulk(
        self,
        ts_code_list: list,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
    ):
        """
        批量获取多只股票的 daily_indicators 数据。
        解决 N+1 查询问题。
        """
        return await self.market_dao.get_daily_indicators_bulk(
            ts_code_list,
            start_date,
            end_date,
        )

    async def get_latest_trade_date(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_latest_trade_date()

    async def get_cached_trade_dates(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_cached_trade_dates()

    async def get_cached_dates_for_table(self, table_name: str) -> set:
        """Proxy method for breakpoint resume check."""
        await self.wait_for_maintenance()
        return await self.quote_dao.get_cached_dates_for_table(table_name)

    # --- Indicators ---

    async def get_latest_indicators(self, trade_date: str | None = None):
        return await self.financial_dao.get_latest_indicators(trade_date)

    async def get_cached_indicator_dates(self):
        return await self.financial_dao.get_cached_indicator_dates()

    # --- Financial Reports ---
    async def save_financial_reports(self, df: pd.DataFrame, conn=None):
        return await self.financial_dao.save_financial_reports(df, conn=conn)

    async def get_cached_financial_records(self, period: str | None = None):
        return await self.financial_dao.get_cached_financial_records(period)

    # --- Other Data Types ---
    async def save_moneyflow(self, df: pd.DataFrame):
        return await self.quote_dao.save_moneyflow(df)

    async def save_northbound(self, df: pd.DataFrame):
        return await self.quote_dao.save_northbound(df)

    async def save_market_news(self, news_item: dict, wait: bool = False):
        return await self.market_dao.save_market_news(news_item, wait)

    async def get_market_news(
        self,
        limit: int | None = 50,
        offset: int = 0,
        min_publish_time: typing.Any = None,
    ):
        return await self.market_dao.get_market_news(limit, offset, min_publish_time)

    # --- Screening Data ---
    async def get_screening_data(self, trade_date: str | None = None):
        return await self.screener_dao.get_screening_data(trade_date)

    async def get_fundamental_screening_data(self, trade_date: str | None = None):
        return await self.screener_dao.get_fundamental_screening_data(trade_date)

    # --- Sync Stats & Misc ---
    async def update_sync_status(
        self,
        table_name: str,
        last_data_date: str,
        record_count: int,
        status: str = "success",
        last_result_status: str | None = None,
    ):
        return await self.sync_dao.update_sync_status(
            table_name,
            last_data_date,
            record_count,
            status,
            last_result_status,
        )

    async def get_sync_status(self, table_name: str | None = None) -> pd.DataFrame | dict | None:
        return await self.sync_dao.get_sync_status(table_name)

    async def check_comprehensive_health(self):
        """Check coverage and freshness of all HEALTH_CHECK_TABLES."""
        await self.wait_for_maintenance()
        results = {}

        logger.debug("[CacheManager] Health | Starting comprehensive check...")

        # === Step 1: All DAO calls outside async with (avoid connection nesting) ===
        total_stocks_result, date_range_result = await asyncio.gather(
            self.stock_dao.get_active_stock_count(),
            self.quote_dao.get_date_range(),
            return_exceptions=True,
        )
        total_stocks = (total_stocks_result if not isinstance(total_stocks_result, BaseException) else None) or 1
        if isinstance(date_range_result, BaseException):
            logger.warning(
                f"[CacheManager] Health | ⚠️ Date range query failed (non-fatal): {date_range_result}",
            )
        logger.debug(
            f"[CacheManager] Health | Active stocks baseline: {total_stocks}",
        )

        global_trade_days = 0
        global_expected_rows = None
        try:
            if not isinstance(date_range_result, BaseException):
                g_min, g_max = date_range_result
                if g_min and g_max:
                    global_trade_days, global_expected_rows = await asyncio.gather(
                        self.stock_dao.count_trade_days(g_min, g_max),
                        self.stock_dao.count_expected_rows(g_min, g_max),
                    )
                    logger.debug(
                        f"[CacheManager] Health | Baseline: trade_days={global_trade_days}, expected_rows={global_expected_rows}",
                    )
        except Exception as e:
            logger.warning(
                f"[CacheManager] Health | ⚠️ Baseline calc failed (non-fatal): {e}",
            )

        # Tables Check (Dynamic iteration based on registry)
        # Filter for quality monitored tables
        monitored_tables = {k: v for k, v in TABLE_DEFINITIONS.items() if v.get("quality_config", {}).get("monitor")}

        from data.persistence.models import Base as ModelsBase

        # === Step 2: Parallel table health checks (each with own connection) ===
        if self.engine is None:
            raise RuntimeError("Database engine not initialized")

        async def _check_single_table(table: str, meta: dict) -> tuple[str, dict]:
            table_type = meta.get("type", "stock")
            is_stock_table = table_type != "global"
            is_sparse = meta.get("quality_config", {}).get("sparse", False)

            tbl = ModelsBase.metadata.tables.get(table)
            if tbl is None:
                logger.warning("[CacheManager] Health | Unknown table: %s", table)
                return table, {
                    "covered": 0,
                    "ratio": 0,
                    "fresh_ratio": 0,
                    "depth_ratio": None,
                    "breadth_ratio": None,
                    "type": table_type,
                }

            try:
                async with self.engine.connect() as conn:
                    await conn.execution_options(isolation_level="AUTOCOMMIT")

                    if not is_stock_table:
                        r = await conn.execute(
                            sa.select(sa.func.count()).select_from(tbl),
                        )
                        cnt = r.scalar() or 0
                        ratio = 1.0 if cnt > 0 else 0.0
                        fresh_ratio = ratio
                    else:
                        cols = meta.get("columns", {})
                        keys = meta.get("sync_config", {}).get("keys", [])
                        code_col = "con_code" if "con_code" in cols or "con_code" in keys else "ts_code"

                        r = await conn.execute(
                            sa.select(
                                sa.func.count(sa.distinct(sa.column(code_col))),
                            ).select_from(tbl),
                        )
                        cnt = r.scalar() or 0
                        ratio = min(1.0, cnt / total_stocks) if total_stocks > 0 else 0

                        fresh_ratio = 0.0
                        date_col_candidates = []
                        configured_col = meta.get("sync_config", {}).get("date_col")
                        if configured_col:
                            date_col_candidates.append(configured_col)
                        date_col_candidates.extend(
                            ["trade_date", "end_date", "ann_date"],
                        )
                        try:
                            max_date = None
                            for dc in date_col_candidates:
                                try:
                                    r2 = await conn.execute(
                                        sa.select(
                                            sa.func.max(sa.column(dc)),
                                        ).select_from(tbl),
                                    )
                                    val = r2.scalar()
                                    if val:
                                        max_date = val
                                        break
                                except Exception as exc:
                                    logger.debug(
                                        "[CacheManager] Health | Date probe failed for %s.%s: %s", table, dc, exc
                                    )
                                    continue
                            if max_date:
                                from utils.time_utils import parse_date

                                max_dt = parse_date(max_date)
                                age_days = (get_now() - max_dt).days
                                if age_days <= 7:
                                    fresh_ratio = 1.0
                                elif age_days <= 30:
                                    fresh_ratio = max(0.0, 1.0 - (age_days - 7) / 23.0)

                            if ratio < 0.01:
                                fresh_ratio = 0.0
                        except (ValueError, TypeError, RuntimeError) as exc:
                            logger.debug(
                                "[CacheManager] Health | Freshness check skipped for %s: %s",
                                table,
                                exc,
                            )

                    depth_ratio = None
                    if is_stock_table and global_trade_days > 0:
                        depth_ratio = min(
                            1.0,
                            global_trade_days / get_health_depth_full_trade_days(),
                        )

                    breadth_ratio = None
                    is_daily_freq = meta.get("quality_config", {}).get("frequency") == "daily"
                    if is_daily_freq and global_expected_rows and global_expected_rows > 0:
                        try:
                            r_total = await conn.execute(
                                sa.select(sa.func.count()).select_from(tbl),
                            )
                            actual_rows = r_total.scalar() or 0
                            breadth_ratio = min(1.0, actual_rows / global_expected_rows)
                        except Exception as exc:
                            logger.debug(
                                "[CacheManager] Health | Breadth calc failed for %s: %s",
                                table,
                                exc,
                            )

                    result = {
                        "covered": cnt,
                        "ratio": ratio,
                        "fresh_ratio": fresh_ratio,
                        "depth_ratio": depth_ratio,
                        "breadth_ratio": breadth_ratio,
                        "type": table_type,
                        "sparse": is_sparse,
                    }

                    if ratio < 0.1:
                        if is_sparse:
                            logger.debug(
                                "[CacheManager] Health | Table %s (sparse): %d/%d (%.1f%%)",
                                table,
                                cnt,
                                total_stocks,
                                ratio * 100,
                            )
                        elif is_stock_table:
                            logger.warning(
                                "[CacheManager] Health | ⚠️ Table %s coverage CRITICAL: %d/%d (%.1f%%)",
                                table,
                                cnt,
                                total_stocks,
                                ratio * 100,
                            )
                        else:
                            logger.warning(
                                "[CacheManager] Health | ⚠️ Table %s (global) CRITICAL: %d records",
                                table,
                                cnt,
                            )
                    elif is_stock_table:
                        d_str = f", depth={depth_ratio:.1%}" if depth_ratio is not None else ""
                        b_str = f", breadth={breadth_ratio:.1%}" if breadth_ratio is not None else ""
                        logger.debug(
                            "[CacheManager] Health | Table %s: %d/%d (%.1f%%), fresh=%.0f%%%s%s",
                            table,
                            cnt,
                            total_stocks,
                            ratio * 100,
                            fresh_ratio * 100,
                            d_str,
                            b_str,
                        )
                    else:
                        logger.debug(
                            "[CacheManager] Health | Table %s (global): %d records",
                            table,
                            cnt,
                        )

                    return table, result
            except Exception as e:
                if "no such table" in str(e):
                    logger.warning(
                        "[CacheManager] Health | ⚠️ Table %s missing/not created yet.",
                        table,
                    )
                else:
                    logger.error(
                        "[CacheManager] Health | ❌ Failed to check table %s: %s",
                        table,
                        e,
                        exc_info=True,
                    )
                return table, {
                    "covered": 0,
                    "ratio": 0,
                    "fresh_ratio": 0,
                    "depth_ratio": None,
                    "breadth_ratio": None,
                    "type": table_type,
                }

        check_coros = [_check_single_table(t, m) for t, m in monitored_tables.items()]
        gather_results = await asyncio.gather(*check_coros, return_exceptions=True)
        for item in gather_results:
            if isinstance(item, BaseException):
                logger.warning("[CacheManager] Health | Table check failed: %s", item)
                continue
            table_name, table_result = item  # type: ignore[misc]
            results[table_name] = table_result

        return {"total_stocks": total_stocks, "tables": results, "global_trade_days": global_trade_days}

    async def get_concept_count(self):
        """Get total count of stock concept mappings."""
        return await self.stock_dao.get_concept_count()

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_forecast(df)

    async def save_fina_mainbz(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_mainbz(df)

    async def save_pledge_stat(self, df: pd.DataFrame):
        return await self.financial_dao.save_pledge_stat(df)

    async def save_repurchase(self, df: pd.DataFrame):
        return await self.financial_dao.save_repurchase(df)

    async def save_dividend(self, df: pd.DataFrame):
        return await self.financial_dao.save_dividend(df)

    async def save_index_daily(self, df: pd.DataFrame):
        return await self.quote_dao.save_index_daily(df)

    async def save_index_dailybasic(self, df: pd.DataFrame):
        return await self.quote_dao.save_index_dailybasic(df)

    async def get_index_daily(self, ts_code: str | None = None, trade_date: datetime.date | str | None = None):
        return await self.quote_dao.get_index_daily(ts_code, trade_date)

    async def get_index_daily_range(
        self,
        ts_code_list: list,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
    ):
        """
        批量获取多只指数的日线数据。
        """
        return await self.quote_dao.get_index_daily_range(
            ts_code_list,
            start_date,
            end_date,
        )

    async def save_limit_list(self, df: pd.DataFrame):
        return await self.quote_dao.save_limit_list(df)

    async def save_margin_daily(self, df: pd.DataFrame):
        return await self.quote_dao.save_margin_daily(df)

    async def save_suspend_d(self, df: pd.DataFrame):
        return await self.quote_dao.save_suspend_d(df)

    async def save_fina_audit(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_audit(df)

    async def save_top_list(self, df: pd.DataFrame):
        return await self.quote_dao.save_top_list(df)

    async def get_top_list(self, trade_date: str | None = None):
        return await self.quote_dao.get_top_list(trade_date)

    async def save_block_trade(self, df: pd.DataFrame):
        return await self.quote_dao.save_block_trade(df)

    async def get_block_trade(self, trade_date: str | None = None):
        return await self.quote_dao.get_block_trade(trade_date)

    async def get_moneyflow(self, trade_date: str | None = None, ts_code: str | None = None):
        return await self.quote_dao.get_moneyflow(trade_date, ts_code)

    async def get_northbound(self, trade_date: str | None = None, ts_code: str | None = None):
        return await self.quote_dao.get_northbound(trade_date, ts_code)

    # --- Screening History ---
    async def get_screening_history(self, strategy_name: str | None = None, limit: int | None = 100):
        return await self.screener_dao.get_screening_history(strategy_name, limit)

    async def get_history_tree(self, offset: int = 0, limit: int | None = 30):
        return await self.screener_dao.get_history_tree(offset, limit)

    async def get_history_records(
        self, trade_date: str | None, strategy_name: str | None = None, run_id: str | None = None
    ):
        return await self.screener_dao.get_history_records(trade_date, strategy_name, run_id)

    async def get_pending_reviews(self):
        return await self.screener_dao.get_pending_reviews()

    async def get_learning_examples(self, limit: int | None = 3):
        return await self.screener_dao.get_learning_examples(limit)

    # --- Sync Status Step 4 ---
    async def get_completed_step4_stocks(self, sync_version: int = 1):
        return await self.sync_dao.get_completed_step4_stocks(sync_version)

    async def mark_stock_step4_completed(self, ts_code: str | None, sync_version: int = 1, conn=None):
        return await self.sync_dao.mark_stock_step4_completed(ts_code, sync_version, conn=conn)

    async def clear_step4_sync_status(self):
        return await self.sync_dao.clear_step4_sync_status()

    # --- Trade Calendar Logic ---
    async def get_trade_cal_range(self):
        """Get the min and max calendar dates from DB"""
        return await self.stock_dao.get_trade_cal_range()

    async def save_trade_cal(self, df: pd.DataFrame):
        return await self.stock_dao.save_trade_cal(df)

    async def get_trade_cal(
        self,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        is_open: int | str | None = None,
    ):
        return await self.stock_dao.get_trade_cal(start_date, end_date, is_open)

    async def get_start_date_by_trade_days(self, end_date: datetime.date | str | None, trade_days: int):
        return await self.stock_dao.get_start_date_by_trade_days(end_date, trade_days)

    async def get_latest_northbound(self):
        return await self.quote_dao.get_latest_northbound()

    # --- Policy-Driven AI Extensions ---

    # Macro
    async def save_macro_economy(self, df: pd.DataFrame):
        return await self.macro_dao.save_macro_economy(df)

    async def save_shibor_daily(self, df: pd.DataFrame):
        return await self.macro_dao.save_shibor_daily(df)

    # Holders
    async def save_holder_number(self, df: pd.DataFrame):
        return await self.holder_dao.save_holder_number(df)

    async def save_top10_holders(self, df: pd.DataFrame):
        return await self.holder_dao.save_top10_holders(df)

    async def save_index_weights(self, df: pd.DataFrame):
        return await self.market_dao.save_index_weights(df)

    async def get_index_weights(self, index_code: str | None, trade_date: str | None):
        return await self.market_dao.get_index_weights(index_code, trade_date)

    async def get_latest_index_weight_date(self):
        return await self.market_dao.get_latest_index_weight_date()

    async def save_moneyflow_hsgt(self, df: pd.DataFrame):
        return await self.market_dao.save_moneyflow_hsgt(df)

    async def get_moneyflow_hsgt(self, trade_date: datetime.date | str | None = None, limit: int | None = None):
        return await self.market_dao.get_moneyflow_hsgt(trade_date, limit)

    # === Phase 1.5: Cache 层新增方法（AI Prompt 数据注入）===

    # --- 财务数据方法 ---
    async def get_financial_reports_history(self, ts_code: str, periods: int = 8) -> pd.DataFrame:
        """获取多期财务报告历史"""
        return await self.financial_dao.get_financial_reports_history(ts_code, periods)

    async def get_fina_audit(self, ts_code: str) -> pd.DataFrame:
        """获取审计意见"""
        return await self.financial_dao.get_fina_audit_batch([ts_code])

    async def get_fina_mainbz(self, ts_code: str) -> pd.DataFrame:
        """获取主营业务构成"""
        return await self.financial_dao.get_fina_mainbz(ts_code)

    async def get_dividend(self, ts_code: str) -> pd.DataFrame:
        """获取分红记录"""
        return await self.financial_dao.get_dividend_batch([ts_code])

    async def get_pledge_stat(self, ts_code: str) -> pd.DataFrame:
        """获取股权质押统计"""
        return await self.financial_dao.get_pledge_stat_batch([ts_code])

    # --- 股东数据方法 ---
    async def get_top10_holders(self, ts_code: str) -> pd.DataFrame:
        """获取前十大股东"""
        return await self.holder_dao.get_top10_holders(ts_code)

    async def get_stk_holdernumber(self, ts_code: str) -> pd.DataFrame:
        """获取股东人数"""
        return await self.holder_dao.get_stk_holdernumber(ts_code)

    async def get_existing_top10_ts_codes(self, period: str) -> set[str]:
        """获取指定报告期已有 top10_holders 数据的股票代码集合"""
        return await self.holder_dao.get_existing_top10_ts_codes(period)

    # --- 宏观数据方法 ---
    async def get_macro_economy(self, as_of_date=None) -> pd.DataFrame:
        """获取宏观经济数据"""
        return await self.macro_dao.get_macro_economy_latest(as_of_date=as_of_date)

    async def get_shibor_latest(self, as_of_date=None) -> pd.DataFrame:
        """获取最新 Shibor 利率"""
        return await self.macro_dao.get_shibor_latest(as_of_date=as_of_date)

    # === Phase 2: 批量质量评分方法 ===

    async def get_bulk_table_counts(
        self,
        table_name: str,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict:
        """批量获取表记录数"""
        return await self.quote_dao.get_bulk_table_counts(table_name, start_date, end_date)

    async def get_bulk_expected_stock_counts(
        self,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict:
        """批量获取理论存活股票数"""
        return await self.quote_dao.get_bulk_expected_stock_counts(start_date, end_date)

    async def get_bulk_sync_quality_scores(
        self,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
        tables: list | None = None,
    ) -> dict:
        """批量获取同步质量评分"""
        return await self.quote_dao.get_bulk_sync_quality_scores(start_date, end_date, tables)

    async def get_expected_stock_count(self, trade_date: datetime.date | str) -> int:
        """获取指定日期的理论存活股票数"""
        return await self.quote_dao.get_expected_stock_count(trade_date)

    async def get_sync_quality_score(self, trade_date: datetime.date | str) -> dict:
        """获取单个日期的同步质量评分"""
        return await self.quote_dao.get_sync_quality_score(trade_date)

    async def get_field_completeness(self, trade_date: str | datetime.date) -> dict[str, float]:
        """获取字段级基本面完整度"""
        await self.wait_for_maintenance()
        return await self.quote_dao.get_field_completeness(trade_date)

    # === L2 批量预取方法（避免 N+1 查询）===

    async def prefetch_auxiliary_data(self, ts_codes: list[str]) -> dict:
        """
        批量预取辅助数据，避免在分析循环中逐只股票查询。

        对于 30 只候选股票的批量分析，可将 180-240 次独立 DB 查询
        减少为 6-8 次批量查询。

        Args:
            ts_codes: 股票代码列表

        Returns:
            {ts_code: {"audit": df, "dividend": df, ...}} 结构
        """
        result = {code: {} for code in ts_codes}

        gather_results = await asyncio.gather(
            self.financial_dao.get_fina_audit_batch(ts_codes),
            self.financial_dao.get_dividend_batch(ts_codes),
            self.financial_dao.get_pledge_stat_batch(ts_codes),
            self.holder_dao.get_top10_holders_batch(ts_codes),
            self.financial_dao.get_fina_mainbz_batch(ts_codes),
            self.financial_dao.get_financial_reports_history_batch(ts_codes),
            self.holder_dao.get_stk_holdernumber_batch(ts_codes),
            return_exceptions=True,
        )

        batch_keys = [
            "audit",
            "dividend",
            "pledge",
            "holders",
            "mainbz",
            "financial_history",
            "holdernumber",
        ]
        batch_results = {}
        for key, raw in zip(batch_keys, gather_results, strict=False):
            if isinstance(raw, Exception):
                logger.warning("[CacheManager] prefetch_auxiliary_data: %s query failed: %s", key, raw)
                batch_results[key] = None
            else:
                batch_results[key] = raw

        for key, df in batch_results.items():
            if df is not None and not df.empty:
                if "ts_code" in df.columns:
                    grouped = df.groupby("ts_code")
                    for code in ts_codes:
                        try:
                            code_df = grouped.get_group(code)
                            result[code][key] = code_df
                        except KeyError:
                            pass
                else:
                    for code in ts_codes:
                        code_df = df[df["ts_code"] == code]
                        if not code_df.empty:
                            result[code][key] = code_df

        return result

    async def get_incomplete_financial_stocks(self, min_periods: int = 4, sync_version: int = 1) -> set:
        """
        获取财务数据不完整的股票集合。

        用于断点续传时，将这些"伪完成"或"半残"的股票剔除出完成列表，进行强制重试。

        Args:
            min_periods: 最小报告期数量（默认4个季度）
            sync_version: 同步版本号（默认1）

        Returns:
            财务数据不完整的股票代码集合
        """
        return await self.financial_dao.get_incomplete_financial_stocks(min_periods, sync_version)

    async def check_table_has_data(self, table_name: str) -> bool:
        """
        检查指定表是否有数据（安全白名单校验）。

        用于 Prompt 声明校验器验证辅助表数据是否已同步。

        Args:
            table_name: 表名（必须在安全白名单中）

        Returns:
            True 如果表中有至少一条记录
        """
        from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES

        if table_name not in _SAFE_TABLE_NAMES:
            logger.warning("[CacheManager] Invalid table name rejected: %s", table_name)
            return False

        try:
            from data.persistence.models import Base as ModelsBase

            tbl = ModelsBase.metadata.tables.get(table_name)
            if tbl is None:
                logger.warning("[CacheManager] Unknown table in check_table_has_data: %s", table_name)
                return False
            if self.engine is None:
                raise RuntimeError("Database engine not initialized")
            async with self.engine.connect() as conn:
                result = await conn.execute(sa.select(1).select_from(tbl).limit(1))
                return result.first() is not None
        except Exception as e:
            logger.warning("[CacheManager] check_table_has_data failed for %s: %s", table_name, e)
            return False
