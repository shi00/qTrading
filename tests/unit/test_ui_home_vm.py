"""
Tests for HomeViewModel.

验证首页视图模型的数据加载、状态管理、事件订阅等核心功能。
所有测试使用 Mock 隔离外部依赖，不连接真实数据库或服务。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ui.viewmodels.home_view_model import HomeViewModel, MarketIndexRow, NewsRow

pytestmark = pytest.mark.unit


class TestHomeViewModelInit:
    """测试初始化"""

    def test_init_state(self):
        """初始状态"""
        vm = HomeViewModel()

        assert vm.state.news_page == 0
        assert vm.PAGE_SIZE == 20
        assert vm.state.has_more_news is False
        assert vm.state.is_loading_more is False
        assert vm.state.news_rows == ()
        assert vm.state.market_indices == ()
        assert vm.state.market_hsgt.value == "--"
        assert vm.state.market_hot_concepts == ()
        assert vm.state.market_date == "--"
        assert vm.state.market_stale is False

    def test_init_subscribes_services(self):
        """init 订阅 NewsSubscriptionService / MarketDataService"""
        vm = HomeViewModel()

        with (
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_news_svc,
            patch("ui.viewmodels.home_view_model.MarketDataService") as mock_market_svc,
        ):
            mock_news_svc.return_value.add_listener = MagicMock()
            mock_market_svc.return_value.add_listener = MagicMock()

            vm.init()

            mock_news_svc.return_value.add_listener.assert_called_once()
            mock_market_svc.return_value.add_listener.assert_called_once()

    def test_subscribe_returns_unsubscribe_and_removes_callback(self):
        """subscribe 返回取消订阅函数,调用后移除 callback"""
        vm = HomeViewModel()
        callback = MagicMock()
        unsubscribe = vm.subscribe(callback)
        assert callable(unsubscribe)

        vm._set_state(has_more_news=True)
        callback.assert_called_once()

        unsubscribe()
        callback.reset_mock()
        vm._set_state(has_more_news=False)
        callback.assert_not_called()

    def test_dispose_clears_subscribers(self):
        """dispose 清空 subscribers 列表"""
        vm = HomeViewModel()
        callback = MagicMock()
        vm.subscribe(callback)

        with (
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService"),
            patch("ui.viewmodels.home_view_model.MarketDataService"),
        ):
            vm.dispose()

        # dispose 后 _notify 不应再调用 callback
        callback.reset_mock()
        vm._set_state(has_more_news=True)
        callback.assert_not_called()


class TestHomeViewModelNewsAlertListener:
    """测试新闻告警监听注册/退订 (P2-2: View 经 VM 命令转发, 不直调 NewsSubscriptionService)."""

    def test_register_news_alert_listener_delegates_to_service(self):
        """register_news_alert_listener 转发到 NewsSubscriptionService.add_listener(is_alert=True)."""
        vm = HomeViewModel()
        callback = MagicMock()

        with patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_svc:
            mock_svc.return_value.add_listener = MagicMock()

            vm.register_news_alert_listener(callback)

            mock_svc.return_value.add_listener.assert_called_once_with(callback, is_alert=True)

    def test_unregister_news_alert_listener_delegates_to_service(self):
        """unregister_news_alert_listener 转发到 NewsSubscriptionService.remove_listener(is_alert=True)."""
        vm = HomeViewModel()
        callback = MagicMock()

        with patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_svc:
            mock_svc.return_value.remove_listener = MagicMock()

            vm.unregister_news_alert_listener(callback)

            mock_svc.return_value.remove_listener.assert_called_once_with(callback, is_alert=True)

    def test_register_and_unregister_round_trip(self):
        """注册 + 退订闭环: 同一 callback 经两个命令转发到 service."""
        vm = HomeViewModel()
        callback = MagicMock()

        with patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_svc:
            mock_svc.return_value.add_listener = MagicMock()
            mock_svc.return_value.remove_listener = MagicMock()

            vm.register_news_alert_listener(callback)
            vm.unregister_news_alert_listener(callback)

            mock_svc.return_value.add_listener.assert_called_once_with(callback, is_alert=True)
            mock_svc.return_value.remove_listener.assert_called_once_with(callback, is_alert=True)


class TestHomeViewModelMarketData:
    """测试市场数据加载"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        return HomeViewModel()

    @pytest.mark.asyncio
    async def test_load_market_data_with_cache(self, vm):
        """从缓存加载市场数据"""
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [{"name": "AI", "change": "+5%", "color": "red"}],
            "date": "2024-03-21",
            "stale": False,
        }

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=mock_data)

            result = await vm.load_market_data()

            assert result == mock_data
            assert len(vm.state.market_indices) == 1
            assert vm.state.market_indices[0].value == "3000"
            assert vm.state.market_hsgt.value == "100亿"
            assert len(vm.state.market_hot_concepts) == 1
            assert vm.state.market_date == "2024-03-21"

    @pytest.mark.asyncio
    async def test_load_market_data_retry(self, vm):
        """市场数据重试逻辑"""
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            call_count = [0]

            def get_data():
                call_count[0] += 1
                if call_count[0] < 3:
                    return None
                return mock_data

            mock_svc.return_value.get_cached_data = get_data

            result = await vm.load_market_data()

            assert result == mock_data
            assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_load_market_data_empty(self, vm):
        """市场数据为空"""
        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=None)

            result = await vm.load_market_data()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_market_data(self, vm):
        """获取缓存市场数据"""
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=mock_data)

            result = await vm.get_cached_market_data()

            assert result == mock_data
            assert len(vm.state.market_indices) == 1


