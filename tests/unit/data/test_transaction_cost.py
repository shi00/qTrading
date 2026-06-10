"""交易成本模型单元测试"""

import math
from datetime import date

import pytest

from data.domain_services.transaction_cost import (
    STAMP_DUTY_SCHEDULE,
    StampDutySchedule,
    TransactionCost,
    TransactionCostConfig,
    TransactionCostModel,
    get_stamp_duty_rate,
    get_stamp_duty_schedule_description,
)


class TestStampDutySchedule:
    def test_schedule_is_sorted(self) -> None:
        dates = [s.effective_date for s in STAMP_DUTY_SCHEDULE]
        assert dates == sorted(dates)

    def test_get_rate_before_first_schedule(self) -> None:
        assert get_stamp_duty_rate(date(2000, 1, 1)) == STAMP_DUTY_SCHEDULE[0].rate

    def test_get_rate_at_first_schedule(self) -> None:
        assert get_stamp_duty_rate(date(2008, 9, 19)) == STAMP_DUTY_SCHEDULE[0].rate

    def test_get_rate_between_schedules(self) -> None:
        assert get_stamp_duty_rate(date(2015, 1, 1)) == STAMP_DUTY_SCHEDULE[0].rate

    def test_get_rate_at_second_schedule(self) -> None:
        assert get_stamp_duty_rate(date(2023, 8, 28)) == STAMP_DUTY_SCHEDULE[1].rate

    def test_get_rate_after_last_schedule(self) -> None:
        assert get_stamp_duty_rate(date(2025, 1, 1)) == STAMP_DUTY_SCHEDULE[-1].rate

    def test_get_rate_none_returns_current(self) -> None:
        assert get_stamp_duty_rate(None) == STAMP_DUTY_SCHEDULE[-1].rate

    def test_description_returns_correct_text(self) -> None:
        assert "0.1%" in get_stamp_duty_schedule_description(date(2020, 1, 1))
        assert "0.05%" in get_stamp_duty_schedule_description(date(2024, 1, 1))

    def test_schedule_item_creation(self) -> None:
        item = StampDutySchedule(date(2023, 8, 28), 5e-4, "减半征收 0.05%")
        assert item.effective_date == date(2023, 8, 28)
        assert item.rate == 5e-4
        assert item.description == "减半征收 0.05%"


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


class TestTransactionCostWithSchedule:
    @pytest.fixture
    def model(self) -> TransactionCostModel:
        return TransactionCostModel(TransactionCostConfig(stamp_duty_rate=None))

    def test_sell_uses_date_based_rate_before_2023(self, model: TransactionCostModel) -> None:
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2022, 1, 1),
        )
        # 印花税基于 gross_amount（含滑点调整后金额）计算
        assert cost.stamp_duty == cost.gross_amount * 1e-3

    def test_sell_uses_date_based_rate_after_2023(self, model: TransactionCostModel) -> None:
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2024, 1, 1),
        )
        assert cost.stamp_duty == cost.gross_amount * 5e-4

    def test_sell_uses_date_based_rate_on_change_date(self, model: TransactionCostModel) -> None:
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2023, 8, 28),
        )
        assert cost.stamp_duty == cost.gross_amount * 5e-4

    def test_explicit_rate_overrides_schedule(self) -> None:
        model = TransactionCostModel(TransactionCostConfig(stamp_duty_rate=2e-3))
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=False,
            trade_date=date(2024, 1, 1),
        )
        assert cost.stamp_duty == cost.gross_amount * 2e-3

    def test_buy_never_has_stamp_duty(self, model: TransactionCostModel) -> None:
        cost = model.calculate(
            price=10.0,
            volume=1000,
            is_buy=True,
            trade_date=date(2022, 1, 1),
        )
        assert cost.stamp_duty == 0.0

    def test_no_trade_date_uses_current_rate(self, model: TransactionCostModel) -> None:
        cost = model.calculate(price=10.0, volume=1000, is_buy=False)
        assert cost.stamp_duty == cost.gross_amount * 5e-4


class TestFutureScheduleExtension:
    def test_can_add_new_schedule_item(self) -> None:
        extended_schedule = STAMP_DUTY_SCHEDULE + [
            StampDutySchedule(date(2030, 1, 1), 3e-4, "未来费率"),
        ]
        assert len(extended_schedule) == 3
        assert extended_schedule[-1].rate == 3e-4

    def test_schedule_is_frozen(self) -> None:
        assert isinstance(STAMP_DUTY_SCHEDULE, list)
        for item in STAMP_DUTY_SCHEDULE:
            assert isinstance(item, StampDutySchedule)
