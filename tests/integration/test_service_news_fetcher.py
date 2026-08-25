"""
Tests for NewsFetcher.

验证新闻获取功能，包括股票新闻、全球新闻、美股动态和热门概念。
"""

import asyncio


def _wire_http_get(mock_client, mock_response):
    """B16: requests.get -> httpx.AsyncClient mock 适配（httpx 0.28 async with）。

    mock_client 是 patch("httpx.AsyncClient") 的类 mock；AsyncClient(...) 构造返回
    mock_client.return_value（实例）。async with 需要实例的 __aenter__ 返回自身，
    get 为 AsyncMock 返回 mock_response。
    """
    instance = mock_client.return_value
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.get = AsyncMock(return_value=mock_response)
    return mock_response


import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.external.news_fetcher import NewsFetcher, _run_with_python_string_storage
import pytest


pytestmark = pytest.mark.integration


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
        mock_manager.run_async = AsyncMock(side_effect=TimeoutError())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
            self.assertEqual(result, [])

        asyncio.run(run_test())


class TestGetLatestGlobalNews(unittest.TestCase):
    """测试全球新闻获取（直连 CLS API）。"""

    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("httpx.AsyncClient")
    def test_get_global_news_success(self, mock_get, mock_pool):
        """成功获取全球新闻"""
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "roll_data": [
                    {
                        "title": "美联储加息",
                        "content": "美联储宣布加息25个基点",
                        "ctime": 1710469800,  # 2024-03-15 10:30:00 CST
                    },
                    {
                        "title": "经济数据公布",
                        "content": "最新经济数据出炉",
                        "ctime": 1710464400,  # 2024-03-15 09:00:00 CST
                    },
                ]
            }
        }
        _wire_http_get(mock_get, mock_resp)

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["title"], "美联储加息")
            self.assertEqual(result[0]["time"], "2024-03-15 10:30:00")

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("httpx.AsyncClient")
    def test_get_global_news_empty(self, mock_get, mock_pool):
        """空 roll_data 返回空列表"""
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"roll_data": []}}
        _wire_http_get(mock_get, mock_resp)

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("httpx.AsyncClient")
    def test_get_global_news_none_response(self, mock_get, mock_pool):
        """返回 None 时返回空列表"""
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status_code = 200
        mock_resp.json.return_value = None
        _wire_http_get(mock_get, mock_resp)

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())

    @patch("httpx.AsyncClient")
    def test_get_global_news_runtime_error(self, mock_get):
        """RuntimeError 返回空列表且不触发熔断"""
        import data.external.news_fetcher as nf_mod

        _wire_http_get(mock_get, MagicMock())
        mock_get.return_value.get.side_effect = RuntimeError("Pool error")

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])
            self.assertEqual(nf_mod._CLS_CONSECUTIVE_FAILURES, 0)

        asyncio.run(run_test())

    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("httpx.AsyncClient")
    def test_get_global_news_missing_structure(self, mock_get, mock_pool):
        """返回 JSON 缺少 data 键时返回空列表"""
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"unexpected": "structure"}
        _wire_http_get(mock_get, mock_resp)

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_pool.return_value = mock_manager

        async def run_test():
            result = await NewsFetcher.get_latest_global_news(limit=20)
            self.assertEqual(result, [])

        asyncio.run(run_test())


class TestGetUSMajorMoves(unittest.TestCase):
    """测试美股动态获取"""

    @patch("httpx.AsyncClient")
    def test_get_us_moves_success(self, mock_get):
        """成功获取美股动态"""
        import data.external.news_fetcher as nf_mod

        nf_mod._US_MOVES_CACHE.clear()

        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "100", "diff": "2.5", "chg": "2.5"}, {"name": "TSLA", "cname": "特斯拉", "price": "200", "diff": "-2.0", "chg": "-1.2"}]});'
        _wire_http_get(mock_get, mock_resp)

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertIn("NVDA", result)

        asyncio.run(run_test())

    @patch("httpx.AsyncClient")
    def test_get_us_moves_empty(self, mock_get):
        """空数据返回默认消息"""
        import data.external.news_fetcher as nf_mod

        nf_mod._US_MOVES_CACHE.clear()

        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": []});'
        _wire_http_get(mock_get, mock_resp)

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertIn("unavailable", result)

        asyncio.run(run_test())

    @patch("httpx.AsyncClient")
    def test_get_us_moves_none(self, mock_get):
        """None 数据返回默认消息"""
        import data.external.news_fetcher as nf_mod

        nf_mod._US_MOVES_CACHE.clear()

        # Sina JSONP 解析后 data 为空列表 -> 视为无数据
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": []});'
        _wire_http_get(mock_get, mock_resp)

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertIn("unavailable", result)

        asyncio.run(run_test())

    @patch("httpx.AsyncClient")
    def test_get_us_moves_error(self, mock_get):
        """错误返回错误消息"""
        import data.external.news_fetcher as nf_mod

        nf_mod._US_MOVES_CACHE.clear()

        _wire_http_get(mock_get, MagicMock())
        mock_get.return_value.get.side_effect = Exception("Network error")

        async def run_test():
            result = await NewsFetcher.get_us_major_moves()
            self.assertIn("unavailable", result)

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

    def test_run_with_python_string_storage_restores_global_option(self):
        """全局 pandas string_storage 应在调用后恢复。"""
        original = pd.options.mode.string_storage
        pd.options.mode.string_storage = "pyarrow"

        def _fetcher():
            self.assertEqual(pd.options.mode.string_storage, "python")
            return "ok"

        try:
            result = _run_with_python_string_storage(_fetcher)
            self.assertEqual(result, "ok")
            self.assertEqual(pd.options.mode.string_storage, "pyarrow")
        finally:
            pd.options.mode.string_storage = original


if __name__ == "__main__":
    unittest.main()
