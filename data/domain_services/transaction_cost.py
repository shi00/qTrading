"""A股交易成本模型

供回测引擎与 ReviewManager 共用，确保费率计算一致。

印花税政策时间线：
- 2008-09-19：单边征收（仅卖出），税率 0.1%
- 2023-08-28：减半征收，税率 0.05%

未来费率变更时，只需在 STAMP_DUTY_SCHEDULE 中追加新档位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class StampDutySchedule:
    """印花税率档位"""

    effective_date: date
    rate: float
    description: str = ""


STAMP_DUTY_SCHEDULE: list[StampDutySchedule] = [
    StampDutySchedule(date(2008, 9, 19), 1e-3, "单边征收 0.1%"),
    StampDutySchedule(date(2023, 8, 28), 5e-4, "减半征收 0.05%"),
]


def get_stamp_duty_rate(trade_date: date | None = None) -> float:
    """根据交易日期返回印花税率。

    Args:
        trade_date: 交易日期。None 时返回当前最新费率。

    Returns:
        对应日期的印花税率。
    """
    if trade_date is None:
        return STAMP_DUTY_SCHEDULE[-1].rate

    for schedule in reversed(STAMP_DUTY_SCHEDULE):
        if trade_date >= schedule.effective_date:
            return schedule.rate

    return STAMP_DUTY_SCHEDULE[0].rate


def get_stamp_duty_schedule_description(trade_date: date | None = None) -> str:
    """获取印花税率档位描述。"""
    if trade_date is None:
        return STAMP_DUTY_SCHEDULE[-1].description

    for schedule in reversed(STAMP_DUTY_SCHEDULE):
        if trade_date >= schedule.effective_date:
            return schedule.description

    return STAMP_DUTY_SCHEDULE[0].description


@dataclass(frozen=True)
class TransactionCostConfig:
    """交易成本配置（独立于 BacktestConfig，避免分层依赖）"""

    commission_rate: float = 3e-4
    commission_min: float = 5.0
    stamp_duty_rate: float | None = None
    stamp_duty_buy: bool = False
    transfer_fee_rate: float = 1e-5

    slippage_model: Literal["fixed_bps", "volume_ratio", "sqrt_volume"] = "fixed_bps"
    slippage_bps: float = 5.0


@dataclass
class TransactionCost:
    """单笔交易成本

    字段语义：
    - gross_amount: 成交金额（含滑点调整），= slippage_adjusted_price * volume
    - commission / stamp_duty / transfer_fee: 法定费用，基于 gross_amount 计算
    - slippage_cost: 滑点价差 = adjusted_price * volume - price * volume
      表示因滑点导致的成交金额偏离原始价格的差额
    - net_amount: 实际现金流（买入为支出，卖出为收入）
      滑点影响已通过 gross_amount 体现，net_amount 不再额外加减 slippage_cost
    - total_cost: 所有成本之和（commission + stamp_duty + transfer_fee + slippage_cost）
      注意：total_cost ≠ net_amount - price*volume，因为 slippage_cost 已含在 gross_amount 中
    """

    gross_amount: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    net_amount: float
    slippage_adjusted_price: float = 0.0

    @property
    def total_cost(self) -> float:
        """所有成本均为正数，买入加到现金支出，卖出从现金收入扣除。

        注意：slippage_cost 是滑点价差（adjusted - raw），已含在 gross_amount 中。
        total_cost 仅用于成本分析，不应与 net_amount 叠加计算现金流。
        """
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
        trade_date: date | None = None,
    ) -> TransactionCost:
        slippage_pct = self._calc_slippage_pct(price, volume, is_buy, avg_daily_volume)

        # 滑点调整成交价：买入上浮、卖出下浮
        if is_buy:
            adjusted_price = price * (1 + slippage_pct)
        else:
            adjusted_price = price * (1 - slippage_pct)

        gross_amount = adjusted_price * volume

        commission = max(gross_amount * self.config.commission_rate, self.config.commission_min)

        stamp_duty = 0.0
        if not is_buy or self.config.stamp_duty_buy:
            effective_rate = self._get_effective_stamp_duty_rate(trade_date)
            stamp_duty = gross_amount * effective_rate

        transfer_fee = gross_amount * self.config.transfer_fee_rate

        # slippage_cost = 滑点价差 = 调整后成交金额 - 原始成交金额
        # 买入时为正（多付），卖出时为正（少收），始终 >= 0
        # 此值已含在 gross_amount 中，不应再与 net_amount 叠加
        slippage_cost = abs(gross_amount - price * volume)

        if is_buy:
            net_amount = gross_amount + commission + transfer_fee
        else:
            net_amount = gross_amount - commission - stamp_duty - transfer_fee

        return TransactionCost(
            gross_amount=gross_amount,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            slippage_cost=slippage_cost,
            net_amount=net_amount,
            slippage_adjusted_price=adjusted_price,
        )

    def _get_effective_stamp_duty_rate(self, trade_date: date | None) -> float:
        """获取有效的印花税率。"""
        if self.config.stamp_duty_rate is not None:
            return self.config.stamp_duty_rate
        return get_stamp_duty_rate(trade_date)

    def _calc_slippage_pct(
        self,
        price: float,
        volume: int,
        is_buy: bool,
        avg_daily_volume: float | None,
    ) -> float:
        """计算滑点百分比（小数形式，如 0.0005 表示 5bps）。"""
        if self.config.slippage_model == "fixed_bps":
            return self.config.slippage_bps / 10000
        elif self.config.slippage_model == "volume_ratio" and avg_daily_volume:
            participation = volume / avg_daily_volume
            return self.config.slippage_bps / 10000 * (1 + participation * 10)
        elif self.config.slippage_model == "sqrt_volume" and avg_daily_volume:
            participation = volume / avg_daily_volume
            return self.config.slippage_bps / 10000 * math.sqrt(participation * 100)
        return self.config.slippage_bps / 10000

    def _calc_slippage(
        self,
        price: float,
        volume: int,
        is_buy: bool,
        avg_daily_volume: float | None,
    ) -> float:
        """计算滑点费用金额（向后兼容）。"""
        return abs(price * volume * self._calc_slippage_pct(price, volume, is_buy, avg_daily_volume))
