"""
AIStrategyMixin — Universal AI Analysis Engine

Any strategy that inherits this Mixin gains Level-2 AI analysis capability.
The strategy only needs to:
  1. Call `self.run_ai_analysis(candidates_df, context)` after its math filtering.
  2. Override `get_ai_context(row)` to inject strategy-specific context into the AI prompt.

The Mixin handles:
  - Sequential analysis with streaming output support
  - Progress callbacks and streaming results to UI
  - Graceful degradation when AI is not configured
  - Cancellation detection
  - Candidate count capping (cost control)
"""

import asyncio
import logging
import math
import pandas as pd
from datetime import timedelta

from data.news_fetcher import NewsFetcher
from services.ai_service import AIService
from utils.config_handler import ConfigHandler  # Used by get_ai_max_candidates
from utils.technical_analysis import TechnicalAnalysis
from ui.i18n import I18n
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class AIStrategyMixin:
    """
    Mixin class providing sequential AI analysis capability to any strategy.

    Usage:
        class OversoldStrategy(BaseStrategy, AIStrategyMixin):
            async def filter(self, context):
                candidates = ... # Math filtering
                return await self.run_ai_analysis(candidates, context)

            def get_ai_context(self, row: dict) -> str:
                return f"RSI({row.get('_rsi_period', 14)})={row.get('rsi_14', 'N/A')} — oversold candidate"
    """

    def get_ai_context(self, row: dict) -> str:
        """
        Override to inject strategy-specific context into the AI prompt.
        This tells the AI WHY this stock was selected, preventing "context vacuum".

        Args:
            row: Dict of stock data for a single candidate.
        Returns:
            A human-readable string describing the strategy context.
        """
        return ""  # Default: no additional context

    async def run_ai_analysis(
        self, candidates_df: pd.DataFrame, context: dict, max_stocks: int = None
    ) -> pd.DataFrame:
        """
        Run sequential AI analysis on pre-filtered candidates.

        Args:
            candidates_df: DataFrame of stocks that passed Level-1 math filtering.
            context: Full strategy context dict (contains data_processor, callbacks, etc.)
            max_stocks: Override for max candidates to analyze (default: from config).

        Returns:
            DataFrame enriched with ai_score, ai_reason columns, sorted by ai_score desc.
            Falls back to original candidates_df if AI is unavailable.
        """
        ai_client = AIService()
        dp = context.get("data_processor")
        on_progress = context.get("on_progress")
        on_result = context.get("on_stream_result") or context.get("on_result")

        # Extract UI real-time prompt override (handles users clicking Run before blurring Flet textarea)
        ui_prompt_override = context.get("params", {}).get("ai_system_prompt", None)

        # --- Guard: AI Available? ---
        if ai_client.client is None:
            logger.info(
                "[AIStrategyMixin] AI service not configured — returning math-only results"
            )
            if on_progress:
                on_progress(
                    0,
                    0,
                    I18n.get("ai_not_configured", "AI 服务未配置，仅展示数学筛选结果"),
                )
            return candidates_df

        # --- Guard: Empty Input ---
        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        # --- Cost Control: Cap candidates ---
        cap = max_stocks or ConfigHandler.get_ai_max_candidates()
        if len(candidates_df) > cap:
            logger.info(
                f"[AIStrategyMixin] Capping candidates from {len(candidates_df)} to {cap}"
            )
            candidates_df = candidates_df.head(cap)

        # --- Fetch Global Context ONCE ---
        # --- Pre-fetch Learning Context ONCE for the entire batch ---
        history_context = ""
        try:
            from data.review_manager import ReviewManager

            rm = ReviewManager()
            history_context = await rm.get_learning_context()
        except Exception as e:
            logger.warning(
                f"[AIStrategyMixin] Failed to pre-fetch learning context: {e}"
            )

        global_context = ""
        try:
            global_context = await NewsFetcher.get_us_major_moves()
        except Exception as e:
            logger.warning(f"[AIStrategyMixin] Failed to fetch global context: {e}")

        # --- Pre-fetch Concepts for all candidates (N+1 optimization) ---
        concepts_map = {}
        all_ts_codes = candidates_df["ts_code"].tolist()
        try:
            concepts_map = await dp.cache.get_concepts(all_ts_codes)
        except Exception as e:
            logger.warning(f"[AIStrategyMixin] Failed to pre-fetch concepts: {e}")

        # --- Ultimate Pipeline: Bulk History DB Query & Async News Task Pipelining (Fixing N+1) ---
        prefetched_history = {}
        news_tasks = {}
        try:
            # 1. O(1) DB Query for History
            end_date_str = get_now().strftime("%Y%m%d")

            years = ConfigHandler.get_init_history_years()
            start_date_str = (get_now() - timedelta(days=365 * years + 30)).strftime(
                "%Y%m%d"
            )
            bulk_history_df = await dp.cache.get_daily_quotes(
                ts_code_list=all_ts_codes,
                start_date=start_date_str,
                end_date=end_date_str,
            )
            if bulk_history_df is not None and not bulk_history_df.empty:
                for code, group in bulk_history_df.groupby("ts_code"):
                    prefetched_history[code] = group

            # 2. Background Pipelining for News with Semaphore(1)
            news_sem = asyncio.Semaphore(1)

            async def bg_fetch_news(code):
                async with news_sem:
                    try:
                        return await NewsFetcher.get_stock_news(code, limit=5)
                    except Exception:
                        return []

            news_tasks = {
                code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes
            }
        except Exception as e:
            logger.warning(f"[AIStrategyMixin] Ultimate Pipeline init failed: {e}")

        # --- Batch Pre-Fetch: Capital Flow Data (Moneyflow, TopList, Northbound) ---
        # Fetch once for the trade date, filter per-stock in the loop (0ms per stock)
        trade_date = None
        try:
            trade_date = await dp.get_latest_trade_date()
        except Exception as e:
            logger.warning(f"[AIStrategyMixin] Failed to get latest trade date: {e}")

        moneyflow_df = pd.DataFrame()
        top_list_df = pd.DataFrame()
        northbound_df = pd.DataFrame()

        if trade_date:
            try:
                moneyflow_df = await dp.cache.get_moneyflow(trade_date=trade_date)
            except Exception as e:
                logger.warning(f"[AIStrategyMixin] Failed to pre-fetch moneyflow: {e}")

            try:
                top_list_df = await dp.cache.get_top_list(trade_date=trade_date)
            except Exception as e:
                logger.warning(f"[AIStrategyMixin] Failed to pre-fetch top_list: {e}")

            try:
                northbound_df = await dp.cache.get_northbound(trade_date=trade_date)
            except Exception as e:
                logger.warning(f"[AIStrategyMixin] Failed to pre-fetch northbound: {e}")

        logger.info(
            f"[AIStrategyMixin] Pre-fetched capital data: moneyflow={len(moneyflow_df)}, top_list={len(top_list_df)}, northbound={len(northbound_df)}"
        )

        # Bundle pre-fetched data for the loop
        prefetched_capital = {
            "moneyflow_df": moneyflow_df,
            "top_list_df": top_list_df,
            "northbound_df": northbound_df,
            "trade_date": trade_date,
        }

        # --- Sequential Analysis Loop ---
        total_tasks = len(candidates_df)
        completed_count = 0

        if on_progress:
            on_progress(
                0, total_tasks, I18n.get("ai_progress_init", "初始化 AI 分析引擎...")
            )

        final_rows = []
        on_stream_start = context.get("on_stream_start")

        for row in candidates_df.itertuples(index=False):
            if dp and dp.is_cancelled():
                logger.info(
                    "[AIStrategyMixin] Cancellation detected — stopping remaining tasks"
                )
                break

            row_data = row._asdict()
            stock_name = row_data.get("name", row_data.get("ts_code", "?"))

            # Setup streaming callback for this specific stock
            on_chunk_callback = None
            if on_stream_start:
                on_chunk_callback = on_stream_start(stock_name)

            try:
                if on_progress:
                    on_progress(
                        completed_count,
                        total_tasks,
                        I18n.get("ai_analyzing_stock", name=stock_name),
                    )

                hist_df = prefetched_history.get(
                    row_data.get("ts_code"), pd.DataFrame()
                )
                news_list = []
                if row_data.get("ts_code") in news_tasks:
                    news_list = await news_tasks[row_data.get("ts_code")]

                res = await self._mixin_analyze_single(
                    row_data,
                    dp,
                    ai_client,
                    global_context,
                    concepts_map,
                    prefetched_capital=prefetched_capital,
                    on_chunk=on_chunk_callback,
                    history_df=hist_df,
                    news=news_list,
                    history_context=history_context,
                    ui_prompt_override=ui_prompt_override,
                )

                completed_count += 1

                if (
                    isinstance(res, Exception)
                    or res is None
                    or res.get("score", 0) == 0
                ):
                    if on_progress:
                        on_progress(
                            completed_count,
                            total_tasks,
                            I18n.get("ai_progress_skipped", name=stock_name),
                        )
                    continue

                # Valid result — enrich row
                row_dict = dict(row_data)
                row_dict["ai_score"] = res.get("score", 0)
                row_dict["ai_reason"] = res.get("summary", "")
                row_dict["thinking"] = res.get("thinking", "")
                final_rows.append(row_dict)

                # Stream to UI
                if on_result:
                    on_result(row_dict)

                if on_progress:
                    on_progress(
                        completed_count,
                        total_tasks,
                        I18n.get(
                            "ai_progress_analyzed",
                            name=stock_name,
                            score=row_dict["ai_score"],
                        ),
                    )

            except asyncio.CancelledError:
                logger.info("[AIStrategyMixin] Task cancelled")
                break
            except Exception as e:
                logger.error(
                    f"[AIStrategyMixin] Task error for {stock_name}: {e}", exc_info=True
                )
                completed_count += 1
            finally:
                # Always drain pending throttled text so the UI doesn't freeze mid-stream
                if on_chunk_callback and hasattr(on_chunk_callback, "final_flush"):
                    on_chunk_callback.final_flush()

        logger.info(
            f"[AIStrategyMixin] Complete. {completed_count}/{total_tasks} processed, {len(final_rows)} valid results"
        )

        # Cleanup: Cancel any orphan news tasks that were never awaited (e.g. user cancelled early)
        for code, task in news_tasks.items():
            if not task.done():
                task.cancel()

        if not final_rows:
            return candidates_df  # Fallback: return math-only results

        result_df = pd.DataFrame(final_rows)
        return result_df.sort_values("ai_score", ascending=False)

    async def _mixin_analyze_single(
        self,
        row: dict,
        dp,
        ai_client: AIService,
        global_context: str,
        concepts_map: dict,
        prefetched_capital: dict = None,
        on_chunk=None,
        history_df=None,
        news=None,
        history_context: str = None,
        ui_prompt_override: str = None,
    ):
        """
        Analyze a single stock. Fetches history, tech indicators, news,
        capital flow, financials, then calls AI with strategy-specific context injected.
        """
        try:
            ts_code = row["ts_code"]

            # 1. History (60 trading days)
            if history_df is None or history_df.empty:
                req_days = getattr(self, "required_history_days", 60)
                history_df = await dp.get_stock_history(ts_code, days=req_days)

            # 2. Technical Indicators (pointwise)
            trend_signal, _, _ = TechnicalAnalysis.get_macd(history_df)
            kdj_signal, k, d, j = TechnicalAnalysis.get_kdj(history_df)

            tech_context = {
                "macd_signal": trend_signal,
                "kdj_signal": kdj_signal,
                "k": round(k, 1),
                "j": round(j, 1),
            }

            # 2b. Technical Structure (MA alignment + volume trend from history_df)
            tech_structure = self._compute_technical_structure(history_df)
            tech_context.update(tech_structure)

            # 3. News
            if news is None:
                news = await NewsFetcher.get_stock_news(ts_code, limit=5)

            # 4. Concepts (use pre-fetched map)
            concepts = []
            if concepts_map and ts_code in concepts_map:
                concepts = concepts_map[ts_code]
            elif not concepts_map:
                cmap = await dp.cache.get_concepts([ts_code])
                concepts = cmap.get(ts_code, [])

            # 5. Strategy-specific context (The Hook!)
            strategy_ctx = self.get_ai_context(row)

            # 6. Capital Flow (filter pre-fetched batch data by ts_code)
            capital_flow_text = self._build_capital_flow_text(
                ts_code, prefetched_capital or {}
            )

            # 7. Financials (extract from stock_info which already has screening data)
            financials_text = self._build_financials_text(row)

            # 7b. History Feature Summary (Level-3: Factor Extraction + Summarization)
            history_text = self._build_history_text(history_df)

            # 8. Build stock_info and call AI
            stock_info = dict(row)
            stock_info["concepts"] = concepts

            ai_result = await ai_client.analyze_stock(
                stock_info,
                tech_context,
                news,
                global_context,
                strategy_context=strategy_ctx,
                capital_flow_text=capital_flow_text,
                financials_text=financials_text,
                history_text=history_text,
                on_chunk=on_chunk,
                history_context=history_context,
                strategy_key=getattr(self, "key", None),
                ui_prompt_override=ui_prompt_override,
            )
            return ai_result

        except Exception as e:
            logger.error(
                f"[AIStrategyMixin] Analysis failed for {row.get('ts_code', '?')}: {e}"
            )
            return None

    # ============================================================
    # Data Enrichment Helpers
    # ============================================================

    @staticmethod
    def _compute_technical_structure(history_df) -> dict:
        """
        Compute MA alignment and volume trend from history DataFrame.
        Returns a dict of human-readable technical structure signals.
        """
        result = {}
        if history_df is None or history_df.empty or len(history_df) < 5:
            result["ma_alignment"] = "Insufficient data"
            result["volume_trend"] = "Insufficient data"
            result["price_trend_5d"] = "Insufficient data"
            return result

        try:
            # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
            df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
            df = df_qfq.sort_values("trade_date", ascending=True).copy()
            close = df["close"]

            # MA Alignment
            ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else None
            ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else None
            ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            current_price = close.iloc[-1]

            if ma5 is not None and ma10 is not None and ma20 is not None:
                if ma5 > ma10 > ma20:
                    result["ma_alignment"] = (
                        f"Bullish (MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f})"
                    )
                elif ma5 < ma10 < ma20:
                    result["ma_alignment"] = (
                        f"Bearish (MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f})"
                    )
                else:
                    result["ma_alignment"] = (
                        f"Mixed (MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f})"
                    )

                # Price deviation from MA20
                if ma20 != 0:
                    deviation = ((current_price - ma20) / ma20) * 100
                    result["price_vs_ma20"] = f"{deviation:+.1f}% from MA20"
                else:
                    result["price_vs_ma20"] = "MA20 is zero"
            else:
                result["ma_alignment"] = "Insufficient history for MA calculation"

            # Volume Trend (last 5 days)
            if "vol" in df.columns and len(df) >= 10:
                vol_5d = df["vol"].tail(5).mean()
                vol_10d = df["vol"].tail(10).mean()
                if vol_10d > 0:
                    vol_ratio = vol_5d / vol_10d
                    if vol_ratio < 0.7:
                        result["volume_trend"] = (
                            f"Shrinking (5d/10d ratio: {vol_ratio:.2f})"
                        )
                    elif vol_ratio > 1.3:
                        result["volume_trend"] = (
                            f"Expanding (5d/10d ratio: {vol_ratio:.2f})"
                        )
                    else:
                        result["volume_trend"] = (
                            f"Stable (5d/10d ratio: {vol_ratio:.2f})"
                        )
                else:
                    result["volume_trend"] = "No volume data"
            else:
                result["volume_trend"] = "Insufficient data"

            # 5-day Price Trend
            if len(df) >= 5:
                price_5d_ago = close.iloc[-5]
                if price_5d_ago != 0:
                    pct_5d = ((current_price - price_5d_ago) / price_5d_ago) * 100
                else:
                    pct_5d = 0.0
                closes_5d = ", ".join([f"{c:.2f}" for c in close.tail(5).tolist()])
                result["price_trend_5d"] = (
                    f"{pct_5d:+.1f}% over 5 days (closes: {closes_5d})"
                )
            else:
                result["price_trend_5d"] = "Insufficient data"

        except Exception as e:
            logger.warning(
                f"[AIStrategyMixin] Technical structure computation failed: {e}"
            )
            result["ma_alignment"] = "Computation error"
            result["volume_trend"] = "Computation error"
            result["price_trend_5d"] = "Computation error"

        return result

    @staticmethod
    def _build_history_text(history_df: pd.DataFrame) -> str:
        """
        Build a semantic summary of recent price action using quantitative factor extraction.
        This provides the LLM with "vision" into the actual OHLCV structure.

        NOTE: Output intentionally excludes XML wrapper tags because the caller
              (ai_service.py) already wraps this in <recent_price_action>.
        """
        if history_df is None or history_df.empty:
            return ""

        try:
            # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
            df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
            # Ensure chronological order
            df = df_qfq.sort_values("trade_date", ascending=True).reset_index(drop=True)

            # Compute Macro Horizon
            macro_cagr = "N/A"
            macro_mdd = "N/A"
            if len(df) > 60:
                # Compute long-term CAGR and Max Drawdown on `df`
                first_close_macro = df["close"].iloc[0]
                if first_close_macro > 0:
                    macro_cagr = (
                        f"{((df['close'].iloc[-1] / first_close_macro) - 1) * 100:.1f}%"
                    )
                roll_max = df["close"].cummax()
                drawdown = (df["close"] - roll_max) / roll_max
                macro_mdd = f"{drawdown.min() * 100:.1f}%"

                # Slice for short-term K-line context
                df = df.tail(60).reset_index(drop=True)

            if len(df) < 5:
                return "Insufficient historical data (<5 days)."

            # 1. Extract Base Series
            close = df["close"]
            has_vol = "vol" in df.columns
            has_pct_chg = "pct_chg" in df.columns

            # 2. Trend & Swing Factors (with division-by-zero guards)
            first_close = close.iloc[0]
            fifth_ago_close = close.iloc[-5]
            pct_all = (
                ((close.iloc[-1] / first_close) - 1) * 100 if first_close > 0 else 0.0
            )
            pct_5d = (
                ((close.iloc[-1] / fifth_ago_close) - 1) * 100
                if fifth_ago_close > 0
                else 0.0
            )

            # 20-Day MA Bias
            bias_str = "N/A (insufficient data)"
            if len(df) >= 20:
                ma20 = close.tail(20).mean()
                if ma20 > 0:
                    bias = ((close.iloc[-1] - ma20) / ma20) * 100
                    bias_str = f"{bias:+.2f}%"

            # Consecutive streaks (with NaN guard)
            consec_str = "N/A"
            if has_pct_chg:
                last_pct = df["pct_chg"].iloc[-1]
                # Guard against NaN
                if pd.isna(last_pct):
                    last_pct = 0.0
                sign_last = 1 if last_pct > 0 else -1 if last_pct < 0 else 0
                consec_days = 0
                if sign_last != 0:
                    for p in reversed(df["pct_chg"].tolist()):
                        if pd.isna(p):
                            break
                        if (p > 0 and sign_last > 0) or (p < 0 and sign_last < 0):
                            consec_days += 1
                        else:
                            break
                consec_str = (
                    f"Consecutive {'Up' if sign_last > 0 else 'Down'} for {consec_days} days"
                    if consec_days > 1
                    else "Consolidation"
                )

            # 3. Drawdown Factor
            rolling_max = close.cummax()
            drawdowns = (close - rolling_max) / rolling_max
            mdd = drawdowns.min() * 100

            # 4. Volume Factor (graceful fallback if 'vol' column is missing)
            vol_line = "- Volume data not available."
            if has_vol:
                vol = df["vol"]
                vol_5d_avg = vol.tail(5).mean()
                vol_older_avg = vol.iloc[:-5].mean() if len(df) > 5 else 0.0
                # Guard NaN from mean of NaN-containing series
                if pd.isna(vol_5d_avg):
                    vol_5d_avg = 0.0
                if pd.isna(vol_older_avg):
                    vol_older_avg = 0.0
                vol_ratio_5d = vol_5d_avg / vol_older_avg if vol_older_avg > 0 else 1.0
                vol_desc = (
                    "Significant Expansion"
                    if vol_ratio_5d > 1.5
                    else "Significant Contraction"
                    if vol_ratio_5d < 0.7
                    else "Flat"
                )
                vol_line = f"- Volume State: {vol_desc} vs historical baseline, Vol Ratio = {vol_ratio_5d:.2f}."

            # 5. Build Semantic Prompt (WITHOUT XML wrapper tags — caller adds them)
            lines = [
                "【Macro Horizon】(Configured Baseline)",
                f"- Long-Term: Total Return {macro_cagr}, Max Drawdown {macro_mdd}.",
                "",
                f"【Trend & Swing Characteristics】(Over last {len(df)} trading days)",
                f"- Swing: Total return {pct_all:+.2f}%, Max Drawdown {mdd:.2f}%.",
                f"- Short-term Momentum: 5-day return {pct_5d:+.2f}%, currently {consec_str}.",
                f"- MA20 Bias: {bias_str}.",
                "",
                "【Volume & Price Coordination】",
                vol_line,
                "",
                "【Micro 3-Day Action】",
                "Date | Close | Pct_Chg | Vol",
            ]

            # Append last 3 micro candles
            for _, r in df.tail(3).iterrows():
                d = str(r.get("trade_date", ""))[-4:]
                c = f"{r.get('close', 0):.2f}"
                p_val = r.get("pct_chg", 0)
                p = f"{p_val:+.2f}%" if not pd.isna(p_val) else "N/A"
                v_val = r.get("vol", 0)
                v = f"{v_val:.0f}" if (has_vol and not pd.isna(v_val)) else "N/A"
                lines.append(f"{d} | {c} | {p} | {v}")

            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"[AIStrategyMixin] Failed to build history text: {e}")
            return "Error extracting price action features."

    @staticmethod
    def _safe_float(val, default=0.0):
        """Safely convert a value to float, handling None, NaN, and non-numeric."""
        if val is None:
            return default
        try:
            fval = float(val)
            return default if math.isnan(fval) else fval
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _build_capital_flow_text(ts_code: str, prefetched: dict) -> str:
        """
        Build a human-readable capital flow summary from pre-fetched batch DataFrames.
        """
        sf = AIStrategyMixin._safe_float
        parts = []

        # 1. Moneyflow (主力资金)
        mf_df = prefetched.get("moneyflow_df")
        if mf_df is not None and not mf_df.empty:
            stock_mf = mf_df[mf_df["ts_code"] == ts_code]
            if not stock_mf.empty:
                row = stock_mf.iloc[0]
                # Large + Extra-large = Main Force
                buy_lg = sf(row.get("buy_lg_amount"))
                sell_lg = sf(row.get("sell_lg_amount"))
                buy_elg = sf(row.get("buy_elg_amount"))
                sell_elg = sf(row.get("sell_elg_amount"))
                net_main = (buy_lg + buy_elg) - (sell_lg + sell_elg)
                net_total = sf(row.get("net_mf_amount"))
                parts.append(f"主力净流入: {net_main:.2f}万元 (大单+超大单)")
                parts.append(f"全市场净流入: {net_total:.2f}万元")
            else:
                parts.append("个股资金流数据: 当日无记录")
        else:
            parts.append("个股资金流数据: 暂不可用")

        # 2. Top List (龙虎榜)
        tl_df = prefetched.get("top_list_df")
        if tl_df is not None and not tl_df.empty:
            stock_tl = tl_df[tl_df["ts_code"] == ts_code]
            if not stock_tl.empty:
                row = stock_tl.iloc[0]
                reason = row.get("reason")
                reason = (
                    reason
                    if reason and not (isinstance(reason, float) and reason != reason)
                    else "N/A"
                )
                net_amt = sf(row.get("net_amount"))
                parts.append(f"龙虎榜: 是 (原因: {reason}, 净买入: {net_amt:.2f}万元)")
            else:
                parts.append("龙虎榜: 当日未上榜")
        else:
            parts.append("龙虎榜数据: 暂不可用")

        # 3. Northbound (北向资金)
        nb_df = prefetched.get("northbound_df")
        if nb_df is not None and not nb_df.empty:
            stock_nb = nb_df[nb_df["ts_code"] == ts_code]
            if not stock_nb.empty:
                row = stock_nb.iloc[0]
                vol = sf(row.get("vol"))
                ratio = sf(row.get("ratio"))
                parts.append(f"北向持股: {vol:.0f}股, 占流通股比例: {ratio:.2f}%")
            else:
                parts.append("北向持股: 当日无持股记录")
        else:
            parts.append("北向持股数据: 暂无")

        return "\n".join(parts) if parts else "资金面数据暂不可用"

    @staticmethod
    def _build_financials_text(row: dict) -> str:
        """
        Build a human-readable financials summary from the stock_info data.
        The screening data already contains key financial metrics from the join.
        """
        sf = AIStrategyMixin._safe_float
        parts = []

        def fmt(val, suffix="", fmt_spec=".2f"):
            """Format a value using _safe_float for NaN safety, returning 'N/A' for missing."""
            f = sf(val, default=None)
            if f is None:
                return "N/A"
            return f"{f:{fmt_spec}}{suffix}"

        parts.append(f"PE(TTM): {fmt(row.get('pe_ttm'))}")
        parts.append(f"PB: {fmt(row.get('pb'))}")
        parts.append(f"ROE: {fmt(row.get('roe'), '%')}")
        parts.append(f"毛利率: {fmt(row.get('grossprofit_margin'), '%')}")
        parts.append(f"资产负债率: {fmt(row.get('debt_to_assets'), '%')}")
        parts.append(f"营收同比增长: {fmt(row.get('or_yoy'), '%')}")
        parts.append(f"净利润同比增长: {fmt(row.get('netprofit_yoy'), '%')}")

        tmv = sf(row.get("total_mv"), default=None)
        parts.append(
            f"总市值: {f'{tmv / 10000:.2f}亿元' if tmv is not None else 'N/A'}"
        )

        parts.append(f"股息率(TTM): {fmt(row.get('dv_ttm'), '%')}")

        # PEG calculation
        pe_val = sf(row.get("pe_ttm"), default=None)
        growth_val = sf(row.get("netprofit_yoy"), default=None)
        if pe_val is not None and growth_val is not None and growth_val > 0:
            peg = pe_val / growth_val
            parts.append(f"PEG: {peg:.2f} (PE/净利润增速)")
        else:
            parts.append("PEG: N/A (增速<=0或数据缺失)")

        return "\n".join(parts)
