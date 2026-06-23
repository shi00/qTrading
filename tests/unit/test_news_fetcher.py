import asyncio
import datetime
import logging

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from core.i18n import I18n
from data.external.news_fetcher import (
    NewsFetcher,
    _run_with_python_string_storage,
    _US_MOVES_CACHE,
    _SINA_CONSECUTIVE_EMPTY,
    _SINA_CONSECUTIVE_FAILURES,
    _SINA_EMPTY_THRESHOLD,
)
from utils.time_utils import CST_TZ
import requests

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


@pytest.fixture(autouse=True)
def clean_global_caches():
    _US_MOVES_CACHE.clear()
    _SINA_CONSECUTIVE_EMPTY.clear()
    _SINA_CONSECUTIVE_EMPTY["concept"] = 0
    _SINA_CONSECUTIVE_EMPTY["us_api"] = 0
    _SINA_CONSECUTIVE_FAILURES["concept"] = 0
    # 重置 CLS 熔断器状态，防止测试间状态泄漏
    import data.external.news_fetcher as _nf_mod

    _nf_mod._CLS_CONSECUTIVE_FAILURES = 0
    _nf_mod._CLS_CIRCUIT_OPENED_AT = 0.0
    yield
    _US_MOVES_CACHE.clear()
    _SINA_CONSECUTIVE_EMPTY.clear()
    _SINA_CONSECUTIVE_EMPTY["concept"] = 0
    _SINA_CONSECUTIVE_EMPTY["us_api"] = 0
    _SINA_CONSECUTIVE_FAILURES["concept"] = 0
    _nf_mod._CLS_CONSECUTIVE_FAILURES = 0
    _nf_mod._CLS_CIRCUIT_OPENED_AT = 0.0


class TestRunWithPythonStringStorage:
    def test_returns_fetcher_result(self):
        assert _run_with_python_string_storage(lambda: 42) == 42

    def test_restores_string_storage(self):
        original = pd.options.mode.string_storage
        _run_with_python_string_storage(lambda: None)
        assert pd.options.mode.string_storage == original

    def test_restores_on_exception(self):
        original = pd.options.mode.string_storage

        def bad_fetcher():
            raise ValueError("test")

        with pytest.raises(ValueError):
            _run_with_python_string_storage(bad_fetcher)
        assert pd.options.mode.string_storage == original


class TestGetStockNews:
    @pytest.mark.asyncio
    async def test_empty_ts_code(self):
        result = await NewsFetcher.get_stock_news("")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_ts_code(self):
        result = await NewsFetcher.get_stock_news(None)
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_success(self, mock_ak, mock_run, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        df_cninfo = pd.DataFrame(
            {
                "代码": ["000001"],
                "简称": ["平安银行"],
                "公告标题": ["2024年半年度报告"],
                "公告时间": ["2024-08-30"],
                "公告链接": ["http://example.com"],
            }
        )
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = df_cninfo

        future = MagicMock()
        future.result.return_value = [
            {
                "title": "2024年半年度报告",
                "publish_time": "2024-08-30 00:00:00",
                "source": "巨潮公告",
            }
        ]
        mock_tpm_instance.run_async = AsyncMock(return_value=future)

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [coro.close(), []][1],
        ) as mock_wait:
            mock_wait.return_value = [
                {
                    "title": "2024年半年度报告",
                    "publish_time": "2024-08-30 00:00:00",
                    "source": "巨潮公告",
                }
            ]
            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
            assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_timeout(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=MagicMock())

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [
                coro.close(),
                (_ for _ in ()).throw(TimeoutError()),
            ][1],
        ):
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_asyncio_timeout_error(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=MagicMock())

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [
                coro.close(),
                (_ for _ in ()).throw(TimeoutError()),
            ][1],
        ):
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_dispatch_error(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("dispatch error"))

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [
                coro.close(),
                (_ for _ in ()).throw(Exception("dispatch error")),
            ][1],
        ):
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_fails_em_fallback(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo error")
        df_em = pd.DataFrame(
            {
                "新闻标题": ["银行股上涨"],
                "新闻内容": ["详细内容"],
                "新闻时间": ["2024-06-14 10:00:00"],
                "文章来源": ["东财新闻"],
            }
        )
        mock_ak.stock_news_em.return_value = df_em

        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [coro.close(), []][1],
        ) as mock_wait:
            mock_wait.return_value = [
                {
                    "title": "银行股上涨",
                    "publish_time": "2024-06-14 10:00:00",
                    "source": "东财新闻",
                }
            ]
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_both_fail_returns_empty(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo error")
        mock_ak.stock_news_em.side_effect = Exception("em error")

        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [coro.close(), []][1],
        ) as mock_wait:
            mock_wait.return_value = []
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    async def test_outer_exception(self, mock_run, mock_tpm):
        mock_run.side_effect = Exception("fatal error")
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance

        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [coro.close(), []][1],
        ) as mock_wait:
            mock_wait.return_value = []
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []


