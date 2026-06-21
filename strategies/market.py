import logging

import pandas as pd
import polars as pl

from data.persistence.quality_gate import QualityTier
from strategies.base_strategy import register_strategy
from strategies.polars_base import PolarsBaseStrategy
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@register_strategy("volume_breakout")
class VolumeBreakoutStrategy(PolarsBaseStrategy):
    """
    P1-19 fix: Renamed from TechnicalBreakoutStrategy to VolumeBreakoutStrategy.
    The strategy filters stocks by price change percentage and turnover rate,
    which is more accurately described as "volume breakout" rather than
    "technical breakout" (which would imply pattern-based analysis).
    """

    required_quality_tier = QualityTier.SILVER
    enable_ai_analysis = False

    def __init__(self):
        super().__init__("strategy_volume_breakout_name", "strategy_volume_breakout_desc")
        self._data_warnings: list[str] = []

    def get_parameters(self):
        return [
            {
                "name": "pct_chg_min",
                "label_key": "param_pct_chg_min",
                "type": "slider",
                "min": 0,
                "max": 10,
                "default": 2,
                "step": 0.5,
            },
            {
                "name": "pct_chg_max",
                "label_key": "param_pct_chg_max",
                "type": "slider",
                "min": 3,
                "max": 10,
                "default": 7,
                "step": 0.5,
            },
            {
                "name": "turnover_min",
                "label_key": "param_turnover_min",
                "type": "slider",
                "min": 0,
                "max": 20,
                "default": 3,
                "step": 1,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        self._data_warnings = []
        p = context.get("params", {})
        chg_min = p.get("pct_chg_min", 2)
        chg_max = p.get("pct_chg_max", 7)
        turnover = p.get("turnover_min", 3)
        if chg_min >= chg_max:
            warning_msg = (
                f"[VolumeBreakoutStrategy] pct_chg_min ({chg_min}) >= pct_chg_max ({chg_max}), "
                f"auto-adjusting pct_chg_max to {chg_min + 0.5}"
            )
            logger.warning(warning_msg)
            self._data_warnings.append(warning_msg)
            chg_max = chg_min + 0.5
        return (
            lf.drop_nulls(subset=["pct_chg", "turnover_rate"])
            .filter(pl.col("pct_chg").is_between(chg_min, chg_max))
            .filter(pl.col("turnover_rate") > turnover)
            .sort("pct_chg", descending=True)
        )


@register_strategy("northbound_holding")
class NorthboundHoldingStrategy(PolarsBaseStrategy):
    """
    Renamed from NorthboundStrategy to clarify semantics.
    This strategy filters stocks by northbound (HK capital) HOLDING RATIO,
    not by net capital flow. For net flow analysis, use NorthboundFlowStrategy.
    """

    required_quality_tier = QualityTier.BRONZE
    enable_ai_analysis = False
    required_context_keys: tuple[str, ...] = ("northbound_data",)
    required_tables: tuple[str, ...] = ("northbound_holding",)
    required_apis: tuple[str, ...] = ("hk_hold",)

    def __init__(self):
        super().__init__("strategy_northbound_holding_name", "strategy_northbound_holding_desc")

    def get_parameters(self):
        return [
            {
                "name": "nb_ratio_min",
                "label_key": "param_nb_ratio_min",
                "type": "slider",
                "min": 1,
                "max": 20,
                "default": 3,
                "step": 0.5,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        nb_df = context.get("northbound_data")
        p = context.get("params", {})
        target_ratio = p.get("nb_ratio_min", 3)

        if nb_df is None or nb_df.empty:
            return lf.head(0)  # Return empty

        try:
            nb_lf = pl.from_pandas(nb_df).lazy()
            # Select limited columns from base to avoid collisions or generic naming
            base_lf = lf.select(["ts_code", "name", "industry", "pe_ttm", "total_mv"])

            return (
                nb_lf.drop_nulls(subset=["ratio"])
                .filter(pl.col("ratio") > target_ratio)
                .filter(
                    pl.col("ts_code").str.ends_with(".SH") | pl.col("ts_code").str.ends_with(".SZ"),
                )
                .join(base_lf, on="ts_code", how="inner")
                .sort("ratio", descending=True)
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Logic error: {e}. Params: {context.get('params')}",
                exc_info=True,
            )
            return lf.head(0)


@register_strategy("northbound_flow")
class NorthboundFlowStrategy(PolarsBaseStrategy):
    """
    Northbound NET CAPITAL FLOW as market sentiment gating signal.

    moneyflow_hsgt is market-level data (no ts_code), so it cannot be
    joined with individual stocks. Instead, north_money is used as a
    gating condition: when net northbound inflow exceeds the threshold,
    the strategy selects stocks from the base universe using fundamental
    criteria (market cap, PE, etc.).
    """

    enable_ai_analysis = False
    required_context_keys: tuple[str, ...] = ("northbound_flow_data",)
    required_tables: tuple[str, ...] = ("moneyflow_hsgt",)
    required_apis: tuple[str, ...] = ("moneyflow_hsgt",)

    def __init__(self):
        super().__init__("strategy_northbound_flow_name", "strategy_northbound_flow_desc")

    def get_parameters(self):
        return [
            {
                "name": "nb_flow_min",
                "label_key": "param_nb_flow_min",
                "type": "slider",
                "min": 0,
                "max": 200,
                "default": 50,
                "step": 10,
            },
            {
                "name": "total_mv_min",
                "label_key": "param_total_mv_min",
                "type": "slider",
                "min": 0,
                "max": 10000,
                "default": 100,
                "step": 100,
            },
        ]

    async def filter(self, context: dict):
        """Override filter to add northbound flow gating before _filter_logic."""
        flow_df = context.get("northbound_flow_data")
        p = context.get("params", {})
        target_flow = p.get("nb_flow_min", 50)

        if flow_df is None or flow_df.empty:
            logger.debug(f"[{self.name}] Gating: no northbound_flow_data. Returning empty.")
            return pd.DataFrame()

        try:
            flow_lf = pl.from_pandas(flow_df).lazy()
            gated_lf = flow_lf.sort("trade_date", descending=True).select(pl.col("north_money").first())
            # Offload CPU-intensive collect to thread pool
            north_money_val = (await ThreadPoolManager().run_async(TaskType.CPU, gated_lf.collect)).item()

            if north_money_val is None or north_money_val <= target_flow:
                logger.debug(
                    f"[{self.name}] Gating: north_money={north_money_val}, "
                    f"threshold={target_flow}. Returning empty (market sentiment insufficient)."
                )
                return pd.DataFrame()
        except Exception as e:
            logger.warning(f"[{self.name}] Gating check failed: {e}", exc_info=True)
            return pd.DataFrame()

        return await super().filter(context)

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        mv_min = p.get("total_mv_min", 100)

        return (
            lf.drop_nulls(subset=["total_mv", "pe_ttm"])
            .filter((pl.col("total_mv") >= mv_min) & (pl.col("pe_ttm") > 0))
            .sort("total_mv", descending=True)
        )


@register_strategy("institutional")
class InstitutionalStrategy(PolarsBaseStrategy):
    required_quality_tier = QualityTier.BRONZE
    enable_ai_analysis = False
    required_context_keys: tuple[str, ...] = ("top_list",)
    required_tables: tuple[str, ...] = ("top_list",)
    required_apis: tuple[str, ...] = ("top_list",)

    def __init__(self):
        super().__init__("strategy_institutional_name", "strategy_institutional_desc")

    def get_parameters(self):
        return [
            {
                "name": "inst_net_min",
                "label_key": "param_inst_net_min",
                "type": "slider",
                "min": 0,
                "max": 20000,
                "default": 3000,
                "step": 500,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        lhb = context.get("top_list")
        p = context.get("params", {})
        target_net = p.get("inst_net_min", 3000)

        if lhb is None or lhb.empty:
            return lf.head(0)

        if "net_amount" not in lhb.columns:
            return lf.head(0)

        try:
            top_lf = pl.from_pandas(lhb).lazy()
            base_lf = lf.select(["ts_code", "name", "industry", "pe_ttm", "total_mv"])

            return (
                top_lf.filter(pl.col("net_amount").is_not_null())
                .filter(pl.col("net_amount") > target_net)
                .join(base_lf, on="ts_code", how="inner")
                .sort("net_amount", descending=True)
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Logic error: {e}. Params: {context.get('params')}",
                exc_info=True,
            )
            return lf.head(0)


@register_strategy("block_trade")
class BlockTradeStrategy(PolarsBaseStrategy):
    required_quality_tier = QualityTier.BRONZE
    enable_ai_analysis = False
    required_context_keys: tuple[str, ...] = ("block_trade",)
    required_tables: tuple[str, ...] = ("block_trade",)
    required_apis: tuple[str, ...] = ("block_trade",)

    def __init__(self):
        super().__init__("strategy_block_trade_name", "strategy_block_trade_desc")

    def get_parameters(self):
        return [
            {
                "name": "block_amount_min",
                "label_key": "param_block_amount_min",
                "type": "slider",
                "min": 0,
                "max": 10000,
                "default": 1000,
                "step": 200,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        block = context.get("block_trade")
        p = context.get("params", {})
        target_amount = p.get("block_amount_min", 1000)

        if block is None or block.empty:
            return lf.head(0)

        if "amount" not in block.columns:
            return lf.head(0)

        try:
            block_lf = pl.from_pandas(block).lazy()
            base_lf = lf.select(["ts_code", "name", "industry", "pe_ttm", "total_mv"])

            return (
                block_lf.filter(pl.col("amount") > target_amount)
                .group_by("ts_code")
                .agg(
                    [
                        pl.col("amount").sum(),
                        pl.col("vol").sum(),
                        ((pl.col("price") * pl.col("amount")).sum() / pl.col("amount").sum()).alias("price"),
                    ],
                )
                .join(base_lf, on="ts_code", how="inner")
                .sort("amount", descending=True)
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Logic error: {e}. Params: {context.get('params')}",
                exc_info=True,
            )
            return lf.head(0)
