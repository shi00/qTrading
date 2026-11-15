"""交易成本模型单元测试"""

from datetime import date

import pytest

from data.domain_services.transaction_cost import (
    STAMP_DUTY_SCHEDULE,
    TransactionCost,
    TransactionCostConfig,
    TransactionCostModel,
    get_stamp_duty_rate,
)

pytestmark = pytest.mark.unit


class TestTransactionCost:
    def test_buy_commission_with_min(self):
        config = TransactionCostConfig(commission_rate=3e-4, commission_min=5.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=100, is_buy=True)
        assert cost.commission == 5.0
        assert cost.stamp_duty == 0.0
        assert cost.transfer_fee > 0
        assert cost.slippage_cost > 0
        assert cost.net_amount > cost.gross_amount

    def test_buy_commission_without_min(self):
        config = TransactionCostConfig(commission_rate=3e-4, commission_min=5.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=10000, is_buy=True)
        # 滑点调整后 gross_amount 包含滑点溢价，commission 基于 adjusted gross_amount
        expected_gross = 10.0 * 10000 * (1 + config.slippage_bps / 10000)
        assert cost.commission == pytest.approx(expected_gross * 3e-4)
        assert cost.stamp_duty == 0.0

    def test_sell_stamp_duty(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert cost.stamp_duty == pytest.approx(10.0 * 1000 * 1e-3)
        assert cost.net_amount < cost.gross_amount

    def test_buy_no_stamp_duty(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3, stamp_duty_buy=False)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.stamp_duty == 0.0

    def test_stamp_duty_buy_flag(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3, stamp_duty_buy=True, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.stamp_duty == pytest.approx(10.0 * 1000 * 1e-3)

    def test_slippage_always_positive(self):
        config = TransactionCostConfig(slippage_model="fixed_bps", slippage_bps=5.0)
        model = TransactionCostModel(config)
        buy_cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        sell_cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert buy_cost.slippage_cost > 0
        assert sell_cost.slippage_cost > 0
        # slippage_cost 基于原始价格计算，买入卖出相同
        assert buy_cost.slippage_cost == sell_cost.slippage_cost
        # 但成交价不同：买入上浮、卖出下浮
        assert buy_cost.slippage_adjusted_price > sell_cost.slippage_adjusted_price

    def test_total_cost_property(self):
        config = TransactionCostConfig()
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert cost.total_cost == cost.commission + cost.stamp_duty + cost.transfer_fee + cost.slippage_cost

    def test_cost_bps_property(self):
        config = TransactionCostConfig(slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        if cost.gross_amount > 0:
            assert cost.cost_bps == cost.total_cost / cost.gross_amount * 10000

    def test_cost_bps_zero_gross(self):
        # D12-004: 零价/零量已由 calculate() 显式拒绝（非法成交），
        # cost_bps 的零除保护改为直接对零成交金额构造体验证。
        cost = TransactionCost(
            gross_amount=0.0,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            net_amount=0.0,
        )
        assert cost.cost_bps == 0.0

    def test_volume_ratio_slippage(self):
        config = TransactionCostConfig(slippage_model="volume_ratio", slippage_bps=5.0)
        model = TransactionCostModel(config)
        low_participation = model.calculate(price=10.0, volume=100, is_buy=True, avg_daily_volume=100000)
        high_participation = model.calculate(price=10.0, volume=10000, is_buy=True, avg_daily_volume=100000)
        assert high_participation.slippage_cost > low_participation.slippage_cost

    def test_sqrt_volume_slippage(self):
        config = TransactionCostConfig(slippage_model="sqrt_volume", slippage_bps=5.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True, avg_daily_volume=100000)
        assert cost.slippage_cost > 0

    def test_slippage_fallback_to_fixed(self):
        config = TransactionCostConfig(slippage_model="volume_ratio", slippage_bps=5.0)
        model = TransactionCostModel(config)
        cost_no_vol = model.calculate(price=10.0, volume=1000, is_buy=True, avg_daily_volume=None)
        fixed_config = TransactionCostConfig(slippage_model="fixed_bps", slippage_bps=5.0)
        fixed_model = TransactionCostModel(fixed_config)
        cost_fixed = fixed_model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost_no_vol.slippage_cost == cost_fixed.slippage_cost

    def test_transfer_fee(self):
        config = TransactionCostConfig(transfer_fee_rate=1e-5, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.transfer_fee == pytest.approx(10.0 * 1000 * 1e-5)

    def test_sell_net_amount_deduction(self):
        config = TransactionCostConfig(
            commission_rate=3e-4,
            commission_min=0.0,
            stamp_duty_rate=1e-3,
            transfer_fee_rate=1e-5,
            slippage_model="fixed_bps",
            slippage_bps=0.0,
        )
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        gross = 10.0 * 1000
        expected_net = gross - cost.commission - cost.stamp_duty - cost.transfer_fee
        assert cost.net_amount == pytest.approx(expected_net)


class TestStampDutyScheduleIntegration:
    def test_stamp_duty_rate_before_2023_change(self):
        config = TransactionCostConfig(stamp_duty_rate=None, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2022, 1, 1),
        )
        # 2022 年为 2008-09-19 单边档位，税率 0.1%
        assert cost.stamp_duty == pytest.approx(10000.0 * 1e-3)

    def test_stamp_duty_rate_after_2023_change(self):
        config = TransactionCostConfig(stamp_duty_rate=None, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2024, 1, 1),
        )
        expected_rate = STAMP_DUTY_SCHEDULE[-1].rate
        assert cost.stamp_duty == pytest.approx(10000.0 * expected_rate)

    def test_stamp_duty_rate_on_change_date(self):
        config = TransactionCostConfig(stamp_duty_rate=None, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2023, 8, 28),
        )
        expected_rate = STAMP_DUTY_SCHEDULE[-1].rate
        assert cost.stamp_duty == pytest.approx(10000.0 * expected_rate)

    def test_explicit_rate_overrides_schedule(self):
        config = TransactionCostConfig(stamp_duty_rate=2e-3, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2024, 1, 1),
        )
        assert cost.stamp_duty == pytest.approx(10.0 * 1000 * 2e-3)

    def test_no_trade_date_uses_current_rate(self):
        config = TransactionCostConfig(stamp_duty_rate=None, slippage_bps=0.0)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        expected_rate = get_stamp_duty_rate(None)
        assert cost.stamp_duty == pytest.approx(10000.0 * expected_rate)

    def test_buy_stamp_duty_zero_in_single_side_periods(self):
        config = TransactionCostConfig(stamp_duty_rate=None)
        model = TransactionCostModel(config)
        cost_before = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=True,
            trade_date=date(2022, 1, 1),
        )
        cost_after = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=True,
            trade_date=date(2024, 1, 1),
        )
        assert cost_before.stamp_duty == 0.0
        assert cost_after.stamp_duty == 0.0


