"""回测配置数据类"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import polars as pl

from data.domain_services.transaction_cost import TransactionCostConfig


@dataclass(frozen=True)
class BacktestConfig:
    """回测配置（不可变，确保回测可复现）"""

    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0

    commission_rate: float = 3e-4
    commission_min: float = 5.0
    stamp_duty_rate: float = 1e-3
    stamp_duty_buy: bool = False
    transfer_fee_rate: float = 1e-5

    slippage_model: Literal["fixed_bps", "volume_ratio", "sqrt_volume"] = "fixed_bps"
    slippage_bps: float = 5.0

    rebalance_freq: Literal["daily", "weekly", "monthly", "signal"] = "signal"
    position_sizing: Literal["equal_weight", "market_cap_weight", "risk_parity"] = "equal_weight"
    max_position_count: int = 50
    max_single_weight: float = 0.1

    benchmark_code: str = "000300.SH"
    risk_free_rate: float = 0.02
    fail_fast: bool = True
    disable_ai: bool = True
    persist_artifacts: bool = False
    price_adjustment: Literal["qfq"] = "qfq"
    execution_price: Literal["next_open", "next_close"] = "next_open"
    allow_limit_up_buy: bool = False
    allow_limit_down_sell: bool = False

    def validate(self) -> list[str]:
        errors = []
        if self.start_date >= self.end_date:
            errors.append("start_date must be before end_date")
        if self.initial_capital <= 0:
            errors.append("initial_capital must be positive")
        if self.commission_rate < 0 or self.commission_rate > 0.01:
            errors.append("commission_rate should be between 0 and 1%")
        if self.max_single_weight <= 0 or self.max_single_weight > 1:
            errors.append("max_single_weight must be in (0, 1]")
        if self.max_position_count <= 0:
            errors.append("max_position_count must be positive")
        return errors

    def get_cost_config(self) -> TransactionCostConfig:
        """获取交易成本配置。"""
        return TransactionCostConfig(
            commission_rate=self.commission_rate,
            commission_min=self.commission_min,
            stamp_duty_rate=self.stamp_duty_rate,
            stamp_duty_buy=self.stamp_duty_buy,
            transfer_fee_rate=self.transfer_fee_rate,
            slippage_model=self.slippage_model,
            slippage_bps=self.slippage_bps,
        )


@dataclass
class BacktestResult:
    """回测结果"""

    config: BacktestConfig
    strategy_name: str
    params_snapshot: dict

    nav_curve: pl.DataFrame
    daily_returns: pl.Series
    benchmark_returns: pl.Series

    trades: pl.DataFrame
    positions: pl.DataFrame
    skipped_orders: pl.DataFrame

    metrics: dict[str, float]
    ic_series: pl.Series
    period_stats: pl.DataFrame
    data_warnings: list[str]
    failed_signal_dates: list[dict]

    run_id: str
    executed_at: datetime
    duration_ms: int
