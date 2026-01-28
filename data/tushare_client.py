import tushare as ts
import pandas as pd
import time
import datetime
import config
import logging
import random

logger = logging.getLogger(__name__)

class TushareClient:
    """
    Enhanced Tushare API client with timeout, retry, and trade calendar support.
    """
    
    # Singleton instance
    _instance = None
    _trade_cal_cache = None  # Cache trade calendar
    
    def __new__(cls, token=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, token=None):
        if self._initialized:
            return
        
        self.token = token or config.TS_TOKEN
        self.timeout = 30  # Request timeout in seconds
        self.max_retries = 3
        
        if self.token:
            ts.set_token(self.token)
            self.pro = ts.pro_api()
        else:
            self.pro = None
        
        self._initialized = True

    def set_token(self, token):
        self.token = token
        ts.set_token(token)
        self.pro = ts.pro_api()

    def _handle_api_call(self, func, **kwargs):
        """Helper to handle rate limits, retries, and errors with jittered backoff"""
        for i in range(self.max_retries):
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
                    logger.warning(f"[API] ⚠️ Rate limit hit, backing off {sleep_time:.2f}s... (attempt {i+1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue
                    
                if is_network_error:
                    # Network error: Linear backoff with jitter
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.warning(f"[API] ⚠️ Connection error, retrying in {sleep_time:.2f}s... (attempt {i+1}/{self.max_retries})")
                    time.sleep(sleep_time)
                    continue
                
                # Other errors
                if i == self.max_retries - 1:
                    logger.error(f"[API] ❌ Failed after {self.max_retries} attempts: {error_msg}")
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
        """Get daily quotes"""
        logger.debug(f"[API] fetching daily quotes for date={trade_date} code={ts_code}...")
        return self._handle_api_call(
            self.pro.daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            trade_date=trade_date
        )

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
    
    def get_income(self, period, ts_code=None):
        """Get income statement data for a specific reporting period"""
        logger.debug(f"[API] fetching income statement for period={period}...")
        return self._handle_api_call(
            self.pro.income,
            period=period,
            ts_code=ts_code,
            fields='ts_code,end_date,n_income,revenue,operate_profit' 
        )

    def get_cashflow(self, period, ts_code=None):
        """Get cashflow statement data"""
        logger.debug(f"[API] fetching cashflow for period={period}...")
        return self._handle_api_call(
            self.pro.cashflow,
            period=period,
            ts_code=ts_code,
            fields='ts_code,end_date,n_cashflow_act,c_cashflow_return_pay,n_cashflow_inv'
        )
        
    def get_balancesheet(self, period, ts_code=None):
        """Get balance sheet data"""
        logger.debug(f"[API] fetching balancesheet for period={period}...")
        return self._handle_api_call(
            self.pro.balancesheet,
            period=period,
            ts_code=ts_code,
            fields='ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int'
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

    def get_fina_indicator(self, period, ts_code=None):
        """
        Get financial indicators (ROE, growth rates, etc.)
        """
        logger.debug(f"[API] fetching fina_indicator for period={period}...")
        return self._handle_api_call(
            self.pro.fina_indicator,
            period=period,
            ts_code=ts_code,
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

    # ========== Market Overview APIs ==========
    def get_index_daily(self, ts_code=None, trade_date=None):
        """Get index daily data"""
        logger.debug(f"[API] fetching index_daily date={trade_date} code={ts_code}...")
        # Index Daily
        return self._handle_api_call(
            self.pro.index_daily,
            ts_code=ts_code,
            trade_date=trade_date
        )

    def get_moneyflow_hsgt(self, trade_date=None):
        """Get Northbound (HSGT) money flow"""
        logger.debug(f"[API] fetching moneyflow_hsgt date={trade_date}...")
        return self._handle_api_call(
            self.pro.moneyflow_hsgt,
            trade_date=trade_date
        )
