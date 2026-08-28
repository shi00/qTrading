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
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from cachetools import TTLCache

import pandas as pd

from core.i18n import I18n, Message
from data.constants import SAFE_BACKTEST_LEARNING_OFFSET_DAYS, SAFE_LIVE_LEARNING_OFFSET_DAYS
from data.external.news_fetcher import NewsFetcher
from services.ai_service import AIService
from strategies.ai_context import (
    ContextBuilder,
    PreFetchedContext,
    _build_auxiliary_data_text,
    _build_capital_flow_text,
    _build_financials_text,
    _build_history_text,
    _build_macro_context,
    _build_multi_period_financials,
    _compute_technical_structure,
)
from utils.async_utils import gather_for_shutdown_cleanup, gather_return_exceptions_propagating_cancel
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.technical_analysis import TechnicalAnalysis
from utils.time_utils import get_now, to_yyyymmdd_str

logger = logging.getLogger(__name__)


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
            Value: Callable[[row: dict, prefetched: PreFetchedContext], tuple[str, bool]]
                where the bool is `is_valid` (True = inject block, False = skip).
    """

    enable_ai_analysis: bool = True

    _HISTORY_CACHE_MAX = 4
    _HISTORY_CACHE_TTL = 120

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_builders: dict[str, ContextBuilder] = {}
        self._history_cache: TTLCache = TTLCache(maxsize=self._HISTORY_CACHE_MAX, ttl=self._HISTORY_CACHE_TTL)
        # UX-2.3: 供 retry_single 复用（_last_prefetched 避免重新预取 news）
        self._last_candidates_df: pd.DataFrame | None = None
        self._last_prefetched: PreFetchedContext | None = None
        self._last_dp = None

    def register_context_builder(self, name: str, builder: ContextBuilder) -> None:
        """
        Register a custom context builder for this strategy.

        Args:
            name: Context block name (e.g., "turnover", "sector", "market")
            builder: Function(row: dict, prefetched: PreFetchedContext) -> tuple[str, bool]
                Returns (text, is_valid); block is injected only when is_valid is True.
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
                "[%s] Sorted %d candidates by %s (descending) for AI analysis",
                self.__class__.__name__,
                len(df),
                col,
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

    @staticmethod
    def compute_learning_as_of(trade_date_raw, is_backtest: bool) -> date:
        import datetime

        from utils.time_utils import get_now, parse_date

        as_of = None
        if trade_date_raw is not None:
            try:
                as_of = parse_date(str(trade_date_raw))
                if isinstance(as_of, datetime.datetime):
                    as_of = as_of.date()
            except (ValueError, TypeError) as e:
                if is_backtest:
                    raise ValueError(
                        f"Cannot parse trade_date for backtest learning context: {trade_date_raw!r}. "
                        f"Refusing to use unbounded learning context to prevent lookahead bias."
                    ) from e
                severity = classify_severity(e)
                log_level = logger.error if severity == "system" else logger.warning
                log_level("AI context error: %s", e, exc_info=True)
        if as_of is None and is_backtest:
            raise ValueError(
                f"Cannot compute learning as_of for backtest: trade_date is {trade_date_raw!r}. "
                f"Refusing to use unbounded learning context to prevent lookahead bias."
            )
        if as_of is None:
            as_of = get_now().date() - datetime.timedelta(days=SAFE_LIVE_LEARNING_OFFSET_DAYS)
        elif is_backtest:
            as_of = as_of - datetime.timedelta(days=SAFE_BACKTEST_LEARNING_OFFSET_DAYS)
        return as_of

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def run_ai_analysis(
        self,
        candidates_df: pd.DataFrame,
        context: dict,
        max_stocks: int | None = None,
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

        # P1-2: 新批次开始时清空 retry 复用缓存，避免预取失败时 retry_single
        # 跨"代"复用上一批次的 _last_candidates_df/_last_prefetched（状态错配）。
        # 仅批量预取完成后才重新赋值，保证 _last_ai_context 与数据同源。
        self._last_candidates_df = None
        self._last_prefetched = None
        self._last_dp = None

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
                    Message("ai_not_configured"),
                )
            return candidates_df

        # --- Guard: AI External Data Acknowledged? (Task 2.2) ---
        # NOTE(lazy): ack 状态读取移到 analyze_one 内部检查，避免阻断 cache 预取验证类测试
        # (lookahead_bias / oversold_prompt_alignment)。ceiling: 该 guard 在并发循环每个候选
        # 调用一次。upgrade: 改为预读一次后传入 analyze_one，或迁移到 AIService.analyze_stock
        # 入口检查。
        ai_external_acknowledged = ConfigHandler.is_ai_external_acknowledged()
        if not ai_external_acknowledged:
            logger.info(
                "[AIStrategyMixin] AI external data policy not acknowledged — skipping cloud AI calls",
            )
            if on_progress:
                on_progress(
                    0,
                    0,
                    Message("ai_external_acknowledgment_prompt"),
                )

        # --- Guard: DataProcessor Available? ---
        if dp is None:
            logger.warning(
                "[AIStrategyMixin] DataProcessor missing from context — returning math-only results",
            )
            return candidates_df

        # --- Guard: Backtest AI Disabled? ---
        if context.get("_disable_ai"):
            logger.info(
                "[AIStrategyMixin] AI disabled by backtest config — returning math-only results",
            )
            return candidates_df

        # --- Guard: Empty Input ---
        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        # --- Cost Control: Cap candidates ---
        cap = max_stocks or ConfigHandler.get_ai_max_candidates()
        if len(candidates_df) > cap:
            logger.info(
                "[AIStrategyMixin] Capping candidates from %d to %d",
                len(candidates_df),
                cap,
            )
            candidates_df = candidates_df.head(cap)

        # --- Calculate News as_of ---
        news_as_of = None
        trade_date_raw = context.get("trade_date")
        if trade_date_raw is not None:
            try:
                from utils.time_utils import parse_date

                parsed = parse_date(str(trade_date_raw))
                if isinstance(parsed, datetime):
                    news_as_of = parsed.date()
                elif isinstance(parsed, date):
                    news_as_of = parsed
            except (ValueError, TypeError) as e:
                if context.get("is_backtest"):
                    raise ValueError(
                        f"Cannot parse trade_date for backtest news context: {trade_date_raw!r}. "
                        f"Refusing to use unbounded news context to prevent lookahead bias."
                    ) from e
                severity = classify_severity(e)
                log_level = logger.error if severity == "system" else logger.warning
                log_level("AI context error: %s", e, exc_info=True)

        # --- Fetch Global Context ONCE ---
        # --- Pre-fetch Learning Context ONCE for the entire batch ---
        history_context = ""
        if self.should_include_learning_context():
            try:
                from data.persistence.review_manager import ReviewManager

                rm = ReviewManager()
                as_of = self.compute_learning_as_of(context.get("trade_date"), context.get("is_backtest", False))
                history_context = await rm.get_learning_context(as_of=as_of)
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning(
                    "[AIStrategyMixin] Failed to pre-fetch learning context: %s",
                    DataSanitizer.sanitize_error(e),
                )

        global_context = ""
        if self.should_include_global_context():
            try:
                global_context = await NewsFetcher.get_us_major_moves(as_of=news_as_of)
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to fetch global context: %s", DataSanitizer.sanitize_error(e))

        # --- Pre-fetch Concepts for all candidates (N+1 optimization) ---
        concepts_map = {}
        all_ts_codes = candidates_df["ts_code"].tolist()
        try:
            concepts_map = await dp.cache.get_concepts(all_ts_codes)  # type: ignore[union-attr]
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to pre-fetch concepts: %s", DataSanitizer.sanitize_error(e))

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
                bulk_history_df = await dp.cache.quote_dao.get_daily_quotes(  # type: ignore[union-attr]
                    ts_code_list=all_ts_codes,
                    start_date=start_date,
                    end_date=end_date,
                    suppress_errors=False,
                )
                self._history_cache[cache_key] = bulk_history_df
            if bulk_history_df is not None and not bulk_history_df.empty:
                for code, group in bulk_history_df.groupby("ts_code"):
                    prefetched_history[code] = group

            # 2. Background Pipelining for News (concurrency follows analysis concurrency)
            _news_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
            news_sem = asyncio.Semaphore(_news_concurrency)

            async def bg_fetch_news(code):
                async with news_sem:
                    try:
                        return await NewsFetcher.get_stock_news(code, limit=5, as_of=news_as_of)
                    except asyncio.CancelledError:
                        # R2: 传播取消信号，配合优雅停机
                        raise
                    except (ValueError, RuntimeError, OSError, ConnectionError):
                        return []

            news_tasks = {code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes}
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Ultimate Pipeline init failed: %s", DataSanitizer.sanitize_error(e))

        # --- Batch Pre-Fetch: Capital Flow Data (Moneyflow, TopList, Northbound) ---
        # Fetch once for the trade date, filter per-stock in the loop (0ms per stock)
        trade_date = self._normalize_trade_date_for_cache(context.get("trade_date"))
        try:
            if trade_date is None:
                trade_date = self._normalize_trade_date_for_cache(await dp.get_latest_trade_date())  # type: ignore[union-attr]
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to get latest trade date: %s", DataSanitizer.sanitize_error(e))

        moneyflow_df = pd.DataFrame()
        top_list_df = pd.DataFrame()
        northbound_df = pd.DataFrame()
        top_inst_df = pd.DataFrame()

        if trade_date:
            try:
                moneyflow_df = await dp.cache.quote_dao.get_moneyflow(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch moneyflow: %s", DataSanitizer.sanitize_error(e))

            try:
                top_list_df = await dp.cache.quote_dao.get_top_list(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch top_list: %s", DataSanitizer.sanitize_error(e))

            try:
                northbound_df = await dp.cache.quote_dao.get_northbound(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch northbound: %s", DataSanitizer.sanitize_error(e))

            # Phase 3C：top_inst 龙虎榜机构席位预取（auxiliary 数据，权限不足时由 _build_stale_section 标注）
            try:
                top_inst_df = await dp.cache.get_top_inst_batch(all_ts_codes, as_of_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch top_inst: %s", DataSanitizer.sanitize_error(e))

        logger.info(
            "[AIStrategyMixin] Pre-fetched capital data: moneyflow=%d, top_list=%d, northbound=%d, top_inst=%d",
            len(moneyflow_df),
            len(top_list_df),
            len(northbound_df),
            len(top_inst_df),
        )

        # --- Pre-fetch Auxiliary Data (Audit, Dividend, Pledge, Holders) ---
        auxiliary_data = {}
        try:
            auxiliary_data = await dp.cache.prefetch_auxiliary_data(all_ts_codes, as_of_date=trade_date)
            logger.info("[AIStrategyMixin] Pre-fetched auxiliary data for %d stocks", len(auxiliary_data))
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to pre-fetch auxiliary data: %s", DataSanitizer.sanitize_error(e))

        # --- Bundle all pre-fetched data into PreFetchedContext ---
        prefetched = PreFetchedContext(
            capital={
                "moneyflow_df": moneyflow_df,
                "top_list_df": top_list_df,
                "northbound_df": northbound_df,
                "top_inst_df": top_inst_df,
                "trade_date": trade_date,
            },
            history=prefetched_history,
            concepts_map=concepts_map,
            news_tasks=news_tasks,
            history_context=history_context,
            global_context=global_context,
            trade_date=trade_date,
            auxiliary_data=auxiliary_data,
            news_as_of=news_as_of,
            is_backtest=bool(context.get("is_backtest")),
        )

        # --- Strategy-specific prefetch hook ---
        prefetched = await self._prefetch_strategy_specific(candidates_df, context, prefetched)
        # UX-2.3: 保留 batch state 供 retry_single 复用（避免重新预取 news/history）
        self._last_candidates_df = candidates_df
        self._last_prefetched = prefetched
        self._last_dp = dp

        # D7: Prefetch macro_context once before concurrent loop to avoid thundering herd
        try:
            prefetched.macro_context = await _build_macro_context(dp.cache, as_of_date=prefetched.trade_date)
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 数据预取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to prefetch macro context: %s", DataSanitizer.sanitize_error(e))

        # --- Concurrent Analysis ---
        concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
        screening_sem = asyncio.Semaphore(concurrency)
        stream_enabled = concurrency == 1

        total_tasks = len(candidates_df)
        completed = 0
        final_rows: list[dict] = []
        on_stream_start = context.get("on_stream_start") if stream_enabled else None
        on_card_start = context.get("on_card_start") if not stream_enabled else None

        if on_progress:
            on_progress(0, total_tasks, Message("ai_progress_init"))

        on_card_error = context.get("on_card_error")  # UX-2.3: 单股失败回调

        async def analyze_one(row_data: dict) -> dict | None:
            async with screening_sem:
                if dp and dp.is_cancelled():
                    return None  # 已取消，不触发 on_card_error
                stock_name = row_data.get("name", row_data.get("ts_code", "?"))
                on_chunk = on_stream_start(stock_name) if on_stream_start else None
                if on_card_start:
                    on_card_start(stock_name)
                # Task 2.2: 未确认 AI 外发政策时跳过云端调用（保持 cache 预取等数据流不变）
                if not ai_external_acknowledged:
                    return None  # 预期跳过，不触发 on_card_error
                try:
                    hist_df = prefetched.history.get(row_data.get("ts_code"), pd.DataFrame())
                    news_list = []
                    if row_data.get("ts_code") in prefetched.news_tasks:
                        news_list = await prefetched.news_tasks[row_data.get("ts_code")]
                    res = await self._mixin_analyze_single(
                        row_data,
                        dp,
                        ai_client,
                        prefetched,
                        on_chunk=on_chunk,
                        history_df=hist_df,
                        news=news_list,
                        ui_prompt_override=ui_prompt_override,
                        vol_ratio_threshold=context.get("params", {}).get("vol_ratio_threshold", 1.5),
                    )
                    if res is None:
                        # UX-2.3: 软失败（_mixin_analyze_single 内部已日志）
                        if on_card_error:
                            on_card_error(stock_name, I18n.get("ai_card_analysis_failed"))
                        return None
                    return self._build_result_row(row_data, res)
                except asyncio.CancelledError:
                    raise  # R2 合规
                except Exception as e:
                    # UX-2.3: 网络错误等（_mixin_analyze_single raise 的异常）
                    if on_card_error:
                        on_card_error(stock_name, DataSanitizer.sanitize_error(e))
                    raise  # 继续传播给 gather 收集（保持现有行为）
                finally:
                    if on_chunk and hasattr(on_chunk, "final_flush"):
                        on_chunk.final_flush()

        # Batch task creation to avoid unbounded coroutine explosion
        _BATCH_SIZE = 20
        all_records = candidates_df.to_dict("records")
        results: list = []

        # F4-ST-001: try/finally 确保任何路径（含 CancelledError）下 news_tasks 被清理，
        # 避免后台 HTTP 任务句柄/连接泄漏。gather_return_exceptions_propagating_cancel
        # 在 CancelledError 时直接 raise，若无 try/finally，下方 for 循环与
        # _cancel_orphan_news_tasks 调用将不会执行（成为死代码）。
        try:
            for batch_start in range(0, len(all_records), _BATCH_SIZE):
                if dp and dp.is_cancelled():
                    break
                batch = all_records[batch_start : batch_start + _BATCH_SIZE]
                batch_tasks = [asyncio.create_task(analyze_one(row_data)) for row_data in batch]
                batch_results = await gather_return_exceptions_propagating_cancel(*batch_tasks)
                results.extend(batch_results)

            for res in results:
                # F4-ST-001: 防御性 — CancelledError 继承 BaseException 而非 Exception,
                # 若未来 gather 实现变化导致 CancelledError 漏入 results, 此处显式 raise (R2)
                if isinstance(res, asyncio.CancelledError):
                    raise res
                completed += 1
                if isinstance(res, Exception):
                    # UX-2.3: on_card_error 已在 analyze_one 内调用, 此处仅日志
                    error_info = classify_error(res, context="general")
                    logger.error(
                        "[AIStrategyMixin] Task error (%s): %s", error_info["code"], DataSanitizer.sanitize_error(res)
                    )
                elif isinstance(res, dict):
                    final_rows.append(res)
                    if on_result:
                        on_result(res)
                if on_progress:
                    on_progress(
                        completed,
                        total_tasks,
                        Message("ai_progress_done", {"done": completed, "total": total_tasks}),
                    )

            logger.info(
                "[AIStrategyMixin] Complete. %d/%d processed, %d valid results",
                completed,
                total_tasks,
                len(final_rows),
            )
        finally:
            # F4-ST-001: 无论正常完成、CancelledError 或异常，都清理 news_tasks
            # _cancel_orphan_news_tasks 内部用 asyncio.gather(return_exceptions=True)（不 raise），finally 中安全
            await self._cancel_orphan_news_tasks(prefetched)

        if not final_rows:
            return candidates_df  # Fallback: return math-only results

        result_df = pd.DataFrame(final_rows)

        # Log partial analysis: if some stocks were skipped due to errors,
        # record it in logs so downstream consumers (UI, CSV, DB) are not polluted.
        error_count = total_tasks - len(final_rows)
        if error_count > 0:
            logger.info(
                "[AIStrategyMixin] Partial analysis: %d/%d stocks skipped or failed",
                error_count,
                total_tasks,
            )

        return result_df.sort_values("ai_score", ascending=False)

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def retry_single(self, stock_name: str, context: dict) -> None:
        """UX-2.3: 重试单股 AI 分析。

        v3: 不走 run_ai_analysis（会覆盖 _last_candidates_df 与 _last_prefetched），
        改为复用 _last_prefetched 直接调 _mixin_analyze_single。
        避免重新预取 news/history（节省网络开销）+ 避免 _last_candidates_df 被覆盖。
        """
        on_card_error = context.get("on_card_error")  # R1-7: 前移到早退检查之前
        if self._last_candidates_df is None or self._last_prefetched is None:
            logger.warning("[AIStrategyMixin] retry_single: no cached batch state")
            if on_card_error:
                on_card_error(stock_name, I18n.get("ai_card_analysis_failed"))
            return
        df = self._last_candidates_df
        mask = (df["name"] == stock_name) | (df["ts_code"] == stock_name)
        single_records = df.loc[mask].to_dict("records")
        if not single_records:
            logger.warning("[AIStrategyMixin] retry_single: stock %s not found", stock_name)
            if on_card_error:
                on_card_error(stock_name, I18n.get("ai_card_analysis_failed"))
            return
        row_data = single_records[0]
        prefetched = self._last_prefetched
        ai_client = AIService()
        dp = context.get("data_processor") or self._last_dp
        # P1-1: retry_single 不调用 on_card_start（避免重复建卡）。
        # 调用方（ScreenerViewModel.retry_single_stock）已先将失败卡复用为占位卡；
        # 重试语义是"更新已有卡"，此处再触发 on_card_start（start_stream_card 追加）
        # 会造成同名股票出现两张卡。
        on_result = context.get("on_result") or context.get("on_stream_result")
        name_str = row_data.get("name", row_data.get("ts_code", "?"))
        try:
            ts_code = row_data.get("ts_code")
            hist_df = prefetched.history.get(ts_code, pd.DataFrame())
            news_list: list = []
            if ts_code in prefetched.news_tasks:
                news_task = prefetched.news_tasks[ts_code]
                if news_task.done() and news_task.cancelled():
                    # UX-2.3 v4 P1-1: 原 news task 已被 cancel（批次取消场景），降级重新拉取
                    if prefetched.news_as_of:
                        news_list = await NewsFetcher.get_stock_news(ts_code, limit=5, as_of=prefetched.news_as_of)
                    else:
                        news_list = []
                else:
                    # task 未完成或正常完成，await 复用（CancelledError 由外层传播）
                    news_list = await news_task
            elif prefetched.news_as_of:
                news_list = await NewsFetcher.get_stock_news(ts_code, limit=5, as_of=prefetched.news_as_of)
            res = await self._mixin_analyze_single(
                row_data,
                dp,
                ai_client,
                prefetched,
                history_df=hist_df,
                news=news_list,
                vol_ratio_threshold=context.get("params", {}).get("vol_ratio_threshold", 1.5),
            )
            if res is None:
                if on_card_error:
                    on_card_error(name_str, I18n.get("ai_card_analysis_failed"))
                return
            result_row = self._build_result_row(row_data, res)
            if result_row and on_result:
                on_result(result_row)
            elif on_card_error:
                # I-1: score==0（模型判定无信号）时 _build_result_row 返回 None。
                # 调用方 retry_single_stock 已把失败卡转为 is_analyzing=True 占位卡，
                # 此处必须终结之，否则卡片永久停留在"分析中"且无重试按钮。
                on_card_error(name_str, I18n.get("ai_card_analysis_failed"))
        except asyncio.CancelledError:
            raise  # R2 合规
        except Exception as e:
            if on_card_error:
                on_card_error(name_str, DataSanitizer.sanitize_error(e))
            logger.error("[AIStrategyMixin] retry_single failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    @staticmethod
    async def _cancel_orphan_news_tasks(prefetched: PreFetchedContext) -> None:
        """Cancel any orphan news fetch tasks that were never awaited.

        R2 合规：调用 ``task.cancel()`` 后必须 ``await`` 被取消的 task 实际终止，
        避免 HTTP 连接/文件句柄等资源泄漏（被取消的 task 仍可能持有资源直到调度器回收）。
        """
        pending = [task for task in prefetched.news_tasks.values() if not task.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        # 等待被取消的 task 完成；CancelledError 和其他异常都被吞没（已记录日志或预期）
        await gather_for_shutdown_cleanup(*pending)

    def _build_result_row(self, row_data: dict, res: object) -> dict | None:
        """把单股 AI 结果组装为结果行；无效（None/异常/score==0）返回 None。"""
        if isinstance(res, Exception) or res is None:
            return None
        score_val = res.get("score", 0)  # type: ignore[union-attr]
        if score_val == 0:
            return None

        row_dict = dict(row_data)
        summary_raw = res.get("summary", "")  # type: ignore[union-attr]
        summary = str(summary_raw) if summary_raw else ""
        confidence = res.get("confidence")  # type: ignore[union-attr]
        uncertainty = res.get("uncertainty_factors")  # type: ignore[union-attr]

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

        row_dict["ai_score"] = (
            round(min(100, max(0, float(score_val))), 1) if isinstance(score_val, (int, float)) else 0
        )
        row_dict["ai_reason"] = summary
        thinking_raw = res.get("thinking", "")  # type: ignore[union-attr]
        row_dict["thinking"] = str(thinking_raw) if thinking_raw else ""
        row_dict["confidence"] = (
            min(100, max(1, int(confidence))) if isinstance(confidence, (int, float, Decimal)) else 50
        )
        return row_dict

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def _mixin_analyze_single(
        self,
        row: dict,
        dp,
        ai_client: AIService,
        prefetched: PreFetchedContext,
        on_chunk: Callable | None = None,
        history_df: pd.DataFrame | None = None,
        news: list | None = None,
        ui_prompt_override: str | None = None,
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
            tech_structure = _compute_technical_structure(history_df, vol_ratio_threshold=vol_ratio_threshold)
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
                news = await NewsFetcher.get_stock_news(ts_code, limit=5, as_of=prefetched.news_as_of)

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
                    block_text, block_valid = builder(row, prefetched)
                    if block_valid and block_text:
                        custom_context_blocks.append(f"### {name}\n{block_text}")
                # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出 AI 上下文构建异常. upgrade: 策略层重构时统一走 classify_error.
                except Exception as e:
                    logger.warning(
                        "[AIStrategyMixin] Context builder '%s' failed: %s", name, DataSanitizer.sanitize_error(e)
                    )

            if custom_context_blocks:
                strategy_ctx = strategy_ctx + "\n\n" + "\n\n".join(custom_context_blocks)

            # 6. Capital Flow (filter pre-fetched batch data by ts_code)
            capital_labels: list[str] = []
            capital_flow_text = _build_capital_flow_text(
                ts_code,
                prefetched.capital or {},
                labels_out=capital_labels,
            )

            # 7. Financials (extract from stock_info which already has screening data)
            financial_labels: list[str] = []
            base_financials = _build_financials_text(row, labels_out=financial_labels)

            # 7a. Multi-Period Financial Trends (Phase 1.2)
            multi_period_labels: list[str] = []
            multi_period_text, multi_period_valid = await _build_multi_period_financials(
                ts_code,
                dp.cache,
                prefetched.auxiliary_data,
                as_of_date=prefetched.trade_date,
                labels_out=multi_period_labels,
            )

            # 7b. Auxiliary Data (Phase 1.2)
            auxiliary_labels: list[str] = []
            auxiliary_text, auxiliary_valid = await _build_auxiliary_data_text(
                ts_code,
                dp.cache,
                prefetched.auxiliary_data,
                as_of_date=prefetched.trade_date,
                labels_out=auxiliary_labels,
            )

            # 7c. Macro Context

            # Combine all financial context
            financials_parts = [base_financials]
            if multi_period_valid:
                financials_parts.append(
                    f"\n{I18n.get('ai_section_wrapper', title=I18n.get('ai_multi_period_trend'))}\n{multi_period_text}"
                )
                financial_labels.extend(multi_period_labels)
            if auxiliary_valid:
                financials_parts.append(
                    f"\n{I18n.get('ai_section_wrapper', title=I18n.get('ai_auxiliary_data'))}\n{auxiliary_text}"
                )
                financial_labels.extend(auxiliary_labels)
            if prefetched.macro_context:
                financials_parts.append(f"\n{prefetched.macro_context}")
                # Phase 2A.1 §4.1 v1.6.0 P0-1：拆分 ai_label_macro 为
                # ai_label_shibor（points_120，shibor 段落）+ ai_label_macro_full
                # （points_2000，cn_m/cn_cpi/cn_ppi 段落）。filter_available_labels
                # 按档位动态过滤（points_120 时 ai_label_macro_full 被移除）
                financial_labels.append("ai_label_shibor")
                financial_labels.append("ai_label_macro_full")

            financials_text = "\n".join(financials_parts)

            # 7d. History Feature Summary (Level-3: Factor Extraction + Summarization)
            history_labels: list[str] = []
            history_text = _build_history_text(
                history_df,  # type: ignore[arg-type]
                ts_code=ts_code,
                stock_name=row.get("name", ""),
                vol_ratio_threshold=vol_ratio_threshold,
                labels_out=history_labels,
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
                is_backtest=prefetched.is_backtest,
                financial_labels=financial_labels,
                capital_labels=capital_labels,
                history_labels=history_labels,
            )
            return ai_result

        except asyncio.CancelledError:
            raise
        except (ConnectionError, TimeoutError, httpx.TimeoutException) as e:
            logger.error(
                "[AIStrategyMixin] Network error for %s: %s",
                row.get("ts_code", "?"),
                DataSanitizer.sanitize_error(e),
            )
            raise
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出单股 AI 分析异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error(
                "[AIStrategyMixin] Analysis failed for %s: %s",
                row.get("ts_code", "?"),
                DataSanitizer.sanitize_error(e),
            )
            logger.debug("[AIStrategyMixin] Analysis failed traceback:", exc_info=True)
            return None
