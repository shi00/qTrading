import asyncio
import datetime
import json
import logging
import typing
import uuid

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.constants import DEFAULT_BENCHMARK_INDEX
from data.external.tushare_client import TushareClient
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import safe_error
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date, to_date

logger = logging.getLogger(__name__)


# D2-4: 复权持仓期收益率唯一正本，供 run_review（T+1/T+5 内联）与
# backfill_horizon_returns（T+5 延迟回填）共用，避免复权口径复制。
# ret = (close_tN/adj_tN) ÷ (close_t0/adj_t0) − 1；复权基准 adj_ref 在比率中抵消，
# 结果与基准选择无关（基准免疫）；除权日因 adj 变化自动校正（D2-3）。
# 无 adj_factor（存量数据/测试替身）时回退原始 close 比率，保持向后兼容。
def _qfq_return_pct(
    tn_ser: pd.Series,
    basis_close: float,
    basis_adj: float | None,
    has_adj_factor: bool,
) -> float | None:
    tn_close_raw = tn_ser.get("close")
    tn_close = float(tn_close_raw) if bool(pd.notna(tn_close_raw)) else None
    if tn_close is None:
        return None
    if has_adj_factor:
        tn_adj_raw = tn_ser.get("adj_factor")
        tn_adj = float(tn_adj_raw) if bool(pd.notna(tn_adj_raw)) else None
        if tn_adj is None or tn_adj == 0:
            return None
        if basis_adj is None or basis_adj == 0:
            return None
        return (tn_close / tn_adj) / (basis_close / basis_adj) - 1.0
    return (tn_close / basis_close) - 1.0


# RV-01: 基准侧窗口累计收益（百分比）。个股侧 _qfq_return_pct 返回分数、调用方 ×100，
# 此处直接返回百分比以便与 t5_pct 同为百分数相减算 alpha。指数点位已含分红除权调整、
# 无 adj_factor（index_daily 无复权因子），无需复权，直接用窗口首尾收盘价比值。
# 窗口两端任一 close 缺失/0 均视为窗口无意义，返回 None 由调用方跳过避免标签污染
# （对抗检视 Blocking-1：仅守卫 t0 会让终点缺失时产生 None 除法 TypeError）。
def _index_window_return_pct(idx_close_t0: float, idx_close_tn: float) -> float | None:
    if not idx_close_t0 or not idx_close_tn:
        return None
    return (idx_close_tn / idx_close_t0 - 1.0) * 100.0


