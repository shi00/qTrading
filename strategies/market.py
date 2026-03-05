import polars as pl
import logging
from strategies.polars_base import PolarsBaseStrategy
from strategies.base_strategy import register_strategy

logger = logging.getLogger(__name__)

@register_strategy("tech_breakout")
class TechnicalBreakoutStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_tech_breakout_name", "strategy_tech_breakout_desc")

    def get_parameters(self):
        return [
            {"name": "pct_chg_min", "label_key": "param_pct_chg_min", "type": "slider",
             "min": 0, "max": 10, "default": 2, "step": 0.5},
            {"name": "pct_chg_max", "label_key": "param_pct_chg_max", "type": "slider",
             "min": 3, "max": 10, "default": 7, "step": 0.5},
            {"name": "turnover_min", "label_key": "param_turnover_min", "type": "slider",
             "min": 0, "max": 20, "default": 3, "step": 1},
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get('params', {})
        chg_min = p.get('pct_chg_min', 2)
        chg_max = p.get('pct_chg_max', 7)
        turnover = p.get('turnover_min', 3)
        return (
            lf
            .drop_nulls(subset=['pct_chg', 'turnover_rate'])
            .filter(pl.col('pct_chg').is_between(chg_min, chg_max))
            .filter(pl.col('turnover_rate') > turnover)
            .sort('pct_chg', descending=True)
        )

@register_strategy("northbound")
class NorthboundStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_northbound_name", "strategy_northbound_desc")

    def get_parameters(self):
        return [
            {"name": "nb_ratio_min", "label_key": "param_nb_ratio_min", "type": "slider",
             "min": 1, "max": 20, "default": 3, "step": 0.5},
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        nb_df = context.get('northbound_data')
        p = context.get('params', {})
        target_ratio = p.get('nb_ratio_min', 3)
        
        if nb_df is None or nb_df.empty:
            return lf.head(0) # Return empty
        
        try:
            nb_lf = pl.from_pandas(nb_df).lazy()
            # Select limited columns from base to avoid collisions or generic naming
            base_lf = lf.select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])

            return (
                nb_lf
                .drop_nulls(subset=['ratio'])
                .filter(pl.col('ratio') > target_ratio)
                .filter(pl.col('ts_code').str.ends_with('.SH') | pl.col('ts_code').str.ends_with('.SZ'))
                .join(base_lf, on='ts_code', how='inner')
                .sort('ratio', descending=True)
            )
        except Exception as e:
            logger.warning(f"[{self.name}] Logic error: {e}. Params: {context.get('params')}", exc_info=True)
            return lf.head(0)

@register_strategy("institutional")
class InstitutionalStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_institutional_name", "strategy_institutional_desc")

    def get_parameters(self):
        return [
            {"name": "inst_net_min", "label_key": "param_inst_net_min", "type": "slider",
             "min": 0, "max": 20000, "default": 3000, "step": 500},
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        lhb = context.get('top_list')
        p = context.get('params', {})
        target_net = p.get('inst_net_min', 3000)
        
        if lhb is None or lhb.empty: 
            return lf.head(0)
            
        if 'net_amount' not in lhb.columns:
             return lf.head(0)

        try:
            top_lf = pl.from_pandas(lhb).lazy()
            base_lf = lf.select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                top_lf
                .filter(pl.col('net_amount').is_not_null())
                .filter(pl.col('net_amount') > target_net)
                .join(base_lf, on='ts_code', how='inner')
                .sort('net_amount', descending=True)
            )
        except Exception as e:
             logger.warning(f"[{self.name}] Logic error: {e}. Params: {context.get('params')}", exc_info=True)
             return lf.head(0)

@register_strategy("block_trade")
class BlockTradeStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_block_trade_name", "strategy_block_trade_desc")

    def get_parameters(self):
        return [
            {"name": "block_amount_min", "label_key": "param_block_amount_min", "type": "slider",
             "min": 0, "max": 10000, "default": 1000, "step": 200},
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        block = context.get('block_trade')
        p = context.get('params', {})
        target_amount = p.get('block_amount_min', 1000)
        
        if block is None or block.empty:
             return lf.head(0)
             
        if 'amount' not in block.columns:
             return lf.head(0)

        try:
            block_lf = pl.from_pandas(block).lazy()
            base_lf = lf.select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                block_lf
                .filter(pl.col('amount') > target_amount)
                .group_by('ts_code')
                .agg([
                    pl.col('amount').sum(),
                    pl.col('vol').sum(),
                    pl.col('price').mean()
                ])
                .join(base_lf, on='ts_code', how='inner')
                .sort('amount', descending=True)
            )
        except Exception as e:
            logger.warning(f"[{self.name}] Logic error: {e}. Params: {context.get('params')}", exc_info=True)
            return lf.head(0)
