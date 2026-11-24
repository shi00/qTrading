"""E-01-4 单位换算注入网关：币种拒绝、金额失败分类、数量未核（R21）、精确换算（I68）。"""

from decimal import Decimal

import pytest

from app.agent_wiring import agent_unit_converter
from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    FactValue,
    Selection,
    SourceKind,
    SourceLocator,
    SourceVersionRef,
    VerificationState,
    sha256_hex,
)
from core.errors import StrategyParamError
from core.i18n import Message
from services.agent_service.units import CONVERSION_REF, PRECISION_POLICY, UnitConversionGateway
from typing import Any

_DIGEST = sha256_hex(b"e01-units")


def _fact(
    *,
    currency: str | None = "CNY",
    field_key: str = "amount",
    source_unit: str = "million_cny",
    display_unit: str = "yi_cny",
) -> FactValue:
    return FactValue(
        field_key=field_key,
        object_ref="600000.SH@2025",
        raw_type="json_decimal",
        raw_value='{"numeric_encoding":"agent-number-v1","raw_type":"json_decimal",'
        '"lexeme":"1.5","exact_decimal":"1.5"}',
        numeric_lexeme="1.5",
        numeric_encoding="agent-number-v1",
        source_unit=source_unit,
        currency=currency,
        display_unit=display_unit,
        conversion_ref=None,
        period="2025",
        basis=None,
        source_locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.BUSINESS_SNAPSHOT,
                source_id="biz-1",
                source_version="1",
                source_digest=_DIGEST,
            ),
            selection=Selection(kind="all"),
        ),
        verification=VerificationState.PROGRAM_VERIFIED,
        missing_reason=None,
        xlsx_cell_meta=None,
    )


class RecordingConverter:
    def __init__(self, *, result: float = 2.5, error: StrategyParamError | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[object, str, str, float, str]] = []

    def __call__(self, df: Any, column: str, declared_unit: str, value: float, value_unit: str) -> float:
        self.calls.append((df, column, declared_unit, value, value_unit))
        if self.error is not None:
            raise self.error
        return self.result


