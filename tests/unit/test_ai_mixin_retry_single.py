"""AIStrategyMixin.retry_single 与 analyze_one 的 on_card_error 路径单元测试。

覆盖 UX-2.3 单股重试机制（覆盖率补强，base=origin/main 未覆盖行）：
- retry_single: L726-787（52 行）
- analyze_one: L608（dp.is_cancelled 分支）/ L635（res is None 软失败）/ L643（异常分支）

测试范式参考 tests/unit/test_ai_mixin.py 的 TestRunAiAnalysis。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.i18n import I18n
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext

pytestmark = pytest.mark.unit


# --- Fixtures & Helpers ---


class ConcreteStrategy(AIStrategyMixin):
    """测试用具体策略（与 test_ai_mixin.py 一致）。"""

    key = "test_strategy"

    def __init__(self):
        super().__init__()

    def get_ai_context(self, row):
        return f"Test context for {row.get('ts_code', '?')}"


def _make_prefetched(news_tasks: dict | None = None, news_as_of=None) -> PreFetchedContext:
    """构造 PreFetchedContext，含可选 news_tasks。"""
    return PreFetchedContext(
        news_tasks=news_tasks or {},
        news_as_of=news_as_of,
    )


def _make_candidates_df(name: str = "贵州茅台", ts_code: str = "600519.SH") -> pd.DataFrame:
    """构造单股 candidates DataFrame。"""
    return pd.DataFrame({"ts_code": [ts_code], "name": [name], "close": [1500.0]})


def _make_mock_dp_with_cache(*, is_cancelled: bool = False) -> MagicMock:
    """与 test_ai_mixin.py 的 _make_mock_dp 一致，含完整 cache mock。"""
    dp = MagicMock()
    dp.is_cancelled = MagicMock(return_value=is_cancelled)
    dp.cache = MagicMock()
    dp.cache.get_concepts = AsyncMock(return_value={})
    dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_top_list = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_northbound = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_top_inst_batch = AsyncMock(return_value=pd.DataFrame())
    dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
    dp.cache.get_macro_economy = AsyncMock(return_value=None)
    dp.cache.get_shibor_latest = AsyncMock(return_value=None)
    dp.get_latest_trade_date = AsyncMock(return_value="20240118")
    dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
    return dp


@pytest.fixture(autouse=True)
def _mock_ai_external_acknowledged_default_true():
    """与 test_ai_mixin.py 一致：默认 AI 外发已确认。"""
    with patch(
        "strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged",
        return_value=True,
    ):
        yield


# ============================================================================
# retry_single
# ============================================================================


class TestRetrySingleNoCachedState:
    """L726-730: _last_candidates_df / _last_prefetched 为 None 时调 on_card_error。"""

    @pytest.mark.asyncio
    async def test_no_cached_df_calls_on_card_error(self):
        s = ConcreteStrategy()
        s._last_candidates_df = None
        s._last_prefetched = _make_prefetched()
        on_card_error = MagicMock()
        context = {"on_card_error": on_card_error}

        await s.retry_single("贵州茅台", context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        args = on_card_error.call_args.args
        assert args[0] == "贵州茅台"
        assert isinstance(args[1], str) and args[1]

    @pytest.mark.asyncio
    async def test_no_cached_prefetched_calls_on_card_error(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = None
        on_card_error = MagicMock()
        context = {"on_card_error": on_card_error}

        await s.retry_single("贵州茅台", context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "贵州茅台"

    @pytest.mark.asyncio
    async def test_no_on_card_error_callback_is_safe(self):
        """context 无 on_card_error → 不抛异常（仅日志 warning）。"""
        s = ConcreteStrategy()
        s._last_candidates_df = None
        s._last_prefetched = None
        context = {}

        await s.retry_single("贵州茅台", context)


class TestRetrySingleStockNotFound:
    """L734-738: candidates_df 中找不到 stock_name 时调 on_card_error。"""

    @pytest.mark.asyncio
    async def test_stock_not_in_df_calls_on_card_error(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df(name="贵州茅台")
        s._last_prefetched = _make_prefetched()
        on_card_error = MagicMock()
        context = {"on_card_error": on_card_error}

        await s.retry_single("不存在的股票", context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "不存在的股票"

    @pytest.mark.asyncio
    async def test_match_by_ts_code(self):
        """mask 同时匹配 name 与 ts_code。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df(name="贵州茅台", ts_code="600519.SH")
        s._last_prefetched = _make_prefetched()
        on_result = MagicMock()
        context = {"on_result": on_result, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 80, "summary": "ok"})),
        ):
            await s.retry_single("600519.SH", context)

            on_result.assert_called_once()  # noqa: weak-assertion success path verified


