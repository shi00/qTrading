import logging
import typing
from abc import abstractmethod

import pandas as pd
import polars as pl

from data.persistence.quality_gate import QualityGateError, QualityTier, require_quality
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class PolarsBaseStrategy(BaseStrategy):
    """
    Base class for strategies that use Polars for efficient filtering.
    Handles the boilerplate of:
    1. Input validation (DataFrame empty check)
    2. Conversion to LazyFrame
    3. Error handling
    4. collecting back to Pandas
    """

    @require_quality(QualityTier.BRONZE)
    async def filter(self, context: typing.Any):
        """
        Template method that handles boilerplates.
        Subclasses should implement `_filter_logic(lazy_frame, context) -> LazyFrame`.
        """
        df = context.get("screening_data")
        # Fallback for legacy contexts
        if df is None:
            df = context.get("data")

        if df is None or df.empty:
            return pd.DataFrame()

        try:
            # Convert to LazyFrame for optimization
            lf = pl.from_pandas(df).lazy()

            # Execute specific strategy logic
            result_lf = self._filter_logic(lf, context)

            # Collect result
            return result_lf.collect().to_pandas()

        except QualityGateError as e:
            # Handle Quality Gate rejection (graceful exit)
            logger.warning(f"[Strategy] {self.name} Blocked: {e}")
            # Raising it up allows UI to show specific error
            raise e
        except Exception as e:
            logger.error(f"[Strategy] {self.name} failed: {e}", exc_info=True)
            raise RuntimeError(f"Strategy {self.name} execution failed: {e}") from e

    @abstractmethod
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        """
        Implement the specific filtering logic here.
        :param lf: Input LazyFrame containing merged data
        :param context: Full context dict (for accessing other data like 'block_trade')
        :return: Filtered/Sorted LazyFrame
        """
        pass
