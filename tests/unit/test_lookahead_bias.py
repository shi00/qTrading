import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd


def test_ai_mixin_uses_context_trade_date_first():
    from strategies.ai_mixin import AIStrategyMixin

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"

    normalized = mixin._normalize_trade_date_for_cache("20240315")
    assert normalized == "20240315"


def test_ai_mixin_falls_back_to_latest_when_context_none():
    from strategies.ai_mixin import AIStrategyMixin

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"

    result = mixin._normalize_trade_date_for_cache(None)
    assert result is None


@patch("strategies.ai_mixin.AIService")
@patch("strategies.ai_mixin.NewsFetcher")
@patch("strategies.ai_mixin.I18n")
async def test_capital_data_uses_context_trade_date(mock_i18n, mock_news, mock_ai):
    from strategies.ai_mixin import AIStrategyMixin

    mock_ai_inst = MagicMock()
    mock_ai_inst.is_cloud_available.return_value = True
    mock_ai.return_value = mock_ai_inst
    mock_news.get_stock_news = AsyncMock(return_value=[])

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"
    mixin._history_cache = {}
    mixin.should_include_learning_context = MagicMock(return_value=False)
    mixin.should_include_global_context = MagicMock(return_value=False)
    mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
    mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

    cache = MagicMock()
    cache.get_concepts = AsyncMock(return_value={})
    cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

    dp = MagicMock()
    dp.cache = cache
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value="20240430")

    context = {"trade_date": "20240315", "data_processor": dp}
    candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    await mixin.run_ai_analysis(candidates_df, context)

    cache.get_moneyflow.assert_awaited_once_with(trade_date="20240315")
    cache.get_top_list.assert_awaited_once_with(trade_date="20240315")
    cache.get_northbound.assert_awaited_once_with(trade_date="20240315")
    dp.get_latest_trade_date.assert_not_awaited()


@patch("strategies.ai_mixin.AIService")
@patch("strategies.ai_mixin.NewsFetcher")
@patch("strategies.ai_mixin.I18n")
async def test_capital_data_falls_back_to_latest(mock_i18n, mock_news, mock_ai):
    from strategies.ai_mixin import AIStrategyMixin

    mock_ai_inst = MagicMock()
    mock_ai_inst.is_cloud_available.return_value = True
    mock_ai.return_value = mock_ai_inst
    mock_news.get_stock_news = AsyncMock(return_value=[])

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"
    mixin._history_cache = {}
    mixin.should_include_learning_context = MagicMock(return_value=False)
    mixin.should_include_global_context = MagicMock(return_value=False)
    mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
    mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

    cache = MagicMock()
    cache.get_concepts = AsyncMock(return_value={})
    cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

    dp = MagicMock()
    dp.cache = cache
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 4, 30))

    context = {"data_processor": dp}
    candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    await mixin.run_ai_analysis(candidates_df, context)

    dp.get_latest_trade_date.assert_awaited_once()
    cache.get_moneyflow.assert_awaited_once_with(trade_date="20240430")
    cache.get_top_list.assert_awaited_once_with(trade_date="20240430")
    cache.get_northbound.assert_awaited_once_with(trade_date="20240430")


def test_normalize_trade_date_handles_various_types():
    from strategies.ai_mixin import AIStrategyMixin

    assert AIStrategyMixin._normalize_trade_date_for_cache("20240315") == "20240315"
    assert AIStrategyMixin._normalize_trade_date_for_cache(datetime.date(2024, 3, 15)) == "20240315"
    assert AIStrategyMixin._normalize_trade_date_for_cache(pd.Timestamp("2024-03-15")) == "20240315"
    assert AIStrategyMixin._normalize_trade_date_for_cache(None) is None
    assert AIStrategyMixin._normalize_trade_date_for_cache("") is None