class ReviewManager:
    """
    Manages the 'Verification' and 'Correction' phases of the AI loop.
    1. Calculates Actual Returns (T+1, T+5).
    2. Labels predictions (Win/Loss).
    3. Extracts 'Lessons' for Prompt Context.
    """

    def __init__(
        self,
        alpha_win_threshold: float = 3.0,
        alpha_loss_threshold: float = 3.0,
        label_horizon: str = "t5",
    ):
        # D4-M4: 标签窗口取 T+5 而非 T+1。单日超额收益的标准差与个股日波动同量级
        # （约 2~3 个百分点），以 ±0.5pp 划线会让近八成随机样本被打上 WIN/LOSS；
        # 而这些标签是 few-shot 学习样本的筛选依据，噪声标签会把模型引向虚假模式。
        # 阈值按 T+5 窗口波动尺度设定（5 日累计超额的残差量级，典型 3pp 以上才有区分度），
        # 且 T+5 收益（t5_pct）由 backfill 通道回填后方可打标签——保证标签建立在
        # 系统已采样的可靠窗口上，而非当日运气。
        self.cache = CacheManager()
        self.api = TushareClient()
        self.config = ConfigHandler()
        self.alpha_win_threshold = alpha_win_threshold
        self.alpha_loss_threshold = alpha_loss_threshold
        self.label_horizon = label_horizon

    def _classify_alpha(self, alpha: float) -> str:
        """按阈值将超额收益 alpha（百分点）分类为 WIN / LOSS / DRAW。

        D4-M4: 唯一打标签判据入口，run_review（T+5 成熟时）与
        backfill_horizon_returns（T+5 回填时）共用，避免口径漂移。
        """
        if alpha > self.alpha_win_threshold:
            return "WIN"
        if alpha < -self.alpha_loss_threshold:
            return "LOSS"
        return "DRAW"

    @log_async_operation(
        operation_name="t1_review",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run_review(self):
        """
        Main entry point: Review all pending predictions.
        Should be run daily after 16:00.
        """
        logger.info("[Review] Starting daily review...")

        pending_df = await self._get_pending_predictions()
        if pending_df.empty:
            logger.info("[Review] No pending predictions to review.")
            return

        updates: list[dict] = []

        all_codes = pending_df["ts_code"].unique().tolist()
        min_pred_date = self._normalize_trade_date(pending_df["trade_date"].min())

        bulk_quotes = await self.cache.quote_dao.get_daily_quotes(
            ts_code_list=all_codes,
            start_date=min_pred_date,
        )
        if bulk_quotes is None or bulk_quotes.empty:
            logger.warning("[Review] Bulk quotes fetch returned empty.")
            return
        quotes_by_code = {code: group.sort_values("trade_date") for code, group in bulk_quotes.groupby("ts_code")}

        # D3-M1: T+N 锚定的唯一交易日来源 = 全市场交易日历（TradeCalendarService，与回测层同通路）。
        # 候选股少或集中停牌时跨股票行情并集会整体丢日，导致 T+1/T+5 静默错位并污染标签。
        max_quote_date = self._normalize_trade_date(bulk_quotes["trade_date"].max())
        market_trade_dates: list[datetime.date] = await self._market_trade_dates(
            min_pred_date,
            max_quote_date,
            observed_dates={self._normalize_trade_date(d) for d in bulk_quotes["trade_date"]},
        )
        market_pos = {d: i for i, d in enumerate(market_trade_dates)}

        has_adj_factor = "adj_factor" in bulk_quotes.columns

        index_code = ConfigHandler.get_config("benchmark_index", DEFAULT_BENCHMARK_INDEX)
        index_cache = await self._prefetch_index_cache(index_code, min_pred_date, max_quote_date)
        # RV-01: 已完整探测（本地库+API 均无数据）的日期集合，避免对同一缺失日期逐股票重复探测。
        index_missing: set[str] = set()

        for _, row in pending_df.iterrows():
            ts_code = row["ts_code"]
            pred_date = row["trade_date"]

            df_quotes = quotes_by_code.get(ts_code)
            if df_quotes is None or df_quotes.empty:
                continue

            try:
                t0_match = df_quotes[df_quotes["trade_date"].astype(object) == pred_date]
                if t0_match.empty:
                    continue

                t0_label = t0_match.index[0]
                t0_date = self._normalize_trade_date(df_quotes.loc[t0_label, "trade_date"])
                t0_ser = df_quotes.loc[t0_label]
                t0_close_raw = t0_ser.get("close")
                t0_close = float(t0_close_raw) if bool(pd.notna(t0_close_raw)) else None
                if t0_close is None or t0_close == 0:
                    continue
                t0_adj_raw = t0_ser.get("adj_factor") if has_adj_factor else None
                t0_adj = float(t0_adj_raw) if has_adj_factor and bool(pd.notna(t0_adj_raw)) else None

                # D2-3 停牌防护：个股交易日→原始行序查表。停牌个股在真实 T+N 日缺行，
                # 以跨股票并集日历（market_trade_dates）锚定真实 T+N 再查个股价格，避免行位置漂移。
                stock_pos = {self._normalize_trade_date(d): int(i) for i, d in enumerate(df_quotes["trade_date"])}

                t0_mpos = market_pos.get(t0_date)
                t1_date = (
                    market_trade_dates[t0_mpos + 1]
                    if t0_mpos is not None and t0_mpos + 1 < len(market_trade_dates)
                    else None
                )
                t5_date = (
                    market_trade_dates[t0_mpos + 5]
                    if t0_mpos is not None and t0_mpos + 5 < len(market_trade_dates)
                    else None
                )

                t1_row = None
                t1_pct: float | None = None
                t1_price: float | None = None
                t5_pct: float | None = None
                t5_price: float | None = None

                # T+1（真实交易日 +1）
                if t1_date is not None and (t1_idx := stock_pos.get(t1_date)) is not None:
                    t1_row = df_quotes.iloc[t1_idx]
                    # D2-3 数据完整性门控：行存在但涨跌幅缺失（停牌保留行/脏数据）→ 悬空不标，
                    # 与"停牌缺行不标"同语义，避免把数据不完整的 T+1 误标为 0% 收益。
                    if "pct_chg" in t1_row.index and bool(pd.notna(t1_row["pct_chg"])) is False:
                        continue
                    t1_ret = _qfq_return_pct(t1_row, t0_close, t0_adj, has_adj_factor)
                    t1_pct = round(t1_ret * 100.0, 4) if t1_ret is not None else None
                    if "close" in t1_row.index and bool(pd.notna(t1_row["close"])):
                        t1_price = float(t1_row["close"])

                # T+5（真实交易日 +5）
                if t5_date is not None and (t5_idx := stock_pos.get(t5_date)) is not None:
                    t5_row = df_quotes.iloc[t5_idx]
                    t5_ret = _qfq_return_pct(t5_row, t0_close, t0_adj, has_adj_factor)
                    t5_pct = round(t5_ret * 100.0, 4) if t5_ret is not None else None
                    if "close" in t5_row.index and bool(pd.notna(t5_row["close"])):
                        t5_price = float(t5_row["close"])

                if t1_pct is not None and t1_row is not None:
                    t1_date_val = t1_row["trade_date"]
                    if hasattr(t1_date_val, "date") and callable(t1_date_val.date):
                        t1_date_obj: datetime.date = typing.cast(datetime.date, t1_date_val.date())
                    elif hasattr(t1_date_val, "year"):
                        t1_date_obj = t1_date_val
                    else:
                        t1_date_obj = datetime.datetime.strptime(str(t1_date_val).replace("-", "")[:8], "%Y%m%d").date()

                    # D4-M4: 标签窗口取 T+5。get_learning_context 只读
                    # ``t5_pct IS NOT NULL + review_status=COMPLETED`` 记录，故标签必须反映
                    # T+5 窗口而非 T+1 单日；t5 未成熟时仅回填 t1 数值、打 DRAW 占位
                    # （status 由 update_prediction_result 置 T1_DONE），待 run_review
                    # 重访或 backfill 补 T+5 后再以 T+5 超额定稿标签。
                    label_date = t5_date if (self.label_horizon == "t5" and t5_date is not None) else t1_date_obj
                    label_pct = t5_pct if (self.label_horizon == "t5" and t5_pct is not None) else None
                    if self.label_horizon != "t5":
                        # 兼容历史 t1 口径（仅测试/显式覆盖时）：T+1 阶段即以当日超额定稿。
                        label_pct = t1_pct

                    if label_pct is None:
                        # T+5 未成熟：暂不判定 WIN/LOSS，仅推进 T+1 数值与 T1_DONE 状态。
                        updates.append(
                            {
                                "record_id": row["id"],
                                "pct": t1_pct,
                                "label": "DRAW",
                                "index_pct": None,
                                "benchmark_code": index_code,
                                "t1_price": t1_price,
                                "t5_pct": None,
                                "t5_price": None,
                                "alpha": None,
                            }
                        )
                        logger.info(
                            "[Review] %s: T+5 not matured, staged T+1=%.2f%% as DRAW (label pending T+5)",
                            ts_code,
                            t1_pct,
                        )
                        continue

                    # RV-01: 基准侧必须与个股侧同窗口——取 T0 至 label（T+5）的指数
                    # 累计收益，而非 label 当日的单日涨跌幅（旧口径把标签变成对市场方向的押注）。
                    # 指数点位无需复权（index_daily 无 adj_factor），用首尾收盘价比值。
                    # 窗口终点取 label_date（t5 口径为 T+5，兼容 t1 口径为 T+1），保证同窗口同口径。
                    # index_missing 记录「已完整探测（本地库+API）仍无数据」的日期，避免
                    # 同一缺失日期对每只股票重复打 API（对抗检视 Minor-1：缓存 None 无法
                    # 区分未探测与已探无，把去重移到独立集合保持「每次探测一次」语义）。
                    t0_date_str = t0_date.strftime("%Y%m%d")
                    label_date_str = label_date.strftime("%Y%m%d")
                    for _d_str, _d in ((t0_date_str, t0_date), (label_date_str, label_date)):
                        # RV-01 修复点：缓存存 None（本地库该日无数据）时也须
                        # 尝试 API 兜底，仅凭 key 存在会短路兜底路径（对抗检视 Major-1）。
                        if _d_str not in index_cache and _d_str not in index_missing:
                            _d_close = await self._resolve_index_close(index_code, _d)
                            if _d_close is None:
                                index_missing.add(_d_str)
                            else:
                                index_cache[_d_str] = _d_close

                    index_pct = _index_window_return_pct(index_cache.get(t0_date_str), index_cache.get(label_date_str))

                    if index_pct is None:
                        logger.warning(
                            "[Review] %s: Index return unavailable for [%s, %s], skipping to avoid label pollution",
                            ts_code,
                            t0_date_str,
                            label_date_str,
                        )
                        continue

                    alpha = round(label_pct - index_pct, 4)

                    label = self._classify_alpha(alpha)

                    updates.append(
                        {
                            "record_id": row["id"],
                            "pct": t1_pct,
                            "label": label,
                            "index_pct": index_pct,
                            "benchmark_code": index_code,
                            "t1_price": t1_price,
                            "t5_pct": t5_pct if self.label_horizon == "t5" else None,
                            "t5_price": t5_price if self.label_horizon == "t5" else None,
                            "alpha": alpha,
                        }
                    )
                    logger.info(
                        "[Review] %s: Stock %s%% vs Index %s%% = Alpha %.2f%% -> %s, T+5=%s",
                        ts_code,
                        label_pct,
                        index_pct,
                        alpha,
                        label,
                        t5_pct if t5_pct is not None else "N/A",
                    )

            except Exception as e:
                logger.error("[Review] Error reviewing %s: %s", ts_code, safe_error(e))

        if updates:
            await self._batch_update_results(updates)

        logger.info("[Review] Completed. Updated %s records.", len(updates))

    @log_async_operation(operation_name="t5_backfill", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def backfill_horizon_returns(self, horizon: int = 5) -> int:
        """阶段 2：回填所有已满 horizon 个交易日、但 T+{horizon} 仍为 NULL 的复盘记录。

        D2-4：``run_review`` 只覆盖近 10 交易日的 pending 记录，一旦预测移出窗口，
        其 T+5 不再被任何机制回访。本方法每日调度一次即可自然覆盖全部历史，
        返回回填条数供任务进度上报。

        只处理 ``review_status='T1_DONE'`` 且 ``t5_pct IS NULL`` 的记录（已过 T+1、
        缺远期收益）；未满 horizon / 停牌缺行 / 复权计算失败的记录保持 NULL，
        次日重试。与 ``_qfq_return_pct`` 共享复权口径，避免预测当天与回填口径不一致。
        """
        if horizon <= 0:
            raise ValueError("[Review] backfill horizon must be positive")

        candidates = await self.cache.screener_dao.get_unfilled_horizon_predictions()
        if not candidates:
            logger.info("[Review] No unfilled T+%d records to backfill.", horizon)
            return 0

        all_codes = sorted({c["ts_code"] for c in candidates})
        min_t0 = min(self._normalize_trade_date(c["trade_date"]) for c in candidates)

        bulk_quotes = await self.cache.quote_dao.get_daily_quotes(
            ts_code_list=all_codes,
            start_date=min_t0,
        )
        if bulk_quotes is None or bulk_quotes.empty:
            logger.warning("[Review] Backfill: bulk quotes fetch returned empty.")
            return 0
        quotes_by_code = {code: group.sort_values("trade_date") for code, group in bulk_quotes.groupby("ts_code")}

        # D3-M1: 与 run_review 同一 T+N 锚定口径，全市场交易日历（TradeCalendarService）。
        max_quote_date = self._normalize_trade_date(bulk_quotes["trade_date"].max())
        market_trade_dates: list[datetime.date] = await self._market_trade_dates(
            min_t0,
            max_quote_date,
            observed_dates={self._normalize_trade_date(d) for d in bulk_quotes["trade_date"]},
        )
        market_pos = {d: i for i, d in enumerate(market_trade_dates)}

        has_adj_factor = "adj_factor" in bulk_quotes.columns

        # D4-M4: T+5 backfill 需定稿 T+5 标签，故须解析基准指数（与 run_review 同口径）。
        index_code = ConfigHandler.get_config("benchmark_index", DEFAULT_BENCHMARK_INDEX)
        index_cache = await self._prefetch_index_cache(index_code, min_t0, max_quote_date)
        # RV-01: 已完整探测（本地库+API 均无数据）的日期集合，避免对同一缺失日期逐记录重复探测。
        index_missing: set[str] = set()

        updates: list[dict] = []
        for cand in candidates:
            code = cand["ts_code"]
            t0_date = self._normalize_trade_date(cand["trade_date"])
            df_quotes = quotes_by_code.get(code)
            if df_quotes is None or df_quotes.empty:
                continue
            stock_pos = {self._normalize_trade_date(d): int(i) for i, d in enumerate(df_quotes["trade_date"])}
            t0_idx = stock_pos.get(t0_date)
            if t0_idx is None:
                continue  # t0 无收盘价 → 基准价未知，无法计算，留 NULL
            t0_ser = df_quotes.iloc[t0_idx]
            t0_close_raw = t0_ser.get("close")
            t0_close = float(t0_close_raw) if bool(pd.notna(t0_close_raw)) else None
            if t0_close is None or t0_close == 0:
                continue
            t0_adj_raw = t0_ser.get("adj_factor") if has_adj_factor else None
            t0_adj = float(t0_adj_raw) if has_adj_factor and bool(pd.notna(t0_adj_raw)) else None

            t0_mpos = market_pos.get(t0_date)
            if t0_mpos is None or t0_mpos + horizon >= len(market_trade_dates):
                continue  # T+horizon 尚未成熟，留 NULL 次日重试
            t5_date = market_trade_dates[t0_mpos + horizon]
            t5_idx = stock_pos.get(t5_date)
            if t5_idx is None:
                continue  # T+5 日停牌/缺行 → 数据不可得，不伪造
            t5_row = df_quotes.iloc[t5_idx]
            ret = _qfq_return_pct(t5_row, t0_close, t0_adj, has_adj_factor)
            if ret is None:
                continue
            t5_close_raw = t5_row.get("close")
            t5_price = float(t5_close_raw) if bool(pd.notna(t5_close_raw)) else None
            t5_pct = round(ret * 100.0, 4)

            # D4-M4: T+5 成熟回填时同步定稿 T+5 窗口标签。run_review 在 T+5
            # 未成熟时仅打 DRAW 占位（status=T1_DONE），此处补齐 T+5 数值并
            # 以 T+5 超额定稿 WIN/LOSS/DRAW（与 run_review 共用 _classify_alpha）。
            # RV-01: 基准侧取 T0→T+5 指数累计收益（与个股 t5_pct 同窗口同口径），
            # 而非 T+5 当日单日涨跌幅。窗口端点缺任一（含本地库+API 均不可得）均视为不可标。
            t0_date_str = t0_date.strftime("%Y%m%d")
            t5_date_str = t5_date.strftime("%Y%m%d")
            for _d_str, _d in ((t0_date_str, t0_date), (t5_date_str, t5_date)):
                # RV-01 修复点：仅对未缓存且未标记缺失的日期探测（本地库 → API 兜底），
                # 避免缓存 None 短路兜底（Major-1）同时也避免逐记录重复探测（Minor-1）。
                if _d_str not in index_cache and _d_str not in index_missing:
                    _d_close = await self._resolve_index_close(index_code, _d)
                    if _d_close is None:
                        index_missing.add(_d_str)
                    else:
                        index_cache[_d_str] = _d_close

            index_pct = _index_window_return_pct(index_cache.get(t0_date_str), index_cache.get(t5_date_str))
            if index_pct is None:
                logger.warning(
                    "[Review] T+5 backfill: %s: Index return unavailable for [%s, %s], skipping to avoid label pollution",
                    code,
                    t0_date_str,
                    t5_date_str,
                )
                continue
            alpha = round(t5_pct - index_pct, 4)
            label = self._classify_alpha(alpha)

            updates.append(
                {
                    "record_id": cand["id"],
                    "t5_pct": t5_pct,
                    "t5_price": t5_price,
                    "label": label,
                    "index_pct": index_pct,
                    "benchmark_code": index_code,
                    "alpha": alpha,
                }
            )

        if updates:
            await self._batch_backfill_t5(updates)

        logger.info("[Review] T+%d backfill completed: %s records updated.", horizon, len(updates))
        return len(updates)

    @log_async_operation(operation_name="t1_backfill", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def backfill_t1_returns(self) -> int:
        """阶段 1b：回填所有缺 T+1 的 PENDING/NULL 复盘记录（BIZ-03）。

        ``run_review`` 只覆盖近 10 交易日的 pending 记录，一旦预测移出窗口，
        其 T+1 不再被任何机制回访（T+5 回填通道只处理 T1_DONE，PENDING 进不去），
        形成永久数据缺口。本方法每日调度一次即可自然覆盖全部历史，返回回填条数。

        只处理 ``review_status IN (PENDING, NULL)`` 且 ``t1_pct IS NULL`` 的记录；
        T+1 交易日未满 / 停牌缺行 / 复权计算失败的记录保持 NULL，次日重试。
        与 ``run_review`` 共享 ``_qfq_return_pct`` 复权口径与 ``market_trade_dates``
        锚定，保证预测当天与回填口径一致。

        D4-M4: 标签窗口取 T+5，T+1 阶段仅回填 T+1 数值并打 DRAW 占位（与
        ``run_review`` 的 T+1 分支一致，不再以 T+1 单日超额定稿 WIN/LOSS）；标签由
        ``run_review`` 重访或 ``backfill_horizon_returns`` 在 T+5 成熟时定稿。
        状态推进由 ``update_prediction_result`` 完成（t5_pct 为空 → 自动置
        ``T1_DONE``，不会越级到 COMPLETED）。
        写库经 ``_batch_update_results(guard_t1=True)`` 启用 T+1 幂等守卫
        （WHERE 带 ``t1_pct IS NULL``），避免与 run_review 同批并行时把已填的
        T+1 重复覆盖。
        """
        candidates = await self.cache.screener_dao.get_unfilled_t1_predictions()
        if not candidates:
            logger.info("[Review] No unfilled T+1 records to backfill.")
            return 0

        all_codes = sorted({c["ts_code"] for c in candidates})
        min_t0 = min(self._normalize_trade_date(c["trade_date"]) for c in candidates)

        bulk_quotes = await self.cache.quote_dao.get_daily_quotes(
            ts_code_list=all_codes,
            start_date=min_t0,
        )
        if bulk_quotes is None or bulk_quotes.empty:
            logger.warning("[Review] T+1 backfill: bulk quotes fetch returned empty.")
            return 0
        quotes_by_code = {code: group.sort_values("trade_date") for code, group in bulk_quotes.groupby("ts_code")}

        # D3-M1: 与 run_review 同一 T+N 锚定口径，全市场交易日历（TradeCalendarService）。
        max_quote_date = self._normalize_trade_date(bulk_quotes["trade_date"].max())
        market_trade_dates: list[datetime.date] = await self._market_trade_dates(
            min_t0,
            max_quote_date,
            observed_dates={self._normalize_trade_date(d) for d in bulk_quotes["trade_date"]},
        )
        market_pos = {d: i for i, d in enumerate(market_trade_dates)}

        has_adj_factor = "adj_factor" in bulk_quotes.columns

        # D4-M4: 标签窗口取 T+5，T+1 阶段不解析 T+1 基准指数（alpha/标签留待 T+5 定稿）。
        index_code = ConfigHandler.get_config("benchmark_index", DEFAULT_BENCHMARK_INDEX)
        updates: list[dict] = []
        for cand in candidates:
            code = cand["ts_code"]
            t0_date = self._normalize_trade_date(cand["trade_date"])
            df_quotes = quotes_by_code.get(code)
            if df_quotes is None or df_quotes.empty:
                continue
            stock_pos = {self._normalize_trade_date(d): int(i) for i, d in enumerate(df_quotes["trade_date"])}
            t0_idx = stock_pos.get(t0_date)
            if t0_idx is None:
                continue  # t0 无收盘价 → 基准价未知，无法计算，留 NULL
            t0_ser = df_quotes.iloc[t0_idx]
            t0_close_raw = t0_ser.get("close")
            t0_close = float(t0_close_raw) if bool(pd.notna(t0_close_raw)) else None
            if t0_close is None or t0_close == 0:
                continue
            t0_adj_raw = t0_ser.get("adj_factor") if has_adj_factor else None
            t0_adj = float(t0_adj_raw) if has_adj_factor and bool(pd.notna(t0_adj_raw)) else None

            t0_mpos = market_pos.get(t0_date)
            if t0_mpos is None or t0_mpos + 1 >= len(market_trade_dates):
                continue  # T+1 尚未成熟，留 NULL 次日重试
            t1_date = market_trade_dates[t0_mpos + 1]
            t1_idx = stock_pos.get(t1_date)
            if t1_idx is None:
                continue  # T+1 日停牌/缺行 → 数据不可得，不伪造
            t1_row = df_quotes.iloc[t1_idx]
            # D2-3 数据完整性门控：行存在但涨跌幅缺失（停牌保留行/脏数据）→ 悬空不标。
            if "pct_chg" in t1_row.index and bool(pd.notna(t1_row["pct_chg"])) is False:
                continue
            ret = _qfq_return_pct(t1_row, t0_close, t0_adj, has_adj_factor)
            if ret is None:
                continue
            t1_pct = round(ret * 100.0, 4)
            t1_close_raw = t1_row.get("close")
            t1_price = float(t1_close_raw) if bool(pd.notna(t1_close_raw)) else None

            # D4-M4: 标签窗口取 T+5，T+1 阶段仅打 DRAW 占位（alpha/index_pct 留待
            # T+5 成熟时由 run_review 或 backfill_horizon_returns 定稿，与 run_review
            # 的 T+1 分支一致，避免以 T+1 单日超额定稿噪声标签）。
            label = "DRAW"

            updates.append(
                {
                    "record_id": cand["id"],
                    "pct": t1_pct,
                    "label": label,
                    "index_pct": None,
                    "benchmark_code": index_code,
                    "t1_price": t1_price,
                    "t5_pct": None,
                    "t5_price": None,
                    "alpha": None,
                }
            )

        if updates:
            await self._batch_update_results(updates, guard_t1=True)

        logger.info("[Review] T+1 backfill completed: %s records updated.", len(updates))
        return len(updates)

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _batch_backfill_t5(self, updates: list[dict]) -> None:
        """单事务批量回填 T+5（对齐 _batch_update_results 的事务与逐条降级语义）。"""
        dao = self.cache.screener_dao
        engine = self.cache.engine
        if engine is None:
            logger.error("[Review] Engine not available for T+5 backfill.")
            return

        try:
            async with engine.begin() as conn:
                for u in updates:
                    await dao.backfill_t5_prediction(
                        u["record_id"],
                        u["t5_pct"],
                        u["t5_price"],
                        label=u.get("label"),
                        index_pct=u.get("index_pct"),
                        benchmark_code=u.get("benchmark_code"),
                        alpha=u.get("alpha"),
                        conn=conn,
                    )
        except EngineDisposedError:
            # R5 一致性： disposed 引擎不可恢复，主路径必须上抛避免被吞没.
            raise
        except Exception as e:
            logger.error("[Review] Batch T+5 backfill failed, falling back to individual updates: %s", safe_error(e))
            for u in updates:
                try:
                    await dao.backfill_t5_prediction(
                        u["record_id"],
                        u["t5_pct"],
                        u["t5_price"],
                        label=u.get("label"),
                        index_pct=u.get("index_pct"),
                        benchmark_code=u.get("benchmark_code"),
                        alpha=u.get("alpha"),
                    )
                except EngineDisposedError:
                    # R5 一致性：fallback 路径同样必须上抛（与主路径对齐）.
                    raise
                except Exception as inner_e:
                    logger.error(
                        "[Review] Individual T+5 backfill also failed for record %s: %s",
                        u["record_id"],
                        safe_error(inner_e),
                    )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_pending_predictions(self):
        """
        Get predictions from last 10 trade days that have no result yet.
        Uses trade calendar for accurate lookback instead of natural days.
        """
        try:
            end_date = await self.cache.quote_dao.get_latest_trade_date()
            if not end_date:
                date_threshold = (get_now() - datetime.timedelta(days=14)).date()
            else:
                end_dt = parse_date(str(end_date))
                start_dt = end_dt - datetime.timedelta(days=30)
                trade_cal_df = await self.cache.stock_dao.get_trade_cal(
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=end_dt.strftime("%Y%m%d"),
                    is_open="1",
                )
                if trade_cal_df is not None and not trade_cal_df.empty and len(trade_cal_df) >= 10:
                    date_threshold = trade_cal_df.iloc[-10]["cal_date"]
                elif trade_cal_df is not None and not trade_cal_df.empty:
                    date_threshold = trade_cal_df.iloc[0]["cal_date"]
                else:
                    date_threshold = (get_now() - datetime.timedelta(days=14)).date()

            if date_threshold is not None:
                date_threshold = to_date(date_threshold)
            return await self.cache.screener_dao.get_pending_predictions(date_threshold)  # type: ignore[union-attr]

        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            severity = classify_severity(e, context="db")
            log_classified(
                logger,
                e,
                "db",
                "[Review] Error fetching pending predictions (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
            return pd.DataFrame()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_learning_context(
        self,
        limit: int | None = 3,
        as_of: datetime.date | datetime.datetime | None = None,
        strategy_name: str | None = None,
    ):
        """
        Extract 'Best Wins' and 'Worst Losses' for Prompt Injection.
        Returns formatted XML string for few-shot learning.

        P0-5 fix: as_of parameter prevents look-ahead bias. When provided,
        only predictions with trade_date < as_of are included, preventing
        future data from leaking into historical replay contexts.

        D4-M3: ``strategy_name`` 非空时只取同策略样本，避免跨策略 few-shot 混用导致
        模型学到错误的「特征 → 收益」映射。``limit`` 样本是分布尾部（top WIN/LOSS），
        为让模型能校准置信度，额外注入同口径总体统计（样本总数 / alpha 中位数 / 胜率），
        并声明样本量是否充足（不足时模型不应过度依赖尾部样本）。

        Corner cases:
        - No history: Returns minimal XML
        - All wins/no losses: Handles gracefully
        - DB errors: Returns empty context (non-blocking)
        """
        if as_of is not None and isinstance(as_of, datetime.datetime):
            as_of = as_of.date()
        if as_of is None:
            logger.warning(
                "[ReviewManager] get_learning_context called without as_of; "
                "using all completed samples. This may introduce look-ahead bias in backtest scenarios."
            )
        wins = []
        losses = []
        stats: dict | None = None

        try:
            df_wins = await self.cache.screener_dao.get_learning_context(
                limit=limit or 3,
                is_win=True,
                as_of=as_of,
                strategy_name=strategy_name,
            )
            if df_wins is not None and not df_wins.empty:
                for _, row in df_wins.iterrows():
                    wins.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "alpha": row["alpha"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "benchmark": row.get("benchmark_code"),
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore[union-attr]
                            else "",
                        },
                    )

            df_losses = await self.cache.screener_dao.get_learning_context(
                limit=limit or 3,
                is_win=False,
                as_of=as_of,
                strategy_name=strategy_name,
            )
            if df_losses is not None and not df_losses.empty:
                for _, row in df_losses.iterrows():
                    losses.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "alpha": row["alpha"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "benchmark": row.get("benchmark_code"),
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore[union-attr]
                            else "",
                        },
                    )

            stats = await self.cache.screener_dao.get_learning_context_stats(
                as_of=as_of,
                strategy_name=strategy_name,
            )

        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            severity = classify_severity(e, context="db")
            log_classified(
                logger,
                e,
                "db",
                "[Review] Error fetching learning context (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
            # Non-blocking: return empty context on error

        # Build XML
        xml = "<history_context>\n"

        # D4-M3 偏差二/三：附总体统计 + 样本量充足性声明。
        # 仅统计口径与提示语对模型可见，不改变 top WIN/LOSS 样本本身。
        if stats is not None and stats["total"] > 0:
            # 胜率 = WIN/(WIN+LOSS)，DRAW 不计入分母（与 get_strategy_review_stats 消费端口径一致）。
            labeled = stats["win_cnt"] + stats["loss_cnt"]
            win_rate = stats["win_cnt"] / labeled if labeled else None
            median_str = f"{float(stats['alpha_median']):+.1f}" if stats["alpha_median"] is not None else "N/A"
            win_rate_str = f"{win_rate * 100:.1f}%" if win_rate is not None else "N/A"
            enough = stats["total"] >= (limit or 3) * 4
            xml += f"  {I18n.get('review_ctx_stats', total=stats['total'], median=median_str, win_rate=win_rate_str)}\n"
            if not enough:
                xml += f"  {I18n.get('review_ctx_low_sample')}\n"

        if wins:
            xml += f"  [{I18n.get('review_ctx_positive')}]\n"
            for w in wins:
                alpha_str = f"{w['alpha']:+.1f}"
                pct_str = f"{w['pct']:+.1f}"
                reason = w["reason"] or I18n.get("review_ctx_no_reason")
                benchmark = w["benchmark"] or I18n.get("review_ctx_benchmark_na")
                xml += (
                    f"  - [{benchmark}] "
                    f"{I18n.get('review_ctx_win_detail', code=w['code'], name=w['name'], alpha=alpha_str, pct=pct_str, reason=reason)}\n"
                )

        if losses:
            xml += f"  [{I18n.get('review_ctx_negative')}]\n"
            for loss in losses:
                alpha_str = f"{loss['alpha']:+.1f}"
                pct_str = f"{loss['pct']:+.1f}"
                reason = loss["reason"] or I18n.get("review_ctx_no_reason")
                benchmark = loss["benchmark"] or I18n.get("review_ctx_benchmark_na")
                xml += (
                    f"  - [{benchmark}] "
                    f"{I18n.get('review_ctx_loss_detail', code=loss['code'], name=loss['name'], alpha=alpha_str, pct=pct_str, reason=reason)}\n"
                )

        if not wins and not losses:
            xml += f"  {I18n.get('review_ctx_none')}\n"

        xml += "</history_context>"
        return xml

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _batch_update_results(self, updates: list[dict], *, guard_t1: bool = False):
        """Update all review results within a single transaction.

        ``guard_t1`` 透传给 ``update_prediction_result``：仅 stale T+1 回填传递 True，
        为主路径与逐条降级两条写入路径统一加 T+1 幂等守卫。
        """
        dao = self.cache.screener_dao
        engine = self.cache.engine
        if engine is None:
            logger.error("[Review] Engine not available for batch update.")
            return

        try:
            async with engine.begin() as conn:
                for u in updates:
                    await dao.update_prediction_result(
                        u["record_id"],
                        u["pct"],
                        u["label"],
                        t1_price=u["t1_price"],
                        t5_pct=u["t5_pct"],
                        t5_price=u["t5_price"],
                        index_pct=u["index_pct"],
                        benchmark_code=u.get("benchmark_code"),
                        alpha=u["alpha"],
                        conn=conn,
                        guard_t1=guard_t1,
                    )
        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            logger.error("[Review] Batch update failed, falling back to individual updates: %s", safe_error(e))
            for u in updates:
                try:
                    await self._update_result(
                        u["record_id"],
                        u["pct"],
                        u["label"],
                        index_pct=u["index_pct"],
                        benchmark_code=u.get("benchmark_code"),
                        t1_price=u["t1_price"],
                        t5_pct=u["t5_pct"],
                        t5_price=u["t5_price"],
                        alpha=u["alpha"],
                        guard_t1=guard_t1,
                    )
                except EngineDisposedError:
                    # R5 一致性： disposed 引擎不可恢复，fallback 路径同样必须上抛（与主路径对齐）.
                    raise
                except Exception as inner_e:
                    logger.error(
                        "[Review] Individual update also failed for record %s: %s", u["record_id"], safe_error(inner_e)
                    )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _update_result(
        self,
        record_id: typing.Any,
        pct: typing.Any,
        label: typing.Any,
        index_pct: typing.Any = None,
        benchmark_code: typing.Any = None,
        t1_price: typing.Any = None,
        t5_pct: typing.Any = None,
        t5_price: typing.Any = None,
        alpha: typing.Any = None,
        review_status: typing.Any = None,
        guard_t1: bool = False,
    ):
        """Update DB with T+1/T+5 review metrics and review_status."""
        await self.cache.screener_dao.update_prediction_result(
            record_id,
            pct,
            label,
            t1_price=t1_price,
            t5_pct=t5_pct,
            t5_price=t5_price,
            index_pct=index_pct,
            benchmark_code=benchmark_code,
            alpha=alpha,
            review_status=review_status,
            guard_t1=guard_t1,
        )

    @staticmethod
    def _normalize_trade_date(value: typing.Any) -> datetime.date:
        """Normalize supported trade_date input types to datetime.date."""
        return to_date(value)

    async def _market_trade_dates(
        self,
        start,
        end,
        observed_dates: set[datetime.date] | None = None,
    ) -> list[datetime.date]:
        """T+N 锚定的唯一交易日来源：与回测层共用 TradeCalendarService（同一通路）。
        候选股少或集中停牌时行情并集会整体丢日，导致 T+1/T+5 静默错位并污染
        WIN/LOSS 标签与 AI few-shot 样本；全市场日历不受候选股范围影响。

        返回空日历（日历数据源不可用 / start>end 等退化态）时记警告日志，
        避免调用方在无有效日历下静默跳过全部 T+N 锚定（R3 反静默）。

        D3-M1 残留修复：get_trade_dates 对短窗口（≤30 自然日）不校验 DB 日历完整性，
        部分缺失（非全空）会静默返回残缺日历，T+N 锚点索引随之错位并污染标签。
        调用方传入 observed_dates（候选股行情日期并集，必 ⊆ [start, end]）做单向
        包含校验：观测交易日必须都在日历内；缺任一观测日即视为日历不可信，整体
        降级返回空日历（宁缺毋错，整批跳过本次锚定，下次调度自愈）。

        已知局限（对抗性检视确认）：若日历缺日 X 且所有候选股在 X 均停牌（观测
        集合恰无 X），单向包含校验无法检出；此为单向校验的固有代价，正常停牌场景
        下 T+N 锚点本就无行情可用，不会触发静默错位。
        """
        from data.domain_services.trade_calendar_service import TradeCalendarService

        dates = await TradeCalendarService(self.cache, None).get_trade_dates(start, end)
        if dates:
            if observed_dates:
                calendar_set = set(dates)
                missing = sorted(d for d in observed_dates if d not in calendar_set)
                if missing:
                    logger.warning(
                        "[Review] Market trade calendar incomplete: %d observed trading "
                        "date(s) absent (e.g. %s..%s) for [%s, %s]. Calendar may lag "
                        "quotes sync; T+N anchoring disabled for this run.",
                        len(missing),
                        missing[0],
                        missing[-1],
                        start,
                        end,
                    )
                    return []
        else:
            logger.warning(
                "[Review] Market trade calendar empty for [%s, %s]: T+N anchoring disabled for this run.",
                start,
                end,
            )
        return dates

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _prefetch_index_cache(
        self,
        index_code: str | None,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> dict[str, float]:
        """RV-01: 批量预取基准指数收盘点位到 {YYYYMMDD: close} 缓存。

        run_review 与 backfill_horizon_returns 共用，避免两处各自实现同一预取逻辑。
        RV-01 起缓存的是 ``close``（收盘点位）而非 ``pct_chg``（单日涨跌幅）——
        Alpha 的基准侧须为窗口累计收益，由调用方对窗口首尾两点 close 经
        ``_index_window_return_pct`` 换算，与个股 T+5 累计收益同窗口同口径。
        与旧逻辑一致：预取失败仅告警（非 system 级），
        由调用方的单条兜底（_resolve_index_close）补缺失日期。

        D3-m2: 日期参数全程使用 date 对象（与 DAT-26「DAO 边界显式转 date」方向
        统一），不再经 str(date) 隐式转换后再由 DAO 转回，避免 "2024-01-05" 与
        "20240105" 两种日期格式在库内并存造成的摩擦。
        """
        index_cache: dict[str, float] = {}
        try:
            df_index_bulk = await self.cache.get_index_daily_range(
                ts_code_list=[index_code],
                start_date=start_date,
                end_date=end_date,
            )
            if df_index_bulk is not None and not df_index_bulk.empty:
                for _, i_row in df_index_bulk.iterrows():
                    dt_val = i_row["trade_date"]
                    if hasattr(dt_val, "strftime"):
                        dt_str = dt_val.strftime("%Y%m%d")
                    else:
                        dt_str = str(dt_val).replace("-", "")[:8]
                    raw_close = i_row.get("close")
                    # RV-01: 仅缓存有效 close；本地库缺失日期不写 key，保证调用方
                    # 的逐日探测（本地库 → API 兜底）得以触发（旧实现对 None 日期
                    # 写 key 会让后续 `key not in cache` 短路 API 兜底）。
                    if raw_close is not None and pd.notna(raw_close) is True:
                        index_cache[dt_str] = float(raw_close)
                logger.info(
                    "[Review] Bulk loaded %d days of index close for %s.",
                    len(df_index_bulk),
                    index_code,
                )
        except asyncio.CancelledError:
            logger.warning("[Review] Cancelled during index bulk pre-fetch.")
            raise
        except Exception as exc:
            severity = classify_severity(exc, context="db")
            log_classified(
                logger,
                exc,
                "db",
                "[Review] Failed to bulk pre-fetch index quotes (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
        return index_cache

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _resolve_index_close(
        self,
        index_code: str | None,
        trade_date: datetime.date,
    ) -> float | None:
        """RV-01: 单条兜底解析指定交易日的基准指数收盘点位（close），失败返回 None。

        run_review 与 backfill_horizon_returns 共用，口径一致：先查缓存（本地库），
        缺失再降级 Tushare API；API 不可得 / 数据缺失返回 None，由调用方决定跳过。
        RV-01 起取 close（收盘点位）供窗口累计收益换算，不再取单日 pct_chg。
        """
        trade_date_str = trade_date.strftime("%Y%m%d")
        try:
            df_idx = await self.cache.quote_dao.get_index_daily(
                ts_code=index_code,
                trade_date=trade_date,
            )
            if df_idx is not None and not df_idx.empty:
                raw_close = df_idx.iloc[0]["close"]
                return float(raw_close) if pd.notna(raw_close) is True else None
            try:
                df_idx_api = await self.api.get_index_daily(
                    ts_code=index_code,
                    start_date=trade_date_str,
                    end_date=trade_date_str,
                )
                if df_idx_api is not None and not df_idx_api.empty:
                    raw_close = df_idx_api.iloc[0]["close"]
                    return float(raw_close) if pd.notna(raw_close) is True else None
                return None
            except (ValueError, TypeError, KeyError):
                return None
        except Exception as exc:
            severity = classify_severity(exc, context="db")
            log_classified(
                logger,
                exc,
                "db",
                "[Review] Cache index lookup failed for %s on %s (%s): %s",
                index_code,
                trade_date_str,
                exc_info=True,
            )
            if severity == "system":
                raise
            return None

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def save_results(
        self,
        strategy_name: str | None,
        df: pd.DataFrame,
        trade_date: datetime.date | datetime.datetime | pd.Timestamp | str | None = None,
        run_id: str | None = None,
        params_snapshot: str | dict[str, typing.Any] | None = None,
    ) -> int:
        """
        Save screening results to history for future review.
        Persists the full strategy execution snapshot including financial indicators and AI thinking.

        Args:
            strategy_name: Name of the strategy that produced the results.
            df: DataFrame of screening results.
            trade_date: The trading date being analyzed (not the current natural date).
                        If omitted, a single unique df["trade_date"] value may be used.

        Returns:
            Number of records actually persisted. ``0`` means nothing was written
            (empty df, or every row filtered out by ai_status — e.g. budget exceeded /
            policy not acknowledged / all AI failures). Callers MUST treat ``0`` as
            "no reviewable result produced", never as success (D4-C1).
        """
        if df is None or df.empty:
            return 0

        effective_date = self._normalize_trade_date(trade_date) if trade_date is not None else None

        df_trade_date = None
        if "trade_date" in df.columns:
            normalized_dates = {self._normalize_trade_date(v) for v in df["trade_date"].dropna().unique().tolist()}
            if len(normalized_dates) > 1:
                raise ValueError("save_results received multiple trade_date values in result dataframe")
            if normalized_dates:
                df_trade_date = next(iter(normalized_dates))

        if effective_date is None and df_trade_date is not None:
            effective_date = df_trade_date
        elif effective_date is not None and df_trade_date is not None and effective_date != df_trade_date:
            raise ValueError(
                f"save_results trade_date mismatch: arg={effective_date} df={df_trade_date}",
            )

        if effective_date is None:
            raise ValueError(
                "save_results requires an analysis trade_date or a single unique df['trade_date'] value",
            )

        if run_id is None:
            run_id = uuid.uuid4().hex[:16]

        if params_snapshot is None:
            params_snapshot_value = None
        elif isinstance(params_snapshot, dict):
            params_snapshot_value = params_snapshot
        else:
            try:
                params_snapshot_value = (
                    json.loads(params_snapshot) if isinstance(params_snapshot, str) else params_snapshot
                )
            except (json.JSONDecodeError, TypeError):
                params_snapshot_value = {"raw": str(params_snapshot)}

        # Helpers to safely extract fields
        def _f(row_data: typing.Any, key: typing.Any, default: typing.Any = None):
            v = row_data.get(key, default)
            if pd.isnull(v):
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _s(row_data: typing.Any, key: typing.Any, default: typing.Any = ""):
            v = row_data.get(key, default)
            if pd.isnull(v):
                return default
            return str(v)

        records = []
        for _, row in df.iterrows():
            ts_code = row.get("ts_code")
            if not ts_code:
                continue

            # D3-7: 仅写入 ai_status == analyzed 的记录，不写入 rejected / failed
            # 避免非分析结果污染 AI 学习闭环数据（failed 写盘会让 AI"学习从未输出过的预测"）
            ai_status = row.get("ai_status")
            if ai_status is not None and ai_status != "analyzed":
                continue  # rejected/failed 不入数据库，由上层/UI 呈现不参与学习

            # BIZ-01: 缺省 None——「无 AI 分数」（纯数学策略/未配置 AI）与「AI 给 0 分」
            # 在数据层可区分，且不把非分析结果伪装成 0 分（R21 缺失值用 None 哨兵）。
            ai_score = row.get("ai_score")
            try:
                ai_score = int(ai_score) if pd.notnull(ai_score) else None  # type: ignore[union-attr]
            except (ValueError, TypeError):
                ai_score = None

            ai_reason = row.get("ai_reason", "")
            if pd.isnull(ai_reason):  # type: ignore[union-attr]
                ai_reason = ""

            thinking = row.get("thinking", "")
            if pd.isnull(thinking):  # type: ignore[union-attr]
                thinking = ""

            records.append(
                {
                    "run_id": run_id,
                    "trade_date": effective_date,
                    "strategy_name": strategy_name,
                    "ts_code": ts_code,
                    "name": _s(row, "name"),
                    "close": _f(row, "close"),
                    "pct_chg": _f(row, "pct_chg"),
                    "industry": _s(row, "industry_sw_l2"),
                    "vol": _f(row, "vol"),
                    "amount": _f(row, "amount"),
                    "turnover_rate": _f(row, "turnover_rate"),
                    "pe_ttm": _f(row, "pe_ttm"),
                    "pb": _f(row, "pb"),
                    "ps_ttm": _f(row, "ps_ttm"),
                    "dv_ttm": _f(row, "dv_ttm"),
                    "total_mv": _f(row, "total_mv"),
                    "circ_mv": _f(row, "circ_mv"),
                    "roe": _f(row, "roe"),
                    "grossprofit_margin": _f(row, "grossprofit_margin"),
                    "debt_to_assets": _f(row, "debt_to_assets"),
                    "or_yoy": _f(row, "or_yoy"),
                    "netprofit_yoy": _f(row, "netprofit_yoy"),
                    "ai_score": ai_score,
                    "ai_reason": str(ai_reason),
                    "thinking": str(thinking),
                    "params_snapshot": params_snapshot_value,
                }
            )

        if not records:
            # D4-C1: 全量被 ai_status 过滤（预算超限/政策未确认/AI 全失败）属于业务失败，
            # 调用方须据此决定是否标记 idempotency，不可按"df 非空"当作成功。
            # D4-m1: 静默 return 会让夜间任务"宣称成功、实际零落库"，必须留日志。
            logger.warning(
                "[Review] save_results: all %d rows filtered by ai_status, nothing persisted (strategy=%s)",
                len(df),
                strategy_name,
            )
            return 0

        await self.cache.screener_dao.save_screening_results(records)
        logger.info("[Review] Saved %s predictions for %s", len(records), strategy_name)
        return len(records)
