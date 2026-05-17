"""
AIStrategyMixin — Universal AI Analysis Engine

Any strategy that inherits this Mixin gains Level-2 AI analysis capability.
The strategy only needs to:
  1. Call `self.run_ai_analysis(candidates_df, context)` after its math filtering.
  2. Override `get_ai_context(row)` to inject strategy-specific context into the AI prompt.
  3. (Optional) Register custom context builders via `register_context_builder()`.

The Mixin handles:
  - Sequential analysis with streaming output support
  - Progress callbacks and streaming results to UI
  - Graceful degradation when AI is not configured
  - Cancellation detection
  - Candidate count capping (cost control)
  - Pluggable context builder mechanism for strategy-specific enhancements
"""

import asyncio
import logging
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from cachetools import TTLCache

import pandas as pd

from data.constants import TOP_LIST_NET_AMOUNT_UNIT, get_column_unit
from data.external.news_fetcher import NewsFetcher
from services.ai_service import AIService
from strategies.utils import fmt_val, safe_float
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.technical_analysis import TechnicalAnalysis
from utils.time_utils import get_now, to_yyyymmdd_str

logger = logging.getLogger(__name__)


@dataclass
class PreFetchedContext:
    """
    Container for pre-fetched data shared across all stock analyses in a batch.

    This dataclass encapsulates all pre-fetched data to avoid parameter bloat
    in method signatures and enable clean extension for future enhancements.
    """

    capital: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    concepts_map: dict = field(default_factory=dict)
    news_tasks: dict = field(default_factory=dict)
    history_context: str = ""
    global_context: str = ""
    trade_date: object | None = None

    indicators: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_stats: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)
    market_context_str: str = ""
    macro_context: str = ""
    auxiliary_data: dict = field(default_factory=dict)


ContextBuilder = Callable[[dict, PreFetchedContext], str]


