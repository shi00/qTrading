import functools
import logging

import pandas as pd

from data.persistence.models import ScreeningHistory, get_model_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class ScreenerDao(BaseDao):
    @functools.cached_property
    def SH_BASE_COLS(self):
        """Dynamically generate base columns excluding heavy fields like 'thinking'."""
        cols = get_model_columns(ScreeningHistory, exclude={"updated_at", "created_at", "thinking"})
        return ", ".join(cols)

    @functools.cached_property
    def SH_FULL_COLS(self):
        """Full columns including 'thinking' for detail views."""
        return f"{self.SH_BASE_COLS}, thinking"

    # --- Screening History ---

    async def get_screening_history(self, strategy_name: str | None = None, limit: int | None = 100):
        sql = f"SELECT {self.SH_BASE_COLS} FROM screening_history WHERE 1=1"
        p = []
        idx = 1
        if strategy_name:
            sql += f" AND strategy_name=${idx}"
            p.append(strategy_name)
            idx += 1
        sql += f" ORDER BY trade_date DESC LIMIT ${idx}"
        p.append(limit)
        return await self._read_db(sql, p)

    async def get_history_tree(self, offset: int = 0, limit: int | None = 30):
        """
        Get aggregated tree data for the history sidebar.
        Returns rows of (trade_date, strategy_name, cnt) grouped and ordered by date DESC.
        Python caller should further group by trade_date to build the tree structure.
        """
        sql = """
            SELECT trade_date, strategy_name, COUNT(*) as cnt
            FROM screening_history
            GROUP BY trade_date, strategy_name
            ORDER BY trade_date DESC
            LIMIT $1 OFFSET $2
        """
        return await self._read_db(
            sql,
            ((limit or 30) * 5, offset),
        )  # limit*5 to cover multiple strategies per date

    async def get_history_records(self, trade_date: str | None, strategy_name: str | None = None):
        """
        Get screening records for a specific date, optionally filtered by strategy.
        """
        sql = f"SELECT {self.SH_FULL_COLS} FROM screening_history WHERE trade_date = $1"
        p = [trade_date]
        if strategy_name:
            sql += " AND strategy_name = $2"
            p.append(strategy_name)
        sql += " ORDER BY ai_score DESC"
        return await self._read_db(sql, p)

    async def get_pending_reviews(self):
        sql = f"SELECT {self.SH_BASE_COLS} FROM screening_history WHERE t1_price IS NULL OR t5_price IS NULL ORDER BY created_at DESC LIMIT 500"
        df = await self._read_db(sql)
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    async def update_screening_performance(self, updates: dict):
        # updates = list of tuples (t1_price, t1_pct, t5_price, t5_pct, id)
        if not updates:
            return
        sql = "UPDATE screening_history SET t1_price = $1, t1_pct = $2, t5_price = $3, t5_pct = $4 WHERE id = $5"
        await self._write_db(sql, updates, is_many=True)

    async def get_learning_examples(self, limit: int | None = 3):

        sql_win = f"SELECT {self.SH_BASE_COLS} FROM screening_history WHERE prediction_result='WIN' ORDER BY t1_pct DESC LIMIT $1"
        sql_loss = f"SELECT {self.SH_BASE_COLS} FROM screening_history WHERE prediction_result='LOSS' ORDER BY t1_pct ASC LIMIT $1"

        wins = await self._read_db(sql_win, (limit,))
        losses = await self._read_db(sql_loss, (limit,))

        return wins, losses

    # --- Internal: Resolve latest trade date from DB (Defense in Depth) ---
    async def _get_latest_closed_trade_date(self) -> str:
        """DAO self-resolves the latest closed trade date from daily_quotes.
        This ensures the security boundary is fully encapsulated within the DAO,
        preventing callers from accidentally injecting future dates."""
        df = await self._read_db("SELECT MAX(trade_date) as max_td FROM daily_quotes")
        if df is not None and not df.empty:
            return df["max_td"].iloc[0]
        return None  # type: ignore

    # --- Screening Data Fetch for Logic ---
    async def get_screening_data(self, trade_date: str | None = None):
        if not trade_date:
            trade_date = await self._get_latest_closed_trade_date()

        sql = """
              SELECT b.ts_code,
                     b.name,
                     b.industry,
                     b.list_date,
                     b.list_status,
                     q.trade_date,
                     q.close,
                     q.pct_chg,
                     q.vol,
                     q.amount,
                     i.pe_ttm,
                     i.pb,
                     i.ps_ttm,
                     i.dv_ttm,
                     i.total_mv,
                     i.circ_mv,
                     i.turnover_rate,
                     f.roe,
                     f.grossprofit_margin,
                     f.debt_to_assets,
                     f.or_yoy,
                     f.netprofit_yoy
               FROM stock_basic b
                        LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = $1
                        LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = $2
                        LEFT JOIN (SELECT f_inner.ts_code,
                                          f_inner.roe,
                                          f_inner.grossprofit_margin,
                                          f_inner.debt_to_assets,
                                          f_inner.or_yoy,
                                          f_inner.netprofit_yoy
                                   FROM (SELECT ts_code,
                                                roe,
                                                grossprofit_margin,
                                                debt_to_assets,
                                                or_yoy,
                                                netprofit_yoy,
                                                ROW_NUMBER() OVER (
                                                    PARTITION BY ts_code
                                                    ORDER BY ann_date DESC, end_date DESC
                                                ) AS rn
                                         FROM financial_reports
                                         WHERE ann_date <= $3) f_inner
                                   WHERE f_inner.rn = 1) f
                                  ON b.ts_code = f.ts_code
               WHERE q.close IS NOT NULL \
              """
        return await self._read_db(sql, (trade_date, trade_date, trade_date))

    # --- Review Manager Methods (P2-S3 Abstracting raw SQL) ---

    async def get_pending_predictions(self, date_threshold: str):
        """Get predictions that have no result yet since the date_threshold."""
        sql = """
            SELECT id, trade_date, ts_code, ai_score, ai_reason
            FROM screening_history
            WHERE trade_date >= $1
              AND prediction_result IS NULL
              AND ai_score > 0
            ORDER BY trade_date DESC
        """
        df = await self._read_db(sql, (date_threshold,))
        return df if df is not None else pd.DataFrame()

    async def get_learning_context(self, limit: int = 3, is_win: bool = True):
        """Extract 'Best Wins' or 'Worst Losses' for Learning Context."""
        label = "WIN" if is_win else "LOSS"
        order = "DESC" if is_win else "ASC"

        sql = f"""
            SELECT ts_code, name, t1_pct, ai_score, ai_reason
            FROM screening_history
            WHERE prediction_result = $1 AND t1_pct IS NOT NULL
            ORDER BY t1_pct {order}
            LIMIT $2
        """
        df = await self._read_db(sql, (label, limit))
        return df if df is not None else pd.DataFrame()

    async def update_prediction_result(self, record_id: int, pct: float, label: str):
        """Update DB with T+1 result."""
        sql = 'UPDATE screening_history SET "t1_pct"=$1, "prediction_result"=$2 WHERE "id"=$3'
        await self._write_db(sql, (pct, label, record_id), is_many=False)

    async def save_screening_results(self, records: list):
        """
        Save screening results to history using BaseDao UPSERT.
        Columns are derived dynamically from the ScreeningHistory ORM model.
        """
        if not records:
            return

        all_cols = get_model_columns(
            ScreeningHistory,
            exclude={"id", "updated_at", "created_at", "t1_price", "t1_pct", "t5_price", "t5_pct", "prediction_result"},
        )

        dict_records = [dict(zip(all_cols, r, strict=True)) for r in records]
        df = pd.DataFrame(dict_records)

        await self._save_upsert(
            df=df,
            table_name="screening_history",
            columns=all_cols,
            pk_columns=["trade_date", "strategy_name", "ts_code"],
        )
