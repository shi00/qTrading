"""F17 连接计价（connection_pricing）单测：冻结快照解析 + Decimal 成本计算。

覆盖（F17 回归清单）：
- 收费：单价齐全 + 真实 usage → Decimal 分量计价（uncached / 缓存 / 输出）；
- 免费：价格事实为 0 → 已知零 "0.0000"（非未知）；
- 未知：usage_only / billing 缺失 / 单价或用量缺失 → None（R21 禁止 0 伪装）；
- 缓存 token：报告而单价缺失 → 整体未知；未报告（None）→ 分量跳过
  （litellm_client 归一化语义：uncached 已覆盖全部输入）；
- encoder 计价单位：单价 × encoder_input_tokens；token 未知 → None；
- 汇率与舍入：fx_to_cny 乘数；quantize 4 位 ROUND_HALF_UP；
- 快照解析：billing 缺失 / 非 mapping / 畸形金额降级 + metadata 往返保精度。
"""

import logging
from decimal import Decimal

import pytest

from core.model_management_types import NormalizedLLMUsage
from services.ai_service.connection_pricing import (
    ConnectionPricing,
    compute_cost_cny,
    parse_connection_pricing,
    pricing_from_metadata,
    pricing_snapshot_metadata,
)

pytestmark = pytest.mark.unit


def _billing(**overrides) -> dict:
    base = {
        "mode": "estimate_cost",
        "pricing_unit": "llm_tokens",
        "price_source": "user",
        "input_price_per_m": "2.00",
        "output_price_per_m": "8.00",
        "cache_read_price_per_m": "1.00",
        "cache_write_price_per_m": None,
        "encoder_price_per_m": None,
        "fx_to_cny": None,
        "price_effective_at": "2026-10-01",
    }
    base.update(overrides)
    return base


def _pricing(**overrides) -> ConnectionPricing:
    parsed = parse_connection_pricing(_billing(**overrides))
    assert parsed is not None
    return parsed


def _usage(**overrides) -> NormalizedLLMUsage:
    base = {
        "input_total_tokens": 1_500_000,
        "uncached_input_tokens": 1_000_000,
        "cache_read_tokens": 500_000,
        "cache_write_tokens": None,
        "output_tokens": 250_000,
    }
    base.update(overrides)
    return NormalizedLLMUsage(**base)


class TestComputeCostCny:
    def test_paid_llm_tokens_exact_decimal(self):
        """收费：uncached / 缓存 / 输出三分量 Decimal 计价（无汇率）。"""
        cost = compute_cost_cny(_pricing(), _usage())
        # 1_000_000/1e6*2 + 500_000/1e6*1 + 250_000/1e6*8 = 2 + 0.5 + 2 = 4.5
        assert cost == "4.5000"

    def test_paid_with_fx_multiplier(self):
        """汇率：fx_to_cny 非 None 为换算乘数（外币申报单价）。"""
        cost = compute_cost_cny(_pricing(fx_to_cny="7.2"), _usage())
        assert cost == "32.4000"  # 4.5 × 7.2

    def test_fx_none_means_price_already_cny(self):
        """汇率 None：乘数 1（单价视为人民币申报），不换算。"""
        cost = compute_cost_cny(_pricing(fx_to_cny=None), _usage())
        assert cost == "4.5000"

    def test_fx_zero_is_unknown_not_free(self):
        """fx_to_cny=0：零折算为无效汇率配置 → 不可计价 None（不伪装免费）。"""
        assert compute_cost_cny(_pricing(fx_to_cny="0"), _usage()) is None

    def test_free_price_zero_is_known_zero(self):
        """免费：价格事实为 0 → 已知零（非 None 未知），R21 语义区分点。"""
        cost = compute_cost_cny(
            _pricing(input_price_per_m="0", output_price_per_m="0", cache_read_price_per_m="0"), _usage()
        )
        assert cost == "0.0000"

    def test_usage_only_mode_never_priced(self):
        """usage_only 不计价 → None（未知语义，不伪装 0）。"""
        assert compute_cost_cny(_pricing(mode="usage_only"), _usage()) is None

    def test_pricing_none_returns_none(self):
        """billing 缺失（快照 None）→ 不可计价。"""
        assert compute_cost_cny(None, _usage()) is None

    def test_usage_none_returns_none(self):
        """无用量 → 不可计价（不伪装免费）。"""
        assert compute_cost_cny(_pricing(), None) is None

    def test_missing_price_returns_none(self):
        """estimate_cost 但缺输出单价 → 未知。"""
        assert compute_cost_cny(_pricing(output_price_per_m=None), _usage()) is None
        assert compute_cost_cny(_pricing(input_price_per_m=None), _usage()) is None

    def test_missing_uncached_or_output_tokens_returns_none(self):
        """uncached / output token 缺失 → 未知（不推导不伪装）。"""
        assert compute_cost_cny(_pricing(), _usage(uncached_input_tokens=None)) is None
        assert compute_cost_cny(_pricing(), _usage(output_tokens=None)) is None

    def test_cache_read_reported_without_price_is_unknown(self):
        """缓存 token 已报告而单价缺失 → 整体未知（部分未知不伪装 0）。"""
        cost = compute_cost_cny(_pricing(cache_read_price_per_m=None), _usage())
        assert cost is None

    def test_cache_read_unreported_is_skipped(self):
        """缓存 token 未报告（None）→ 分量跳过（uncached 已覆盖全部输入），
        其余分量照常计价：1_000_000*2/1e6 + 250_000*8/1e6 = 2 + 2 = 4。"""
        cost = compute_cost_cny(
            _pricing(cache_read_price_per_m=None),
            _usage(cache_read_tokens=None, cache_write_tokens=None, uncached_input_tokens=1_000_000),
        )
        assert cost == "4.0000"

    def test_cache_write_component_priced_when_reported(self):
        """缓存写分量防御性支持：报告 + 单价 → 计入。"""
        cost = compute_cost_cny(
            _pricing(cache_write_price_per_m="2.00"),
            _usage(cache_write_tokens=100_000),
        )
        # 2 + 0.5*1 + 0.1*2 + 2 = 4.7
        assert cost == "4.7000"

    def test_encoder_unit_priced_by_encoder_tokens(self):
        """encoder 计价单位：encoder_price_per_m × encoder_input_tokens。"""
        pricing = _pricing(pricing_unit="encoder_input_tokens", encoder_price_per_m="1.50")
        cost = compute_cost_cny(pricing, _usage(), encoder_input_tokens=2_000_000)
        assert cost == "3.0000"

    def test_encoder_unit_with_fx(self):
        pricing = _pricing(pricing_unit="encoder_input_tokens", encoder_price_per_m="1.50", fx_to_cny="7.2")
        cost = compute_cost_cny(pricing, _usage(), encoder_input_tokens=2_000_000)
        assert cost == "21.6000"

    def test_encoder_unit_missing_tokens_is_unknown(self):
        """encoder token 未知 → 不可计价。"""
        pricing = _pricing(pricing_unit="encoder_input_tokens", encoder_price_per_m="1.50")
        assert compute_cost_cny(pricing, _usage(), encoder_input_tokens=None) is None

    def test_encoder_unit_missing_price_is_unknown(self):
        pricing = _pricing(pricing_unit="encoder_input_tokens", encoder_price_per_m=None)
        assert compute_cost_cny(pricing, _usage(), encoder_input_tokens=100) is None

    def test_unknown_pricing_unit_returns_none(self):
        assert compute_cost_cny(_pricing(pricing_unit="weird_unit"), _usage()) is None

    def test_rounding_half_up_four_digits(self):
        """舍入：quantize 4 位 HALF_UP（0.00005 → 0.0001，非银行家舍入）。"""
        pricing = _pricing(input_price_per_m="1.00", output_price_per_m="0", cache_read_price_per_m="0")
        usage = _usage(uncached_input_tokens=50, cache_read_tokens=None, cache_write_tokens=None, output_tokens=50)
        # 50/1e6*1 + 50/1e6*0 = 0.00005 → HALF_UP → 0.0001
        assert compute_cost_cny(pricing, usage) == "0.0001"


