import polars as pl
from strategies.polars_base import PolarsBaseStrategy
from strategies.base_strategy import register_strategy

@register_strategy("tech_breakout")
class TechnicalBreakoutStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_tech_breakout_name", "strategy_tech_breakout_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return (
            lf
            .filter(pl.col('pct_chg').is_between(2, 7))
            .filter(pl.col('turnover_rate').is_between(3, 15))
            .sort('pct_chg', descending=True)
        )

@register_strategy("northbound")
class NorthboundStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_northbound_name", "strategy_northbound_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        nb_df = context.get('northbound_data')
        
        if nb_df is None or nb_df.empty:
            return lf.head(0) # Return empty
        
        try:
            nb_lf = pl.from_pandas(nb_df).lazy()
            # Select limited columns from base to avoid collisions or generic naming
            base_lf = lf.select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])

            return (
                nb_lf
                .filter(pl.col('ratio') > 3)
                .filter(pl.col('ts_code').str.ends_with('.SH') | pl.col('ts_code').str.ends_with('.SZ'))
                .join(base_lf, on='ts_code', how='inner')
                .sort('ratio', descending=True)
            )
        except Exception as e:
            # We log here but let base class handle empty return
            return lf.head(0)

@register_strategy("institutional")
class InstitutionalStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_institutional_name", "strategy_institutional_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        lhb = context.get('top_list')
        
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
                .filter(pl.col('net_amount') > 3000)
                .join(base_lf, on='ts_code', how='inner')
                .sort('net_amount', descending=True)
            )
        except Exception:
             return lf.head(0)

@register_strategy("block_trade")
class BlockTradeStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_block_trade_name", "strategy_block_trade_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        block = context.get('block_trade')
        
        if block is None or block.empty:
             return lf.head(0)
             
        if 'amount' not in block.columns:
             return lf.head(0)

        try:
            block_lf = pl.from_pandas(block).lazy()
            base_lf = lf.select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                block_lf
                .filter(pl.col('amount') > 1000)
                .group_by('ts_code')
                .agg([
                    pl.col('amount').sum(),
                    pl.col('vol').sum(),
                    pl.col('price').mean()
                ])
                .join(base_lf, on='ts_code', how='inner')
                .sort('amount', descending=True)
            )
        except Exception:
            return lf.head(0)
