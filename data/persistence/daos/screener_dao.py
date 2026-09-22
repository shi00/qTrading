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


def _derive_screening_from_fundamental(fund: pd.DataFrame | None) -> pd.DataFrame | None:
    """DS-05: 由 require_close=False 全集派生 require_close=True 子集（screening ⊂ fundamental）。

    get_screening_data*/get_fundamental_screening_data* 共享同一 SQL 模板，唯一差异是
    ``q.close IS NOT NULL`` WHERE 条件。SQL 层该判定与 pandas ``close.notna()`` 等价，
    故两遍几乎相同的全市场 JOIN 收敛为一遍 + 内存过滤。缺 close 列（防御路径，真实模板恒含）
    时原样返回，避免对旧列形状/测试替身做出不可预期裁剪。
    """
    if fund is None or fund.empty or "close" not in fund.columns:
        return fund
    return fund[fund["close"].notna()].reset_index(drop=True)


# UX-05: 复盘聚合统计的历史窗口（天）。get_history_tree 与 get_strategy_review_stats 共用，
# 消除魔术字符串漂移（四审 L1/m1/m2）。值拼接进 SQL 的 INTERVAL，为受控模块常量、非用户输入，
# 无注入面（review03-C7 约束的是用户输入可变点）。
REVIEW_STATS_WINDOW_DAYS = 180

# _LEARNING_CONTEXT_BASE_SQL removed - refactored to SQLAlchemy Core


# review03-C7: 单日/区间选股 SQL 静态模板。__CLOSE_COND__ 与 __STOCK_ALIVE_CONDITION__
# 为唯一可变点，分别由 _build_screening_sql/_build_screening_sql_range 按 require_close
# 布尔与 PIT 时点（DAT-01）替换为模块受控片段（无用户输入），避免 f-string 拼 SQL 模式。
# __STOCK_ALIVE_CONDITION__ 必须经 stock_alive_condition() 渲染（唯一正本），禁止内联复制。
# DS-02（ST 时点还原链路）：name 列经 name-history LATERAL JOIN 按 as-of 时点还原历史名称
# （无历史记录时 COALESCE 回退当前名称 stock_basic.name，防空表把全市场误判为非 ST）；
# 新增派生列 is_st（UPPER(name) LIKE '%ST%'，覆盖 *ST/S*ST，与 _get_limit_pct 语义一致），
# 供数据层行过滤排除风险警示股（P2）与 as-of 名称涨跌停判定。as-of 参数复用既有 $5
# （单日版，恒等于 trade_date）与 cal.cal_date（区间版），不新增参数位。
_SCREENING_SQL_TEMPLATE = """
              SELECT b.ts_code,
                     COALESCE(nh.name, b.name) AS name,
                     CASE WHEN UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%' THEN TRUE ELSE FALSE END AS is_st,
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
                        LEFT JOIN LATERAL (
                            SELECT name
                            FROM stock_name_history
                            WHERE ts_code = b.ts_code
                              AND start_date <= $5
                              AND (end_date IS NULL OR end_date > $5)
                            ORDER BY start_date DESC
                            LIMIT 1
                        ) nh ON TRUE
                        LEFT JOIN suspend_d s ON b.ts_code = s.ts_code AND s.trade_date = $6
               WHERE __CLOSE_COND__b.list_date <= $4
                 AND __STOCK_ALIVE_CONDITION__
              """


