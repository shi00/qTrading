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

# UX-05: 复盘聚合统计的历史窗口（天）。get_history_tree 与 get_strategy_review_stats 共用，
# 消除魔术字符串漂移（四审 L1/m1/m2）。值拼接进 SQL 的 INTERVAL，为受控模块常量、非用户输入，
# 无注入面（review03-C7 约束的是用户输入可变点）。
REVIEW_STATS_WINDOW_DAYS = 180

# _LEARNING_CONTEXT_BASE_SQL removed - refactored to SQLAlchemy Core


# review03-C7: 单日/区间选股 SQL 静态模板。__CLOSE_COND__ 与 __STOCK_ALIVE_CONDITION__
# 为唯一可变点，分别由 _build_screening_sql/_build_screening_sql_range 按 require_close
# 布尔与 PIT 时点（DAT-01）替换为模块受控片段（无用户输入），避免 f-string 拼 SQL 模式。
# __STOCK_ALIVE_CONDITION__ 必须经 stock_alive_condition() 渲染（唯一正本），禁止内联复制。
_SCREENING_SQL_TEMPLATE = """
              SELECT b.ts_code,
                     b.name,
                     m.l2_name AS industry_sw_l2,
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
                     f.n_income,
                     f_prev.gpm_prev,
                     CASE WHEN s.ts_code IS NOT NULL THEN FALSE ELSE TRUE END AS is_tradable
               FROM stock_basic b
                        LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = $1
                        LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = $2
                        LEFT JOIN (SELECT f_inner.ts_code,
                                          f_inner.roe,
                                          f_inner.grossprofit_margin,
                                          f_inner.debt_to_assets,
                                          f_inner.or_yoy,
                                          f_inner.netprofit_yoy,
                                          f_inner.n_income
                                   FROM (SELECT ts_code,
                                                roe,
                                                grossprofit_margin,
                                                debt_to_assets,
                                                or_yoy,
                                                netprofit_yoy,
                                                n_income,
                                                ROW_NUMBER() OVER (
                                                    PARTITION BY ts_code
                                                    ORDER BY end_date DESC, ann_date DESC  -- DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC
                                                ) AS rn
                                         FROM financial_reports
                                         WHERE ann_date IS NOT NULL AND ann_date <= $3) f_inner
                                   WHERE f_inner.rn = 1) f
                                  ON b.ts_code = f.ts_code
                        LEFT JOIN (SELECT g.ts_code,
                                          g.grossprofit_margin AS gpm_prev
                                   FROM (SELECT f2.ts_code,
                                                f2.grossprofit_margin,
                                                ROW_NUMBER() OVER (
                                                    PARTITION BY f2.ts_code
                                                    ORDER BY f2.end_date DESC
                                                ) AS pr
                                         FROM (SELECT fr.ts_code,
                                                      fr.grossprofit_margin,
                                                      fr.end_date,
                                                      ROW_NUMBER() OVER (
                                                          PARTITION BY fr.ts_code, fr.end_date
                                                          ORDER BY fr.ann_date DESC
                                                      ) AS rn_period
                                               FROM financial_reports fr
                                               WHERE fr.ann_date IS NOT NULL AND fr.ann_date <= $3) f2
                                         WHERE f2.rn_period = 1) g
                                   WHERE g.pr = 2) f_prev
                                  ON b.ts_code = f_prev.ts_code
                        LEFT JOIN LATERAL (
                            SELECT l2_name
                            FROM sw_industry_member
                            WHERE ts_code = b.ts_code
                              AND in_date <= $5
                              AND (out_date IS NULL OR out_date > $5)
                              AND l2_name IS NOT NULL AND l2_name <> ''
                            ORDER BY l2_code  -- DATA-04 L2: 按 as-of 时点（$5）过滤，取该时点有效行的最小 l2_code，防 LIMIT 1 随执行计划漂移
                            LIMIT 1
                        ) m ON TRUE
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = $6
               WHERE __CLOSE_COND__b.list_date <= $4
                 AND __STOCK_ALIVE_CONDITION__
              """


