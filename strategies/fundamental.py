import polars as pl
from strategies.polars_base import PolarsBaseStrategy
from strategies.base_strategy import register_strategy

@register_strategy("value")
class ValueStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_value_name", "strategy_value_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return (
            lf
            .filter(pl.col('pe_ttm').is_between(5, 20))
            .filter(pl.col('pb').is_between(0.5, 3))
            .filter(pl.col('dv_ttm') > 2)
            .sort('dv_ttm', descending=True)
        )

@register_strategy("growth")
class GrowthStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_growth_name", "strategy_growth_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return (
            lf
            .filter(pl.col('or_yoy') > 20)
            .filter(pl.col('netprofit_yoy') > 25)
            .filter(pl.col('roe') > 15)
            .sort('roe', descending=True)
        )

@register_strategy("dividend")
class DividendStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_dividend_name", "strategy_dividend_desc")
        
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return (
            lf
            .filter(pl.col('dv_ttm') > 4)
            .sort('dv_ttm', descending=True)
        )

@register_strategy("cashflow")
class CashFlowStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_cashflow_name", "strategy_cashflow_desc")
    
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return (
            lf
            .filter(pl.col('debt_to_assets') < 50)
            .filter(pl.col('roe') > 10)
            .sort('roe', descending=True)
        )

@register_strategy("large_pe")
class LargePEStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_large_pe_name", "strategy_large_pe_desc")
    
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        # Market cap > 500亿 (unit: 万元 -> 5000000)
        return (
            lf
            .filter(pl.col('total_mv') > 5000000)
            .filter(pl.col('pe_ttm').is_between(0, 15)) 
            .sort('total_mv', descending=True)
        )
