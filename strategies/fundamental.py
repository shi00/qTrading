import polars as pl

from strategies.base_strategy import register_strategy
from strategies.polars_base import PolarsBaseStrategy


@register_strategy("value")
class ValueStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_value_name", "strategy_value_desc")

    def get_parameters(self):
        return [
            {
                "name": "pe_min",
                "label_key": "param_pe_min",
                "type": "slider",
                "min": 0,
                "max": 50,
                "default": 5,
                "step": 1,
            },
            {
                "name": "pe_max",
                "label_key": "param_pe_max",
                "type": "slider",
                "min": 5,
                "max": 100,
                "default": 20,
                "step": 1,
            },
            {
                "name": "pb_max",
                "label_key": "param_pb_max",
                "type": "slider",
                "min": 0.5,
                "max": 10,
                "default": 3,
                "step": 0.5,
            },
            {
                "name": "dv_min",
                "label_key": "param_dv_min",
                "type": "slider",
                "min": 0,
                "max": 10,
                "default": 2,
                "step": 0.5,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        pe_min = p.get("pe_min", 5)
        pe_max = p.get("pe_max", 20)
        pb_max = p.get("pb_max", 3)
        dv_min = p.get("dv_min", 2)
        return (
            lf.drop_nulls(subset=["pe_ttm", "pb", "dv_ttm"])
            .filter(pl.col("pe_ttm").is_between(pe_min, pe_max))
            .filter(pl.col("pb").is_between(0, pb_max))
            .filter(pl.col("dv_ttm") > dv_min)
            .sort("dv_ttm", descending=True)
        )


@register_strategy("growth")
class GrowthStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_growth_name", "strategy_growth_desc")

    def get_parameters(self):
        return [
            {
                "name": "revenue_growth_min",
                "label_key": "param_revenue_growth",
                "type": "slider",
                "min": 0,
                "max": 100,
                "default": 20,
                "step": 5,
            },
            {
                "name": "profit_growth_min",
                "label_key": "param_profit_growth",
                "type": "slider",
                "min": 0,
                "max": 200,
                "default": 25,
                "step": 5,
            },
            {
                "name": "roe_min",
                "label_key": "param_roe_min",
                "type": "slider",
                "min": 0,
                "max": 50,
                "default": 15,
                "step": 1,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        rev = p.get("revenue_growth_min", 20)
        profit = p.get("profit_growth_min", 25)
        roe = p.get("roe_min", 15)
        return (
            lf.drop_nulls(subset=["or_yoy", "netprofit_yoy", "roe"])
            .filter(pl.col("or_yoy") > rev)
            .filter(pl.col("netprofit_yoy") > profit)
            .filter(pl.col("roe") > roe)
            .sort("roe", descending=True)
        )


@register_strategy("dividend")
class DividendStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_dividend_name", "strategy_dividend_desc")

    def get_parameters(self):
        return [
            {
                "name": "dv_min",
                "label_key": "param_dv_min",
                "type": "slider",
                "min": 1,
                "max": 15,
                "default": 4,
                "step": 0.5,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        dv_min = p.get("dv_min", 4)
        return (
            lf.drop_nulls(subset=["dv_ttm"])
            .filter(pl.col("dv_ttm") > dv_min)
            .sort("dv_ttm", descending=True)
        )


@register_strategy("cashflow")
class CashFlowStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_cashflow_name", "strategy_cashflow_desc")

    def get_parameters(self):
        return [
            {
                "name": "debt_max",
                "label_key": "param_debt_max",
                "type": "slider",
                "min": 10,
                "max": 90,
                "default": 50,
                "step": 5,
            },
            {
                "name": "roe_min",
                "label_key": "param_roe_min",
                "type": "slider",
                "min": 0,
                "max": 50,
                "default": 10,
                "step": 1,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        debt_max = p.get("debt_max", 50)
        roe_min = p.get("roe_min", 10)
        return (
            lf.drop_nulls(subset=["debt_to_assets", "roe"])
            .filter(pl.col("debt_to_assets") < debt_max)
            .filter(pl.col("roe") > roe_min)
            .sort("roe", descending=True)
        )


@register_strategy("large_pe")
class LargePEStrategy(PolarsBaseStrategy):
    def __init__(self):
        super().__init__("strategy_large_pe_name", "strategy_large_pe_desc")

    def get_parameters(self):
        return [
            {
                "name": "market_cap_min",
                "label_key": "param_market_cap",
                "type": "slider",
                "min": 50,
                "max": 5000,
                "default": 500,
                "step": 50,
            },
            {
                "name": "pe_max",
                "label_key": "param_pe_max",
                "type": "slider",
                "min": 5,
                "max": 50,
                "default": 15,
                "step": 1,
            },
        ]

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        # UI shows 亿, DB total_mv is 万元 → multiply by 10000
        cap_min = p.get("market_cap_min", 500) * 10000
        pe_max = p.get("pe_max", 15)
        return (
            lf.drop_nulls(subset=["total_mv", "pe_ttm"])
            .filter(pl.col("total_mv") > cap_min)
            .filter(pl.col("pe_ttm").is_between(0, pe_max))
            .sort("total_mv", descending=True)
        )
