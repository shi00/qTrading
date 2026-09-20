"""LLM 云端调用货币成本估算（AI-01 litellm 化）。

对 token 计量（``token_budget.py``）补一层价格映射，把「调用次数 / token 数」
换算成「货币成本」。设计约定：**未知模型返回 None 而非强猜** —— 不猜价格，由 UI 显示
「成本未知」，符合 R21「缺失值伪装」红线（不把「不可计量」伪装成「零成本」）。

价格基准（AI-01 全 litellm 化，用户拍板 2026-09-20）：
- 全部成本估算依托 ``litellm.cost_per_token``，删除自维护 ``MODEL_PRICING`` 手写价格表
  与手写公式，不造轮子。
- 币种：litellm 内置价格表为美元，本模块统一乘 ``_USD_TO_CNY_RATE`` 汇率换算为人民币（元）
  展示，符合国内用户直觉。
- 模型 id 统一对齐 litellm 官方 key：官方 key 在 litellm 价格表天然有价，无需注册私有价；
  用户可见模型清单也以 litellm 官方目录为权威源（见 ``utils/llm_providers`` 3.1c）。
- 免费模型：litellm 对已定价免费模型返回 ``(0.0, 0.0)``（非抛异常），本模块返回 ``0.0`` 而非
  ``None``，由上层用 ``cost is not None`` 区分「计量为 0（免费）」与「不可计量（None）」。
- 不可计价模型（不在 litellm 价格表）：``cost_per_token`` 抛异常 → 本模块返回 ``None``，
  由上层按「不可计价」计数。

litellm 为惰性加载（避免 UI 主循环 import 阻塞，与 ``litellm_client`` 一致）：本模块内
``import litellm`` 均在函数体内执行，不做模块顶层 import。
"""

from __future__ import annotations

from datetime import date

# 价格变动时更新此日期，便于审计价格时效性。
PRICING_UPDATED = date(2026, 9, 14)

# 汇率：litellm 内置价格表为美元，换算为人民币展示。
_USD_TO_CNY_RATE = 7.2  # NOTE(lazy): 简化，固定汇率不实时拉取. ceiling: 波动<±10%. upgrade: 需精确对账或引入实时汇率时


def estimate_cost(
    effective_model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """估算一次 LLM 调用的货币成本（元），全量依托 litellm 计价。

    模型 id 需为 litellm 官方 key（可为 ``"provider/model"`` 亦可为裸 model id，
    ``litellm.cost_per_token`` 原生支持 provider 前缀逐级剥匹配）。

    Args:
        effective_model: 生效模型标识，即 litellm 官方 key（如 ``"deepseek/deepseek-chat"``）。
        input_tokens: prompt 侧 token 数。
        output_tokens: completion 侧 token 数。

    Returns:
        估算成本（元）。模型不在 litellm 价格表（cost_per_token 抛异常）或 token 数为负时
        返回 None（不猜价格，成本未知）；免费模型返回 0.0（非 None，属已计价）。

    Raises:
        无。纯函数，非 None 返回时调用方可直接使用。
    """
    if input_tokens < 0 or output_tokens < 0:
        return None

    import litellm  # type: ignore[import-untyped]  # 惰性加载，避免 UI 主循环 import 阻塞

    # AI-01 §3.1c-review #1：计价键回溯。effective_model 形如 ``<provider>/<model>``
    # （provider 为项目 provider_id），但对 qwen 等 provider，litellm ``cost_per_token``
    # 不认 ``qwen/`` 前缀（探针实测抛 BadRequestError）。以 ``litellm_catalog_key``
    # （如 qwen→dashscope）覆盖计价前缀为 litellm 官方 key 后再交 ``cost_per_token``。
    effective_provider, sep, model_id = effective_model.partition("/")
    if sep:
        from utils.llm_providers import _resolve_catalog_key

        catalog_key = _resolve_catalog_key(effective_provider)
        if catalog_key and catalog_key != effective_provider:
            effective_model = f"{catalog_key}/{model_id}"

    try:
        in_usd, out_usd = litellm.cost_per_token(
            effective_model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
    except Exception:
        # 模型不在 litellm 价格表 → 抛异常。控制流分支：返回 None，由上层按「不可计价」
        # 计数（R21：不把「不可计量」伪装成「零成本」）。
        return None

    cost = (in_usd + out_usd) * _USD_TO_CNY_RATE
    return round(cost, 4)
