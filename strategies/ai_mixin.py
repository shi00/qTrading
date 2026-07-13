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
from decimal import Decimal
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from cachetools import TTLCache

import pandas as pd

from data.constants import SAFE_BACKTEST_LEARNING_OFFSET_DAYS, SAFE_LIVE_LEARNING_OFFSET_DAYS
from data.constants import TOP_LIST_NET_AMOUNT_UNIT, get_column_unit
from data.external.news_fetcher import NewsFetcher
from services.ai_service import AIService
from strategies.utils import fmt_val, safe_float
from core.i18n import I18n
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.technical_analysis import TechnicalAnalysis
from utils.time_utils import get_now, to_yyyymmdd_str

logger = logging.getLogger(__name__)


def _build_stale_section(
    api_name: str,
    df: pd.DataFrame,
    formatter: typing.Callable[[pd.DataFrame], str],
    date_column: str = "ann_date",
) -> str:
    """统一 stale 标注格式。

    Phase 2A.1 §4.4.5 v1.6.0 P1-7：模块级辅助函数，作为 ``AIStrategyMixin``
    的基类静态方法供子类复用，避免在多 ``_build_*_text`` 方法中重复实现
    stale 检查逻辑。

    Args:
        api_name: 该子段落对应的 API 名（如 "share_float" / "cn_m"）
        df: 该子段落的数据 DataFrame
        formatter: 格式化函数，接收 df 返回该子段落的文本
        date_column: df 中代表"最后更新日期"的列名，默认 "ann_date"。
            v1.8.0 P2-D 修订：由各 _build_*_text 调用时传入实际列名
            （如 trade_date/date/month）。

    Returns:
        - df 为空 → 返回空字符串（不注入）
        - api_name 不在当前档位覆盖内 → 返回 stale 前缀 + formatter(df)
        - api_name 在档位覆盖内 → 返回 formatter(df)（无 stale 标注）
    """
    if df.empty:
        return ""
    from data.external.tushare_client import TushareClient

    client = TushareClient()
    if not client.is_api_covered_by_tier(api_name):
        last_update = (
            pd.to_datetime(df[date_column].max()).strftime("%Y-%m-%d") if date_column in df.columns else "未知"
        )
        return f"【数据停止更新，最后更新：{last_update}】\n" + formatter(df)
    return formatter(df)


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
    trade_date: str | None = None

    indicators: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_stats: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)
    market_context_str: str = ""
    macro_context: str = ""
    auxiliary_data: dict = field(default_factory=dict)
    news_as_of: date | None = None
    is_backtest: bool = False


