"""单位换算注入网关（E-01 / R20）：services 不 import strategies，由 app 注入。

统一换算入口仍是 ``strategies.utils.threshold_in_data_unit``；本模块只负责：
- 校验事实的金额币种（未知币种拒绝）；
- 把注入 callable 的失败按语义分类：未知金额单位 → ``INPUT_INVALID``；
  数量分支无证据/未实施 → ``unverified`` 结果（R21：不填 0、不伪核验）；
- 成功时返回带 ``conversion_ref`` 的结果，供 FactValue 持久化引用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from core.agent_types import AgentErrorCode, AgentServiceError, FactValue, VerificationState
from core.errors import StrategyParamError

#: 已知的金额币种（事实 currency 为空表示不适用/未知，由调用方决定）。
_SUPPORTED_CURRENCIES = frozenset({"CNY"})
#: 数量单位（与 strategies.utils 的数量分支一致）。
_QUANTITY_UNITS = frozenset({"lot", "share"})
#: 统一换算入口的版本化引用（写入 FactValue.conversion_ref）。
CONVERSION_REF = "threshold_in_data_unit@v1"


class UnitConversion(Protocol):
    """注入的结构化换算 callable（签名与 strategies.utils.threshold_in_data_unit 一致）。"""

    def __call__(self, df: Any, column: str, declared_unit: str, value: float, value_unit: str) -> float: ...


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    """换算结果：成功值 + 核验态 + 引用；未实施分支保留原因而不返回数值。"""

    value: float | None
    verification: VerificationState
    conversion_ref: str | None
    reason: str | None


class UnitConversionGateway:
    """事实换算网关（构造时注入 callable；生产由 app 注入统一入口）。"""

    def __init__(self, convert: UnitConversion) -> None:
        self._convert = convert

    def convert(
        self,
        fact: FactValue,
        *,
        value: float,
        value_unit: str,
        df: Any,
        declared_unit: str,
    ) -> ConversionOutcome:
        """把 UI 阈值换算到数据单位；未知币种/金额单位拒绝，数量证据缺失记未核。"""

        if fact.currency is not None and fact.currency not in _SUPPORTED_CURRENCIES:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"unit_rejected:unsupported_currency:{fact.currency}",
                fact.field_key,
            )
        try:
            converted = self._convert(df, fact.field_key, declared_unit, value, value_unit)
        except StrategyParamError as exc:
            if declared_unit in _QUANTITY_UNITS and value_unit in _QUANTITY_UNITS:
                return ConversionOutcome(
                    value=None,
                    verification=VerificationState.UNVERIFIED,
                    conversion_ref=None,
                    reason="quantity_conversion_not_implemented",
                )
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"unit_rejected:{getattr(getattr(exc, 'info', None), 'message_key', 'unit_unknown')}",
                fact.field_key,
            ) from exc
        return ConversionOutcome(
            value=float(converted),
            verification=VerificationState.PROGRAM_VERIFIED,
            conversion_ref=CONVERSION_REF,
            reason=None,
        )
