import logging
import pandas as pd
from .base_dao import BaseDao
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)


class FinancialDao(BaseDao):
    # --- Financial Reports ---
    async def save_financial_reports(self, df):
        if df is None or df.empty:
            return 0
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "report_type",
            "total_revenue",
            "revenue",
            "n_income",
            "n_income_attr_p",
            "total_assets",
            "total_liab",
            "total_hldr_eqy_exc_min_int",
            "roe",
            "roe_dt",
            "grossprofit_margin",
            "netprofit_margin",
            "debt_to_assets",
            "or_yoy",
            "netprofit_yoy",
            "goodwill",
        ]

        # Complex Upsert SQL (COALESCE preserves existing non-null values)
        sql = """
            INSERT INTO financial_reports (
                "ts_code","end_date","ann_date","report_type","total_revenue","revenue",
                "n_income","n_income_attr_p","total_assets","total_liab",
                "total_hldr_eqy_exc_min_int","roe","roe_dt","grossprofit_margin",
                "netprofit_margin","debt_to_assets","or_yoy","netprofit_yoy","goodwill"
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19
            ) 
            ON CONFLICT("ts_code","end_date") DO UPDATE SET
                "ann_date" = COALESCE(excluded."ann_date", financial_reports."ann_date"),
                "report_type" = COALESCE(excluded."report_type", financial_reports."report_type"),
                "total_revenue" = COALESCE(excluded."total_revenue", financial_reports."total_revenue"),
                "revenue" = COALESCE(excluded."revenue", financial_reports."revenue"),
                "n_income" = COALESCE(excluded."n_income", financial_reports."n_income"),
                "n_income_attr_p" = COALESCE(excluded."n_income_attr_p", financial_reports."n_income_attr_p"),
                "total_assets" = COALESCE(excluded."total_assets", financial_reports."total_assets"),
                "total_liab" = COALESCE(excluded."total_liab", financial_reports."total_liab"),
                "total_hldr_eqy_exc_min_int" = COALESCE(excluded."total_hldr_eqy_exc_min_int", financial_reports."total_hldr_eqy_exc_min_int"),
                "roe" = COALESCE(excluded."roe", financial_reports."roe"),
                "roe_dt" = COALESCE(excluded."roe_dt", financial_reports."roe_dt"),
                "grossprofit_margin" = COALESCE(excluded."grossprofit_margin", financial_reports."grossprofit_margin"),
                "netprofit_margin" = COALESCE(excluded."netprofit_margin", financial_reports."netprofit_margin"),
                "debt_to_assets" = COALESCE(excluded."debt_to_assets", financial_reports."debt_to_assets"),
                "or_yoy" = COALESCE(excluded."or_yoy", financial_reports."or_yoy"),
                "netprofit_yoy" = COALESCE(excluded."netprofit_yoy", financial_reports."netprofit_yoy"),
                "goodwill" = COALESCE(excluded."goodwill", financial_reports."goodwill")
        """
        params = await ThreadPoolManager().run_async(
            TaskType.CPU, self._prepare_data_params, df, cols
        )
        return await self._write_db(sql, params, is_many=True)

    async def get_latest_financials(self):
        """Get latest financial report per stock.

        WARNING: Dead code — only called from test_cache_manager.py L148.
        Uses MAX(end_date) which is a future-function risk if used in strategies.
        If reactivated, must change to MAX(ann_date) with ann_date <= cutoff_date filter.
        """
        sql = """
              SELECT f.*
              FROM financial_reports f
                       INNER JOIN (SELECT ts_code, MAX(end_date) as max_date
                                   FROM financial_reports
                                   GROUP BY ts_code) latest
                                  ON f.ts_code = latest.ts_code AND f.end_date = latest.max_date \
              """
        return await self._read_db(sql)

    async def get_cached_financial_records(self, period=None):
        if period:
            df = await self._read_db(
                "SELECT ts_code, end_date FROM financial_reports WHERE end_date = $1",
                (period,),
            )
        else:
            df = await self._read_db("SELECT ts_code, end_date FROM financial_reports")
        if df is None or df.empty:
            return set()
        return set(zip(df["ts_code"], df["end_date"]))

    # --- Daily Indicators (Read Only — writes go through MarketDao) ---

    async def get_latest_indicators(self, trade_date=None):
        with_date = trade_date
        if not with_date:
            df = await self._read_db(
                "SELECT MAX(trade_date) as max_td FROM daily_indicators"
            )
            if df is not None and not df.empty:
                with_date = df["max_td"].iloc[0]
            else:
                with_date = None

        if not with_date:
            return pd.DataFrame()
        return await self._read_db(
            "SELECT * FROM daily_indicators WHERE trade_date = $1", (with_date,)
        )

    async def get_cached_indicator_dates(self):
        df = await self._read_db("SELECT DISTINCT trade_date FROM daily_indicators")
        if df is None or df.empty:
            return set()
        return set(df["trade_date"])

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df):
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "type",
            "p_change_min",
            "p_change_max",
            "net_profit_min",
            "net_profit_max",
        ]
        return await self._save_upsert(
            df, "fina_forecast", cols, pk_columns=["ts_code", "end_date", "ann_date"]
        )

    async def save_fina_mainbz(self, df):
        cols = [
            "ts_code",
            "end_date",
            "bz_item",
            "bz_sales",
            "bz_profit",
            "bz_cost",
            "curr_type",
        ]
        return await self._save_upsert(
            df, "fina_mainbz", cols, pk_columns=["ts_code", "end_date", "bz_item"]
        )

    async def save_fina_audit(self, df):
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "audit_result",
            "audit_fees",
            "audit_agency",
        ]
        return await self._save_upsert(
            df, "fina_audit", cols, pk_columns=["ts_code", "end_date"]
        )

    async def save_pledge_stat(self, df):
        cols = [
            "ts_code",
            "end_date",
            "pledge_count",
            "unrest_pledge",
            "rest_pledge",
            "total_share",
            "pledge_ratio",
        ]
        return await self._save_upsert(
            df, "pledge_stat", cols, pk_columns=["ts_code", "end_date"]
        )

    async def save_repurchase(self, df):
        cols = [
            "ts_code",
            "ann_date",
            "end_date",
            "proc",
            "exp_date",
            "vol",
            "amount",
            "high_limit",
            "low_limit",
        ]
        return await self._save_upsert(
            df, "repurchase", cols, pk_columns=["ts_code", "ann_date"]
        )

    async def save_dividend(self, df):
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "div_proc",
            "stk_div",
            "stk_bo_rate",
            "stk_co_rate",
            "cash_div_tax",
            "cash_div_tax_rate",
            "record_date",
            "ex_date",
        ]
        return await self._save_upsert(
            df, "dividend", cols, pk_columns=["ts_code", "end_date", "ann_date"]
        )