class TestRetrySingleSuccessPath:
    """主路径（news_task 复用 / 成功调 on_result）。"""

    @pytest.mark.asyncio
    async def test_does_not_create_new_card(self):
        """P1-1: retry_single 不调用 on_card_start（避免与调用方卡片复用叠加产生重复卡）。

        卡片创建/复用由调用方（ScreenerViewModel.retry_single_stock）负责——它先把失败卡
        复用为占位卡；retry_single 只复用缓存重新分析并更新已有卡，不应再触发 on_card_start
        （start_stream_card 为追加语义，会新建一张同名卡）。
        """
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        on_card_start = MagicMock()
        context = {"on_card_start": on_card_start, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})),
        ):
            await s.retry_single("贵州茅台", context)

        on_card_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_calls_on_result_with_row(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        on_result = MagicMock()
        context = {"on_result": on_result, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 75, "summary": "good"})),
        ):
            await s.retry_single("贵州茅台", context)

        on_result.assert_called_once()  # noqa: weak-assertion params verified below
        row = on_result.call_args.args[0]
        assert row["name"] == "贵州茅台"
        assert row["ai_score"] == 75.0

    @pytest.mark.asyncio
    async def test_score_zero_terminates_placeholder_card(self):
        """I-1: score==0（无信号）时 _build_result_row 返回 None → 调 on_card_error 终结占位卡。

        调用方 retry_single_stock 已把失败卡转为 is_analyzing=True 占位卡；若此处不终结，
        卡片会永久停留在"分析中"且无重试按钮。on_card_error 将占位卡复位为错误状态。
        """
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        on_result = MagicMock()
        on_card_error = MagicMock()
        context = {"on_result": on_result, "on_card_error": on_card_error, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 0, "summary": "no signal"})),
        ):
            await s.retry_single("贵州茅台", context)

        on_result.assert_not_called()
        on_card_error.assert_called_once_with("贵州茅台", I18n.get("ai_card_analysis_failed"))

    @pytest.mark.asyncio
    async def test_no_on_result_callback_skips_call(self):
        """context 无 on_result → 不调 on_result（不抛异常）。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})),
        ):
            await s.retry_single("贵州茅台", context)


class TestRetrySingleNewsTaskReuse:
    """L752-764: news_task 复用分支（cancelled/done/normal/none）。"""

    @pytest.mark.asyncio
    async def test_news_task_cancelled_with_as_of_refetches(self):
        """L754-757: news_task 已 cancelled + news_as_of 存在 → 重新拉取。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        cancelled_task = asyncio.get_running_loop().create_future()
        cancelled_task.cancel()
        s._last_prefetched = _make_prefetched(
            news_tasks={"600519.SH": cancelled_task},
            news_as_of="20240118",
        )
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})),
            patch(
                "strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[{"title": "n"}])
            ) as mock_news,
        ):
            await s.retry_single("贵州茅台", context)

        mock_news.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_news_task_cancelled_without_as_of_empty_list(self):
        """L758-759: news_task cancelled + 无 news_as_of → news_list=[]。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        cancelled_task = asyncio.get_running_loop().create_future()
        cancelled_task.cancel()
        s._last_prefetched = _make_prefetched(
            news_tasks={"600519.SH": cancelled_task},
            news_as_of=None,
        )
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(
                s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})
            ) as mock_analyze,
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])) as mock_news,
        ):
            await s.retry_single("贵州茅台", context)

        mock_news.assert_not_awaited()
        assert mock_analyze.call_args.kwargs["news"] == []

    @pytest.mark.asyncio
    async def test_news_task_normal_awaited(self):
        """L760-762: news_task 未完成/正常完成 → await 复用。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()

        async def _return_news():
            return [{"title": "cached news"}]

        loop = asyncio.get_event_loop()
        normal_task = loop.create_task(_return_news())
        await normal_task

        s._last_prefetched = _make_prefetched(news_tasks={"600519.SH": normal_task})
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(
                s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})
            ) as mock_analyze,
        ):
            await s.retry_single("贵州茅台", context)

        assert mock_analyze.call_args.kwargs["news"] == [{"title": "cached news"}]

    @pytest.mark.asyncio
    async def test_no_news_task_with_as_of_refetches(self):
        """L763-764: news_tasks 无此 ts_code + news_as_of 存在 → 重新拉取。"""
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched(
            news_tasks={},
            news_as_of="20240118",
        )
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value={"score": 70, "summary": "ok"})),
            patch(
                "strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[{"title": "n"}])
            ) as mock_news,
        ):
            await s.retry_single("贵州茅台", context)

        mock_news.assert_awaited_once()


