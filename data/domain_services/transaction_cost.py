"""A股交易成本模型

供回测引擎与 ReviewManager 共用，确保费率计算一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TransactionCostConfig:
    """交易成本配置（独立于 BacktestConfig，避免分层依赖）"""

    commission_rate: float = 3e-4
    commission_min: float = 5.0
    stamp_duty_rate: float = 1e-3
    stamp_duty_buy: bool = False
    transfer_fee_rate: float = 1e-5

    slippage_model: Literal["fixed_bps", "volume_ratio", "sqrt_volume"] = "fixed_bps"
    slippage_bps: float = 5.0


@dataclass
class TransactionCost:
    """单笔交易成本"""

    gross_amount: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    net_amount: float

    @property
    def total_cost(self) -> float:
        """所有成本均为正数，买入加到现金支出，卖出从现金收入扣除。"""
        return self.commission + self.stamp_duty + self.transfer_fee + self.slippage_cost

    @property
    def cost_bps(self) -> float:
        if self.gross_amount == 0:
            return 0.0
        return (self.total_cost / abs(self.gross_amount)) * 10000


class TransactionCostModel:
    """A股交易成本模型"""

    def __init__(self, config: TransactionCostConfig):
        self.config = config

    def calculate(
        self,
        price: float,
        volume: int,
        is_buy: bool,
        avg_daily_volume: float | None = None,
    ) -> TransactionCost:
        gross_amount = price * volume

        commission = max(gross_amount * self.config.commission_rate, self.config.commission_min)

        stamp_duty = 0.0
        if not is_buy or self.config.stamp_duty_buy:
            stamp_duty = gross_amount * self.config.stamp_duty_rate

        transfer_fee = gross_amount * self.config.transfer_fee_rate

        slippage_cost = self._calc_slippage(price, volume, is_buy, avg_daily_volume)

        if is_buy:
            net_amount = gross_amount + commission + transfer_fee + slippage_cost
        else:
            net_amount = gross_amount - commission - stamp_duty - transfer_fee - slippage_cost

        return TransactionCost(
            gross_amount=gross_amount,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            slippage_cost=slippage_cost,
            net_amount=net_amount,
        )

    def _calc_slippage(
        self,
        price: float,
        volume: int,
        is_buy: bool,
        avg_daily_volume: float | None,
    ) -> float:
        if self.config.slippage_model == "fixed_bps":
            slippage_pct = self.config.slippage_bps / 10000
        elif self.config.slippage_model == "volume_ratio" and avg_daily_volume:
            participation = volume / avg_daily_volume
            slippage_pct = self.config.slippage_bps / 10000 * (1 + participation * 10)
        elif self.config.slippage_model == "sqrt_volume" and avg_daily_volume:
            participation = volume / avg_daily_volume
            slippage_pct = self.config.slippage_bps / 10000 * math.sqrt(participation * 100)
        else:
            slippage_pct = self.config.slippage_bps / 10000

        return abs(price * volume * slippage_pct)
