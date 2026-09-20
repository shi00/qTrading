import logging
from abc import abstractmethod
from dataclasses import replace

import pandas as pd
import polars as pl

from core.errors import StrategyParamError
from core.i18n import Message
from data.persistence.quality_gate import QualityGateError, QualityTier, require_quality
from strategies.attribution import ATTRIBUTION_COLUMN, attribution_to_json
from strategies.ai_mixin import AIStrategyMixin
from strategies.base_strategy import BaseStrategy
from strategies.utils import StrategyContext
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class PolarsBaseStrategy(BaseStrategy, AIStrategyMixin):
    """
    Base class for strategies that use Polars for efficient filtering.
    Handles the boilerplate of:
    1. Input validation (DataFrame empty check)
    2. Conversion to LazyFrame
    3. Error handling
    4. Collecting back to Pandas
    5. Phase 2: AI analysis (via AIStrategyMixin) — graceful degradation when AI is not configured

    Subclasses can override `required_quality_tier` to raise the data quality bar.
    Default is SILVER (continuity check). Strategies that only need data availability
    (e.g. snapshot-based filters) can set BRONZE, but will receive a runtime warning.

    Subclasses can set `enable_ai_analysis = False` (inherited from AIStrategyMixin)
    to skip Phase 2 AI analysis entirely.

    AI Context Design:
    - Subclasses MUST override `get_ai_context(row)` to inject strategy-specific context.
    - Subclasses MAY register custom context builders via `register_context_builder()`
      in __init__ for richer AI prompts (see OversoldStrategy for reference).
    - Subclasses MAY override `_prefetch_strategy_specific()` for batch data pre-fetching.
    - Subclasses MAY override `_sort_for_ai()` to customize pre-AI sort order.
    - Strategies without custom context builders will use the base AI context from
      AIStrategyMixin (history, tech indicators, news, capital flow, financials).
    """

    required_quality_tier: QualityTier = QualityTier.SILVER
    requires_fundamental_coverage: bool = False
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes",)

    # SC-01: 是否排除 ST/*ST 风险警示股（基类统一过滤，根因优先于症状）。
    # A 股量化的行业默认；专门研究 ST 股特征的策略可覆盖为 False。
    # context["exclude_st"] 可运行时覆盖（UI 开关），缺省回退本类属性。
    exclude_st: bool = True

    @require_quality(from_attr="required_quality_tier")
    async def filter(self, context: StrategyContext):
        if self.required_quality_tier == QualityTier.BRONZE:
            logger.warning(
                "[Strategy] %s: running on BRONZE-tier data (lowest quality). "
                "This strategy only checks data availability, not historical continuity. "
                "Consider upgrading to SILVER if you need MA/RSI or other technical indicators.",
                self.name,
            )

        dep_result = self.check_dependencies(context)
        if dep_result["status"] == "unready":
            logger.warning(
                "[Strategy] %s: dependencies unready, missing_keys=%s, missing_tables=%s",
                self.name,
                dep_result["missing_keys"],
                dep_result["missing_tables"],
            )
            context["_dependency_status"] = dep_result
            return pd.DataFrame()
        elif dep_result["status"] == "degraded":
            logger.info("[Strategy] %s: running in degraded mode, empty_keys=%s", self.name, dep_result["empty_keys"])
            context["_dependency_status"] = dep_result

        if self.requires_fundamental_coverage:
            df = context.get("fundamental_screening_data")
            if df is None or df.empty:
                logger.warning(
                    "[Strategy] %s: fundamental_screening_data unavailable, cannot execute fundamental strategy without it",
                    self.name,
                )
                return pd.DataFrame()
        else:
            df = context.get("screening_data")
        if df is None:
            df = context.get("data")

        if df is None or df.empty:
            return pd.DataFrame()

        try:
            # D3-4: 每次策略执行从空通道开始，避免同一 context 跨运行/跨策略残留旧警告。
            # _filter_logic 在线程池线程内仅对 list append（GIL 原子），run_async 等待
            # 完成后主协程再读取，无并发写读竞态。
            context["warnings"] = []

            # Offload CPU-intensive from_pandas, _filter_logic graph building, collect, and conversion to thread pool
            # to avoid blocking the Flet event loop during full-market screening.
            # Thread-safety: df and context are not mutated concurrently during filter() execution.
            def _convert_and_filter(df_in, ctx):
                lf = pl.from_pandas(df_in).lazy()
                lf = self._apply_exclude_st(lf, ctx)
                result_lf = self._filter_logic(lf, ctx)
                return result_lf.collect().to_pandas()

            candidates_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                lambda: _convert_and_filter(df, context),
            )
        except QualityGateError:
            raise
        except StrategyParamError:
            raise
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出策略过滤异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error("[Strategy] %s failed: %s", self.name, e, exc_info=True)
            raise RuntimeError(f"Strategy {self.name} execution failed: {DataSanitizer.sanitize_error(e)}") from e

        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        # UX-04: 归因在 AI 分支 (排序/截断) 之前生成，rank.total = AI 截断前真实候选池
        # (一次检视 Major#1: 排名语义不因 ai_mixin 的 head(cap) 失真)。早退路径同样生成。
        if self.attribution_enabled:
            total = len(candidates_df)
            candidates_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                lambda: _build_attributions(self, candidates_df, total, context),
            )

        if not self.enable_ai_analysis:
            # SC-02: 类级关闭 AI 属"AI 有效不运行"，向 warnings 声明风险检查缺失。
            # type: ignore[arg-type]  # StrategyContext→dict 不兼容，同下方 run_ai_analysis 既有模式
            self._note_ai_risk_check_skipped(context)  # type: ignore[arg-type]
            return candidates_df

        candidates_df = self._sort_for_ai(candidates_df)

        return await self.run_ai_analysis(candidates_df, context)  # type: ignore[arg-type]

    def _apply_exclude_st(self, lf: pl.LazyFrame, ctx: StrategyContext) -> pl.LazyFrame:
        """SC-01: 基类统一排除 ST/*ST 风险警示股。

        在 ``_filter_logic`` 之前施加（根因优先于症状，避免每个策略各加一次）。
        - ``ctx["exclude_st"]`` 运行时覆盖（UI 开关），缺省回退类属性 ``exclude_st``；
        - 无 ``is_st`` 列（如测试构造数据/未升级数据源）时跳过，保持向后兼容；
        - 排除数量经既有 D3-4 warnings 通道透传（「已排除 N 只风险警示股」），
          避免变成一次静默过滤（SC-01 报告建议 3）。
        在线程池线程内执行：仅对 ctx list append（GIL 原子），主协程 await 完成后读取，无竞态。
        """
        exclude = ctx.get("exclude_st", self.exclude_st)
        if not exclude or "is_st" not in lf.collect_schema().names():
            return lf
        # NULL is_st（stock_basic.name 可空，双 COALESCE 仍可能为 NULL）按「非 ST」处理：
        # 不排除且不计数，避免 NULL 行被 ~pl.col() 过滤掉造成静默漏股。
        excluded = lf.filter(pl.col("is_st").is_not_null() & pl.col("is_st")).select(pl.len()).collect().item()
        if excluded:
            ctx.setdefault("warnings", []).append(Message("strategy_excluded_st", {"count": excluded}))
        return lf.filter(pl.col("is_st").fill_null(False).not_())

    @abstractmethod
    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        """
        Implement the specific filtering logic here.
        :param lf: Input LazyFrame containing merged data
        :param context: StrategyContext dict (see strategies.utils.StrategyContext)
        :return: Filtered/Sorted LazyFrame
        """
        pass


