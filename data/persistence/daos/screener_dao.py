import functools
import logging

import pandas as pd

from data.constants import REVIEW_STATUS_COMPLETED, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE
from data.persistence.models import ScreeningHistory, get_model_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class ScreenerDao(BaseDao):
    @functools.cached_property
    def SH_BASE_COLS(self):
        cols = get_model_columns(ScreeningHistory, exclude={"updated_at", "created_at", "params_snapshot"})
        return ", ".join(f"sh.{c}" for c in cols)

    @functools.cached_property
    def SH_FULL_COLS(self):
        return f"{self.SH_BASE_COLS}, st.thinking, sh.params_snapshot"

    # --- Screening History ---

    async def get_screening_history(self, strategy_name: str | None = None, limit: int | None = 100):
        sql = f"SELECT {self.SH_BASE_COLS} FROM screening_history sh WHERE 1=1"
        p = []
        idx = 1
        if strategy_name:
            sql += f" AND sh.strategy_name=${idx}"
            p.append(strategy_name)
            idx += 1
        sql += f" ORDER BY sh.trade_date DESC LIMIT ${idx}"
        p.append(limit)
        return await self._read_db(sql, p)

    async def get_history_tree(self, offset: int = 0, limit: int | None = 30):
        sql = """
            SELECT run_id, trade_date, strategy_name, COUNT(*) as cnt
            FROM screening_history
            GROUP BY run_id, trade_date, strategy_name
            ORDER BY trade_date DESC, MIN(created_at) DESC
            LIMIT $1 OFFSET $2
        """
        return await self._read_db(
            sql,
            ((limit or 30) * 5, offset),
        )

    async def get_history_records(
        self, trade_date: str | None, strategy_name: str | None = None, run_id: str | None = None
    ):
        if run_id:
            sql = (
                f"SELECT {self.SH_FULL_COLS} FROM screening_history sh"
                f" LEFT JOIN screening_thinking st ON sh.id = st.history_id"
                f" WHERE sh.run_id = $1 ORDER BY sh.ai_score DESC"
            )
            return await self._read_db(sql, (run_id,))

        sql = (
            f"SELECT {self.SH_FULL_COLS} FROM screening_history sh"
            f" LEFT JOIN screening_thinking st ON sh.id = st.history_id"
            f" WHERE sh.trade_date = $1"
        )
        p = [trade_date]
        if strategy_name:
            sql += " AND sh.strategy_name = $2"
            p.append(strategy_name)
        sql += " ORDER BY sh.ai_score DESC"
        return await self._read_db(sql, p)

    async def get_pending_reviews(self):
        sql = f"""
            SELECT {self.SH_BASE_COLS} FROM screening_history sh
            WHERE sh.review_status IN ($1, $2) OR sh.review_status IS NULL
            ORDER BY sh.created_at DESC LIMIT 500
        """
        df = await self._read_db(sql, (REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE))
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    async def get_learning_examples(self, limit: int | None = 3):
        sql_win = f"""
            SELECT {self.SH_BASE_COLS}
            FROM screening_history sh
            WHERE sh.prediction_result='WIN' AND sh.alpha IS NOT NULL
            ORDER BY sh.alpha DESC, sh.t1_pct DESC
            LIMIT $1
        """
        sql_loss = f"""
            SELECT {self.SH_BASE_COLS}
            FROM screening_history sh
            WHERE sh.prediction_result='LOSS' AND sh.alpha IS NOT NULL
            ORDER BY sh.alpha ASC, sh.t1_pct ASC
            LIMIT $1
        """

        wins = await self._read_db(sql_win, (limit,))
        losses = await self._read_db(sql_loss, (limit,))

        return wins, losses

    # --- Internal: Resolve latest trade date from DB (Defense in Depth) ---
    async def _get_latest_closed_trade_date(self) -> str:
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
                     f.netprofit_yoy,
                     CASE WHEN s.ts_code IS NOT NULL THEN FALSE ELSE TRUE END AS is_tradable
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
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = $6
               WHERE q.close IS NOT NULL
                 AND b.list_status = 'L'
                 AND b.list_date <= $4
                 AND (b.delist_date IS NULL OR b.delist_date > $5)
              """
        return await self._read_db(sql, (trade_date, trade_date, trade_date, trade_date, trade_date, trade_date))

    async def get_fundamental_screening_data(self, trade_date: str | None = None):
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
                     f.netprofit_yoy,
                     CASE WHEN s.ts_code IS NOT NULL THEN FALSE ELSE TRUE END AS is_tradable
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
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = $6
               WHERE b.list_status = 'L'
                 AND b.list_date <= $4
                 AND (b.delist_date IS NULL OR b.delist_date > $5)
              """
        return await self._read_db(sql, (trade_date, trade_date, trade_date, trade_date, trade_date, trade_date))

    # --- Review Manager Methods ---

    async def get_pending_predictions(self, date_threshold: str):
        """Get predictions that have no result yet since the date_threshold."""
        sql = """
            SELECT id, trade_date, ts_code, ai_score, ai_reason
            FROM screening_history
            WHERE trade_date >= $1
              AND (review_status IN ($2, $3) OR review_status IS NULL)
              AND ai_score > 0
            ORDER BY trade_date DESC
        """
        df = await self._read_db(sql, (date_threshold, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE))
        return df if df is not None else pd.DataFrame()

    async def get_learning_context(self, limit: int = 3, is_win: bool = True):
        label = "WIN" if is_win else "LOSS"
        order = "DESC" if is_win else "ASC"

        sql = f"""
            SELECT ts_code, name, alpha, t1_pct, t5_pct, ai_score, ai_reason
            FROM screening_history
            WHERE prediction_result = $1 AND alpha IS NOT NULL
            ORDER BY alpha {order}, t1_pct {order}
            LIMIT $2
        """
        df = await self._read_db(sql, (label, limit))
        return df if df is not None else pd.DataFrame()

    async def update_prediction_result(
        self,
        record_id: int,
        pct: float,
        label: str,
        t1_price: float | None = None,
        *,
        t5_pct: float | None = None,
        t5_price: float | None = None,
        index_pct: float | None = None,
        alpha: float | None = None,
    ):
        """Update review metrics and advance review_status according to available horizons."""
        review_status = REVIEW_STATUS_COMPLETED if t5_pct is not None else REVIEW_STATUS_T1_DONE
        sql = """
            UPDATE screening_history
            SET "t1_pct"=$1,
                "prediction_result"=$2,
                "t1_price"=$3,
                "t5_pct"=$4,
                "t5_price"=$5,
                "index_pct"=$6,
                "alpha"=$7,
                "review_status"=$8
            WHERE "id"=$9
        """
        await self._write_db(
            sql,
            (pct, label, t1_price, t5_pct, t5_price, index_pct, alpha, review_status, record_id),
            is_many=False,
        )

    async def save_screening_results(self, records: list[dict | tuple]):
        if not records:
            return

        all_cols = get_model_columns(
            ScreeningHistory,
            exclude={
                "id",
                "updated_at",
                "created_at",
                "t1_price",
                "t1_pct",
                "t5_price",
                "t5_pct",
                "index_pct",
                "alpha",
                "prediction_result",
                "review_status",
            },
        )

        all_cols_with_review = all_cols + ["review_status"]

        enriched_records = []
        thinking_records = []
        for r in records:
            if isinstance(r, dict):
                row = dict(r)
            else:
                row = dict(zip(all_cols, r, strict=False))
            thinking_text = row.pop("thinking", "")
            row["review_status"] = REVIEW_STATUS_PENDING
            enriched_records.append(tuple(row.get(c) for c in all_cols_with_review))
            if thinking_text:
                thinking_records.append(
                    {"run_id": row.get("run_id"), "ts_code": row.get("ts_code"), "thinking": str(thinking_text)}
                )

        df = pd.DataFrame(enriched_records, columns=all_cols_with_review)

        await self._save_upsert(
            df=df,
            table_name="screening_history",
            columns=all_cols_with_review,
            pk_columns=["run_id", "ts_code"],
        )

        if thinking_records:
            await self._save_thinking(thinking_records)

    async def _save_thinking(self, thinking_records: list[dict]):
        ids_sql = "SELECT id, run_id, ts_code FROM screening_history WHERE run_id = ANY($1)"
        run_ids = list({r["run_id"] for r in thinking_records})
        id_df = await self._read_db(ids_sql, (run_ids,))
        if id_df is None or id_df.empty:
            return
        lookup = {(row["run_id"], row["ts_code"]): row["id"] for row in id_df.to_dict("records")}
        rows = []
        for rec in thinking_records:
            history_id = lookup.get((rec["run_id"], rec["ts_code"]))
            if history_id:
                rows.append((history_id, rec["thinking"]))
        if not rows:
            return
        df = pd.DataFrame(rows, columns=["history_id", "thinking"])
        await self._save_upsert(
            df=df,
            table_name="screening_thinking",
            columns=["history_id", "thinking"],
            pk_columns=["history_id"],
        )