class TestRetrySingleResIsNone:
    """L774-777: _mixin_analyze_single 返回 None → on_card_error。"""

    @pytest.mark.asyncio
    async def test_res_none_calls_on_card_error(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        on_card_error = MagicMock()
        context = {"on_card_error": on_card_error, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value=None)),
        ):
            await s.retry_single("贵州茅台", context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "贵州茅台"


class TestRetrySingleExceptionHandling:
    """L781-786: CancelledError 传播（R2） / Exception 调 on_card_error。"""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_r2(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        context = {"data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(side_effect=asyncio.CancelledError())),
        ):
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion  # R2 合规必须 re-raise
                await s.retry_single("贵州茅台", context)

    @pytest.mark.asyncio
    async def test_exception_calls_on_card_error(self):
        s = ConcreteStrategy()
        s._last_candidates_df = _make_candidates_df()
        s._last_prefetched = _make_prefetched()
        on_card_error = MagicMock()
        context = {"on_card_error": on_card_error, "data_processor": MagicMock()}

        with (
            patch("strategies.ai_mixin.AIService"),
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(side_effect=RuntimeError("network down"))),
        ):
            await s.retry_single("贵州茅台", context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "贵州茅台"
        assert isinstance(on_card_error.call_args.args[1], str)


# ============================================================================
# analyze_one 的 on_card_error 路径（L608 / L635 / L643）
# 通过 run_ai_analysis 触发
# ============================================================================


class TestAnalyzeOneCardError:
    """analyze_one 内 on_card_error 调用路径（覆盖率补强 L608/L635/L643）。"""

    @pytest.mark.asyncio
    async def test_dp_cancelled_returns_none_no_card_error(self):
        """L608: dp.is_cancelled()=True → return None，不触发 on_card_error。"""
        s = ConcreteStrategy()
        dp = _make_mock_dp_with_cache(is_cancelled=True)
        on_card_error = MagicMock()
        context = {"data_processor": dp, "on_card_error": on_card_error}
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})

        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = True
            await s.run_ai_analysis(candidates, context)

        on_card_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_res_none_calls_on_card_error(self):
        """L635: _mixin_analyze_single 返回 None → on_card_error。"""
        s = ConcreteStrategy()
        dp = _make_mock_dp_with_cache(is_cancelled=False)
        on_card_error = MagicMock()
        context = {"data_processor": dp, "on_card_error": on_card_error, "trade_date": "20240118"}
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch.object(s, "_mixin_analyze_single", new=AsyncMock(return_value=None)),
        ):
            mock_ai.return_value.is_cloud_available.return_value = True
            mock_ai.return_value.analyze_stock = AsyncMock()
            await s.run_ai_analysis(candidates, context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "测试"

    @pytest.mark.asyncio
    async def test_analyze_single_exception_calls_on_card_error(self):
        """L643: _mixin_analyze_single 抛异常 → on_card_error。"""
        s = ConcreteStrategy()
        dp = _make_mock_dp_with_cache(is_cancelled=False)
        on_card_error = MagicMock()
        context = {"data_processor": dp, "on_card_error": on_card_error, "trade_date": "20240118"}
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch.object(
                s,
                "_mixin_analyze_single",
                new=AsyncMock(side_effect=RuntimeError("ai error")),
            ),
        ):
            mock_ai.return_value.is_cloud_available.return_value = True
            mock_ai.return_value.analyze_stock = AsyncMock()
            await s.run_ai_analysis(candidates, context)

        on_card_error.assert_called_once()  # noqa: weak-assertion params verified below
        assert on_card_error.call_args.args[0] == "测试"
        assert isinstance(on_card_error.call_args.args[1], str)