@patch("strategies.ai_mixin.AIService")
@patch("strategies.ai_mixin.NewsFetcher")
@patch("strategies.ai_mixin.I18n")
async def test_kline_history_end_date_aligned_with_context_trade_date(mock_i18n, mock_news, mock_ai):
    from strategies.ai_mixin import AIStrategyMixin

    mock_ai_inst = MagicMock()
    mock_ai_inst.is_cloud_available.return_value = True
    mock_ai.return_value = mock_ai_inst
    mock_news.get_stock_news = AsyncMock(return_value=[])

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"
    mixin._history_cache = {}
    mixin.should_include_learning_context = MagicMock(return_value=False)
    mixin.should_include_global_context = MagicMock(return_value=False)
    mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
    mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

    cache = MagicMock()
    cache.get_concepts = AsyncMock(return_value={})
    cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

    dp = MagicMock()
    dp.cache = cache
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value="20240430")

    context = {"trade_date": "20240315", "data_processor": dp}
    candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    await mixin.run_ai_analysis(candidates_df, context)

    all_calls = cache.get_daily_quotes.call_args_list
    call_kwargs = all_calls[-1].kwargs if all_calls else {}
    assert call_kwargs.get("end_date") == datetime.date(2024, 3, 15), (
        f"S-2: K-line end_date must equal context trade_date, got {call_kwargs.get('end_date')}"
    )
    start_date = call_kwargs.get("start_date")
    if start_date is not None:
        fixed_today = datetime.date(2026, 5, 15)
        with patch("datetime.date") as mock_date:
            mock_date.today.return_value = fixed_today
            mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
            now_based_start = mock_date.today() - datetime.timedelta(days=365 * 5 + 30)
        assert start_date < now_based_start, (
            f"S-2: start_date must be derived from end_date (2024-03-15), not get_now(); got {start_date}"
        )


@patch("strategies.ai_mixin.AIService")
@patch("strategies.ai_mixin.NewsFetcher")
@patch("strategies.ai_mixin.I18n")
async def test_kline_history_cache_key_includes_trade_date(mock_i18n, mock_news, mock_ai):
    from strategies.ai_mixin import AIStrategyMixin

    mock_ai_inst = MagicMock()
    mock_ai_inst.is_cloud_available.return_value = True
    mock_ai.return_value = mock_ai_inst
    mock_news.get_stock_news = AsyncMock(return_value=[])

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"
    mixin._history_cache = {}
    mixin.should_include_learning_context = MagicMock(return_value=False)
    mixin.should_include_global_context = MagicMock(return_value=False)
    mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
    mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

    cache = MagicMock()
    cache.get_concepts = AsyncMock(return_value={})
    cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

    dp = MagicMock()
    dp.cache = cache
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value="20240430")

    context = {"trade_date": "20240315", "data_processor": dp}
    candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    await mixin.run_ai_analysis(candidates_df, context)

    assert len(mixin._history_cache) == 1
    cache_key = list(mixin._history_cache.keys())[0]
    assert "20240315" in str(cache_key), f"S-2: cache_key must include trade_date, got {cache_key}"


@patch("strategies.ai_mixin.AIService")
@patch("strategies.ai_mixin.NewsFetcher")
@patch("strategies.ai_mixin.I18n")
async def test_kline_history_cache_key_changes_with_trade_date(mock_i18n, mock_news, mock_ai):
    """M-1: 不同 trade_date 不应命中同一个历史缓存键。"""
    from strategies.ai_mixin import AIStrategyMixin

    mock_ai_inst = MagicMock()
    mock_ai_inst.is_cloud_available.return_value = True
    mock_ai.return_value = mock_ai_inst
    mock_news.get_stock_news = AsyncMock(return_value=[])

    mixin = AIStrategyMixin.__new__(AIStrategyMixin)
    mixin.strategy_name = "test"
    mixin._history_cache = {}
    mixin.should_include_learning_context = MagicMock(return_value=False)
    mixin.should_include_global_context = MagicMock(return_value=False)
    mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
    mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

    cache = MagicMock()
    cache.get_concepts = AsyncMock(return_value={})
    cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

    dp = MagicMock()
    dp.cache = cache
    dp.is_cancelled.return_value = False
    dp.get_latest_trade_date = AsyncMock(return_value="20240430")

    candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})
    await mixin.run_ai_analysis(candidates_df, {"trade_date": "20240315", "data_processor": dp})
    await mixin.run_ai_analysis(candidates_df, {"trade_date": "20240316", "data_processor": dp})

    assert cache.get_daily_quotes.await_count == 2, (
        f"M-1: different trade_date should trigger separate history fetches, got {cache.get_daily_quotes.await_count}"
    )