_SCREENING_SQL_RANGE_TEMPLATE = """
              SELECT b.ts_code,
                     b.name,
                     m.l2_name AS industry_sw_l2,
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
                     f.n_income,
                     f_prev.gpm_prev,
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
                                   f_inner.netprofit_yoy,
                                   f_inner.n_income
                            FROM financial_reports f_inner
                            WHERE f_inner.ts_code = b.ts_code
                              AND f_inner.ann_date IS NOT NULL
                              AND f_inner.ann_date <= cal.cal_date
                            ORDER BY f_inner.end_date DESC, f_inner.ann_date DESC  -- DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC
                            LIMIT 1
                        ) f ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT g.grossprofit_margin AS gpm_prev
                            FROM (
                                SELECT f2.grossprofit_margin,
                                       ROW_NUMBER() OVER (
                                           ORDER BY f2.end_date DESC
                                       ) AS pr
                                FROM (
                                    SELECT fr.grossprofit_margin,
                                           fr.end_date,
                                           ROW_NUMBER() OVER (
                                               PARTITION BY fr.end_date
                                               ORDER BY fr.ann_date DESC
                                           ) AS rn_period
                                    FROM financial_reports fr
                                    WHERE fr.ts_code = b.ts_code
                                      AND fr.ann_date IS NOT NULL
                                      AND fr.ann_date <= cal.cal_date
                                ) f2
                                WHERE f2.rn_period = 1
                            ) g
                            WHERE g.pr = 2
                        ) f_prev ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT l2_name
                            FROM sw_industry_member
                            WHERE ts_code = b.ts_code
                              AND in_date <= cal.cal_date
                              AND (out_date IS NULL OR out_date > cal.cal_date)
                              AND l2_name IS NOT NULL AND l2_name <> ''
                            ORDER BY l2_code  -- DATA-04 L2: 按 as-of 时点（cal.cal_date）过滤，取该时点有效行的最小 l2_code，防 LIMIT 1 随执行计划漂移
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
        # LIFE-03: 覆盖语义下同 (trade_date, strategy_name, ts_code) 仅保留最新快照，
        # 历史树按 (trade_date, strategy_name) 聚合该日该策略当前股票集（COUNT(*) = 股票数）。
        # run_id 取组内字典序最大值的 uuid 作为展示代表值（非严格"最新"，仅作就地展示；
        # 点击按 trade_date+strategy_name 载入，不依赖 run_id 过滤）。
        sql = f"""
            SELECT trade_date, strategy_name, COUNT(*) as cnt, MAX(run_id) as run_id
            FROM screening_history
            WHERE trade_date >= CURRENT_DATE - INTERVAL '{REVIEW_STATS_WINDOW_DAYS} days'
            GROUP BY trade_date, strategy_name
            ORDER BY trade_date DESC, COUNT(*) ASC, MIN(created_at) DESC
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
        # BIZ-01: 无 AI 记录 ai_score 为 NULL，Postgres DESC 默认 NULLS FIRST 会把
        # 无分数记录顶到历史视图最前；显式 NULLS LAST 保持「有 AI 分数记录优先」语义。
        stmt = stmt.order_by(sh.c.ai_score.desc().nulls_last())
        return await self._read_db_select(stmt)

    async def get_strategy_review_stats(self) -> pd.DataFrame:
        """按 (strategy_name, benchmark_code, trade_date) 返回复盘日组合聚合统计（UX-05）。

        口径（设计 v5）：
        - 覆盖语义：同 (trade_date, strategy_name, ts_code) 仅保留最新快照
          （DISTINCT ON ... ORDER BY run_id DESC，四审 L1），消除被淘汰股票行/多运行日加权残留。
        - 指标独立 N：t1_pct / t5_pct / alpha 各以非 NULL 股票独立聚类求日组合均值与有效
          样本数（AVG/COUNT 自动忽略 NULL，四审 M4）。
        - 胜率：prediction_result 为 WIN/LOSS 的逐股计数（样例单位=股票行，跨日由消费端累计，
          与日序列 N 独立，四审 M2）。
        - NULL 基准：benchmark_code 为 NULL 的历史行自成一组（UI 归入「基准未知」组）。
        - 窗口：近 REVIEW_STATS_WINDOW_DAYS 天，与 get_history_tree 共用常量。
        全 SQLAlchemy Core（参数化 window_days），无 SQL 注入（R4）。
        """
        sh = ScreeningHistory.__table__
        latest = (
            sa.select(
                sh.c.trade_date,
                sh.c.strategy_name,
                sh.c.ts_code,
                sh.c.benchmark_code,
                sh.c.t1_pct,
                sh.c.t5_pct,
                sh.c.alpha,
                sh.c.prediction_result,
            )
            .where(
                sh.c.trade_date
                >= sa.func.current_date() - sa.bindparam("window_days", REVIEW_STATS_WINDOW_DAYS, type_=sa.INTEGER)
            )
            # DISTINCT ON (trade_date, strategy_name, ts_code) ORDER BY ... run_id DESC
            .distinct(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code)
            .order_by(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code, sh.c.run_id.desc())
            .subquery("latest_review_stats")
        )
        stmt = (
            sa.select(
                latest.c.trade_date,
                latest.c.strategy_name,
                latest.c.benchmark_code,
                sa.func.count().label("daily_cnt"),
                sa.func.avg(latest.c.t1_pct).label("t1_mean"),
                sa.func.count(latest.c.t1_pct).label("t1_n"),
                sa.func.avg(latest.c.t5_pct).label("t5_mean"),
                sa.func.count(latest.c.t5_pct).label("t5_n"),
                sa.func.avg(latest.c.alpha).label("alpha_mean"),
                sa.func.count(latest.c.alpha).label("alpha_n"),
                sa.func.count().filter(latest.c.prediction_result == "WIN").label("win_cnt"),
                sa.func.count().filter(latest.c.prediction_result == "LOSS").label("loss_cnt"),
            )
            .select_from(latest)
            .group_by(latest.c.trade_date, latest.c.strategy_name, latest.c.benchmark_code)
            .order_by(latest.c.strategy_name, latest.c.benchmark_code, latest.c.trade_date)
        )
        return await self._read_db_select(stmt)

    async def get_ai_attribution_stats(self) -> pd.DataFrame:
        """按 (strategy_name, benchmark_code, has_ai, trade_date) 返回 AI 归因日组合聚合（BIZ-04 第二层）。

        口径与 ``get_strategy_review_stats`` 对齐（设计 v5）：
        - 覆盖语义：同 (trade_date, strategy_name, ts_code) 仅保留最新快照
          （DISTINCT ON ... ORDER BY run_id DESC），消除被淘汰股票行/多运行日加权残留。
        - has_ai 分组：``ai_score IS NOT NULL`` 视为「历史上真实发生的 AI 判断」组
          （AI 启用且产生结论），NULL 为无 AI 组。代理口径局限见 ADR-0009（无 AI 组可能
          混入「AI 启用但失败/未确认」记录；组间差异含自选择偏差，相关非因果）。
        - 指标独立 N：t1_pct / t5_pct / alpha 各以非 NULL 股票独立聚类求日组合均值与有效
          样本数（AVG/COUNT 自动忽略 NULL）。
        - 胜率：prediction_result 为 WIN/LOSS 的逐股计数。
        - 窗口：近 REVIEW_STATS_WINDOW_DAYS 天。
        全 SQLAlchemy Core（参数化 window_days），无 SQL 注入（R4）。
        """
        sh = ScreeningHistory.__table__
        latest = (
            sa.select(
                sh.c.trade_date,
                sh.c.strategy_name,
                sh.c.ts_code,
                sh.c.benchmark_code,
                sh.c.ai_score,
                sh.c.t1_pct,
                sh.c.t5_pct,
                sh.c.alpha,
                sh.c.prediction_result,
            )
            .where(
                sh.c.trade_date
                >= sa.func.current_date() - sa.bindparam("window_days", REVIEW_STATS_WINDOW_DAYS, type_=sa.INTEGER)
            )
            # DISTINCT ON (trade_date, strategy_name, ts_code) ORDER BY ... run_id DESC
            .distinct(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code)
            .order_by(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code, sh.c.run_id.desc())
            .subquery("latest_ai_attribution")
        )
        has_ai = latest.c.ai_score.isnot(None).label("has_ai")
        stmt = (
            sa.select(
                latest.c.trade_date,
                latest.c.strategy_name,
                latest.c.benchmark_code,
                has_ai,
                sa.func.count().label("daily_cnt"),
                sa.func.avg(latest.c.t1_pct).label("t1_mean"),
                sa.func.count(latest.c.t1_pct).label("t1_n"),
                sa.func.avg(latest.c.t5_pct).label("t5_mean"),
                sa.func.count(latest.c.t5_pct).label("t5_n"),
                sa.func.avg(latest.c.alpha).label("alpha_mean"),
                sa.func.count(latest.c.alpha).label("alpha_n"),
                sa.func.count().filter(latest.c.prediction_result == "WIN").label("win_cnt"),
                sa.func.count().filter(latest.c.prediction_result == "LOSS").label("loss_cnt"),
            )
            .select_from(latest)
            .group_by(latest.c.trade_date, latest.c.strategy_name, latest.c.benchmark_code, has_ai)
            .order_by(latest.c.strategy_name, latest.c.benchmark_code, has_ai, latest.c.trade_date)
        )
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

    # --- Data fetch for logic: 日期形参在 DAO 边界显式转为 date（DAT-26） ---
    # 调用方（data_processor/data_provider）以 8 位 YYYYMMDD 字符串作为 context key，
    # 不能改其格式；只在 DAO 边界经 BaseDao._to_db_date 显式转成 date 对象供 _read_db 绑定。

    # --- Internal: Resolve latest trade date from DB (Defense in Depth) ---
    async def _get_latest_closed_trade_date(self) -> datetime.date | None:
        """返回最近已收盘交易日（date 对象）。DAT-26: 直接返回 date，不再 str()。"""
        df = await self._read_db("SELECT MAX(trade_date) as max_td FROM daily_quotes")
        if df is not None and not df.empty:
            val = df["max_td"].iloc[0]
            if val is not None and not (isinstance(val, float) and val != val):
                return typing.cast(datetime.date, val)
        return None

    # --- Screening Data Fetch for Logic ---
    def _build_screening_sql(self, *, require_close: bool = True) -> str:
        # review03-C7: SQL 模板为模块级静态常量，__CLOSE_COND__ 仅由 require_close
        # 布尔决定两种受控片段（空串 / "q.close IS NOT NULL...\nAND "），无用户输入，
        # 避免 f-string 拼 SQL 模式（列名亦来自 ORM 元数据，非拼接点）。
        # DAT-01: __STOCK_ALIVE_CONDITION__ 由 stock_alive_condition() 唯一正本渲染。
        close_clause = "q.close IS NOT NULL\n                 AND " if require_close else ""
        sql = _SCREENING_SQL_TEMPLATE.replace("__CLOSE_COND__", close_clause)
        return sql.replace("__STOCK_ALIVE_CONDITION__", stock_alive_condition(alias="b.", as_of="$5"))

    async def get_screening_data(self, trade_date: str | datetime.date | None = None):
        if not trade_date:
            trade_date = await self._get_latest_closed_trade_date()
        if not trade_date:
            logger.warning("[ScreenerDao] No trade_date available for screening data query")
            return pd.DataFrame()
        sql = self._build_screening_sql(require_close=True)
        # DAT-26: 边界显式转 date；suppress_errors=False 使查询失败显式传播，
        # 与"无数据返回空表"可区分（上游 data_provider 有 try/except 承接）。
        td = self._to_db_date(trade_date)
        return await self._read_db(sql, (td,) * 6, suppress_errors=False)

    async def get_fundamental_screening_data(self, trade_date: str | datetime.date | None = None):
        if not trade_date:
            trade_date = await self._get_latest_closed_trade_date()
        if not trade_date:
            logger.warning("[ScreenerDao] No trade_date available for fundamental screening data query")
            return pd.DataFrame()
        sql = self._build_screening_sql(require_close=False)
        td = self._to_db_date(trade_date)
        return await self._read_db(sql, (td,) * 6, suppress_errors=False)

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

    async def get_screening_data_range(self, start_date: str, end_date: str, max_rows: int | None = None):
        sql = self._build_screening_sql_range(require_close=True)
        # DAT-10: 区间预载携带行数护栏，超限抛 ValueError 由上游降级逐日查询
        # DAT-26: 边界显式转 date
        # D3-M4: max_rows 由调用方按区间真实规模自适应传入（None 沿用常量兜底），
        # 防止固定护栏与 A 股扩容后的真实行数过于接近而静默触发降级。
        return await self._read_db(
            sql,
            (self._to_db_date(start_date), self._to_db_date(end_date)),
            max_rows=max_rows if max_rows is not None else _MAX_SCREENING_RANGE_ROWS,
        )

    async def get_fundamental_screening_data_range(self, start_date: str, end_date: str, max_rows: int | None = None):
        sql = self._build_screening_sql_range(require_close=False)
        return await self._read_db(
            sql,
            (self._to_db_date(start_date), self._to_db_date(end_date)),
            max_rows=max_rows if max_rows is not None else _MAX_SCREENING_RANGE_ROWS,
        )

    # --- Review Manager Methods ---

    async def get_pending_predictions(self, date_threshold: str):
        """Get predictions that have no result yet since the date_threshold.

        BIZ-01: 不再以 ``ai_score > 0`` 过滤——纯数学策略（enable_ai_analysis=False）
        与无 AI 用户的记录没有 ai_score 列（落库为 NULL），此前被永久排除在复盘池外。
        AI 学习样例（get_learning_context）已单独用 prediction_result + alpha + ai_score
        过滤，不会被非 AI 记录污染。
        """
        sql = """
            SELECT id, trade_date, ts_code, ai_score, ai_reason
            FROM screening_history
            WHERE trade_date >= $1
              AND (review_status IN ($2, $3) OR review_status IS NULL)
            ORDER BY trade_date DESC
        """
        df = await self._read_db(sql, (date_threshold, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE))
        return df if df is not None else pd.DataFrame()

    async def get_unfilled_horizon_predictions(self, limit: int = 2000) -> list[dict]:
        """D2-4: 返回已过 T+1、但 T+5 仍未回填的复盘记录（id, ts_code, trade_date）。

        限定 ``review_status='T1_DONE'``：只回填"已过 T+1"的记录，避免把 pending
        （T+1 未做）提前置 COMPLETED 而从 ``get_pending_predictions`` 池中掉出、
        导致 T+1 永久缺失。按 trade_date 升序先补最旧（优先修复 AI 学习样本），
        LIMIT 封顶单次工作量，超出的次日继续，天然覆盖全部历史。
        T+{horizon} 成熟度（t0 + horizon 是否落在最新行情内）由调用方
        ``ReviewManager.backfill_horizon_returns`` 判定，故本方法不重复过滤。
        """
        t = ScreeningHistory.__table__
        stmt = (
            sa.select(t.c.id, t.c.ts_code, t.c.trade_date)
            .select_from(t)
            .where(
                t.c.review_status == REVIEW_STATUS_T1_DONE,
                t.c.t5_pct.is_(None),
            )
            .order_by(t.c.trade_date.asc())
            .limit(limit)
        )
        df = await self._read_db_select(stmt)
        return df.to_dict("records") if df is not None and not df.empty else []

    async def get_unfilled_t1_predictions(self, limit: int = 2000) -> list[dict]:
        """BIZ-03: 返回仍缺 T+1 的 PENDING/NULL 记录（id, ts_code, trade_date）。

        与 ``get_unfilled_horizon_predictions`` 对称：``run_review`` 的 10 交易日
        窗口只覆盖近期，长期未启动应用产生的 PENDING 记录由本通道兜底，避免
        T+1 永久缺失。限定 ``review_status IN (PENDING, NULL)`` 且 ``t1_pct IS NULL``；
        T+1 成熟度（t0 + 1 是否落在最新行情内）由调用方
        ``ReviewManager.backfill_t1_returns`` 判定，故本方法不重复过滤。
        按 trade_date 升序先补最旧（优先修复 AI 学习样本），LIMIT 封顶单次工作量，
        超出的次日继续，天然覆盖全部历史。
        """
        t = ScreeningHistory.__table__
        stmt = (
            sa.select(t.c.id, t.c.ts_code, t.c.trade_date)
            .select_from(t)
            .where(
                sa.or_(t.c.review_status == REVIEW_STATUS_PENDING, t.c.review_status.is_(None)),
                t.c.t1_pct.is_(None),
            )
            .order_by(t.c.trade_date.asc())
            .limit(limit)
        )
        df = await self._read_db_select(stmt)
        return df.to_dict("records") if df is not None and not df.empty else []

    @log_async_operation(
        operation_name="ScreenerDao.backfill_t5_prediction",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def backfill_t5_prediction(
        self,
        record_id: int,
        t5_pct: float,
        t5_price: float | None,
        *,
        label: str | None = None,
        index_pct: float | None = None,
        benchmark_code: str | None = None,
        alpha: float | None = None,
        conn: typing.Any = None,
    ):
        """D2-4: 幂等回填单条记录的 T+5 并推进 review_status 为 COMPLETED。

        WHERE 带 ``t5_pct IS NULL`` → 与 ``run_review`` 同批并行也不会重复/覆盖已填值。

        D4-M4: 新增可选 label/index_pct/benchmark_code/alpha，供 backfill_horizon_returns
        在 T+5 成熟回填时同步定稿 T+5 窗口标签（run_review 在 T+5 未成熟时仅打 DRAW 占位）。
        均为 None 时保持 D2-4 既有纯数值回填语义，不覆盖既有列。
        """
        self._check_engine()
        table = Base.metadata.tables.get("screening_history")
        if table is None:
            logger.error("[ScreenerDao] Table screening_history not found in SQLAlchemy metadata.")
            return

        values_: dict[str, typing.Any] = {
            "t5_pct": t5_pct,
            "t5_price": t5_price,
            "review_status": REVIEW_STATUS_COMPLETED,
        }
        if label is not None:
            values_["prediction_result"] = label
        if index_pct is not None:
            values_["index_pct"] = index_pct
        if benchmark_code is not None:
            values_["benchmark_code"] = benchmark_code
        if alpha is not None:
            values_["alpha"] = alpha

        stmt = sa.update(table).where(table.c.id == record_id, table.c.t5_pct.is_(None)).values(**values_)

        # DAT-01: 维护事件放行后复查引擎，防范 conn 路径 TOCTOU
        await self._wait_maintenance_guard(context="backfill_t5_prediction")
        if conn is not None:
            await conn.execute(stmt)
        else:
            try:
                async with self._guarded_begin() as tx_conn:
                    await tx_conn.execute(stmt)
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.warning("[ScreenerDao] Failed to backfill T+5 for record %s: %s", record_id, safe_error(e))

    async def get_learning_context(
        self,
        limit: int = 3,
        is_win: bool = True,
        as_of: datetime.date | datetime.datetime | None = None,
        strategy_name: str | None = None,
    ):
        """Return top WIN/LOSS samples for few-shot learning.

        ``strategy_name`` 非空时只取同策略样本：不同策略的选股逻辑与持有周期假设不同，
        跨策略 few-shot 会让模型学到错误的「特征 → 收益」映射（BIZ-01 排除纯数学记录
        是同一动机的策略维度延伸）。

        D4-M5 维护契约：**前视防护的真正正主是下方 WHERE 的 ``review_status ==
        REVIEW_STATUS_COMPLETED`` + ``t5_pct.isnot(None)`` 过滤**（二者保证样本的 T+5 复盘
        窗口已成熟、标签已定稿）。调用方 ``compute_learning_as_of`` 传入的 ``as_of`` 偏移是
        按自然日计算的额外冗余边界，语义上并不等于「T+5 已回填」。**因此放宽本过滤条件前
        （如为增加样本量而接受 T1_DONE 记录），必须先同步把 ``as_of`` 偏移改为交易日口径
        （经 TradeCalendarService 回退 N 个交易日）**，否则偏移不足会立刻退化为真实的前视泄漏。
        """
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
            t.c.benchmark_code,
        ).where(
            t.c.prediction_result == label,
            t.c.alpha.isnot(None),
            t.c.t5_pct.isnot(None),
            t.c.review_status == REVIEW_STATUS_COMPLETED,
            # BIZ-01: 学习样例仅含 AI 评分过的记录（纯数学策略记录同样满足
            # prediction_result+alpha 过滤，但无 ai_score，须显式排除避免污染 few-shot）。
            t.c.ai_score.isnot(None),
        )
        if strategy_name is not None:
            stmt = stmt.where(t.c.strategy_name == strategy_name)
        if as_of is not None:
            if isinstance(as_of, datetime.datetime):
                as_of = as_of.date()
            stmt = stmt.where(t.c.trade_date < as_of)
        stmt = stmt.order_by(order_dir(t.c.alpha), order_dir(t.c.t1_pct)).limit(limit)
        df = await self._read_db_select(stmt)
        return df if df is not None else pd.DataFrame()

    async def get_learning_context_stats(
        self,
        as_of: datetime.date | datetime.datetime | None = None,
        strategy_name: str | None = None,
    ):
        """返回 few-shot 学习样本的总体统计（BIZ/D4-M3 偏差二兜底）。

        极值样本（top WIN + top LOSS）不代表分布全貌；把样本总数、alpha 中位数与胜率
        一并注入 prompt，让模型知道 top 样本是尾部而非默认表现，避免高估自身识别
        极端机会的能力并难以校准置信度。
        """
        t = Base.metadata.tables["screening_history"]
        # 仅统计已复盘、带 alpha 的 AI 样本；与 get_learning_context 的 BIZ-01 过滤口径一致。
        stmt = (
            sa.select(
                sa.func.count().label("total"),
                sa.func.count().filter(t.c.prediction_result == "WIN").label("win_cnt"),
                sa.func.count().filter(t.c.prediction_result == "LOSS").label("loss_cnt"),
                sa.func.avg(t.c.alpha).label("alpha_mean"),
                sa.func.percentile_cont(0.5).within_group(t.c.alpha).label("alpha_median"),
            )
            .select_from(t)
            .where(
                t.c.alpha.isnot(None),
                t.c.t5_pct.isnot(None),
                t.c.review_status == REVIEW_STATUS_COMPLETED,
                t.c.ai_score.isnot(None),
            )
        )
        if strategy_name is not None:
            stmt = stmt.where(t.c.strategy_name == strategy_name)
        if as_of is not None:
            if isinstance(as_of, datetime.datetime):
                as_of = as_of.date()
            stmt = stmt.where(t.c.trade_date < as_of)
        df = await self._read_db_select(stmt)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "total": int(row["total"]),
            "win_cnt": int(row["win_cnt"] or 0),
            "loss_cnt": int(row["loss_cnt"] or 0),
            "alpha_mean": row["alpha_mean"],
            "alpha_median": row["alpha_median"],
        }

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
        benchmark_code: str | None = None,
        alpha: float | None = None,
        review_status: str | None = None,
        conn: typing.Any = None,
        guard_t1: bool = False,
    ):
        """Update review metrics and advance review_status according to available horizons.

        ``guard_t1``：T+1 幂等守卫。为 True 时 UPDATE 的 WHERE 追加 ``t1_pct IS NULL``，
        与 T+5 回填 ``backfill_t5_prediction`` 的 ``t5_pct IS NULL`` 语义对称，避免同时写入方
        （run_review 窗内新写）把 stale T+1 回填已填/正在填的值重复覆盖。
        默认 False：run_review 会对 T1_DONE 记录二次调用以补 T+5，此时 t1_pct 已非 NULL，
        必须允许覆盖，故仅 stale T+1 回填路径（``ReviewManager.backfill_t1_returns``）启用守卫。
        """
        self._check_engine()
        effective_status = review_status
        if effective_status is None:
            effective_status = REVIEW_STATUS_COMPLETED if t5_pct is not None else REVIEW_STATUS_T1_DONE

        table = Base.metadata.tables.get("screening_history")
        if table is None:
            logger.error("[ScreenerDao] Table screening_history not found in SQLAlchemy metadata.")
            return

        values: dict[str, typing.Any] = {
            "t1_pct": pct,
            "prediction_result": label,
            "t1_price": t1_price,
            "t5_pct": t5_pct,
            "t5_price": t5_price,
            "index_pct": index_pct,
            "alpha": alpha,
            "review_status": effective_status,
        }
        # D2-5：基准仅在本复盘拉取到响应对齐的 index_pct 后写入；None 表示调用方无基准上下文
        # （如 T+5 回填），保持既有值不动，避免用 NULL 覆写已落库的基准。
        if benchmark_code is not None:
            values["benchmark_code"] = benchmark_code

        stmt = sa.update(table).where(table.c.id == record_id).values(**values)
        if guard_t1:
            # BIZ-03 幂等守卫：仅 stale T+1 回填启用，防止把已填/正在填的 t1_pct 重复覆盖。
            stmt = stmt.where(table.c.t1_pct.is_(None))

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
            # LIFE-03: 覆盖语义——同 (trade_date, strategy_name, ts_code) 会覆盖历史行。
            # 显式置 PENDING：若被覆盖行此前已复盘（COMPLETED），其 prediction_result/alpha 等
            # computed 列因不在本次写入列而保留，但 review_status 重置为 PENDING 重新进入待复盘，
            # 保证复盘统计基于当日最新快照（覆盖即需重复盘）。此为覆盖语义的既定权衡。
            row["review_status"] = REVIEW_STATUS_PENDING
            enriched_records.append(tuple(row.get(c) for c in all_cols))
            if thinking_text:
                thinking_records.append(
                    {"run_id": row.get("run_id"), "ts_code": row.get("ts_code"), "thinking": str(thinking_text)}
                )

        df = pd.DataFrame(enriched_records, columns=all_cols)

        # LIFE-03: 覆盖语义唯一键 (trade_date, strategy_name, ts_code)。
        # 批内按新主键预去重（keep="last" 保留最新），避免批内同 key 触发重复告警；
        # 跨批/并发冲突交由 ON CONFLICT DO UPDATE 串行化处理。
        _pk = ["trade_date", "strategy_name", "ts_code"]
        df = df.drop_duplicates(subset=_pk, keep="last")

        await self._save_upsert(
            df=df,
            table_name="screening_history",
            columns=all_cols,
            pk_columns=_pk,
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
