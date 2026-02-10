from strategies.ai_strategy import AISelectionStrategy
from strategies.oversold_strategy import OversoldStrategy
from strategies.fundamental import (
    ValueStrategy, 
    GrowthStrategy, 
    DividendStrategy, 
    CashFlowStrategy, 
    LargePEStrategy
)
from strategies.market import (
    TechnicalBreakoutStrategy, 
    NorthboundStrategy, 
    InstitutionalStrategy, 
    BlockTradeStrategy
)

class StrategyManager:
    def __init__(self):
        self.strategies = {
            "ai_active": AISelectionStrategy(),
            "oversold": OversoldStrategy(),
            "value": ValueStrategy(),
            "growth": GrowthStrategy(),
            "dividend": DividendStrategy(),
            "tech_breakout": TechnicalBreakoutStrategy(),
            "northbound": NorthboundStrategy(),
            "institutional": InstitutionalStrategy(),
            "block_trade": BlockTradeStrategy(),
            "cashflow": CashFlowStrategy(),
            "large_pe": LargePEStrategy(),
        }

    def get_strategy(self, key):
        return self.strategies.get(key)
    
    def get_all_names(self):
        return {k: v.name for k, v in self.strategies.items()}

