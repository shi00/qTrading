"""连接计价冻结快照与 Decimal 成本计算（F17，§9.1 计量语义）。

职责：
- ``parse_connection_pricing``：快照连接 mapping 的 ``billing`` 块 → 不可变计价
  快照（mode / pricing_unit / 价格来源 / 各单价 / 汇率 / 生效时点）。价格从
  **十进制字符串**构造 :class:`~decimal.Decimal`（金额契约 D2-D5：禁 float，
  经 ``utils.config_models.validate_decimal_amount`` 唯一正本校验），解析失败的
  单价记 ``None``（不可计价方向，保守降级 + warning 可观测，不伪装免费）。
- ``compute_cost_cny``：按冻结价格 × 真实 usage 用 Decimal 计算 CNY 成本
  （quantize 4 位小数，ROUND_HALF_UP）。**不可计价一律返回 None**（R21：
  未知成本禁止伪装为已知零）；唯一合法的零 = 价格事实为 0（免费，已知零）。

计价时点契约：``compute_cost_cny`` 在终态落账时点（usage 确定后）调用——
cost 与 usage 同源，避免观察时点计价在重试场景下与成功 attempt 的用量失配。

分量语义（与 litellm_client 归一化一致，§9.1 ``input_total = uncached +
cache_read + cache_write``）：缓存 token 字段为 None 表示供应商未报告该分量
（uncached 已覆盖全部输入），该分量不计价；token 非 None 而对应单价缺失 →
整体不可计价（部分未知 = 整体未知，不把未知分量伪装为 0）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from core.model_management_types import NormalizedLLMUsage
from utils.config_models import validate_decimal_amount

logger = logging.getLogger(__name__)

# 成本舍入：4 位小数（与旧链 pricing ``round(cost, 4)`` 精度对齐），金额惯例
# ROUND_HALF_UP（Decimal 显式指定，不依赖上下文默认银行家舍入）。
_COST_QUANTUM: Final[Decimal] = Decimal("0.0001")
# 单价换算基数：单价按「每百万 token」申报（BillingConfig *_price_per_m 语义）。
_TOKENS_PER_M: Final[Decimal] = Decimal(1_000_000)

_PRICING_AMOUNT_FIELDS: Final[tuple[str, ...]] = (
    "input_price_per_m",
    "output_price_per_m",
    "cache_read_price_per_m",
    "cache_write_price_per_m",
    "encoder_price_per_m",
    "fx_to_cny",
)


@dataclass(frozen=True, slots=True)
class ConnectionPricing:
    """连接计价冻结快照（F17）：快照语义——普通保存只影响后续请求。

    单价 / 汇率为 :class:`~decimal.Decimal` 或 ``None``（缺失 = 不可计价，
    不伪装 0）；``mode`` / ``pricing_unit`` 为磁盘原值（畸形值照录留证，
    由 :func:`compute_cost_cny` 判定不计价）。
    """

    mode: str | None
    pricing_unit: str | None
    price_source: str | None
    input_price_per_m: Decimal | None
    output_price_per_m: Decimal | None
    cache_read_price_per_m: Decimal | None
    cache_write_price_per_m: Decimal | None
    encoder_price_per_m: Decimal | None
    fx_to_cny: Decimal | None
    price_effective_at: str | None


def _parse_amount(raw: Any, *, field: str, connection_hint: str) -> Decimal | None:
    """billing 金额字段（十进制字符串）→ Decimal；None 直通，非法 → None + warning。"""
    if raw is None:
        return None
    try:
        validate_decimal_amount(str(raw))
    except ValueError as e:
        logger.warning("[connection_pricing] %s billing %s 非法，按不可计价降级: %s", connection_hint, field, e)
        return None
    return Decimal(str(raw))


def parse_connection_pricing(billing_raw: Any, *, connection_hint: str = "") -> ConnectionPricing | None:
    """快照连接 mapping 的 ``billing`` 块 → 计价冻结快照。

    ``billing`` 缺失 / 非 mapping → ``None``（无计价配置，未知语义，不伪装
    usage_only）；字段畸形（金额非法）时该单价降级 None（不可计价方向），
    其余字段照常解析（R21：证据不静默丢弃）。
    """
    if billing_raw is None:
        return None
    if not isinstance(billing_raw, Mapping):
        logger.warning(
            "[connection_pricing] %s billing 形态异常（%s），按无计价配置处理",
            connection_hint,
            type(billing_raw).__name__,
        )
        return None
    amounts = {
        field: _parse_amount(billing_raw.get(field), field=field, connection_hint=connection_hint)
        for field in _PRICING_AMOUNT_FIELDS
    }
    return ConnectionPricing(
        mode=billing_raw.get("mode"),
        pricing_unit=billing_raw.get("pricing_unit"),
        price_source=billing_raw.get("price_source"),
        input_price_per_m=amounts["input_price_per_m"],
        output_price_per_m=amounts["output_price_per_m"],
        cache_read_price_per_m=amounts["cache_read_price_per_m"],
        cache_write_price_per_m=amounts["cache_write_price_per_m"],
        encoder_price_per_m=amounts["encoder_price_per_m"],
        fx_to_cny=amounts["fx_to_cny"],
        price_effective_at=billing_raw.get("price_effective_at"),
    )


def pricing_snapshot_metadata(pricing: ConnectionPricing | None) -> dict[str, Any] | None:
    """计价快照 → metadata 携带形态（金额 Decimal 串化为十进制字符串保精度）。"""
    if pricing is None:
        return None
    return {
        "mode": pricing.mode,
        "pricing_unit": pricing.pricing_unit,
        "price_source": pricing.price_source,
        "input_price_per_m": None if pricing.input_price_per_m is None else str(pricing.input_price_per_m),
        "output_price_per_m": None if pricing.output_price_per_m is None else str(pricing.output_price_per_m),
        "cache_read_price_per_m": (
            None if pricing.cache_read_price_per_m is None else str(pricing.cache_read_price_per_m)
        ),
        "cache_write_price_per_m": (
            None if pricing.cache_write_price_per_m is None else str(pricing.cache_write_price_per_m)
        ),
        "encoder_price_per_m": None if pricing.encoder_price_per_m is None else str(pricing.encoder_price_per_m),
        "fx_to_cny": None if pricing.fx_to_cny is None else str(pricing.fx_to_cny),
        "price_effective_at": pricing.price_effective_at,
    }


def pricing_from_metadata(snapshot: Any) -> ConnectionPricing | None:
    """metadata 携带的计价快照 → 计价快照（Decimal 从十进制字符串重建）。"""
    if snapshot is None:
        return None
    if not isinstance(snapshot, Mapping):
        logger.warning(
            "[connection_pricing] metadata pricing 快照形态异常（%s），按不可计价处理", type(snapshot).__name__
        )
        return None
    amounts = {
        field: _parse_amount(snapshot.get(field), field=field, connection_hint="metadata")
        for field in _PRICING_AMOUNT_FIELDS
    }
    return ConnectionPricing(
        mode=snapshot.get("mode"),
        pricing_unit=snapshot.get("pricing_unit"),
        price_source=snapshot.get("price_source"),
        input_price_per_m=amounts["input_price_per_m"],
        output_price_per_m=amounts["output_price_per_m"],
        cache_read_price_per_m=amounts["cache_read_price_per_m"],
        cache_write_price_per_m=amounts["cache_write_price_per_m"],
        encoder_price_per_m=amounts["encoder_price_per_m"],
        fx_to_cny=amounts["fx_to_cny"],
        price_effective_at=snapshot.get("price_effective_at"),
    )


def _component_cost(tokens: int | None, price_per_m: Decimal | None) -> Decimal | None:
    """单分量成本（token / 1e6 × 单价）；token 未报告 → 0（分量不存在），
    token 报告而单价缺失 → None（部分未知 = 整体未知）。"""
    if tokens is None:
        return Decimal("0")
    if tokens < 0 or price_per_m is None:
        return None
    return Decimal(tokens) / _TOKENS_PER_M * price_per_m


def compute_cost_cny(
    pricing: ConnectionPricing | None,
    usage: NormalizedLLMUsage | None,
    *,
    encoder_input_tokens: int | None = None,
) -> str | None:
    """按冻结价格 × 真实 usage 计算 CNY 成本（十进制字符串，4 位 HALF_UP）。

    返回 ``None`` = 不可计价（usage_only / 计价配置缺失 / 单价或用量未知），
    调用方保持 cost 缺失语义（R21 禁止填 0 伪装）；返回 ``"0.0000"`` = 免费
    （价格事实为 0，已知零）。

    - ``fx_to_cny``：非 None 为换算乘数（单价为外币申报）；None 乘数 1
      （单价视为人民币申报）；0 为无效折算配置 → 不可计价。
    - ``llm_tokens``：uncached_input / output 必需（缺失 → 未知）；缓存分量
      token 未报告跳过、报告而单价缺失 → 未知。
    - ``encoder_input_tokens``：encoder_price_per_m × encoder_input_tokens
      （token 或单价缺失 → 未知）。
    """
    if pricing is None or pricing.mode != "estimate_cost":
        return None
    if usage is None:
        return None
    fx = pricing.fx_to_cny if pricing.fx_to_cny is not None else Decimal("1")
    if fx <= 0:
        return None
    if pricing.pricing_unit == "encoder_input_tokens":
        if encoder_input_tokens is None:
            return None
        total = _component_cost(encoder_input_tokens, pricing.encoder_price_per_m)
        if total is None:
            return None
    elif pricing.pricing_unit == "llm_tokens":
        if pricing.input_price_per_m is None or pricing.output_price_per_m is None:
            return None
        uncached = usage.uncached_input_tokens
        output = usage.output_tokens
        if uncached is None or output is None:
            return None
        cache_read = _component_cost(usage.cache_read_tokens, pricing.cache_read_price_per_m)
        cache_write = _component_cost(usage.cache_write_tokens, pricing.cache_write_price_per_m)
        input_cost = _component_cost(uncached, pricing.input_price_per_m)
        output_cost = _component_cost(output, pricing.output_price_per_m)
        if cache_read is None or cache_write is None or input_cost is None or output_cost is None:
            return None
        total = input_cost + cache_read + cache_write + output_cost
    else:
        return None
    cost = total * fx
    if not cost.is_finite() or cost < 0:
        logger.warning("[connection_pricing] 成本计算结果非有限/负值，按不可计价处理: %s", cost)
        return None
    return str(cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP))

