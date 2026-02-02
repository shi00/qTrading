import tushare as ts
import pandas as pd
import time
import datetime
import config
from utils.config_handler import ConfigHandler
from utils.rate_limiter import TokenBucket
import logging
import random

import threading

logger = logging.getLogger(__name__)

class TushareClient:
    """
    Enhanced Tushare API client with timeout, retry, trade calendar support, and TokenBucket Rate Limiting.
    """
    
    # Singleton instance
    _instance = None
    _lock = threading.Lock() # Thread safety lock
    _trade_cal_cache = None  # Cache trade calendar
    
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
            self.timeout = ConfigHandler.get_tushare_timeout() # Custom timeout for Tushare
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
        for i in range(self.max_retries):
            # Consume token BEFORE request (Proactive Rate Limiting)
            if self._rate_limiter:
                self._rate_limiter.consume(1)
                
            try:
                if not self.pro:
                    raise Exception("Tushare Token not set. Please set your token in settings.")
                result = func(**kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                # Parse Tushare error codes/messages
                is_rate_limit = "每分钟最多访问" in error_msg or "抱歉" in error_msg or "检测到您" in error_msg
                is_network_error = "timeout" in error_msg.lower() or "connection" in error_msg.lower() or "timed out" in error_msg.lower()
                
                if is_rate_limit:
                    # Rate limit: Exponential backoff with strong jitter
                    # e.g. 1s->(1-2s), 2s->(2-4s), 3s->(4-8s)
                    sleep_time = (2 ** i) + random.uniform(0, 1)
                    logger.warning(f"[API] [WARN] Rate limit hit, backing off {sleep_time:.2f}s... (attempt {i+1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue
                    
                if is_network_error:
                    # Network error: Linear backoff with jitter
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.warning(f"[API] [WARN] Connection error, retrying in {sleep_time:.2f}s... (attempt {i+1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue
                
                # Other errors
                if i == self.max_retries - 1:
                    logger.error(f"[API] [FAIL] Failed after {self.max_retries} attempts: {error_msg}")
                    raise e
                
                # Unknown transient error, short sleep
                time.sleep(1)
        return None

    # ========== Trade Calendar ==========
    
    def get_trade_cal(self, start_date, end_date, exchange='SSE'):
        """Get trade calendar (cached)"""
        cache_key = f"{exchange}_{start_date}_{end_date}"
        if TushareClient._trade_cal_cache is not None:
            cached = TushareClient._trade_cal_cache.get(cache_key)
            if cached is not None:
                return cached
        
        logger.debug(f"[API] fetching trade_cal {start_date} to {end_date}...")
        df = self._handle_api_call(
            self.pro.trade_cal,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            is_open='1'
        )
        
        if df is not None:
            if TushareClient._trade_cal_cache is None:
                TushareClient._trade_cal_cache = {}
            TushareClient._trade_cal_cache[cache_key] = df
        
        return df

    def get_trade_dates(self, start_date, end_date):
        """Get list of actual trading dates (includes holidays handling)"""
        df = self.get_trade_cal(start_date, end_date)
        if df is not None and not df.empty:
            return df['cal_date'].tolist()
        return []

    def is_trading_day(self, date_str=None):
        """
        Check if a given date is a trading day (handles Chinese holidays).
        
        Args:
            date_str: Date in YYYYMMDD format. If None, uses today.
            
        Returns:
            bool: True if trading day, False if holiday/weekend
            
        Corner cases:
        - API failure: Falls back to weekday check only
        - Cache miss: Fetches from API
        """
        if date_str is None:
            date_str = datetime.datetime.now().strftime('%Y%m%d')
        
        try:
            # Fetch calendar for just this date
            df = self._handle_api_call(
                self.pro.trade_cal,
                exchange='SSE',
                start_date=date_str,
                end_date=date_str
            )
            
            if df is not None and not df.empty:
                is_open = df.iloc[0].get('is_open', 0)
                return str(is_open) == '1'
            
        except Exception as e:
            logger.warning(f"[API] Trade calendar check failed: {e}, falling back to Offline Calendar")
        
        # Fallback: Use Offline Calendar (pandas_market_calendars)
        try:
            from data.offline_calendar import OfflineCalendar
            return OfflineCalendar.is_trading_day(date_str)
        except Exception as ex:
            logger.error(f"[API] Offline calendar check failed: {ex}")
            # Ultimate Fallback: Simple weekday check (Mon-Fri)
            try:
                dt = datetime.datetime.strptime(date_str, '%Y%m%d')
                return dt.weekday() < 5
            except:
                return True  # Default to allowing if all else fails

    # ========== Stock Basic ==========
    
    def get_stock_basic(self):
        """Get basic list of all stocks"""
        logger.debug(f"[API] fetching stock_basic...")
        return self._handle_api_call(
            self.pro.stock_basic, 
            exchange='', 
            list_status='L', 
            fields='ts_code,symbol,name,area,industry,list_date,market'
        )

    def get_stock_list(self):
        """Alias for get_stock_basic"""
        return self.get_stock_basic()

    # ========== Daily Data ==========
    
    def get_daily_quotes(self, trade_date=None, start_date=None, end_date=None, ts_code=None):
        """Get daily quotes with adj_factor joined"""
        logger.debug(f"[API] fetching daily quotes + adj_factor for date={trade_date} code={ts_code}...")
        
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
        logger.debug(f"[API] fetching daily_basic for date={trade_date}...")
        return self._handle_api_call(
            self.pro.daily_basic,
            ts_code=ts_code,
            trade_date=trade_date,
            fields='ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,turnover_rate,volume_ratio'
        )

    # ========== Financial Data ==========
    
    def get_income(self, period=None, start_date=None, end_date=None, ts_code=None):
        """Get income statement data"""
        logger.debug(f"[API] fetching income statement for period={period} range={start_date}-{end_date}...")
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
        logger.debug(f"[API] fetching cashflow for period={period} range={start_date}-{end_date}...")
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
        logger.debug(f"[API] fetching balancesheet for period={period} range={start_date}-{end_date}...")
        return self._handle_api_call(
            self.pro.balancesheet,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields='ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,goodwill'
        )

    def get_fina_indicator(self, period=None, start_date=None, end_date=None, ts_code=None):
        """Get financial indicators"""
        logger.debug(f"[API] fetching fina_indicator for code={ts_code} period={period} range={start_date}-{end_date}...")
        return self._handle_api_call(
            self.pro.fina_indicator,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
             fields='ts_code,ann_date,end_date,roe,roe_dt,grossprofit_margin,netprofit_margin,debt_to_assets,or_yoy,netprofit_yoy'
        )

    # ========== Fund Flow & Institutional Data ==========

    def get_top_list(self, trade_date):
        """Dragon Tiger Board (LHB) data"""
        logger.debug(f"[API] fetching top_list for {trade_date}...")
        return self._handle_api_call(
            self.pro.top_list,
            trade_date=trade_date
        )
        
    def get_top_inst(self, trade_date):
        """LHB Institutional Seat Transaction Detail"""
        logger.debug(f"[API] fetching top_inst for {trade_date}...")
        return self._handle_api_call(
            self.pro.top_inst,
            trade_date=trade_date
        )

    def get_hk_hold(self, trade_date):
        """Northbound (HK->Connect) holdings"""
        logger.debug(f"[API] fetching hk_hold for {trade_date}...")
        return self._handle_api_call(
            self.pro.hk_hold,
            trade_date=trade_date
        )

    def get_moneyflow(self, trade_date):
        """Individual stock money flow (Main force)"""
        logger.debug(f"[API] fetching moneyflow for {trade_date}...")
        return self._handle_api_call(
            self.pro.moneyflow,
            trade_date=trade_date
        )

    def get_block_trade(self, trade_date):
        """Block trade data"""
        logger.debug(f"[API] fetching block_trade for {trade_date}...")
        return self._handle_api_call(
            self.pro.block_trade,
            trade_date=trade_date
        )

    def get_stk_holdernumber(self, ts_code=None, end_date=None):
        """Shareholder number"""
        logger.debug(f"[API] fetching stk_holdernumber...")
        return self._handle_api_call(
            self.pro.stk_holdernumber,
            ts_code=ts_code,
            end_date=end_date
        )

    def get_fina_indicator(self, ts_code=None, period=None, start_date=None, end_date=None):
        """
        Get financial indicators (ROE, growth rates, etc.)
        Can query by:
        1. ts_code + start_date/end_date (Get history for one stock)
        2. period (Get all stocks for one quarter - Requires permissions)
        """
        logger.debug(f"[API] fetching fina_indicator for code={ts_code} period={period}...")
        return self._handle_api_call(
            self.pro.fina_indicator,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,end_date,roe,roe_waa,roe_dt,netprofit_margin,grossprofit_margin,debt_to_assets,q_sales_yoy,q_profit_yoy,or_yoy,netprofit_yoy'
        )

    def get_disclosure_date(self, date):
        """
        Get disclosure list for a specific date (Incremental Sync).
        Uses 'actual_date' to find reports released on this day.
        """
        logger.debug(f"[API] fetching disclosure_date for actual_date={date}...")
        return self._handle_api_call(
            self.pro.disclosure_date,
            actual_date=date, 
            fields='ts_code,ann_date,end_date,actual_date'
        )

    def get_concept_detail(self, ts_code):
        """
        Get concepts for a specific stock (e.g. Lithium, Sora, etc.)
        """
        logger.debug(f"[API] fetching concept_detail for {ts_code}...")
        return self._handle_api_call(
            self.pro.concept_detail,
            ts_code=ts_code,
            fields='id,concept_name'
        )

    # ========== Market Overview APIs ==========
    def get_index_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        """Get index daily data"""
        logger.debug(f"[API] fetching index_daily date={trade_date} code={ts_code}...")
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
        logger.debug(f"[API] fetching moneyflow_hsgt date={trade_date}...")
        return self._handle_api_call(
            self.pro.moneyflow_hsgt,
            trade_date=trade_date
        )

    def get_index_dailybasic(self, trade_date=None, ts_code=None):
        """Get index daily indicators (PE, PB, etc.)"""
        logger.debug(f"[API] fetching index_dailybasic date={trade_date}...")
        return self._handle_api_call(
            self.pro.index_dailybasic,
            trade_date=trade_date,
            ts_code=ts_code,
            fields='ts_code,trade_date,total_mv,float_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,pe,pe_ttm,pb'
        )

    def get_limit_list(self, trade_date=None):
        """Get daily limit up/down list"""
        logger.debug(f"[API] fetching limit_list date={trade_date}...")
        return self._handle_api_call(
            self.pro.limit_list,
            trade_date=trade_date,
            # fields='trade_date,ts_code,name,close,pct_chg,amp,fc_ratio,fl_ratio,fd_amount,first_time,last_time,open_times,strth,limit_type'
        )

    def get_suspend_d(self, trade_date=None, ts_code=None):
        """Get daily suspension list"""
        logger.debug(f"[API] fetching suspend_d date={trade_date}...")
        return self._handle_api_call(
            self.pro.suspend_d,
            trade_date=trade_date,
            ts_code=ts_code,
            suspend_type='S' # Only stop
        )

    def get_margin_detail(self, trade_date=None, ts_code=None):
        """Get individual stock margin detail"""
        logger.debug(f"[API] fetching margin_detail date={trade_date} code={ts_code}...")
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
        logger.debug(f"[API] fetching audit for {ts_code}...")
        return self._handle_api_call(
            self.pro.fina_audit,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,end_date,ann_date,audit_result,audit_agency,audit_sign'
        )

    def get_forecast(self, ts_code=None, period=None, start_date=None, end_date=None):
        """Get performance forecast"""
        logger.debug(f"[API] fetching forecast for {ts_code}...")
        return self._handle_api_call(
            self.pro.forecast,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max'
        )

    def get_fina_mainbz(self, ts_code=None, period=None, start_date=None, end_date=None):
        """Get main business composition"""
        logger.debug(f"[API] fetching mainbz for {ts_code}...")
        return self._handle_api_call(
            self.pro.fina_mainbz,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            type='P' # By Product
        )

    def get_pledge_stat(self, ts_code=None, end_date=None):
        """Get share pledge statistics"""
        logger.debug(f"[API] fetching pledge_stat for {ts_code}...")
        return self._handle_api_call(
            self.pro.pledge_stat,
            ts_code=ts_code,
            end_date=end_date
        )

    def get_repurchase(self, ts_code=None, start_date=None, end_date=None):
        """Get share repurchase"""
        logger.debug(f"[API] fetching repurchase for {ts_code}...")
        return self._handle_api_call(
            self.pro.repurchase,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date
        ) 

    def get_dividend(self, ts_code=None, start_date=None, end_date=None):
        """Get dividend history"""
        logger.debug(f"[API] fetching dividend for {ts_code}...")
        return self._handle_api_call(
            self.pro.dividend,
            ts_code=ts_code,
            ann_date=start_date # Using date range if possible, or ts_code
            # Tushare dividend API standard fields
        )