class TestGetLatestGlobalNews:
    """测试 get_latest_global_news —— 直连 CLS API + 熔断器。"""

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_data(self, mock_get, mock_tpm):
        """成功获取数据，ctime 秒级时间戳正确转换。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "重大新闻1", "content": "内容1", "ctime": 1718330400},
                    {"title": "重大新闻2", "content": "内容2", "ctime": 1718326800},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["title"] == "重大新闻1"
        assert result[0]["time"] == "2024-06-14 10:00:00"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_millisecond_ctime(self, mock_get, mock_tpm):
        """ctime 毫秒级时间戳自动检测并除以 1000。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "毫秒新闻", "content": "", "ctime": 1718330400000},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert len(result) == 1
        assert result[0]["time"] == "2024-06-14 10:00:00"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_missing_ctime(self, mock_get, mock_tpm):
        """ctime 缺失时回退到 get_now()。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "无时间戳", "content": "内容"},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert len(result) == 1
        assert result[0]["title"] == "无时间戳"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_invalid_ctime(self, mock_get, mock_tpm):
        """ctime 非法值时回退到 get_now()。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "非法时间", "content": "", "ctime": "not-a-number"},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_title_fallback_to_content(self, mock_get, mock_tpm):
        """title 缺失时回退到 content。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"content": "内容作为标题", "ctime": 1718330400},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert result[0]["title"] == "内容作为标题"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_empty_title_and_content_uses_i18n(self, mock_get, mock_tpm):
        """title 和 content 均空时使用 I18n 默认值。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "", "content": "", "ctime": 1718330400},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert result[0]["title"] == I18n.get("news_no_title")

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_empty_roll_data(self, mock_get, mock_tpm):
        """roll_data 为空列表时返回空列表。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"roll_data": []}}
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_missing_data_structure(self, mock_get, mock_tpm):
        """返回 JSON 缺少 data/roll_data 键时返回空列表。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": "structure"}
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_runtime_error_does_not_trigger_circuit_breaker(self, mock_tpm):
        """RuntimeError（基础设施错误）不递增熔断计数。"""
        import data.external.news_fetcher as nf_mod

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=RuntimeError("no pool"))
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result == []
        assert nf_mod._CLS_CONSECUTIVE_FAILURES == 0

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_requests_exception_increments_circuit_breaker(self, mock_get, mock_tpm):
        """requests 异常递增熔断计数。"""
        import data.external.news_fetcher as nf_mod

        mock_get.side_effect = requests.ConnectionError("network down")

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result == []
        assert nf_mod._CLS_CONSECUTIVE_FAILURES == 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_http_error_increments_circuit_breaker(self, mock_get, mock_tpm):
        """HTTP 4xx/5xx 递增熔断计数。"""
        import data.external.news_fetcher as nf_mod

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result == []
        assert nf_mod._CLS_CONSECUTIVE_FAILURES == 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_limit_truncates_results(self, mock_get, mock_tpm):
        """limit 参数截断结果数量。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"roll_data": [{"title": f"新闻{i}", "content": "", "ctime": 1718330400 + i} for i in range(10)]}
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_sorted_desc_by_time(self, mock_get, mock_tpm):
        """结果按时间降序排列。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "旧新闻", "content": "", "ctime": 1718326800},
                    {"title": "新新闻", "content": "", "ctime": 1718330400},
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert result[0]["title"] == "新新闻"
        assert result[1]["title"] == "旧新闻"


