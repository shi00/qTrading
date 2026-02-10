import logging
import pandas as pd
from .base_dao import BaseDao
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

class FinancialDao(BaseDao):

    # --- Financial Reports ---
    async def save_financial_reports(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                'n_income', 'n_income_attr_p', 'total_assets', 'total_liab',
                'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin',
                'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy', 'goodwill']

        # Complex Upsert SQL
        sql = """
            INSERT INTO financial_reports (
                ts_code, end_date, ann_date, report_type, total_revenue, revenue,
                n_income, n_income_attr_p, total_assets, total_liab,
                total_hldr_eqy_exc_min_int, roe, roe_dt, grossprofit_margin,
                netprofit_margin, debt_to_assets, or_yoy, netprofit_yoy, goodwill
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) 
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
        """
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        return await self._write_db(sql, params, is_many=True)

    async def get_latest_financials(self):
        sql = '''
              SELECT f.*
              FROM financial_reports f
                       INNER JOIN (SELECT ts_code, MAX(end_date) as max_date
                                   FROM financial_reports
                                   GROUP BY ts_code) latest
                                  ON f.ts_code = latest.ts_code AND f.end_date = latest.max_date \
              '''
        return await self._read_db(sql)

    async def get_cached_financial_records(self, period=None):
        async with self.engine.connect() as conn:
            if period:
                res = await conn.exec_driver_sql("SELECT ts_code, end_date FROM financial_reports WHERE end_date = ?",
                                                 (period,))
            else:
                res = await conn.exec_driver_sql("SELECT ts_code, end_date FROM financial_reports")
            return set((row[0], row[1]) for row in res.fetchall())

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df, priority=None):
        cols = ['ts_code', 'trade_date', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm', 'total_mv',
                'circ_mv', 'total_share', 'float_share', 'free_share', 'turnover_rate', 'turnover_rate_f']
        return await self._save_standard(df, "daily_indicators", cols)

    async def get_latest_indicators(self, trade_date=None):
        with_date = trade_date
        if not with_date:
            async with self.engine.connect() as conn:
                r = await conn.exec_driver_sql("SELECT MAX(trade_date) FROM daily_indicators")
                row = r.fetchone()
                with_date = row[0] if row else None

        if not with_date: return pd.DataFrame()
        return await self._read_db("SELECT * FROM daily_indicators WHERE trade_date = ?", (with_date,))

    async def get_cached_indicator_dates(self):
        async with self.engine.connect() as conn:
            res = await conn.exec_driver_sql("SELECT DISTINCT trade_date FROM daily_indicators")
            return set(row[0] for row in res.fetchall())

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df):
        cols = ['ts_code', 'end_date', 'ann_date', 'type', 'p_change_min', 'p_change_max', 'net_profit_min',
                'net_profit_max']
        return await self._save_standard(df, "fina_forecast", cols)

    async def save_fina_mainbz(self, df):
        cols = ['ts_code', 'end_date', 'bz_item', 'bz_sales', 'bz_profit', 'bz_cost', 'curr_type']
        return await self._save_standard(df, "fina_mainbz", cols)
    
    async def save_fina_audit(self, df):
        cols = ['ts_code', 'end_date', 'ann_date', 'audit_result', 'audit_fees', 'audit_agency']
        return await self._save_standard(df, "fina_audit", cols)

    async def save_pledge_stat(self, df):
        cols = ['ts_code', 'end_date', 'pledge_count', 'unrest_pledge', 'rest_pledge', 'total_share', 'pledge_ratio']
        return await self._save_standard(df, "pledge_stat", cols)

    async def save_repurchase(self, df):
        cols = ['ts_code', 'ann_date', 'end_date', 'proc', 'exp_date', 'vol', 'amount', 'high_limit', 'low_limit']
        return await self._save_standard(df, "repurchase", cols)

    async def save_dividend(self, df):
        cols = ['ts_code', 'end_date', 'ann_date', 'div_proc', 'stk_div', 'stk_bo_rate', 'stk_co_rate', 'cash_div_tax',
                'cash_div_tax_rate', 'record_date', 'ex_date']
        return await self._save_standard(df, "dividend", cols)