class TestUnitConversionGateway:
    def test_success_returns_exact_decimal_with_lineage(self) -> None:
        converter = RecordingConverter(result=5000.0)
        outcome = UnitConversionGateway(converter).convert(
            _fact(), value=50, value_unit="yi_cny", df=object(), declared_unit="million_cny"
        )
        assert outcome.value == Decimal("5000")
        assert outcome.exact_decimal == "5000"
        assert outcome.verification is VerificationState.PROGRAM_VERIFIED
        assert outcome.precision == PRECISION_POLICY
        assert outcome.conversion_ref == (
            f"{CONVERSION_REF}|policy={PRECISION_POLICY}|yi_cny->million_cny|scale=1e+2|in=50|out=5000"
        )
        assert outcome.reason is None
        assert converter.calls[0][1:] == ("amount", "million_cny", 50.0, "yi_cny")

    def test_float_input_rejected_before_conversion(self) -> None:
        # I68：float 已丢精度，不得进入精确核验路径；转换器不得被调用。
        converter = RecordingConverter()
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(),
                value=50.5,  # pyright: ignore[reportArgumentType]  # float 入口须被拒绝（I68）
                value_unit="yi_cny",
                df=object(),
                declared_unit="million_cny",
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:inexact_input" in str(exc.value)
        assert converter.calls == []

    def test_exact_decimal_input_survives_float_witness_noise(self) -> None:
        # 0.1*100 的浮点见证带噪声，但精确值仍是 10；结果不经 float 承载。
        converter = RecordingConverter(result=10.000000000000002)
        outcome = UnitConversionGateway(converter).convert(
            _fact(), value=Decimal("0.1"), value_unit="yi_cny", df=object(), declared_unit="million_cny"
        )
        assert outcome.value == Decimal("10")
        assert outcome.exact_decimal == "10"
        assert outcome.verification is VerificationState.PROGRAM_VERIFIED

    def test_non_power_of_ten_scaling_is_unverified_not_verified(self) -> None:
        # 注入 callable 不是纯十进制缩放时，无法用十进制重算：不宣称已核（R21）。
        converter = RecordingConverter(result=7.5)
        outcome = UnitConversionGateway(converter).convert(
            _fact(), value=50, value_unit="yi_cny", df=object(), declared_unit="million_cny"
        )
        assert outcome.value is None
        assert outcome.exact_decimal is None
        assert outcome.verification is VerificationState.UNVERIFIED
        assert outcome.conversion_ref is None
        assert outcome.reason == "conversion_not_exact_scale"

    def test_witness_mismatch_beyond_policy_is_unverified(self) -> None:
        # 位移指数可辨识但见证偏离超过 1e-12 相对精度：不宣称已核。
        converter = RecordingConverter(result=5000.000001)
        outcome = UnitConversionGateway(converter).convert(
            _fact(), value=50, value_unit="yi_cny", df=object(), declared_unit="million_cny"
        )
        assert outcome.verification is VerificationState.UNVERIFIED
        assert outcome.reason == "conversion_witness_mismatch"

    def test_zero_input_is_exact_zero(self) -> None:
        converter = RecordingConverter(result=0.0)
        outcome = UnitConversionGateway(converter).convert(
            _fact(), value=0, value_unit="yi_cny", df=object(), declared_unit="million_cny"
        )
        assert outcome.value == Decimal(0)
        assert outcome.verification is VerificationState.PROGRAM_VERIFIED
        assert "|scale=unknown|" in str(outcome.conversion_ref)

    def test_non_finite_result_rejected(self) -> None:
        converter = RecordingConverter(result=float("inf"))
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(), value=1, value_unit="yi_cny", df=object(), declared_unit="million_cny"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:non_finite_result" in str(exc.value)

    def test_unknown_currency_rejected_before_conversion(self) -> None:
        converter = RecordingConverter()
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(currency="USD"), value=1, value_unit="yi_cny", df=object(), declared_unit="million_cny"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unsupported_currency" in str(exc.value)
        assert converter.calls == []

    def test_amount_unit_failure_is_input_invalid(self) -> None:
        converter = RecordingConverter(error=StrategyParamError(Message("strategy_unit_unknown", {})))
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(currency=None, source_unit="btc", display_unit="btc"),
                value=1,
                value_unit="btc",
                df=object(),
                declared_unit="btc",
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:strategy_unit_unknown" in str(exc.value)

    def test_quantity_evidence_missing_is_unverified_not_zero(self) -> None:
        converter = RecordingConverter(error=StrategyParamError(Message("strategy_unit_evidence_missing", {})))
        outcome = UnitConversionGateway(converter).convert(
            _fact(field_key="vol", currency=None, source_unit="lot", display_unit="share"),
            value=200,
            value_unit="share",
            df=object(),
            declared_unit="lot",
        )
        assert outcome.value is None
        assert outcome.verification is VerificationState.UNVERIFIED
        assert outcome.conversion_ref is None
        assert outcome.reason == "quantity_conversion_not_implemented"

    def test_source_unit_mismatch_rejected_before_conversion(self) -> None:
        converter = RecordingConverter()
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(), value=1, value_unit="yi_cny", df=object(), declared_unit="wan_cny"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:source_unit_mismatch" in str(exc.value)
        assert converter.calls == []

    def test_display_unit_mismatch_rejected_before_conversion(self) -> None:
        converter = RecordingConverter()
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(), value=1, value_unit="wan_cny", df=object(), declared_unit="million_cny"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:display_unit_mismatch" in str(exc.value)
        assert converter.calls == []

    def test_amount_unit_requires_currency(self) -> None:
        converter = RecordingConverter()
        with pytest.raises(AgentServiceError) as exc:
            UnitConversionGateway(converter).convert(
                _fact(currency=None), value=1, value_unit="yi_cny", df=object(), declared_unit="million_cny"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "unit_rejected:currency_required" in str(exc.value)
        assert converter.calls == []


def test_app_wiring_injects_the_unified_entry() -> None:
    from strategies.utils import threshold_in_data_unit

    assert agent_unit_converter() is threshold_in_data_unit