class TestGetUsMajorMoves:
    @pytest.mark.asyncio
    async def test_cached_result(self):
        _US_MOVES_CACHE["result"] = "NVDA: 2.5%, TSLA: -1.2%"
        result = await NewsFetcher.get_us_major_moves()
        assert result == "NVDA: 2.5%, TSLA: -1.2%"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_data(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "2.45"}, {"name": "TSLA", "cname": "特斯拉", "price": "200.0", "diff": "-2.0", "chg": "-1.0"}]});'
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {
                    "name": "NVDA",
                    "cname": "英伟达",
                    "price": "135.2",
                    "diff": "3.2",
                    "chg": "2.45",
                },
                {
                    "name": "TSLA",
                    "cname": "特斯拉",
                    "price": "200.0",
                    "diff": "-2.0",
                    "chg": "-1.0",
                },
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert "NVDA" in result

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_all_retries_fail(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("network error"))

        with patch("data.external.news_fetcher.asyncio.sleep", new_callable=AsyncMock):
            result = await NewsFetcher.get_us_major_moves()
            assert isinstance(result, str)
            assert "unavailable" in result or "error" in result.lower()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_jsonp_parse_failure(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = "invalid response without jsonp"
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_high_pct_movers(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = (
            'IO({"data": [{"name": "UNKNOWN", "cname": "未知", "price": "10.0", "diff": "0.5", "chg": "5.0"}]});'
        )
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {
                    "name": "UNKNOWN",
                    "cname": "未知",
                    "price": "10.0",
                    "diff": "0.5",
                    "chg": "5.0",
                },
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert "UNKNOWN" in result

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_no_giants_fallback(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "SMALL1", "cname": "小公司1", "price": "1.0", "diff": "0.01", "chg": "0.5"}, {"name": "SMALL2", "cname": "小公司2", "price": "2.0", "diff": "0.02", "chg": "0.3"}, {"name": "SMALL3", "cname": "小公司3", "price": "3.0", "diff": "0.03", "chg": "0.1"}, {"name": "SMALL4", "cname": "小公司4", "price": "4.0", "diff": "0.04", "chg": "0.2"}, {"name": "SMALL5", "cname": "小公司5", "price": "5.0", "diff": "0.05", "chg": "0.4"}]});'
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {"name": "SMALL1", "cname": "小公司1", "chg": "0.5"},
                {"name": "SMALL2", "cname": "小公司2", "chg": "0.3"},
                {"name": "SMALL3", "cname": "小公司3", "chg": "0.1"},
                {"name": "SMALL4", "cname": "小公司4", "chg": "0.2"},
                {"name": "SMALL5", "cname": "小公司5", "chg": "0.4"},
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_invalid_chg_value(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = (
            'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "invalid"}]});'
        )
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {
                    "name": "NVDA",
                    "cname": "英伟达",
                    "price": "135.2",
                    "diff": "3.2",
                    "chg": "invalid",
                },
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_json_decode_error(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = "IO(not valid json);"
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_exception_in_processing(self, mock_get, mock_tpm):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA"}]});'
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[{"name": "NVDA"}])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)


