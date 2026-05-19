import datetime
import logging
import typing

import pandas as pd
import polars as pl

from data.persistence.quality_gate import QualityGateError, QualityTier, require_quality
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.utils import StrategyContext
from strategies.base_strategy import BaseStrategy, register_strategy
from core.i18n import I18n
from utils.technical_analysis import TechnicalAnalysis

logger = logging.getLogger(__name__)


@register_strategy("oversold")
class OversoldStrategy(BaseStrategy, AIStrategyMixin):
    """
    RSI Oversold Rebound Strategy (AI-Enhanced)

    Level 1 (Math):  Filter stocks with RSI(N) < threshold (both configurable via UI).
    Level 2 (AI):    Parallel AI analysis on top candidates to distinguish
                     "golden pit" rebounds from "falling knife" traps.
    """

    required_context_keys = ["screening_data"]
    required_tables = ["daily_quotes"]

    @property
    def required_history_days(self):
        return 120

    def __init__(self):
        super().__init__("strategy_oversold_name", "strategy_oversold_desc")

        self.register_context_builder("turnover", self._build_turnover_context)
        self.register_context_builder("sector", self._build_sector_context)
        self.register_context_builder("market", self._build_market_context)
        self.register_context_builder("support", self._build_support_context)

    def should_include_global_context(self) -> bool:
        """超跌反弹优先关注个股自身修复信号，避免被美股噪音干扰。"""
        return False

    def should_include_learning_context(self) -> bool:
        """超跌反弹避免注入 few-shot 复盘样例，降低命令式偏置。"""
        return False

    # ============================================================
    # Dynamic Parameters — exposed to UI as a slider
    # ============================================================
    def get_parameters(self):
        return [
            {
                "name": "rsi_period",
                "label_key": "param_rsi_period",
                "type": "slider",
                "group": "core_signal",
                "min": 2,
                "max": 30,
                "default": 14,
                "step": 1,
            },
            {
                "name": "rsi_threshold",
                "label_key": "param_rsi_threshold_oversold",
                "type": "slider",
                "group": "core_signal",
                "min": 0,
                "max": 100,
                "default": 30,
                "step": 1,
            },
            {
                "name": "vol_ratio_threshold",
                "label_key": "param_vol_ratio_threshold",
                "type": "slider",
                "group": "volume_confirm",
                "min": 0.8,
                "max": 3.0,
                "default": 1.5,
                "step": 0.1,
            },
        ]

    def get_dynamic_description(self, current_params: dict) -> str:
        """Return description matching current slider params."""
        period = current_params.get("rsi_period", 14)
        threshold = current_params.get("rsi_threshold", 30)
        return I18n.get(
            "strategy_oversold_dynamic_desc",
            f"RSI({period}) < {threshold}",
        ).format(period=period, threshold=threshold)

    def _sort_for_ai(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        rsi_col = None
        for c in df.columns:
            if c.startswith("rsi_"):
                rsi_col = c
                break
        sort_cols = []
        if rsi_col:
            sort_cols.append((rsi_col, True))
        if "amount" in df.columns:
            sort_cols.append(("amount", False))
        elif "vol" in df.columns:
            sort_cols.append(("vol", False))
        if "total_mv" in df.columns:
            sort_cols.append(("total_mv", False))
        if not sort_cols:
            return df
        return df.sort_values(
            [c for c, _ in sort_cols],
            ascending=[a for _, a in sort_cols],
        )

    # ============================================================
    # AI Context Hook — tells the LLM WHY this stock was selected
    # ============================================================
    def get_ai_context(self, row: dict) -> str:
        period = row.get("_rsi_period", 14)
        rsi = row.get(f"rsi_{period}", "N/A")
        threshold = row.get("_rsi_threshold", 30)
        vol_ratio_threshold = row.get("_vol_ratio_threshold", 1.5)
        rsi_feature = row.get("_rsi_feature_text", "")

        context_parts = [
            "该股票由 RSI 超跌反弹策略筛选。",
            (f"当前策略参数: RSI周期={period}, 超卖阈值={threshold}, 量能判定阈值={vol_ratio_threshold}"),
            f"当前 RSI({period}) = {rsi}（阈值 < {threshold}），表明处于极端超卖状态。",
        ]

        if rsi_feature and "暂不解读" not in rsi_feature and "历史数据不足" not in rsi_feature:
            context_parts.append(f"{I18n.get('ai_pattern_feedback')}{rsi_feature}")

        context_parts.append("请评估：这是「黄金坑」反弹机会（如恐慌急跌/钝化），还是基本面恶化导致的「无底洞」下跌？")

        return "\n".join(context_parts)

    # ============================================================
    # Main Filter Logic
    # ============================================================
    @require_quality(QualityTier.SILVER)
    async def filter(self, context: StrategyContext):
        """
        Two-phase filtering:
        Phase 1: RSI math filter (Polars) → top N oversold candidates
        Phase 2: AI analysis (via Mixin) → scored and ranked results
        """
        dep_result = self.check_dependencies(context)
        if dep_result["status"] == "unready":
            logger.warning(
                f"[Strategy] {self.name}: dependencies unready, "
                f"missing_keys={dep_result['missing_keys']}, "
                f"missing_tables={dep_result['missing_tables']}"
            )
            return pd.DataFrame()
        elif dep_result["status"] == "degraded":
            logger.info(f"[Strategy] {self.name}: running in degraded mode, empty_keys={dep_result['empty_keys']}")

        # --- Read dynamic params from UI (with fallback) ---
        params = context.get("params", {})
        rsi_period = params.get("rsi_period", 14)
        rsi_threshold = params.get("rsi_threshold", 30)
        vol_ratio_threshold = params.get("vol_ratio_threshold", 1.5)

        # --- Phase 1: Math Filter ---
        candidates = await self._math_filter(context, rsi_period, rsi_threshold, vol_ratio_threshold)

        if candidates is None or candidates.empty:
            return pd.DataFrame()

        # Inject thresholds into each row so get_ai_context() can use them
        candidates["_rsi_period"] = rsi_period
        candidates["_rsi_threshold"] = rsi_threshold
        candidates["_vol_ratio_threshold"] = vol_ratio_threshold

        logger.info(
            f"[OversoldStrategy] Phase 1 complete: {len(candidates)} candidates (RSI < {rsi_threshold})",
        )

        # --- Phase 2: AI Analysis (via Mixin) ---
        candidates = self._sort_for_ai(candidates)
        return await self.run_ai_analysis(candidates, context)  # type: ignore[arg-type]

    async def _math_filter(
        self,
        context: typing.Any,
        rsi_period: typing.Any,
        rsi_threshold: typing.Any,
        vol_ratio_threshold: typing.Any,
    ):
        """
        Phase 1: Pure mathematical RSI filtering using Polars.
        Returns a Pandas DataFrame of candidates sorted by RSI ascending.
        """
        snapshot_df = context.get("screening_data")
        dp = context.get("data_processor")

        if snapshot_df is None or snapshot_df.empty:
            logger.warning("[OversoldStrategy] No snapshot data available.")
            return pd.DataFrame()

        if dp is None:
            logger.error("[OversoldStrategy] DataProcessor not found in context.")
            return pd.DataFrame()

        context_trade_date = context.get("trade_date")
        if context_trade_date:
            if isinstance(context_trade_date, str):
                for fmt in ("%Y%m%d", "%Y-%m-%d"):
                    try:
                        end_date_obj = datetime.datetime.strptime(context_trade_date, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    logger.warning(f"[OversoldStrategy] Cannot parse trade_date: {context_trade_date}")
                    return pd.DataFrame()
            elif isinstance(context_trade_date, datetime.date):
                end_date_obj = context_trade_date
            else:
                end_date_obj = context_trade_date
        else:
            end_date_obj = await dp.trade_calendar.get_latest_trade_date()
        if not end_date_obj:
            logger.warning(
                "[OversoldStrategy] No trade date found. Is the calendar service initialized?",
            )
            return pd.DataFrame()

        # Use trading days instead of calendar days for accurate RSI calculation
        start_date_obj = await dp.trade_calendar.get_start_date_by_trade_days(end_date_obj, 120)
        if not start_date_obj:
            logger.warning(
                "[OversoldStrategy] Failed to get start date by trade days, falling back to calendar days.",
            )
            start_date_obj = end_date_obj - datetime.timedelta(days=170)

        logger.info(
            f"[OversoldStrategy] Fetching history {start_date_obj} → {end_date_obj} for RSI calculation...",
        )

        try:
            valid_codes = snapshot_df["ts_code"].tolist()
            history_pdf = await dp.cache.get_daily_quotes(
                start_date=start_date_obj,
                end_date=end_date_obj,
                ts_code_list=valid_codes,
                suppress_errors=False,
            )

            if history_pdf is None or history_pdf.empty:
                logger.warning("[OversoldStrategy] No historical data found.")
                return pd.DataFrame()

            # Fix: Polars requires pyarrow for pandas 'Int64' (nullable int).
            # Cast to float64 to bypass pyarrow dependency.
            for col in history_pdf.select_dtypes(include=["Int64"]).columns:
                history_pdf[col] = history_pdf[col].astype("float64")

            df = pl.from_pandas(history_pdf)

            # CRITICAL: Must sort chronologically BEFORE any window operations like .last()
            # because the underlying SQL query (get_daily_quotes) does not guarantee ORDER BY.
            df_lazy = df.lazy().sort(["ts_code", "trade_date"])

            if df["trade_date"].dtype == pl.Date:
                end_date_value = end_date_obj
            elif df["trade_date"].dtype == pl.Datetime:
                end_date_value = datetime.datetime.combine(end_date_obj, datetime.time())
            else:
                end_date_value = end_date_obj.strftime("%Y%m%d")

            # Calculate QFQ Close (前复权收盘价)
            if "adj_factor" in df.columns:
                ffilled = pl.col("adj_factor").forward_fill().over("ts_code")
                latest_factor = ffilled.last().over("ts_code").fill_null(1.0)
                safe_latest = pl.when(latest_factor == 0).then(1.0).otherwise(latest_factor)
                qfq_ratio_expr = (ffilled.fill_null(safe_latest) / safe_latest).alias("qfq_ratio")
                qfq_close_expr = (pl.col("close") * pl.col("qfq_ratio")).alias("qfq_close")
                qfq_vol_expr = (
                    pl.when(pl.col("qfq_ratio") > 0)
                    .then(pl.col("vol") / pl.col("qfq_ratio"))
                    .otherwise(pl.col("vol"))
                    .alias("qfq_vol")
                )
                df_lazy = df_lazy.with_columns([qfq_ratio_expr]).with_columns([qfq_close_expr, qfq_vol_expr])
            else:
                df_lazy = df_lazy.with_columns([pl.col("close").alias("qfq_close"), pl.col("vol").alias("qfq_vol")])

            # Calculate Dynamic RSI
            rsi_col_name = f"rsi_{rsi_period}"
            rsi_expr = TechnicalAnalysis.get_rsi_expr(
                col_name="qfq_close",
                period=rsi_period,
                alias=rsi_col_name,
            )
            vol_ratio_expr = (
                pl.when(pl.col("qfq_vol").rolling_mean(5).over("ts_code") > 0)
                .then(pl.col("qfq_vol") / pl.col("qfq_vol").rolling_mean(5).over("ts_code"))
                .otherwise(None)
                .alias("vol_ratio_5d")
            )

            result_df = (
                df_lazy.with_columns(
                    [
                        rsi_expr.over("ts_code"),
                        vol_ratio_expr,
                        pl.col("close").count().over("ts_code").alias("day_count"),
                    ],
                )
                .filter(pl.col("trade_date") == end_date_value)
                .filter(pl.col("day_count") >= rsi_period * 2)
                .filter(pl.col(rsi_col_name) < rsi_threshold)
                .filter(pl.col("vol_ratio_5d") >= float(vol_ratio_threshold))
                .collect()
            )

            if result_df.height == 0:
                logger.info("[OversoldStrategy] No stocks found matching RSI criteria.")
                return pd.DataFrame()

            # Join with snapshot
            rsi_pdf = result_df.select(["ts_code", rsi_col_name, "vol_ratio_5d"]).to_pandas()
            final_df = pd.merge(snapshot_df, rsi_pdf, on="ts_code", how="inner")

            # Sort by RSI ascending (most oversold first)
            return final_df.sort_values(rsi_col_name, ascending=True)

        except QualityGateError:
            raise
        except Exception as e:
            logger.error(
                f"[OversoldStrategy] Error during execution: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Strategy internal error: {e}") from e

    # ============================================================
    # Context Builders — Strategy-specific enhancements
    # ============================================================

    async def _prefetch_strategy_specific(
        self, candidates_df: pd.DataFrame, context: dict, prefetched: PreFetchedContext
    ) -> PreFetchedContext:
        """
        预取超跌反弹策略特定的数据：
        1. 换手率指标数据 (daily_indicators)
        2. 行业统计
        3. 大盘指数数据
        """
        dp = context.get("data_processor")
        if dp is None:
            return prefetched

        ts_codes = candidates_df["ts_code"].tolist() if not candidates_df.empty else []

        try:
            if hasattr(dp.cache, "get_daily_indicators_bulk"):
                end_date = prefetched.trade_date
                if end_date:
                    start_date = await dp.trade_calendar.get_start_date_by_trade_days(end_date, 30)
                    if not start_date:
                        start_date = end_date - datetime.timedelta(days=45)  # type: ignore[operator]
                    prefetched.indicators = await dp.cache.get_daily_indicators_bulk(
                        ts_code_list=ts_codes,
                        start_date=start_date,
                        end_date=end_date,
                    )
        except Exception as e:
            logger.warning(f"[OversoldStrategy] Failed to prefetch indicators: {e}")

        try:
            screening_data = context.get("screening_data")
            if screening_data is not None and not screening_data.empty:
                prefetched.sector_stats = self._compute_sector_stats(screening_data)
        except Exception as e:
            logger.warning(f"[OversoldStrategy] Failed to compute sector stats: {e}")

        try:
            if hasattr(dp.cache, "get_index_daily_range"):
                trade_date = prefetched.trade_date
                if trade_date:
                    start_date = await dp.trade_calendar.get_start_date_by_trade_days(trade_date, 30)
                    if not start_date:
                        start_date = trade_date - datetime.timedelta(days=45)  # type: ignore[operator]

                    indices = ["000001.SH", "399001.SZ", "399006.SZ"]
                    idx_df = await dp.cache.get_index_daily_range(
                        ts_code_list=indices,
                        start_date=start_date,
                        end_date=trade_date,
                    )

                    if idx_df is not None and not idx_df.empty:
                        market_context = {}
                        for idx_code in indices:
                            idx_data = idx_df[idx_df["ts_code"] == idx_code].copy()
                            if idx_data.empty:
                                continue

                            idx_data = idx_data.sort_values("trade_date", ascending=True)

                            current_row = idx_data[
                                idx_data["trade_date"] == trade_date.strftime("%Y%m%d")  # type: ignore[union-attr]
                            ]
                            if current_row.empty:
                                current_row = idx_data.tail(1)

                            pct_chg = current_row["pct_chg"].iloc[0] if "pct_chg" in current_row.columns else 0

                            ma20 = None
                            trend = "未知"
                            if len(idx_data) >= 20 and "close" in idx_data.columns:
                                ma20 = idx_data["close"].tail(20).mean()
                                current_close = current_row["close"].iloc[0] if "close" in current_row.columns else 0
                                if ma20 and current_close:
                                    if current_close > ma20 * 1.02:
                                        trend = "多头趋势"
                                    elif current_close < ma20 * 0.98:
                                        trend = "空头趋势"
                                    else:
                                        trend = "震荡整理"

                            market_context[idx_code] = {
                                "pct_chg": pct_chg if not pd.isna(pct_chg) else 0,
                                "ma20": ma20,
                                "trend": trend,
                            }
                        prefetched.market_context = market_context
        except Exception as e:
            logger.warning(f"[OversoldStrategy] Failed to prefetch market data: {e}")

        return prefetched

    def _compute_sector_stats(self, screening_data: pd.DataFrame) -> dict:
        """
        计算各行业的涨跌统计。
        """
        if "industry" not in screening_data.columns or "pct_chg" not in screening_data.columns:
            return {}

        stats = {}
        for industry, group in screening_data.groupby("industry"):
            stats[industry] = {
                "count": len(group),
                "up_count": (group["pct_chg"] > 0).sum(),
                "down_count": (group["pct_chg"] < 0).sum(),
                "avg_pct_chg": group["pct_chg"].mean(),
            }
        return stats

    def _build_turnover_context(self, row: dict, prefetched: PreFetchedContext) -> str:
        """
        构建换手率趋势上下文。
        分析换手率变化趋势，判断是缩量下跌还是放量下跌。
        """
        ts_code = row.get("ts_code", "")
        indicators_df = prefetched.indicators

        if indicators_df is None or indicators_df.empty:
            return "换手率数据: 暂不可用"

        stock_indicators = indicators_df[indicators_df["ts_code"] == ts_code]
        if stock_indicators.empty:
            return "换手率数据: 当日无记录"

        parts = []
        turnover_col = "turnover_rate"

        if turnover_col in stock_indicators.columns:
            sorted_df = stock_indicators.sort_values("trade_date", ascending=False)  # type: ignore[union-attr]
            recent_5 = sorted_df.head(5)
            recent_20 = sorted_df.head(20)

            if len(recent_5) >= 3:
                current_turnover = recent_5[turnover_col].iloc[0]
                avg_5d = recent_5[turnover_col].mean()
                avg_20d = recent_20[turnover_col].mean() if len(recent_20) >= 20 else None

                if pd.isna(current_turnover) or pd.isna(avg_5d):  # type: ignore[union-attr]
                    return "换手率数据: 包含无效值"

                parts.append(f"当前换手率: {current_turnover:.2f}%")
                parts.append(f"5日均值: {avg_5d:.2f}%")

                if avg_20d is not None and not pd.isna(avg_20d):  # type: ignore[union-attr]
                    parts.append(f"20日均值: {avg_20d:.2f}%")
                    ratio_5_20 = avg_5d / avg_20d if avg_20d > 0 else 1
                    if ratio_5_20 < 0.7:
                        parts.append("趋势: 持续缩量 (5日均值低于20日均值的70%)")
                    elif ratio_5_20 > 1.3:
                        parts.append("趋势: 近期放量 (5日均值高于20日均值的130%)")

                if current_turnover < avg_5d * 0.7:
                    parts.append("当日: 缩量下跌 (换手率低于5日均值的70%)")
                elif current_turnover > avg_5d * 1.3:
                    parts.append("当日: 放量下跌 (换手率高于5日均值的130%)")
                else:
                    parts.append("当日: 换手率相对平稳")

        return "\n".join(parts) if parts else "换手率数据: 无有效数据"

    def _build_sector_context(self, row: dict, prefetched: PreFetchedContext) -> str:
        """
        构建行业统计上下文。
        显示同行业其他股票的涨跌分布。
        """
        sector_stats = prefetched.sector_stats
        industry = row.get("industry", "")

        if not sector_stats or industry not in sector_stats:
            return f"行业统计: {industry or '未知'} (暂无数据)"

        stats = sector_stats[industry]
        parts = [f"行业: {industry}"]
        parts.append(f"行业内股票数: {stats.get('count', 0)}")
        parts.append(f"上涨家数: {stats.get('up_count', 0)}")
        parts.append(f"下跌家数: {stats.get('down_count', 0)}")
        avg_pct = stats.get("avg_pct_chg") or 0
        parts.append(f"平均涨跌幅: {avg_pct:.2f}%")

        return "\n".join(parts)

    def _build_market_context(self, row: dict, prefetched: PreFetchedContext) -> str:
        """
        构建大盘环境上下文。
        显示上证指数、深证成指、创业板指的表现及 MA20 趋势。
        """
        market_context_str = prefetched.market_context_str
        if market_context_str:
            return market_context_str

        market_data = prefetched.market_context
        if not market_data:
            return "大盘环境: 数据暂不可用"

        index_names = {
            "000001.SH": "上证指数",
            "399001.SZ": "深证成指",
            "399006.SZ": "创业板指",
        }

        parts = ["大盘环境"]
        for idx_code, data in market_data.items():
            if not isinstance(data, dict):
                continue

            name = index_names.get(idx_code, idx_code)
            pct_chg = data.get("pct_chg") or 0
            trend = data.get("trend", "未知")

            direction = "上涨" if pct_chg > 0 else "下跌" if pct_chg < 0 else "平盘"
            parts.append(f"{name}: {pct_chg:+.2f}% ({direction}, {trend})")

        return "\n".join(parts)

    def _build_support_context(self, row: dict, prefetched: PreFetchedContext) -> str:
        """
        构建多维量化支撑位分析上下文。
        包含：布林带下轨(动态支撑)、VWAC(筹码支撑)、最大放量柱支撑、价值区下沿(结构支撑)。

        P1-18 fix: 使用复权后的价格和成交量进行计算，确保与 _math_filter 中的 RSI 计算口径一致。
        跨除权除息日时，原始 close 会有跳变，导致 VWAC 失真。
        """
        ts_code = row.get("ts_code", "")
        history_dict = prefetched.history

        if not history_dict or ts_code not in history_dict:
            return "支撑位分析: 历史数据暂不可用"

        history_df = history_dict[ts_code]
        if history_df is None or history_df.empty or len(history_df) < 20:
            return "支撑位分析: 数据不足"

        parts = []
        current_close = row.get("close")

        if current_close is None or current_close <= 0:
            return "支撑位分析: 当前价格数据无效"

        if "trade_date" in history_df.columns:
            history_df = history_df.sort_values("trade_date", ascending=True)

        qfq_close_col = "close"
        qfq_vol_col = "vol"

        if "adj_factor" in history_df.columns:
            adj_factors = history_df["adj_factor"].ffill().fillna(1.0)
            latest_factor = adj_factors.iloc[-1] if len(adj_factors) > 0 else 1.0
            if latest_factor == 0:
                latest_factor = 1.0
            qfq_ratio = adj_factors / latest_factor
            qfq_ratio = qfq_ratio.fillna(1.0)
            qfq_close_col = "qfq_close"
            qfq_vol_col = "qfq_vol"
            history_df = history_df.copy()
            history_df["qfq_close"] = history_df["close"] * qfq_ratio
            history_df["qfq_vol"] = history_df["vol"] / qfq_ratio.replace(0, 1.0)

        recent_60 = history_df.tail(60) if len(history_df) >= 60 else history_df

        current_qfq_close = current_close
        if qfq_close_col == "qfq_close" and qfq_close_col in history_df.columns:
            current_qfq_close = history_df[qfq_close_col].iloc[-1] if len(history_df) > 0 else current_close

        if len(history_df) >= 20:
            close_20 = history_df[qfq_close_col].tail(20)
            ma20 = close_20.mean()
            std20 = close_20.std()
            if pd.notna(ma20) and pd.notna(std20) and std20 > 0:
                boll_lower = ma20 - 2 * std20
                if pd.notna(boll_lower) and boll_lower > 0:
                    dist_boll = (current_qfq_close - boll_lower) / boll_lower * 100
                    parts.append(f"布林下轨(动态支撑): {boll_lower:.2f} (距离 {dist_boll:+.2f}%)")

        if len(recent_60) >= 20 and qfq_vol_col in recent_60.columns and qfq_close_col in recent_60.columns:
            vol_sum = recent_60[qfq_vol_col].sum()
            if vol_sum > 0:
                vwac_60 = (recent_60[qfq_close_col] * recent_60[qfq_vol_col]).sum() / vol_sum
                if pd.notna(vwac_60) and vwac_60 > 0:
                    dist_vwac = (current_qfq_close - vwac_60) / vwac_60 * 100
                    parts.append(f"60日量价均价(VWAC): {vwac_60:.2f} (距离 {dist_vwac:+.2f}%)")

        if len(recent_60) >= 5 and qfq_vol_col in recent_60.columns and qfq_close_col in recent_60.columns:
            vol_values = recent_60[qfq_vol_col].values
            if len(vol_values) > 0 and vol_values.max() > 0:
                max_vol_pos = vol_values.argmax()
                max_vol_support = recent_60[qfq_close_col].iloc[max_vol_pos]
                if pd.notna(max_vol_support) and max_vol_support > 0:
                    dist_vol_peak = (current_qfq_close - max_vol_support) / max_vol_support * 100
                    parts.append(f"近60日最大放量柱支撑: {max_vol_support:.2f} (距离 {dist_vol_peak:+.2f}%)")

        if len(history_df) >= 120:
            close_120 = history_df[qfq_close_col].tail(120)
            val_120 = close_120.quantile(0.1)
            if pd.notna(val_120) and val_120 > 0:
                dist_val = (current_qfq_close - val_120) / val_120 * 100
                parts.append(f"120日价值区下沿(前低集群): {val_120:.2f} (距离 {dist_val:+.2f}%)")

        return "\n".join(parts) if parts else "支撑位分析: 无有效数据"