ContextBuilder = Callable[[dict, PreFetchedContext], tuple[str, bool]]


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
                    I18n.get("ai_not_configured"),
                )
            return candidates_df

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
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning(
                    "[AIStrategyMixin] Failed to pre-fetch learning context: %s",
                    e,
                )

        global_context = ""
        if self.should_include_global_context():
            try:
                global_context = await NewsFetcher.get_us_major_moves(as_of=news_as_of)
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to fetch global context: %s", DataSanitizer.sanitize_error(e))

        # --- Pre-fetch Concepts for all candidates (N+1 optimization) ---
        concepts_map = {}
        all_ts_codes = candidates_df["ts_code"].tolist()
        try:
            concepts_map = await dp.cache.get_concepts(all_ts_codes)  # type: ignore[union-attr]
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
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

            # 2. Background Pipelining for News (concurrency follows analysis concurrency)
            _news_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
            news_sem = asyncio.Semaphore(_news_concurrency)

            async def bg_fetch_news(code):
                async with news_sem:
                    try:
                        return await NewsFetcher.get_stock_news(code, limit=5, as_of=news_as_of)
                    except (ValueError, RuntimeError, OSError, ConnectionError):
                        return []

            news_tasks = {code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes}
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Ultimate Pipeline init failed: %s", DataSanitizer.sanitize_error(e))

        # --- Batch Pre-Fetch: Capital Flow Data (Moneyflow, TopList, Northbound) ---
        # Fetch once for the trade date, filter per-stock in the loop (0ms per stock)
        trade_date = self._normalize_trade_date_for_cache(context.get("trade_date"))
        try:
            if trade_date is None:
                trade_date = self._normalize_trade_date_for_cache(await dp.get_latest_trade_date())  # type: ignore[union-attr]
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to get latest trade date: %s", DataSanitizer.sanitize_error(e))

        moneyflow_df = pd.DataFrame()
        top_list_df = pd.DataFrame()
        northbound_df = pd.DataFrame()
        top_inst_df = pd.DataFrame()

        if trade_date:
            try:
                moneyflow_df = await dp.cache.get_moneyflow(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch moneyflow: %s", DataSanitizer.sanitize_error(e))

            try:
                top_list_df = await dp.cache.get_top_list(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch top_list: %s", DataSanitizer.sanitize_error(e))

            try:
                northbound_df = await dp.cache.get_northbound(trade_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                logger.warning("[AIStrategyMixin] Failed to pre-fetch northbound: %s", DataSanitizer.sanitize_error(e))

            # Phase 3C：top_inst 龙虎榜机构席位预取（auxiliary 数据，权限不足时由 _build_stale_section 标注）
            try:
                top_inst_df = await dp.cache.get_top_inst_batch(all_ts_codes, as_of_date=trade_date)  # type: ignore[union-attr]
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
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
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
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

        # D7: Prefetch macro_context once before concurrent loop to avoid thundering herd
        try:
            prefetched.macro_context = await self._build_macro_context(dp.cache, as_of_date=prefetched.trade_date)
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
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
            on_progress(0, total_tasks, I18n.get("ai_progress_init"))

        async def analyze_one(row_data: dict) -> dict | None:
            async with screening_sem:
                if dp and dp.is_cancelled():
                    return None
                stock_name = row_data.get("name", row_data.get("ts_code", "?"))
                on_chunk = on_stream_start(stock_name) if on_stream_start else None
                if on_card_start:
                    on_card_start(stock_name)
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
                    return self._build_result_row(row_data, res)
                finally:
                    if on_chunk and hasattr(on_chunk, "final_flush"):
                        on_chunk.final_flush()

        # Batch task creation to avoid unbounded coroutine explosion
        _BATCH_SIZE = 20
        all_records = candidates_df.to_dict("records")
        results: list = []

        for batch_start in range(0, len(all_records), _BATCH_SIZE):
            if dp and dp.is_cancelled():
                break
            batch = all_records[batch_start : batch_start + _BATCH_SIZE]
            batch_tasks = [asyncio.create_task(analyze_one(row_data)) for row_data in batch]
            batch_results = await gather_return_exceptions_propagating_cancel(*batch_tasks)
            results.extend(batch_results)

        for res in results:
            if isinstance(res, asyncio.CancelledError):
                self._cancel_orphan_news_tasks(prefetched)
                raise res
            completed += 1
            if isinstance(res, Exception):
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
                    I18n.get("ai_progress_done", done=completed, total=total_tasks),
                )

        logger.info(
            "[AIStrategyMixin] Complete. %d/%d processed, %d valid results",
            completed,
            total_tasks,
            len(final_rows),
        )

        self._cancel_orphan_news_tasks(prefetched)

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

    @staticmethod
    def _cancel_orphan_news_tasks(prefetched: PreFetchedContext) -> None:
        """Cancel any orphan news fetch tasks that were never awaited."""
        for _code, task in prefetched.news_tasks.items():
            if not task.done():
                task.cancel()

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
                # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
                except Exception as e:
                    logger.warning(
                        "[AIStrategyMixin] Context builder '%s' failed: %s", name, DataSanitizer.sanitize_error(e)
                    )

            if custom_context_blocks:
                strategy_ctx = strategy_ctx + "\n\n" + "\n\n".join(custom_context_blocks)

            # 6. Capital Flow (filter pre-fetched batch data by ts_code)
            capital_labels: list[str] = []
            capital_flow_text = self._build_capital_flow_text(
                ts_code,
                prefetched.capital or {},
                labels_out=capital_labels,
            )

            # 7. Financials (extract from stock_info which already has screening data)
            financial_labels: list[str] = []
            base_financials = self._build_financials_text(row, labels_out=financial_labels)

            # 7a. Multi-Period Financial Trends (Phase 1.2)
            multi_period_labels: list[str] = []
            multi_period_text, multi_period_valid = await self._build_multi_period_financials(
                ts_code,
                dp.cache,
                prefetched.auxiliary_data,
                as_of_date=prefetched.trade_date,
                labels_out=multi_period_labels,
            )

            # 7b. Auxiliary Data (Phase 1.2)
            auxiliary_labels: list[str] = []
            auxiliary_text, auxiliary_valid = await self._build_auxiliary_data_text(
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
            history_text = self._build_history_text(
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
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error(
                "[AIStrategyMixin] Analysis failed for %s: %s",
                row.get("ts_code", "?"),
                DataSanitizer.sanitize_error(e),
            )
            logger.debug("[AIStrategyMixin] Analysis failed traceback:", exc_info=True)
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
            severity = classify_severity(e)
            if severity == "system":
                logger.error(
                    "[AIStrategyMixin] Technical structure computation system error: %s",
                    e,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "[AIStrategyMixin] Technical structure computation failed (transient): %s",
                    e,
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
        labels_out: list[str] | None = None,
    ) -> str:
        """
        Build a semantic summary of recent price action using quantitative factor extraction.
        This provides the LLM with "vision" into the actual OHLCV structure.

        NOTE: Output intentionally excludes XML wrapper tags because the caller
              (ai_service.py) already wraps this in <recent_price_action>.

        Args:
            labels_out: 输出参数，收集成功注入的标签 key；哨兵/异常时不注册
        """
        if history_df is None or history_df.empty:
            return I18n.get("ai_history_insufficient")

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
                # 哨兵：数据不足，不注册标签
                return I18n.get("ai_history_insufficient")

            # 1. Extract Base Series
            close = df["close"]

            # 全 NaN close → 无有效价格数据，返回哨兵不注册标签
            if close.isna().all():
                return I18n.get("ai_history_insufficient")
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

            for r in df.tail(3).to_dict("records"):
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

            # 实际数据产出，注册标签
            if labels_out is not None:
                labels_out.append("ai_label_kline")

            return "\n".join(lines)

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIStrategyMixin] Failed to build history text: %s", DataSanitizer.sanitize_error(e))
            if labels_out is not None:
                labels_out.clear()
            return I18n.get("ai_history_extract_error")

    @staticmethod
    def _build_capital_flow_text(ts_code: str, prefetched: dict, labels_out: list[str] | None = None) -> str:
        """
        Build a human-readable capital flow summary from pre-fetched batch DataFrames.

        Args:
            ts_code: 股票代码
            prefetched: 预取的资金数据
            labels_out: 输出参数，收集成功注入的标签 key；异常时自动清空
        """
        try:
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

            mf_df = prefetched.get("moneyflow_df")
            if mf_df is not None and not mf_df.empty:
                stock_mf = mf_df[mf_df["ts_code"] == ts_code]
                if not stock_mf.empty:
                    row = stock_mf.iloc[0]
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
                    if labels_out is not None:
                        labels_out.append("ai_label_main_flow")
                else:
                    parts.append(I18n.get("ai_stock_mf_no_record"))
            else:
                parts.append(I18n.get("ai_stock_mf_na"))

            tl_df = prefetched.get("top_list_df")
            if tl_df is not None and not tl_df.empty:
                stock_tl = tl_df[tl_df["ts_code"] == ts_code]
                if not stock_tl.empty:
                    row = stock_tl.iloc[0]
                    reason = row.get("reason")
                    reason = (
                        reason if reason and not (isinstance(reason, (float, Decimal)) and reason != reason) else "N/A"
                    )
                    net_amt = sf(row.get("net_amount"))
                    net_amount_unit = get_column_unit(tl_df, "net_amount", TOP_LIST_NET_AMOUNT_UNIT)
                    parts.append(
                        f"{I18n.get('ai_top_list_yes')} ({I18n.get('ai_reason')}: {reason}, {I18n.get('ai_net_buy')}: {format_amount(net_amt, net_amount_unit)})"  # type: ignore[arg-type]
                    )
                    if labels_out is not None:
                        labels_out.append("ai_label_top_list")
                else:
                    parts.append(I18n.get("ai_top_list_no"))
            else:
                parts.append(I18n.get("ai_top_list_na"))

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
                    if labels_out is not None:
                        labels_out.append("ai_label_northbound")
                else:
                    parts.append(I18n.get("ai_north_no_record"))
            else:
                parts.append(I18n.get("ai_north_na"))

            # Phase 3C：top_inst 龙虎榜机构席位（auxiliary 数据，遵循 §4.4.5 stale 标注）
            # 与 top_list/northbound 不同：top_inst 是 Phase 3C 新增段落，按 §4.4.5 设计
            # 空 df 不注入占位文本（不污染 prompt）；非空但档位不覆盖时由 _build_stale_section 标注。
            ti_df = prefetched.get("top_inst_df")
            if ti_df is not None and not ti_df.empty:
                stock_ti = ti_df[ti_df["ts_code"] == ts_code]
                if not stock_ti.empty:

                    def _format_top_inst(df: pd.DataFrame) -> str:
                        row = df.iloc[0]
                        net_amt = sf(row.get("net_amount"))
                        return (
                            f"{I18n.get('ai_top_inst_yes')} ({I18n.get('ai_net_buy')}: "
                            f"{format_amount(net_amt, TOP_LIST_NET_AMOUNT_UNIT)})"
                        )

                    section = _build_stale_section("top_inst", stock_ti, _format_top_inst, date_column="trade_date")
                    if section:
                        parts.append(section)
                        if labels_out is not None:
                            labels_out.append("ai_label_top_inst")

            return "\n".join(parts)

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[AIStrategyMixin] Failed to build capital flow text for %s: %s",
                ts_code,
                DataSanitizer.sanitize_error(e),
            )
            if labels_out is not None:
                labels_out.clear()
            return I18n.get("ai_capital_flow_fetch_failed")

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _build_multi_period_financials(
        self,
        ts_code: str,
        cache: typing.Any,
        prefetched: dict | None = None,
        as_of_date: str | None = None,
        labels_out: list[str] | None = None,
    ) -> tuple[str, bool]:
        """
        构建多期财务趋势数据。

        获取最近8个季度的财务数据，分析ROE、毛利率、营收/利润增速趋势。

        Args:
            ts_code: 股票代码
            cache: 数据缓存实例
            prefetched: 预取的辅助数据
            as_of_date: 截止日期（含），None 表示不限制，防止前视偏差
            labels_out: 输出参数，收集成功注入的标签 key

        Returns:
            (财务趋势文本, is_valid)：is_valid=False 表示数据不足/失败，调用方应跳过注入。
        """

        try:
            if prefetched and ts_code in prefetched and "financial_history" in prefetched[ts_code]:
                df = prefetched[ts_code]["financial_history"]
            else:
                df = await cache.get_financial_reports_history(ts_code, periods=8, as_of_date=as_of_date)

            if df is None or df.empty:
                return ("", False)

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
                    if labels_out is not None:
                        labels_out.append("ai_label_roe_trend")

            if "grossprofit_margin" in df.columns:
                margin_values = df["grossprofit_margin"].dropna().tolist()
                if margin_values:
                    margin_str = ", ".join([f"{v:.2f}" for v in margin_values[:4]])
                    parts.append(f"{I18n.get('ai_gross_margin_trend')}: {margin_str}")
                    if labels_out is not None:
                        labels_out.append("ai_label_gross_margin_trend")

            if "or_yoy" in df.columns:
                or_yoy_values = df["or_yoy"].dropna().tolist()
                if or_yoy_values:
                    or_yoy_str = ", ".join([f"{v:.2f}" for v in or_yoy_values[:4]])
                    parts.append(f"{I18n.get('ai_revenue_growth_trend')}: {or_yoy_str}")
                    if labels_out is not None:
                        labels_out.append("ai_label_revenue_growth_trend")

            if "netprofit_yoy" in df.columns:
                profit_yoy_values = df["netprofit_yoy"].dropna().tolist()
                if profit_yoy_values:
                    profit_yoy_str = ", ".join([f"{v:.2f}" for v in profit_yoy_values[:4]])
                    parts.append(f"{I18n.get('ai_profit_growth_trend')}: {profit_yoy_str}")
                    if labels_out is not None:
                        labels_out.append("ai_label_profit_growth_trend")

            if "n_cashflow_act" in df.columns and "n_income_attr_p" in df.columns:
                cf_values = df["n_cashflow_act"].dropna().tolist()
                profit_values = df["n_income_attr_p"].dropna().tolist()
                if cf_values and profit_values:
                    latest_cf = cf_values[0] if cf_values else 0
                    latest_profit = profit_values[0] if profit_values else 0
                    if latest_profit > 0:
                        cf_ratio = latest_cf / latest_profit
                        parts.append(f"{I18n.get('ai_cf_profit_ratio')}: {cf_ratio:.2f}")
                        if labels_out is not None:
                            labels_out.append("ai_label_cf_profit_ratio")

            if "total_assets" in df.columns and "goodwill" in df.columns:
                ta_values = df["total_assets"].dropna().tolist()
                gw_values = df["goodwill"].dropna().tolist()
                if ta_values and gw_values and ta_values[0] and ta_values[0] > 0:
                    gw_ratio = (gw_values[0] / ta_values[0]) * 100
                    parts.append(f"{I18n.get('ai_goodwill_ratio')}: {gw_ratio:.2f}%")
                    if labels_out is not None:
                        labels_out.append("ai_label_goodwill_ratio")

            if "money_cap" in df.columns:
                mc_values = df["money_cap"].dropna().tolist()
                if mc_values:
                    parts.append(
                        f"{I18n.get('ai_monetary_capital')}: {mc_values[0] / 1e8:.2f}{I18n.get('ai_unit_billion')}"
                    )
                    if labels_out is not None:
                        labels_out.append("ai_label_monetary_capital")

            if "accounts_receiv" in df.columns:
                ar_values = df["accounts_receiv"].dropna().tolist()
                if ar_values:
                    parts.append(
                        f"{I18n.get('ai_accounts_receiv')}: {ar_values[0] / 1e8:.2f}{I18n.get('ai_unit_billion')}"
                    )
                    if labels_out is not None:
                        labels_out.append("ai_label_accounts_receiv")

            return ("\n".join(parts), True) if parts else ("", False)

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[AIMixin] Failed to build multi-period financials for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            if labels_out is not None:
                labels_out.clear()
            return ("", False)

    @staticmethod
    def _format_forecast_section(df: pd.DataFrame) -> str:
        """格式化业绩预告段落（Phase 3A）。

        入参 df 由 ``get_fina_forecast_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
        仅返回每只股票最新一期预告，故直接取 ``iloc[0]``。

        格式示例：``- 业绩预告: 2024Q3 预增 50.0%-70.0%（公告日 2024-10-15）``
        """
        if df is None or df.empty:
            return ""
        row = df.iloc[0]
        end_date = row.get("end_date")
        ann_date = row.get("ann_date")
        forecast_type = row.get("type") or I18n.get("ai_unknown")
        p_min = row.get("p_change_min")
        p_max = row.get("p_change_max")

        # end_date 为 Date 类型（季度末日期），转换为 "YYYYQN" 格式
        quarter_str = str(end_date) if end_date is not None else I18n.get("ai_unknown")
        try:
            d = pd.to_datetime(str(end_date))
            q = (d.month - 1) // 3 + 1
            quarter_str = f"{d.year}Q{q}"
        except (ValueError, TypeError):
            logger.warning("[AIStrategyMixin] Failed to parse forecast end_date to quarter: %r", end_date)

        # 拼接预告幅度区间
        range_str = ""
        if p_min is not None and not pd.isna(p_min) and p_max is not None and not pd.isna(p_max):
            range_str = f" {float(p_min):.1f}%-{float(p_max):.1f}%"

        # 公告日格式化为 YYYY-MM-DD
        ann_str = str(ann_date) if ann_date is not None else I18n.get("ai_unknown")
        try:
            ann_str = pd.to_datetime(str(ann_date)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            logger.warning("[AIStrategyMixin] Failed to format forecast ann_date: %r", ann_date)

        return (
            f"- {I18n.get('ai_forecast')}: {quarter_str} {forecast_type}{range_str}"
            f"（{I18n.get('ai_forecast_ann_date')}: {ann_str}）"
        )

    @staticmethod
    def _format_pledge_detail_section(df: pd.DataFrame) -> str:
        """格式化股权质押明细段落（Phase 3B）。

        入参 df 由 ``get_pledge_detail_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
        仅返回每只股票最新一期明细，故直接取 ``iloc[0]``。

        格式示例：``- 质押明细: 质押股数 1000.00 万股（无限售 800.00，有限售 200.00），占总股本 35.2%``
        """
        if df is None or df.empty:
            return ""
        row = df.iloc[0]
        pledge_amount = row.get("pledge_amount")
        unlimited = row.get("unlimited_pledge_amount")
        limited = row.get("limited_pledge_amount")
        total_pledge = row.get("total_pledge_amount")
        pledge_ratio = row.get("pledge_ratio")

        parts: list[str] = []
        if pledge_amount is not None and not pd.isna(pledge_amount):
            parts.append(f"{I18n.get('ai_pledge_amount')}: {float(pledge_amount):.2f}")
        if total_pledge is not None and not pd.isna(total_pledge):
            parts.append(f"{I18n.get('ai_pledge_total')}: {float(total_pledge):.2f}")
        if unlimited is not None and not pd.isna(unlimited):
            parts.append(f"{I18n.get('ai_pledge_unlimited')}: {float(unlimited):.2f}")
        if limited is not None and not pd.isna(limited):
            parts.append(f"{I18n.get('ai_pledge_limited')}: {float(limited):.2f}")

        if not parts:
            return ""

        detail_str = "（" + "，".join(parts) + "）"
        ratio_str = (
            f"，{I18n.get('ai_pledge_ratio')} {float(pledge_ratio):.1f}%"
            if pledge_ratio is not None and not pd.isna(pledge_ratio)
            else ""
        )
        return f"- {I18n.get('ai_pledge_detail')}: {detail_str}{ratio_str}"

    @staticmethod
    def _format_share_float_section(df: pd.DataFrame) -> str:
        """格式化限售解禁段落（Phase 3D）。

        入参 df 由 ``get_share_float_upcoming_batch`` 返回，包含未来解禁记录。
        最多展示 3 条最近解禁事件。

        格式示例：``- 限售解禁: 2024-08-15 解禁 1000.00 万股（5.2%）；2024-09-20 解禁 500.00 万股（2.6%）``
        """
        if df is None or df.empty:
            return ""
        items: list[str] = []
        for _, row in df.head(3).iterrows():
            float_date = row.get("float_date")
            float_share = row.get("float_share")
            float_ratio = row.get("float_ratio")
            if hasattr(float_date, "strftime"):
                date_str = float_date.strftime("%Y-%m-%d")
            else:
                date_str = str(float_date) if float_date is not None else "N/A"
            share_str = f"{float(float_share):.2f}" if float_share is not None and not pd.isna(float_share) else "N/A"
            ratio_str = f"（{float(float_ratio):.1f}%）" if float_ratio is not None and not pd.isna(float_ratio) else ""
            items.append(f"{date_str} 解禁 {share_str} 万股{ratio_str}")
        if not items:
            return ""
        return f"- {I18n.get('ai_share_float')}: " + "；".join(items)

    @staticmethod
    def _format_holder_trade_section(df: pd.DataFrame) -> str:
        """格式化股东增减持段落（Phase 3E）。

        入参 df 由 ``get_stk_holdertrade_batch`` 返回，包含近期增减持记录。
        最多展示 3 条最近记录。

        格式示例：``- 股东增减持: 2024-06-01 张三 增持 100.00 万股（增持比例 0.5%）``
        """
        if df is None or df.empty:
            return ""
        recent = df.sort_values("ann_date", ascending=False).head(3)
        items: list[str] = []
        for _, row in recent.iterrows():
            ann_date = row.get("ann_date")
            date_str = str(ann_date) if ann_date is not None and not pd.isna(ann_date) else "N/A"
            holder_name = row.get("holder_name")
            name_str = str(holder_name) if holder_name is not None and not pd.isna(holder_name) else "N/A"
            in_de = row.get("in_de")
            if in_de == "IN":
                action_str = I18n.get("ai_holder_trade_increase")
            elif in_de == "DE":
                action_str = I18n.get("ai_holder_trade_decrease")
            else:
                action_str = "N/A"
            change_vol = row.get("change_vol")
            vol_str = f"{float(change_vol):.2f}" if change_vol is not None and not pd.isna(change_vol) else "N/A"
            change_ratio = row.get("change_ratio")
            ratio_str = (
                f"（{float(change_ratio):.2f}%）" if change_ratio is not None and not pd.isna(change_ratio) else ""
            )
            items.append(f"{date_str} {name_str} {action_str} {vol_str} 股{ratio_str}")
        if not items:
            return ""
        return f"- {I18n.get('ai_holder_trade')}: " + "；".join(items)

    @staticmethod
    def _format_express_section(df: pd.DataFrame) -> str:
        """格式化业绩快报段落（Phase 3G §4.3.4）。

        入参 df 由 ``get_express_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
        仅返回每只股票最新一期快报，故直接取 ``iloc[0]``。

        业绩快报早于正式财报 30-60 天公告，AI 可提前反应业绩拐点。
        营收/净利/扣非单位由元转换为亿元（÷1e8）保留 2 位小数。

        格式示例：``- 业绩快报: 2024Q3 营收 50.00亿（+25.0% YoY）、净利 8.00亿（+40.0% YoY）、扣非 7.50亿（+35.0% YoY）（公告日 2024-10-15）``
        """
        if df is None or df.empty:
            return ""
        row = df.iloc[0]
        end_date = row.get("end_date")
        ann_date = row.get("ann_date")

        # end_date 为 Date 类型（季度末日期），转换为 "YYYYQN" 格式
        quarter_str = str(end_date) if end_date is not None else I18n.get("ai_unknown")
        try:
            d = pd.to_datetime(str(end_date))
            q = (d.month - 1) // 3 + 1
            quarter_str = f"{d.year}Q{q}"
        except (ValueError, TypeError):
            logger.warning("[AIStrategyMixin] Failed to parse express end_date to quarter: %r", end_date)

        # 拼接营收/净利/扣非段落（单位转换：元 → 亿元）
        parts: list[str] = []
        revenue = row.get("revenue")
        yoy_sales = row.get("yoy_sales")
        if revenue is not None and not pd.isna(revenue):
            rev_str = f"{I18n.get('ai_express_revenue')}: {float(revenue) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
            if yoy_sales is not None and not pd.isna(yoy_sales):
                rev_str += f"（{float(yoy_sales):+.1f}% YoY）"
            parts.append(rev_str)

        n_income = row.get("n_income")
        yoy_profit = row.get("yoy_profit")
        if n_income is not None and not pd.isna(n_income):
            ni_str = f"{I18n.get('ai_express_n_income')}: {float(n_income) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
            if yoy_profit is not None and not pd.isna(yoy_profit):
                ni_str += f"（{float(yoy_profit):+.1f}% YoY）"
            parts.append(ni_str)

        deduct_profit = row.get("deduct_profit")
        yoy_dedu_np = row.get("yoy_dedu_np")
        if deduct_profit is not None and not pd.isna(deduct_profit):
            dp_str = f"{I18n.get('ai_express_deduct')}: {float(deduct_profit) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
            if yoy_dedu_np is not None and not pd.isna(yoy_dedu_np):
                dp_str += f"（{float(yoy_dedu_np):+.1f}% YoY）"
            parts.append(dp_str)

        if not parts:
            return ""

        # 公告日格式化为 YYYY-MM-DD
        ann_str = str(ann_date) if ann_date is not None else I18n.get("ai_unknown")
        try:
            ann_str = pd.to_datetime(str(ann_date)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            logger.warning("[AIStrategyMixin] Failed to format express ann_date: %r", ann_date)

        return (
            f"- {I18n.get('ai_express')}: {quarter_str} "
            f"{'、'.join(parts)}（{I18n.get('ai_express_ann_date')}: {ann_str}）"
        )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _build_auxiliary_data_text(
        self,
        ts_code: str,
        cache: typing.Any,
        prefetched: dict | None = None,
        as_of_date: str | None = None,
        labels_out: list[str] | None = None,
    ) -> tuple[str, bool]:
        """
        构建辅助数据文本。

        包含审计意见、主营构成、分红记录、质押比例、股东信息等辅助信息。

        Args:
            ts_code: 股票代码
            cache: 数据缓存实例
            prefetched: 预取的辅助数据（避免 N+1 查询）
            as_of_date: 截止日期（含），None 表示不限制，防止前视偏差
            labels_out: 输出参数，收集成功注入的标签 key

        Returns:
            (辅助数据文本, is_valid)：is_valid=False 表示无数据/异常，调用方应跳过注入。
        """

        lines = []
        has_data = False

        try:
            if prefetched and ts_code in prefetched and "audit" in prefetched[ts_code]:
                audit_df = prefetched[ts_code]["audit"]
            else:
                audit_df = await cache.get_fina_audit(ts_code, as_of_date=as_of_date)

            if audit_df is not None and not audit_df.empty:
                latest_audit = audit_df.iloc[0]
                audit_result = latest_audit.get("audit_result", I18n.get("ai_unknown"))
                lines.append(f"- {I18n.get('ai_audit_opinion')}: {audit_result}")
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_audit")

            if prefetched and ts_code in prefetched and "mainbz" in prefetched[ts_code]:
                top_business = prefetched[ts_code]["mainbz"]
            else:
                top_business = await cache.get_fina_mainbz(ts_code, as_of_date=as_of_date)
            if top_business is not None and not top_business.empty:
                total_sales = top_business["bz_sales"].sum()
                if total_sales > 0:
                    biz_items = []
                    for row in top_business.head(3).to_dict("records"):
                        bz_name = row.get("bz_item", I18n.get("ai_unknown"))
                        bz_sales = row.get("bz_sales", 0)
                        ratio = (bz_sales / total_sales * 100) if total_sales > 0 else 0
                        biz_items.append(f"{bz_name}({ratio:.1f}%)")
                    lines.append(f"- {I18n.get('ai_main_business')}: {', '.join(biz_items)}")
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_main_business")

            if prefetched and ts_code in prefetched and "dividend" in prefetched[ts_code]:
                dividend_df = prefetched[ts_code]["dividend"]
            else:
                dividend_df = await cache.get_dividend(ts_code, as_of_date=as_of_date)

            if dividend_df is not None and not dividend_df.empty:
                recent_div = dividend_df.head(3)
                div_items = []
                for row in recent_div.to_dict("records"):
                    end_date = str(row.get("end_date", ""))[:4]
                    div_proc = row.get("div_proc", "")
                    div_items.append(f"{end_date}{I18n.get('ai_year_suffix')}{div_proc}")
                lines.append(f"- {I18n.get('ai_recent_dividend')}: {', '.join(div_items)}")
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_dividend")

            if prefetched and ts_code in prefetched and "pledge" in prefetched[ts_code]:
                pledge_df = prefetched[ts_code]["pledge"]
            else:
                pledge_df = await cache.get_pledge_stat(ts_code, as_of_date=as_of_date)

            if pledge_df is not None and not pledge_df.empty:
                latest_pledge = pledge_df.iloc[0]
                pledge_ratio = latest_pledge.get("pledge_ratio", 0)
                if pledge_ratio and pledge_ratio > 0:
                    warning = f" ⚠️ {I18n.get('ai_pledge_high_warning')}" if pledge_ratio > 30 else ""
                    lines.append(f"- {I18n.get('ai_pledge_ratio')}: {pledge_ratio:.1f}%{warning}")
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_pledge")

            # Phase 3B：股权质押明细（pledge_detail）— 与 pledge_stat 互补，提供更细粒度的质押信息
            if prefetched and ts_code in prefetched and "pledge_detail" in prefetched[ts_code]:
                pledge_detail_df = prefetched[ts_code]["pledge_detail"]
            else:
                pledge_detail_df = await cache.get_pledge_detail(ts_code, as_of_date=as_of_date)

            if pledge_detail_df is not None and not pledge_detail_df.empty:
                pledge_detail_line = _build_stale_section(
                    "pledge_detail",
                    pledge_detail_df,
                    self._format_pledge_detail_section,
                    date_column="end_date",
                )
                if pledge_detail_line:
                    lines.append(pledge_detail_line)
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_pledge_detail")

            # Phase 3D：限售解禁（share_float）— 未来解禁压力，与 pledge_stat/pledge_detail 互补
            if prefetched and ts_code in prefetched and "share_float" in prefetched[ts_code]:
                share_float_df = prefetched[ts_code]["share_float"]
            else:
                share_float_df = await cache.get_share_float_upcoming(ts_code, as_of_date=as_of_date)

            if share_float_df is not None and not share_float_df.empty:
                share_float_line = _build_stale_section(
                    "share_float",
                    share_float_df,
                    self._format_share_float_section,
                    date_column="ann_date",
                )
                if share_float_line:
                    lines.append(share_float_line)
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_share_float")

            # Phase 3E：股东增减持（stk_holdertrade）— 产业资本信号，与 share_float 互补
            if prefetched and ts_code in prefetched and "holdertrade" in prefetched[ts_code]:
                holdertrade_df = prefetched[ts_code]["holdertrade"]
            else:
                holdertrade_df = await cache.get_stk_holdertrade(ts_code, as_of_date=as_of_date)

            if holdertrade_df is not None and not holdertrade_df.empty:
                holdertrade_line = _build_stale_section(
                    "stk_holdertrade",
                    holdertrade_df,
                    self._format_holder_trade_section,
                    date_column="ann_date",
                )
                if holdertrade_line:
                    lines.append(holdertrade_line)
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_holder_trade")

            if prefetched and ts_code in prefetched and "holders" in prefetched[ts_code]:
                holders_df = prefetched[ts_code]["holders"]
            else:
                holders_df = await cache.get_top10_holders(ts_code, as_of_date=as_of_date)

            if holders_df is not None and not holders_df.empty:
                if "ann_date" in holders_df.columns and holders_df["ann_date"].notna().any():
                    latest_holders = holders_df[holders_df["ann_date"] == holders_df["ann_date"].max()]
                else:
                    latest_holders = holders_df[holders_df["end_date"] == holders_df["end_date"].max()]
                if not latest_holders.empty:
                    top_holder = latest_holders.iloc[0].get("holder_name", I18n.get("ai_unknown"))
                    top_ratio = latest_holders.iloc[0].get("hold_ratio", 0)
                    lines.append(
                        f"- {I18n.get('ai_top_holder')}: {top_holder} ({I18n.get('ai_holder_share')}{top_ratio:.2f}%)"
                    )
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_top_holder")

            if prefetched and ts_code in prefetched and "holdernumber" in prefetched[ts_code]:
                holder_num = prefetched[ts_code]["holdernumber"]
            else:
                holder_num = await cache.get_stk_holdernumber(ts_code, as_of_date=as_of_date)
            if holder_num is not None and not holder_num.empty:
                if "ann_date" in holder_num.columns and holder_num["ann_date"].notna().any():
                    latest_holder_num = holder_num[holder_num["ann_date"] == holder_num["ann_date"].max()]
                    latest = latest_holder_num.iloc[0] if not latest_holder_num.empty else holder_num.iloc[0]
                else:
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
                    if labels_out is not None:
                        labels_out.append("ai_label_holder_count")

            # Phase 3A：业绩预告（fina_forecast）— 表已建 + DAO 读取已激活，注入 AI
            if prefetched and ts_code in prefetched and "forecast" in prefetched[ts_code]:
                forecast_df = prefetched[ts_code]["forecast"]
            else:
                forecast_df = await cache.get_fina_forecast(ts_code, as_of_date=as_of_date)

            if forecast_df is not None and not forecast_df.empty:
                forecast_line = _build_stale_section(
                    "forecast",
                    forecast_df,
                    self._format_forecast_section,
                    date_column="ann_date",
                )
                if forecast_line:
                    lines.append(forecast_line)
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_forecast")

            # Phase 3F-2：申万行业（sw_industry_member 全局快照，月度更新，无 stale 标注）
            # prefetched[ts_code]["sw_industry"] 为 sw_l2_name 字符串（cache_manager 已分发）
            # 注入前检查档位覆盖：points_120 降级时 index_classify/index_member_all 不在覆盖内，
            # filter_available_labels 已过滤 ai_label_sw_industry 标签；此处同步跳过 body 注入，
            # 避免 <available_data> 块不列但 prompt body 仍注入的设计矛盾。
            if prefetched and ts_code in prefetched and "sw_industry" in prefetched[ts_code]:
                sw_industry_name = prefetched[ts_code]["sw_industry"]
                if sw_industry_name:
                    from data.external.tushare_client import TushareClient

                    if TushareClient().is_api_covered_by_tier("index_classify"):
                        lines.append(f"- {I18n.get('ai_label_sw_industry')}: {sw_industry_name}")
                        has_data = True
                        if labels_out is not None:
                            labels_out.append("ai_label_sw_industry")

            # Phase 3G §4.3.4：业绩快报（express）— 早于正式财报 30-60 天，提前反应业绩拐点
            if prefetched and ts_code in prefetched and "express" in prefetched[ts_code]:
                express_df = prefetched[ts_code]["express"]
            else:
                express_df = await cache.get_express(ts_code, as_of_date=as_of_date)

            if express_df is not None and not express_df.empty:
                express_line = _build_stale_section(
                    "express",
                    express_df,
                    self._format_express_section,
                    date_column="ann_date",
                )
                if express_line:
                    lines.append(express_line)
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_express")

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[AIMixin] Failed to build auxiliary data for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            if labels_out is not None:
                labels_out.clear()
            return ("", False)

        if has_data:
            return ("\n".join(lines) + "\n", True)
        return ("", False)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _build_macro_context(self, cache: typing.Any, as_of_date: str | None = None) -> str:
        """
        构建宏观经济环境上下文。

        L3 修复：新增 Shibor 利率注入，对价值投资和固收相关策略有重要参考价值。
        B-P1-1 修复：新增 as_of_date 参数，在历史回放场景下按日期截断宏观数据，防止前视偏差。

        Phase 2A.1 §4.4.5 v1.6.0 P0-1：按子段落分别 stale 标注
        - shibor 段落（对应 ai_label_shibor，points_120）：shibor API 在 points_120
          覆盖内，正常注入（无 stale 标注）
        - m2/cpi/ppi 段落（对应 ai_label_macro_full，points_2000）：cn_m/cn_cpi/cn_ppi
          在 points_2000 覆盖内；points_120 降级时按子段落 stale 标注注入历史数据

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
                # Phase 2D §3.2.6 修复：m2 行与 GDP 行 period 不同（月度 vs 季度末日），
                # 作为独立行存储。DAO 返回最多 2 行，需分别定位月度行和 GDP 行。
                # 用 pd.notna() 判断字段是否可用，避免 NaN 被 `is not None` 误判为有效值。
                m2_row = (
                    macro.dropna(subset=["m2_yoy"]).iloc[0]
                    if "m2_yoy" in macro.columns and not macro.dropna(subset=["m2_yoy"]).empty
                    else None
                )
                gdp_row = (
                    macro.dropna(subset=["gdp_yoy"]).iloc[0]
                    if "gdp_yoy" in macro.columns and not macro.dropna(subset=["gdp_yoy"]).empty
                    else None
                )

                # Phase 2A.1 §4.4.5：m2/cpi/ppi 段落对应 ai_label_macro_full（points_2000），
                # cn_m/cn_cpi/cn_ppi 在 points_2000 覆盖内；points_120 降级时按子段落 stale 标注。
                # cn_m 作为整个 macro 段落的代理（三者档位一致）
                macro_lines: list[str] = []
                if m2_row is not None:
                    m2_yoy = m2_row.get("m2_yoy")
                    if pd.notna(m2_yoy):
                        macro_lines.append(f"- {I18n.get('macro_m2_yoy')}: {m2_yoy:.2f}%")

                    cpi = m2_row.get("cpi")
                    if pd.notna(cpi):
                        macro_lines.append(f"- {I18n.get('macro_cpi')}: {cpi:.2f}")

                    ppi = m2_row.get("ppi")
                    if pd.notna(ppi):
                        macro_lines.append(f"- {I18n.get('macro_ppi')}: {ppi:.2f}")

                if macro_lines:
                    macro_text = "\n".join(macro_lines)
                    # 用 _build_stale_section 统一标注（cn_m 作为代理 API，date_column="period"）
                    macro_section = _build_stale_section(
                        "cn_m",
                        macro,
                        lambda _df: macro_text,
                        date_column="period",
                    )
                    if macro_section:
                        lines.append(macro_section)
                        has_data = True

                # Phase 2D §3.2.6：cn_gdp 段落（季度数据，period 为季度末日）
                # GDP 行与 m2 行 period 不同，分别 stale 标注
                gdp_lines: list[str] = []
                if gdp_row is not None:
                    gdp_yoy = gdp_row.get("gdp_yoy")
                    if pd.notna(gdp_yoy):
                        # 从 period（季度末日）推断 quarter 字符串，如 2024-12-31 → "2024Q4"
                        period = gdp_row.get("period")
                        quarter_str = ""
                        if hasattr(period, "year") and hasattr(period, "month"):
                            q = (period.month - 1) // 3 + 1
                            quarter_str = f"（{period.year}Q{q}）"
                        gdp_lines.append(f"- {I18n.get('macro_gdp_yoy')}{quarter_str}: {gdp_yoy:.2f}%")

                        pi_yoy = gdp_row.get("pi_yoy")
                        if pd.notna(pi_yoy):
                            gdp_lines.append(f"- {I18n.get('macro_pi_yoy')}: {pi_yoy:.2f}%")

                        si_yoy = gdp_row.get("si_yoy")
                        if pd.notna(si_yoy):
                            gdp_lines.append(f"- {I18n.get('macro_si_yoy')}: {si_yoy:.2f}%")

                        ti_yoy = gdp_row.get("ti_yoy")
                        if pd.notna(ti_yoy):
                            gdp_lines.append(f"- {I18n.get('macro_ti_yoy')}: {ti_yoy:.2f}%")

                if gdp_lines:
                    gdp_text = "\n".join(gdp_lines)
                    # cn_gdp 作为 GDP 段落代理 API，date_column="period"
                    gdp_section = _build_stale_section(
                        "cn_gdp",
                        macro,
                        lambda _df: gdp_text,
                        date_column="period",
                    )
                    if gdp_section:
                        lines.append(gdp_section)
                        has_data = True

            shibor = await cache.get_shibor_latest(as_of_date=as_of_date)
            if shibor is not None and not shibor.empty:
                shibor_latest = shibor.iloc[0]

                shibor_lines: list[str] = []
                on_rate = shibor_latest.get("on_rate")
                if on_rate is not None:
                    shibor_lines.append(f"- {I18n.get('macro_shibor_overnight')}: {on_rate:.2f}%")

                w1_rate = shibor_latest.get("week_1")
                if w1_rate is not None:
                    shibor_lines.append(f"- {I18n.get('macro_shibor_1w')}: {w1_rate:.2f}%")

                m3_rate = shibor_latest.get("month_3")
                if m3_rate is not None:
                    shibor_lines.append(f"- {I18n.get('macro_shibor_3m')}: {m3_rate:.2f}%")

                if shibor_lines:
                    shibor_text = "\n".join(shibor_lines)
                    # shibor 段落对应 ai_label_shibor（points_120），shibor API 在 points_120 覆盖内
                    # points_120 降级时 shibor 仍可正常注入（无 stale 标注）
                    shibor_section = _build_stale_section(
                        "shibor",
                        shibor,
                        lambda _df: shibor_text,
                        date_column="record_date",
                    )
                    if shibor_section:
                        lines.append(shibor_section)
                        has_data = True

                # Phase 3G §4.3.4：LPR 段落（与 shibor 同表 shibor_daily，独立 stale 标注）
                # shibor_lpr 在 points_120 覆盖内，正常注入（无 stale 标注）
                lpr_lines: list[str] = []
                lpr_1y = shibor_latest.get("lpr_1y")
                if lpr_1y is not None:
                    lpr_lines.append(f"- {I18n.get('macro_lpr_1y')}: {lpr_1y:.2f}%")

                lpr_5y = shibor_latest.get("lpr_5y")
                if lpr_5y is not None:
                    lpr_lines.append(f"- {I18n.get('macro_lpr_5y')}: {lpr_5y:.2f}%")

                if lpr_lines:
                    lpr_text = "\n".join(lpr_lines)
                    lpr_section = _build_stale_section(
                        "shibor_lpr",
                        shibor,
                        lambda _df: lpr_text,
                        date_column="record_date",
                    )
                    if lpr_section:
                        lines.append(lpr_section)
                        has_data = True

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIMixin] Failed to build macro context: %s", DataSanitizer.sanitize_error(e))

        if has_data:
            return "\n".join(lines) + "\n"
        return ""

    @staticmethod
    def _build_financials_text(row: dict, labels_out: list[str] | None = None) -> str:
        """
        Build a human-readable financials summary from the stock_info data.
        The screening data already contains key financial metrics from the join.

        Args:
            row: 筛选数据行（含 PE/PB/ROE 等估值指标）
            labels_out: 输出参数，收集成功注入的标签 key；异常时自动清空
        """
        try:
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

            if parts and labels_out is not None:
                labels_out.append("ai_label_valuation")

            return "\n".join(parts)

        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 38处策略层异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning("[AIMixin] Failed to build financials text: %s", DataSanitizer.sanitize_error(e))
            if labels_out is not None:
                labels_out.clear()
            return I18n.get("ai_financial_insufficient")