_SCREENING_SQL_RANGE_TEMPLATE = """
              SELECT b.ts_code,
                     COALESCE(nh.name, b.name) AS name,
                     CASE WHEN UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%' THEN TRUE ELSE FALSE END AS is_st,
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
                        LEFT JOIN LATERAL (
                            SELECT name
                            FROM stock_name_history
                            WHERE ts_code = b.ts_code
                              AND start_date <= cal.cal_date
                              AND (end_date IS NULL OR end_date > cal.cal_date)
                            ORDER BY start_date DESC  -- SC-01: as-of 名称还原（DATA-04 L3），消除当前名称快照的前视偏差
                            LIMIT 1
                        ) nh ON TRUE
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
        # RV-03: append-only 语义下同 (trade_date, strategy_name) 可含多次运行（不同 run_id），
        # 历史树按 (trade_date, strategy_name, run_id) 聚合——「今天跑了 3 次」分别可见可开。
        # COUNT(*) 为该 run 的股票数；展示代表值取 MAX(id)（id 单调递增 = 最近一次命中，
        # run_id 为随机 hex 字典序不能代表时间序，对抗检视 Major-1）。
        # 点击按 trade_date+strategy_name+id 载入（load_history_records 已支持 run_id 过滤）。
        sql = f"""
            SELECT trade_date, strategy_name, run_id, COUNT(*) as cnt, MAX(id) as latest_id
            FROM screening_history
            WHERE trade_date >= CURRENT_DATE - INTERVAL '{REVIEW_STATS_WINDOW_DAYS} days'
            GROUP BY trade_date, strategy_name, run_id
            ORDER BY trade_date DESC, latest_id DESC
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

        口径（设计 v5 + RV-03）：
        - **最新运行口径（RV-03 显式决策）**：同 (trade_date, strategy_name, ts_code) 取
          **最近一次运行**（DISTINCT ON 3 键 ORDER BY ... id DESC，id 单调递增 = 最近；
          run_id 为随机 hex 字典序不能代表时间序，故弃用 run_id DESC）。append-only
          语义下每 4 键组（含 run_id）是一行，DISTINCT 从死逻辑变真逻辑，消除被淘汰
          股票行/多次运行加权残留。
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
                sh.c.id,
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
            # RV-03: DISTINCT ON (trade_date, strategy_name, ts_code) ORDER BY ... id DESC
            # （id 单调 = 最近一次运行；DISTINCT ON 要求 ORDER BY 列在 select list，故含 id）
            .distinct(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code)
            .order_by(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code, sh.c.id.desc())
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

        口径与 ``get_strategy_review_stats`` 对齐（设计 v5 + RV-03）：
        - **最新运行口径（RV-03）**：同 (trade_date, strategy_name, ts_code) 取最近一次
          运行（DISTINCT ON ... ORDER BY ... id DESC，id 单调 = 最近；弃用 run_id DESC）。
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
                sh.c.id,
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
            # RV-03: DISTINCT ON ... ORDER BY ... id DESC（id 单调 = 最近一次运行；
            # DISTINCT ON 要求 ORDER BY 列在 select list，故含 id）
            .distinct(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code)
            .order_by(sh.c.trade_date, sh.c.strategy_name, sh.c.ts_code, sh.c.id.desc())
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
        # DS-05: screening ⊂ fundamental（同模板唯差 close 条件），由全集内存派生，
        # 收敛两遍几乎相同的全市场 JOIN 为一遍。origin 的 suppression/错误传播语义
        # 由 get_fundamental_screening_data 继承（suppress_errors=False 同源）。
        return _derive_screening_from_fundamental(await self.get_fundamental_screening_data(trade_date))

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
        # DS-05: 同 get_screening_data，由区间全集派生，消除 preload_range 双份 150 万行
        # 内存峰值与 DB 两遍 JOIN。护栏 max_rows 应用于全集查询（较松那份，行数更多），
        # 超限仍由 _read_db 抛 ValueError 驱动降级逐日——护栏语义变化见 PR 说明。
        return _derive_screening_from_fundamental(
            await self.get_fundamental_screening_data_range(start_date, end_date, max_rows=max_rows)
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

    async def get_unlabeled_predictions(self, limit: int = 2000) -> list[dict]:
        """RV-04: 返回「数值已齐、标签未定稿」的复盘记录（id, ts_code, trade_date, t5_pct）。

        限定 ``review_status='T1_DONE'`` 且 ``t5_pct IS NOT NULL`` 且 ``alpha IS NULL``：
        基准缺失时数值-only 解耦写入（RV-04）与 #1097 迁移重置的历史行均落入本通道，
        由 ``ReviewManager.backfill_horizon_returns`` 补定稿标签。与
        ``get_unfilled_horizon_predictions``（A 类缺数值）分池独立 LIMIT，
        防止历史 B 类堆积挤占 A 类候选（对抗检视：合并池会按 trade_date asc
        让老记录排满 LIMIT 饿死新记录）。
        """
        t = ScreeningHistory.__table__
        stmt = (
            sa.select(t.c.id, t.c.ts_code, t.c.trade_date, t.c.t5_pct)
            .select_from(t)
            .where(
                t.c.review_status == REVIEW_STATUS_T1_DONE,
                t.c.t5_pct.isnot(None),
                t.c.alpha.is_(None),
            )
            .order_by(t.c.trade_date.asc())
            .limit(limit)
        )
        df = await self._read_db_select(stmt)
        return df.to_dict("records") if df is not None and not df.empty else []

    @log_async_operation(
        operation_name="ScreenerDao.finalize_prediction_label",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def finalize_prediction_label(
        self,
        record_id: int,
        *,
        label: str,
        index_pct: float,
        benchmark_code: str,
        alpha: float,
        conn: typing.Any = None,
    ):
        """RV-04: 幂等定稿单条记录的复盘标签并推进 review_status 为 COMPLETED。

        WHERE 带 ``alpha IS NULL`` 守卫 → 已定稿标签（含并发方先写）不会被覆盖；
        数值列（t5_pct 等）不在写入集，与 backfill_t5_prediction 的数值通道互补：
        数值回填（WHERE t5_pct IS NULL）与标签定稿（WHERE alpha IS NULL）各自幂等。
        """
        self._check_engine()
        table = Base.metadata.tables.get("screening_history")
        if table is None:
            logger.error("[ScreenerDao] Table screening_history not found in SQLAlchemy metadata.")
            return

        values: dict[str, typing.Any] = {
            "prediction_result": label,
            "index_pct": index_pct,
            "benchmark_code": benchmark_code,
            "alpha": alpha,
            "review_status": REVIEW_STATUS_COMPLETED,
        }
        stmt = sa.update(table).where(table.c.id == record_id, table.c.alpha.is_(None)).values(**values)

        # DAT-01: 维护事件放行后复查引擎，防范 conn 路径 TOCTOU
        await self._wait_maintenance_guard(context="finalize_prediction_label")
        if conn is not None:
            await conn.execute(stmt)
        else:
            try:
                async with self._guarded_begin() as tx_conn:
                    await tx_conn.execute(stmt)
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.warning("[ScreenerDao] Failed to finalize label for record %s: %s", record_id, safe_error(e))

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
        """D2-4: 幂等回填单条记录的 T+5 并按标签成熟度推进 review_status。

        WHERE 带 ``t5_pct IS NULL`` → 与 ``run_review`` 同批并行也不会重复/覆盖已填值。

        D4-M4: 新增可选 label/index_pct/benchmark_code/alpha，供 backfill_horizon_returns
        在 T+5 成熟回填时同步定稿 T+5 窗口标签（run_review 在 T+5 未成熟时仅打 DRAW 占位）。
        均为 None 时保持 D2-4 既有纯数值回填语义，不覆盖既有列。

        RV-04: label 为 None（基准缺失的数值-only 解耦写入）时 status 停留 ``T1_DONE``
        而非 COMPLETED——标签未定稿的记录须留在补标签通道（get_unlabeled_predictions）
        的候选池内，且不进入学习/统计消费方（均要求 alpha IS NOT NULL）。
        """
        self._check_engine()
        table = Base.metadata.tables.get("screening_history")
        if table is None:
            logger.error("[ScreenerDao] Table screening_history not found in SQLAlchemy metadata.")
            return

        values_: dict[str, typing.Any] = {
            "t5_pct": t5_pct,
            "t5_price": t5_price,
            "review_status": REVIEW_STATUS_COMPLETED if label is not None else REVIEW_STATUS_T1_DONE,
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
        # RV-03: append-only 下同日同股多 run 会重复取样——先按 (trade_date, strategy_name,
        # ts_code) 去重取**最近一次运行**（DISTINCT ON ... ORDER BY id DESC，与统计口径一致），
        # 再取极值样本，避免同名样本重复挤占 few-shot 多样性（对抗检视 Major-2 闭合）。
        latest = (
            sa.select(
                t.c.id,
                t.c.ts_code,
                t.c.name,
                t.c.alpha,
                t.c.t1_pct,
                t.c.t5_pct,
                t.c.ai_score,
                t.c.ai_reason,
                t.c.benchmark_code,
                t.c.trade_date,
                t.c.strategy_name,
            )
            .where(
                t.c.prediction_result == label,
                t.c.alpha.isnot(None),
                t.c.t5_pct.isnot(None),
                t.c.review_status == REVIEW_STATUS_COMPLETED,
                # BIZ-01: 学习样例仅含 AI 评分过的记录（纯数学策略记录同样满足
                # prediction_result+alpha 过滤，但无 ai_score，须显式排除避免污染 few-shot）。
                t.c.ai_score.isnot(None),
            )
            .distinct(t.c.trade_date, t.c.strategy_name, t.c.ts_code)
            .order_by(t.c.trade_date, t.c.strategy_name, t.c.ts_code, t.c.id.desc())
            .subquery("latest_learning")
        )
        stmt = sa.select(
            latest.c.ts_code,
            latest.c.name,
            latest.c.alpha,
            latest.c.t1_pct,
            latest.c.t5_pct,
            latest.c.ai_score,
            latest.c.ai_reason,
            latest.c.benchmark_code,
        )
        if strategy_name is not None:
            stmt = stmt.where(latest.c.strategy_name == strategy_name)
        if as_of is not None:
            if isinstance(as_of, datetime.datetime):
                as_of = as_of.date()
            stmt = stmt.where(latest.c.trade_date < as_of)
        stmt = stmt.order_by(order_dir(latest.c.alpha), order_dir(latest.c.t1_pct)).limit(limit)
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
        label: str | None,
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
        RV-04: ``label=None`` 表示标签未定稿（基准缺失的数值-only 解耦写入），
        prediction_result 置 NULL（R21 缺失值语义），调用方须显式传 review_status。
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
            # RV-03: append-only 语义——新写入成为独立研究记录（唯一键含 run_id）。
            # 与 LIFE-03 覆盖语义不同：不再显式重置 PENDING 覆盖既有行的复盘状态。
            # run_id 每次运行唯一，同一 (trade_date, strategy_name, ts_code, run_id)
            # 的重复写入（重试）经 4 键 upsert 幂等；不同 run 各自独立成行。
            row["review_status"] = REVIEW_STATUS_PENDING
            enriched_records.append(tuple(row.get(c) for c in all_cols))
            if thinking_text:
                thinking_records.append(
                    {"run_id": row.get("run_id"), "ts_code": row.get("ts_code"), "thinking": str(thinking_text)}
                )

        df = pd.DataFrame(enriched_records, columns=all_cols)

        # RV-03: append-only 唯一键 (trade_date, strategy_name, ts_code, run_id)。
        # 批内按 4 主键预去重（keep="last" 保留最新），避免批内同 key 触发重复告警；
        # 跨批/并发冲突交由 ON CONFLICT DO UPDATE 串行化处理。
        _pk = ["trade_date", "strategy_name", "ts_code", "run_id"]
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
