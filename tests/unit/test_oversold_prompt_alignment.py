import datetime
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from typing import Any

from services.ai_service import AIService, STRATEGY_CONTEXT_MAX_LEN
from strategies.all_strategies import StrategyManager
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


def _get_user_prompt(service):
    messages = service._chat_completion.await_args.args[0]
    return next(m["content"] for m in messages if m["role"] == "user")


def _build_history_df(ts_code: str, days: int = 80) -> pd.DataFrame:
    rows = []
    start = datetime.date(2024, 1, 2)
    for idx in range(days):
        trade_date = (start + datetime.timedelta(days=idx)).strftime("%Y%m%d")
        close = 16.0 - idx * 0.05
        rows.append(
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "open": close + 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "vol": 1000 + idx * 10,
            }
        )
    return pd.DataFrame(rows)


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

    user_prompt = _get_user_prompt(service)
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
        "_vol_ratio_threshold": 1.5,
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

    user_prompt = _get_user_prompt(service)
    strategy_segment = user_prompt.split("<strategy_context>\n", 1)[1].split("\n</strategy_context>", 1)[0]

    assert "### turnover" in strategy_segment
    assert "### sector" in strategy_segment
    assert "### market" in strategy_segment
    assert "### support" in strategy_segment
    assert ("布林下轨" in strategy_segment) or ("VWAC" in strategy_segment)
    assert "...(truncated)" not in strategy_segment

    AIService._instance = None


def test_oversold_strategy_context_reflects_dynamic_ui_params():
    """超跌 strategy_context 应显式反映当前 UI 参数组合。"""
    strategy = OversoldStrategy()

    row = {
        "_rsi_period": 6,
        "rsi_6": 18.7,
        "_rsi_threshold": 25,
        "_vol_ratio_threshold": 2.2,
        "_rsi_feature_text": "近60日观察: 已连续 4 天处于超卖(<30)；距上次多头状态(>50)已历经 6 天 【恐慌急跌】",
    }

    strategy_ctx = strategy.get_ai_context(row)

    assert "当前策略参数: RSI周期=6, 超卖阈值=25, 量能判定阈值=2.2" in strategy_ctx
    assert "当前 RSI(6) = 18.7（阈值 < 25）" in strategy_ctx


@pytest.mark.asyncio
async def test_strategy_manager_oversold_pipeline_forwards_shared_context_flags():
    """真实经过 StrategyManager -> OversoldStrategy -> AIStrategyMixin -> AIService 的链路应转发 shared context 开关。"""
    service = _build_test_ai_service()
    service.analyze_stock = AsyncMock(wraps=service.analyze_stock)

    manager = StrategyManager()
    strategy = manager.get_strategy("oversold")

    assert strategy is not None

    candidates = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "close": 10.0,
                "industry": "银行",
                "pct_chg": -4.2,
                "rsi_14": 21.5,
            }
        ]
    )
    history_df = _build_history_df("000001.SZ")

    dp = MagicMock()
    dp._quality_tier = 2
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 4, 24))
    dp.get_stock_history = AsyncMock(return_value=history_df)
    dp.trade_calendar = MagicMock()
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2024, 3, 1))
    dp.cache = MagicMock()
    dp.cache.get_concepts = AsyncMock(return_value={})
    dp.cache.get_daily_quotes = AsyncMock(return_value=history_df)
    dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
    dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
    dp.cache.get_daily_indicators_bulk = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())

    context = {
        "data_processor": dp,
        "screening_data": candidates,
        "params": {"rsi_period": 14, "rsi_threshold": 30, "vol_ratio_threshold": 1.5},
    }

    with (
        patch.object(strategy, "_math_filter", AsyncMock(return_value=candidates.copy())),
        patch("strategies.ai_mixin.AIService", return_value=service),
        patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
        patch(
            "strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="US market noise")
        ) as mock_global_context,
        patch("data.persistence.review_manager.ReviewManager") as mock_review_manager,
        patch("strategies.ai_mixin.ConfigHandler.get_ai_max_candidates", return_value=5),
        patch.object(strategy, "_build_multi_period_financials", AsyncMock(return_value="财务数据不足")),
        patch.object(strategy, "_build_auxiliary_data_text", AsyncMock(return_value="无辅助数据")),
        patch.object(strategy, "_build_macro_context", AsyncMock(return_value="")),
        patch.object(strategy, "_build_history_text", return_value="近60日价格行为摘要"),
    ):
        result = await strategy.filter(context)

    assert len(result) == 1
    mock_global_context.assert_not_awaited()
    mock_review_manager.assert_not_called()

    analyze_call = service.analyze_stock.await_args
    assert analyze_call is not None
    assert analyze_call.args[3] == ""
    assert analyze_call.kwargs["history_context"] == ""
    assert analyze_call.kwargs["strategy_key"] == "oversold"
    assert analyze_call.kwargs["include_global_context"] is False
    assert analyze_call.kwargs["include_learning_context"] is False

    user_prompt = _get_user_prompt(service)

    assert "<global_context>" not in user_prompt
    assert "<history_context>" not in user_prompt
    assert "<strategy_context>" in user_prompt

    AIService._instance = None


