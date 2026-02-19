import datetime
import logging
import random
import threading
import time

import pandas as pd
import requests
import tushare as ts

from utils.config_handler import ConfigHandler
from utils.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)



class TushareClient:
    """
    Enhanced Tushare API client with timeout, retry, trade calendar support, and TokenBucket Rate Limiting.
    """

    # Singleton instance
    _instance = None
    _lock = threading.Lock()  # Thread safety lock
    _trade_cal_cache = set()  # Cache valid trading days (Set lookup O(1))
    _loaded_years = set()  # Track which years have been loaded
    _calendar_lock = threading.Lock()  # Lock for cache updates

    def __new__(cls, token=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, token=None):
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
                self._rate_limiter = TokenBucket(start_tokens=capacity, capacity=capacity, rate=rate_per_sec)
                logger.info(f"[API] Rate Limiter initialized: {limit_per_min} req/min ({rate_per_sec:.2f} req/s)")
            else:
                self._rate_limiter = None
                logger.info("[API] Rate Limiter disabled (No limit set)")

            if self.token:
                ts.set_token(self.token)
                # Pass timeout to requests via tushare SDK
                self.pro = ts.pro_api(timeout=self.timeout)
                logger.info(f"[API] Tushare Client initialized with timeout={self.timeout}s")
            else:
                self.pro = None

            self._initialized = True

    def set_token(self, token):
        self.token = token
        ts.set_token(token)
        # Re-initialize with timeout
        self.pro = ts.pro_api(timeout=self.timeout)
        logger.info(f"[API] Token updated. Client re-initialized with timeout={self.timeout}s")

    def _handle_api_call(self, func, **kwargs):
        """Helper to handle rate limits, retries, and errors with jittered backoff"""
        api_name = getattr(func, '__name__', str(func))
        
        for i in range(self.max_retries):
            if self._rate_limiter:
                self._rate_limiter.consume(1)

            try:
                if not self.pro:
                    raise Exception("Tushare Token not set. Please set your token in settings.")
                result = func(**kwargs)
                return result
            except (requests.exceptions.RequestException, Exception) as e:
                error_msg = str(e)
                is_rate_limit = "每分钟最多访问" in error_msg or "抱歉" in error_msg or "检测到" in error_msg
                # Enhanced network error detection
                is_network_error = isinstance(e, requests.exceptions.RequestException) or \
                                   "timeout" in error_msg.lower() or \
                                   "connection" in error_msg.lower() or \
                                   "timed out" in error_msg.lower()

                if is_rate_limit:
                    sleep_time = (2 ** i) + random.uniform(0, 1)
                    # Log rate limit backoff
                    logger.debug(
                        f"[tushare_api] RATE_LIMITED ({api_name}): backoff={sleep_time:.2f}s (attempt {i + 1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue

                if is_network_error:
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.warning(
                        f"[tushare_api] CONNECTION_ERROR ({api_name}): {type(e).__name__} - retry in {sleep_time:.2f}s (attempt {i + 1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue

                # Other errors
                if i == self.max_retries - 1:
                    logger.error(f"[tushare_api] RETRY_EXHAUSTED ({api_name}): {error_msg}")
                    raise e

                time.sleep(1)
        return None

    # ========== Trade Calendar ==========

    def get_trade_cal(self, start_date, end_date, exchange='SSE'):
        """
        Get trade calendar. 
        Note: This is the raw API wrapper. For is_trading_day checks, use is_trading_day() 
        which implements optimized year-based caching.
        """
        return self._handle_api_call(
            self.pro.trade_cal,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            is_open='1'
        )

    def get_trade_dates(self, start_date, end_date):
        """Get list of actual trading dates (includes holidays handling)"""
        # For date ranges, we can still use the API directly or potentially optimize to use cache too.
        # Given this is likely used for batch data fetching, direct API is acceptable, 
        # or we could iterate if range is small, but API is safer for ranges.
        df = self.get_trade_cal(start_date, end_date)
        if df is not None and not df.empty:
            return df['cal_date'].tolist()
        return []

    def is_trading_day(self, date_str=None):
        """
        Check if a given date is a trading day with optimized caching.
        Strategy: Year-based lazy loading with Double-Checked Locking.
        
        Args:
            date_str: Date in YYYYMMDD format. If None, uses today.
            
        Returns:
            bool: True if trading day, False if holiday/weekend
        """
        if date_str is None:
            date_str = datetime.datetime.now().strftime('%Y%m%d')

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

                df = self.get_trade_cal(start_date, end_date)

                if df is not None and not df.empty:
                    # Helper to bulk update set
                    dates = set(df['cal_date'].tolist())
                    TushareClient._trade_cal_cache.update(dates)
                    TushareClient._loaded_years.add(year)
                    logger.info(f"[Cache] Successfully loaded {len(dates)} trading days for {year}")

                    return date_str in dates
                else:
                    logger.warning(f"[Cache] Failed to load calendar for {year} (Empty response)")
                    # Do not mark as loaded so we retry next time, or logic below deals with it

        except Exception as e:
            logger.warning(f"[API] Trade calendar cache load failed: {e}, falling back to Offline Calendar")

        # 3. Fallback: Offline Calendar (pandas_market_calendars)
        try:
            from data.offline_calendar import OfflineCalendar
            return OfflineCalendar.is_trading_day(date_str)
        except Exception as ex:
            logger.error(f"[API] Offline calendar check failed: {ex}")
            # Ultimate Fallback: Simple weekday check (Mon-Fri)
            try:
                dt = datetime.datetime.strptime(date_str, '%Y%m%d')
                is_weekday = dt.weekday() < 5
                if is_weekday:
                    logger.warning(
                        f"[API] UNSAFE_FALLBACK: Assuming {date_str} is trading day (weekday check). May be inaccurate for holidays!")
                return is_weekday
            except (ValueError, TypeError):
                return True  # Default to allowing if all else fails

    # ========== Stock Basic ==========

    def get_stock_basic(self):
        """Get basic list of all stocks"""
        return self._handle_api_call(
            self.pro.stock_basic,
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name,area,industry,list_date,market,list_status'
        )

    def get_stock_list(self):
        """Alias for get_stock_basic"""
        return self.get_stock_basic()

    # ========== Daily Data ==========

    def get_daily_quotes(self, trade_date=None, start_date=None, end_date=None, ts_code=None):
        """Get daily quotes with adj_factor joined"""

        # 1. Fetch Daily Quotes
        df_daily = self._handle_api_call(
            self.pro.daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            trade_date=trade_date
        )

        if df_daily is None or df_daily.empty:
            return df_daily

        # 2. Fetch Adj Factor
        # Tushare adj_factor API has same signature logic
        try:
            df_adj = self._handle_api_call(
                self.pro.adj_factor,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                trade_date=trade_date
            )

            if df_adj is not None and not df_adj.empty:
                # Merge logic
                # Tushare returns trade_date, ts_code, adj_factor
                # Ensure keys specifically
                if 'trade_date' in df_adj.columns and 'ts_code' in df_adj.columns:
                    df_daily = pd.merge(
                        df_daily,
                        df_adj[['ts_code', 'trade_date', 'adj_factor']],
                        on=['ts_code', 'trade_date'],
                        how='left'
                    )
        except Exception as e:
            logger.warning(f"[API] Failed to fetch adj_factor: {e}, using default 1.0")

        # Fill NaN adj_factor with 1.0
        if 'adj_factor' in df_daily.columns:
            df_daily['adj_factor'] = df_daily['adj_factor'].fillna(1.0)
        else:
            df_daily['adj_factor'] = 1.0

        return df_daily

    def get_daily_basic(self, trade_date=None, ts_code=None):
        """Get daily basic indicators (PE, PB, Turnover, etc.)"""
        return self._handle_api_call(
            self.pro.daily_basic,
            ts_code=ts_code,
            trade_date=trade_date,
            fields='ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,turnover_rate,volume_ratio'
        )

    # ========== Financial Data ==========

    def get_income(self, period=None, start_date=None, end_date=None, ts_code=None):
        """Get income statement data"""

        return self._handle_api_call(
            self.pro.income,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields='ts_code,end_date,ann_date,report_type,n_income,revenue,operate_profit,total_revenue,n_income_attr_p'
        )

    def get_cashflow(self, period=None, start_date=None, end_date=None, ts_code=None):
        """Get cashflow statement data"""

        return self._handle_api_call(
            self.pro.cashflow,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields='ts_code,end_date,n_cashflow_act,c_cashflow_return_pay,n_cashflow_inv'
        )

    def get_balancesheet(self, period=None, start_date=None, end_date=None, ts_code=None):
        """Get balance sheet data"""

        return self._handle_api_call(
            self.pro.balancesheet,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields='ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,goodwill'
        )

    # get_fina_indicator removed (duplicate)

    # ========== Fund Flow & Institutional Data ==========

    def get_top_list(self, trade_date):
        """Dragon Tiger Board (LHB) data"""

        return self._handle_api_call(
            self.pro.top_list,
            trade_date=trade_date
        )

    def get_top_inst(self, trade_date):
        """LHB Institutional Seat Transaction Detail"""

        return self._handle_api_call(
            self.pro.top_inst,
            trade_date=trade_date
        )

    def get_hk_hold(self, trade_date):
        """Northbound (HK->Connect) holdings"""

        return self._handle_api_call(
            self.pro.hk_hold,
            trade_date=trade_date
        )

    def get_moneyflow(self, trade_date):
        """Individual stock money flow (Main force)"""

        return self._handle_api_call(
            self.pro.moneyflow,
            trade_date=trade_date
        )

    def get_block_trade(self, trade_date):
        """Block trade data"""

        return self._handle_api_call(
            self.pro.block_trade,
            trade_date=trade_date
        )


    def get_fina_indicator(self, ts_code=None, period=None, start_date=None, end_date=None):
        """
        Get financial indicators (ROE, growth rates, etc.)
        Can query by:
        1. ts_code + start_date/end_date (Get history for one stock)
        2. period (Get all stocks for one quarter - Requires permissions)
        """

        return self._handle_api_call(
            self.pro.fina_indicator,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,ann_date,end_date,roe,roe_waa,roe_dt,netprofit_margin,grossprofit_margin,debt_to_assets,q_sales_yoy,q_profit_yoy,or_yoy,netprofit_yoy'
        )

    def get_disclosure_date(self, date):
        """
        Get disclosure list for a specific date (Incremental Sync).
        Uses 'actual_date' to find reports released on this day.
        """

        return self._handle_api_call(
            self.pro.disclosure_date,
            actual_date=date,
            fields='ts_code,ann_date,end_date,actual_date'
        )

    def get_concept_list(self, src='ts'):
        """Get all concept categories"""
        return self._handle_api_call(
            self.pro.concept,
            src=src
        )

    def get_concept_detail_by_id(self, concept_id):
        """
        Get all stocks in a specific concept group by concept ID.
        Unlike get_concept_detail(ts_code), this fetches members of a concept.
        """
        return self._handle_api_call(
            self.pro.concept_detail,
            id=concept_id
        )

    def get_concept_detail(self, ts_code):
        """
        Get concepts for a specific stock (e.g. Lithium, Sora, etc.)
        """

        return self._handle_api_call(
            self.pro.concept_detail,
            ts_code=ts_code,
            fields='id,concept_name'
        )

    # ========== Market Overview APIs ==========
    def get_index_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        """Get index daily data"""

        # Index Daily
        return self._handle_api_call(
            self.pro.index_daily,
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date
        )

    def get_moneyflow_hsgt(self, trade_date=None):
        """Get Northbound (HSGT) money flow"""

        return self._handle_api_call(
            self.pro.moneyflow_hsgt,
            trade_date=trade_date
        )

    def get_index_dailybasic(self, trade_date=None, ts_code=None):
        """Get index daily indicators (PE, PB, etc.)"""

        return self._handle_api_call(
            self.pro.index_dailybasic,
            trade_date=trade_date,
            ts_code=ts_code,
            fields='ts_code,trade_date,total_mv,float_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,pe,pe_ttm,pb'
        )

    def get_limit_list(self, trade_date=None):
        """Get daily limit up/down list"""

        return self._handle_api_call(
            self.pro.limit_list,
            trade_date=trade_date,
            # fields='trade_date,ts_code,name,close,pct_chg,amp,fc_ratio,fl_ratio,fd_amount,first_time,last_time,open_times,strth,limit_type'
        )

    def get_suspend_d(self, trade_date=None, ts_code=None):
        """Get daily suspension list"""

        return self._handle_api_call(
            self.pro.suspend_d,
            trade_date=trade_date,
            ts_code=ts_code,
            suspend_type='S'  # Only stop
        )

    def get_margin_detail(self, trade_date=None, ts_code=None):
        """Get individual stock margin detail"""

        # Note: API might be 'margin_detail' or 'margin' depending on permissions
        # Usually 'margin_detail' is for individual stocks
        return self._handle_api_call(
            self.pro.margin_detail,
            trade_date=trade_date,
            ts_code=ts_code
        )

    # ========== Extended Fundamentals (Step 4) ==========

    def get_fina_audit(self, ts_code, start_date=None, end_date=None):
        """Get financial audit opinion"""

        return self._handle_api_call(
            self.pro.fina_audit,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,end_date,ann_date,audit_result,audit_agency,audit_sign'
        )

    def get_forecast(self, ts_code=None, period=None, start_date=None, end_date=None, ann_date=None):
        """Get performance forecast"""

        return self._handle_api_call(
            self.pro.forecast,
            ts_code=ts_code,
            period=period,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max'
        )

    def get_fina_mainbz(self, ts_code=None, period=None, start_date=None, end_date=None):
        """Get main business composition"""

        return self._handle_api_call(
            self.pro.fina_mainbz,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            type='P'  # By Product
        )

    def get_pledge_stat(self, ts_code=None, end_date=None):
        """Get share pledge statistics"""

        return self._handle_api_call(
            self.pro.pledge_stat,
            ts_code=ts_code,
            end_date=end_date
        )

    def get_repurchase(self, ts_code=None, start_date=None, end_date=None, ann_date=None):
        """Get share repurchase"""

        return self._handle_api_call(
            self.pro.repurchase,
            ts_code=ts_code,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date
        )

    def get_dividend(self, ts_code=None, start_date=None, end_date=None, ann_date=None):
        """Get dividend history"""

        # Tushare dividend API standard fields
        # Note: ann_date is a valid param in pro.dividend
        return self._handle_api_call(
            self.pro.dividend,
            ts_code=ts_code,
            ann_date=ann_date,
            end_date=end_date
            # Tushare dividend has diverse params, ann_date is key for batch
        )

    # ========== Policy-Driven AI Extensions ==========

    # Whitelist of allowed macro API names to prevent arbitrary API injection
    _MACRO_API_WHITELIST = {'cn_m', 'cn_cpi', 'cn_ppi', 'cn_gdp'}

    def get_macro_data(self, api_name, start_m=None, end_m=None):
        """
        Getter for macro data (cn_m, cn_cpi, cn_ppi).
        api_name must be in _MACRO_API_WHITELIST.
        """
        if api_name not in self._MACRO_API_WHITELIST:
            logger.error(f"[API] Rejected macro API: {api_name} (not in whitelist)")
            return None

        func = getattr(self.pro, api_name, None)
        if not func:
            logger.error(f"[API] Macro API not found: {api_name}")
            return None
            
        return self._handle_api_call(func, start_m=start_m, end_m=end_m)

    def get_shibor(self, start_date=None, end_date=None):
        """Get Shibor rates"""
        return self._handle_api_call(
            self.pro.shibor,
            start_date=start_date,
            end_date=end_date
        )

    def get_top10_holders(self, ts_code=None, end_date=None):
        """Get Top 10 Holders"""
        return self._handle_api_call(
            self.pro.top10_holders,
            ts_code=ts_code,
            end_date=end_date
        )

    def get_index_weight(self, index_code=None, trade_date=None, start_date=None, end_date=None):
        """Get Index Component Weights"""
        return self._handle_api_call(
            self.pro.index_weight,
            index_code=index_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date
        )

    def get_stk_holdernumber(self, ts_code=None, end_date=None, start_date=None):
        """Get Stock Holder Number (Chip Concentration)"""
        return self._handle_api_call(
            self.pro.stk_holdernumber,
            ts_code=ts_code,
            end_date=end_date,
            start_date=start_date
        )