class TestHomeViewModelNewsData:
    """测试新闻数据加载"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        vm = HomeViewModel()
        vm.processor = MagicMock()
        vm.processor.cache = MagicMock()
        vm.processor.cache.get_market_news = AsyncMock()
        return vm

    @pytest.mark.asyncio
    async def test_refresh_news(self, vm):
        """刷新新闻"""
        mock_news = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20)],
                "content": [f"内容{i}" for i in range(20)],
            }
        )
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        result, has_more = await vm.refresh_news()

        assert len(result) == 20
        assert has_more is True
        assert vm.state.news_page == 0
        assert len(vm.state.news_rows) == 20

    @pytest.mark.asyncio
    async def test_refresh_news_empty(self, vm):
        """刷新新闻为空"""
        vm.processor.cache.get_market_news = AsyncMock(return_value=pd.DataFrame())

        result, has_more = await vm.refresh_news()

        assert result.empty
        assert has_more is False
        assert vm.state.news_rows == ()

    @pytest.mark.asyncio
    async def test_load_next_page(self, vm):
        """加载下一页"""
        page1 = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20)],
            }
        )
        page2 = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20, 40)],
            }
        )

        vm.processor.cache.get_market_news = AsyncMock(side_effect=[page1, page2])

        await vm.refresh_news()
        new_batch, has_more = await vm.load_next_page()

        assert len(new_batch) == 20
        assert has_more is True
        assert vm.state.news_page == 1
        assert len(vm.state.news_rows) == 40

    @pytest.mark.asyncio
    async def test_load_next_page_no_more(self, vm):
        """没有更多新闻"""
        page1 = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(10)],
            }
        )

        vm.processor.cache.get_market_news = AsyncMock(return_value=page1)

        await vm.refresh_news()
        new_batch, has_more = await vm.load_next_page()

        assert has_more is False

    @pytest.mark.asyncio
    async def test_load_next_page_while_loading(self, vm):
        """加载中时阻止重复加载"""
        vm._set_state(is_loading_more=True)

        result, has_more = await vm.load_next_page()

        assert result is None
        assert has_more is False

    @pytest.mark.asyncio
    async def test_load_next_page_generation_change(self, vm):
        """加载过程中刷新导致代际变更"""
        page1 = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20)],
            }
        )
        page2 = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20, 40)],
            }
        )

        vm.processor.cache.get_market_news = AsyncMock(return_value=page1)

        await vm.refresh_news()

        vm.processor.cache.get_market_news = AsyncMock(return_value=page2)

        task = asyncio.create_task(vm.load_next_page())

        await asyncio.sleep(0.01)
        vm._load_generation += 1

        await task

    @pytest.mark.asyncio
    async def test_fetch_news_batch_error(self, vm):
        """新闻获取错误"""
        vm.processor.cache.get_market_news = AsyncMock(side_effect=Exception("Database Error"))

        result = await vm._fetch_news_batch(0)

        assert result is None


class TestHomeViewModelStateManagement:
    """测试状态管理"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        vm = HomeViewModel()
        vm.processor = MagicMock()
        vm.processor.cache = MagicMock()
        return vm

    def test_clear_state(self, vm):
        """清除状态"""
        vm._set_state(
            has_more_news=True,
            news_page=5,
            news_rows=(NewsRow(content="test"),),
            market_indices=(MarketIndexRow(value="3000"),),
        )

        vm.clear_state()

        assert vm.state.has_more_news is False
        assert vm.state.news_page == 0
        assert vm.state.news_rows == ()
        assert vm.state.market_indices == ()
        assert vm.state.market_hsgt.value == "--"
        assert vm.state.market_hot_concepts == ()

    @pytest.mark.asyncio
    async def test_on_news_service_update_new_item(self, vm):
        """NEW_ITEM 事件: 前插新行到 news_rows"""
        from services.news_subscription_service import NewsUpdateType

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

        with patch("ui.viewmodels.home_view_model._news_item_to_row") as mock_to_row:
            mock_to_row.return_value = NewsRow(content="test")
            await vm._on_news_service_update(NewsUpdateType.NEW_ITEM, [{"content": "test"}])

        assert len(vm.state.news_rows) == 1
        assert vm.state.news_rows[0].content == "test"
        assert any(len(s.news_rows) == 1 for s in snapshots)

    @pytest.mark.asyncio
    async def test_on_news_service_update_tag_update(self, vm):
        """TAG_UPDATE 事件: 更新匹配行的 tags"""
        from services.news_subscription_service import NewsUpdateType

        vm._set_state(news_rows=(NewsRow(content="old", tags="old_tag"),))

        await vm._on_news_service_update(NewsUpdateType.TAG_UPDATE, {"content": "old", "tags": "new_tag"})

        assert vm.state.news_rows[0].tags == "new_tag"

    @pytest.mark.asyncio
    async def test_on_news_service_update_initial(self, vm):
        """INITIAL 事件: 触发 refresh_news 全量刷新"""
        from services.news_subscription_service import NewsUpdateType

        mock_news = pd.DataFrame({"content": [f"news{i}" for i in range(5)]})
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        await vm._on_news_service_update(NewsUpdateType.INITIAL)

        assert len(vm.state.news_rows) == 5

    def test_on_market_service_update_emits_state(self, vm):
        """市场服务更新: 更新 market_indices/hsgt/hot_concepts"""
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [{"name": "AI", "change": "+5%", "color": "red"}],
            "date": "2024-03-21",
            "stale": False,
        }
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=mock_data)
            vm._on_market_service_update()

        assert len(vm.state.market_indices) == 1
        assert vm.state.market_indices[0].value == "3000"
        assert vm.state.market_hsgt.value == "100亿"
        assert any(len(s.market_indices) == 1 for s in snapshots)

    @pytest.mark.asyncio
    async def test_on_news_service_update_without_subscribers(self, vm):
        """无 subscribers 时新闻服务更新不报错"""
        from services.news_subscription_service import NewsUpdateType

        with patch("ui.viewmodels.home_view_model._news_item_to_row") as mock_to_row:
            mock_to_row.return_value = NewsRow(content="test")
            await vm._on_news_service_update(NewsUpdateType.NEW_ITEM, [{"content": "test"}])

        assert len(vm.state.news_rows) == 1


