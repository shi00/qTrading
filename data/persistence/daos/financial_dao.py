import logging

import pandas as pd

from utils.thread_pool import TaskType, ThreadPoolManager

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class FinancialDao(BaseDao):
    async def save_financial_reports(self, df: pd.DataFrame):
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
            "audit_result",
            "n_cashflow_act",
        ]

        sql = """
            INSERT INTO financial_reports (
                "ts_code","end_date","ann_date","report_type","total_revenue","revenue",
                "n_income","n_income_attr_p","total_assets","total_liab",
                "total_hldr_eqy_exc_min_int","roe","roe_dt","grossprofit_margin",
                "netprofit_margin","debt_to_assets","or_yoy","netprofit_yoy","goodwill","audit_result","n_cashflow_act"
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21
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
                "goodwill" = COALESCE(excluded."goodwill", financial_reports."goodwill"),
                "audit_result" = COALESCE(excluded."audit_result", financial_reports."audit_result"),
                "n_cashflow_act" = COALESCE(excluded."n_cashflow_act", financial_reports."n_cashflow_act")
        """
        params = await ThreadPoolManager().run_async(
            TaskType.CPU, self._prepare_data_params, df, cols, "financial_reports"
        )
        return await self._write_db(sql, params, is_many=True)

    async def get_cached_financial_records(self, period: str | None = None):
        if period:
            df = await self._read_db(
                "SELECT ts_code, end_date FROM financial_reports WHERE end_date = $1",
                (period,),
            )
        else:
            df = await self._read_db("SELECT ts_code, end_date FROM financial_reports")
        if df is None or df.empty:
            return set()
        return set(zip(df["ts_code"], df["end_date"], strict=False))

    # --- Daily Indicators (Read Only — writes go through MarketDao) ---

    async def get_latest_indicators(self, trade_date: str | None = None):
        with_date = trade_date
        if not with_date:
            df = await self._read_db(
                "SELECT MAX(trade_date) as max_td FROM daily_indicators",
            )
            if df is not None and not df.empty:
                with_date = df["max_td"].iloc[0]
            else:
                with_date = None

        if not with_date:
            return pd.DataFrame()
        return await self._read_db(
            "SELECT * FROM daily_indicators WHERE trade_date = $1",
            (with_date,),
        )

    async def get_cached_indicator_dates(self):
        df = await self._read_db("SELECT DISTINCT trade_date FROM daily_indicators")
        if df is None or df.empty:
            return set()
        return set(df["trade_date"])

    # --- Extra Savers (Boilerplate) ---
    async def save_fina_forecast(self, df: pd.DataFrame):
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
            df,
            "fina_forecast",
            cols,
            pk_columns=["ts_code", "end_date", "ann_date"],
        )

    async def save_fina_mainbz(self, df: pd.DataFrame):
        cols = [
            "ts_code",
            "end_date",
            "bz_item",
            "bz_sales",
            "bz_profit",
            "bz_cost",
            "curr_type",
            "update_flag",
        ]
        return await self._save_upsert(
            df,
            "fina_mainbz",
            cols,
            pk_columns=["ts_code", "end_date", "bz_item"],
        )

    async def save_fina_audit(self, df: pd.DataFrame):
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "audit_result",
            "audit_sign",
            "audit_fees",
            "audit_agency",
        ]
        return await self._save_upsert(
            df,
            "fina_audit",
            cols,
            pk_columns=["ts_code", "end_date"],
        )

    async def save_pledge_stat(self, df: pd.DataFrame):
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
            df,
            "pledge_stat",
            cols,
            pk_columns=["ts_code", "end_date"],
        )

    async def save_repurchase(self, df: pd.DataFrame):
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
            df,
            "repurchase",
            cols,
            pk_columns=["ts_code", "ann_date"],
        )

    async def save_dividend(self, df: pd.DataFrame):
        cols = [
            "ts_code",
            "end_date",
            "ann_date",
            "div_proc",
            "stk_div",
            "stk_bo_rate",
            "stk_co_rate",
            "cash_div",
            "cash_div_tax",
            "record_date",
            "ex_date",
        ]
        return await self._save_upsert(
            df,
            "dividend",
            cols,
            pk_columns=["ts_code", "end_date", "ann_date"],
        )

    async def get_financial_reports_history(
        self, ts_code: str, periods: int = 8
    ) -> pd.DataFrame:
        """
        获取多期财务报告历史。

        Args:
            ts_code: 股票代码
            periods: 获取的期数（默认8个季度）

        Returns:
            DataFrame with financial reports history
        """
        try:
            df = await self._read_db(
                """
                SELECT 
                    ts_code, end_date, ann_date, report_type,
                    total_revenue, revenue, n_income, n_income_attr_p,
                    total_assets, total_liab, total_hldr_eqy_exc_min_int,
                    roe, roe_dt, grossprofit_margin, netprofit_margin,
                    debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                    audit_result, n_cashflow_act
                FROM financial_reports
                WHERE ts_code = $1
                ORDER BY end_date DESC
                LIMIT $2
                """,
                (ts_code, periods),
            )

            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(
                f"[FinancialDao] Failed to get financial history for {ts_code}: {e}"
            )
            return pd.DataFrame()

    async def get_financial_reports_history_batch(
        self, ts_codes: list[str], periods: int = 8
    ) -> pd.DataFrame:
        """
        批量获取多只股票的财务报告历史。

        Args:
            ts_codes: 股票代码列表
            periods: 每只股票获取的期数（默认8个季度）

        Returns:
            DataFrame with financial reports history for all stocks
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ", ".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT * FROM (
                    SELECT 
                        ts_code, end_date, ann_date, report_type,
                        total_revenue, revenue, n_income, n_income_attr_p,
                        total_assets, total_liab, total_hldr_eqy_exc_min_int,
                        roe, roe_dt, grossprofit_margin, netprofit_margin,
                        debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                        audit_result, n_cashflow_act,
                        ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                    FROM financial_reports
                    WHERE ts_code IN ({placeholders})
                ) sub 
                WHERE rn <= ${len(ts_codes) + 1}
                ORDER BY ts_code, end_date DESC
            """
            df = await self._read_db(sql, ts_codes + [periods])
            if df is not None and not df.empty:
                df = df.drop(columns=["rn"])
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[FinancialDao] Failed to get financial history batch: {e}")
            return pd.DataFrame()

    async def get_fina_audit_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取审计意见。

        从 fina_audit 表获取完整的审计信息，包括审计意见、
        审计签字、审计费用和审计机构。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with audit results
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT DISTINCT ON (ts_code) 
                    ts_code, end_date, audit_result, audit_sign, audit_fees, audit_agency
                FROM fina_audit
                WHERE ts_code IN ({placeholders})
                  AND audit_result IS NOT NULL
                ORDER BY ts_code, end_date DESC
            """

            df = await self._read_db(sql, ts_codes)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[FinancialDao] Failed to get audit batch: {e}")
            return pd.DataFrame()

    async def get_dividend_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取分红记录。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with dividend records
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT ts_code, end_date, ann_date, cash_div, stk_div, div_proc
                FROM dividend
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, end_date DESC
            """

            df = await self._read_db(sql, ts_codes)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[FinancialDao] Failed to get dividend batch: {e}")
            return pd.DataFrame()

    async def get_pledge_stat_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取质押比例。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with pledge statistics
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT DISTINCT ON (ts_code) 
                    ts_code, end_date, pledge_count, pledge_ratio
                FROM pledge_stat
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, end_date DESC
            """

            df = await self._read_db(sql, ts_codes)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[FinancialDao] Failed to get pledge batch: {e}")
            return pd.DataFrame()

    async def get_fina_mainbz(self, ts_code: str) -> pd.DataFrame:
        """
        获取主营业务构成。

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame with main business composition
        """
        try:
            df = await self._read_db(
                """
                SELECT ts_code, end_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                FROM fina_mainbz 
                WHERE ts_code = $1 
                ORDER BY end_date DESC, bz_sales DESC 
                LIMIT 10
                """,
                (ts_code,),
            )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(
                f"[FinancialDao] Failed to get fina_mainbz for {ts_code}: {e}"
            )
            return pd.DataFrame()

    async def get_fina_mainbz_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取主营业务构成。

        使用窗口函数只取每只股票最新一期的主营构成数据，
        避免返回过多历史数据。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with main business composition for all stocks (latest period only)
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ", ".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT ts_code, end_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                FROM (
                    SELECT *, DENSE_RANK() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as dr
                    FROM fina_mainbz 
                    WHERE ts_code IN ({placeholders})
                ) sub
                WHERE dr = 1
                ORDER BY ts_code, bz_sales DESC
            """
            df = await self._read_db(sql, ts_codes)
            if df is not None and not df.empty:
                df = df.drop(columns=["dr"])
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[FinancialDao] Failed to get fina_mainbz batch: {e}")
            return pd.DataFrame()

    async def verify_stock_financial_integrity(
        self,
        ts_code: str,
        min_periods: int = 4,
    ) -> dict:
        """
        验证股票财务数据完整性。

        Args:
            ts_code: 股票代码
            min_periods: 最小报告期数量

        Returns:
            {"valid": bool, "periods": int, "tables": dict}
        """
        result = {"valid": True, "periods": 0, "tables": {}}

        try:
            df = await self._read_db(
                "SELECT COUNT(DISTINCT end_date) as periods FROM financial_reports WHERE ts_code=$1",
                (ts_code,),
            )
            periods = df["periods"].iloc[0] if df is not None and not df.empty else 0
            result["periods"] = periods

            if periods < min_periods:
                result["valid"] = False
                result["reason"] = f"报告期不足: {periods} < {min_periods}"

            df = await self._read_db(
                "SELECT COUNT(*) as cnt FROM financial_reports WHERE ts_code=$1",
                (ts_code,),
            )
            count = df["cnt"].iloc[0] if df is not None and not df.empty else 0
            result["tables"]["financial_reports"] = count
            if count == 0:
                result["valid"] = False

            # Note: Originally designed to check fina_indicator, but fina_indicator
            # has been merged into financial_reports. We check fina_audit instead
            # as a secondary validation table for financial data integrity.
            try:
                df = await self._read_db(
                    "SELECT COUNT(*) as cnt FROM fina_audit WHERE ts_code=$1",
                    (ts_code,),
                )
                count = df["cnt"].iloc[0] if df is not None and not df.empty else 0
                result["tables"]["fina_audit"] = count
            except Exception:
                result["tables"]["fina_audit"] = 0

        except Exception as e:
            result["valid"] = False
            result["error"] = str(e)

        return result

    async def get_incomplete_financial_stocks(
        self,
        min_periods: int = 4,
        sync_version: int = 1,
    ) -> set:
        """
        获取财务数据不完整的股票集合。

        用于断点续传时，将这些"伪完成"或"半残"的股票剔除出完成列表，进行强制重试。

        Args:
            min_periods: 最小报告期数量（默认4个季度）
            sync_version: 同步版本号（默认1）

        Returns:
            财务数据不完整的股票代码集合
        """
        try:
            df = await self._read_db(
                """
                SELECT s.ts_code
                FROM stock_sync_status s
                LEFT JOIN (
                    SELECT ts_code, COUNT(DISTINCT end_date) as periods
                    FROM financial_reports
                    GROUP BY ts_code
                ) f ON s.ts_code = f.ts_code
                WHERE s.sync_version = $1
                  AND (f.periods IS NULL OR f.periods < $2)
                """,
                (sync_version, min_periods),
            )

            if df is not None and not df.empty:
                return set(df["ts_code"])
            return set()
        except Exception as e:
            logger.warning(
                f"[FinancialDao] Failed to get incomplete financial stocks: {e}"
            )
            return set()
