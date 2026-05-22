"""回测框架模块"""

from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.metrics import BacktestMetrics

__all__ = [
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestStrategyAdapter",
    "VectorBacktestEngine",
]
