"""交易成本模型单元测试"""

from datetime import date

import pytest

from data.domain_services.transaction_cost import (
    STAMP_DUTY_SCHEDULE,
    TransactionCostConfig,
    TransactionCostModel,
    get_stamp_duty_rate,
)


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
        assert cost.commission == pytest.approx(30.0)
        assert cost.stamp_duty == 0.0

    def test_sell_stamp_duty(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert cost.stamp_duty == 10.0
        assert cost.net_amount < cost.gross_amount

    def test_buy_no_stamp_duty(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3, stamp_duty_buy=False)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.stamp_duty == 0.0

    def test_stamp_duty_buy_flag(self):
        config = TransactionCostConfig(stamp_duty_rate=1e-3, stamp_duty_buy=True)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.stamp_duty == 10.0

    def test_slippage_always_positive(self):
        config = TransactionCostConfig(slippage_model="fixed_bps", slippage_bps=5.0)
        model = TransactionCostModel(config)
        buy_cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        sell_cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert buy_cost.slippage_cost > 0
        assert sell_cost.slippage_cost > 0
        assert buy_cost.slippage_cost == sell_cost.slippage_cost

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
        config = TransactionCostConfig()
        model = TransactionCostModel(config)
        cost = model.calculate(price=0.0, volume=0, is_buy=True)
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
        config = TransactionCostConfig(transfer_fee_rate=1e-5)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.transfer_fee == 10.0 * 1000 * 1e-5

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
        assert cost.net_amount == expected_net


class TestStampDutyScheduleIntegration:
    def test_stamp_duty_rate_before_2023_change(self):
        config = TransactionCostConfig(stamp_duty_rate=None)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2022, 1, 1),
        )
        expected_rate = STAMP_DUTY_SCHEDULE[0].rate
        assert cost.stamp_duty == pytest.approx(10000.0 * expected_rate)

    def test_stamp_duty_rate_after_2023_change(self):
        config = TransactionCostConfig(stamp_duty_rate=None)
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
        config = TransactionCostConfig(stamp_duty_rate=None)
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
        config = TransactionCostConfig(stamp_duty_rate=2e-3)
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2024, 1, 1),
        )
        assert cost.stamp_duty == 20.0

    def test_no_trade_date_uses_current_rate(self):
        config = TransactionCostConfig(stamp_duty_rate=None)
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        expected_rate = get_stamp_duty_rate(None)
        assert cost.stamp_duty == pytest.approx(10000.0 * expected_rate)

    def test_buy_stamp_duty_zero_regardless_of_date(self):
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
