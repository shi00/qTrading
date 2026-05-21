"""交易成本模型单元测试"""

import math

import pytest

from data.domain_services.transaction_cost import (
    TransactionCost,
    TransactionCostConfig,
    TransactionCostModel,
)


class TestTransactionCost:
    def test_total_cost_property(self) -> None:
        cost = TransactionCost(
            gross_amount=10000.0,
            commission=5.0,
            stamp_duty=10.0,
            transfer_fee=1.0,
            slippage_cost=5.0,
            net_amount=9979.0,
        )
        assert cost.total_cost == 21.0

    def test_cost_bps_calculation(self) -> None:
        cost = TransactionCost(
            gross_amount=10000.0,
            commission=5.0,
            stamp_duty=10.0,
            transfer_fee=1.0,
            slippage_cost=5.0,
            net_amount=9979.0,
        )
        assert cost.cost_bps == pytest.approx(21.0, rel=0.01)

    def test_cost_bps_zero_gross(self) -> None:
        cost = TransactionCost(
            gross_amount=0.0,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            net_amount=0.0,
        )
        assert cost.cost_bps == 0.0


class TestTransactionCostModel:
    @pytest.fixture
    def config(self) -> TransactionCostConfig:
        return TransactionCostConfig(
            commission_rate=3e-4,
            commission_min=5.0,
            stamp_duty_rate=1e-3,
            transfer_fee_rate=1e-5,
            slippage_model="fixed_bps",
            slippage_bps=5.0,
        )

    def test_buy_commission_with_min(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=100, is_buy=True)
        assert cost.commission == 5.0
        assert cost.stamp_duty == 0.0
        assert cost.net_amount > 1000.0

    def test_buy_commission_without_min(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=10000, is_buy=True)
        expected_commission = 100000.0 * 3e-4
        assert cost.commission == pytest.approx(expected_commission, rel=0.01)

    def test_sell_stamp_duty(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        expected_stamp_duty = 10000.0 * 1e-3
        assert cost.stamp_duty == pytest.approx(expected_stamp_duty, rel=0.01)
        assert cost.net_amount < 10000.0

    def test_sell_no_stamp_duty_on_buy(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        assert cost.stamp_duty == 0.0

    def test_transfer_fee_on_both_sides(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        buy_cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        sell_cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        expected_transfer_fee = 10000.0 * 1e-5
        assert buy_cost.transfer_fee == pytest.approx(expected_transfer_fee, rel=0.01)
        assert sell_cost.transfer_fee == pytest.approx(expected_transfer_fee, rel=0.01)

    def test_fixed_bps_slippage(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        expected_slippage = 10000.0 * 5.0 / 10000
        assert cost.slippage_cost == pytest.approx(expected_slippage, rel=0.01)

    def test_volume_ratio_slippage(self, config: TransactionCostConfig) -> None:
        config = TransactionCostConfig(
            slippage_model="volume_ratio",
            slippage_bps=5.0,
        )
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=True,
            avg_daily_volume=10000.0,
        )
        participation = 1000.0 / 10000.0
        expected_slippage = 10000.0 * 5.0 / 10000 * (1 + participation * 10)
        assert cost.slippage_cost == pytest.approx(expected_slippage, rel=0.01)

    def test_sqrt_volume_slippage(self, config: TransactionCostConfig) -> None:
        config = TransactionCostConfig(
            slippage_model="sqrt_volume",
            slippage_bps=5.0,
        )
        model = TransactionCostModel(config)
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=True,
            avg_daily_volume=10000.0,
        )
        participation = 1000.0 / 10000.0
        expected_slippage = 10000.0 * 5.0 / 10000 * math.sqrt(participation * 100)
        assert cost.slippage_cost == pytest.approx(expected_slippage, rel=0.01)

    def test_net_amount_buy(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=True)
        gross = 10000.0
        expected_net = gross + cost.commission + cost.transfer_fee + cost.slippage_cost
        assert cost.net_amount == pytest.approx(expected_net, rel=0.01)

    def test_net_amount_sell(self, config: TransactionCostConfig) -> None:
        model = TransactionCostModel(config)
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        gross = 10000.0
        expected_net = gross - cost.commission - cost.stamp_duty - cost.transfer_fee - cost.slippage_cost
        assert cost.net_amount == pytest.approx(expected_net, rel=0.01)
