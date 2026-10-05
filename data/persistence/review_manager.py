import asyncio
import datetime
import json
import logging
import math
import typing
import uuid
from collections.abc import Sequence

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.constants import (
    DEFAULT_BENCHMARK_INDEX,
    MAJOR_INDICES,
    REVIEW_STATUS_T1_DONE,
    REVIEW_STATUS_UNTRADABLE,
    board_benchmark_for,
)
from data.domain_services.review_stats_service import REVIEW_MIN_SAMPLE
from data.external.tushare_client import TushareClient
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import safe_error
from core.i18n import I18n, Message
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date, to_date
from utils.prompt_guard import neutralize_external_text

logger = logging.getLogger(__name__)

# CRITICAL-02：策略结果中承载结构化归因的列名（``strategies.attribution.ATTRIBUTION_COLUMN``
# 的镜像常量）。R1 禁止 data 层 import strategies 层，故此处复制字面量，两侧修改须同步。
_ATTRIBUTION_COLUMN = "_filter_attribution"


def serialize_exec_warnings(warnings: Sequence[Message] | None) -> list[dict[str, typing.Any]] | None:
    """执行期 warnings 通道 → JSONB 可落库形态（CRITICAL-02，R21/BT-03）。

    ``None``（调用方未提供执行上下文）保持 SQL NULL，历史回看据此走「未记录」分支；
    空序列返回空数组 ``[]``，表示「已记录且无警告」——两者语义不同（R21：缺失不伪装成
    合法值）。Message 的 key/params 序列化为 ``{"key": .., "params": ..}``。
    """
    if warnings is None:
        return None
    serialized: list[dict[str, typing.Any]] = []
    for w in warnings:
        key = getattr(w, "key", None)
        if not key:
            continue
        params = getattr(w, "params", None)
        serialized.append({"key": str(key), "params": dict(params) if isinstance(params, dict) else {}})
    return serialized


def deserialize_exec_warnings(raw: typing.Any) -> tuple[Message, ...] | None:
    """JSONB 落库值 → Message 元组（CRITICAL-02 历史回看还原）。

    ``None``/空值（SQL NULL）表示「该次筛选未记录执行上下文」，返回 ``None`` 由调用方
    决定渲染「未记录」哨兵；空数组返回空元组（已记录且无警告）。JSON 字符串（raw SQL
    路径）与已解析 list（SQLAlchemy Core 路径）均接受，非法输入按「未记录」处理。
    """
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(raw, list):
        return None
    out: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        params = item.get("params")
        out.append(Message(str(key), params if isinstance(params, dict) else {}))
    return tuple(out)


def _parse_filter_attribution(raw: typing.Any) -> dict[str, typing.Any] | None:
    """解析策略结果 ``_filter_attribution`` 列（JSON 字符串）为 JSONB 可落库 dict。

    缺失/非法/None → ``None``（不伪造归因）。R1：data 层不得 import strategies，
    故列名沿用镜像常量 ``_ATTRIBUTION_COLUMN``。
    """
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


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


# MAJOR-02: 一字涨停可成交性判定容差。stk_limit 的 up_limit 与个股 open 均为名义价，
# 浮点相等判定留 1e-6 容差（对齐回测撮合层 strategies/backtest/portfolio.py 的
# ``open >= up_limit - 1e-6`` 判定，避免两处口径漂移）。
_LIMIT_UP_EPSILON = 1e-6


