"""回测配置数据类"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import polars as pl

from data.domain_services.transaction_cost import TransactionCostConfig


@dataclass(frozen=True)
class DataWarning:
    """回测数据警告（结构化）

    用于记录 enrichment 失败时的详细信息，便于用户理解数据质量问题。
    """

    warning_type: Literal["suspend_enrich_failed", "limit_enrich_failed"]
    start_date: str
    end_date: str
    affected_stock_count: int
    error_message: str

    def __str__(self) -> str:
        return (
            f"[{self.warning_type}] {self.start_date}-{self.end_date}: "
            f"{self.affected_stock_count} stocks affected. {self.error_message}"
        )


@dataclass(frozen=True)
class BacktestConfig:
    """回测配置（不可变，确保回测可复现）"""

    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0

    commission_rate: float = 3e-4
    commission_min: float = 5.0
    stamp_duty_rate: float | None = None  # None=自动按政策分段，显式值覆盖
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
    cash_reserve_pct: float = 0.1
    preload_max_days: int = 366

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
        if self.cash_reserve_pct < 0 or self.cash_reserve_pct >= 1:
            errors.append("cash_reserve_pct must be in [0, 1)")
        if self.max_position_count <= 0:
            errors.append("max_position_count must be positive")
        if self.preload_max_days < 30:
            errors.append("preload_max_days must be at least 30")
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


@dataclass(frozen=True)
class BacktestResult:
    """回测结果（不可变，确保回测可复现）"""

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
    data_warnings: tuple[str, ...]
    failed_signal_dates: tuple[dict, ...]

    run_id: str
    executed_at: datetime
    duration_ms: int

    def with_warnings(self, warnings: list[str] | tuple[str, ...]) -> BacktestResult:
        warnings_tuple = tuple(warnings) if isinstance(warnings, list) else warnings
        return BacktestResult(
            config=self.config,
            strategy_name=self.strategy_name,
            params_snapshot=self.params_snapshot,
            nav_curve=self.nav_curve,
            daily_returns=self.daily_returns,
            benchmark_returns=self.benchmark_returns,
            trades=self.trades,
            positions=self.positions,
            skipped_orders=self.skipped_orders,
            metrics=self.metrics,
            ic_series=self.ic_series,
            period_stats=self.period_stats,
            data_warnings=warnings_tuple,
            failed_signal_dates=self.failed_signal_dates,
            run_id=self.run_id,
            executed_at=self.executed_at,
            duration_ms=self.duration_ms,
        )

    def to_persist_dict(self) -> dict:
        """生成持久化所需的字典（不含 app_version，由调用方补充）。

        将 BacktestResult 与 BacktestConfig 中需要落库的字段平铺为单层 dict，
        供 BacktestService._persist_result 调用，避免在服务层散落字段映射逻辑。
        """
        return {
            "run_id": self.run_id,
            "strategy_name": self.strategy_name,
            "params_snapshot": self.params_snapshot,
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "initial_capital": self.config.initial_capital,
            "metrics": self.metrics,
            "nav_curve": self.nav_curve,
            "trades": self.trades,
            "period_stats": self.period_stats,
            "duration_ms": self.duration_ms,
            "execution_price": self.config.execution_price,
            "allow_limit_up_buy": self.config.allow_limit_up_buy,
            "allow_limit_down_sell": self.config.allow_limit_down_sell,
            "slippage_model": self.config.slippage_model,
        }
