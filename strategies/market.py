import polars as pl
from strategies.polars_base import PolarsBaseStrategy

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

class NorthboundStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_northbound_name", "strategy_northbound_desc")

    def filter(self, context):
        # Override standard filter because source data is different (northbound_data)
        # But we can try to adapt... 
        # Actually Northbound uses a join, which fits _filter_logic if we pass base_df as LazyFrame too?
        # But base input for _filter_logic is 'screening_data'.
        # Northbound primary input is 'northbound_data'.
        # So we keep custom filter mostly, but maybe use PolarsBaseStrategy for result collecting?
        # Let's keep it manual but safer here.
        
        nb_df = context.get('northbound_data')
        base_df = context.get('screening_data')
        
        if nb_df is None or nb_df.empty or base_df is None:
            return super().filter(context) # Returns empty via base check
        
        try:
            nb_lf = pl.from_pandas(nb_df).lazy()
            base_lf = pl.from_pandas(base_df).lazy().select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                nb_lf
                .filter(pl.col('ratio') > 3)
                .filter(pl.col('ts_code').str.ends_with('.SH') | pl.col('ts_code').str.ends_with('.SZ'))
                .join(base_lf, on='ts_code', how='inner')
                .sort('ratio', descending=True)
                .collect()
                .to_pandas()
            )
        except Exception:
            return pd.DataFrame()

    def _filter_logic(self, lf, context):
        # Not used because we override filter
        return lf

class InstitutionalStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_institutional_name", "strategy_institutional_desc")

    def filter(self, context):
        lhb = context.get('top_list')
        base_df = context.get('screening_data')
        
        if lhb is None or lhb.empty or base_df is None: 
            return super().filter(context)
            
        if 'net_amount' not in lhb.columns:
             return super().filter(context)

        try:
            lf = pl.from_pandas(lhb).lazy()
            base_lf = pl.from_pandas(base_df).lazy().select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                lf
                .filter(pl.col('net_amount').is_not_null())
                .filter(pl.col('net_amount') > 3000)
                .join(base_lf, on='ts_code', how='inner')
                .sort('net_amount', descending=True)
                .collect()
                .to_pandas()
            )
        except Exception:
             return pd.DataFrame()

    def _filter_logic(self, lf, context):
        return lf

class BlockTradeStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_block_trade_name", "strategy_block_trade_desc")

    def filter(self, context):
        block = context.get('block_trade')
        base_df = context.get('screening_data')
        
        if block is None or block.empty or base_df is None:
             return super().filter(context)
             
        if 'amount' not in block.columns:
             return super().filter(context)

        try:
            lf = pl.from_pandas(block).lazy()
            base_lf = pl.from_pandas(base_df).lazy().select(['ts_code', 'name', 'industry', 'pe_ttm', 'total_mv'])
            
            return (
                lf
                .filter(pl.col('amount') > 1000)
                .group_by('ts_code')
                .agg([
                    pl.col('amount').sum(),
                    pl.col('vol').sum(),
                    pl.col('price').mean()
                ])
                .join(base_lf, on='ts_code', how='inner')
                .sort('amount', descending=True)
                .collect()
                .to_pandas()
            )
        except Exception:
            return pd.DataFrame()

    def _filter_logic(self, lf, context):
        return lf