def _build_attributions(strategy, df: pd.DataFrame, total: int, context: StrategyContext) -> pd.DataFrame:
    """为每个筛选行构建归因并写入 ``ATTRIBUTION_COLUMN`` 列 (UX-04).

    在线程池 (``TaskType.CPU``) 内执行, 不阻塞 Flet 事件循环 (R16)。
    ``rank.position`` 在此按 ``rank.field`` 在候选池内统一排序计算 (base 兜底,
    单策略内 rank.field 共享, 一次排序即可); ``rank.total`` = AI 截断前候选池总数。
    """
    rows = df.to_dict("records")
    built: list = [strategy.build_attribution(r, total, context) for r in rows]

    # 统一计算排名 (按 rank.field/ascending; 无值/缺值恒排末尾)。冻结构用 replace 重建。
    # 缺值哨兵按方向选择: 升序时置 +inf 排最后, 降序时置 -inf 排最后 (二次检视 a2)。
    ranked = [(i, a) for i, a in enumerate(built) if a is not None and a.rank is not None]
    if ranked:
        ascending = ranked[0][1].rank.ascending
        missing = float("inf") if ascending else float("-inf")
        ranked.sort(
            key=lambda i_a: i_a[1].rank.value if i_a[1].rank.value is not None else missing,
            reverse=not ascending,
        )
        for pos, (idx, attr) in enumerate(ranked, start=1):
            built[idx] = replace(attr, rank=replace(attr.rank, position=pos, total=total))

    df[ATTRIBUTION_COLUMN] = [attribution_to_json(a) for a in built]
    return df
