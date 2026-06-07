import pandas as pd
import polars as pl
from decimal import Decimal

from data.persistence.quality_gate import QualityTier
from strategies.base_strategy import register_strategy
from strategies.polars_base import PolarsBaseStrategy
from strategies.utils import fmt_val


@register_strategy("value")
class ValueStrategy(PolarsBaseStrategy):
    required_quality_tier = QualityTier.SILVER
    requires_fundamental_coverage = True
    required_context_keys: tuple[str, ...] = ("screening_data", "fundamental_screening_data")
    required_tables: tuple[str, ...] = ("daily_quotes", "daily_indicators")

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

    def get_ai_context(self, row: dict) -> str:
        # NOTE: AI prompts are intentionally in Chinese (A-share analysis context).
        # Do NOT internationalize these strings — they are LLM prompts, not UI text.
        fv = fmt_val
        pe = fv(row.get("pe_ttm"))
        pb = fv(row.get("pb"))
        dv = fv(row.get("dv_ttm"))
        roe = fv(row.get("roe"))
        debt = fv(row.get("debt_to_assets"))
        return (
            f"该股票由价值投资策略筛选，满足低估值+高分红条件。\n"
            f"PE(TTM)={pe}, PB={pb}, 股息率(TTM)={dv}%, ROE={roe}%, 资产负债率={debt}%\n"
            f"请通过 ROE 水平代理评估护城河厚度；通过资产负债率结合 PB 评估是否存在高杠杆换取的虚假繁荣。\n"
            f"如果 ROE 方差较大但当前 PE 极低，请定性为'周期股'而非'价值股'；\n"
            f"如果不仅高股息且 PB<1，研判是否存在'价值陷阱'的财务特征（如经营现金流/净利润 < 1）。"
        )

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
    required_quality_tier = QualityTier.SILVER
    requires_fundamental_coverage = True
    required_context_keys: tuple[str, ...] = ("screening_data", "fundamental_screening_data")
    required_tables: tuple[str, ...] = ("daily_quotes", "daily_indicators")

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

    def get_ai_context(self, row: dict) -> str:
        fv = fmt_val
        or_yoy = fv(row.get("or_yoy"))
        np_yoy = fv(row.get("netprofit_yoy"))
        roe = fv(row.get("roe"))
        gpm = fv(row.get("grossprofit_margin"))
        pe = fv(row.get("pe_ttm"))
        return (
            f"该股票由高成长策略筛选，满足高增长+高盈利条件。\n"
            f"营收YOY={or_yoy}%, 净利润YOY={np_yoy}%, ROE={roe}%, 毛利率={gpm}%, PE(TTM)={pe}\n"
            f"请严格比对营收YOY和净利润YOY：如果利润增速远高于营收增速，且毛利率并未显著提升，\n"
            f"强烈质疑其非经常性损益或降本增效带来的不可持续性增长。\n"
            f"如果此时 PE 极高(>60)，提示右侧杀跌的戴维斯双杀风险。"
        )

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
    required_quality_tier = QualityTier.SILVER
    requires_fundamental_coverage = True
    required_context_keys: tuple[str, ...] = ("screening_data", "fundamental_screening_data")
    required_tables: tuple[str, ...] = ("daily_quotes", "daily_indicators")

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

    def get_ai_context(self, row: dict) -> str:
        fv = fmt_val
        dv = fv(row.get("dv_ttm"))
        pe = fv(row.get("pe_ttm"))
        or_yoy = fv(row.get("or_yoy"))
        roe = fv(row.get("roe"))
        return (
            f"该股票由高股息策略筛选，以持续高分红为核心筛选条件。\n"
            f"股息率(TTM)={dv}%, PE(TTM)={pe}, 营收YOY={or_yoy}%, ROE={roe}%\n"
            f"请判断分红的底气（自由现金流vs变卖资产）和分红的意愿（历史稳定性）。\n"
            f"严查'假高息'：如果当前超高股息率是由近一年股价暴跌被动造成的，\n"
            f"且最新财报显示 ROE 和营收增速均恶化，请直接判定为雷区并 reject。"
        )

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        dv_min = p.get("dv_min", 4)
        return lf.drop_nulls(subset=["dv_ttm"]).filter(pl.col("dv_ttm") > dv_min).sort("dv_ttm", descending=True)


@register_strategy("cashflow")
class CashFlowStrategy(PolarsBaseStrategy):
    required_quality_tier = QualityTier.SILVER
    requires_fundamental_coverage = True
    required_context_keys: tuple[str, ...] = ("screening_data", "fundamental_screening_data")
    required_tables: tuple[str, ...] = ("daily_quotes", "daily_indicators")

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

    def get_ai_context(self, row: dict) -> str:
        fv = fmt_val
        debt = fv(row.get("debt_to_assets"))
        roe = fv(row.get("roe"))
        return (
            f"该股票由现金流优质策略筛选，满足低负债+高盈利条件。\n"
            f"资产负债率={debt}%, ROE={roe}%\n"
            f"请评估资产负债表的铁甲程度、产业链话语权、抗寒冬能力，\n"
            f"以及是否存在资金链断裂隐患。一旦发现资金链存在断裂风险，请直接 reject。"
        )

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
    required_quality_tier = QualityTier.SILVER

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

    def get_ai_context(self, row: dict) -> str:
        fv = fmt_val
        total_mv = row.get("total_mv", 0)
        mv_yi = (
            round(total_mv / 10000, 1)
            if total_mv and not (isinstance(total_mv, (float, Decimal)) and total_mv != total_mv)
            else "N/A"
        )
        pe = fv(row.get("pe_ttm"))
        pb = fv(row.get("pb"))
        dv = fv(row.get("dv_ttm"))
        return (
            f"该股票由大盘低估策略筛选，满足大市值+低估值条件。\n"
            f"总市值={mv_yi}亿, PE(TTM)={pe}, PB={pb}, 股息率(TTM)={dv}%\n"
            f"请判断低估值的成因：是周期性底部（可逆）还是基本面永久恶化（不可逆）？\n"
            f"是否存在明确的估值修复催化剂？即使估值不修复，当前股息率能否提供足够安全垫？"
        )

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        p = context.get("params", {})
        cap_min = p.get("market_cap_min", 500) * 10000
        pe_max = p.get("pe_max", 15)
        return (
            lf.drop_nulls(subset=["total_mv", "pe_ttm"])
            .filter(pl.col("total_mv") > cap_min)
            .filter(pl.col("pe_ttm").is_between(0, pe_max))
            .sort("total_mv", descending=True)
        )

    def _sort_for_ai(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if "pe_ttm" in df.columns:
            return df.sort_values("pe_ttm", ascending=True)
        return df
