import pytest
from unittest.mock import AsyncMock, patch
import pandas as pd

from services.ai_service import AIService, STRATEGY_CONTEXT_MAX_LEN
from strategies.ai_mixin import PreFetchedContext
from strategies.oversold_strategy import OversoldStrategy
from strategies.strategy_prompts import STRATEGY_PROMPTS


def _build_test_ai_service():
    """构造一个纯本地测试用 AIService，避免依赖真实 provider 或外网。"""
    AIService._instance = None

    with (
        patch.object(AIService, "_configure_litellm", return_value=None),
        patch.object(AIService, "_setup_client", return_value=None),
        patch("services.ai_service.logger.isEnabledFor", return_value=False),
    ):
        service = AIService()

    service._is_cloud_configured = True
    service._litellm_config = {"api_key": "test-key"}
    service._chat_completion = AsyncMock(return_value={"score": 88})
    return service


def test_oversold_prompt_matches_current_injected_data():
    """超跌反弹提示词应与当前代码真实注入的数据粒度对齐。"""
    prompt = STRATEGY_PROMPTS["oversold"]

    # 正向断言：检查“摘要化注入”语义，而非绑定完整文案句子
    assert "价格行为摘要" in prompt
    assert ("近60个交易日" in prompt) or ("近60日" in prompt)
    assert "北向持股" in prompt
    assert "主力净流入" in prompt
    assert "全市场净流入" in prompt

    assert "大宗交易" not in prompt
    assert "散户净额" not in prompt
    assert "MACD信号（金叉/死叉/背离）" not in prompt
    assert "北向资金动向" not in prompt
    assert "60日K线数据（开/高/低/收/成交量）" not in prompt


@pytest.mark.asyncio
async def test_strategy_context_truncation_limit_is_relaxed():
    """strategy_context 截断上限应高于旧的 1000 字，避免超跌策略上下文过早被截断。"""
    service = _build_test_ai_service()

    with patch("services.ai_service.ConfigHandler.get_ai_system_prompt", return_value="test system prompt"):
        long_context = "S" * (STRATEGY_CONTEXT_MAX_LEN + 100)

        await service.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
            tech_info={"macd_signal": "BEARISH"},
            news_list=[],
            strategy_context=long_context,
        )

    messages = service._chat_completion.await_args.args[0]
    user_prompt = messages[1]["content"]
    strategy_segment = user_prompt.split("<strategy_context>\n", 1)[1].split("\n</strategy_context>", 1)[0]

    assert "...(truncated)" in strategy_segment
    assert len(strategy_segment) > 1000
    assert "S" * 1200 in strategy_segment

    AIService._instance = None


@pytest.mark.asyncio
async def test_oversold_runtime_strategy_context_keeps_all_core_blocks():
    """超跌策略运行时的 strategy_context 应能保留 turnover/sector/market/support 四块核心上下文。"""
    strategy = OversoldStrategy()

    row = {
        "ts_code": "000001.SZ",
        "name": "平安银行",
        "close": 12.0,
        "industry": "银行",
        "_rsi_period": 14,
        "rsi_14": 21.5,
        "_rsi_threshold": 30,
        "_rsi_feature_text": "超卖钝化，" * 130,
    }

    indicators_df = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": f"202403{day:02d}",
                "turnover_rate": 1.0 + day * 0.1,
            }
            for day in range(1, 21)
        ]
    )

    history_df = pd.DataFrame(
        [
            {
                "trade_date": f"202401{day:02d}" if day <= 31 else f"202402{day - 31:02d}",
                "open": 15.0 - day * 0.03,
                "high": 15.2 - day * 0.03,
                "low": 14.8 - day * 0.03,
                "close": 15.0 - day * 0.03,
                "vol": 1000 + day * 20,
            }
            for day in range(1, 60)
        ]
        + [
            {
                "trade_date": f"202403{day:02d}" if day <= 31 else f"202404{day - 31:02d}",
                "open": 13.2 - day * 0.02,
                "high": 13.4 - day * 0.02,
                "low": 13.0 - day * 0.02,
                "close": 13.2 - day * 0.02,
                "vol": 2200 + day * 30,
            }
            for day in range(1, 63)
        ]
    )

    prefetched = PreFetchedContext(
        indicators=indicators_df,
        sector_stats={"银行": {"count": 12, "up_count": 2, "down_count": 10, "avg_pct_chg": -1.8}},
        market_context={
            "000001.SH": {"pct_chg": -1.2, "ma20": 3100.0, "trend": "空头趋势"},
            "399001.SZ": {"pct_chg": -0.9, "ma20": 9800.0, "trend": "空头趋势"},
            "399006.SZ": {"pct_chg": -1.8, "ma20": 2100.0, "trend": "空头趋势"},
        },
        history={"000001.SZ": history_df},
    )

    strategy_ctx = strategy.get_ai_context(row)
    for name, builder in strategy._context_builders.items():
        block_text = builder(row, prefetched)
        if block_text:
            strategy_ctx += f"\n\n### {name}\n{block_text}"

    support_idx = strategy_ctx.index("### support")
    assert support_idx > 1000
    assert support_idx < STRATEGY_CONTEXT_MAX_LEN

    service = _build_test_ai_service()

    with patch("services.ai_service.ConfigHandler.get_ai_system_prompt", return_value="test system prompt"):
        await service.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
            tech_info={"macd_signal": "BEARISH"},
            news_list=[],
            strategy_context=strategy_ctx,
        )

    messages = service._chat_completion.await_args.args[0]
    user_prompt = messages[1]["content"]
    strategy_segment = user_prompt.split("<strategy_context>\n", 1)[1].split("\n</strategy_context>", 1)[0]

    assert "### turnover" in strategy_segment
    assert "### sector" in strategy_segment
    assert "### market" in strategy_segment
    assert "### support" in strategy_segment
    assert ("布林下轨" in strategy_segment) or ("VWAC" in strategy_segment)
    assert "...(truncated)" not in strategy_segment

    AIService._instance = None