class TestInvalidTradeInputsRejected:
    """D12-004: 非法价格/成交量/滑点必须显式失败，不得返回可入账的负数成交金额。"""

    @pytest.mark.parametrize("bad_price", [0.0, -1.0, -1e-9, float("nan"), float("inf"), float("-inf")])
    def test_non_positive_or_non_finite_price_rejected(self, bad_price: float) -> None:
        model = TransactionCostModel(TransactionCostConfig())
        with pytest.raises(ValueError, match="price must be finite and positive"):
            model.calculate(price=bad_price, volume=1000, is_buy=False)

    @pytest.mark.parametrize("bad_volume", [0.0, -100.0, float("nan"), float("inf")])
    def test_non_positive_or_non_finite_volume_rejected(self, bad_volume: float) -> None:
        model = TransactionCostModel(TransactionCostConfig())
        with pytest.raises(ValueError, match="volume must be finite and positive"):
            model.calculate(price=10.0, volume=bad_volume, is_buy=True)

    def test_sell_with_100pct_slippage_rejected_not_booked(self) -> None:
        """10000bps 卖出调整价恰为 0：必须显式失败（调用方跳过），不得按 0 价入账。"""
        model = TransactionCostModel(TransactionCostConfig(slippage_bps=10_000.0))
        with pytest.raises(ValueError, match="invalid execution price"):
            model.calculate(price=10.0, volume=1000, is_buy=False)

    def test_sell_above_slippage_ceiling_rejected(self) -> None:
        model = TransactionCostModel(TransactionCostConfig(slippage_bps=10_001.0))
        with pytest.raises(ValueError, match="slippage_bps must be in"):
            model.calculate(price=10.0, volume=1000, is_buy=False)

    @pytest.mark.parametrize("bad_bps", [-1.0, float("nan"), float("inf")])
    def test_non_finite_or_negative_slippage_config_rejected(self, bad_bps: float) -> None:
        model = TransactionCostModel(TransactionCostConfig(slippage_bps=bad_bps))
        with pytest.raises(ValueError, match="slippage_bps must be in"):
            model.calculate(price=10.0, volume=1000, is_buy=True)

    def test_buy_with_100pct_slippage_still_valid(self) -> None:
        """买入方向 100% 滑点成交价为 2×price（仍为正），不得被卖出护栏误伤。"""
        model = TransactionCostModel(TransactionCostConfig(slippage_bps=10_000.0, commission_min=0.0))
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.slippage_adjusted_price == pytest.approx(20.0)
        assert cost.gross_amount == pytest.approx(20_000.0)
        assert cost.net_amount > cost.gross_amount

    def test_volume_ratio_participation_above_100pct_rejected(self) -> None:
        """配置内滑点（5bps）经参与率放大后仍可能 >100%，卖出价非正必须拒绝。"""
        model = TransactionCostModel(TransactionCostConfig(slippage_model="volume_ratio", slippage_bps=5.0))
        with pytest.raises(ValueError, match="invalid execution price"):
            model.calculate(price=10.0, volume=1_000_000, is_buy=False, avg_daily_volume=100.0)

    def test_non_positive_avg_daily_volume_falls_back_to_fixed(self) -> None:
        """avg_daily_volume 为 0/NaN/负时退回固定滑点，不产生负滑点或除零。"""
        for avg in (0.0, -100.0, float("nan")):
            model = TransactionCostModel(TransactionCostConfig(slippage_model="sqrt_volume", slippage_bps=5.0))
            cost = model.calculate(price=10.0, volume=1000, is_buy=False, avg_daily_volume=avg)
            assert cost.slippage_adjusted_price == pytest.approx(10.0 * (1 - 5 / 10000))
