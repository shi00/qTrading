"""LLM 云端调用货币成本估算（review04 AI-03 完整版）。

对 token 计量（``token_budget.py``）补一层价格映射，把「调用次数 / token 数」
换算成「货币成本」。设计约定与 ``token_budget.py`` 一致：**未知模型返回 None 而非
强猜** —— 不猜价格，由 UI 显示「成本未知」，符合检视报告 AI-03「未知模型返回 None」。

价格基准：
- 币种：人民币（CNY，元 / 百万 tokens）。默认 provider 为 DeepSeek，官方按人民币
  定价，UI 展示「元」符合国内用户直觉。
- 模型 key：匹配**单位模型 id**（不含 provider 前缀）。调用方传入的 ``effective_model``
  形如 ``f"{provider}/{model}"``（见 ``litellm_client._chat_completion_litellm``），
  本模块经 ``split("/", 1)[-1]`` 取其 model 段再查价，与
  ``token_budget._get_model_context_window`` 的同款拆法保持一致。
- 金额统一按**整数分（cent）**累计（见 ``usage_tracker.py``），本模块返回浮点元，
  由调用方转换为分。
"""

from __future__ import annotations

from datetime import date

# 价格变动时更新此日期，便于审计价格时效性。
PRICING_UPDATED = date(2026, 9, 14)

# model_id: (input_price_cny_per_1m, output_price_cny_per_1m)
# 单位：元 / 百万 tokens。
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v4-pro": (2.0, 8.0),
    "deepseek-v4-flash": (1.0, 2.0),
}


def estimate_cost(
    effective_model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """估算一次 LLM 调用的货币成本（元）。

    Args:
        effective_model: 生效模型标识。可为 ``"provider/model"`` 亦可为裸 model id，
            内部统一取 ``split("/", 1)[-1]`` 的 model 段匹配价格表。
        input_tokens: prompt 侧 token 数。
        output_tokens: completion 侧 token 数。

    Returns:
        估算成本（元）。模型不在价格表或 token 数为负时返回 None（不猜价格，成本未知）。

    Raises:
        无。纯函数，非 None 返回时调用方可直接使用。
    """
    if input_tokens < 0 or output_tokens < 0:
        return None

    model_id = effective_model.rsplit("/", 1)[-1]
    pricing = MODEL_PRICING.get(model_id)
    if pricing is None:
        return None

    input_price, output_price = pricing
    cost = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
    return round(cost, 4)