class TestGetHotConcepts:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_success(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "板块": ["人工智能", "芯片", "新能源"],
                "涨跌幅": [5.2, 3.1, -2.5],
            }
        )
        mock_ak.stock_sector_spot.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["name"] == "人工智能"
        assert result[0]["color"] == "red"
        assert result[2]["color"] == "green"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_empty_df(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_sector_spot.return_value = pd.DataFrame()
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_none_df_returns_empty(self, mock_tpm):
        """df is None means data source failure — returns []."""
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=None)

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_nan_change(self, mock_ak, mock_run, mock_tpm):
        import numpy as np

        df = pd.DataFrame(
            {
                "板块": ["概念1"],
                "涨跌幅": [np.nan],
            }
        )
        mock_ak.stock_sector_spot.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=1)
        assert len(result) == 1
        assert result[0]["change"] == "0.00%"
        assert result[0]["color"] == "grey"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_zero_change(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "板块": ["概念1"],
                "涨跌幅": [0.0],
            }
        )
        mock_ak.stock_sector_spot.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=1)
        assert result[0]["color"] == "grey"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_invalid_change_value(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "板块": ["概念1"],
                "涨跌幅": ["invalid"],
            }
        )
        mock_ak.stock_sector_spot.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=1)
        assert result[0]["change"] == "0.00%"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_no_name_column(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "板块": ["", "概念2"],
                "涨跌幅": [1.0, 2.0],
            }
        )
        mock_ak.stock_sector_spot.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert len(result) == 1
        assert result[0]["name"] == "概念2"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_sina_exception_returns_empty(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_sector_spot.side_effect = Exception("sina error")
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("sina error"))

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch(
        "data.external.news_fetcher._run_with_python_string_storage",
        side_effect=lambda f: f(),
    )
    @patch("data.external.news_fetcher.ak")
    async def test_general_exception_returns_empty(self, mock_ak, mock_run, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("general error"))

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_timeout_returns_empty(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=TimeoutError())

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_cancelled_error_propagates(self, mock_tpm):
        """CancelledError must always propagate (R2: graceful shutdown)."""
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        # Simulate wait_for raising CancelledError (which it does when the inner task is cancelled)
        with patch("asyncio.wait_for", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await NewsFetcher.get_hot_concepts()

    @pytest.mark.asyncio
    async def test_akshare_returns_list(self):
        """When akshare returns list instead of DataFrame, _ensure_dataframe normalizes it."""
        fetcher = NewsFetcher()
        list_data = [
            {"板块": "AI", "涨跌幅": 3.5},
            {"板块": "芯片", "涨跌幅": 2.1},
        ]
        with patch("data.external.news_fetcher.ak") as mock_ak:
            mock_ak.stock_sector_spot.return_value = list_data
            result = await fetcher.get_hot_concepts()
            # Should not raise AttributeError
            assert isinstance(result, list)


class TestNewsFetcherGetLatestGlobalNews:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_get_latest_global_news(self, mock_get, mock_tpm):
        """基本冒烟测试：返回 list 类型。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"roll_data": []}}
        mock_get.return_value = mock_response

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        result = await NewsFetcher.get_latest_global_news()
        assert isinstance(result, list)


class TestNewsFetcherGetStockNews:
    @pytest.mark.asyncio
    async def test_empty_ts_code(self):
        result = await NewsFetcher.get_stock_news("")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_ts_code(self):
        result = await NewsFetcher.get_stock_news(None)
        assert result == []


class TestNewsFetcherGetUsMajorMoves:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_get_us_major_moves_returns_list_or_str(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=None)
        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, (list, str))


class TestUsMajorMovesLookAheadGuard:
    @pytest.mark.asyncio
    async def test_historical_date_returns_empty(self):
        import datetime

        past_date = datetime.date(2024, 1, 1)
        result = await NewsFetcher.get_us_major_moves(as_of=past_date)
        assert result == ""

    @pytest.mark.asyncio
    async def test_none_as_of_fetches_normally(self):
        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            result = await NewsFetcher.get_us_major_moves(as_of=None)
            assert isinstance(result, (list, str))

    @pytest.mark.asyncio
    async def test_today_as_of_fetches_normally(self):
        import datetime

        today = datetime.date.today()
        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            result = await NewsFetcher.get_us_major_moves(as_of=today)
            assert isinstance(result, (list, str))

    @pytest.mark.asyncio
    async def test_datetime_as_of_converted_to_date(self):
        import datetime

        today_dt = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            result = await NewsFetcher.get_us_major_moves(as_of=today_dt)
            assert isinstance(result, (list, str))

    @pytest.mark.asyncio
    async def test_historical_datetime_returns_empty(self):
        import datetime

        past_dt = datetime.datetime(2024, 1, 1, 12, 0, 0)
        result = await NewsFetcher.get_us_major_moves(as_of=past_dt)
        assert result == ""


class TestGetHotConceptsTimeout:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_timeout_returns_empty(self, mock_tpm, mock_run):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=TimeoutError())

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_within_timeout_succeeds(self, mock_tpm, mock_run):
        df = pd.DataFrame(
            {
                "板块": ["AI", "芯片"],
                "涨跌幅": [3.0, -1.5],
            }
        )
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "green"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_zero_change_is_grey(self, mock_tpm, mock_run):
        df = pd.DataFrame(
            {
                "板块": ["平盘板块"],
                "涨跌幅": [0.0],
            }
        )
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert len(result) == 1
        assert result[0]["color"] == "grey"


class TestSinaConsecutiveEmptyAlert:
    def test_threshold_defined(self):
        assert _SINA_EMPTY_THRESHOLD >= 2

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_concept_empty_increments_counter(self, mock_tpm, mock_run):
        """Empty DataFrame (not None) should increment empty counter and return []."""
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert result == []
        assert _SINA_CONSECUTIVE_EMPTY["concept"] == 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_concept_success_resets_counter(self, mock_tpm, mock_run):
        _SINA_CONSECUTIVE_EMPTY["concept"] = 5
        df = pd.DataFrame({"板块": ["AI"], "涨跌幅": [3.0]})
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_hot_concepts(limit=3)
        assert len(result) == 1
        assert _SINA_CONSECUTIVE_EMPTY["concept"] == 0


class TestGetStockNewsDirectExecution:
    """Tests that exercise the _fetch/_fetch_locked inner functions directly."""

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_direct_success(self, mock_ak):
        df_cninfo = pd.DataFrame(
            {
                "代码": ["000001"],
                "简称": ["平安银行"],
                "公告标题": ["2024年半年度报告"],
                "公告时间": ["2024-08-30"],
                "公告链接": ["http://example.com"],
            }
        )
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = df_cninfo

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0]["source"] == "巨潮公告"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_empty_em_fallback_direct(self, mock_ak):
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame()
        df_em = pd.DataFrame(
            {
                "新闻标题": ["银行股上涨"],
                "新闻内容": ["详细内容"],
                "新闻时间": ["2024-06-14 10:00:00"],
                "文章来源": ["东财新闻"],
            }
        )
        mock_ak.stock_news_em.return_value = df_em

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0]["source"] == "东财新闻"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_both_layers_fail_direct(self, mock_ak):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo error")
        mock_ak.stock_news_em.side_effect = Exception("em error")

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ")
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_no_title_col_returns_empty(self, mock_ak):
        df_cninfo = pd.DataFrame({"col_a": [1], "col_b": [2]})
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = df_cninfo
        mock_ak.stock_news_em.return_value = pd.DataFrame()

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ")
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_positional_fallback_columns(self, mock_ak):
        df_cninfo = pd.DataFrame(
            {
                "代码": ["000001"],
                "简称": ["平安银行"],
                "col2": ["公告标题fallback"],
                "col3": ["2024-08-30"],
            }
        )
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = df_cninfo

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_em_news_content_fallback(self, mock_ak):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo error")
        df_em = pd.DataFrame(
            {
                "新闻内容": ["详细内容作为标题"],
                "发布时间": ["2024-06-14 10:00:00"],
                "文章来源": ["东财新闻"],
            }
        )
        mock_ak.stock_news_em.return_value = df_em

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
        assert isinstance(result, list)
        assert result[0]["title"] == "详细内容作为标题"

    @pytest.mark.asyncio
    async def test_market_import_fallback(self):
        with patch("data.external.news_fetcher.ak") as mock_ak:
            import akshare.stock_feature.stock_disclosure_cninfo as mod

            original_fn = getattr(mod, "stock_zh_a_disclosure_report_cninfo", None)
            if original_fn is not None:
                delattr(mod, "stock_zh_a_disclosure_report_cninfo")

            mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()

            with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
                mock_tpm_instance = MagicMock()
                mock_tpm.return_value = mock_tpm_instance
                mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

                result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

            if original_fn is not None:
                mod.stock_zh_a_disclosure_report_cninfo = original_fn


class TestGetLatestGlobalNewsDirectExecution:
    """通过 ThreadPoolManager side_effect 直接执行 _fetch_cls 的测试。"""

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_direct_success_ctime_seconds(self, mock_get):
        """秒级 ctime 直接执行成功。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "直接执行", "content": "内容", "ctime": 1718330400},
                ]
            }
        }
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_latest_global_news(limit=1)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "直接执行"
        assert result[0]["time"] == "2024-06-14 10:00:00"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_direct_success_ctime_milliseconds(self, mock_get):
        """毫秒级 ctime 自动检测并转换。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "roll_data": [
                    {"title": "毫秒", "content": "", "ctime": 1718330400000},
                ]
            }
        }
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_latest_global_news(limit=1)
        assert len(result) == 1
        assert result[0]["time"] == "2024-06-14 10:00:00"

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_direct_missing_data_key(self, mock_get):
        """返回 JSON 缺少 data 键时返回空列表。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"no_data": True}
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_latest_global_news(limit=1)
        assert result == []


