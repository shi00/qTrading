"""
Tests for NewsFetcher.

验证新闻获取功能，包括股票新闻、全球新闻、美股动态和热门概念。
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.external.news_fetcher import NewsFetcher


class TestGetStockNews(unittest.TestCase):
    """测试股票新闻获取"""

    def test_get_stock_news_empty_code(self):
        """空代码返回空列表"""

        async def run_test():
            result = await NewsFetcher.get_stock_news("")
            self.assertEqual(result, [])

        asyncio.run(run_test())

    def test_get_stock_news_none_code(self):
        """None 代码返回空列表"""

        async def run_test():
            result = await NewsFetcher.get_stock_news(None)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_stock_news_cninfo_success(self, mock_pool):
        """巨潮公告成功"""
        mock_future = MagicMock()
        mock_future.result.return_value = [
            {
                "title": "业绩预告",
                "publish_time": "2024-03-15 00:00:00",
                "source": "巨潮公告",
            },
            {
                "title": "年报披露",
                "publish_time": "2024-03-10 00:00:00",
                "source": "巨潮公告",
            },
        ]

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(
            return_value=[
                {
                    "title": "业绩预告",
                    "publish_time": "2024-03-15 00:00:00",
                    "source": "巨潮公告",
                },
                {
                    "title": "年报披露",
                    "publish_time": "2024-03-10 00:00:00",
                    "source": "巨潮公告",
                },
            ]
        )
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["source"], "巨潮公告")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_stock_news_timeout(self, mock_pool):
        """超时返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
            self.assertEqual(result, [])

        asyncio.run(run_test())


class TestGetLatestGlobalNews(unittest.TestCase):
    """测试全球新闻获取"""

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_global_news_success(self, mock_pool):
        """成功获取全球新闻"""
        mock_df = pd.DataFrame(
            {
                "标题": ["美联储加息", "经济数据公布"],
                "内容": ["美联储宣布加息25个基点", "最新经济数据出炉"],
                "发布时间": ["2024-03-15 10:30:00", "2024-03-15 09:00:00"],
            }
        )

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_df)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(len(result), 2)

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_global_news_empty(self, mock_pool):
        """空数据返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=pd.DataFrame())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_global_news_none(self, mock_pool):
        """None 数据返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=None)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_global_news_runtime_error(self, mock_pool):
        """RuntimeError 返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=RuntimeError("Pool error"))
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())


class TestGetUSMajorMoves(unittest.TestCase):
    """测试美股动态获取"""

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_us_moves_success(self, mock_pool):
        """成功获取美股动态"""
        mock_data = [
            {"name": "NVDA", "cname": "英伟达", "chg": "2.5"},
            {"name": "TSLA", "cname": "特斯拉", "chg": "-1.2"},
            {"name": "AAPL", "cname": "苹果", "chg": "0.5"},
        ]

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_data)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertIn("NVDA", result)

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_us_moves_empty(self, mock_pool):
        """空数据返回默认消息"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=[])
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertEqual(result, "Global data unavailable.")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_us_moves_none(self, mock_pool):
        """None 数据返回默认消息"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=None)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertEqual(result, "Global data unavailable.")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_us_moves_error(self, mock_pool):
        """错误返回错误消息"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=Exception("Network error"))
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertEqual(result, "Global data error.")

        asyncio.run(run_test())


class TestGetHotConcepts(unittest.TestCase):
    """测试热门概念获取"""

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_hot_concepts_success(self, mock_pool):
        """成功获取热门概念"""
        mock_df = pd.DataFrame(
            {
                "板块": ["人工智能", "新能源", "芯片"],
                "涨跌幅": [3.5, 2.1, -1.5],
            }
        )

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_df)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0]["name"], "人工智能")
            self.assertEqual(result[0]["color"], "red")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_hot_concepts_with_green(self, mock_pool):
        """下跌概念显示绿色"""
        mock_df = pd.DataFrame(
            {
                "板块": ["房地产", "银行"],
                "涨跌幅": [-2.5, -0.5],
            }
        )

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_df)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(result[0]["color"], "green")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_hot_concepts_empty(self, mock_pool):
        """空数据返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=pd.DataFrame())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_hot_concepts_none(self, mock_pool):
        """None 数据返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=None)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_get_hot_concepts_error(self, mock_pool):
        """错误返回空列表"""
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=Exception("API error"))
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(result, [])

        asyncio.run(run_test())


class TestNewsFetcherEdgeCases(unittest.TestCase):
    """测试边界条件"""

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_concepts_with_nan_values(self, mock_pool):
        """NaN 涨跌幅处理"""
        import numpy as np

        mock_df = pd.DataFrame(
            {
                "板块": ["测试板块"],
                "涨跌幅": [np.nan],
            }
        )

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_df)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["change"], "0.00%")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    def test_concepts_missing_column(self, mock_pool):
        """缺少涨跌幅列"""
        mock_df = pd.DataFrame(
            {
                "板块": ["测试板块"],
            }
        )

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(return_value=mock_df)
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_hot_concepts(limit=8)
            self.assertEqual(len(result), 1)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
