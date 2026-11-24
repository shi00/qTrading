"""单位换算注入网关（E-01 / R20 / I68）：services 不 import strategies，由 app 注入。

统一换算入口仍是 ``strategies.utils.threshold_in_data_unit``；本模块只负责：
- 校验事实的金额币种（未知币种拒绝）；
- 拒绝 float 入口：浮点数已丢精度，不得进入「精确核验」路径（I68）；
- 把注入 callable 的失败按语义分类：未知金额单位 → ``INPUT_INVALID``；
  数量分支无证据/未实施 → ``unverified`` 结果（R21：不填 0、不伪核验）；
- 成功时以十进制位移精确重算换算值（不经 float 承载结果），并记录精度政策
  与换算谱系（``conversion_ref``），供 FactValue 持久化引用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Protocol, cast

from core.agent_types import (
    MAX_EXACT_DECIMAL_CHARS,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    FactValue,
    VerificationState,
    encode_exact_number,
)
from core.errors import StrategyParamError

#: 已知的金额币种（事实 currency 为空表示不适用/未知，由调用方决定）。
_SUPPORTED_CURRENCIES = frozenset({"CNY"})
#: 金额单位（与 strategies.utils 的金额分支一致；不 import strategies，避免跨层依赖）。
_AMOUNT_UNITS = frozenset({"yuan", "wan_cny", "million_cny", "yi_cny"})
#: 数量单位（与 strategies.utils 的数量分支一致）。
_QUANTITY_UNITS = frozenset({"lot", "share"})
#: 统一换算入口的版本化引用（写入 FactValue.conversion_ref 的谱系前缀）。
CONVERSION_REF = "threshold_in_data_unit@v1"
#: 精度政策（I68）：换算值只由「精确十进制 + 10 的整数幂位移」产生，float 仅作见证。
PRECISION_POLICY = "exact_decimal_scale@v1"
#: 浮点见证与精确十进制结果之间容许的相对偏差（超出即拒绝宣称已核）。
_WITNESS_RELATIVE_TOLERANCE = Decimal("1e-12")
#: 由见证反推位移指数时容许的相对偏差（足以区分相邻 10 的幂）。
_SCALE_RELATIVE_TOLERANCE = 1e-9
#: float 可表示的数量级上限（超出无法反推位移指数，保守拒绝）。
_MAX_SCALE_EXPONENT = 300


class UnitConversion(Protocol):
    """注入的结构化换算 callable（签名与 strategies.utils.threshold_in_data_unit 一致）。"""

    def __call__(self, df: Any, column: str, declared_unit: str, value: float, value_unit: str) -> float: ...


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    """换算结果：精确十进制值 + 核验态 + 精度政策 + 谱系；未核分支不返回数值。"""

    value: Decimal | None
    verification: VerificationState
    conversion_ref: str | None
    reason: str | None
    exact_decimal: str | None
    precision: str | None


def _exact_lexeme(value: Decimal | int | str) -> str:
    """把换算输入规整为严格 JSON number 词法；float/bool 不是精确数值输入。"""

    if isinstance(value, bool):
        raise ValueError("bool_is_not_number")
    if isinstance(value, float):
        # float 已在进入网关前丢失十进制精度，不能作为精确核验的输入（I68）。
        raise ValueError("float_is_inexact")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non_finite_input")
        return format(value, "f")
    if isinstance(value, str):
        return value
    raise ValueError("unsupported_input_type")


def _scale_exponent(exact_value: Decimal, witness: float) -> int | None:
    """由浮点见证反推十进制位移指数；非纯十进制缩放返回 ``None``（不宣称已核）。"""

    if exact_value == 0:
        return None
    try:
        ratio = witness / float(exact_value)
    except (OverflowError, ZeroDivisionError):
        return None
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    exponent = round(math.log10(ratio))
    if abs(exponent) > _MAX_SCALE_EXPONENT:
        return None
    if abs(ratio / (10.0**exponent) - 1.0) > _SCALE_RELATIVE_TOLERANCE:
        return None
    return exponent


def _shift_exponent(value: Decimal, exponent: int) -> Decimal:
    """十进制指数位移（构造即精确，不受 Decimal 上下文精度影响）。"""

    sign, digits, exp = value.as_tuple()
    return Decimal((sign, digits, cast(int, exp) + exponent))


def _fixed_decimal_text(value: Decimal) -> str | None:
    """定点十进制文本（无指数形式）；超出 core 的精确十进制长度上限返回 ``None``。"""

    text = format(value, "f")
    if len(text.lstrip("-")) > MAX_EXACT_DECIMAL_CHARS:
        return None
    return text


class UnitConversionGateway:
    """事实换算网关（构造时注入 callable；生产由 app 注入统一入口）。"""

    def __init__(self, convert: UnitConversion) -> None:
        self._convert = convert

    def convert(
        self,
        fact: FactValue,
        *,
        value: Decimal | int | str,
        value_unit: str,
        df: Any,
        declared_unit: str,
    ) -> ConversionOutcome:
        """把 UI 阈值精确换算到数据单位；未知币种/金额单位拒绝，数量证据缺失记未核。

        ``value`` 必须是精确十进制输入（``Decimal``/``int``/严格 JSON number 词法），
        float 入口一律拒绝；换算值由注入 callable 的浮点结果反推十进制位移指数后
        用 :class:`Decimal` 重算，不用 float 承载结果。反推不出纯十进制缩放、或浮点
        见证与精确值偏差超过精度政策时返回 ``UNVERIFIED``（R21），不冒充已核。
        """

        if fact.currency is not None and fact.currency not in _SUPPORTED_CURRENCIES:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"unit_rejected:unsupported_currency:{fact.currency}",
                fact.field_key,
            )
        # 声明与换算谱系必须一致（R20）：调用方传入的数据/展示单位须与事实自身声明
        # 的 source_unit/display_unit 相符，否则谱系会记录一个与事实相矛盾的换算。
        if fact.source_unit is not None and fact.source_unit != declared_unit:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:source_unit_mismatch",
                fact.field_key,
            )
        if fact.display_unit is not None and fact.display_unit != value_unit:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:display_unit_mismatch",
                fact.field_key,
            )
        if declared_unit in _AMOUNT_UNITS and fact.currency is None:
            # 金额单位已声明却未声明币种：不能按「不适用」放行（R20/R21）。
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:currency_required",
                fact.field_key,
            )
        try:
            encoded = encode_exact_number(_exact_lexeme(value))
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:inexact_input",
                fact.field_key,
            ) from exc
        exact_value = Decimal(encoded.lexeme)
        try:
            converted = self._convert(df, fact.field_key, declared_unit, float(exact_value), value_unit)
        except StrategyParamError as exc:
            if declared_unit in _QUANTITY_UNITS and value_unit in _QUANTITY_UNITS:
                return ConversionOutcome(
                    value=None,
                    verification=VerificationState.UNVERIFIED,
                    conversion_ref=None,
                    reason="quantity_conversion_not_implemented",
                    exact_decimal=None,
                    precision=None,
                )
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"unit_rejected:{getattr(getattr(exc, 'info', None), 'message_key', 'unit_unknown')}",
                fact.field_key,
            ) from exc

        witness = float(converted)
        if not math.isfinite(witness):
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:non_finite_result",
                fact.field_key,
            )
        exponent = _scale_exponent(exact_value, witness)
        if exponent is None:
            if exact_value == 0 and witness == 0.0:
                # 零在任何有限缩放比下都精确为 0，位移指数不可辨识但不影响精确性。
                return ConversionOutcome(
                    value=Decimal(0),
                    verification=VerificationState.PROGRAM_VERIFIED,
                    conversion_ref=self._lineage(value_unit, declared_unit, encoded.lexeme, "0", None),
                    reason=None,
                    exact_decimal="0",
                    precision=PRECISION_POLICY,
                )
            return ConversionOutcome(
                value=None,
                verification=VerificationState.UNVERIFIED,
                conversion_ref=None,
                reason="conversion_not_exact_scale",
                exact_decimal=None,
                precision=PRECISION_POLICY,
            )
        exact = _shift_exponent(exact_value, exponent)
        exact_text = _fixed_decimal_text(exact)
        if exact_text is None:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "unit_rejected:result_out_of_range",
                fact.field_key,
            )
        if not self._witness_matches(Decimal(repr(witness)), exact):
            return ConversionOutcome(
                value=None,
                verification=VerificationState.UNVERIFIED,
                conversion_ref=None,
                reason="conversion_witness_mismatch",
                exact_decimal=None,
                precision=PRECISION_POLICY,
            )
        return ConversionOutcome(
            value=exact,
            verification=VerificationState.PROGRAM_VERIFIED,
            conversion_ref=self._lineage(value_unit, declared_unit, encoded.lexeme, exact_text, exponent),
            reason=None,
            exact_decimal=exact_text,
            precision=PRECISION_POLICY,
        )

    def _lineage(
        self,
        value_unit: str,
        declared_unit: str,
        input_lexeme: str,
        exact_decimal: str,
        exponent: int | None,
    ) -> str:
        """换算谱系：入口版本 + 精度政策 + 单位方向 + 位移指数 + 输入/输出精确词法。"""

        scale = "unknown" if exponent is None else f"1e{exponent:+d}"
        return (
            f"{CONVERSION_REF}|policy={PRECISION_POLICY}|{value_unit}->{declared_unit}"
            f"|scale={scale}|in={input_lexeme}|out={exact_decimal}"
        )

    @staticmethod
    def _witness_matches(witness: Decimal, exact: Decimal) -> bool:
        """浮点见证须落在精度政策允许的相对偏差内（长数字按位数放大上下文精度）。"""

        digits = len(exact.as_tuple().digits)
        with localcontext() as ctx:
            ctx.prec = max(28, digits + 8)
            if exact == 0:
                return witness == 0
            return abs(witness - exact) <= abs(exact) * _WITNESS_RELATIVE_TOLERANCE
