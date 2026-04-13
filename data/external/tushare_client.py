import datetime
import logging
import threading
import typing

import pandas as pd
import requests
import tushare as ts

from utils.config_handler import ConfigHandler
from utils.rate_limiter import TokenBucket
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class TushareClient:
    """
    Enhanced Tushare API client with timeout, retry, trade calendar support, and TokenBucket Rate Limiting.
    """

    pro: typing.Any
    # Singleton instance
    _instance = None
    _lock = threading.Lock()  # Thread safety lock
    _trade_cal_cache = set()  # Cache valid trading days (Set lookup O(1))
    _loaded_years = set()  # Track which years have been loaded
    _calendar_lock = threading.Lock()  # Lock for cache updates

    _COLUMN_RENAMES = {
        "cn_cpi": {"month": "period", "nt_val": "cpi"},
        "cn_ppi": {"month": "period", "ppi_yoy": "ppi"},
        "cn_m": {"month": "period"},
    }

    def __new__(cls, token: str | None = None):
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
            cls._trade_cal_cache = set()
            cls._loaded_years = set()
            cls._initialized = False

    def __init__(self, token: str | None = None):
        # Double-check locking for initialization to prevent race conditions
        if self._initialized:
            if token and token != self.token:
                self.set_token(token)
            return

        with self._lock:
            if self._initialized:
                return

            self.token = token or ConfigHandler.get_token()
            self.timeout = ConfigHandler.get_tushare_timeout()  # Custom timeout for Tushare
            self.max_retries = ConfigHandler.get_request_max_retries()

            # Initialize Rate Limiter
            # Get limit per minute (default None)
            limit_per_min = ConfigHandler.get_tushare_api_limit()

            if limit_per_min and limit_per_min > 0:
                rate_per_sec = limit_per_min / 60.0
                # Capacity allows for small bursts (e.g. 5 seconds worth or fixed 10)
                capacity = max(10, rate_per_sec * 5)
                self._rate_limiter = TokenBucket(
                    start_tokens=capacity,
                    capacity=capacity,
                    rate=rate_per_sec,
                )
                logger.info(
                    f"[API] Rate Limiter initialized: {limit_per_min} req/min ({rate_per_sec:.2f} req/s)",
                )
            else:
                self._rate_limiter = None
                logger.info("[API] Rate Limiter disabled (No limit set)")

            if self.token:
                ts.set_token(self.token)
                # Pass timeout to requests via tushare SDK
                self.pro = ts.pro_api(timeout=self.timeout)
                logger.info(
                    f"[API] Tushare Client initialized with timeout={self.timeout}s",
                )
            else:
                self.pro = None

            self._initialized = True

    def set_token(self, token: str | None):
        self.token = token
        ts.set_token(token)
        # Re-initialize with timeout
        self.pro = ts.pro_api(timeout=self.timeout)
        logger.info(
            f"[API] Token updated. Client re-initialized with timeout={self.timeout}s",
        )

    async def _handle_api_call(self, func: typing.Callable, **kwargs: typing.Any):
        """Async wrapper that yields to event loop during rate limit / backoff"""
        import asyncio
        import functools

        # Tushare SDK wraps API methods as functools.partial(DataApi.query, 'api_name').
        # Standard __name__ doesn't exist on partial objects, so extract from partial.args[0].
        import functools as _functools

        from utils.thread_pool import ThreadPoolManager

        if isinstance(func, _functools.partial) and func.args:
            api_name = func.args[0]
        else:
            api_name = getattr(func, "__name__", str(func))

        # Core Boundary Fix: format any native date/datetime arguments to Tushare string format YYYYMMDD
        formatted_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, (datetime.date, datetime.datetime)):
                formatted_kwargs[k] = v.strftime("%Y%m%d")
            else:
                formatted_kwargs[k] = v
        kwargs = formatted_kwargs

        for i in range(self.max_retries):
            if self._rate_limiter:
                await self._rate_limiter.consume_async(1)

            try:
                if not self.pro:
                    raise Exception(
                        "Tushare Token not set. Please set your token in settings.",
                    )

                loop = asyncio.get_running_loop()
                # Run the actual synchronous HTTP call in the IO thread pool
                result = await loop.run_in_executor(
                    ThreadPoolManager().io_pool,
                    functools.partial(func, **kwargs),
                )

                # Apply unified column renaming if this API needs it
                # Note: rename is safe even on empty DataFrames (it only affects column labels, not data)
                if result is not None and api_name in self._COLUMN_RENAMES:
                    result = result.rename(columns=self._COLUMN_RENAMES[api_name])

                return result
            except Exception as e:
                import random

                error_msg = str(e).lower()
                is_rate_limit = (
                    "每分钟最多访问" in error_msg
                    or "抱歉" in error_msg
                    or "检测到" in error_msg
                    or "429" in error_msg
                    or "rate limit" in error_msg
                    or "频次超限" in error_msg
                )
                is_network_error = (
                    isinstance(e, requests.exceptions.RequestException)
                    or "timeout" in error_msg.lower()
                    or "connection" in error_msg.lower()
                    or "timed out" in error_msg.lower()
                )

                if is_rate_limit:
                    sleep_time = (2**i) * 60 + random.uniform(0, 10)
                    logger.warning(
                        f"[tushare_api] RATE_LIMITED ({api_name}): backoff={sleep_time:.2f}s (attempt {i + 1}/{self.max_retries})",
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if is_network_error:
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.warning(
                        f"[tushare_api] CONNECTION_ERROR ({api_name}): {type(e).__name__} - retry in {sleep_time:.2f}s (attempt {i + 1}/{self.max_retries})",
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if i == self.max_retries - 1:
                    logger.error(
                        f"[tushare_api] RETRY_EXHAUSTED ({api_name}): {error_msg}",
                    )
                    raise e

                await asyncio.sleep(1)
        return None

    async def _handle_api_call_paginated(self, func: typing.Callable, max_pages: int = 100, **kwargs: typing.Any):
        import pandas as pd

        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        df_list = []
        offset = 0
        page = 0
        full_page_size = None

        while page < max_pages:
            kwargs["offset"] = offset
            try:
                df = await self._handle_api_call(func, **kwargs)
            except Exception:
                if page == 0:
                    raise
                break

            if df is None or df.empty:
                break

            df_list.append(df)
            returned_len = len(df)

            if full_page_size is None:
                full_page_size = returned_len

            if returned_len < full_page_size:
                break

            offset += returned_len
            page += 1

        if page >= max_pages:
            logger.error(
                f"[API] Pagination hit max_pages={max_pages} (offset={offset}). "
                f"Results are INCOMPLETE. Consider increasing max_pages or using date range filters."
            )

        if not df_list:
            return None
        return pd.concat(df_list, ignore_index=True)

    def get_trade_dates(self, start_date: str | None, end_date: str | None):
        """Get list of actual trading dates (includes holidays handling).
        NOTE: This is a SYNC method — must remain sync for APScheduler (non-asyncio thread).
        For async contexts, use get_trade_cal() instead."""
        if not self.pro:
            raise Exception("Tushare Token not set. Please set your token in settings.")

        if isinstance(start_date, (datetime.date, datetime.datetime)):
            start_date = start_date.strftime("%Y%m%d")
        if isinstance(end_date, (datetime.date, datetime.datetime)):
            end_date = end_date.strftime("%Y%m%d")

        try:
            df = self.pro.trade_cal(
                exchange="SSE",
                start_date=start_date,
                end_date=end_date,
                is_open="1",
            )
            if df is not None and not df.empty:
                return df["cal_date"].tolist()
        except Exception as e:
            logger.warning(f"[API] get_trade_dates sync call failed: {e}")
        return []

    def is_trading_day(self, date_str: typing.Any = None):
        """
        Check if a given date is a trading day with optimized caching.
        Strategy: Year-based lazy loading with Double-Checked Locking.

        Args:
            date_str: Date in YYYYMMDD format, or a native datetime.date object. If None, uses today.

        Returns:
            bool: True if trading day, False if holiday/weekend
        """
        if date_str is None:
            date_str = get_now().strftime("%Y%m%d")
        elif isinstance(date_str, (datetime.date, datetime.datetime)):
            date_str = date_str.strftime("%Y%m%d")
        elif not isinstance(date_str, str):
            date_str = str(date_str)

        year = date_str[:4]

        # 1. Fast Path: Check if year is already loaded (No Lock)
        if year in TushareClient._loaded_years:
            return date_str in TushareClient._trade_cal_cache

        # 2. Slow Path: Load the year with Lock
        try:
            with TushareClient._calendar_lock:
                # Double-check inside lock
                if year in TushareClient._loaded_years:
                    return date_str in TushareClient._trade_cal_cache

                logger.info(f"[Cache] Loading trading calendar for year {year}...")

                # Fetch full year data
                start_date = f"{year}0101"
                end_date = f"{year}1231"

                # Sync path: use tushare SDK directly (not async _handle_api_call)
                if not self.pro:
                    raise Exception("Tushare Token not set")
                df = self.pro.trade_cal(
                    exchange="SSE",
                    start_date=start_date,
                    end_date=end_date,
                    is_open="1",
                )

                if df is not None and not df.empty:
                    # Helper to bulk update set
                    dates = set(df["cal_date"].tolist())
                    TushareClient._trade_cal_cache.update(dates)
                    TushareClient._loaded_years.add(year)
                    logger.info(
                        f"[Cache] Successfully loaded {len(dates)} trading days for {year}",
                    )

                    return date_str in dates
                logger.warning(
                    f"[Cache] Failed to load calendar for {year} (Empty response)",
                )
                # Do not mark as loaded so we retry next time, or logic below deals with it

        except Exception as e:
            logger.warning(
                f"[API] Trade calendar cache load failed: {e}, falling back to Offline Calendar",
            )

        # 3. Fallback: Offline Calendar (pandas_market_calendars)
        try:
            from data.domain_services.offline_calendar import OfflineCalendar

            return OfflineCalendar.is_trading_day(date_str)
        except Exception as ex:
            logger.error(f"[API] Offline calendar check failed: {ex}")
            # Ultimate Fallback: Simple weekday check (Mon-Fri)
            try:
                dt = datetime.datetime.strptime(date_str, "%Y%m%d")
                is_weekday = dt.weekday() < 5
                if is_weekday:
                    logger.warning(
                        f"[API] UNSAFE_FALLBACK: Assuming {date_str} is trading day (weekday check). May be inaccurate for holidays!",
                    )
                return is_weekday
            except (ValueError, TypeError):
                return True  # Default to allowing if all else fails

    # ========== Policy-Driven AI Extensions ==========

    # Whitelist of allowed macro API names to prevent arbitrary API injection
    _MACRO_API_WHITELIST = {"cn_m", "cn_cpi", "cn_ppi", "cn_gdp"}

    async def get_trade_cal(self, start_date: str | None, end_date: str | None, exchange: str = "SSE"):
        """
        Get trade calendar.
        Note: This is the raw API wrapper. For is_trading_day checks, use is_trading_day()
        which implements optimized year-based caching.  # type: ignore
        """
        return await self._handle_api_call(
            self.pro.trade_cal,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            is_open="1",
        )

    async def get_stock_basic(self, list_status: str = "L"):
        """
        Get basic list of stocks.

        Args:
            list_status: 上市状态过滤
                - "L": 仅上市中（默认，保持向后兼容）
                - "D": 仅退市
                - "": 全部（用于数据同步）

        Returns:
            DataFrame with columns: ts_code, symbol, name, area, industry,
                                list_date, delist_date, market, list_status
        """
        return await self._handle_api_call(
            self.pro.stock_basic,
            exchange="",
            list_status=list_status,
            fields="ts_code,symbol,name,area,industry,list_date,delist_date,market,list_status",
        )

    async def get_stock_basic_all(self):
        """Get all stocks (including delisted stocks) - for data sync"""
        return await self.get_stock_basic(list_status="")

    async def get_stock_list(self):
        """Alias for get_stock_basic"""
        return await self.get_stock_basic()

    async def get_daily_quotes(
        self,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get daily quotes with adj_factor joined"""
        # type: ignore
        # 1. Fetch Daily Quotes
        df_daily = await self._handle_api_call(
            self.pro.daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            trade_date=trade_date,
        )

        if df_daily is None or df_daily.empty:
            return df_daily

        # 2. Fetch Adj Factor
        # Tushare adj_factor API has same signature logic  # type: ignore
        try:
            df_adj = await self._handle_api_call(
                self.pro.adj_factor,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                trade_date=trade_date,
            )

            if df_adj is not None and not df_adj.empty:
                # Merge logic
                # Tushare returns trade_date, ts_code, adj_factor
                # Ensure keys specifically
                if "trade_date" in df_adj.columns and "ts_code" in df_adj.columns:
                    df_daily = pd.merge(
                        df_daily,
                        df_adj[["ts_code", "trade_date", "adj_factor"]],
                        on=["ts_code", "trade_date"],
                        how="left",
                    )
        except Exception as e:
            logger.warning(f"[API] Failed to fetch adj_factor: {e}, using default 1.0")

        # Fill NaN adj_factor with 1.0
        if "adj_factor" in df_daily.columns:
            df_daily["adj_factor"] = df_daily["adj_factor"].fillna(1.0)
        else:
            df_daily["adj_factor"] = 1.0

        return df_daily

    async def get_daily_basic(self, trade_date: str | None = None, ts_code: str | None = None):  # type: ignore
        """Get daily basic indicators (PE, PB, Turnover, etc.)"""
        return await self._handle_api_call(
            self.pro.daily_basic,
            ts_code=ts_code,
            trade_date=trade_date,
            fields="ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,volume_ratio",
        )

    async def get_income(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get income statement data"""  # type: ignore

        return await self._handle_api_call(
            self.pro.income,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,end_date,ann_date,report_type,n_income,revenue,operate_profit,total_revenue,n_income_attr_p",
        )

    async def get_cashflow(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get cashflow statement data"""  # type: ignore

        return await self._handle_api_call(
            self.pro.cashflow,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,end_date,n_cashflow_act,c_cashflow_return_pay,n_cashflow_inv",
        )

    async def get_balancesheet(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get balance sheet data"""  # type: ignore

        return await self._handle_api_call(
            self.pro.balancesheet,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,goodwill",
        )

    async def get_top_list(self, trade_date: str | None):  # type: ignore
        """Dragon Tiger Board (LHB) data"""

        return await self._handle_api_call(
            self.pro.top_list,
            trade_date=trade_date,
            fields="trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason",
        )

    async def get_top_inst(self, trade_date: str | None):  # type: ignore
        """LHB Institutional Seat Transaction Detail"""

        return await self._handle_api_call(self.pro.top_inst, trade_date=trade_date)

    async def get_hk_hold(self, trade_date: str | None):  # type: ignore
        """Northbound (HK->Connect) holdings"""

        return await self._handle_api_call(
            self.pro.hk_hold,
            trade_date=trade_date,
            fields="ts_code,trade_date,name,vol,ratio,exchange",
        )

    async def get_moneyflow(self, trade_date: str | None):  # type: ignore
        """Individual stock money flow (Main force)"""

        return await self._handle_api_call(
            self.pro.moneyflow,
            trade_date=trade_date,
            fields="ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount",
        )

    async def get_block_trade(self, trade_date: str | None):  # type: ignore
        """Block trade data"""

        return await self._handle_api_call(
            self.pro.block_trade,
            trade_date=trade_date,
            fields="ts_code,trade_date,price,vol,amount,buyer,seller",
        )

    async def get_fina_indicator(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """
        Get financial indicators (ROE, growth rates, etc.)
        Can query by:
        1. ts_code + start_date/end_date (Get history for one stock)
        2. period (Get all stocks for one quarter - Requires permissions)
        """  # type: ignore

        return await self._handle_api_call(
            self.pro.fina_indicator,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,roe,roe_waa,roe_dt,netprofit_margin,grossprofit_margin,debt_to_assets,q_sales_yoy,q_profit_yoy,or_yoy,netprofit_yoy",
        )

    async def get_disclosure_date(self, date: str):
        """
        Get disclosure list for a specific date (Incremental Sync).
        Uses 'actual_date' to find reports released on this day.
        """  # type: ignore

        return await self._handle_api_call(
            self.pro.disclosure_date,
            actual_date=date,
            fields="ts_code,ann_date,end_date,actual_date",
        )

    # type: ignore
    async def get_concept_list(self, src: str = "ts"):
        """Get all concept categories"""
        return await self._handle_api_call(self.pro.concept, src=src)

    async def get_concept_detail_by_id(self, concept_id: str):
        """
        Get all stocks in a specific concept group by concept ID.  # type: ignore
        Unlike get_concept_detail(ts_code), this fetches members of a concept.
        """
        return await self._handle_api_call(
            self.pro.concept_detail,
            id=concept_id,
            fields="id,concept_name,ts_code",
        )

    async def get_concept_detail(self, ts_code: str | None):
        """
        Get concepts for a specific stock (e.g. Lithium, Sora, etc.)
        """  # type: ignore

        return await self._handle_api_call(
            self.pro.concept_detail,
            ts_code=ts_code,
            fields="id,concept_name",
        )

    async def get_index_daily(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get index daily data"""
        # type: ignore
        # Index Daily
        return await self._handle_api_call(
            self.pro.index_daily,
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount",
        )

    async def get_moneyflow_hsgt(self, trade_date: str | None = None):
        """Get Northbound (HSGT) money flow"""  # type: ignore

        return await self._handle_api_call(
            self.pro.moneyflow_hsgt,
            trade_date=trade_date,
            fields="trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money",
        )

    async def get_index_dailybasic(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get index daily indicators (PE, PB, etc.)"""  # type: ignore

        return await self._handle_api_call(
            self.pro.index_dailybasic,
            trade_date=trade_date,
            ts_code=ts_code,
            fields="ts_code,trade_date,total_mv,float_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,pe,pe_ttm,pb",
        )

    async def get_limit_list(self, trade_date: str | None = None):
        """Get daily limit up/down list

        Tushare API returns:
        - trade_date: 交易日期
        - ts_code: 股票代码
        - name: 股票名称
        - close: 收盘价
        - pct_chg: 涨跌幅
        - amp: 振幅
        - fc_ratio: 封单金额/日成交金额
        - fl_ratio: 封单手数/流通股本
        - fd_amount: 封单金额
        - first_time: 首次涨停时间
        - last_time: 最后封板时间
        - open_times: 打开次数
        - strth: 涨跌停强度
        - limit: D跌停U涨停
        """  # type: ignore

        return await self._handle_api_call(
            self.pro.limit_list,
            trade_date=trade_date,
            fields="trade_date,ts_code,name,close,pct_chg,amp,fc_ratio,fl_ratio,fd_amount,first_time,last_time,open_times,strth,limit",
        )

    async def get_suspend_d(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get daily suspension list"""  # type: ignore

        return await self._handle_api_call(
            self.pro.suspend_d,
            trade_date=trade_date,
            ts_code=ts_code,
            suspend_type="S",
            fields="ts_code,trade_date,suspend_timing,suspend_type",
        )

    async def get_margin_detail(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get individual stock margin detail"""

        return await self._handle_api_call(
            self.pro.margin_detail,
            trade_date=trade_date,
            ts_code=ts_code,
            fields="ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzrqye",
        )

    async def get_fina_audit(
        self,
        ts_code: str | None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get financial audit opinion"""  # type: ignore

        return await self._handle_api_call(
            self.pro.fina_audit,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,end_date,ann_date,audit_result,audit_agency,audit_sign,audit_fees",
        )

    async def get_forecast(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get performance forecast"""  # type: ignore

        return await self._handle_api_call(
            self.pro.forecast,
            ts_code=ts_code,
            period=period,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max",
        )

    async def get_fina_mainbz(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get main business composition"""  # type: ignore

        return await self._handle_api_call(
            self.pro.fina_mainbz,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            type="P",
            fields="ts_code,end_date,bz_item,bz_sales,bz_profit,bz_cost,curr_type,update_flag",
        )

    async def get_pledge_stat(self, ts_code: str | None = None, end_date: str | None = None):
        """Get share pledge statistics"""  # type: ignore

        return await self._handle_api_call_paginated(
            self.pro.pledge_stat,
            ts_code=ts_code,
            end_date=end_date,
            fields="ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio",
        )

    async def get_repurchase(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get share repurchase"""  # type: ignore

        return await self._handle_api_call(
            self.pro.repurchase,
            ts_code=ts_code,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit",
        )

    async def get_dividend(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get dividend history"""

        return await self._handle_api_call(
            self.pro.dividend,
            ts_code=ts_code,
            ann_date=ann_date,
            end_date=end_date,
            fields="ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date",
        )

    async def get_shibor(self, start_date: str | None = None, end_date: str | None = None):  # type: ignore
        """Get Shibor rates"""
        return await self._handle_api_call(
            self.pro.shibor,
            start_date=start_date,
            end_date=end_date,
            fields="date,on,1w,2w,1m,3m,6m,9m,1y",
        )

    async def get_top10_holders(
        self,
        ts_code: str | None = None,
        end_date: str | None = None,
        start_date: str | None = None,
        ann_date: str | None = None,
    ):  # type: ignore
        """Get Top 10 Holders"""
        return await self._handle_api_call_paginated(
            self.pro.top10_holders,
            ts_code=ts_code,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio,hold_float_ratio,hold_change,holder_type",
        )

    async def get_index_weight(
        self,
        index_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):  # type: ignore
        """Get Index Component Weights"""
        return await self._handle_api_call(
            self.pro.index_weight,
            index_code=index_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            fields="index_code,con_code,trade_date,weight",
        )

    async def get_stk_holdernumber(
        self,
        ts_code: str | None = None,
        end_date: str | None = None,
        start_date: str | None = None,
        ann_date: str | None = None,
    ):  # type: ignore
        """Get Stock Holder Number (Chip Concentration)"""
        return await self._handle_api_call_paginated(
            self.pro.stk_holdernumber,
            ts_code=ts_code,
            ann_date=ann_date,
            end_date=end_date,
            start_date=start_date,
            fields="ts_code,end_date,ann_date,holder_num,holder_num_change,holder_num_ratio",
        )

    async def get_macro_data(self, api_name: str, start_m: str | None = None, end_m: str | None = None):
        if api_name not in self._MACRO_API_WHITELIST:
            logger.error(f"[API] Rejected macro API: {api_name} (not in whitelist)")
            return None
        func = getattr(self.pro, api_name, None)
        if not func:
            logger.error(f"[API] Macro API not found: {api_name}")
            return None
        return await self._handle_api_call(func, start_m=start_m, end_m=end_m)