class TestHomeViewModelInitData:
    """测试数据初始化"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        vm = HomeViewModel()
        vm.processor = MagicMock()
        vm.processor.init_data = AsyncMock()
        return vm

    @pytest.mark.asyncio
    async def test_init_data(self, vm):
        """初始化数据"""
        await vm.init_data()

        vm.processor.init_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_data_error(self, vm):
        """初始化数据错误"""
        vm.processor.init_data = AsyncMock(side_effect=Exception("Init Error"))

        with pytest.raises(Exception, match="Init Error"):
            await vm.init_data()


class TestHomeViewModelPagination:
    """测试分页逻辑"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        vm = HomeViewModel()
        vm.processor = MagicMock()
        vm.processor.cache = MagicMock()
        return vm

    @pytest.mark.asyncio
    async def test_pagination_exact_page_size(self, vm):
        """恰好一页数据"""
        mock_news = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(20)],
            }
        )
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        await vm.refresh_news()

        assert vm.state.has_more_news is True

    @pytest.mark.asyncio
    async def test_pagination_less_than_page_size(self, vm):
        """少于一页数据"""
        mock_news = pd.DataFrame(
            {
                "title": [f"新闻{i}" for i in range(15)],
            }
        )
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        await vm.refresh_news()

        assert vm.state.has_more_news is False

    @pytest.mark.asyncio
    async def test_pagination_more_than_page_size(self, vm):
        """多页数据"""
        page1 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20)]})
        page2 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20, 40)]})
        page3 = pd.DataFrame({"title": [f"新闻{i}" for i in range(40, 55)]})

        vm.processor.cache.get_market_news = AsyncMock(side_effect=[page1, page2, page3])

        await vm.refresh_news()
        assert vm.state.has_more_news is True

        await vm.load_next_page()
        assert vm.state.has_more_news is True

        await vm.load_next_page()
        assert vm.state.has_more_news is False

    @pytest.mark.asyncio
    async def test_pagination_offset_calculation(self, vm):
        """分页偏移计算"""
        mock_news = pd.DataFrame({"title": ["test"]})
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        await vm._fetch_news_batch(0)
        vm.processor.cache.get_market_news.assert_called_with(limit=20, offset=0)

        await vm._fetch_news_batch(1)
        vm.processor.cache.get_market_news.assert_called_with(limit=20, offset=20)

        await vm._fetch_news_batch(5)
        vm.processor.cache.get_market_news.assert_called_with(limit=20, offset=100)


class TestHomeViewModelConcurrency:
    """测试并发控制"""

    @pytest.fixture
    def vm(self):
        """创建视图模型实例"""
        vm = HomeViewModel()
        vm.processor = MagicMock()
        vm.processor.cache = MagicMock()
        return vm

    @pytest.mark.asyncio
    async def test_concurrent_refresh(self, vm):
        """并发刷新"""
        mock_news = pd.DataFrame({"title": ["test"]})
        vm.processor.cache.get_market_news = AsyncMock(return_value=mock_news)

        results = await asyncio.gather(
            vm.refresh_news(),
            vm.refresh_news(),
        )

        assert all(r[0] is not None for r in results)

    @pytest.mark.asyncio
    async def test_load_generation_invalidation(self, vm):
        """代际失效机制"""
        page1 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20)]})
        page2 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20, 40)]})

        vm.processor.cache.get_market_news = AsyncMock(side_effect=[page1, page2])

        await vm.refresh_news()
        gen1 = vm._load_generation

        await vm.refresh_news()
        gen2 = vm._load_generation

        assert gen2 == gen1 + 1