@pytest.mark.asyncio
async def test_oversold_prompt_skips_shared_context_blocks():
    """超跌策略应通过策略能力开关跳过 shared context，而不是依赖 key 硬编码。"""
    service = _build_test_ai_service()
    strategy = StrategyManager().get_strategy("oversold")

    assert strategy is not None
    assert strategy.should_include_global_context() is False
    assert strategy.should_include_learning_context() is False

    with patch("services.ai_service.ConfigHandler.get_ai_system_prompt", return_value="test system prompt"):
        analyze_kwargs: dict[str, Any] = {
            "stock_info": {"ts_code": "000001.SZ", "name": "平安银行"},
            "tech_info": {"macd_signal": "BEARISH"},
            "news_list": [],
            "global_context": "US market noise",
            "history_context": "<history_context>few-shot bias</history_context>",
            "strategy_context": "超跌策略上下文",
            "strategy_key": strategy.key,
        }
        params = inspect.signature(service.analyze_stock).parameters
        if "include_global_context" in params:
            analyze_kwargs["include_global_context"] = strategy.should_include_global_context()
        if "include_learning_context" in params:
            analyze_kwargs["include_learning_context"] = strategy.should_include_learning_context()

        await service.analyze_stock(**analyze_kwargs)

    user_prompt = _get_user_prompt(service)

    assert "<global_context>" not in user_prompt
    assert "<history_context>" not in user_prompt
    assert "<strategy_context>" in user_prompt

    AIService._instance = None


@pytest.mark.asyncio
async def test_strategy_key_alone_no_longer_disables_shared_context():
    """仅凭 strategy_key 不应再触发 shared context 屏蔽。"""
    service = _build_test_ai_service()

    with patch("services.ai_service.ConfigHandler.get_ai_system_prompt", return_value="test system prompt"):
        await service.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
            tech_info={"macd_signal": "BEARISH"},
            news_list=[],
            global_context="US market noise",
            history_context="<history_context>few-shot bias</history_context>",
            strategy_context="超跌策略上下文",
            strategy_key="oversold",
        )

    user_prompt = _get_user_prompt(service)

    assert "<global_context>" in user_prompt
    assert "<history_context>" in user_prompt

    AIService._instance = None


@pytest.mark.asyncio
async def test_oversold_final_prompt_contains_dynamic_ui_params():
    """最终 prompt 中应保留超跌策略当前 UI 参数。"""
    service = _build_test_ai_service()
    strategy = OversoldStrategy()

    strategy_context = strategy.get_ai_context(
        {
            "_rsi_period": 21,
            "rsi_21": 27.3,
            "_rsi_threshold": 35,
            "_vol_ratio_threshold": 1.8,
            "_rsi_feature_text": "近60日观察: 已连续 3 天处于超卖(<30)；近60日内未回到多头状态(>50)",
        }
    )

    with patch("services.ai_service.ConfigHandler.get_ai_system_prompt", return_value="test system prompt"):
        await service.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
            tech_info={"macd_signal": "BEARISH"},
            news_list=[],
            strategy_context=strategy_context,
            strategy_key="oversold",
        )

    user_prompt = _get_user_prompt(service)
    strategy_segment = user_prompt.split("<strategy_context>\n", 1)[1].split("\n</strategy_context>", 1)[0]

    assert "当前策略参数: RSI周期=21, 超卖阈值=35, 量能判定阈值=1.8" in strategy_segment
    assert "当前 RSI(21) = 27.3（阈值 < 35）" in strategy_segment

    AIService._instance = None