class TestParseConnectionPricing:
    def test_billing_none_returns_none(self):
        assert parse_connection_pricing(None) is None

    def test_billing_non_mapping_returns_none(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING):
            assert parse_connection_pricing("not-a-mapping") is None
        assert any("billing" in r.message for r in caplog.records)

    def test_fields_frozen_into_snapshot(self):
        pricing = parse_connection_pricing(_billing())
        assert pricing is not None
        assert pricing.mode == "estimate_cost"
        assert pricing.pricing_unit == "llm_tokens"
        assert pricing.price_source == "user"
        assert pricing.input_price_per_m == Decimal("2.00")
        assert pricing.output_price_per_m == Decimal("8.00")
        assert pricing.cache_read_price_per_m == Decimal("1.00")
        assert pricing.fx_to_cny is None
        assert pricing.price_effective_at == "2026-10-01"

    def test_malformed_amount_degrades_to_none_with_warning(self, caplog: pytest.LogCaptureFixture):
        """畸形金额串 → 该单价 None（不可计价方向）+ warning，其余字段照录。"""
        with caplog.at_level(logging.WARNING):
            pricing = parse_connection_pricing(_billing(input_price_per_m="abc"))
        assert pricing is not None
        assert pricing.input_price_per_m is None
        assert pricing.output_price_per_m == Decimal("8.00")
        assert any("input_price_per_m" in r.message for r in caplog.records)

    def test_negative_amount_rejected(self):
        """负金额 → 该单价降级 None（不可计价方向），其余字段照录不静默丢弃。"""
        pricing = parse_connection_pricing(_billing(output_price_per_m="-5"))
        assert pricing is not None
        assert pricing.output_price_per_m is None
        assert pricing.input_price_per_m == Decimal("2.00")
        assert pricing.mode == "estimate_cost"


class TestMetadataRoundTrip:
    def test_snapshot_metadata_none_passthrough(self):
        assert pricing_snapshot_metadata(None) is None
        assert pricing_from_metadata(None) is None

    def test_round_trip_preserves_decimal_precision(self):
        """metadata 串化往返：Decimal → str → Decimal 保精度，计价结果一致。"""
        pricing = _pricing(fx_to_cny="7.2")
        snapshot = pricing_snapshot_metadata(pricing)
        assert snapshot is not None
        restored = pricing_from_metadata(snapshot)
        assert restored == pricing
        usage = _usage()
        assert compute_cost_cny(restored, usage) == compute_cost_cny(pricing, usage) == "32.4000"

    def test_from_metadata_malformed_mapping_returns_none(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING):
            assert pricing_from_metadata("junk") is None
        assert any("pricing" in r.message for r in caplog.records)
