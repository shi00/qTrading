"""estimate_cost 纯函数单测（AI-01 litellm 化，T2）。

以 mock ``litellm.cost_per_token`` 锁定计价行为，避免断言锁死 litellm 真实价格表的
版本性变动（符合「不依赖具体费率数值」的稳健做法）；私有模型 id 已随 AI-01 移除，
测试统一使用 litellm 官方 key 形态与不可计价兜底覆盖。
"""

from datetime import date
from typing import cast
from unittest.mock import patch

from services.ai_service.pricing import PRICING_UPDATED, _USD_TO_CNY_RATE, estimate_cost


def test_known_model_with_provider_prefix():
    """provider/model → litellm 计价并乘汇率换算为人民币。"""
    # litellm 返回每 token 美元 (in, out)；换算后 round 4 位。
    with patch("litellm.cost_per_token", return_value=(0.00001, 0.00002)):
        cost = cast(float, estimate_cost("deepseek/deepseek-chat", input_tokens=1_000_000, output_tokens=500_000))
    assert cost > 0
    assert cost == round((0.00001 + 0.00002) * _USD_TO_CNY_RATE, 4)


def test_known_model_bare_id():
    """裸官方 model id 走同一路径（litellm 逐级剥前缀匹配）。"""
    with patch("litellm.cost_per_token", return_value=(0.00001, 0.00002)):
        cost = cast(float, estimate_cost("deepseek-chat", 500_000, 250_000))
    assert cost == round((0.00001 + 0.00002) * _USD_TO_CNY_RATE, 4)


def test_unknown_model_returns_none():
    """模型不在价格表（cost_per_token 抛异常）→ None，不猜价格（R21）。"""
    with patch("litellm.cost_per_token", side_effect=Exception("model not in cost map")):
        assert estimate_cost("deepseek/deepseek-unknown", 100, 100) is None


def test_free_model_returns_zero_not_none():
    """免费模型（已计价、返 0.0）→ 返回 0.0 而非 None，属已计价，不算不可计。"""
    with patch("litellm.cost_per_token", return_value=(0.0, 0.0)):
        assert estimate_cost("deepseek/deepseek-chat", 0, 0) == 0.0


def test_negative_tokens_returns_none():
    """负 token 视为数据异常，返回 None（成本未知），不调用 litellm。"""
    with patch("litellm.cost_per_token") as m:
        assert estimate_cost("deepseek/deepseek-chat", -1, 100) is None
        assert estimate_cost("deepseek/deepseek-chat", 100, -1) is None
        m.assert_not_called()


def test_usd_rate_note_lazy_complete():
    """汇率常量为 `# NOTE(lazy)` 标记，三要素齐全（量化 threshold 常量须可升级）。"""
    import inspect

    src = inspect.getsource(__import__("services.ai_service.pricing", fromlist=["x"]))
    # 以常量行为锚点，求得该行并断言含 ceiling/upgrade 三要素。
    line = next(ln for ln in src.splitlines() if "_USD_TO_CNY_RATE =" in ln)
    assert "ceiling" in line and "upgrade" in line


def test_pricing_updated_is_date():
    """价格时效性锚点是已过去的日期。"""
    assert isinstance(PRICING_UPDATED, date)
    assert date.today() >= PRICING_UPDATED
