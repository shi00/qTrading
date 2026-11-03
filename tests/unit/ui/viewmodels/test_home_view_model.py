"""HomeViewModel clear_state 扩展 + set_load_error/set_loading + stop() 单元测试.

测试 VM state 命令的行为 (state-driven, 不依赖 Flet 渲染)。
- clear_state/set_load_error/set_loading 仅测命令, 不调用 init() (避免 service 订阅)。
- stop() 用 mock service 防重型实例化 (NewsSubscriptionService/MarketDataService 单例
  构造触发 AIService/litellm import, 见 home_view.py E2E 注释)。
subscribe/dispose 由 test_home_view.py 通过 FakeHomeViewModel 覆盖。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ui.viewmodels import Message
from ui.viewmodels.home_view_model import HomeViewModel

pytestmark = pytest.mark.unit


@pytest.fixture
def vm():
    """HomeViewModel with mocked DataProcessor (避免真实初始化)."""
    with patch("ui.viewmodels.home_view_model.DataProcessor"):
        return HomeViewModel()


@pytest.fixture
def vm_with_service_mocks(monkeypatch):
    """HomeViewModel with mocked DataProcessor + service singletons.

    防重型实例化: NewsSubscriptionService/MarketDataService 单例构造会触发
    AIService/litellm import 等副作用。stop() 测试仅验证 add_listener/
    remove_listener 调用契约, 不关心服务内部行为 (与既有 DataProcessor mock 一致)。
    """
    news_service = MagicMock()
    market_service = MagicMock()
    monkeypatch.setattr("ui.viewmodels.home_view_model.NewsSubscriptionService", lambda: news_service)
    monkeypatch.setattr("ui.viewmodels.home_view_model.MarketDataService", lambda: market_service)
    with patch("ui.viewmodels.home_view_model.DataProcessor"):
        vm = HomeViewModel()
    return vm, news_service, market_service


class TestClearStateExtended:
    """P1-3 批次 2: clear_state 扩展重置 is_loading_more + 新增 4 字段."""

    def test_clear_state_resets_is_loading_more_bug_fix(self, vm):
        """修复 bug: 原 clear_state 不重置 is_loading_more, 导致卡在 True."""
        vm._set_state(is_loading_more=True)
        assert vm.state.is_loading_more is True
        vm.clear_state()
        assert vm.state.is_loading_more is False

    def test_clear_state_resets_new_fields(self, vm):
        """clear_state 重置 P1-3 批次 2 新增的 4 字段."""
        vm._set_state(
            is_loading=True,
            has_market_data=True,
            has_news_data=True,
            load_error=Message("home_load_failed_title", {}),
        )
        vm.clear_state()
        assert vm.state.is_loading is False
        assert vm.state.has_market_data is False
        assert vm.state.has_news_data is False
        assert vm.state.load_error is None


class TestSetLoadError:
    """P1-3 批次 2: set_load_error 命令."""

    def test_set_load_error_sets_message(self, vm):
        msg = Message("home_load_failed_title", {})
        vm.set_load_error(msg)
        assert vm.state.load_error is msg

    def test_set_load_error_none_clears(self, vm):
        vm.set_load_error(Message("home_load_failed_title", {}))
        vm.set_load_error(None)
        assert vm.state.load_error is None


class TestSetLoading:
    """P1-3 批次 2: set_loading 命令."""

    def test_set_loading_true(self, vm):
        vm.set_loading(True)
        assert vm.state.is_loading is True

    def test_set_loading_false(self, vm):
        vm.set_loading(True)
        vm.set_loading(False)
        assert vm.state.is_loading is False


class TestStopServiceListeners:
    """Task 1: HomeViewModel.stop() 移除 service listener (与 init() 配对, 幂等).

    覆盖 home_view_model.py stop():
    - init() 后 stop(): 两 service 的 remove_listener 以对应回调调用
    - 幂等: 重复调用不抛错 (remove_listener 基于 set.remove + try/except KeyError)
    - init() → stop() → init(): listener 重新注册 (切回 active 恢复)

    HomeView 常驻 app_layout ft.Stack 永不卸载, 失活时经 effect cleanup 调
    stop() 退订, 否则被新闻/行情 push 反复重渲染。
    """

    def test_init_then_stop_removes_both_listeners(self, vm_with_service_mocks):
        """init() 后 stop(): 两 service 的 remove_listener 以对应回调调用."""
        vm, news_service, market_service = vm_with_service_mocks

        vm.init()
        news_service.add_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.add_listener.assert_called_once_with(vm._on_market_service_update)

        vm.stop()
        news_service.remove_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.remove_listener.assert_called_once_with(vm._on_market_service_update)

    def test_stop_is_idempotent(self, vm_with_service_mocks):
        """stop() 幂等: 重复调用不抛错 (remove_listener 重复删除安全)."""
        vm, _news_service, _market_service = vm_with_service_mocks

        vm.init()
        vm.stop()
        vm.stop()  # 幂等: 重复调用不抛错即通过

    def test_stop_then_init_reactivates_listeners(self, vm_with_service_mocks):
        """init() → stop() → init(): listener 重新注册 (切回 active 恢复)."""
        vm, news_service, market_service = vm_with_service_mocks

        vm.init()
        news_service.add_listener.reset_mock()
        market_service.add_listener.reset_mock()

        vm.stop()
        vm.init()  # 重新注册
        news_service.add_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.add_listener.assert_called_once_with(vm._on_market_service_update)


class TestDetectAiTagged:
    """Task 8.1: _detect_ai_tagged 检测新闻是否被 AI 打标."""

    def test_empty_tags_and_source_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "") is False
        assert _detect_ai_tagged("", None) is False
        assert _detect_ai_tagged(None, "") is False

    def test_ai_prefix_tag_returns_true(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("AI_LLM_科技", "") is True
        assert _detect_ai_tagged("AI,科技", "") is True
        assert _detect_ai_tagged("ai,科技", "") is True

    def test_non_ai_tag_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("科技,金融", "") is False
        assert _detect_ai_tagged("利好", "") is False

    def test_ai_source_returns_true(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "AI") is True
        assert _detect_ai_tagged("", "ai") is True

    def test_non_ai_source_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "CLS") is False
        assert _detect_ai_tagged("", "Tushare") is False


class _FakeMarketNewsDao:
    """隔离的 market_news DAO 替身: 稳定 id + keyset, 可控制失败/空页/取消."""

    def __init__(self, rows: list[dict]):
        self.rows = list(rows)
        self.fail = False
        self.cancel = False
        self.empty = False
        self.calls: list[dict] = []

    async def get_market_news(
        self,
        limit: int = 50,
        offset: int = 0,
        cursor_publish_time=None,
        cursor_id=None,
        suppress_errors: bool = True,
    ) -> pd.DataFrame:
        self.calls.append(
            {
                "offset": offset,
                "cursor_publish_time": cursor_publish_time,
                "cursor_id": cursor_id,
                "suppress_errors": suppress_errors,
            }
        )
        if self.cancel:
            raise asyncio.CancelledError()
        if self.fail:
            raise RuntimeError("db down")
        if self.empty:
            return pd.DataFrame()
        if cursor_publish_time is None:
            return pd.DataFrame(self.rows[offset : offset + limit])
        cursor_time = pd.to_datetime(cursor_publish_time).to_pydatetime()
        out: list[dict] = []
        for row in self.rows:
            row_time = pd.to_datetime(row["publish_time"]).to_pydatetime()
            if row_time < cursor_time or (row_time == cursor_time and row["id"] < cursor_id):
                out.append(row)
            if len(out) >= limit:
                break
        return pd.DataFrame(out)


def _news_rows_n(n: int) -> list[dict]:
    """构造 n 条按时间倒序、id 递减的新闻行（id=n..1）。"""
    return [{"id": i, "publish_time": f"2026-10-27 10:{i:02d}:00", "content": f"news{i}"} for i in range(n, 0, -1)]


class TestNewsFetchFailureProtocol:
    """VMD-F02: 读取失败不得伪装成无新闻/末页, 且保留旧窗口并可重试."""

    @pytest.fixture
    def vm(self):
        with patch("ui.viewmodels.home_view_model.DataProcessor"):
            view_model = HomeViewModel()
        view_model.processor = MagicMock()
        view_model.processor.cache = MagicMock()
        return view_model

    @pytest.mark.asyncio
    async def test_refresh_failure_preserves_window_and_publishes_error(self, vm):
        dao = _FakeMarketNewsDao(_news_rows_n(21))
        vm.processor.cache.market_dao = dao

        first, has_more = await vm.refresh_news()
        assert not first.empty
        assert has_more is True
        assert dao.calls[-1]["suppress_errors"] is False
        rows_before = vm.state.news_rows
        cursor_before = vm._news_cursor
        assert len(rows_before) == 20

        dao.fail = True
        result, has_more_on_failure = await vm.refresh_news()

        assert result is None
        assert has_more_on_failure is True
        assert vm.state.news_rows == rows_before
        assert vm._news_cursor == cursor_before
        assert vm.state.has_more_news is True
        assert vm.state.has_news_data is True
        assert vm.state.load_error is not None
        assert vm.state.load_error.key == "home_load_failed_title"

        dao.fail = False
        recovered, has_more_after = await vm.refresh_news()
        assert recovered is not None
        assert not recovered.empty
        assert has_more_after is True
        assert len(vm.state.news_rows) == 20
        assert vm.state.has_more_news is True
        # 恢复成功后清除本次新闻读取发布的错误
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_legal_empty_dataframe_clears_rows_and_terminates_pagination(self, vm):
        dao = _FakeMarketNewsDao(_news_rows_n(21))
        vm.processor.cache.market_dao = dao
        await vm.refresh_news()
        assert len(vm.state.news_rows) == 20
        assert vm.state.has_more_news is True

        dao.empty = True
        result, has_more = await vm.refresh_news()

        assert result is not None
        assert result.empty
        assert has_more is False
        assert vm.state.news_rows == ()
        assert vm.state.has_news_data is False
        assert vm.state.has_more_news is False
        assert vm._news_cursor is None
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_cancelled_refresh_is_not_treated_as_empty_page(self, vm):
        dao = _FakeMarketNewsDao(_news_rows_n(21))
        vm.processor.cache.market_dao = dao
        await vm.refresh_news()
        rows_before = vm.state.news_rows
        cursor_before = vm._news_cursor

        dao.cancel = True
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.refresh_news()
        assert isinstance(exc_info.value, asyncio.CancelledError)

        assert vm.state.news_rows == rows_before
        assert vm._news_cursor == cursor_before
        assert vm.state.has_more_news is True
        assert vm.state.has_news_data is True
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_pagination_failure_preserves_eligibility_and_retry_does_not_skip(self, vm):
        dao = _FakeMarketNewsDao(_news_rows_n(21))
        vm.processor.cache.market_dao = dao
        await vm.refresh_news()
        rows_before = vm.state.news_rows
        cursor_before = vm._news_cursor

        dao.fail = True
        result, has_more = await vm.load_next_page()

        assert result is None
        assert has_more is True
        assert vm.state.news_rows == rows_before
        assert vm._news_cursor == cursor_before
        assert vm.state.has_more_news is True
        assert vm.state.is_loading_more is False
        assert vm.state.load_error is not None

        dao.fail = False
        retried, retried_has_more = await vm.load_next_page()

        assert retried is not None
        assert not retried.empty
        assert retried_has_more is False
        assert len(vm.state.news_rows) == 21
        assert vm.state.news_rows[-1].news_id == 1
        # 重试从同一游标继续: 未跳项, 新请求仍使用严格失败语义
        assert dao.calls[-1]["cursor_id"] == cursor_before[1]
        assert dao.calls[-1]["suppress_errors"] is False
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_pagination_legal_empty_page_terminates_pagination(self, vm):
        dao = _FakeMarketNewsDao(_news_rows_n(20))
        vm.processor.cache.market_dao = dao
        await vm.refresh_news()
        rows_before = vm.state.news_rows
        assert vm.state.has_more_news is True

        dao.empty = True
        result, has_more = await vm.load_next_page()

        assert result is not None
        assert result.empty
        assert has_more is False
        assert vm.state.has_more_news is False
        assert vm.state.news_rows == rows_before
        assert vm.state.load_error is None


class TestNewsRowAiTagged:
    """Task 8.1: NewsRow.is_ai_tagged 通过 _news_item_to_row 正确设置."""

    def test_news_item_with_ai_tag_sets_is_ai_tagged(self):
        from ui.viewmodels.home_view_model import _news_item_to_row

        item = {"content": "test", "tags": "AI_LLM_科技", "source": "CLS"}
        with patch("data.cache.cache_manager.CacheManager.normalize_news_item", return_value=item):
            row = _news_item_to_row(item)
        assert row.is_ai_tagged is True

    def test_news_item_without_ai_tag(self):
        from ui.viewmodels.home_view_model import _news_item_to_row

        item = {"content": "test", "tags": "科技", "source": "CLS"}
        with patch("data.cache.cache_manager.CacheManager.normalize_news_item", return_value=item):
            row = _news_item_to_row(item)
        assert row.is_ai_tagged is False
