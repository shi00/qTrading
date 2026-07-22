import asyncio
import logging

import pandas as pd

from data.persistence.models import (
    FinaAudit,
    FinaForecast,
    FinaMainbz,
    FinancialReports,
    PledgeStat,
    Repurchase,
    Dividend,
    get_model_columns,
    get_model_pk_columns,
)
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class FinancialDao(BaseDao):
    async def save_financial_reports(self, df: pd.DataFrame, conn=None):
        if df is None or df.empty:
            return 0
        cols = get_model_columns(FinancialReports)
        pk_columns = get_model_pk_columns(FinancialReports)

        return await self._save_upsert(
            df,
            "financial_reports",
            cols,
            pk_columns=pk_columns,
            conn=conn,
        )

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
                """
                SELECT MAX(trade_date) as max_td
                FROM daily_indicators
                WHERE trade_date <= (SELECT MAX(trade_date) FROM daily_quotes)
                """,
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
    async def save_fina_forecast(self, df: pd.DataFrame) -> int:
        cols = get_model_columns(FinaForecast)
        pk_columns = get_model_pk_columns(FinaForecast)
        return await self._save_upsert(
            df,
            "fina_forecast",
            cols,
            pk_columns=pk_columns,
        )

    async def save_fina_mainbz(self, df: pd.DataFrame):
        cols = get_model_columns(FinaMainbz)
        pk_columns = get_model_pk_columns(FinaMainbz)
        return await self._save_upsert(
            df,
            "fina_mainbz",
            cols,
            pk_columns=pk_columns,
        )

    async def save_fina_audit(self, df: pd.DataFrame):
        cols = get_model_columns(FinaAudit)
        pk_columns = get_model_pk_columns(FinaAudit)
        return await self._save_upsert(
            df,
            "fina_audit",
            cols,
            pk_columns=pk_columns,
        )

    async def save_pledge_stat(self, df: pd.DataFrame) -> int:
        # ann_date excluded: Tushare pledge_stat API does not return ann_date (MD-001).
        # If Tushare ever starts returning it, remove this exclude to persist it.
        cols = get_model_columns(PledgeStat, exclude={"ann_date"})
        pk_columns = get_model_pk_columns(PledgeStat)
        return await self._save_upsert(
            df,
            "pledge_stat",
            cols,
            pk_columns=pk_columns,
        )

    async def save_repurchase(self, df: pd.DataFrame):
        cols = get_model_columns(Repurchase)
        pk_columns = get_model_pk_columns(Repurchase)
        return await self._save_upsert(
            df,
            "repurchase",
            cols,
            pk_columns=pk_columns,
        )

    async def save_dividend(self, df: pd.DataFrame) -> int:
        cols = get_model_columns(Dividend)
        pk_columns = get_model_pk_columns(Dividend)
        return await self._save_upsert(
            df,
            "dividend",
            cols,
            pk_columns=pk_columns,
        )

    async def get_financial_reports_history(self, ts_code: str, periods: int = 8, as_of_date=None) -> pd.DataFrame:
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    """
                    SELECT
                        ts_code, end_date, ann_date, report_type,
                        total_revenue, revenue, n_income, n_income_attr_p,
                        total_assets, total_liab, total_hldr_eqy_exc_min_int,
                        roe, roe_dt, grossprofit_margin, netprofit_margin,
                        debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                        audit_result, n_cashflow_act, money_cap, accounts_receiv
                    FROM financial_reports
                    WHERE ts_code = $1 AND ann_date <= $2
                    ORDER BY end_date DESC
                    LIMIT $3
                    """,
                    (ts_code, as_of_date, periods),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT
                        ts_code, end_date, ann_date, report_type,
                        total_revenue, revenue, n_income, n_income_attr_p,
                        total_assets, total_liab, total_hldr_eqy_exc_min_int,
                        roe, roe_dt, grossprofit_margin, netprofit_margin,
                        debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                        audit_result, n_cashflow_act, money_cap, accounts_receiv
                    FROM financial_reports
                    WHERE ts_code = $1
                    ORDER BY end_date DESC
                    LIMIT $2
                    """,
                    (ts_code, periods),
                )

            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[FinancialDao] Failed to get financial history for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()

    async def get_financial_reports_history_batch(
        self, ts_codes: list[str], periods: int = 8, as_of_date=None
    ) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:

                def sql_template_fn(placeholders, chunk_len):
                    ann_date_param = chunk_len + 1
                    limit_param = chunk_len + 2
                    return f"""
                        SELECT * FROM (
                            SELECT
                                ts_code, end_date, ann_date, report_type,
                                total_revenue, revenue, n_income, n_income_attr_p,
                                total_assets, total_liab, total_hldr_eqy_exc_min_int,
                                roe, roe_dt, grossprofit_margin, netprofit_margin,
                                debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                                audit_result, n_cashflow_act, money_cap, accounts_receiv,
                                ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                            FROM financial_reports
                            WHERE ts_code IN ({placeholders}) AND ann_date <= ${ann_date_param}
                        ) sub
                        WHERE rn <= ${limit_param}
                        ORDER BY ts_code, end_date DESC
                    """

                df = await self.chunked_in_query(
                    self._read_db,
                    sql_template_fn,
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date, periods],
                )
            else:

                def sql_template_fn_no_as_of(placeholders, chunk_len):
                    limit_param = chunk_len + 1
                    return f"""
                        SELECT * FROM (
                            SELECT
                                ts_code, end_date, ann_date, report_type,
                                total_revenue, revenue, n_income, n_income_attr_p,
                                total_assets, total_liab, total_hldr_eqy_exc_min_int,
                                roe, roe_dt, grossprofit_margin, netprofit_margin,
                                debt_to_assets, or_yoy, netprofit_yoy, goodwill,
                                audit_result, n_cashflow_act, money_cap, accounts_receiv,
                                ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                            FROM financial_reports
                            WHERE ts_code IN ({placeholders})
                        ) sub
                        WHERE rn <= ${limit_param}
                        ORDER BY ts_code, end_date DESC
                    """

                df = await self.chunked_in_query(
                    self._read_db,
                    sql_template_fn_no_as_of,
                    ts_codes,
                    params_fn=lambda chunk: [periods],
                )

            if df is not None and not df.empty and "rn" in df.columns:
                df = df.drop(columns=["rn"])
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get financial history batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_fina_audit_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                return await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT DISTINCT ON (ts_code)
                            ts_code, end_date, ann_date, audit_result, audit_sign, audit_fees, audit_agency
                        FROM fina_audit
                        WHERE ts_code IN ({placeholders})
                          AND audit_result IS NOT NULL
                          AND ann_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC, ann_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ON (ts_code)
                        ts_code, end_date, ann_date, audit_result, audit_sign, audit_fees, audit_agency
                    FROM fina_audit
                    WHERE ts_code IN ({placeholders})
                      AND audit_result IS NOT NULL
                    ORDER BY ts_code, end_date DESC, ann_date DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get audit batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_dividend_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                return await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT ts_code, end_date, ann_date, cash_div, stk_div, div_proc
                        FROM dividend
                        WHERE ts_code IN ({placeholders})
                          AND ann_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT ts_code, end_date, ann_date, cash_div, stk_div, div_proc
                    FROM dividend
                    WHERE ts_code IN ({placeholders})
                    ORDER BY ts_code, end_date DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get dividend batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_pledge_stat_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                return await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT DISTINCT ON (ts_code)
                            ts_code, end_date, pledge_count, pledge_ratio
                        FROM pledge_stat
                        WHERE ts_code IN ({placeholders})
                          AND end_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ON (ts_code)
                        ts_code, end_date, pledge_count, pledge_ratio
                    FROM pledge_stat
                    WHERE ts_code IN ({placeholders})
                    ORDER BY ts_code, end_date DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get pledge batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_fina_forecast_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                return await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT DISTINCT ON (ts_code)
                            ts_code, end_date, ann_date, type,
                            p_change_min, p_change_max,
                            net_profit_min, net_profit_max
                        FROM fina_forecast
                        WHERE ts_code IN ({placeholders})
                          AND ann_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC, ann_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ON (ts_code)
                        ts_code, end_date, ann_date, type,
                        p_change_min, p_change_max,
                        net_profit_min, net_profit_max
                    FROM fina_forecast
                    WHERE ts_code IN ({placeholders})
                    ORDER BY ts_code, end_date DESC, ann_date DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get forecast batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_fina_mainbz(self, ts_code: str, as_of_date=None) -> pd.DataFrame:
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                    FROM fina_mainbz
                    WHERE ts_code = $1 AND ann_date <= $2
                    ORDER BY end_date DESC, bz_sales DESC
                    LIMIT 10
                    """,
                    (ts_code, as_of_date),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                    FROM fina_mainbz
                    WHERE ts_code = $1
                    ORDER BY end_date DESC, bz_sales DESC
                    LIMIT 10
                    """,
                    (ts_code,),
                )
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[FinancialDao] Failed to get fina_mainbz for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()

    async def get_fina_mainbz_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                df = await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT ts_code, end_date, ann_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                        FROM (
                            SELECT *, DENSE_RANK() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as dr
                            FROM fina_mainbz
                            WHERE ts_code IN ({placeholders}) AND ann_date <= ${start_idx + chunk_len}
                        ) sub
                        WHERE sub.dr = 1
                        ORDER BY ts_code, bz_sales DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                df = await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT ts_code, end_date, ann_date, bz_item, bz_sales, bz_profit, bz_cost, curr_type
                    FROM (
                        SELECT *, DENSE_RANK() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as dr
                        FROM fina_mainbz
                        WHERE ts_code IN ({placeholders})
                    ) sub
                    WHERE sub.dr = 1
                    ORDER BY ts_code, bz_sales DESC
                    """,
                    ts_codes,
                )
            if df is not None and not df.empty:
                if "dr" in df.columns:
                    df = df.drop(columns=["dr"])
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[FinancialDao] Failed to get fina_mainbz batch: %s", DataSanitizer.sanitize_error(e))
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
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as exc:
                logger.debug("[FinancialDao] fina_audit count query failed: %s", DataSanitizer.sanitize_error(exc))
                result["tables"]["fina_audit"] = 0

        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
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
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[FinancialDao] Failed to get incomplete financial stocks: %s", DataSanitizer.sanitize_error(e)
            )
            return set()