class TestGetUsMajorMovesDirectExecution:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_fetch_direct_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "2.45"}, {"name": "TSLA", "cname": "特斯拉", "price": "200.0", "diff": "-2.0", "chg": "-1.0"}]});'
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert "NVDA" in result

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_empty_data_warning(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": []});'
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert _SINA_CONSECUTIVE_EMPTY["us_api"] >= 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_json_decode_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "IO(not valid json);"
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_invalid_jsonp_structure(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "no jsonp structure here"
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_consecutive_empty_threshold_error(self, mock_get):
        _SINA_CONSECUTIVE_EMPTY["us_api"] = _SINA_EMPTY_THRESHOLD - 1
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": []});'
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert _SINA_CONSECUTIVE_EMPTY["us_api"] >= _SINA_EMPTY_THRESHOLD

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_sina_consecutive_empty_threshold_logs_warning(self, mock_get, caplog):
        """Empty-data degradation threshold must log WARNING, not ERROR (CLAUDE.md §5.4)."""
        _SINA_CONSECUTIVE_EMPTY["us_api"] = _SINA_EMPTY_THRESHOLD - 1
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": []});'
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            with caplog.at_level(logging.DEBUG, logger="data.external.news_fetcher"):
                result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert _SINA_CONSECUTIVE_EMPTY["us_api"] >= _SINA_EMPTY_THRESHOLD
        degraded_records = [
            r
            for r in caplog.records
            if "Sina US API returned empty data" in r.getMessage() and "Data source may be degraded" in r.getMessage()
        ]
        assert degraded_records, "Expected a degraded data-source log record"
        assert all(r.levelno == logging.WARNING for r in degraded_records)
        assert not any(r.levelno == logging.ERROR for r in degraded_records)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.requests.get")
    async def test_us_moves_processing_exception(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA"}]});'
        mock_get.return_value = mock_resp

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            with patch(
                "data.external.news_fetcher.json.loads",
                side_effect=Exception("parse error"),
            ):
                result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)


class TestGetHotConceptsDirectExecution:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_sina_concept_exception_returns_empty(self, mock_ak):
        mock_ak.stock_sector_spot.side_effect = Exception("sina error")

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_hot_concepts(limit=3)
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_concept_consecutive_empty_threshold(self, mock_ak):
        _SINA_CONSECUTIVE_EMPTY["concept"] = _SINA_EMPTY_THRESHOLD - 1
        mock_ak.stock_sector_spot.return_value = pd.DataFrame()

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            result = await NewsFetcher.get_hot_concepts(limit=3)
        assert result == []
        assert _SINA_CONSECUTIVE_EMPTY["concept"] >= _SINA_EMPTY_THRESHOLD

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ak")
    async def test_concept_consecutive_empty_threshold_logs_warning(self, mock_ak, caplog):
        """Empty-data degradation threshold must log WARNING, not ERROR (CLAUDE.md §5.4)."""
        _SINA_CONSECUTIVE_EMPTY["concept"] = _SINA_EMPTY_THRESHOLD - 1
        mock_ak.stock_sector_spot.return_value = pd.DataFrame()

        with patch("data.external.news_fetcher.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())

            with caplog.at_level(logging.DEBUG, logger="data.external.news_fetcher"):
                result = await NewsFetcher.get_hot_concepts(limit=3)
        assert result == []
        assert _SINA_CONSECUTIVE_EMPTY["concept"] >= _SINA_EMPTY_THRESHOLD
        degraded_records = [
            r
            for r in caplog.records
            if "Concept boards data empty" in r.getMessage() and "Data source may be degraded" in r.getMessage()
        ]
        assert degraded_records, "Expected a degraded data-source log record"
        assert all(r.levelno == logging.WARNING for r in degraded_records)
        assert not any(r.levelno == logging.ERROR for r in degraded_records)


class TestEnsureDataframe:
    """Tests for the _ensure_dataframe() helper that normalizes akshare return values."""

    def test_none_input(self):
        from data.external.news_fetcher import _ensure_dataframe

        result = _ensure_dataframe(None, source="test")
        assert result is None

    def test_dataframe_input(self):
        from data.external.news_fetcher import _ensure_dataframe

        df = pd.DataFrame({"a": [1]})
        result = _ensure_dataframe(df, source="test")
        assert result is df

    def test_list_of_dicts_input(self):
        from data.external.news_fetcher import _ensure_dataframe

        data = [{"a": 1}, {"a": 2}]
        result = _ensure_dataframe(data, source="test")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result["a"]) == [1, 2]

    def test_empty_list_input(self):
        from data.external.news_fetcher import _ensure_dataframe

        result = _ensure_dataframe([], source="test")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_unexpected_type_input(self):
        from data.external.news_fetcher import _ensure_dataframe

        result = _ensure_dataframe(42, source="test")
        assert result is None


class TestCLSCircuitBreaker:
    """测试 CLS 熔断器的开启、半开探活与恢复逻辑。"""

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_circuit_opens_after_threshold(self, mock_get, mock_tpm):
        """连续 3 次失败后熔断器开启。"""
        import data.external.news_fetcher as nf_mod

        mock_get.side_effect = requests.ConnectionError("network down")
        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        base_time = CST_TZ.localize(datetime.datetime(2024, 6, 14, 10, 0, 0))
        with patch("data.external.news_fetcher.get_now", return_value=base_time):
            for _ in range(3):
                res = await NewsFetcher.get_latest_global_news()
                assert res == []
            assert nf_mod._CLS_CONSECUTIVE_FAILURES == 3
            assert base_time.timestamp() == nf_mod._CLS_CIRCUIT_OPENED_AT

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_circuit_fast_fails_when_open(self, mock_get, mock_tpm):
        """熔断开启期间直接返回空列表，不发起网络调用。"""
        import data.external.news_fetcher as nf_mod

        nf_mod._CLS_CONSECUTIVE_FAILURES = 3
        nf_mod._CLS_CIRCUIT_OPENED_AT = CST_TZ.localize(datetime.datetime(2024, 6, 14, 10, 0, 0)).timestamp()

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        now = CST_TZ.localize(datetime.datetime(2024, 6, 14, 10, 0, 30))
        with patch("data.external.news_fetcher.get_now", return_value=now):
            res = await NewsFetcher.get_latest_global_news()
            assert res == []
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_circuit_half_open_recovery(self, mock_get, mock_tpm):
        """冷却期过后半开探活成功，熔断器关闭恢复。"""
        import data.external.news_fetcher as nf_mod

        nf_mod._CLS_CONSECUTIVE_FAILURES = 3
        base_time = CST_TZ.localize(datetime.datetime(2024, 6, 14, 10, 0, 0))
        nf_mod._CLS_CIRCUIT_OPENED_AT = base_time.timestamp()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"roll_data": [{"title": "探活成功", "content": "", "ctime": 1718330400}]}
        }
        mock_get.return_value = mock_resp

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        future_time = base_time + datetime.timedelta(seconds=61)
        with patch("data.external.news_fetcher.get_now", return_value=future_time):
            res = await NewsFetcher.get_latest_global_news()
            assert len(res) == 1
            assert res[0]["title"] == "探活成功"
            assert nf_mod._CLS_CONSECUTIVE_FAILURES == 0

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_circuit_half_open_failure_resets_cooldown(self, mock_get, mock_tpm):
        """半开探活失败时重置冷却计时器，下一个 60s 窗口重新计时。"""
        import data.external.news_fetcher as nf_mod

        nf_mod._CLS_CONSECUTIVE_FAILURES = 3
        base_time = CST_TZ.localize(datetime.datetime(2024, 6, 14, 10, 0, 0))
        nf_mod._CLS_CIRCUIT_OPENED_AT = base_time.timestamp()

        mock_get.side_effect = requests.ConnectionError("still down")

        mock_manager = MagicMock()
        mock_manager.run_async = AsyncMock(side_effect=lambda tt, fn, *a, **kw: fn())
        mock_tpm.return_value = mock_manager

        probe_time = base_time + datetime.timedelta(seconds=61)
        with patch("data.external.news_fetcher.get_now", return_value=probe_time):
            res = await NewsFetcher.get_latest_global_news()
            assert res == []
            assert nf_mod._CLS_CONSECUTIVE_FAILURES == 4
            assert probe_time.timestamp() == nf_mod._CLS_CIRCUIT_OPENED_AT

        # 探活失败后 30s 内应快速失败
        mock_get.reset_mock()
        fast_fail_time = probe_time + datetime.timedelta(seconds=30)
        with patch("data.external.news_fetcher.get_now", return_value=fast_fail_time):
            res = await NewsFetcher.get_latest_global_news()
            assert res == []
            mock_get.assert_not_called()

        # 探活失败后 61s 应再次进入半开
        mock_get.reset_mock()
        mock_get.side_effect = requests.ConnectionError("still down")
        second_probe_time = probe_time + datetime.timedelta(seconds=61)
        with patch("data.external.news_fetcher.get_now", return_value=second_probe_time):
            res = await NewsFetcher.get_latest_global_news()
            assert res == []
            mock_get.assert_called_once()
