"""estimate_cost 纯函数单测（review04 AI-03 完整版，T2）。"""

from datetime import date
from typing import cast

from services.ai_service.pricing import MODEL_PRICING, PRICING_UPDATED, estimate_cost


def test_known_model_with_provider_prefix():
    """provider/model → 取 model 段查价并返回非零成本。"""
    cost = cast(float, estimate_cost("deepseek/deepseek-v4-flash", input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost > 0
    # 输入 1 元/百万 + 输出 2 元/百万 = 3.0 元
    assert cost == 3.0


def test_known_model_bare_id():
    """裸 model id 也按同一路径匹配。"""
    input_price, output_price = MODEL_PRICING["deepseek-v4-flash"]
    cost = cast(float, estimate_cost("deepseek-v4-flash", 500_000, 250_000))
    expected = (500_000 / 1_000_000) * input_price + (250_000 / 1_000_000) * output_price
    assert cost == round(expected, 4)


def test_unknown_model_returns_none():
    """未知模型返回 None，不猜价格。"""
    assert estimate_cost("openai/gpt-999", 100, 100) is None


def test_zero_tokens_zero_cost():
    """零 token → 零成本（非 None）。"""
    assert estimate_cost("deepseek/deepseek-v4-pro", 0, 0) == 0.0


def test_negative_tokens_returns_none():
    """负 token 视为数据异常，返回 None（成本未知）。"""
    assert estimate_cost("deepseek/deepseek-v4-flash", -1, 100) is None
    assert estimate_cost("deepseek/deepseek-v4-flash", 100, -1) is None


def test_pricing_updated_is_date():
    """价格时效性锚点是已过去的日期。"""
    assert isinstance(PRICING_UPDATED, date)
    assert date.today() >= PRICING_UPDATED