def _is_t1_untradable(basis_open: float | None, up_limit: float | None) -> bool:
    """MAJOR-02: 判定 T+1 是否「一字涨停不可成交」。

    T+1 开盘价 ≥ 涨停价时，开盘即封涨停、无法以开盘价买入（一字板），其后计算的
    「收益」是买不进的纸上收益，若照常打标签会把不可成交的涨停计为选股命中
    （方向性偏差：牛市高 beta 股系统性 WIN）。缺失 up_limit（stk_limit 未同步/停牌）
    或缺失 open 时返回 False（不判定），由调用方降级为原行为，避免误伤正常记录。
    """
    if basis_open is None or up_limit is None:
        return False
    return basis_open >= float(up_limit) - _LIMIT_UP_EPSILON


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
        # RV-04: 本次实例运行期的基准诊断（降级/全缺失说明），None=基准正常；
        # 供 review_backfill job 读取拼进任务结果（用户可见，nightly_prediction B1 先例）。
        self._benchmark_diag: str | None = None
        # MAJOR-02: 本次实例运行期检出的「不可成交」（T+1 一字涨停）记录数。
        # run_review 起始清零（其单次调用即一次完整复盘周期）；backfill 两通道共用实例
        # 累加，由 review_backfill job 在跑完 T+1/T+5 后读取，向用户呈现「N 条未计入」。
        self._untradable_count = 0

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

        # MAJOR-02: 单次 run_review 即一次完整复盘周期，起始清零不可成交计数。
        self._untradable_count = 0

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

        # RV-04: 基准经降级链解析（配置基准不可得 → MAJOR_INDICES 首个可得），
        # 实际基准记入 benchmark_code；诊断供 job 呈现。
        index_code = await self._resolve_benchmark(min_pred_date)
        # MAJOR-02: 复盘基准按个股所属板块选择（创业板→创业板指、科创板→科创50、
        # 主板/其余→配置基准），避免把板块 beta 计为选股超额。仅预取本批实际用到的基准。
        needed_indices = {board_benchmark_for(code, index_code) for code in all_codes}
        index_caches, index_missing = await self._prefetch_benchmark_caches(
            index_code, needed_indices, min_pred_date, max_quote_date
        )
        # MAJOR-02: T+1 可成交性守卫数据（区间涨跌停价）。
        limit_up_cache = await self._prefetch_limit_up_cache(min_pred_date, max_quote_date)

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
                # RV-02: 买入基准改 T+1 开盘复权价（对齐回测默认 next_open）——不再以
                # T0 收盘为基准（T0 收盘→T+1 开盘的隔夜跳空对用户不可得，计入收益会
                # 系统性高估高开策略）。t1 行在此块之后解析（依赖 market 日历锚定），
                # t1 停牌/开盘缺失由下方守卫跳过留待自愈。

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

                # RV-02: T+1 为实际可成交日（回测默认 next_open），买入基准取 T+1 开盘复权价。
                # T+1 停牌/缺行/开盘缺失 → 基准不可得（隔夜跳空对用户不可得，收益须从可成交点起算），
                # 该记录整体跳过（T+5 亦依赖同一基准），留待行情补齐后 backfill 自愈。
                if t1_date is None or (t1_idx := stock_pos.get(t1_date)) is None:
                    continue
                t1_row = df_quotes.iloc[t1_idx]
                # D2-3 数据完整性门控：行存在但涨跌幅缺失（停牌保留行/脏数据）→ 悬空不标。
                if "pct_chg" in t1_row.index and bool(pd.notna(t1_row["pct_chg"])) is False:
                    continue
                basis_open_raw = t1_row.get("open")
                basis_open = float(basis_open_raw) if bool(pd.notna(basis_open_raw)) else None
                if basis_open is None or basis_open == 0:
                    continue  # T+1 开盘不可得 → 买入基准未知，无法计算，留 NULL 待自愈
                # MAJOR-02: T+1 一字涨停（开盘价 ≥ 涨停价）无可成交价格，买入不可执行；
                # 其后的「收益」是买不进的纸上收益。标记 UNTRADABLE 终态：不算收益、不打
                # 标签、不计入 UI 统计，仅进 _untradable_count 供用户知情，避免把不可成交
                # 的涨停当作选股命中（方向性偏差：牛市高 beta 股系统性 WIN）。
                up_limit = limit_up_cache.get((ts_code, t1_date))
                if _is_t1_untradable(basis_open, up_limit):
                    updates.append(
                        {
                            "record_id": row["id"],
                            "pct": None,
                            "label": None,
                            "index_pct": None,
                            "benchmark_code": None,
                            "t1_price": None,
                            "t5_pct": None,
                            "t5_price": None,
                            "alpha": None,
                            "review_status": REVIEW_STATUS_UNTRADABLE,
                        }
                    )
                    self._untradable_count += 1
                    logger.info(
                        "[Review] %s: T+1 open %.4f >= up_limit %.4f on %s, marked UNTRADABLE (no return/label).",
                        ts_code,
                        basis_open,
                        up_limit,
                        t1_date.strftime("%Y%m%d"),
                    )
                    continue
                basis_adj_raw = t1_row.get("adj_factor") if has_adj_factor else None
                basis_adj = float(basis_adj_raw) if has_adj_factor and bool(pd.notna(basis_adj_raw)) else None

                t1_pct: float | None = None
                t1_price: float | None = None
                t5_pct: float | None = None
                t5_price: float | None = None

                # T+1（真实交易日 +1）持有收益：T+1 开盘 → T+1 收盘
                t1_ret = _qfq_return_pct(t1_row, basis_open, basis_adj, has_adj_factor)
                t1_pct = round(t1_ret * 100.0, 4) if t1_ret is not None else None
                if "close" in t1_row.index and bool(pd.notna(t1_row["close"])):
                    t1_price = float(t1_row["close"])

                # T+5（真实交易日 +5）持有收益：T+1 开盘 → T+5 收盘
                if t5_date is not None and (t5_idx := stock_pos.get(t5_date)) is not None:
                    t5_row = df_quotes.iloc[t5_idx]
                    t5_ret = _qfq_return_pct(t5_row, basis_open, basis_adj, has_adj_factor)
                    t5_pct = round(t5_ret * 100.0, 4) if t5_ret is not None else None
                    if "close" in t5_row.index and bool(pd.notna(t5_row["close"])):
                        t5_price = float(t5_row["close"])

                if t1_pct is not None:
                    t1_date_val = t1_row["trade_date"]
                    if hasattr(t1_date_val, "date") and callable(t1_date_val.date):
                        t1_date_obj: datetime.date = typing.cast(datetime.date, t1_date_val.date())
                    elif hasattr(t1_date_val, "year"):
                        t1_date_obj = t1_date_val
                    else:
                        t1_date_obj = datetime.datetime.strptime(str(t1_date_val).replace("-", "")[:8], "%Y%m%d").date()  # noqa: DTZ007  归一化后的 YYYYMMDD 业务日期字符串无时区语义

                    # MAJOR-02: 本记录使用的复盘基准（按个股所属板块选择：创业板→创业板指、
                    # 科创板→科创50、主板/其余→配置基准）。
                    used_index = board_benchmark_for(ts_code, index_code)

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
                                "benchmark_code": used_index,
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

                    # RV-01/RV-02/MAJOR-02: 基准侧必须与个股侧同窗口，且基准按板块选择。
                    # 窗口起点取 T+1 开盘、终点取 label 收盘（与个股「T+1 开盘→T+N 收盘」
                    # 同口径，RV-02）。窗口/缓存/缺失去重逻辑收敛到 _window_index_pct，
                    # run_review 与 backfill_horizon_returns 共用，杜绝口径漂移。
                    t1_date_str = t1_date.strftime("%Y%m%d")
                    label_date_str = label_date.strftime("%Y%m%d")
                    index_pct = await self._window_index_pct(
                        used_index, t1_date, label_date, index_caches, index_missing
                    )
                    effective_index = used_index
                    if index_pct is None and used_index != index_code:
                        # MAJOR-02: 板块基准不可得（同步滞后/停牌日缺失）→ 回退配置基准，
                        # 仍产出标签并如实记录 benchmark_code，避免创业板/科创板个股因
                        # 板块指数缺数据而永久停留待补标签。
                        index_pct = await self._window_index_pct(
                            index_code, t1_date, label_date, index_caches, index_missing
                        )
                        if index_pct is not None:
                            effective_index = index_code

                    if index_pct is None:
                        # RV-04: 基准缺失不再丢弃已算好的数值——数值/标签解耦落库：
                        # t5_pct/t5_price 照写，label/alpha/index_pct 置 NULL 表示标签
                        # 未定稿（R21 缺失值语义），status 显式 T1_DONE 留在补标签通道
                        # （get_unlabeled_predictions），基准恢复后由 backfill 补定稿。
                        # benchmark_code 传 None 保持已有值不覆盖（D2-5）。
                        logger.warning(
                            "[Review] %s: Index return unavailable for [%s, %s], staging numeric-only (label pending)",
                            ts_code,
                            t1_date_str,
                            label_date_str,
                        )
                        updates.append(
                            {
                                "record_id": row["id"],
                                "pct": t1_pct,
                                "label": None,
                                "index_pct": None,
                                "benchmark_code": None,
                                "t1_price": t1_price,
                                "t5_pct": t5_pct if self.label_horizon == "t5" else None,
                                "t5_price": t5_price if self.label_horizon == "t5" else None,
                                "alpha": None,
                                "review_status": REVIEW_STATUS_T1_DONE,
                            }
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
                            "benchmark_code": effective_index,
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

        只处理 ``review_status='T1_DONE'`` 的记录，分两类（RV-04）：A 类缺 ``t5_pct``
        数值（全量计算）；B 类数值已齐但标签未定稿（``alpha IS NULL``，基准缺失时
        数值-only 解耦写入的记录 + #1097 迁移重置的历史行）——仅补定稿标签。
        未满 horizon / 停牌缺行 / 复权计算失败的记录保持 NULL，次日重试；基准
        缺失时 A 类仍写数值（解耦），B 类留待重试。与 ``_qfq_return_pct`` 共享
        复权口径，避免预测当天与回填口径不一致。
        """
        if horizon <= 0:
            raise ValueError("[Review] backfill horizon must be positive")

        # RV-04: A 类（缺 t5_pct 数值）与 B 类（数值已齐、标签未定稿：基准缺失时
        # 数值-only 解耦写入的记录 + #1097 迁移重置的历史行）分池取数、各自独立
        # LIMIT 后合并处理——合并池会按 trade_date asc 让历史 B 类堆积排满单一
        # LIMIT，饿死 A 类新记录（对抗检视）。B 类行带 t5_pct 非 None 标记。
        candidates = await self.cache.screener_dao.get_unfilled_horizon_predictions()
        candidates += await self.cache.screener_dao.get_unlabeled_predictions()
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
        # RV-04: 基准经降级链解析（配置基准不可得 → MAJOR_INDICES 首个可得）。
        index_code = await self._resolve_benchmark(min_t0)
        # MAJOR-02: 复盘基准按个股所属板块选择（与 run_review 同口径），仅预取本批用到的基准。
        needed_indices = {board_benchmark_for(c["ts_code"], index_code) for c in candidates}
        index_caches, index_missing = await self._prefetch_benchmark_caches(
            index_code, needed_indices, min_t0, max_quote_date
        )
        # MAJOR-02: T+1 可成交性守卫数据（区间涨跌停价）。
        limit_up_cache = await self._prefetch_limit_up_cache(min_t0, max_quote_date)

        updates: list[dict] = []
        # RV-04: B 类（数值已齐、标签待定稿）的补标签更新，走 _batch_finalize_labels。
        label_updates: list[dict] = []
        # MAJOR-02: T+1 一字涨停不可成交的记录 → 终态 UNTRADABLE（走 _batch_update_results）。
        untradable_updates: list[dict] = []
        for cand in candidates:
            code = cand["ts_code"]
            t0_date = self._normalize_trade_date(cand["trade_date"])
            t0_mpos = market_pos.get(t0_date)
            # RV-02: 买入基准统一为 T+1 开盘，须先锚定 t1 真实交易日（全市场日历）。
            # T+1 未成熟（日历边界）→ A 类留 NULL 次日重试；B 类（数值已落库）
            # 由 RV-04 通道携带已定稿数值，本处仅推算 t5_date 与索引窗口终点。
            t1_date = (
                market_trade_dates[t0_mpos + 1]
                if t0_mpos is not None and t0_mpos + 1 < len(market_trade_dates)
                else None
            )
            # RV-04: B 类行带 t5_pct（数值已落库，get_unlabeled_predictions 返回该列）
            # → 跳过行情数值计算，仅推算 t5_date 供基准窗口探测；A 类行无该键
            # （get_unfilled_horizon_predictions 不返回 t5_pct）→ 走既有全量计算。
            staged_t5_pct = cand.get("t5_pct")
            t5_price: float | None = None
            if staged_t5_pct is not None:
                t5_pct = float(staged_t5_pct)
                # B 类 t0 必在 market_trade_dates 内（min_t0 取自全部候选的最小值）；
                # t5 数值既已算出，窗口当年即已成熟，此处仅防御日历边界。
                if t0_mpos is None or t0_mpos + horizon >= len(market_trade_dates):
                    continue
                t5_date = market_trade_dates[t0_mpos + horizon]
                # MAJOR-02: 存量 B 类（数值先于本修复落库）可能源自 T+1 一字涨停。行情
                # 可得时用同一守卫复核：不可成交则改判 UNTRADABLE，不再定稿标签（与 A 类
                # /run_review 口径一致）。
                _b_quotes = quotes_by_code.get(code)
                if _b_quotes is not None and not _b_quotes.empty and t1_date is not None:
                    _b_pos = {self._normalize_trade_date(d): int(i) for i, d in enumerate(_b_quotes["trade_date"])}
                    _b_idx = _b_pos.get(t1_date)
                    if _b_idx is not None:
                        _b_open_raw = _b_quotes.iloc[_b_idx].get("open")
                        _b_open = float(_b_open_raw) if bool(pd.notna(_b_open_raw)) else None
                        if _is_t1_untradable(_b_open, limit_up_cache.get((code, t1_date))):
                            untradable_updates.append(
                                {
                                    "record_id": cand["id"],
                                    "pct": None,
                                    "label": None,
                                    "index_pct": None,
                                    "benchmark_code": None,
                                    "t1_price": None,
                                    "t5_pct": None,
                                    "t5_price": None,
                                    "alpha": None,
                                    "review_status": REVIEW_STATUS_UNTRADABLE,
                                }
                            )
                            self._untradable_count += 1
                            continue
            else:
                df_quotes = quotes_by_code.get(code)
                if df_quotes is None or df_quotes.empty:
                    continue
                if t1_date is None:
                    continue  # T+1 尚未成熟，留 NULL 次日重试
                stock_pos = {self._normalize_trade_date(d): int(i) for i, d in enumerate(df_quotes["trade_date"])}
                t0_idx = stock_pos.get(t0_date)
                if t0_idx is None:
                    continue  # t0 无行情行 → 无法按日历定位 T+1，留 NULL
                # RV-02: T+1 为实际可成交日，买入基准取 T+1 开盘复权价。
                # T+1 停牌/缺行/开盘缺失 → 基准不可得，整记录跳过留待自愈（方案风险 1）。
                t1_idx = stock_pos.get(t1_date)
                if t1_idx is None:
                    continue
                t1_row = df_quotes.iloc[t1_idx]
                # D2-3 数据完整性门控：行存在但涨跌幅缺失（停牌保留行/脏数据）→ 悬空不标。
                if "pct_chg" in t1_row.index and bool(pd.notna(t1_row["pct_chg"])) is False:
                    continue
                basis_open_raw = t1_row.get("open")
                basis_open = float(basis_open_raw) if bool(pd.notna(basis_open_raw)) else None
                if basis_open is None or basis_open == 0:
                    continue  # T+1 开盘不可得 → 买入基准未知，无法计算，留 NULL
                # MAJOR-02: 与 run_review 同一 T+1 可成交性守卫——T+1 一字涨停（开盘 ≥ 涨停）
                # 个股无可成交价格，不计算 T+5、不打标签，改判 UNTRADABLE 终态（与 run_review
                # 口径一致，避免两通道漂移）。
                up_limit = limit_up_cache.get((code, t1_date))
                if _is_t1_untradable(basis_open, up_limit):
                    untradable_updates.append(
                        {
                            "record_id": cand["id"],
                            "pct": None,
                            "label": None,
                            "index_pct": None,
                            "benchmark_code": None,
                            "t1_price": None,
                            "t5_pct": None,
                            "t5_price": None,
                            "alpha": None,
                            "review_status": REVIEW_STATUS_UNTRADABLE,
                        }
                    )
                    self._untradable_count += 1
                    continue
                basis_adj_raw = t1_row.get("adj_factor") if has_adj_factor else None
                basis_adj = float(basis_adj_raw) if has_adj_factor and bool(pd.notna(basis_adj_raw)) else None

                if t0_mpos is None or t0_mpos + horizon >= len(market_trade_dates):
                    continue  # T+horizon 尚未成熟，留 NULL 次日重试
                t5_date = market_trade_dates[t0_mpos + horizon]
                t5_idx = stock_pos.get(t5_date)
                if t5_idx is None:
                    continue  # T+5 日停牌/缺行 → 数据不可得，不伪造
                t5_row = df_quotes.iloc[t5_idx]
                ret = _qfq_return_pct(t5_row, basis_open, basis_adj, has_adj_factor)
                if ret is None:
                    continue
                t5_close_raw = t5_row.get("close")
                t5_price = float(t5_close_raw) if bool(pd.notna(t5_close_raw)) else None
                t5_pct = round(ret * 100.0, 4)

            # D4-M4: T+5 成熟回填时同步定稿 T+5 窗口标签。run_review 在 T+5
            # 未成熟时仅打 DRAW 占位（status=T1_DONE），此处补齐 T+5 数值并
            # 以 T+5 超额定稿 WIN/LOSS/DRAW（与 run_review 共用 _classify_alpha）。
            # RV-01/RV-02: 基准侧取 T+1 开盘→T+5 收盘的指数窗口累计收益（与个股
            # t5_pct 同窗口同口径），而非 T+5 当日单日涨跌幅。窗口端点缺任一
            # （含本地库+API 均不可得）均视为不可标。
            if t1_date is None:
                # B 类数值已落库但在日历边界（极端防御）：无 t1 无法定稿窗口
                if staged_t5_pct is not None:
                    continue
                continue
            t1_date_str = t1_date.strftime("%Y%m%d")
            t5_date_str = t5_date.strftime("%Y%m%d")
            # MAJOR-02: 基准按板块选择（创业板→创业板指、科创板→科创50、主板/其余→配置
            # 基准）；窗口/缺失去重逻辑收敛到 _window_index_pct（与 run_review 共用）。
            used_index = board_benchmark_for(code, index_code)
            index_pct = await self._window_index_pct(used_index, t1_date, t5_date, index_caches, index_missing)
            effective_index = used_index
            if index_pct is None and used_index != index_code:
                # MAJOR-02: 板块基准不可得 → 回退配置基准（与 run_review 同口径）。
                index_pct = await self._window_index_pct(index_code, t1_date, t5_date, index_caches, index_missing)
                if index_pct is not None:
                    effective_index = index_code
            if index_pct is None:
                if staged_t5_pct is None:
                    # RV-04: A 类基准缺失 → 数值-only 解耦落库（label=None →
                    # backfill_t5_prediction 置 T1_DONE），次日起作为 B 类候选
                    # 重访补标签；不再丢弃已算好的 t5_pct（数值/标签解耦）。
                    logger.warning(
                        "[Review] T+5 backfill: %s: Index return unavailable for [%s, %s], staging numeric-only (label pending)",
                        code,
                        t1_date_str,
                        t5_date_str,
                    )
                    updates.append(
                        {
                            "record_id": cand["id"],
                            "t5_pct": t5_pct,
                            "t5_price": t5_price,
                            "label": None,
                            "index_pct": None,
                            "benchmark_code": None,
                            "alpha": None,
                        }
                    )
                else:
                    # B 类：数值已在库，仅标签待补 → 留待基准恢复后次日重试（无数据丢失）。
                    logger.warning(
                        "[Review] T+5 backfill: %s: Index return unavailable for [%s, %s], label still pending",
                        code,
                        t1_date_str,
                        t5_date_str,
                    )
                continue
            alpha = round(t5_pct - index_pct, 4)
            label = self._classify_alpha(alpha)

            if staged_t5_pct is not None:
                # RV-04: B 类补定稿标签（数值不动，finalize WHERE alpha IS NULL 幂等）。
                label_updates.append(
                    {
                        "record_id": cand["id"],
                        "label": label,
                        "index_pct": index_pct,
                        "benchmark_code": effective_index,
                        "alpha": alpha,
                    }
                )
            else:
                updates.append(
                    {
                        "record_id": cand["id"],
                        "t5_pct": t5_pct,
                        "t5_price": t5_price,
                        "label": label,
                        "index_pct": index_pct,
                        "benchmark_code": effective_index,
                        "alpha": alpha,
                    }
                )

        if updates:
            await self._batch_backfill_t5(updates)
        if label_updates:
            await self._batch_finalize_labels(label_updates)
        if untradable_updates:
            # MAJOR-02: 不可成交记录走通用更新通道（支持 review_status=UNTRADABLE 终态）。
            await self._batch_update_results(untradable_updates)

        total = len(updates) + len(label_updates) + len(untradable_updates)
        logger.info(
            "[Review] T+%d backfill completed: %s records updated (%s labels finalized, %s untradable).",
            horizon,
            total,
            len(label_updates),
            len(untradable_updates),
        )
        return total

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

        # MAJOR-02: T+1 可成交性守卫数据（区间涨跌停价），与 run_review 同口径。
        limit_up_cache = await self._prefetch_limit_up_cache(min_t0, max_quote_date)

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
                continue  # t0 无行情行 → 无法按日历定位 T+1，留 NULL

            t0_mpos = market_pos.get(t0_date)
            if t0_mpos is None or t0_mpos + 1 >= len(market_trade_dates):
                continue  # T+1 尚未成熟，留 NULL 次日重试
            t1_date = market_trade_dates[t0_mpos + 1]
            # RV-02: T+1 为实际可成交日（回测默认 next_open），买入基准取 T+1 开盘复权价。
            # T+1 停牌/缺行/开盘缺失 → 基准不可得，整记录跳过留待自愈（方案风险 1）。
            t1_idx = stock_pos.get(t1_date)
            if t1_idx is None:
                continue  # T+1 日停牌/缺行 → 数据不可得，不伪造
            t1_row = df_quotes.iloc[t1_idx]
            # D2-3 数据完整性门控：行存在但涨跌幅缺失（停牌保留行/脏数据）→ 悬空不标。
            if "pct_chg" in t1_row.index and bool(pd.notna(t1_row["pct_chg"])) is False:
                continue
            basis_open_raw = t1_row.get("open")
            basis_open = float(basis_open_raw) if bool(pd.notna(basis_open_raw)) else None
            if basis_open is None or basis_open == 0:
                continue  # T+1 开盘不可得 → 买入基准未知，无法计算，留 NULL
            # MAJOR-02: 与 run_review 同一 T+1 可成交性守卫——T+1 一字涨停（开盘 ≥ 涨停）
            # 个股无可成交价格，不回填 T+1、不当 DRAW 占位，改判 UNTRADABLE 终态（不再进入
            # T+5 回填通道，避免把买不进的涨停当作候选）。
            up_limit = limit_up_cache.get((code, t1_date))
            if _is_t1_untradable(basis_open, up_limit):
                updates.append(
                    {
                        "record_id": cand["id"],
                        "pct": None,
                        "label": None,
                        "index_pct": None,
                        "benchmark_code": None,
                        "t1_price": None,
                        "t5_pct": None,
                        "t5_price": None,
                        "alpha": None,
                        "review_status": REVIEW_STATUS_UNTRADABLE,
                    }
                )
                self._untradable_count += 1
                continue
            basis_adj_raw = t1_row.get("adj_factor") if has_adj_factor else None
            basis_adj = float(basis_adj_raw) if has_adj_factor and bool(pd.notna(basis_adj_raw)) else None
            # RV-02: T+1 当日持有收益 = T+1 开盘 → T+1 收盘（不复用 T0 收盘基准，
            # 否则隔夜跳空被计入用户本就不可得的收益段）。
            ret = _qfq_return_pct(t1_row, basis_open, basis_adj, has_adj_factor)
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
                    # MAJOR-02: 与 run_review 的 T+1 staged 分支同口径——记录该股所属
                    # 板块基准（创业板→创业板指、科创板→科创50、主板/其余→配置基准），
                    # 供后续 T+5 定稿沿用，避免两通道 benchmark_code 漂移。
                    "benchmark_code": board_benchmark_for(code, index_code),
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

    @log_async_operation(operation_name="expire_stale", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def expire_stale_pending(self, lookback_trade_days: int = 60) -> int:
        """RV-11: 常驻过期清扫——清理超窗且从未完成 T+1 的僵尸 PENDING 记录。

        迁移 0026 只做了一次性存量清理，本方法负责迁移后的增量：把
        ``review_status IN (PENDING, NULL)`` 且 ``t1_pct IS NULL`` 且
        ``trade_date`` 早于最近 ``lookback_trade_days`` 个交易日的记录置
        ``EXPIRED`` 终态，防止僵尸 PENDING 无限累积挤占
        ``get_pending_reviews`` 的 LIMIT 500 配额（积累到上限后复盘池
        被完全堵死）。语义与迁移 0026 逐字对齐，常量复用默认值避免口径漂移。
        返回清理条数（0 = 无僵尸，正常）。
        """
        return await self.cache.screener_dao.expire_stale_pending(lookback_trade_days=lookback_trade_days)

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

    async def _batch_finalize_labels(self, label_updates: list[dict]) -> None:
        """RV-04: 单事务批量补定稿标签（对齐 _batch_backfill_t5 的事务与逐条降级语义）。"""
        dao = self.cache.screener_dao
        engine = self.cache.engine
        if engine is None:
            logger.error("[Review] Engine not available for label finalization.")
            return

        try:
            async with engine.begin() as conn:
                for u in label_updates:
                    await dao.finalize_prediction_label(
                        u["record_id"],
                        label=u["label"],
                        index_pct=u["index_pct"],
                        benchmark_code=u["benchmark_code"],
                        alpha=u["alpha"],
                        conn=conn,
                    )
        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，主路径必须上抛避免被吞没.
            raise
        except Exception as e:
            logger.error(
                "[Review] Batch label finalization failed, falling back to individual updates: %s",
                safe_error(e),
            )
            for u in label_updates:
                try:
                    await dao.finalize_prediction_label(
                        u["record_id"],
                        label=u["label"],
                        index_pct=u["index_pct"],
                        benchmark_code=u["benchmark_code"],
                        alpha=u["alpha"],
                    )
                except EngineDisposedError:
                    # R5 一致性：fallback 路径同样必须上抛（与主路径对齐）.
                    raise
                except Exception as inner_e:
                    logger.error(
                        "[Review] Individual label finalization also failed for record %s: %s",
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

    async def get_learning_context(
        self,
        limit: int | None = 3,
        as_of: datetime.date | datetime.datetime | None = None,
        strategy_name: str | None = None,
    ) -> str:
        """兼容入口：返回学习上下文 XML 字符串（向后兼容既有调用方）。

        注入样本的可追溯元数据（样本 ID、学习开关状态）经
        :meth:`get_learning_context_with_meta` 获取；本方法丢弃 meta。
        """
        xml, _meta = await self.get_learning_context_with_meta(
            limit=limit,
            as_of=as_of,
            strategy_name=strategy_name,
        )
        return xml

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_learning_context_with_meta(
        self,
        limit: int | None = 3,
        as_of: datetime.date | datetime.datetime | None = None,
        strategy_name: str | None = None,
    ) -> tuple[str, dict]:
        """
        Extract 'Best Wins' and 'Worst Losses' for Prompt Injection.
        Returns ``(xml, meta)``: formatted XML string for few-shot learning plus the
        traceability metadata of this injection (sample IDs + learning switch state).

        P0-5 fix: as_of parameter prevents look-ahead bias. When provided,
        only predictions with trade_date < as_of are included, preventing
        future data from leaking into historical replay contexts.

        D4-M3: ``strategy_name`` 非空时只取同策略样本，避免跨策略 few-shot 混用导致
        模型学到错误的「特征 → 收益」映射。

        D4-M3 修复（本方法核心变更）：
        - **不注入 ``ai_reason``**：T0 事前理由不是「成功原因」，回灌会造成自我强化
          确认偏差（AI-03 读取侧同时关闭了该自由文本注入通道）。
        - **样本只含事后可观察事实**：股票代码、板块、T0 估值、T+5 收益、同期市场收益、
          T+5 超额 Alpha（收益字段与 alpha 同为 T+5 窗口，口径不再混排）。
        - **充足门槛与项目统计口径一致**：``total >= REVIEW_MIN_SAMPLE``（30）；不足时
          **不注入尾部样本**，只注入总体统计 + 样本量不足声明（尾部样本最易被极端行情主导）。
        - 返回 ``meta`` 供调用方写入 ``params_snapshot``，使「本次注入了哪些样本」
          可事后追溯（对抗场景：用户发现 AI 风格漂移却无法归因）。

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
        wins: list[dict] = []
        losses: list[dict] = []
        stats: dict | None = None
        # 缺省哨兵（R21）：样本文本中缺失的可观察事实以 N/A 呈现，不伪造具体数值。
        na = I18n.get("review_ctx_na")

        def _num(value: typing.Any) -> float | None:
            try:
                if value is None or pd.isna(value):
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        try:
            # D4-M3: 先取总体统计以判定样本量是否充足；不足时不查询、不注入尾部样本。
            stats = await self.cache.screener_dao.get_learning_context_stats(
                as_of=as_of,
                strategy_name=strategy_name,
            )
            total = _num(stats.get("total")) if isinstance(stats, dict) else None
            sufficient = total is not None and int(total) >= REVIEW_MIN_SAMPLE

            if sufficient:
                for is_win, bucket in ((True, wins), (False, losses)):
                    df_tail = await self.cache.screener_dao.get_learning_context(
                        limit=limit or 3,
                        is_win=is_win,
                        as_of=as_of,
                        strategy_name=strategy_name,
                    )
                    if df_tail is None or df_tail.empty:
                        continue
                    for _, row in df_tail.iterrows():
                        alpha = _num(row.get("alpha"))
                        t5 = _num(row.get("t5_pct"))
                        pe = _num(row.get("pe_ttm"))
                        index_pct = _num(row.get("index_pct"))
                        benchmark = row.get("benchmark_code")
                        sample_id = _num(row.get("id"))
                        bucket.append(
                            {
                                "id": int(sample_id) if sample_id is not None else None,
                                "code": row["ts_code"],
                                # name/industry 为第三方/DB 自由文本，入 Prompt 前中和
                                # （AI-03：剥离零宽字符、转义尖括号、PII 脱敏）。
                                "name": neutralize_external_text(str(row.get("name") or "")),
                                "industry": neutralize_external_text(str(row.get("industry") or "")) or na,
                                "pe": f"{pe:.1f}" if pe is not None else na,
                                "pct": f"{t5:+.1f}" if t5 is not None else na,
                                "alpha": f"{alpha:+.1f}" if alpha is not None else na,
                                "index": f"{index_pct:+.1f}" if index_pct is not None else na,
                                "benchmark": str(benchmark)
                                if benchmark is not None and not pd.isna(benchmark)
                                else None,
                                "score": row.get("ai_score"),
                            },
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

        total_val = _num(stats.get("total")) if isinstance(stats, dict) else None
        sufficient = total_val is not None and int(total_val) >= REVIEW_MIN_SAMPLE
        sample_ids = [s["id"] for s in (*wins, *losses) if s["id"] is not None]
        meta = {
            "enabled": True,
            "sufficient": sufficient,
            "injected": bool(sample_ids),
            "total": int(total_val) if total_val is not None else None,
            "sample_ids": sample_ids,
            "as_of": as_of.isoformat() if as_of is not None else None,
            "strategy_name": strategy_name,
        }

        # Build XML
        xml = "<history_context>\n"

        # D4-M3 偏差二/三：附总体统计 + 样本量充足性声明。
        # 仅统计口径与提示语对模型可见，不改变 top WIN/LOSS 样本本身。
        if total_val is not None and total_val > 0:
            assert stats is not None  # total_val 仅由 dict 形态的 stats 派生，此处必然非 None
            # 胜率 = WIN/(WIN+LOSS)，DRAW 不计入分母（与 get_strategy_review_stats 消费端口径一致）。
            labeled = stats["win_cnt"] + stats["loss_cnt"]
            win_rate = stats["win_cnt"] / labeled if labeled else None
            median_str = f"{float(stats['alpha_median']):+.1f}" if stats["alpha_median"] is not None else "N/A"
            win_rate_str = f"{win_rate * 100:.1f}%" if win_rate is not None else "N/A"
            xml += f"  {I18n.get('review_ctx_stats', total=stats['total'], median=median_str, win_rate=win_rate_str)}\n"
            if not sufficient:
                # 样本量不足：只注入总体统计，不注入尾部样本（避免尾部被极端行情主导）。
                xml += f"  {I18n.get('review_ctx_low_sample')}\n"
            elif wins or losses:
                xml += f"  {I18n.get('review_ctx_tail_note')}\n"

        if wins:
            xml += f"  [{I18n.get('review_ctx_positive')}]\n"
            for w in wins:
                benchmark = w["benchmark"] or I18n.get("review_ctx_benchmark_na")
                xml += (
                    f"  - [{benchmark}] "
                    f"{I18n.get('review_ctx_win_detail', code=w['code'], name=w['name'], industry=w['industry'], pe=w['pe'], pct=w['pct'], index=w['index'], alpha=w['alpha'])}\n"
                )

        if losses:
            xml += f"  [{I18n.get('review_ctx_negative')}]\n"
            for loss in losses:
                benchmark = loss["benchmark"] or I18n.get("review_ctx_benchmark_na")
                xml += (
                    f"  - [{benchmark}] "
                    f"{I18n.get('review_ctx_loss_detail', code=loss['code'], name=loss['name'], industry=loss['industry'], pe=loss['pe'], pct=loss['pct'], index=loss['index'], alpha=loss['alpha'])}\n"
                )

        if not wins and not losses:
            xml += f"  {I18n.get('review_ctx_none')}\n"

        xml += "</history_context>"
        return xml, meta

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
                        review_status=u.get("review_status"),
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
                        review_status=u.get("review_status"),
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

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _prefetch_index_cache(
        self,
        index_code: str | None,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> dict[str, tuple[float, float]]:
        """RV-01/RV-02: 批量预取基准指数开收盘点位到 {YYYYMMDD: (open, close)} 缓存。

        run_review 与 backfill_horizon_returns 共用，避免两处各自实现同一预取逻辑。
        RV-01 起缓存 ``close``（收盘点位）供窗口累计收益换算，RV-02 起同时缓存
        ``open``（开盘点位）——基准窗口起点由 T0 收盘改为 T+1 开盘（与回测默认
        next_open 对齐），需窗口起点 open 与终点 close 两点。
        与旧逻辑一致：预取失败仅告警（非 system 级），
        由调用方的单条兜底（_resolve_index_quote）补缺失日期。

        D3-m2: 日期参数全程使用 date 对象（与 DAT-26「DAO 边界显式转 date」方向
        统一），不再经 str(date) 隐式转换后再由 DAO 转回，避免 "2024-01-05" 与
        "20240105" 两种日期格式在库内并存造成的摩擦。
        """
        index_cache: dict[str, tuple[float, float]] = {}
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
                    raw_open = i_row.get("open")
                    raw_close = i_row.get("close")
                    # RV-01/RV-02: 仅缓存 open/close 均有效的日期；缺失日期不写 key，
                    # 保证调用方逐日探测（本地库 → API 兜底）得以触发（旧实现对缺失
                    # 日期写 key 会让后续 `key not in cache` 短路 API 兜底）。
                    open_ok = raw_open is not None and pd.notna(raw_open) is True
                    close_ok = raw_close is not None and pd.notna(raw_close) is True
                    if open_ok and close_ok:
                        index_cache[dt_str] = (float(raw_open), float(raw_close))
                logger.info(
                    "[Review] Bulk loaded %d days of index open/close for %s.",
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

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _prefetch_limit_up_cache(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> dict[tuple[str, datetime.date], float]:
        """MAJOR-02: 批量预取区间涨跌停价，键 ``(ts_code, trade_date)`` → up_limit。

        run_review / backfill_horizon_returns / backfill_t1_returns 共用，供 T+1
        可成交性守卫（``_is_t1_untradable``）判定一字涨停。涨跌停价为名义价，与个股
        open 同口径。预取失败（含测试替身未提供 stk_limit_dao）仅告警并返回空缓存 →
        守卫降级为「不判定」，不阻塞复盘；EngineDisposedError 与 system 级异常上抛（R5）。
        """
        limit_cache: dict[tuple[str, datetime.date], float] = {}
        try:
            df_limit = await self.cache.stk_limit_dao.get_stk_limit_range(
                start_date.strftime("%Y%m%d"),
                end_date.strftime("%Y%m%d"),
            )
            if df_limit is not None and not df_limit.empty:
                for _, l_row in df_limit.iterrows():
                    up_raw = l_row.get("up_limit")
                    if up_raw is None or pd.notna(up_raw) is not True:
                        continue
                    trade_date = self._normalize_trade_date(l_row.get("trade_date"))
                    limit_cache[(str(l_row.get("ts_code")), trade_date)] = float(up_raw)
        except asyncio.CancelledError:
            logger.warning("[Review] Cancelled during stk_limit bulk pre-fetch.")
            raise
        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，主路径必须上抛避免被吞没.
            raise
        except Exception as exc:
            severity = classify_severity(exc, context="db")
            log_classified(
                logger,
                exc,
                "db",
                "[Review] Failed to bulk pre-fetch stk_limit (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
        if not limit_cache:
            logger.warning(
                "[Review] stk_limit unavailable for [%s, %s]; T+1 tradability guard disabled.",
                start_date,
                end_date,
            )
        return limit_cache

    async def _prefetch_benchmark_caches(
        self,
        default_index: str,
        needed_indices: set[str],
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> tuple[dict[str, dict[str, tuple[float, float]]], dict[str, set[str]]]:
        """MAJOR-02: 预取本批复盘实际用到的各基准指数开收盘缓存（含板块基准）。

        默认基准必取；板块基准（创业板指/科创50）仅当本批存在对应板块个股时才取，
        避免对无创业板/科创板候选的批次做无用查询。返回 ``(caches, missing)`` 两个
        以 index_code 分桶的字典，供 ``_window_index_pct`` 逐股按板块基准取窗口收益。
        """
        caches: dict[str, dict[str, tuple[float, float]]] = {}
        missing: dict[str, set[str]] = {}
        for idx in dict.fromkeys([default_index, *sorted(needed_indices)]):
            caches[idx] = await self._prefetch_index_cache(idx, start_date, end_date)
            missing[idx] = set()
        return caches, missing

    async def _window_index_pct(
        self,
        index_code: str,
        start_date: datetime.date,
        end_date: datetime.date,
        index_caches: dict[str, dict[str, tuple[float, float]]],
        index_missing: dict[str, set[str]],
    ) -> float | None:
        """RV-02/MAJOR-02: 指定指数在 ``[start, end]`` 的窗口累计收益（百分点）。

        窗口起点取 start 日开盘、终点取 end 日收盘（与个股「T+1 开盘 → label 收盘」
        同口径，RV-02）。端点缺任一（本地库+API 均不可得）返回 None，由调用方按 RV-04
        数值/标签解耦处理。index_caches/index_missing 以 index_code 分桶，避免不同
        板块基准相互污染缓存。run_review 与 backfill_horizon_returns 共用，杜绝口径漂移。
        """
        cache = index_caches.setdefault(index_code, {})
        missing = index_missing.setdefault(index_code, set())
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        for _d_str, _d in ((start_str, start_date), (end_str, end_date)):
            # RV-01 修复点：缓存存 None（本地库该日无数据）时也须尝试 API 兜底，
            # 仅凭 key 存在会短路兜底路径；index_missing 去重避免重复探测同一缺失日期。
            if _d_str not in cache and _d_str not in missing:
                _d_quote = await self._resolve_index_quote(index_code, _d)
                if _d_quote is None:
                    missing.add(_d_str)
                else:
                    cache[_d_str] = _d_quote
        _start_quote = cache.get(start_str)
        _end_quote = cache.get(end_str)
        if _start_quote is None or _end_quote is None:
            return None
        return _index_window_return_pct(_start_quote[0], _end_quote[1])

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _resolve_index_quote(
        self,
        index_code: str | None,
        trade_date: datetime.date,
    ) -> tuple[float, float] | None:
        """RV-01/RV-02: 单条兜底解析指定交易日的基准指数 (open, close)，失败返回 None。

        run_review 与 backfill_horizon_returns 共用，口径一致：先查缓存（本地库），
        缺失再降级 Tushare API；API 不可得 / 数据缺失返回 None，由调用方决定跳过。
        RV-01 起取 close（收盘点位）供窗口累计收益换算，RV-02 起同时取 open——
        窗口起点为 T+1 开盘，open 或 close 任一侧缺失即整点 None（窗口无意义，
        对抗检视：避免以 (None, close) 进窗口导致 TypeError）。
        """
        trade_date_str = trade_date.strftime("%Y%m%d")
        try:
            df_idx = await self.cache.quote_dao.get_index_daily(
                ts_code=index_code,
                trade_date=trade_date,
            )
            if df_idx is not None and not df_idx.empty:
                raw_open = df_idx.iloc[0].get("open")
                raw_close = df_idx.iloc[0]["close"]
                open_ok = raw_open is not None and pd.notna(raw_open) is True
                close_ok = raw_close is not None and pd.notna(raw_close) is True
                if open_ok and close_ok:
                    return (float(raw_open), float(raw_close))
                return None
            try:
                df_idx_api = await self.api.get_index_daily(
                    ts_code=index_code,
                    start_date=trade_date_str,
                    end_date=trade_date_str,
                )
                if df_idx_api is not None and not df_idx_api.empty:
                    raw_open = df_idx_api.iloc[0].get("open")
                    raw_close = df_idx_api.iloc[0]["close"]
                    open_ok = raw_open is not None and pd.notna(raw_open) is True
                    close_ok = raw_close is not None and pd.notna(raw_close) is True
                    if open_ok and close_ok:
                        return (float(raw_open), float(raw_close))
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

    async def _resolve_benchmark(self, probe_date: datetime.date) -> str:
        """RV-04: 解析实际使用的基准指数，配置基准不可得时沿 MAJOR_INDICES 降级。

        候选链 = [配置基准, *MAJOR_INDICES]（去重保序）。对每个候选探测
        ``probe_date``（本批最老记录日）开收盘点位可得性（本地库 → API 兜底，
        与 ``_resolve_index_quote`` 同链路），返回首个可得的候选并记入
        ``benchmark_code``；全不可得时返回配置基准——数值照常落库
        （RV-04 数值/标签解耦），标签停留待补，诊断供 job 呈现。

        局限（有意近似）：以 probe_date 单日可得性为降级信号；历史窗口中段
        缺失不触发降级，由 backfill_horizon_returns 的 B 类补标签通道按日重试兜底。
        """
        configured = ConfigHandler.get_config("benchmark_index", DEFAULT_BENCHMARK_INDEX)
        if not isinstance(configured, str) or not configured:
            configured = DEFAULT_BENCHMARK_INDEX
        for candidate in dict.fromkeys([configured, *MAJOR_INDICES]):
            # 探针异常（system 级已在 _resolve_index_quote 内 critical 记录）视为
            # 候选不可得、继续降级链——与行级「异常吞没、review 继续」语义一致；
            # EngineDisposedError 上抛（R5：disposed 引擎上不再执行降级探测）。
            try:
                probe_quote = await self._resolve_index_quote(candidate, probe_date)
            except EngineDisposedError:
                raise
            except Exception:
                continue
            if probe_quote is not None:
                if candidate != configured:
                    self._benchmark_diag = I18n.get(
                        "review_benchmark_degraded",
                        configured=configured,
                        fallback=candidate,
                    )
                    logger.warning(
                        "[Review] Benchmark %s unavailable on %s, degraded to %s (RV-04 fallback chain)",
                        configured,
                        probe_date.strftime("%Y%m%d"),
                        candidate,
                    )
                else:
                    # 配置基准直接命中时清除过时诊断（同实例先降级后恢复的场景，
                    # 防 job 拼接上一次运行的残留降级说明）。
                    self._benchmark_diag = None
                return candidate
        self._benchmark_diag = I18n.get("review_benchmark_missing", benchmark=configured)
        logger.warning(
            "[Review] Benchmark %s and all MAJOR_INDICES unavailable on %s; "
            "numeric-only staging, labels pending (RV-04)",
            configured,
            probe_date.strftime("%Y%m%d"),
        )
        return configured

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def save_results(
        self,
        strategy_name: str | None,
        df: pd.DataFrame,
        trade_date: datetime.date | datetime.datetime | pd.Timestamp | str | None = None,
        run_id: str | None = None,
        params_snapshot: str | dict[str, typing.Any] | None = None,
        exec_warnings: Sequence[Message] | None = None,
    ) -> int:
        """
        Save screening results to history for future review.
        Persists the full strategy execution snapshot including financial indicators and AI thinking.

        Args:
            strategy_name: Name of the strategy that produced the results.
            df: DataFrame of screening results.
            trade_date: The trading date being analyzed (not the current natural date).
                        If omitted, a single unique df["trade_date"] value may be used.
            exec_warnings: 策略执行期 warnings 通道（``context["warnings"]``）。``None``
                        表示调用方无执行上下文（落库为 SQL NULL，历史回看走「未记录」
                        分支，R21/BT-03）；空序列表示「已记录且无警告」。每行归因自动
                        取自 df 的 ``_filter_attribution`` 列。

        Returns:
            Number of records actually persisted. ``0`` means nothing was written
            (empty df, or every row filtered out by ai_status — e.g. budget exceeded /
            policy not acknowledged / all AI failures). Callers MUST treat ``0`` as
            "no reviewable result produced", never as success (D4-C1).
            MINOR-03: AI 明确否决（``ai_status == "rejected"``，score==0）的行照常落库并
            参与复盘，故「候选全被否决」不再返回 0；被排除的仅限「未产出结论」的状态。
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

        # CRITICAL-02: 执行期 warnings 落库一次（同一 run 全体行共享），每行归因逐行取。
        exec_warnings_value = serialize_exec_warnings(exec_warnings)

        # D4-M3 可追溯：若结果 df 携带本批次学习上下文元数据列（``learning_context_meta``，
        # 由 AIStrategyMixin.run_ai_analysis 按批次附加；或 stock_analysis 直接分析路径逐行附加），
        # 合并进 params_snapshot，使「本次注入了哪些样本 + 学习开关状态」随结果落库、可事后归因。
        # 缺列 / 全为缺失值时不动 params_snapshot（不伪造记录）。
        learning_meta: dict | None = None
        if "learning_context_meta" in df.columns:
            non_null_meta = df["learning_context_meta"].dropna()
            if not non_null_meta.empty and isinstance(non_null_meta.iloc[0], dict):
                learning_meta = dict(non_null_meta.iloc[0])
        if learning_meta is not None:
            base_params = params_snapshot_value if isinstance(params_snapshot_value, dict) else {}
            params_snapshot_value = {**base_params, "learning_context": learning_meta}

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

            # D3-7 / MINOR-03: 只写入「AI 已产出结论」的行——
            #   - analyzed：AI 给出正分；
            #   - rejected：AI 明确否决（score==0，见 _build_result_row）。
            # 否决行照常落库并进入 T+1/T+5 复盘，使学习样本覆盖「正确的否决」（此前仅
            # analyzed 入库，学习样本在方向上只有一半）；否决语义由 ai_score==0 与
            # conclusion_label=="reject" 共同承载。
            # failed（分析未完成/无分数）及 budget_exceeded / budget_unpriced_prompt /
            # policy_not_acknowledged 等「未执行分析」状态一律不入库——避免把「未分析」
            # 伪装成「已否决」（R21），也不让未产出结论的行污染学习闭环。
            ai_status = row.get("ai_status")
            if ai_status is not None and ai_status not in ("analyzed", "rejected"):
                continue  # failed/未执行分析 不入数据库

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

            # MAJOR-01（输出契约统一）：模型结论枚举随结果落库（写入上游已由
            # validate_ai_analysis_response 规范化为合法枚举或 None）。缺失/NaN 用 None
            # 哨兵（R21），不填业务上合法的具体标签。
            conclusion_label = _s(row, "conclusion_label", None)

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
                    "conclusion_label": conclusion_label,
                    "params_snapshot": params_snapshot_value,
                    "exec_warnings": exec_warnings_value,
                    "filter_attribution": _parse_filter_attribution(row.get(_ATTRIBUTION_COLUMN)),
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
