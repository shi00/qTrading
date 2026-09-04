import datetime
import functools
import logging
import typing

import pandas as pd
import sqlalchemy as sa

from data.constants import REVIEW_STATUS_COMPLETED, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE
from data.persistence.models import Base, ScreeningHistory, get_model_columns
from data.sync.base import safe_error
from utils.log_decorators import PerfThreshold, log_async_operation

from .base_dao import BaseDao, EngineDisposedError
from .stock_dao import stock_alive_condition

logger = logging.getLogger(__name__)

# DAT-10: 区间预载日线选股查询的行数护栏，防止大区间一次性拉取致 OOM。
# 超出后由 _read_db 抛 ValueError，BacktestDataProvider.preload_range 捕获并降级逐日查询。
_MAX_SCREENING_RANGE_ROWS = 1_500_000

# _LEARNING_CONTEXT_BASE_SQL removed - refactored to SQLAlchemy Core


# review03-C7: 单日/区间选股 SQL 静态模板。__CLOSE_COND__ 与 __STOCK_ALIVE_CONDITION__
# 为唯一可变点，分别由 _build_screening_sql/_build_screening_sql_range 按 require_close
# 布尔与 PIT 时点（DAT-01）替换为模块受控片段（无用户输入），避免 f-string 拼 SQL 模式。
# __STOCK_ALIVE_CONDITION__ 必须经 stock_alive_condition() 渲染（唯一正本），禁止内联复制。
_SCREENING_SQL_TEMPLATE = """
              SELECT b.ts_code,
                     b.name,
                     m.sw_l2_name AS industry_sw_l2,
                     b.industry AS industry_tushare,
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
                                                    ORDER BY end_date DESC, ann_date DESC  -- DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC
                                                ) AS rn
                                         FROM financial_reports
                                         WHERE ann_date IS NOT NULL AND ann_date <= $3) f_inner
                                   WHERE f_inner.rn = 1) f
                                  ON b.ts_code = f.ts_code
                        LEFT JOIN LATERAL (
                            SELECT sw_l2_name
                            FROM sw_industry_member
                            WHERE ts_code = b.ts_code
                              AND sw_l2_name IS NOT NULL AND sw_l2_name <> ''
                            ORDER BY index_code  -- DAT-08: 确定性排序，主键 (ts_code, index_code) 取最小 index_code，防 LIMIT 1 随执行计划漂移
                            LIMIT 1
                        ) m ON TRUE
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = $6
               WHERE __CLOSE_COND__b.list_date <= $4
                 AND __STOCK_ALIVE_CONDITION__
              """


_SCREENING_SQL_RANGE_TEMPLATE = """
              SELECT b.ts_code,
                     b.name,
                     m.sw_l2_name AS industry_sw_l2,
                     b.industry AS industry_tushare,
                     b.list_date,
                     b.list_status,
                     cal.cal_date AS trade_date,
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
               FROM (
                   SELECT cal_date
                   FROM trade_cal
                   WHERE is_open = 1
                     AND cal_date >= $1
                     AND cal_date <= $2
               ) cal
                        CROSS JOIN stock_basic b
                        LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = cal.cal_date
                        LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = cal.cal_date
                        LEFT JOIN LATERAL (
                            SELECT f_inner.roe,
                                   f_inner.grossprofit_margin,
                                   f_inner.debt_to_assets,
                                   f_inner.or_yoy,
                                   f_inner.netprofit_yoy
                            FROM financial_reports f_inner
                            WHERE f_inner.ts_code = b.ts_code
                              AND f_inner.ann_date IS NOT NULL
                              AND f_inner.ann_date <= cal.cal_date
                            ORDER BY f_inner.end_date DESC, f_inner.ann_date DESC  -- DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC
                            LIMIT 1
                        ) f ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT sw_l2_name
                            FROM sw_industry_member
                            WHERE ts_code = b.ts_code
                              AND sw_l2_name IS NOT NULL AND sw_l2_name <> ''
                            ORDER BY index_code  -- DAT-08: 确定性排序，主键 (ts_code, index_code) 取最小 index_code，防 LIMIT 1 随执行计划漂移
                            LIMIT 1
                        ) m ON TRUE
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = cal.cal_date
               WHERE __CLOSE_COND__b.list_date <= cal.cal_date
                 AND __STOCK_ALIVE_CONDITION__
              """