class AIStrategyMixin:
    """
    Mixin class providing sequential AI analysis capability to any strategy.

    Usage:
        class OversoldStrategy(BaseStrategy, AIStrategyMixin):
            def __init__(self):
                super().__init__()
                # Register custom context builders
                self.register_context_builder("turnover", self._build_turnover_context)
                self.register_context_builder("sector", self._build_sector_context)

            async def filter(self, context):
                candidates = ... # Math filtering
                return await self.run_ai_analysis(candidates, context)

            def get_ai_context(self, row: dict) -> str:
                return f"RSI({row.get('_rsi_period', 14)})={row.get('rsi_14', 'N/A')} — oversold candidate"

    Attributes:
        enable_ai_analysis: Class-level flag; set False to skip Phase 2 AI analysis.
        _context_builders: Dict of registered context builder functions.
            Key: context block name (e.g., "turnover", "sector")
            Value: Callable[[row: dict, prefetched: PreFetchedContext], str]
    """

    enable_ai_analysis: bool = True

    _HISTORY_CACHE_MAX = 4
    _HISTORY_CACHE_TTL = 120

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_builders: dict[str, ContextBuilder] = {}
        self._history_cache: TTLCache = TTLCache(maxsize=self._HISTORY_CACHE_MAX, ttl=self._HISTORY_CACHE_TTL)

    def register_context_builder(self, name: str, builder: ContextBuilder) -> None:
        """
        Register a custom context builder for this strategy.

        Args:
            name: Context block name (e.g., "turnover", "sector", "market")
            builder: Function(row: dict, prefetched: PreFetchedContext) -> str
        """
        self._context_builders[name] = builder
        logger.debug("[AIStrategyMixin] Registered context builder: %s", name)

    def _sort_for_ai(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure candidates are sorted by relevance before AI analysis truncation.
        P1-13 fix: Default sort by market cap (descending) or volume (descending)
        to ensure high-quality candidates are prioritized when capped.

        Subclasses should override if the default sort order is not
        the best proxy for "most promising candidate first".
        """
        if len(df) <= 1:
            return df

        sort_cols = []
        if "total_mv" in df.columns:
            sort_cols.append(("total_mv", False))
        elif "circ_mv" in df.columns:
            sort_cols.append(("circ_mv", False))
        elif "amount" in df.columns:
            sort_cols.append(("amount", False))
        elif "vol" in df.columns:
            sort_cols.append(("vol", False))

        if sort_cols:
            col, ascending = sort_cols[0]
            df = df.sort_values(by=col, ascending=ascending, na_position="last")
            logger.debug(
                f"[{self.__class__.__name__}] Sorted {len(df)} candidates by {col} (descending) for AI analysis"
            )
        else:
            logger.debug("[%s] Using default order for AI analysis (%d candidates)", self.__class__.__name__, len(df))

        return df.reset_index(drop=True)

    def get_context_blocks(self) -> list[str]:
        """Get list of context block names to build for this strategy."""
        return list(self._context_builders.keys())

    def should_include_global_context(self) -> bool:
        """Whether this strategy should inject shared market/global context."""
        return True

    def should_include_learning_context(self) -> bool:
        """Whether this strategy should inject cross-run historical learning context."""
        return True

    async def _prefetch_strategy_specific(
        self, candidates_df: pd.DataFrame, context: dict, prefetched: PreFetchedContext
    ) -> PreFetchedContext:
        """
        Hook for strategy-specific pre-fetching. Override in subclasses.

        Args:
            candidates_df: DataFrame of candidate stocks.
            context: Full strategy context dict.
            prefetched: PreFetchedContext with base pre-fetched data.

        Returns:
            Updated PreFetchedContext with strategy-specific data added.
        """
        return prefetched

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

    @staticmethod
    def _normalize_trade_date_for_cache(value):
        """Normalize context trade_date for cache APIs that expect YYYYMMDD strings."""
        return to_yyyymmdd_str(value)

    @staticmethod
    def resolve_end_date(ctx_td, is_backtest):
        import datetime as _dt

        end_date = get_now().date()
        if ctx_td:
            try:
                end_date = _dt.datetime.strptime(ctx_td, "%Y%m%d").date()
            except (ValueError, TypeError):
                if is_backtest:
                    raise ValueError(
                        f"Cannot parse trade_date for backtest: {ctx_td!r}. "
                        f"Refusing to fall back to current date to prevent lookahead bias."
                    ) from None
        return end_date

    async def run_ai_analysis(
        self,
        candidates_df: pd.DataFrame,
        context: dict,
        max_stocks: int = None,  # type: ignore[assignment]
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

        if ui_prompt_override:
            from utils.prompt_guard import validate_prompt, sanitize_prompt

            is_valid, warning = validate_prompt(ui_prompt_override)
            if not is_valid:
                logger.warning("[AIStrategyMixin] User prompt override rejected: %s", warning)
                ui_prompt_override = None
            else:
                ui_prompt_override = sanitize_prompt(ui_prompt_override)

        # --- Guard: AI Available? ---
        if not ai_client.is_cloud_available():
            logger.info(
                "[AIStrategyMixin] AI service not configured — returning math-only results",
            )
            if on_progress:
                on_progress(
                    0,
                    0,
                    I18n.get("ai_not_configured", "AI 服务未配置，仅展示数学筛选结果"),
                )
            return candidates_df

        # --- Guard: DataProcessor Available? ---
        if dp is None:
            logger.warning(
                "[AIStrategyMixin] DataProcessor missing from context — returning math-only results",
            )
            return candidates_df

        # --- Guard: Empty Input ---
        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        # --- Cost Control: Cap candidates ---
        cap = max_stocks or ConfigHandler.get_ai_max_candidates()
        if len(candidates_df) > cap:
            logger.info(
                f"[AIStrategyMixin] Capping candidates from {len(candidates_df)} to {cap}",
            )
            candidates_df = candidates_df.head(cap)

        # --- Fetch Global Context ONCE ---
        # --- Pre-fetch Learning Context ONCE for the entire batch ---
        history_context = ""
        if self.should_include_learning_context():
            try:
                from data.persistence.review_manager import ReviewManager

                rm = ReviewManager()
                history_context = await rm.get_learning_context()
            except Exception as e:
                logger.warning(
                    f"[AIStrategyMixin] Failed to pre-fetch learning context: {e}",
                )

        global_context = ""
        if self.should_include_global_context():
            try:
                global_context = await NewsFetcher.get_us_major_moves()
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to fetch global context: %s", e)

        # --- Pre-fetch Concepts for all candidates (N+1 optimization) ---
        concepts_map = {}
        all_ts_codes = candidates_df["ts_code"].tolist()
        try:
            concepts_map = await dp.cache.get_concepts(all_ts_codes)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to pre-fetch concepts: %s", e)

        # --- Ultimate Pipeline: Bulk History DB Query & Async News Task Pipelining (Fixing N+1) ---
        prefetched_history = {}
        news_tasks = {}
        try:
            # 1. O(1) DB Query for History (with LRU cache)
            end_date = get_now().date()

            ctx_td = self._normalize_trade_date_for_cache(context.get("trade_date"))
            end_date = self.resolve_end_date(ctx_td, context.get("is_backtest"))

            years = ConfigHandler.get_init_history_years()
            start_date = end_date - timedelta(days=365 * years + 30)

            cache_key = (frozenset(all_ts_codes), start_date, end_date, ctx_td)
            bulk_history_df = self._history_cache.get(cache_key)

            if bulk_history_df is None:
                bulk_history_df = await dp.cache.get_daily_quotes(  # type: ignore[union-attr]
                    ts_code_list=all_ts_codes,
                    start_date=start_date,
                    end_date=end_date,
                    suppress_errors=False,
                )
                self._history_cache[cache_key] = bulk_history_df
            if bulk_history_df is not None and not bulk_history_df.empty:
                for code, group in bulk_history_df.groupby("ts_code"):
                    prefetched_history[code] = group

            # 2. Background Pipelining for News with Semaphore(1)
            news_sem = asyncio.Semaphore(1)

            async def bg_fetch_news(code):
                async with news_sem:
                    try:
                        return await NewsFetcher.get_stock_news(code, limit=5)
                    except (ValueError, RuntimeError, OSError, ConnectionError):
                        return []

            news_tasks = {code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes}
        except Exception as e:
            logger.warning("[AIStrategyMixin] Ultimate Pipeline init failed: %s", e)

        # --- Batch Pre-Fetch: Capital Flow Data (Moneyflow, TopList, Northbound) ---
        # Fetch once for the trade date, filter per-stock in the loop (0ms per stock)
        trade_date = self._normalize_trade_date_for_cache(context.get("trade_date"))
        try:
            if trade_date is None:
                trade_date = self._normalize_trade_date_for_cache(await dp.get_latest_trade_date())  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to get latest trade date: %s", e)

        moneyflow_df = pd.DataFrame()
        top_list_df = pd.DataFrame()
        northbound_df = pd.DataFrame()

        if trade_date:
            try:
                moneyflow_df = await dp.cache.get_moneyflow(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch moneyflow: %s", e)

            try:
                top_list_df = await dp.cache.get_top_list(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch top_list: %s", e)

            try:
                northbound_df = await dp.cache.get_northbound(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch northbound: %s", e)

        logger.info(
            f"[AIStrategyMixin] Pre-fetched capital data: moneyflow={len(moneyflow_df)}, top_list={len(top_list_df)}, northbound={len(northbound_df)}",
        )

        # --- Pre-fetch Auxiliary Data (Audit, Dividend, Pledge, Holders) ---
        auxiliary_data = {}
        try:
            auxiliary_data = await dp.cache.prefetch_auxiliary_data(all_ts_codes)
            logger.info("[AIStrategyMixin] Pre-fetched auxiliary data for %d stocks", len(auxiliary_data))
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to pre-fetch auxiliary data: %s", e)

        # --- Bundle all pre-fetched data into PreFetchedContext ---
        prefetched = PreFetchedContext(
            capital={
                "moneyflow_df": moneyflow_df,
                "top_list_df": top_list_df,
                "northbound_df": northbound_df,
                "trade_date": trade_date,
            },
            history=prefetched_history,
            concepts_map=concepts_map,
            news_tasks=news_tasks,
            history_context=history_context,
            global_context=global_context,
            trade_date=trade_date,
            auxiliary_data=auxiliary_data,
        )

        # --- Strategy-specific prefetch hook ---
        prefetched = await self._prefetch_strategy_specific(candidates_df, context, prefetched)

        # --- Sequential Analysis Loop ---
        total_tasks = len(candidates_df)
        completed_count = 0

        if on_progress:
            on_progress(
                0,
                total_tasks,
                I18n.get("ai_progress_init", "初始化 AI 分析引擎..."),
            )

        final_rows = []
        on_stream_start = context.get("on_stream_start")

        for row in candidates_df.itertuples(index=False):
            if dp and dp.is_cancelled():
                logger.info(
                    "[AIStrategyMixin] Cancellation detected — stopping remaining tasks",
                )
                break

            row_data = row._asdict()  # type: ignore[union-attr]
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

                hist_df = prefetched.history.get(
                    row_data.get("ts_code"),
                    pd.DataFrame(),
                )
                news_list = []
                if row_data.get("ts_code") in prefetched.news_tasks:
                    news_list = await prefetched.news_tasks[row_data.get("ts_code")]

                res = await self._mixin_analyze_single(
                    row_data,
                    dp,
                    ai_client,
                    prefetched,
                    on_chunk=on_chunk_callback,
                    history_df=hist_df,
                    news=news_list,
                    ui_prompt_override=ui_prompt_override,  # type: ignore[arg-type]
                    vol_ratio_threshold=context.get("params", {}).get("vol_ratio_threshold", 1.5),
                )

                completed_count += 1

                if isinstance(res, Exception) or res is None or res.get("score", 0) == 0:
                    if on_progress:
                        on_progress(
                            completed_count,
                            total_tasks,
                            I18n.get("ai_progress_skipped", name=stock_name),
                        )
                    continue

                # Valid result — enrich row
                row_dict = dict(row_data)

                # 组装置信度与风险点到 summary 中，实现 UI 无感展示
                summary_raw = res.get("summary", "")
                summary = str(summary_raw) if summary_raw else ""
                confidence = res.get("confidence")
                uncertainty = res.get("uncertainty_factors")

                if confidence is not None:
                    summary = f"[{I18n.get('ai_confidence_label')}: {confidence}%] {summary}"
                if uncertainty:
                    if isinstance(uncertainty, list):
                        uncertainty_str = ", ".join(str(u) for u in uncertainty if u)
                    else:
                        uncertainty_str = str(uncertainty).strip()
                    if uncertainty_str and uncertainty_str not in [
                        "",
                        "None",
                        I18n.get("ai_none_risk"),
                        I18n.get("ai_none_risk_period"),
                        "[]",
                    ]:
                        summary += f" ({I18n.get('ai_risk_label')}: {uncertainty_str})"

                score_raw = res.get("score", 0)
                row_dict["ai_score"] = (
                    round(min(100, max(0, float(score_raw))), 1) if isinstance(score_raw, (int, float)) else 0
                )
                row_dict["ai_reason"] = summary
                thinking_raw = res.get("thinking", "")
                row_dict["thinking"] = str(thinking_raw) if thinking_raw else ""
                row_dict["confidence"] = (
                    min(100, max(1, int(confidence))) if isinstance(confidence, (int, float)) else 50
                )
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
                    f"[AIStrategyMixin] Task error for {stock_name}: {e}",
                    exc_info=True,
                )
                completed_count += 1
            finally:
                # Always drain pending throttled text so the UI doesn't freeze mid-stream
                if on_chunk_callback and hasattr(on_chunk_callback, "final_flush"):
                    on_chunk_callback.final_flush()

        logger.info(
            f"[AIStrategyMixin] Complete. {completed_count}/{total_tasks} processed, {len(final_rows)} valid results",
        )

        # Cleanup: Cancel any orphan news tasks that were never awaited (e.g. user cancelled early)
        for _code, task in prefetched.news_tasks.items():
            if not task.done():
                task.cancel()

        if not final_rows:
            return candidates_df  # Fallback: return math-only results

        result_df = pd.DataFrame(final_rows)

        # Log partial analysis: if some stocks were skipped due to errors,
        # record it in logs so downstream consumers (UI, CSV, DB) are not polluted.
        error_count = total_tasks - len(final_rows)
        if error_count > 0:
            logger.info(
                f"[AIStrategyMixin] Partial analysis: {error_count}/{total_tasks} stocks skipped or failed",
            )

        return result_df.sort_values("ai_score", ascending=False)

    async def _mixin_analyze_single(
        self,
        row: dict,
        dp,
        ai_client: AIService,
        prefetched: PreFetchedContext,
        on_chunk=None,
        history_df=None,
        news=None,
        ui_prompt_override: str = None,  # type: ignore[assignment]
        vol_ratio_threshold: float = 1.5,
    ):
        """
        Analyze a single stock. Fetches history, tech indicators, news,
        capital flow, financials, then calls AI with strategy-specific context injected.

        Args:
            row: Dict of stock data for a single candidate.
            dp: DataProcessor instance.
            ai_client: AIService instance.
            prefetched: PreFetchedContext containing all pre-fetched batch data.
            on_chunk: Optional streaming callback.
            history_df: Optional pre-fetched history DataFrame.
            news: Optional pre-fetched news list.
            ui_prompt_override: Optional user-provided prompt override.
        """
        try:
            ts_code = row["ts_code"]

            # 1. History (60 trading days)
            if history_df is None or history_df.empty:
                req_days = getattr(self, "required_history_days", 60)
                history_end_date = prefetched.trade_date if prefetched.trade_date else None
                history_df = await dp.get_stock_history(ts_code, days=req_days, end_date=history_end_date)

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
            tech_structure = self._compute_technical_structure(history_df, vol_ratio_threshold=vol_ratio_threshold)
            tech_context.update(tech_structure)

            # 2c. RSI Oversold Features (for oversold strategy enhancement)
            if history_df is not None and not history_df.empty and len(history_df) >= 30:
                df_sorted = history_df.sort_values("trade_date", ascending=True)
                rsi_period = row.get("_rsi_period", 14)
                rsi_features = TechnicalAnalysis.analyze_rsi_oversold_features(df_sorted["close"], period=rsi_period)
                row["_rsi_feature_text"] = rsi_features.get("feature_text", "")
                row["_rsi_consecutive_days"] = rsi_features.get("consecutive_oversold_days", 0)
                row["_rsi_days_since_healthy"] = rsi_features.get("days_since_healthy")
                row["_rsi_stagnation"] = rsi_features.get("stagnation_detected", False)
            else:
                row["_rsi_feature_text"] = ""

            # 3. News
            if news is None:
                news = await NewsFetcher.get_stock_news(ts_code, limit=5)

            # 4. Concepts (use pre-fetched map)
            concepts = []
            if prefetched.concepts_map and ts_code in prefetched.concepts_map:
                concepts = prefetched.concepts_map[ts_code]
            elif not prefetched.concepts_map:
                cmap = await dp.cache.get_concepts([ts_code])
                concepts = cmap.get(ts_code, [])

            # 5. Strategy-specific context (The Hook!)
            strategy_ctx = self.get_ai_context(row)

            # 5b. Registered context builders
            custom_context_blocks = []
            for name, builder in self._context_builders.items():
                try:
                    block_text = builder(row, prefetched)
                    if block_text:
                        custom_context_blocks.append(f"### {name}\n{block_text}")
                except Exception as e:
                    logger.warning("[AIStrategyMixin] Context builder '%s' failed: %s", name, e)

            if custom_context_blocks:
                strategy_ctx = strategy_ctx + "\n\n" + "\n\n".join(custom_context_blocks)

            # 6. Capital Flow (filter pre-fetched batch data by ts_code)
            capital_flow_text = self._build_capital_flow_text(
                ts_code,
                prefetched.capital or {},
            )

            # 7. Financials (extract from stock_info which already has screening data)
            base_financials = self._build_financials_text(row)

            # 7a. Multi-Period Financial Trends (Phase 1.2)
            multi_period_text = await self._build_multi_period_financials(ts_code, dp.cache, prefetched.auxiliary_data)

            # 7b. Auxiliary Data (Phase 1.2)
            auxiliary_text = await self._build_auxiliary_data_text(ts_code, dp.cache, prefetched.auxiliary_data)

            # 7c. Macro Context (Phase 1.3) - build once per batch
            if not prefetched.macro_context:
                prefetched.macro_context = await self._build_macro_context(dp.cache, as_of_date=prefetched.trade_date)

            # Combine all financial context
            financials_parts = [base_financials]
            invalid_texts = [I18n.get("ai_financial_insufficient"), I18n.get("ai_financial_fetch_failed")]
            if multi_period_text and multi_period_text not in invalid_texts:
                financials_parts.append(
                    f"\n{I18n.get('ai_section_wrapper', title=I18n.get('ai_multi_period_trend'))}\n{multi_period_text}"
                )
            if auxiliary_text and auxiliary_text != I18n.get("ai_no_auxiliary_data"):
                financials_parts.append(
                    f"\n{I18n.get('ai_section_wrapper', title=I18n.get('ai_auxiliary_data'))}\n{auxiliary_text}"
                )
            if prefetched.macro_context:
                financials_parts.append(f"\n{prefetched.macro_context}")

            financials_text = "\n".join(financials_parts)

            # 7d. History Feature Summary (Level-3: Factor Extraction + Summarization)
            history_text = self._build_history_text(
                history_df,  # type: ignore[arg-type]
                ts_code=ts_code,
                stock_name=row.get("name", ""),
                vol_ratio_threshold=vol_ratio_threshold,
            )

            # 8. Build stock_info and call AI
            stock_info = dict(row)
            stock_info["concepts"] = concepts

            ai_result = await ai_client.analyze_stock(
                stock_info,
                tech_context,
                news,
                prefetched.global_context,
                strategy_context=strategy_ctx,
                capital_flow_text=capital_flow_text,
                financials_text=financials_text,
                history_text=history_text,
                on_chunk=on_chunk,
                history_context=prefetched.history_context,
                strategy_key=getattr(self, "key", None),
                include_global_context=self.should_include_global_context(),
                include_learning_context=self.should_include_learning_context(),
                ui_prompt_override=ui_prompt_override,
            )
            return ai_result

        except asyncio.CancelledError:
            raise
        except (ConnectionError, TimeoutError, httpx.TimeoutException) as e:
            logger.error(
                f"[AIStrategyMixin] Network error for {row.get('ts_code', '?')}: {e}",
            )
            raise
        except Exception as e:
            logger.error(
                f"[AIStrategyMixin] Analysis failed for {row.get('ts_code', '?')}: {e}",
                exc_info=True,
            )
            return None

    # ============================================================
    # Data Enrichment Helpers
    # ============================================================

    @staticmethod
    def _compute_technical_structure(history_df, vol_ratio_threshold: float = 1.5) -> dict:
        """
        Compute MA alignment and volume trend from history DataFrame.
        Returns a dict of human-readable technical structure signals.
        """
        result = {}
        if history_df is None or history_df.empty or len(history_df) < 5:
            result["ma_alignment"] = I18n.get("ai_data_insufficient")
            result["volume_trend"] = I18n.get("ai_data_insufficient")
            result["price_trend_5d"] = I18n.get("ai_data_insufficient")
            return result

        try:
            # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
            df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
            df = df_qfq.sort_values("trade_date", ascending=True).copy()  # type: ignore[union-attr]
            close = df["close"]

            # MA Alignment
            ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else None
            ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else None
            ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            current_price = close.iloc[-1]

            if ma5 is not None and ma10 is not None and ma20 is not None:
                if ma5 > ma10 > ma20:
                    result["ma_alignment"] = (
                        f"{I18n.get('ai_ma_bullish')} (MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f})"
                    )
                elif ma5 < ma10 < ma20:
                    result["ma_alignment"] = (
                        f"{I18n.get('ai_ma_bearish')} (MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f})"
                    )
                else:
                    result["ma_alignment"] = (
                        f"{I18n.get('ai_ma_crossing')} (MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f})"
                    )

                if ma20 != 0:
                    deviation = ((current_price - ma20) / ma20) * 100
                    result["price_vs_ma20"] = f"{I18n.get('ai_ma20_deviation')} {deviation:+.1f}%"
                else:
                    result["price_vs_ma20"] = I18n.get("ai_ma20_zero")
            else:
                result["ma_alignment"] = I18n.get("ai_ma_insufficient")

            # Volume Trend (last 5 days)
            if "vol" in df.columns and len(df) >= 10:
                vol_5d = df["vol"].tail(5).mean()
                vol_10d = df["vol"].tail(10).mean()
                if vol_10d > 0:
                    vol_ratio = vol_5d / vol_10d
                    if vol_ratio < 0.7:
                        result["volume_trend"] = (
                            f"{I18n.get('ai_vol_shrink')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                        )
                    elif vol_ratio > vol_ratio_threshold:
                        result["volume_trend"] = (
                            f"{I18n.get('ai_vol_expand')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                        )
                    else:
                        result["volume_trend"] = (
                            f"{I18n.get('ai_vol_stable')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                        )
                else:
                    result["volume_trend"] = I18n.get("ai_vol_no_data")
            else:
                result["volume_trend"] = I18n.get("ai_data_insufficient")

            # 5-day Price Trend
            if len(df) >= 5:
                price_5d_ago = close.iloc[-5]
                if price_5d_ago != 0:
                    pct_5d = ((current_price - price_5d_ago) / price_5d_ago) * 100
                else:
                    pct_5d = 0.0
                closes_5d = ", ".join([f"{c:.2f}" for c in close.tail(5).tolist()])
                result["price_trend_5d"] = (
                    f"{I18n.get('ai_price_trend_5d')} {pct_5d:+.1f}% ({I18n.get('ai_close_series')}: {closes_5d})"
                )
            else:
                result["price_trend_5d"] = I18n.get("ai_data_insufficient")

        except Exception as e:
            logger.warning(
                f"[AIStrategyMixin] Technical structure computation failed: {e}",
            )
            result["ma_alignment"] = I18n.get("ai_calc_error")
            result["volume_trend"] = I18n.get("ai_calc_error")
            result["price_trend_5d"] = I18n.get("ai_calc_error")

        return result

    @staticmethod
    def _get_limit_pct(ts_code: str, name: str = "") -> float:
        """
        根据股票代码和名称判断涨跌停幅度。

        规则：
        - ST/*ST 股：±5%
        - 北交所 (8开头)：±30%
        - 创业板 (3开头) / 科创板 (68开头)：±20%
        - 主板 (其他)：±10%
        """
        if name and ("ST" in name.upper()):
            return 5.0
        if ts_code.startswith("8"):
            return 30.0
        if ts_code.startswith("3") or ts_code.startswith("68"):
            return 20.0
        return 10.0

    @staticmethod
    def _build_history_text(
        history_df: pd.DataFrame,
        ts_code: str = "",
        stock_name: str = "",
        vol_ratio_threshold: float = 1.5,
    ) -> str:
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
            df = df_qfq.sort_values("trade_date", ascending=True).reset_index(drop=True)  # type: ignore[union-attr]

            # Compute Macro Horizon
            macro_cagr = "N/A"
            macro_mdd = "N/A"
            if len(df) > 60:
                # Compute long-term CAGR and Max Drawdown on `df`
                first_close_macro = df["close"].iloc[0]
                if first_close_macro > 0:
                    macro_cagr = f"{((df['close'].iloc[-1] / first_close_macro) - 1) * 100:.1f}%"
                roll_max = df["close"].cummax()
                drawdown = (df["close"] - roll_max) / roll_max
                macro_mdd = f"{drawdown.min() * 100:.1f}%"

                # Slice for short-term K-line context
                df = df.tail(60).reset_index(drop=True)

            if len(df) < 5:
                return I18n.get("ai_history_insufficient")

            # 1. Extract Base Series
            close = df["close"]
            has_vol = "vol" in df.columns
            has_pct_chg = "pct_chg" in df.columns

            # 2. Trend & Swing Factors (with division-by-zero guards)
            first_close = close.iloc[0]
            fifth_ago_close = close.iloc[-5]
            pct_all = ((close.iloc[-1] / first_close) - 1) * 100 if first_close > 0 else 0.0
            pct_5d = ((close.iloc[-1] / fifth_ago_close) - 1) * 100 if fifth_ago_close > 0 else 0.0

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
                    f"{I18n.get('ai_consecutive_up') if sign_last > 0 else I18n.get('ai_consecutive_down')} {consec_days} {I18n.get('ai_day_unit')}"
                    if consec_days > 1
                    else I18n.get("ai_sideways")
                )

            # 3. Drawdown Factor
            rolling_max = close.cummax()
            drawdowns = (close - rolling_max) / rolling_max
            mdd = drawdowns.min() * 100

            # 4. Volume Factor (graceful fallback if 'vol' column is missing)
            vol_line = f"- {I18n.get('ai_vol_unavailable')}"
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
                    I18n.get("ai_vol_significant_expand")
                    if vol_ratio_5d > vol_ratio_threshold
                    else I18n.get("ai_vol_significant_shrink")
                    if vol_ratio_5d < 0.7
                    else I18n.get("ai_vol_stable")
                )
                vol_line = I18n.get(
                    "ai_vol_line_format",
                    label=I18n.get("ai_vol_status_label"),
                    desc=vol_desc,
                    baseline=I18n.get("ai_vol_relative_base"),
                    ratio_label=I18n.get("ai_vol_ratio_label"),
                    ratio=f"{vol_ratio_5d:.2f}",
                )

            limit_pct = AIStrategyMixin._get_limit_pct(ts_code, stock_name)

            lines = [
                I18n.get(
                    "ai_macro_cycle_header", title=I18n.get("ai_macro_cycle"), baseline=I18n.get("ai_config_baseline")
                ),
                f"- {I18n.get('ai_long_term')}: {I18n.get('ai_total_return')} {macro_cagr}，{I18n.get('ai_max_drawdown')} {macro_mdd}。",
                "",
                I18n.get(
                    "ai_trend_vol_header",
                    title=I18n.get("ai_trend_volatility"),
                    days=len(df),
                    unit=I18n.get("ai_trading_days"),
                ),
                f"- {I18n.get('ai_volatility')}: {I18n.get('ai_total_return')} {pct_all:+.2f}%，{I18n.get('ai_max_drawdown')} {mdd:.2f}%。",
                f"- {I18n.get('ai_short_momentum')}: {I18n.get('ai_5d_return')} {pct_5d:+.2f}%，{I18n.get('ai_current')} {consec_str}。",
                f"- {I18n.get('ai_ma20_bias')}: {bias_str}。",
                "",
                I18n.get("ai_section_wrapper", title=I18n.get("ai_volume_price")),
                vol_line,
                "",
                I18n.get("ai_section_wrapper", title=I18n.get("ai_recent_3d_kline")),
                I18n.get("ai_kline_header"),
            ]

            import datetime

            for _, r in df.tail(3).iterrows():
                td = r.get("trade_date")
                if isinstance(td, (datetime.date, datetime.datetime)):
                    d = td.strftime("%m%d")
                else:
                    d = str(td or "")[-4:]
                c = f"{r.get('close', 0):.2f}"
                p_val = r.get("pct_chg", 0)
                p = f"{p_val:+.2f}%" if not pd.isna(p_val) else "N/A"
                v_val = r.get("vol", 0)
                v = f"{v_val:.0f}" if (has_vol and not pd.isna(v_val)) else "N/A"

                limit_tag = ""
                if not pd.isna(p_val):
                    if p_val >= limit_pct - 0.5:
                        limit_tag = f" 🔴{I18n.get('ai_limit_up')}"
                    elif p_val <= -(limit_pct - 0.5):
                        limit_tag = f" 🟢{I18n.get('ai_limit_down')}"

                lines.append(f"{d} | {c} | {p}{limit_tag} | {v}")

            return "\n".join(lines)

        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to build history text: %s", e)
            return I18n.get("ai_history_extract_error")

    @staticmethod
    def _build_capital_flow_text(ts_code: str, prefetched: dict) -> str:
        """
        Build a human-readable capital flow summary from pre-fetched batch DataFrames.
        """
        sf = safe_float
        parts = []

        def format_amount(amount: float, source_unit: str) -> str:
            amount_yuan = amount * 10000 if source_unit == "wan_yuan" else amount
            abs_amount = abs(amount_yuan)
            if abs_amount >= 1e8:
                return f"{amount_yuan / 1e8:.2f}{I18n.get('ai_unit_billion')}"
            if abs_amount >= 1e4:
                return f"{amount_yuan / 1e4:.2f}{I18n.get('ai_unit_ten_thousand')}"
            return f"{amount_yuan:.0f}{I18n.get('ai_unit_yuan')}"

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
                parts.append(
                    f"{I18n.get('ai_main_net_inflow')}: {format_amount(net_main, 'wan_yuan')} ({I18n.get('ai_large_extra_large')})"
                )
                parts.append(f"{I18n.get('ai_total_net_inflow')}: {format_amount(net_total, 'wan_yuan')}")
            else:
                parts.append(I18n.get("ai_stock_mf_no_record"))
        else:
            parts.append(I18n.get("ai_stock_mf_na"))

        # 2. Top List (龙虎榜)
        tl_df = prefetched.get("top_list_df")
        if tl_df is not None and not tl_df.empty:
            stock_tl = tl_df[tl_df["ts_code"] == ts_code]
            if not stock_tl.empty:
                row = stock_tl.iloc[0]
                reason = row.get("reason")
                reason = reason if reason and not (isinstance(reason, float) and reason != reason) else "N/A"
                net_amt = sf(row.get("net_amount"))
                net_amount_unit = get_column_unit(tl_df, "net_amount", TOP_LIST_NET_AMOUNT_UNIT)
                parts.append(
                    f"{I18n.get('ai_top_list_yes')} ({I18n.get('ai_reason')}: {reason}, {I18n.get('ai_net_buy')}: {format_amount(net_amt, net_amount_unit)})"  # type: ignore[arg-type]
                )
            else:
                parts.append(I18n.get("ai_top_list_no"))
        else:
            parts.append(I18n.get("ai_top_list_na"))

        # 3. Northbound (北向资金)
        nb_df = prefetched.get("northbound_df")
        if nb_df is not None and not nb_df.empty:
            stock_nb = nb_df[nb_df["ts_code"] == ts_code]
            if not stock_nb.empty:
                row = stock_nb.iloc[0]
                vol = sf(row.get("vol"))
                ratio = sf(row.get("ratio"))
                parts.append(
                    f"{I18n.get('ai_north_holding')}: {vol:.0f}{I18n.get('ai_shares')}, {I18n.get('ai_circulating_ratio')}: {ratio:.2f}%"
                )
            else:
                parts.append(I18n.get("ai_north_no_record"))
        else:
            parts.append(I18n.get("ai_north_na"))

        return "\n".join(parts)

    async def _build_multi_period_financials(
        self, ts_code: str, cache: typing.Any, prefetched: dict | None = None
    ) -> str:
        """
        构建多期财务趋势数据。

        获取最近8个季度的财务数据，分析ROE、毛利率、营收/利润增速趋势。

        Args:
            ts_code: 股票代码
            cache: 数据缓存实例
            prefetched: 预取的辅助数据

        Returns:
            财务趋势文本
        """

        try:
            if prefetched and ts_code in prefetched and "financial_history" in prefetched[ts_code]:
                df = prefetched[ts_code]["financial_history"]
            else:
                df = await cache.get_financial_reports_history(ts_code, periods=8)

            if df is None or df.empty:
                return I18n.get("ai_financial_insufficient")

            parts = []

            if "roe" in df.columns:
                roe_values = df["roe"].dropna().tolist()
                if roe_values:
                    roe_str = ", ".join([f"{v:.2f}" for v in roe_values[:4]])
                    parts.append(
                        I18n.get(
                            "ai_roe_trend_format",
                            label=I18n.get("ai_roe_trend"),
                            count=len(roe_values),
                            unit=I18n.get("ai_recent_quarters"),
                            values=roe_str,
                        )
                    )

            if "grossprofit_margin" in df.columns:
                margin_values = df["grossprofit_margin"].dropna().tolist()
                if margin_values:
                    margin_str = ", ".join([f"{v:.2f}" for v in margin_values[:4]])
                    parts.append(f"{I18n.get('ai_gross_margin_trend')}: {margin_str}")

            if "or_yoy" in df.columns:
                or_yoy_values = df["or_yoy"].dropna().tolist()
                if or_yoy_values:
                    or_yoy_str = ", ".join([f"{v:.2f}" for v in or_yoy_values[:4]])
                    parts.append(f"{I18n.get('ai_revenue_growth_trend')}: {or_yoy_str}")

            if "netprofit_yoy" in df.columns:
                profit_yoy_values = df["netprofit_yoy"].dropna().tolist()
                if profit_yoy_values:
                    profit_yoy_str = ", ".join([f"{v:.2f}" for v in profit_yoy_values[:4]])
                    parts.append(f"{I18n.get('ai_profit_growth_trend')}: {profit_yoy_str}")

            if "n_cashflow_act" in df.columns and "n_income_attr_p" in df.columns:
                cf_values = df["n_cashflow_act"].dropna().tolist()
                profit_values = df["n_income_attr_p"].dropna().tolist()
                if cf_values and profit_values:
                    latest_cf = cf_values[0] if cf_values else 0
                    latest_profit = profit_values[0] if profit_values else 0
                    if latest_profit > 0:
                        cf_ratio = latest_cf / latest_profit
                        parts.append(f"{I18n.get('ai_cf_profit_ratio')}: {cf_ratio:.2f}")

            return "\n".join(parts) if parts else I18n.get("ai_financial_insufficient")

        except Exception as e:
            logger.warning("[AIMixin] Failed to build multi-period financials for %s: %s", ts_code, e)
            return I18n.get("ai_financial_fetch_failed")

    async def _build_auxiliary_data_text(
        self,
        ts_code: str,
        cache: typing.Any,
        prefetched: dict | None = None,
    ) -> str:
        """
        构建辅助数据文本。

        包含审计意见、主营构成、分红记录、质押比例、股东信息等辅助信息。

        Args:
            ts_code: 股票代码
            cache: 数据缓存实例
            prefetched: 预取的辅助数据（避免 N+1 查询）

        Returns:
            辅助数据文本
        """

        lines = []
        has_data = False

        try:
            # 审计意见
            if prefetched and ts_code in prefetched and "audit" in prefetched[ts_code]:
                audit_df = prefetched[ts_code]["audit"]
            else:
                audit_df = await cache.get_fina_audit(ts_code)

            if audit_df is not None and not audit_df.empty:
                latest_audit = audit_df.iloc[0]
                audit_result = latest_audit.get("audit_result", I18n.get("ai_unknown"))
                lines.append(f"- {I18n.get('ai_audit_opinion')}: {audit_result}")
                has_data = True

            # 主营构成
            if prefetched and ts_code in prefetched and "mainbz" in prefetched[ts_code]:
                top_business = prefetched[ts_code]["mainbz"]
            else:
                top_business = await cache.get_fina_mainbz(ts_code)
            if top_business is not None and not top_business.empty:
                total_sales = top_business["bz_sales"].sum()
                if total_sales > 0:
                    biz_items = []
                    for _, row in top_business.head(3).iterrows():
                        bz_name = row.get("bz_item", I18n.get("ai_unknown"))
                        bz_sales = row.get("bz_sales", 0)
                        ratio = (bz_sales / total_sales * 100) if total_sales > 0 else 0
                        biz_items.append(f"{bz_name}({ratio:.1f}%)")
                    lines.append(f"- {I18n.get('ai_main_business')}: {', '.join(biz_items)}")
                    has_data = True

            # 分红记录
            if prefetched and ts_code in prefetched and "dividend" in prefetched[ts_code]:
                dividend_df = prefetched[ts_code]["dividend"]
            else:
                dividend_df = await cache.get_dividend(ts_code)

            if dividend_df is not None and not dividend_df.empty:
                recent_div = dividend_df.head(3)
                div_items = []
                for _, row in recent_div.iterrows():
                    end_date = str(row.get("end_date", ""))[:4]
                    div_proc = row.get("div_proc", "")
                    div_items.append(f"{end_date}{I18n.get('ai_year_suffix')}{div_proc}")
                lines.append(f"- {I18n.get('ai_recent_dividend')}: {', '.join(div_items)}")
                has_data = True

            # 质押比例
            if prefetched and ts_code in prefetched and "pledge" in prefetched[ts_code]:
                pledge_df = prefetched[ts_code]["pledge"]
            else:
                pledge_df = await cache.get_pledge_stat(ts_code)

            if pledge_df is not None and not pledge_df.empty:
                latest_pledge = pledge_df.iloc[0]
                pledge_ratio = latest_pledge.get("pledge_ratio", 0)
                if pledge_ratio and pledge_ratio > 0:
                    warning = f" ⚠️ {I18n.get('ai_pledge_high_warning')}" if pledge_ratio > 30 else ""
                    lines.append(f"- {I18n.get('ai_pledge_ratio')}: {pledge_ratio:.1f}%{warning}")
                    has_data = True

            # 第一大股东
            if prefetched and ts_code in prefetched and "holders" in prefetched[ts_code]:
                holders_df = prefetched[ts_code]["holders"]
            else:
                holders_df = await cache.get_top10_holders(ts_code)

            if holders_df is not None and not holders_df.empty:
                latest_holders = holders_df[holders_df["end_date"] == holders_df["end_date"].max()]
                if not latest_holders.empty:
                    top_holder = latest_holders.iloc[0].get("holder_name", I18n.get("ai_unknown"))
                    top_ratio = latest_holders.iloc[0].get("hold_ratio", 0)
                    lines.append(
                        f"- {I18n.get('ai_top_holder')}: {top_holder} ({I18n.get('ai_holder_share')}{top_ratio:.2f}%)"
                    )
                    has_data = True

            # 股东人数
            if prefetched and ts_code in prefetched and "holdernumber" in prefetched[ts_code]:
                holder_num = prefetched[ts_code]["holdernumber"]
            else:
                holder_num = await cache.get_stk_holdernumber(ts_code)
            if holder_num is not None and not holder_num.empty:
                latest = holder_num.iloc[0]
                curr_num = latest.get("holder_num", 0)
                change_pct = latest.get("holder_num_ratio")
                if curr_num:
                    if change_pct is not None and not pd.isna(change_pct):
                        if change_pct < -5:
                            trend = f"↓ {I18n.get('ai_holder_concentrate')}"
                        elif change_pct > 5:
                            trend = f"↑ {I18n.get('ai_holder_disperse')}"
                        else:
                            trend = f"→ {I18n.get('ai_holder_stable')}"
                        lines.append(
                            f"- {I18n.get('ai_holder_count')}: {int(curr_num):,}{I18n.get('ai_households')} ({trend} {change_pct:+.1f}%)"
                        )
                    else:
                        lines.append(f"- {I18n.get('ai_holder_count')}: {int(curr_num):,}{I18n.get('ai_households')}")
                    has_data = True

        except Exception as e:
            logger.warning("[AIMixin] Failed to build auxiliary data for %s: %s", ts_code, e)

        if has_data:
            return "\n".join(lines) + "\n"
        return I18n.get("ai_no_auxiliary_data")

    async def _build_macro_context(self, cache: typing.Any, as_of_date=None) -> str:
        """
        构建宏观经济环境上下文。

        L3 修复：新增 Shibor 利率注入，对价值投资和固收相关策略有重要参考价值。
        B-P1-1 修复：新增 as_of_date 参数，在历史回放场景下按日期截断宏观数据，防止前视偏差。

        Args:
            cache: 数据缓存实例
            as_of_date: 截止日期（含），None 表示不限制

        Returns:
            宏观经济环境文本
        """
        lines = [I18n.get("ai_section_wrapper", title=I18n.get("macro_env_title"))]
        has_data = False

        try:
            macro = await cache.get_macro_economy(as_of_date=as_of_date)
            if macro is not None and not macro.empty:
                latest = macro.iloc[0]

                m2_yoy = latest.get("m2_yoy")
                if m2_yoy is not None:
                    lines.append(f"- {I18n.get('macro_m2_yoy')}: {m2_yoy:.2f}%")
                    has_data = True

                cpi = latest.get("cpi")
                if cpi is not None:
                    lines.append(f"- {I18n.get('macro_cpi')}: {cpi:.2f}")
                    has_data = True

                ppi = latest.get("ppi")
                if ppi is not None:
                    lines.append(f"- {I18n.get('macro_ppi')}: {ppi:.2f}")
                    has_data = True

            shibor = await cache.get_shibor_latest(as_of_date=as_of_date)
            if shibor is not None and not shibor.empty:
                shibor_latest = shibor.iloc[0]

                on_rate = shibor_latest.get("on")
                if on_rate is not None:
                    lines.append(f"- {I18n.get('macro_shibor_overnight')}: {on_rate:.2f}%")
                    has_data = True

                w1_rate = shibor_latest.get("1w")
                if w1_rate is not None:
                    lines.append(f"- {I18n.get('macro_shibor_1w')}: {w1_rate:.2f}%")
                    has_data = True

                m3_rate = shibor_latest.get("3m")
                if m3_rate is not None:
                    lines.append(f"- {I18n.get('macro_shibor_3m')}: {m3_rate:.2f}%")
                    has_data = True

        except Exception as e:
            logger.warning("[AIMixin] Failed to build macro context: %s", e)

        if has_data:
            return "\n".join(lines) + "\n"
        return ""

    @staticmethod
    def _build_financials_text(row: dict) -> str:
        """
        Build a human-readable financials summary from the stock_info data.
        The screening data already contains key financial metrics from the join.
        """
        parts = []

        parts.append(f"{I18n.get('ai_pe_ttm')}: {fmt_val(row.get('pe_ttm'))}")
        parts.append(f"{I18n.get('ai_pb')}: {fmt_val(row.get('pb'))}")
        parts.append(f"{I18n.get('ai_roe')}: {fmt_val(row.get('roe'), suffix='%')}")
        parts.append(f"{I18n.get('ai_gross_margin')}: {fmt_val(row.get('grossprofit_margin'), suffix='%')}")
        parts.append(f"{I18n.get('ai_debt_ratio')}: {fmt_val(row.get('debt_to_assets'), suffix='%')}")
        parts.append(f"{I18n.get('ai_revenue_yoy')}: {fmt_val(row.get('or_yoy'), suffix='%')}")
        parts.append(f"{I18n.get('ai_profit_yoy')}: {fmt_val(row.get('netprofit_yoy'), suffix='%')}")

        tmv = safe_float(row.get("total_mv"), default=None)  # type: ignore[union-attr]
        if tmv is not None:
            tmv_str = f"{tmv / 10000:.2f}{I18n.get('ai_billion_yuan')}"
        else:
            tmv_str = "N/A"
        parts.append(f"{I18n.get('ai_total_mv')}: {tmv_str}")

        parts.append(f"{I18n.get('ai_dividend_yield_ttm')}: {fmt_val(row.get('dv_ttm'), suffix='%')}")

        pe_val = safe_float(row.get("pe_ttm"), default=None)  # type: ignore[union-attr]
        growth_val = safe_float(row.get("netprofit_yoy"), default=None)  # type: ignore[union-attr]
        if pe_val is not None and growth_val is not None and growth_val > 0:
            peg = pe_val / growth_val
            parts.append(f"{I18n.get('ai_peg')}: {peg:.2f} ({I18n.get('ai_peg_pe_profit_growth')})")
        else:
            parts.append(I18n.get("ai_peg_na"))

        return "\n".join(parts)
