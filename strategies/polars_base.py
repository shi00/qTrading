import logging
from abc import abstractmethod

import pandas as pd
import polars as pl

from data.persistence.quality_gate import QualityGateError, QualityTier, _check_tier
from strategies.ai_mixin import AIStrategyMixin
from strategies.base_strategy import BaseStrategy
from strategies.utils import StrategyContext

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
    Default is BRONZE (availability check only). Strategies that depend on
    historical continuity (e.g. technical indicators) should set SILVER.

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

    async def filter(self, context: StrategyContext):
        _check_tier(
            context.get("data_processor"),
            self.required_quality_tier,
            f"{self.__class__.__name__}.filter",
        )

        dep_result = self.check_dependencies(context)
        if dep_result["status"] == "unready":
            logger.warning(
                f"[Strategy] {self.name}: dependencies unready, "
                f"missing_keys={dep_result['missing_keys']}, "
                f"missing_tables={dep_result['missing_tables']}"
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
                    f"[Strategy] {self.name}: fundamental_screening_data unavailable, "
                    f"cannot execute fundamental strategy without it"
                )
                return pd.DataFrame()
        else:
            df = context.get("screening_data")
        if df is None:
            df = context.get("data")

        if df is None or df.empty:
            return pd.DataFrame()

        try:
            lf = pl.from_pandas(df).lazy()
            result_lf = self._filter_logic(lf, context)
            candidates_df = result_lf.collect().to_pandas()
        except QualityGateError:
            raise
        except Exception as e:
            logger.error("[Strategy] %s failed: %s", self.name, e, exc_info=True)
            raise RuntimeError(f"Strategy {self.name} execution failed: {e}") from e

        if candidates_df is None or candidates_df.empty:
            return pd.DataFrame()

        if not self.enable_ai_analysis:
            return candidates_df

        candidates_df = self._sort_for_ai(candidates_df)

        return await self.run_ai_analysis(candidates_df, context)  # type: ignore[arg-type]

    @abstractmethod
    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        """
        Implement the specific filtering logic here.
        :param lf: Input LazyFrame containing merged data
        :param context: StrategyContext dict (see strategies.utils.StrategyContext)
        :return: Filtered/Sorted LazyFrame
        """
        pass