class ScreenerDao(BaseDao):
    @functools.cached_property
    def SH_BASE_COLS(self):
        # SH_BASE_COLS/SH_FULL_COLS 为历史遗留的列名常量，保留供外部引用与
        # integration 测试断言（review03-C7：消除 f-string 拼 SQL 模式）。
        # 简单查询已迁移到 SQLAlchemy Core（_read_db_select），见 _base_cols()。
        cols = [
            c.name
            for c in ScreeningHistory.__table__.columns
            if c.name not in {"updated_at", "created_at", "params_snapshot"}
        ]
        return ", ".join("sh." + c for c in cols)

    @functools.cached_property
    def SH_FULL_COLS(self):
        return self.SH_BASE_COLS + ", st.thinking, sh.params_snapshot"

    @staticmethod
    def _base_cols() -> list:
        """ScreeningHistory 基础列（排除审计列），供 SQLAlchemy Core SELECT 使用。"""
        return [
            c
            for c in ScreeningHistory.__table__.columns
            if c.name not in {"updated_at", "created_at", "params_snapshot"}
        ]

    # --- Screening History ---

    async def get_screening_history(self, strategy_name: str | None = None, limit: int | None = 100):
        t = ScreeningHistory.__table__
        stmt = sa.select(*self._base_cols()).select_from(t)
        if strategy_name:
            stmt = stmt.where(t.c.strategy_name == strategy_name)
        stmt = stmt.order_by(t.c.trade_date.desc()).limit(limit)
        return await self._read_db_select(stmt)

    async def get_history_tree(self, offset: int = 0, limit: int | None = 30):
        effective_limit = limit or 30
        sql = """
            SELECT run_id, trade_date, strategy_name, COUNT(*) as cnt
            FROM screening_history
            WHERE trade_date >= CURRENT_DATE - INTERVAL '180 days'
            GROUP BY run_id, trade_date, strategy_name
            ORDER BY trade_date DESC, MIN(created_at) DESC
            LIMIT $1 OFFSET $2
        """
        return await self._read_db(
            sql,
            (effective_limit, offset),
        )

    async def get_history_records(
        self, trade_date: str | None, strategy_name: str | None = None, run_id: str | None = None
    ):
        sh = ScreeningHistory.__table__
        st = Base.metadata.tables["screening_thinking"]
        stmt = (
            sa.select(*self._base_cols(), st.c.thinking, sh.c.params_snapshot)
            .select_from(sh)
            .join(st, sh.c.id == st.c.history_id, isouter=True)
        )
        if run_id:
            stmt = stmt.where(sh.c.run_id == run_id)
        else:
            stmt = stmt.where(sh.c.trade_date == trade_date)
            if strategy_name:
                stmt = stmt.where(sh.c.strategy_name == strategy_name)
        stmt = stmt.order_by(sh.c.ai_score.desc())
        return await self._read_db_select(stmt)

    async def get_pending_reviews(self):
        t = ScreeningHistory.__table__
        stmt = (
            sa.select(*self._base_cols())
            .select_from(t)
            .where(
                sa.or_(
                    t.c.review_status.in_([REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE]),
                    t.c.review_status.is_(None),
                ),
                t.c.trade_date >= sa.func.current_date() - sa.text("INTERVAL '90 days'"),
            )
            .order_by(t.c.created_at.desc())
            .limit(500)
        )
        df = await self._read_db_select(stmt)
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    async def get_learning_examples(self, limit: int | None = 3):
        t = ScreeningHistory.__table__
        base = self._base_cols()
        wins = await self._read_db_select(
            sa.select(*base)
            .select_from(t)
            .where(t.c.prediction_result == "WIN", t.c.alpha.isnot(None))
            .order_by(t.c.alpha.desc(), t.c.t1_pct.desc())
            .limit(limit)
        )
        losses = await self._read_db_select(
            sa.select(*base)
            .select_from(t)
            .where(t.c.prediction_result == "LOSS", t.c.alpha.isnot(None))
            .order_by(t.c.alpha.asc(), t.c.t1_pct.asc())
            .limit(limit)
        )
        return wins, losses

    # --- Internal: Resolve latest trade date from DB (Defense in Depth) ---
    async def _get_latest_closed_trade_date(self) -> str:
        df = await self._read_db("SELECT MAX(trade_date) as max_td FROM daily_quotes")
        if df is not None and not df.empty:
            val = df["max_td"].iloc[0]
            if val is not None and not (isinstance(val, float) and val != val):
                return str(val)
        return None  # type: ignore[untyped]

    # --- Screening Data Fetch for Logic ---
    def _build_screening_sql(self, *, require_close: bool = True) -> str:
        # review03-C7: SQL 模板为模块级静态常量，__CLOSE_COND__ 仅由 require_close
        # 布尔决定两种受控片段（空串 / "q.close IS NOT NULL...\nAND "），无用户输入，
        # 避免 f-string 拼 SQL 模式（列名亦来自 ORM 元数据，非拼接点）。
        # DAT-01: __STOCK_ALIVE_CONDITION__ 由 stock_alive_condition() 唯一正本渲染。
        close_clause = "q.close IS NOT NULL\n                 AND " if require_close else ""
        sql = _SCREENING_SQL_TEMPLATE.replace("__CLOSE_COND__", close_clause)
        return sql.replace("__STOCK_ALIVE_CONDITION__", stock_alive_condition(alias="b.", as_of="$5"))

    async def get_screening_data(self, trade_date: str | None = None):
        if not trade_date:
            trade_date = await self._get_latest_closed_trade_date()
        if not trade_date:
            logger.warning("[ScreenerDao] No trade_date available for screening data query")
            return pd.DataFrame()
        sql = self._build_screening_sql(require_close=True)
        return await self._read_db(sql, (trade_date,) * 6)

    async def get_fundamental_screening_data(self, trade_date: str | None = None):
        if not trade_date:
            trade_date = await self._get_latest_closed_trade_date()
        if not trade_date:
            logger.warning("[ScreenerDao] No trade_date available for fundamental screening data query")
            return pd.DataFrame()
        sql = self._build_screening_sql(require_close=False)
        return await self._read_db(sql, (trade_date,) * 6)

    def _build_screening_sql_range(self, *, require_close: bool = True) -> str:
        # review03-C7: 同 _build_screening_sql，__CLOSE_COND__ 仅由 require_close
        # 布尔决定两种受控片段，避免 f-string 拼 SQL 模式。
        # DAT-01: __STOCK_ALIVE_CONDITION__ 由 stock_alive_condition() 唯一正本渲染。
        close_clause = "q.close IS NOT NULL AND " if require_close else ""
        sql = _SCREENING_SQL_RANGE_TEMPLATE.replace("__CLOSE_COND__", close_clause)
        return sql.replace(
            "__STOCK_ALIVE_CONDITION__",
            stock_alive_condition(alias="b.", as_of="cal.cal_date"),
        )

    async def get_screening_data_range(self, start_date: str, end_date: str):
        sql = self._build_screening_sql_range(require_close=True)
        # DAT-10: 区间预载携带行数护栏，超限抛 ValueError 由上游降级逐日查询
        return await self._read_db(sql, (start_date, end_date), max_rows=_MAX_SCREENING_RANGE_ROWS)

    async def get_fundamental_screening_data_range(self, start_date: str, end_date: str):
        sql = self._build_screening_sql_range(require_close=False)
        # DAT-10: 区间预载携带行数护栏（与日线区间一致）
        return await self._read_db(sql, (start_date, end_date), max_rows=_MAX_SCREENING_RANGE_ROWS)

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

    async def get_learning_context(
        self,
        limit: int = 3,
        is_win: bool = True,
        as_of: datetime.date | datetime.datetime | None = None,
    ):
        label = "WIN" if is_win else "LOSS"
        t = Base.metadata.tables["screening_history"]
        order_dir = sa.desc if is_win else sa.asc
        stmt = sa.select(
            t.c.ts_code,
            t.c.name,
            t.c.alpha,
            t.c.t1_pct,
            t.c.t5_pct,
            t.c.ai_score,
            t.c.ai_reason,
        ).where(
            t.c.prediction_result == label,
            t.c.alpha.isnot(None),
            t.c.t5_pct.isnot(None),
            t.c.review_status == REVIEW_STATUS_COMPLETED,
        )
        if as_of is not None:
            if isinstance(as_of, datetime.datetime):
                as_of = as_of.date()
            stmt = stmt.where(t.c.trade_date < as_of)
        stmt = stmt.order_by(order_dir(t.c.alpha), order_dir(t.c.t1_pct)).limit(limit)
        df = await self._read_db_select(stmt)
        return df if df is not None else pd.DataFrame()

    @log_async_operation(
        operation_name="ScreenerDao.update_prediction_result",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def update_prediction_result(
        self,
        record_id: int,
        pct: float,
        label: str,
        *,
        t1_price: float | None = None,
        t5_pct: float | None = None,
        t5_price: float | None = None,
        index_pct: float | None = None,
        alpha: float | None = None,
        review_status: str | None = None,
        conn: typing.Any = None,
    ):
        """Update review metrics and advance review_status according to available horizons."""
        self._check_engine()
        effective_status = review_status
        if effective_status is None:
            effective_status = REVIEW_STATUS_COMPLETED if t5_pct is not None else REVIEW_STATUS_T1_DONE

        table = Base.metadata.tables.get("screening_history")
        if table is None:
            logger.error("[ScreenerDao] Table screening_history not found in SQLAlchemy metadata.")
            return

        stmt = (
            sa.update(table)
            .where(table.c.id == record_id)
            .values(
                t1_pct=pct,
                prediction_result=label,
                t1_price=t1_price,
                t5_pct=t5_pct,
                t5_price=t5_price,
                index_pct=index_pct,
                alpha=alpha,
                review_status=effective_status,
            )
        )

        # DAT-01: 与 base_dao 一致，维护事件放行后复查引擎，防范 conn 路径 TOCTOU
        # （conn 由裸 engine.begin() 提供，无 _guarded_begin 守卫，须在此复查）
        await self._wait_maintenance_guard(context="update_prediction_result")
        if conn is not None:
            await conn.execute(stmt)
        else:
            try:
                async with self._guarded_begin() as tx_conn:
                    await tx_conn.execute(stmt)
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.warning("[ScreenerDao] Failed to update prediction result: %s", safe_error(e))

    async def save_screening_results(self, records: list[dict | tuple]):
        if not records:
            return

        # computed 列（t1_price, alpha, prediction_result 等）由 get_model_columns 自动排除；
        # review_status 不在 exclude 中，下方显式设置为 PENDING。
        all_cols = get_model_columns(
            ScreeningHistory,
            exclude={"id", "updated_at", "created_at"},
        )

        enriched_records = []
        thinking_records = []
        for r in records:
            if isinstance(r, dict):
                row = dict(r)
            else:
                row = dict(zip(all_cols, r, strict=False))
            thinking_text = row.pop("thinking", "")
            row["review_status"] = REVIEW_STATUS_PENDING
            enriched_records.append(tuple(row.get(c) for c in all_cols))
            if thinking_text:
                thinking_records.append(
                    {"run_id": row.get("run_id"), "ts_code": row.get("ts_code"), "thinking": str(thinking_text)}
                )

        df = pd.DataFrame(enriched_records, columns=all_cols)

        await self._save_upsert(
            df=df,
            table_name="screening_history",
            columns=all_cols,
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
