import pandas as pd
import datetime
import asyncio
import logging
from data.tushare_client import TushareClient
from data.cache_manager import CacheManager
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class DataProcessor:
    """
    Main data processing class. Uses singleton TushareClient.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        token = ConfigHandler.get_token()
        self.api = TushareClient(token=token)
        self.cache = CacheManager()
        self._initialized = True

    def get_latest_trade_date(self):
        """Get most recent trade date"""
        now = datetime.datetime.now()
        # If before 16:00, use yesterday
        if now.hour < 16:
            now -= datetime.timedelta(days=1)
        # Skip weekends
        while now.weekday() >= 5:
            now -= datetime.timedelta(days=1)
        return now.strftime('%Y%m%d')

    def get_trade_dates(self, start_date, end_date):
        """Get list of actual trade dates using Tushare trade_cal API"""
        try:
            # Use trade calendar API for accurate dates
            dates = self.api.get_trade_dates(start_date, end_date)
            if dates:
                return dates
        except Exception as e:
            logger.warning(f"[DataProcessor] Trade calendar failed, using fallback: {e}")
        
        # Fallback: simple weekend skip
        dates = []
        current = datetime.datetime.strptime(start_date, '%Y%m%d')
        end = datetime.datetime.strptime(end_date, '%Y%m%d')
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime('%Y%m%d'))
            current += datetime.timedelta(days=1)
        return dates

    async def init_data(self):
        """Initialize DB with enhanced schema"""
        await self.cache.init_db()

    # ===== Single Day Sync Methods =====
    
    async def sync_daily_quotes_for_date(self, trade_date):
        """Sync daily quotes for a specific date"""
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date))
        if df is not None and not df.empty:
            count = await self.cache.save_daily_quotes(df)
            return count
        return 0

    async def sync_daily_indicators_for_date(self, trade_date):
        """Sync daily indicators for a specific date"""
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date))
        if df is not None and not df.empty:
            count = await self.cache.save_daily_indicators(df)
            return count
        return 0

    # ===== Full Sync Methods =====

    async def sync_stock_basic(self):
        """Sync stock basic info"""
        logger.info("[DataProcessor] Syncing stock basic info...")
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_stock_list())
        if df is not None and not df.empty:
            count = await self.cache.save_stock_basic(df)
            await self.cache.update_sync_status('stock_basic', datetime.datetime.now().strftime('%Y%m%d'), count)
            logger.info(f"[DataProcessor] ✅ Saved {count} stocks to cache.")
            return count
        return 0

    async def sync_daily_market_snapshot(self, trade_date=None):
        """
        Sync daily market data (quotes + indicators) for a single date.
        This is the main method for daily updates.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing market snapshot for {trade_date}...")
        
        # Check if already cached
        cached_date = await self.cache.get_latest_trade_date()
        if cached_date == trade_date:
            logger.info(f"[DataProcessor] Cache hit! Data for {trade_date} already exists.")
            df = await self.cache.get_screening_data(trade_date)
            return df
        
        loop = asyncio.get_running_loop()
        
        # Fetch quotes and indicators in parallel
        logger.info(f"[DataProcessor] Fetching from Tushare API...")
        future_quotes = loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date))
        future_basic = loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date))
        
        df_quotes, df_basic = await asyncio.gather(future_quotes, future_basic)
        
        # Handle case where today's data is not ready
        if (df_quotes is None or df_quotes.empty) and (df_basic is None or df_basic.empty):
            prev_date = (datetime.datetime.strptime(trade_date, '%Y%m%d') - datetime.timedelta(days=1)).strftime('%Y%m%d')
            logger.info(f"[DataProcessor] Today's data not ready, trying {prev_date}...")
            future_quotes = loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=prev_date))
            future_basic = loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=prev_date))
            df_quotes, df_basic = await asyncio.gather(future_quotes, future_basic)
            trade_date = prev_date
        
        # Save to cache
        quotes_count = 0
        indicators_count = 0
        
        if df_quotes is not None and not df_quotes.empty:
            quotes_count = await self.cache.save_daily_quotes(df_quotes)
            await self.cache.update_sync_status('daily_quotes', trade_date, quotes_count)
            logger.info(f"[DataProcessor] ✅ Saved {quotes_count} daily quotes.")
        
        if df_basic is not None and not df_basic.empty:
            indicators_count = await self.cache.save_daily_indicators(df_basic)
            await self.cache.update_sync_status('daily_indicators', trade_date, indicators_count)
            logger.info(f"[DataProcessor] ✅ Saved {indicators_count} daily indicators.")
        
        # Return merged data for immediate use
        if df_quotes is not None and df_basic is not None:
            df_merged = pd.merge(df_quotes, df_basic, on=['ts_code', 'trade_date'], how='outer', suffixes=('', '_ind'))
            return df_merged
        
        return df_quotes if df_quotes is not None else df_basic

    async def sync_historical_data(self, days=250, progress_callback=None):
        """
        Sync historical data for the specified number of days.
        INCREMENTAL: Only fetches dates not already in cache (resumable).
        
        :param days: Number of trading days to sync (default 250 = ~1 year)
        :param progress_callback: Optional callback function(current, total, message)
        """
        logger.info(f"[DataProcessor] Starting incremental historical sync for {days} days...")
        
        end_date = self.get_latest_trade_date()
        start = datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=int(days * 1.5))
        start_date = start.strftime('%Y%m%d')
        
        # Get all required trade dates
        all_trade_dates = self.get_trade_dates(start_date, end_date)[-days:]
        
        # Get already cached dates
        cached_dates = await self.cache.get_cached_trade_dates()
        
        # Filter to only sync missing dates
        missing_dates = [d for d in all_trade_dates if d not in cached_dates]
        
        total_required = len(all_trade_dates)
        already_cached = len(all_trade_dates) - len(missing_dates)
        to_sync = len(missing_dates)
        
        logger.info(f"[DataProcessor] 📊 Required: {total_required} dates, Cached: {already_cached}, To sync: {to_sync}")
        
        if to_sync == 0:
            logger.info("[DataProcessor] ✅ All data already cached, nothing to sync!")
            if progress_callback:
                progress_callback(total_required, total_required, "已有全部数据")
            return {'quotes': 0, 'indicators': 0, 'skipped': already_cached}
        
        loop = asyncio.get_running_loop()
        
        quotes_total = 0
        indicators_total = 0
        
        for i, date in enumerate(missing_dates):
            try:
                # Fetch data for missing date
                df_quotes = await loop.run_in_executor(None, lambda d=date: self.api.get_daily_quotes(trade_date=d))
                df_basic = await loop.run_in_executor(None, lambda d=date: self.api.get_daily_basic(trade_date=d))
                
                # Save to cache
                if df_quotes is not None and not df_quotes.empty:
                    quotes_total += await self.cache.save_daily_quotes(df_quotes)
                if df_basic is not None and not df_basic.empty:
                    indicators_total += await self.cache.save_daily_indicators(df_basic)
                
                # Progress callback - show overall progress including cached
                current_total = already_cached + i + 1
                if progress_callback:
                    progress_callback(current_total, total_required, f"同步 {date} (新增 {i+1}/{to_sync})")
                
                # Rate limiting
                if (i + 1) % 20 == 0:
                    logger.info(f"[DataProcessor] Progress: {i+1}/{to_sync} new dates synced...")
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"[DataProcessor] ⚠️ Error syncing {date}: {e}")
                continue
        
        # Update sync status
        await self.cache.update_sync_status('daily_quotes', end_date, quotes_total)
        await self.cache.update_sync_status('daily_indicators', end_date, indicators_total)
        
        logger.info(f"[DataProcessor] ✅ Incremental sync complete! New quotes: {quotes_total}, Skipped: {already_cached}")
        return {'quotes': quotes_total, 'indicators': indicators_total, 'skipped': already_cached}

    async def sync_financial_reports(self, periods=None):
        """
        Sync financial report data for specified periods.
        Uses batch API (income/balancesheet by period) instead of per-stock API.
        
        :param periods: List of periods like ['20231231', '20230930', '20230630', '20230331']
                       If None, syncs last 4 quarters
        """
        if periods is None:
            # Calculate last 4 quarter end dates
            now = datetime.datetime.now()
            periods = []
            for i in range(4):
                quarter_end = now - datetime.timedelta(days=90 * (i + 1))
                year = quarter_end.year
                month = quarter_end.month
                # Map to quarter end: Q1=0331, Q2=0630, Q3=0930, Q4=1231
                if month <= 3:
                    period = f"{year-1}1231"
                elif month <= 6:
                    period = f"{year}0331"
                elif month <= 9:
                    period = f"{year}0630"
                else:
                    period = f"{year}0930"
                if period not in periods:
                    periods.append(period)
        
        logger.info(f"[DataProcessor] Syncing financial reports for periods: {periods}")
        
        loop = asyncio.get_running_loop()
        total_saved = 0
        
        for period in periods:
            try:
                logger.info(f"[DataProcessor] Fetching financial data for {period}...")
                
                # Fetch income, balance sheet, AND financial indicators in parallel
                future_income = loop.run_in_executor(None, lambda p=period: self.api.get_income(period=p))
                future_balance = loop.run_in_executor(None, lambda p=period: self.api.get_balancesheet(period=p))
                future_indicator = loop.run_in_executor(None, lambda p=period: self.api.get_fina_indicator(period=p))
                
                df_income, df_balance, df_indicator = await asyncio.gather(future_income, future_balance, future_indicator)
                
                if df_income is None or df_income.empty:
                    logger.warning(f"[DataProcessor] ⚠️ No income data for {period}")
                    continue
                
                # Prepare income data
                income_cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 
                              'total_revenue', 'revenue', 'n_income', 'n_income_attr_p']
                for col in income_cols:
                    if col not in df_income.columns:
                        df_income[col] = None
                df_income = df_income[income_cols].copy()
                
                # Prepare balance data
                if df_balance is not None and not df_balance.empty:
                    bal_cols = ['ts_code', 'total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int']
                    for col in bal_cols:
                        if col not in df_balance.columns:
                            df_balance[col] = None
                    df_balance = df_balance[bal_cols].copy()
                    
                    # Merge Income + Balance
                    df_merged = pd.merge(df_income, df_balance, on='ts_code', how='left')
                else:
                    df_merged = df_income
                    df_merged['total_assets'] = None
                    df_merged['total_liab'] = None
                    df_merged['total_hldr_eqy_exc_min_int'] = None

                # Prepare Indicator data
                if df_indicator is not None and not df_indicator.empty:
                    # fina_indicator fields: roe, roe_dt, debt_to_assets, q_sales_yoy, q_profit_yoy, or_yoy, netprofit_yoy
                    # We map them to our schema
                    # Note: We need to handle potential duplicates if merging
                    ind_cols = ['ts_code', 'roe', 'roe_dt', 'debt_to_assets', 
                               'netprofit_margin', 'grossprofit_margin', 
                               'or_yoy', 'netprofit_yoy']
                    
                    for col in ind_cols:
                        if col not in df_indicator.columns:
                            df_indicator[col] = None
                    
                    df_ind_subset = df_indicator[ind_cols].copy()
                    
                    # Merge (Income+Balance) + Indicators
                    df_merged = pd.merge(df_merged, df_ind_subset, on='ts_code', how='left', suffixes=('', '_new'))
                    
                    # Update columns with indicator data if available, else keep existing or calculated
                    for col in ['roe', 'roe_dt', 'debt_to_assets', 'netprofit_margin', 'grossprofit_margin', 'or_yoy', 'netprofit_yoy']:
                        if col in df_merged.columns and f'{col}_new' in df_merged.columns:
                            df_merged[col] = df_merged[f'{col}_new'].fillna(df_merged[col])
                            df_merged.drop(columns=[f'{col}_new'], inplace=True)
                
                # Fallback calculations if indicators are missing
                if 'roe' not in df_merged.columns: df_merged['roe'] = None
                
                # Calculate derived metrics ONLY if still missing (fallback)
                mask_roe = df_merged['roe'].isna()
                df_merged.loc[mask_roe, 'roe'] = df_merged.loc[mask_roe].apply(
                    lambda x: (x['n_income'] / x['total_hldr_eqy_exc_min_int'] * 100) 
                    if x['total_hldr_eqy_exc_min_int'] and x['total_hldr_eqy_exc_min_int'] > 0 else None, 
                    axis=1
                )
                
                if 'debt_to_assets' not in df_merged.columns: df_merged['debt_to_assets'] = None
                mask_debt = df_merged['debt_to_assets'].isna()
                df_merged.loc[mask_debt, 'debt_to_assets'] = df_merged.loc[mask_debt].apply(
                    lambda x: (x['total_liab'] / x['total_assets'] * 100) 
                    if x['total_assets'] and x['total_assets'] > 0 else None, 
                    axis=1
                )
                
                # Ensure all schema columns exist
                schema_cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                             'n_income', 'n_income_attr_p', 'total_assets', 'total_liab', 
                             'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin', 
                             'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
                
                for col in schema_cols:
                    if col not in df_merged.columns:
                        df_merged[col] = None
                
                # Save
                count = await self.cache.save_financial_reports(df_merged)
                total_saved += count
                logger.info(f"[DataProcessor] ✅ Saved {count} financial records for {period}")
                
            except Exception as e:
                logger.error(f"[DataProcessor] ❌ Error syncing {period}: {e}")
                logger.error(traceback.format_exc())
                continue
        
        await self.cache.update_sync_status('financial_reports', periods[0] if periods else '', total_saved)
        logger.info(f"[DataProcessor] ✅ Financial sync complete! Total: {total_saved} records")
        return total_saved

    async def sync_moneyflow(self, trade_date=None):
        """
        Sync money flow data for a specific date.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing money flow for {trade_date}...")
        loop = asyncio.get_running_loop()
        
        try:
            df = await loop.run_in_executor(None, lambda: self.api.get_moneyflow(trade_date=trade_date))
            if df is not None and not df.empty:
                count = await self.cache.save_moneyflow(df)
                await self.cache.update_sync_status('moneyflow_daily', trade_date, count)
                logger.info(f"[DataProcessor] ✅ Saved {count} money flow records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] ⚠️ Error syncing money flow: {e}")
        return 0

    async def sync_northbound(self, trade_date=None):
        """
        Sync northbound holding data for a specific date.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing northbound holdings for {trade_date}...")
        loop = asyncio.get_running_loop()
        
        try:
            df = await loop.run_in_executor(None, lambda: self.api.get_hk_hold(trade_date=trade_date))
            if df is not None and not df.empty:
                count = await self.cache.save_northbound(df)
                await self.cache.update_sync_status('northbound_holding', trade_date, count)
                logger.info(f"[DataProcessor] ✅ Saved {count} northbound records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] ⚠️ Error syncing northbound: {e}")
        return 0

    async def sync_all_daily(self, trade_date=None):
        """
        Sync all daily data: quotes, indicators, moneyflow, northbound.
        This is the main method for daily updates.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] === Full daily sync for {trade_date} ===")
        
        results = {
            'quotes': 0,
            'indicators': 0,
            'moneyflow': 0,
            'northbound': 0
        }
        
        # Sync in parallel
        loop = asyncio.get_running_loop()
        
        # Fetch all data types
        # Fetch all data types in parallel
        logger.info(f"[DataProcessor] Fetching extended data from Tushare API...")
        futures = [
            loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_moneyflow(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_hk_hold(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_top_list(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_block_trade(trade_date=trade_date)),
        ]
        
        results = await asyncio.gather(*futures, return_exceptions=True)
        df_quotes, df_basic, df_mf, df_north, df_lhb, df_block = results
        
        saved_counts = {}
        
        # Save each type (handling exceptions)
        if isinstance(df_quotes, pd.DataFrame) and not df_quotes.empty:
            saved_counts['quotes'] = await self.cache.save_daily_quotes(df_quotes)
            
        if isinstance(df_basic, pd.DataFrame) and not df_basic.empty:
            saved_counts['indicators'] = await self.cache.save_daily_indicators(df_basic)
            
        if isinstance(df_mf, pd.DataFrame) and not df_mf.empty:
            saved_counts['moneyflow'] = await self.cache.save_moneyflow(df_mf)
            
        if isinstance(df_north, pd.DataFrame) and not df_north.empty:
            saved_counts['northbound'] = await self.cache.save_northbound(df_north)
            
        if isinstance(df_lhb, pd.DataFrame) and not df_lhb.empty:
            saved_counts['top_list'] = await self.cache.save_top_list(df_lhb)
            
        if isinstance(df_block, pd.DataFrame) and not df_block.empty:
            saved_counts['block_trade'] = await self.cache.save_block_trade(df_block)
        
        logger.info(f"[DataProcessor] === Sync complete: {saved_counts} ===")
        return saved_counts


    async def prepare_screening_context(self):
        """
        Prepare context dictionary for strategies.
        Fetches latest merged data from cache.
        """
        context = {}
        
        # 1. Main merged daily data
        df = await self.get_screening_data()
        context['screening_data'] = df
        
        # 2. Northbound holding
        try:
            northbound_df = await self.cache.get_latest_northbound()
            context['northbound_data'] = northbound_df
        except:
            context['northbound_data'] = pd.DataFrame()

        # 3. LHB (Dragon Tiger Board) - fetching latest available
        # Note: LHB data is daily, we look for the latest trade date we have data for
        try:
            latest_date = await self.cache.get_latest_trade_date()
            if latest_date:
                lhb_df = await self.cache.get_top_list(trade_date=latest_date)
                context['top_list'] = lhb_df
            else:
                context['top_list'] = pd.DataFrame()
        except:
            context['top_list'] = pd.DataFrame()

        # 4. Block Trade
        try:
            latest_date = await self.cache.get_latest_trade_date()
            if latest_date:
                block_df = await self.cache.get_block_trade(trade_date=latest_date)
                context['block_trade'] = block_df
            else:
                context['block_trade'] = pd.DataFrame()
        except:
            context['block_trade'] = pd.DataFrame()
        
        return context

    async def full_historical_sync(self, years=3, progress_callback=None):
        """
        Full historical data sync for specified years.
        Default is 3 years (~750 trading days).
        
        :param years: Number of years to sync (default 3)
        :param progress_callback: Optional callback function(current, total, message)
        """
        days = int(years * 250)  # ~250 trading days per year
        print(f"[DataProcessor] === Starting {years}-year historical sync ({days} days) ===")
        
        # 1. Sync stock basic first
        await self.sync_stock_basic()
        
        # 2. Sync historical quotes and indicators
        await self.sync_historical_data(days=days, progress_callback=progress_callback)
        
        # 3. Sync financial reports (last 12 quarters for 3 years)
        periods = []
        now = datetime.datetime.now()
        for i in range(12):  # 12 quarters = 3 years
            quarter_end = now - datetime.timedelta(days=90 * i)
            year = quarter_end.year
            month = quarter_end.month
            if month <= 3:
                period = f"{year-1}1231"
            elif month <= 6:
                period = f"{year}0331"
            elif month <= 9:
                period = f"{year}0630"
            else:
                period = f"{year}0930"
            if period not in periods:
                periods.append(period)
        
        await self.sync_financial_reports(periods=periods)
        
        print(f"[DataProcessor] === {years}-year historical sync complete! ===")

    # ===== Screening Methods =====

    async def get_screening_data(self, trade_date=None):
        """
        Get merged data for screening from local cache.
        This is the main method used by screening strategies.
        """
        return await self.cache.get_screening_data(trade_date)

    async def prepare_screening_context(self):
        """
        Build the complete context for screening strategies.
        Returns dict with all available data for strategies to use.
        """
        context = {}
        
        # 1. Main screening data (daily + indicators + financials merged)
        df = await self.get_screening_data()
        if df is None or df.empty:
            # Try to sync if no data
            await self.sync_daily_market_snapshot()
            df = await self.get_screening_data()
        
        context['screening_data'] = df
        
        # 2. Northbound holdings (for NorthboundStrategy)
        try:
            northbound_df = await self.cache.get_latest_northbound()
            context['northbound_data'] = northbound_df
        except:
            context['northbound_data'] = pd.DataFrame()
        
        # 3. Money flow (optional, can be expensive)
        # context['moneyflow'] = await self.cache.get_moneyflow()
        
        return context

    async def get_strategy_data(self):
        """Facade method for UI"""
        return await self.prepare_screening_context()

    # ===== Backward Compatibility =====
    
    async def sync_financial_report(self, period):
        """Backward compatibility wrapper"""
        return await self.sync_financial_reports(periods=[period])
