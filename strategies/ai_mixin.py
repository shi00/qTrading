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
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

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
from utils.egress_ack import collect_cloud_ack_providers
from utils.error_classifier import classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.technical_analysis import TechnicalAnalysis
from utils.time_utils import get_now, to_yyyymmdd_str

logger = logging.getLogger(__name__)


def _dataframe_sizeof(value: Any) -> int:
    """
    计算放入 TTLCache 的对象的内存字节数。
    对 DataFrame 使用 memory_usage(deep=True).sum()，非 DataFrame 使用 sys.getsizeof。
    最小返回 1，防止 0 大小导致 cachetools 内部除零或状态异常。
    """
    if isinstance(value, pd.DataFrame):
        try:
            return max(1, int(value.memory_usage(deep=True).sum()))
        except Exception:
            return max(1, sys.getsizeof(value, 1024))
    return max(1, sys.getsizeof(value, 1024))


def _build_egress_prompt_preview(
    candidates_df: pd.DataFrame | None,
    context: dict,
    *,
    providers: list[str] | None = None,
) -> str:
    """SEC-01 gap3: 构造「真实 prompt 预览」用于外发确认对话框。

    gating 点早于逐股完整预取，故此处用候选集第一股的现有字段（已在筛选结果中）
    组装一份结构化的预览文本：展示将发送给云端 LLM 的数据类别与首股样例，
    而非逐股完整 prompt。经 DataSanitizer 脱敏后返回。

    对话框侧会额外标注「示例预览，实际发送内容可能略有差异」，向用户诚实披露预览
    与最终一致性的边界（SEC-01 gap3 方案结论）。

    ``providers`` 非空时，在预览头部追加确认对象（主 + failover 云端 provider 集合），
    让用户确认时知道数据实际可能发往的每个云端供应商（SEC-01 复核修复：failover
    授权对象漂移的可视化披露）。
    """
    if candidates_df is None or candidates_df.empty:
        return ""

    first = candidates_df.iloc[0]
    sample = _safe_preview_field(first)

    # 数据类别摘要（V1 范围：股票基本信息、行情、财务指标、公开新闻摘要）
    # 文案经 I18n.get 取 key，locale 已收录中/英；不经 default 提供 CJK 回退（i18n 门禁）。
    sections = []
    if providers:
        sections.append(I18n.get("ai_egress_preview_providers", providers=", ".join(providers)))
    sections += [
        I18n.get("ai_egress_preview_header"),
        "- " + I18n.get("ai_egress_preview_stock"),
        "- " + I18n.get("ai_egress_preview_news"),
        "",
        sample,
    ]
    return "\n".join(sections)


