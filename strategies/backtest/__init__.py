"""回测框架模块"""

from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.backtest.config import BacktestConfig, BacktestResult

__all__ = ["BacktestConfig", "BacktestResult", "BacktestStrategyAdapter"]
