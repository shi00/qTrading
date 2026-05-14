"""
Tests for HomeViewModel.

验证首页视图模型的数据加载、状态管理、事件订阅等核心功能。
所有测试使用 Mock 隔离外部依赖，不连接真实数据库或服务。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ui.viewmodels.home_view_model import HomeViewModel


class TestHomeViewModelInit:
    """测试初始化"""

    def test_init_state(self):
        """初始状态"""
        vm = HomeViewModel()

        assert vm.news_page == 0
        assert vm.PAGE_SIZE == 20
        assert vm.has_more_news is False
        assert vm.is_loading_more is False
        assert vm.last_market_data == {}
        assert vm.news_data is None
        assert vm.on_news_update is None
        assert vm.on_market_update is None

    def test_init_with_callbacks(self):
        """带回调初始化"""
        vm = HomeViewModel()
        on_news = MagicMock()
        on_market = MagicMock()

        with (
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_news_svc,
            patch("ui.viewmodels.home_view_model.MarketDataService") as mock_market_svc,
        ):
            mock_news_svc.return_value.add_listener = MagicMock()
            mock_market_svc.return_value.add_listener = MagicMock()

            vm.init(on_news, on_market)

            assert vm.on_news_update == on_news
            assert vm.on_market_update == on_market

    def test_dispose(self):
        """销毁时取消订阅"""
        vm = HomeViewModel()

        with (
            patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as mock_news_svc,
            patch("ui.viewmodels.home_view_model.MarketDataService") as mock_market_svc,
        ):
            mock_news_svc.return_value.remove_listener = MagicMock()
            mock_market_svc.return_value.remove_listener = MagicMock()

            vm.dispose()

            mock_news_svc.return_value.remove_listener.assert_called_once()
            mock_market_svc.return_value.remove_listener.assert_called_once()


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
            "index_data": pd.DataFrame({"close": [3000.0]}),
            "last_update": "2024-03-21 15:00:00",
        }

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=mock_data)

            result = await vm.load_market_data()

            assert result == mock_data
            assert vm.last_market_data == mock_data

    @pytest.mark.asyncio
    async def test_load_market_data_retry(self, vm):
        """市场数据重试逻辑"""
        mock_data = {"index_data": pd.DataFrame()}

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
        mock_data = {"index_data": pd.DataFrame()}

        with patch("ui.viewmodels.home_view_model.MarketDataService") as mock_svc:
            mock_svc.return_value.get_cached_data = MagicMock(return_value=mock_data)

            result = await vm.get_cached_market_data()

            assert result == mock_data
            assert vm.last_market_data == mock_data


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
        assert vm.news_page == 0

    @pytest.mark.asyncio
    async def test_refresh_news_empty(self, vm):
        """刷新新闻为空"""
        vm.processor.cache.get_market_news = AsyncMock(return_value=pd.DataFrame())

        result, has_more = await vm.refresh_news()

        assert result is None
        assert has_more is False

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
        assert vm.news_page == 1
        assert len(vm.news_data) == 40

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
        vm.is_loading_more = True

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
        vm.last_market_data = {"test": "data"}
        vm.news_data = pd.DataFrame({"title": ["test"]})
        vm.has_more_news = True
        vm.news_page = 5

        vm.clear_state()

        assert vm.last_market_data == {}
        assert vm.news_data is None
        assert vm.has_more_news is False
        assert vm.news_page == 0

    def test_on_news_service_update(self, vm):
        """新闻服务更新回调"""
        callback = MagicMock()
        vm.on_news_update = callback

        vm._on_news_service_update("update_type", {"data": "test"})

        callback.assert_called_once_with("update_type", {"data": "test"})

    def test_on_market_service_update(self, vm):
        """市场服务更新回调"""
        callback = MagicMock()
        vm.on_market_update = callback

        vm._on_market_service_update()

        callback.assert_called_once()

    def test_on_news_service_update_no_callback(self, vm):
        """无回调时新闻服务更新"""
        vm.on_news_update = None

        vm._on_news_service_update("update_type", {"data": "test"})

    def test_on_market_service_update_no_callback(self, vm):
        """无回调时市场服务更新"""
        vm.on_market_update = None

        vm._on_market_service_update()


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

        assert vm.has_more_news is True

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

        assert vm.has_more_news is False

    @pytest.mark.asyncio
    async def test_pagination_more_than_page_size(self, vm):
        """多页数据"""
        page1 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20)]})
        page2 = pd.DataFrame({"title": [f"新闻{i}" for i in range(20, 40)]})
        page3 = pd.DataFrame({"title": [f"新闻{i}" for i in range(40, 55)]})

        vm.processor.cache.get_market_news = AsyncMock(side_effect=[page1, page2, page3])

        await vm.refresh_news()
        assert vm.has_more_news is True

        await vm.load_next_page()
        assert vm.has_more_news is True

        await vm.load_next_page()
        assert vm.has_more_news is False

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