def _safe_preview_field(row: pd.Series) -> str:
    """提取首股样例为可读文本（脱敏，字段缺失容错）。"""
    try:
        name = row.get("name", row.get("ts_code", ""))
        ts = row.get("ts_code", "")
        close = row.get("close")
        pct = row.get("pct_chg")
        parts = [str(name), str(ts)]
        if close is not None:
            parts.append(f"close={DataSanitizer.sanitize_error(str(close))}")
        if pct is not None:
            parts.append(f"pct_chg={DataSanitizer.sanitize_error(str(pct))}")
        return " | ".join(DataSanitizer.sanitize_error(p) for p in parts)
    except Exception:
        return ""


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

    # SC-02: get_ai_context 承载了定性风险检查职责（如假高息/护城河/资金链判定）
    # 的策略应置 True。AI 实际不运行时（enable_ai_analysis=False / AI 未配置 /
    # 回测 disable_ai），经 _note_ai_risk_check_skipped 向 context["warnings"]
    # 写入降级声明，避免"风险检查缺失"被静默吞掉。
    ai_risk_check_in_prompt: bool = False

    _HISTORY_CACHE_MAX = 4
    _HISTORY_CACHE_MAX_BYTES = 128 * 1024 * 1024  # 128MB
    _HISTORY_CACHE_TTL = 120

    # AI-01: 预算护栏「不可计价调用」保守提示的进程级一次确认标记。
    # 用户确认后置 True，本进程内后续批次/重试不再重复弹提示；重启应用后重置。
    _ai_unpriced_acknowledged = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_builders: dict[str, ContextBuilder] = {}
        self._history_cache: TTLCache = TTLCache(
            maxsize=self._HISTORY_CACHE_MAX_BYTES,
            ttl=self._HISTORY_CACHE_TTL,
            getsizeof=_dataframe_sizeof,
        )
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

    def _note_ai_risk_check_skipped(self, context: dict) -> None:
        """SC-02: AI 实际不运行时声明能力边界（复用 D3-4 warnings 通道）。

        仅对 ``ai_risk_check_in_prompt=True``（get_ai_context 承载风险检查职责）的
        策略生效；其余策略为 no-op。warnings 缺失时静默跳过（调用方未初始化通道）。
        """
        if not self.ai_risk_check_in_prompt:
            return
        warnings = context.get("warnings")
        if warnings is not None:
            warnings.append(Message("strategy_ai_risk_check_skipped"))

    def _sort_for_ai(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preserve the strategy's business sort order for AI analysis truncation.
        D2-M2: previously the default sorted by market cap / volume (descending),
        injecting a large-cap preference into every AI strategy regardless of the
        strategy's own ranking. Now the default keeps the input order so truncation
        honors the strategic ranking produced by ``_filter_logic``.

        Subclasses should override if a custom order better matches the strategy
        intent (e.g. OversoldStrategy sorts by RSI ascending).
        """
        if df.empty:
            return df
        logger.debug(
            "[%s] Using default (business) order for AI analysis (%d candidates)",
            self.__class__.__name__,
            len(df),
        )
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
                end_date = _dt.datetime.strptime(ctx_td, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
            except (ValueError, TypeError):
                if is_backtest:
                    raise ValueError(
                        f"Cannot parse trade_date for backtest: {ctx_td!r}. "
                        f"Refusing to fall back to current date to prevent lookahead bias."
                    ) from None
        return end_date

    @staticmethod
    def compute_learning_as_of(trade_date_raw, is_backtest: bool) -> date:
        """Compute the exclusive learning-context cutoff (``trade_date < as_of``) date.

        D4-M5 语义契约：本方法按**自然日**回退（``timedelta(days=N)``），而「T+5 收益已
        回填」的实际前提是按**交易日**计数——长假期间 5 个交易日可跨 ~2 倍自然日，故自然日
        偏移无法单独保证 T+5 复盘窗口已成熟，它只是一个**额外的冗余安全边界**，并非前视
        防护的正主。真正的前视防护由 DAO 查询的硬性过滤保证（``screener_dao.
        get_learning_context`` 的 ``review_status == REVIEW_STATUS_COMPLETED`` + ``t5_pct
        IS NOT NULL``）：未完成 T+5 回填 / 未定稿标签的记录在取数时即被排除，天然不会泄漏
        未来。因此：**放宽该 DAO 过滤条件（如为增加样本量而接受 T1_DONE）前，必须先把这个
        自然日偏移改为交易日口径（经 TradeCalendarService 回退 N 个交易日）**，否则偏移
        不足会立刻退化为真实的前视泄漏。
        """
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
            # SC-02: 未配置 LLM 属"AI 有效不运行"，向 warnings 声明风险检查缺失。
            self._note_ai_risk_check_skipped(context)
            if on_progress:
                on_progress(
                    0,
                    0,
                    Message("ai_not_configured"),
                )
            return candidates_df

        # --- Guard: AI External Data Acknowledged? (D5-1 / AI-04 / SEC-01) ---
        # run_ai_analysis 是云端专用路径：上方 is_cloud_available() 已保证走到此处必已
        # 配置云端 LLM 的 api_key。本地模型（数据不出本机）无需外发确认——若用户未配云端
        # 或仅配本地，已在 is_cloud_available 提前返回，因此本 guard 仅需按 provider 校验：
        # 更换 provider 后新 provider 无确认记录 → 未确认；外发范围版本升级后旧确认自动失效
        # （SEC-01，scope_version 不满足）→ 同样需重新确认，满足 UN-07。
        current_provider = ConfigHandler.get_llm_provider()
        ack_request = context.get("on_ai_egress_ack_request")
        # SEC-01 复核修复: 确认对象 = 主 provider + failover 不同 provider（主失败时
        # 自动切换到 fallback，若 fallback 未确认则数据外发给未授权对象——授权语义
        # 漂移）。任一未确认即触发运行时确认或跳过，与单 provider 语义保持一致。
        ack_providers = collect_cloud_ack_providers(current_provider)
        if not all(ConfigHandler.is_ai_external_acknowledged(provider=p) for p in ack_providers):
            # SEC-01 gap3: 若调用方注入了运行时确认回调（UI），则构造真实 prompt 预览并
            # 请求用户确认；确认通过后持久化全部确认对象 provider+scope 版本并继续执行。
            # 否则（无确认能力，如夜间/无 UI 调度）回落为直接跳过，确保未经确认绝不发起
            # 云端请求。
            if ack_request is not None:
                try:
                    preview = _build_egress_prompt_preview(candidates_df, context, providers=ack_providers)
                    if await ack_request(preview, current_provider):
                        for p in ack_providers:
                            ConfigHandler.set_ai_external_acknowledged(provider=p, acknowledged=True)
                        logger.info(
                            "[AIStrategyMixin] AI external data policy acknowledged at runtime for providers=%s",
                            ack_providers,
                        )
                    else:
                        logger.info(
                            "[AIStrategyMixin] User declined AI external data policy for provider=%s — skipping AI analysis",
                            current_provider,
                        )
                        if on_progress:
                            on_progress(0, 0, Message("ai_external_acknowledgment_declined"))
                        return candidates_df.assign(ai_score=None, ai_status="policy_not_acknowledged")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "llm",
                        "[AIStrategyMixin] Runtime egress ack request failed (%s: %s) — falling back to skip",
                    )
                    return candidates_df.assign(ai_score=None, ai_status="policy_not_acknowledged")
            else:
                logger.info(
                    "[AIStrategyMixin] AI external data policy not acknowledged for provider=%s — skipping AI analysis (no external requests initiated)",
                    current_provider,
                )
                if on_progress:
                    on_progress(0, 0, Message("ai_external_acknowledgment_prompt"))
                # 政策未确认时返回带状态标记的结果行（与其他 AI 路径同构）：
                # ai_status="policy_not_acknowledged" 让下游能区分"政策未确认"与
                # "AI 分析失败"；review_manager 对非 "analyzed" 状态不写库，
                # 避免未经确认的候选结果污染 AI 学习闭环数据。
                return candidates_df.assign(
                    ai_score=None,
                    ai_status="policy_not_acknowledged",
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
            # SC-02: 回测关闭 AI 属"AI 有效不运行"，向 warnings 声明风险检查缺失。
            self._note_ai_risk_check_skipped(context)
            return candidates_df

        # --- Guard: 月度成本预算未超限 (AI-03 完整版) ---
        # 发起任何 LLM 调用前先读本月累计成本；若已达月度预算上限则软停，
        # 不再发起新的云端调用（软停标记供 UI 提示用户调整预算）。
        await self._ensure_cost_tracker_engine()
        if await self._ai_budget_exhausted():
            logger.warning(
                "[AIStrategyMixin] Monthly AI cost budget exhausted — skipping AI analysis",
            )
            if on_progress:
                on_progress(0, 0, Message("ai_budget_exceeded"))
            context["_ai_budget_exceeded"] = True
            return candidates_df.assign(
                ai_score=None,
                ai_status="budget_exceeded",
            )

        # --- Guard: 本次不可计价调用保守提示 (AI-01 / R21) ---
        # 预算已设但本月存在不可计价调用（cost 为 None）时，即便未超限也应先向用户确认，
        # 否则不可计量调用可绕过预算护栏。与 retry_single 共用 _confirm_unpriced，语义一致。
        if await self._should_prompt_unpriced():
            if not await self._confirm_unpriced(context):
                # B1（review-pr1073）：拒绝原因写 context 标志，夜间 _prediction_logic 据此
                # 返回专门消息（"因 unpriced 保守拒绝"），与"无候选"可区分，避免静默失败+每日重试刷日志。
                context["_ai_unpriced_prompt"] = True
                if on_progress:
                    on_progress(0, 0, Message("ai_budget_unpriced_prompt"))
                return candidates_df.assign(
                    ai_score=None,
                    ai_status="budget_unpriced_prompt",
                )

        # --- Guard: Empty Input ---
        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        # --- Cost Control: Cap candidates ---
        cap = max_stocks or ConfigHandler.get_ai_max_candidates()
        if len(candidates_df) > cap:
            total_before = len(candidates_df)
            logger.info(
                "[AIStrategyMixin] Capping candidates from %d to %d",
                total_before,
                cap,
            )
            candidates_df = candidates_df.head(cap)
            # D2-M2: 截断不再静默 — 复用 warnings 通道，VM/View 经既有 state.warnings
            # 横幅渲染，用户在结果页可见「候选被截断」提示（D3-4 / screener_param_impact 惯例）。
            context.setdefault("warnings", []).append(
                Message("strategy_ai_candidate_truncated", {"total": total_before, "analyzed": cap})
            )

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
                # D4-M3: 传入 name_key 只取同策略 few-shot 样本，避免跨策略污染。
                history_context = await rm.get_learning_context(
                    as_of=as_of,
                    strategy_name=getattr(self, "name_key", None),
                )
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to pre-fetch learning context: %s: %s",
                )

        global_context = ""
        if self.should_include_global_context():
            try:
                global_context = await NewsFetcher.get_us_major_moves(as_of=news_as_of)
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to fetch global context: %s: %s",
                )

        # --- Pre-fetch Concepts for all candidates (N+1 optimization) ---
        concepts_map = {}
        all_ts_codes = candidates_df["ts_code"].tolist()
        try:
            concepts_map = await dp.cache.get_concepts(all_ts_codes)  # type: ignore[union-attr]
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Failed to pre-fetch concepts: %s: %s",
            )

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
                if bulk_history_df is not None:
                    try:
                        self._history_cache[cache_key] = bulk_history_df
                    except ValueError:
                        logger.warning(
                            "[%s] Bulk history dataframe exceeds cache max size (%d bytes), skipping cache",
                            self.__class__.__name__,
                            self._HISTORY_CACHE_MAX_BYTES,
                        )
            if bulk_history_df is not None and not bulk_history_df.empty:
                for code, group in bulk_history_df.groupby("ts_code"):
                    prefetched_history[code] = group

            # 2. Background Pipelining for News (concurrency follows analysis concurrency)
            _news_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
            news_sem = asyncio.Semaphore(_news_concurrency)

            async def bg_fetch_news(code) -> tuple[list, bool]:
                """抓取新闻上下文。

                D5-7: 返回 (news_list, ok)。新闻是可选增强上下文 — 任何抓取失败都
                降级为空列表并返回 ok=False，绝不向上传播导致整只股票分析失败。
                ok 标记透传到结果行，使 UI 能提示"本次分析未包含新闻"，AI 结论的
                信息完备度对用户透明。
                """
                async with news_sem:
                    try:
                        news = await NewsFetcher.get_stock_news(code, limit=5, as_of=news_as_of)
                        return news, True
                    except asyncio.CancelledError:
                        # R2: 传播取消信号，配合优雅停机
                        raise
                    except Exception as e:
                        log_classified(
                            logger,
                            e,
                            "network",
                            "[AIStrategyMixin] Failed to fetch news (%s: %s) for %s, degrading to empty news context",
                            code,
                        )
                        return [], False

            news_tasks = {code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes}
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Ultimate Pipeline init failed: %s: %s",
            )

        # --- Batch Pre-Fetch: Capital Flow Data (Moneyflow, TopList, Northbound) ---
        # Fetch once for the trade date, filter per-stock in the loop (0ms per stock)
        trade_date = self._normalize_trade_date_for_cache(context.get("trade_date"))
        try:
            if trade_date is None:
                trade_date = self._normalize_trade_date_for_cache(await dp.get_latest_trade_date())  # type: ignore[union-attr]
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Failed to get latest trade date: %s: %s",
            )

        moneyflow_df = pd.DataFrame()
        top_list_df = pd.DataFrame()
        northbound_df = pd.DataFrame()
        top_inst_df = pd.DataFrame()

        if trade_date:
            try:
                moneyflow_df = await dp.cache.quote_dao.get_moneyflow(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to pre-fetch moneyflow: %s: %s",
                )

            try:
                top_list_df = await dp.cache.quote_dao.get_top_list(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to pre-fetch top_list: %s: %s",
                )

            try:
                northbound_df = await dp.cache.quote_dao.get_northbound(trade_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to pre-fetch northbound: %s: %s",
                )

            # Phase 3C：top_inst 龙虎榜机构席位预取（auxiliary 数据，权限不足时由 _build_stale_section 标注）
            try:
                top_inst_df = await dp.cache.get_top_inst_batch(all_ts_codes, as_of_date=trade_date)  # type: ignore[union-attr]
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIStrategyMixin] Failed to pre-fetch top_inst: %s: %s",
                )

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
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Failed to pre-fetch auxiliary data: %s: %s",
            )

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
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Failed to prefetch macro context: %s: %s",
            )

        # --- Concurrent Analysis ---
        concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
        screening_sem = asyncio.Semaphore(concurrency)
        stream_enabled = concurrency == 1

        total_tasks = len(candidates_df)
        completed = 0
        final_rows: list[dict] = []
        # AI-03(完整版): 累计本次选股实际消耗的 LLM 调用次数、token 总量与货币成本。
        # 仅统计成功分析(非失败)的调用, token 取自 LLM 返回的 usage.total_tokens,
        # 成本取自 res["cost"]（litellm_client 基于 estimate_cost 计算, 未知模型为 None）。
        # 循环结束后若确有消耗, 回写 context["_ai_usage_summary"] 供 UI 展示,
        # 并累计到 AIUsageTracker 按月持久化。
        ai_usage_cluster = {
            "calls": 0,
            "tokens": 0,
            "cost_cny": 0.0,
            "unpriced_calls": 0,
            "unpriced_tokens": 0,
            # Mi2（review-pr1073）：不可计价明细 model→calls map，供诊断"哪些模型无法计价"。
            "unpriced_by_model": {},
        }
        on_stream_start = context.get("on_stream_start") if stream_enabled else None
        on_card_start = context.get("on_card_start") if not stream_enabled else None

        if on_progress:
            if stream_enabled:
                on_progress(0, total_tasks, Message("ai_progress_init"))
            else:
                on_progress(0, total_tasks, Message("ai_progress_concurrent_info", {"concurrency": concurrency}))

        if not stream_enabled:
            logger.info(
                "[AIStrategyMixin] Concurrent analysis enabled (concurrency=%d). "
                "Streaming chunk output is disabled in multi-concurrency mode; reporting real-time card and progress updates.",
                concurrency,
            )

        on_card_error = context.get("on_card_error")  # UX-2.3: 单股失败回调

        async def analyze_one(row_data: dict) -> dict | None:
            nonlocal completed
            async with screening_sem:
                if dp and dp.is_cancelled():
                    return None  # 已取消，不触发 on_card_error
                stock_name = row_data.get("name", row_data.get("ts_code", "?"))
                on_chunk = on_stream_start(stock_name) if on_stream_start else None
                if on_card_start:
                    on_card_start(stock_name)
                try:
                    hist_df = prefetched.history.get(row_data.get("ts_code"), pd.DataFrame())
                    news_list = []
                    news_ok = True
                    if row_data.get("ts_code") in prefetched.news_tasks:
                        try:
                            news_list, news_ok = await prefetched.news_tasks[row_data.get("ts_code")]
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            log_classified(
                                logger,
                                e,
                                "network",
                                "[AIStrategyMixin] Failed to await news task (%s: %s) for %s, degrading to empty news context",
                                row_data.get("ts_code", "?"),
                            )
                            news_list = []
                            news_ok = False
                    row_data["_ai_news_ok"] = news_ok
                    res = await self._mixin_analyze_single(
                        row_data,
                        dp,
                        ai_client,
                        prefetched,
                        on_chunk=on_chunk,
                        history_df=hist_df,
                        news=news_list,
                        ui_prompt_override=ui_prompt_override,
                        vol_ratio_threshold=context.get("params", {}).get("vol_ratio_threshold", 1.7),
                    )
                    if res is None:
                        # UX-2.3: 软失败（_mixin_analyze_single 内部已日志）
                        if on_card_error:
                            on_card_error(stock_name, I18n.get("ai_card_analysis_failed"))
                        # D3-6/D3-7: 失败也保留候选行（ai_status="failed"），
                        # 使下游能区分"AI 跑过但失败" 与 "AI 未跑"。UI 错误呈现仍由
                        # on_card_error 承载（失败行 UI 化属 D7-3 范围，本层不重复 on_result）。
                        return self._build_result_row(
                            row_data,
                            None,
                            error_reason=I18n.get("ai_card_analysis_failed"),
                        )
                    row = self._build_result_row(row_data, res)
                    # AI-03(完整版): 累计本次消耗。仅统计成功分析（res 为 dict、
                    # 非失败且携带 usage），避免把失败调用计入用户可见的消耗量。
                    if isinstance(res, dict) and res.get("ai_status") != "failed":
                        self._accumulate_usage(ai_usage_cluster, res)
                    if on_result:
                        on_result(row)
                    return row
                except asyncio.CancelledError:
                    raise  # R2 合规
                except Exception as e:
                    # UX-2.3: 网络错误等（_mixin_analyze_single raise 的异常）
                    if on_card_error:
                        on_card_error(stock_name, DataSanitizer.sanitize_error(e))
                    # D3-6/D3-7: 异常不吞没（R2），但构造 failed 行保留候选，
                    # 避免下游将"分析失败"静默当作"被否决/未入选"。异常信息经脱敏。
                    return self._build_result_row(
                        row_data,
                        None,
                        error_reason=DataSanitizer.sanitize_error(e),
                    )
                finally:
                    if on_chunk and hasattr(on_chunk, "final_flush"):
                        on_chunk.final_flush()
                    if not (dp and dp.is_cancelled()):
                        completed += 1
                        if on_progress:
                            on_progress(
                                completed,
                                total_tasks,
                                Message("ai_progress_done", {"done": completed, "total": total_tasks}),
                            )

        # Batch task creation to avoid unbounded coroutine explosion
        _BATCH_SIZE = 20
        # AI-05: 批内周期性预算复查 + 增量落账节奏（与批次粒度一致，20 股一次）。
        # 预算复查必须在每批累计落账之后进行——落账使累计成本单调更新，复查读到最新值。
        # 增量落账将「崩溃丢账」损失从整批收敛到至多一批（20 股）成本。
        all_records = candidates_df.to_dict("records")
        results: list = []

        # F4-ST-001: try/finally 确保任何路径（含 CancelledError）下 news_tasks 被清理，
        # 避免后台 HTTP 任务句柄/连接泄漏。gather_return_exceptions_propagating_cancel
        # 在 CancelledError 时直接 raise，若无 try/finally，下方 for 循环与
        # _cancel_orphan_news_tasks 调用将不会执行（成为死代码）。
        try:
            budget_hit_early = False
            for batch_count, batch_start in enumerate(range(0, len(all_records), _BATCH_SIZE), start=1):
                if dp and dp.is_cancelled():
                    break
                # AI-05: 批开始前快照 usage 四量，批结束 diff 得本批增量（落账差量）。
                usage_snapshot = (
                    ai_usage_cluster["calls"],
                    ai_usage_cluster["tokens"],
                    ai_usage_cluster["cost_cny"],
                    ai_usage_cluster["unpriced_calls"],
                    ai_usage_cluster["unpriced_tokens"],
                )
                batch = all_records[batch_start : batch_start + _BATCH_SIZE]
                batch_tasks = [asyncio.create_task(analyze_one(row_data)) for row_data in batch]
                batch_results = await gather_return_exceptions_propagating_cancel(*batch_tasks)
                results.extend(batch_results)

                # AI-05: 批量落账差量（本批新耗用）。批内所有成本为正单调累加（R22），
                # 负差仅可能因模型切换/计价口径变化，记录 debug 供诊断。
                diff_cost = ai_usage_cluster["cost_cny"] - usage_snapshot[2]
                if diff_cost < 0:
                    logger.debug(
                        "[AIStrategyMixin] cost diff negative (model changed): %s",
                        diff_cost,
                    )
                if ai_usage_cluster["calls"] > usage_snapshot[0]:
                    try:
                        await self._track_cost(
                            diff_cost,
                            unpriced_calls=ai_usage_cluster["unpriced_calls"] - usage_snapshot[3],
                            unpriced_tokens=ai_usage_cluster["unpriced_tokens"] - usage_snapshot[4],
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # 增量落账失败不阻断批次（与批次末落账同语义），下批 diff 仍会重试累加
                        log_classified(
                            logger,
                            e,
                            "general",
                            "[AIStrategyMixin] Failed to persist batch AI cost delta: %s",
                        )

                # AI-05: 批内周期性预算复查——落账后累计单调更新，超限则软停剩余批次。
                if await self._ai_budget_exhausted():
                    logger.warning(
                        "[AIStrategyMixin] Monthly AI cost budget exhausted mid-batch (after %d batches) — stopping remaining candidates",
                        batch_count,
                    )
                    budget_hit_early = True
                    # 与入口 guard 语义一致：软停标记供 UI/夜间任务区分"预算超限"与"无候选"。
                    context["_ai_budget_exceeded"] = True
                    break

            if budget_hit_early:
                # 与入口 guard 语义一致：剩余候选标记 budget_exceeded，保留行供 UI 呈现。
                processed = batch_start + (len(batch) if "batch" in locals() else 0)
                remaining = all_records[processed:]
                if remaining:
                    logger.info(
                        "[AIStrategyMixin] Marking %d remaining candidates budget_exceeded",
                        len(remaining),
                    )
                    for row_data in remaining:
                        row = self._build_result_row(
                            row_data,
                            None,
                            error_reason=I18n.get("ai_budget_exceeded"),
                        )
                        row["ai_status"] = "budget_exceeded"
                        row["ai_score"] = None
                        results.append(row)

            for res in results:
                # F4-ST-001: 防御性 — CancelledError 继承 BaseException 而非 Exception,
                # 若未来 gather 实现变化导致 CancelledError 漏入 results, 此处显式 raise (R2)
                if isinstance(res, asyncio.CancelledError):
                    raise res
                if isinstance(res, Exception):
                    # UX-2.3: on_card_error 已在 analyze_one 内调用, 此处仅日志
                    log_classified(
                        logger,
                        res,
                        "llm",
                        "[AIStrategyMixin] Task error (%s: %s)",
                    )
                elif isinstance(res, dict):
                    # D3-6/D3-7: analyze_one 已对 analyzed/rejected/failed 均返回 dict 行,
                    # 因此此处收集的即完整候选集（含否决与失败），不再仅筛出成功结果。
                    final_rows.append(res)

            logger.info(
                "[AIStrategyMixin] Complete. %d/%d processed, %d valid results",
                completed,
                total_tasks,
                len(final_rows),
            )
        finally:
            # F4-ST-001: 无论正常完成、CancelledError 或异常，都清理 news_tasks
            # _cancel_orphan_news_tasks 内部用 gather_for_shutdown_cleanup（清理语义，
            # 吞内部 CancelledError、不抛普通异常），finally 中安全
            await self._cancel_orphan_news_tasks(prefetched)

        # D3-7: 仅当所有任务被用户取消时（final_rows 为空）才退回原始候选集,
        # 以保留取消/退出的既有语义。AI 全部失败已由 analyze_one 构造 failed 行,
        # 不再出现"AI 跑了但静默退回未打标候选"的情况。
        if not final_rows:
            return candidates_df  # 用户取消全部任务时退回数学筛选结果

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

        # 排序规则：analyzed → rejected → failed → budget_exceeded，
        # 同 ai_status 内按 ai_score 降序。
        # ai_status 为字符串，"analyzed/failed/rejected" 的字典序与目标顺序不一致（f<r），
        # 故用显式次序映射；ai_score=None (failed) 在 pandas 中始终排最后。
        _AI_STATUS_ORDER = {"analyzed": 0, "rejected": 1, "failed": 2, "budget_exceeded": 3}
        order_series = result_df["ai_status"].map(_AI_STATUS_ORDER)

        # AI-03(完整版): 本次选股实际消耗的 LLM 调用次数、token 总量与货币成本回写 context,
        # 供 UI ViewModel 在策略完成后读取并展示。仅当确有成功调用才回写,
        # 避免「一次未发起调用」被 UI 误读为「消耗 0 次」。
        # cost 为浮点元；持久化落账已在每批完成后以增量方式执行（AI-05 增量落账），
        # 此处仅展示回写、不再重复调 _track_cost（避免重复记账）。
        if ai_usage_cluster["calls"] > 0:
            context["_ai_usage_summary"] = {
                "calls": ai_usage_cluster["calls"],
                "tokens": ai_usage_cluster["tokens"],
                "cost_cny": round(ai_usage_cluster["cost_cny"], 4),
                "unpriced_calls": ai_usage_cluster["unpriced_calls"],
                "unpriced_tokens": ai_usage_cluster["unpriced_tokens"],
            }

        return (
            result_df.assign(_ai_order=order_series)
            .sort_values(["_ai_order", "ai_score"], ascending=[True, False])
            .drop(columns=["_ai_order"])
        )

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
        # D4-M2: 重试同样必须通过云端/政策/预算 guard——若跳过预算检查，预算上限可被
        # 点击次数无限突破；跳过 provider 确认则数据可能外发至未授权对象。
        block_reason = await self._preflight_cloud_call(context)
        if block_reason is not None:
            logger.warning("[AIStrategyMixin] retry_single blocked by guard: %s", block_reason)
            if on_card_error:
                on_card_error(stock_name, I18n.get(block_reason))
            return
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
            news_ok = True
            if ts_code in prefetched.news_tasks:
                news_task = prefetched.news_tasks[ts_code]
                if news_task.done() and news_task.cancelled():
                    # UX-2.3 v4 P1-1: 原 news task 已被 cancel（批次取消场景），降级重新拉取
                    if prefetched.news_as_of:
                        try:
                            news_list = await NewsFetcher.get_stock_news(ts_code, limit=5, as_of=prefetched.news_as_of)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            log_classified(
                                logger,
                                e,
                                "network",
                                "[AIStrategyMixin] retry_single re-fetch news failed (%s: %s) for %s, degrading to empty news context",
                                ts_code,
                            )
                            news_list = []
                            news_ok = False
                    else:
                        news_list = []
                        news_ok = False
                else:
                    # task 未完成或正常完成，await 复用（CancelledError 由外层传播）
                    try:
                        news_list, news_ok = await news_task
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log_classified(
                            logger,
                            e,
                            "network",
                            "[AIStrategyMixin] retry_single await news task failed (%s: %s) for %s, degrading to empty news context",
                            ts_code,
                        )
                        news_list = []
                        news_ok = False
            elif prefetched.news_as_of:
                try:
                    news_list = await NewsFetcher.get_stock_news(ts_code, limit=5, as_of=prefetched.news_as_of)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "network",
                        "[AIStrategyMixin] retry_single re-fetch news failed (%s: %s) for %s, degrading to empty news context",
                        ts_code,
                    )
                    news_list = []
                    news_ok = False
            row_data["_ai_news_ok"] = news_ok
            res = await self._mixin_analyze_single(
                row_data,
                dp,
                ai_client,
                prefetched,
                history_df=hist_df,
                news=news_list,
                vol_ratio_threshold=context.get("params", {}).get("vol_ratio_threshold", 1.7),
            )
            if res is None:
                if on_card_error:
                    on_card_error(name_str, I18n.get("ai_card_analysis_failed"))
                return
            result_row = self._build_result_row(row_data, res)
            # D4-M2: 重试成功同样计入月度成本与不可计价量
            # （与 run_ai_analysis 收尾 _track_cost 同口径）。failed 路径不产生有效调用，不计费。
            if isinstance(res, dict) and res.get("ai_status") != "failed":
                cluster = {
                    "calls": 0,
                    "tokens": 0,
                    "cost_cny": 0.0,
                    "unpriced_calls": 0,
                    "unpriced_tokens": 0,
                    # Mi2（review-pr1073）：重试路径同样维护不可计价明细 map。
                    "unpriced_by_model": {},
                }
                self._accumulate_usage(cluster, res)
                if cluster["calls"] > 0 or cluster["unpriced_calls"] > 0:
                    try:
                        await self._track_cost(
                            cluster["cost_cny"],
                            unpriced_calls=cluster["unpriced_calls"],
                            unpriced_tokens=cluster["unpriced_tokens"],
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # 成本持久化失败不阻断结果交付（仅记录），与批量路径一致
                        log_classified(
                            logger,
                            e,
                            "general",
                            "[AIStrategyMixin] retry_single failed to persist cost to tracker: %s",
                        )
            if on_result:
                # D3-6: _build_result_row 不再返回 None（score==0 亦视为 rejected 行）。
                # 调用方 retry_single_stock 已把失败卡转为 is_analyzing=True 占位卡，
                # 此处 on_result 更新该卡状态，不再依赖 score==0 判定失败。
                on_result(result_row)
        except asyncio.CancelledError:
            raise  # R2 合规
        except Exception as e:
            if on_card_error:
                on_card_error(name_str, DataSanitizer.sanitize_error(e))
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] retry_single failed (%s: %s)",
                exc_info=True,
            )

    async def _preflight_cloud_call(self, context: dict) -> str | None:
        """云端调用前置检查，返回阻断原因 i18n key（None 表示放行）。

        批量路径（run_ai_analysis）与 retry_single 必须共用同一套 guard：
        重试若跳过预算检查，预算上限会被点击次数无限突破；跳过 provider 确认
        则数据可能外发至未授权对象。此方法仅做静态检查，不产出 UI 通知——
        由调用方据此触发 on_card_error / on_progress。
        """
        if not AIService().is_cloud_available():
            return "ai_not_configured"
        ack_providers = collect_cloud_ack_providers(ConfigHandler.get_llm_provider())
        if not all(ConfigHandler.is_ai_external_acknowledged(provider=p) for p in ack_providers):
            return "ai_external_acknowledgment_prompt"
        await self._ensure_cost_tracker_engine()
        if await self._ai_budget_exhausted():
            return "ai_budget_exceeded"
        if await self._should_prompt_unpriced():
            # 保守提示：预算已设且本月存在不可计价调用 → 先向用户确认（进程级一次）。
            # 有确认能力（UI 注入 on_ai_unpriced_ack_request）→ 用户确认后放行并置进程级标记；
            # 无法确认（夜间任务 / 无 UI）→ 保守拒绝，不静默放行不可计量调用（R21）。
            if await self._confirm_unpriced(context):
                return None
            # B1（review-pr1073）：拒绝原因写 context 标志，夜间 _prediction_logic 据此可诊断。
            context["_ai_unpriced_prompt"] = True
            return "ai_budget_unpriced_prompt"
        return None

    async def _ensure_cost_tracker_engine(self) -> None:
        """经 EngineProvider 惰性注入 AIUsageTracker 的引擎。

        对抗审查修正：AIUsageTracker 不反向依赖 ``CacheManager`` 单例，engine 由
        调用方注入。此处经 ``engine_provider.get_engine`` 获取 CacheManager 登记的
        受管引擎，仅在引擎存在且未释放（R5 判活）时注入，避免在 disposed 引擎上持久化。
        """
        from data.persistence.engine_provider import is_disposed, get_engine as _provider_engine
        from services.ai_service.usage_tracker import AIUsageTracker  # lazy-import: 运行期依赖

        engine = _provider_engine()
        if engine is not None and not is_disposed(engine):
            AIUsageTracker().configure(engine=engine)

    async def _ai_budget_exhausted(self) -> bool:
        """本月累计成本是否已达月度预算上限。

        上限取自 ``ai_cost_limit_cny`` 配置（元，>0 时启用；None/0 视为不限制）。
        读取失败或未启用均按「未超限」处理（不软停, 不阻断 AI 分析）。
        """
        from services.ai_service.usage_tracker import AIUsageTracker  # lazy-import

        limit_cny = ConfigHandler.get_setting("ai_cost_limit_cny")
        if not limit_cny or limit_cny <= 0:
            return False
        month_cost_cents = await AIUsageTracker().get_month_cost_cents()
        return month_cost_cents >= round(limit_cny * 100)

    async def _should_prompt_unpriced(self) -> bool:
        """是否应弹出「本月存在不可计价调用」保守确认提示。

        语义（用户拍板「保守 —— 提示确认」）：预算已设置（``ai_cost_limit_cny>0``）且
        本月存在不可计价调用时，即便未超限也要先向用户确认（不可计价量可能绕过预算护栏）。
        确认动作由 UI 层触发；确认标记 ``_ai_unpriced_acknowledged`` 为进程级一次
        （重启后重置，每月至少校验/提示一次）。
        """
        if self._ai_unpriced_acknowledged:
            return False
        limit_cny = ConfigHandler.get_setting("ai_cost_limit_cny")
        if not limit_cny or limit_cny <= 0:
            return False
        from services.ai_service.usage_tracker import AIUsageTracker  # lazy-import

        month_unpriced_calls, _ = await AIUsageTracker().get_month_unpriced()
        return month_unpriced_calls > 0

    async def _confirm_unpriced(self, context: dict) -> bool:
        """向用户确认「继续发起不可计价调用」（进程级一次确认）。

        经 context 注入的 ``on_ai_unpriced_ack_request`` 协程（由 UI ViewModel 提供，
        与 SEC-01 的 ``on_ai_egress_ack_request`` 桥接同构）挂起等待用户决策：
        - 确认 ⇒ UI 置 ``_ai_unpriced_acknowledged = True`` 后返回 True（本进程不再弹）；
        - 拒绝 ⇒ 返回 False；
        - 无确认能力（夜间任务 / 未注入回调）⇒ 返回 False（保守拒绝，不静默放行，R21）。
        R2：asyncio.CancelledError 直接传播，不被 except Exception 吞没。
        """
        ack_request = context.get("on_ai_unpriced_ack_request")
        if ack_request is None:
            return False
        try:
            confirmed = await ack_request()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            # log_classified 自动填充前两个 %s（error code + 脱敏异常），勿额外传参。
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Unpriced ack request failed (%s: %s) — conservatively refusing to proceed",
            )
            return False
        if confirmed:
            self._ai_unpriced_acknowledged = True
        return confirmed

    @staticmethod
    def _accumulate_usage(ai_usage_cluster: dict, res: dict) -> None:
        """把一次成功 LLM 调用的消耗统一累加进 usage 聚簇（批量 + 重试共用）。

        - ``usage`` 存在 → 无条件计数 calls / tokens；
        - ``cost`` 为 int/float（含 0.0，即 ``cost is not None``）→ 计入 ``cost_cny``；
          免费模型 cost==0.0 属已计价，走成本记账归零，不误计 unpriced（对齐 litellm 语义）；
        - ``cost`` 为 None（不可计量，R21）→ 计入 ``unpriced_calls``/``unpriced_tokens``，
          避免「不可计量」被呈现为「零成本」；同时经 ``res["model"]``（litellm_client 携带）
          聚合 ``unpriced_by_model`` 明细（Mi2，供诊断"哪些模型无法计价"）。
        """
        usage = res.get("usage")
        if not usage or not isinstance(usage, dict):
            return
        ai_usage_cluster["calls"] += 1
        ai_usage_cluster["tokens"] += int(usage.get("total_tokens", 0) or 0)
        cost = res.get("cost")
        if cost is not None and isinstance(cost, (int, float)):
            ai_usage_cluster["cost_cny"] += float(cost)
        else:
            # SEC/R21: 不可计价（cost None / 非数值）必须计数，否则「不可计量」被呈现为「零成本」。
            ai_usage_cluster["unpriced_calls"] += 1
            ai_usage_cluster["unpriced_tokens"] += int(usage.get("total_tokens", 0) or 0)
            # Mi2（review-pr1073）：model→calls 明细计数（litellm_client result 带 model 键）。
            model = res.get("model")
            if isinstance(model, str) and model:
                by_model = ai_usage_cluster.get("unpriced_by_model")
                if isinstance(by_model, dict):
                    by_model[model] = by_model.get(model, 0) + 1

    async def _track_cost(
        self,
        cost_cny: float | None,
        unpriced_calls: int = 0,
        unpriced_tokens: int = 0,
    ) -> None:
        """将本次耗用的成本（元）与不可计价量计入 AIUsageTracker 本月累计。

        - ``cost_cny`` 非 None（含 0.0 免费）→ 转分后累计；None（不可计量）不累计成本；
        - ``unpriced_calls > 0`` → 累计不可计价调用量与 tokens（R21 诚实兜底，
          修正原「0 元不写库」导致的不可计价记账缺失）。
        """
        from services.ai_service.usage_tracker import AIUsageTracker  # lazy-import

        tracker = AIUsageTracker()
        # N1（review-pr1073）：cost_cny > 0 才写成本账——cost_cny==0.0（免费模型）跳过分
        # 转为 0 的记账写入（账目无意义写零），等价于"归零记账"，与成本语义不冲突。
        if cost_cny is not None and cost_cny > 0:
            await tracker.add_cost_cents(round(cost_cny * 100))
        if unpriced_calls > 0:
            await tracker.add_unpriced(unpriced_calls, unpriced_tokens)

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

    @staticmethod
    def _build_result_row(
        row_data: dict,
        res: object,
        *,
        error_reason: str | None = None,
    ) -> dict:
        """把单股 AI 结果组装为结果行。

        D3-6: 不再以 score==0 作为丢弃判据。结果行始终保留原始候选列，
        并通过 ai_status 表达单股 AI 结论：

        - ai_status="analyzed": res 为正常 dict 且 score>0，携带 ai_score/ai_reason/confidence/thinking
        - ai_status="rejected": res 为正常 dict 且 score==0（模型明确否决），ai_score=0，ai_reason 保留否决理由
        - ai_status="failed":   res 为 None/异常，或 stock_analysis 失败分支的 dict
                                （含 error 字段 / ai_status="failed"），或成功返回但 score 缺失/不可解析
                                （R21：不把"没打分"伪装成"否决"），ai_score=None，ai_reason 承载错误分类

        返回始终为 dict（保留原始行全部字段），不再返回 None。
        """
        row_dict = dict(row_data)

        # 失败/未完成路径：保留原始候选行，填充 failed 状态
        if isinstance(res, Exception) or res is None:
            row_dict["ai_status"] = "failed"
            row_dict["ai_score"] = None
            row_dict["ai_reason"] = error_reason or ""
            row_dict["thinking"] = ""
            row_dict["confidence"] = None
            return row_dict

        # 失败 dict 路径（stock_analysis 失败分支返回）：显式标记 ai_status="failed"
        # 或携带 error 字段（如 "All LLM providers unavailable" / "Analysis timeout"）。
        # 这些 dict 的 score 已为 None，不能按 score==0 判为"AI 否决"（AI-01）——
        # 否则"AI 没跑成"会被伪装成"AI 结论是否决"，污染复盘与误导用户。
        if isinstance(res, dict) and (res.get("ai_status") == "failed" or res.get("error")):
            row_dict["ai_status"] = "failed"
            row_dict["ai_score"] = None
            row_dict["confidence"] = None
            row_dict["ai_reason"] = str(res.get("error") or error_reason or "")
            row_dict["thinking"] = ""
            return row_dict

        score_val = res.get("score", 0)  # type: ignore[union-attr]
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

        # D3-6: score==0 表示模型明确否决，不再丢弃；得分>0 为 analyzed
        # 三态区分：None = 模型未打分（视为分析未完成，failed）；0 = 模型明确否决；>0 = 正常。
        # 不用 res.get("score", 0) 的默认值——缺省 0 会把"没打分"伪装成"否决"（R21）。
        score_val = res.get("score")  # type: ignore[union-attr]
        if score_val is None or not isinstance(score_val, (int, float)):
            row_dict["ai_status"] = "failed"
            row_dict["ai_score"] = None
            row_dict["ai_reason"] = summary or I18n.get("ai_card_no_score")
            row_dict["thinking"] = str(res.get("thinking", "") or "")  # type: ignore[union-attr]
            row_dict["confidence"] = None
            return row_dict
        score_float = float(score_val)
        if not (0 <= score_float <= 100):
            # R21（纵深防御）：越界评分是模型失控信号，正常链路已由 validate_ai_analysis_response
            # 置 None；此处兜底同语义——置 None 按"未打分"处理，不把违规输出钳位成满分/否决。
            row_dict["ai_status"] = "failed"
            row_dict["ai_score"] = None
            row_dict["ai_reason"] = summary or I18n.get("ai_card_no_score")
            row_dict["thinking"] = str(res.get("thinking", "") or "")  # type: ignore[union-attr]
            row_dict["confidence"] = None
            return row_dict
        score_int = round(score_float, 1)
        row_dict["ai_status"] = "rejected" if score_val == 0 else "analyzed"
        row_dict["ai_score"] = score_int
        row_dict["ai_reason"] = summary
        thinking_raw = res.get("thinking", "")  # type: ignore[union-attr]
        row_dict["thinking"] = str(thinking_raw) if thinking_raw else ""
        row_dict["confidence"] = (
            min(100, max(1, int(confidence))) if isinstance(confidence, (int, float)) else None
        )  # AI-02: 缺失不伪造为 50，保留 None
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
        vol_ratio_threshold: float = 1.7,
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
            # D1/D3 (OSS-05 收敛)：get_macd/get_kdj 委托 Polars 正本后第三返回值
            # 变为 ×2 macd 柱，且预热期/一字板场景可能显式返回 None（R21）。
            # 缺失时省略对应字段，而非注入 "k: nan" 或伪造中性值。
            trend_signal, _, _ = TechnicalAnalysis.get_macd(history_df)
            kdj_signal, k, _d, j = TechnicalAnalysis.get_kdj(history_df)  # D 指标未用于上下文

            tech_context = {
                "macd_signal": trend_signal,
                "kdj_signal": kdj_signal,
            }
            if k is not None:
                tech_context["k"] = round(k, 1)
            if j is not None:
                tech_context["j"] = round(j, 1)

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
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "llm",
                        "[AIStrategyMixin] Context builder failed (%s: %s): name=%s",
                        name,
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
            # DS-02 P3: 一次性批量拉取该股名称生效区间（非逐日查询），供历史 K 线涨跌停 as-of 判定
            name_ranges = None
            try:
                name_ranges = await dp.cache.stock_name_history_dao.get_name_ranges(ts_code)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[ai_mixin] get_name_ranges failed, fallback to current-name ST tag: %s",
                    DataSanitizer.sanitize_error(e),
                )
            history_text = _build_history_text(
                history_df,  # type: ignore[arg-type]
                ts_code=ts_code,
                stock_name=row.get("name", ""),
                name_ranges=name_ranges,
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
                learning_strategy_name=getattr(self, "name_key", None),
            )
            return ai_result

        except asyncio.CancelledError:
            raise
        except (ConnectionError, TimeoutError, httpx.TimeoutException) as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Network error (%s: %s) for %s",
                row.get("ts_code", "?"),
            )
            raise
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIStrategyMixin] Analysis failed (%s: %s) for %s",
                row.get("ts_code", "?"),
                exc_info=True,
            )
            logger.debug("[AIStrategyMixin] Analysis failed traceback:", exc_info=True)
            return None
