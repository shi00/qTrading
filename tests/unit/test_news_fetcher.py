import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.external.news_fetcher import NewsFetcher, _run_with_python_string_storage, _US_MOVES_CACHE


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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
            {"title": "2024年半年度报告", "publish_time": "2024-08-30 00:00:00", "source": "巨潮公告"}
        ]
        mock_tpm_instance.run_async = AsyncMock(return_value=future)

        with patch("data.external.news_fetcher.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = [
                {"title": "2024年半年度报告", "publish_time": "2024-08-30 00:00:00", "source": "巨潮公告"}
            ]
            result = await NewsFetcher.get_stock_news("000001.SZ", limit=5)
            assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_timeout(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=MagicMock())

        with patch("data.external.news_fetcher.asyncio.wait_for", new_callable=AsyncMock, side_effect=TimeoutError):
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
            new_callable=AsyncMock,
            side_effect=Exception("dispatch error"),
        ):
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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

        with patch("data.external.news_fetcher.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = [
                {"title": "银行股上涨", "publish_time": "2024-06-14 10:00:00", "source": "东财新闻"}
            ]
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_both_fail_returns_empty(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo error")
        mock_ak.stock_news_em.side_effect = Exception("em error")

        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance

        with patch("data.external.news_fetcher.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
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

        with patch("data.external.news_fetcher.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = []
            result = await NewsFetcher.get_stock_news("000001.SZ")
            assert result == []


class TestGetLatestGlobalNews:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_success_with_data(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "标题": ["重大新闻1", "重大新闻2"],
                "内容": ["内容1", "内容2"],
                "发布时间": ["2024-06-14 10:00:00", "2024-06-14 09:00:00"],
            }
        )
        mock_ak.stock_info_global_cls.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_latest_global_news(limit=5)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_runtime_error(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=RuntimeError("no pool"))

        result = await NewsFetcher.get_latest_global_news()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_empty_df(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_info_global_cls.return_value = pd.DataFrame()
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())

        result = await NewsFetcher.get_latest_global_news()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_none_df(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_info_global_cls.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=None)

        result = await NewsFetcher.get_latest_global_news()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_time_only_string(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "标题": ["新闻"],
                "内容": ["内容"],
                "发布时间": ["09:30:00"],
            }
        )
        mock_ak.stock_info_global_cls.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_latest_global_news(limit=1)
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_time_column_variants(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "标题": ["新闻"],
                "内容": ["内容"],
                "时间": ["2024-06-14 10:00:00"],
            }
        )
        mock_ak.stock_info_global_cls.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_latest_global_news(limit=1)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_title_column_variant(self, mock_ak, mock_run, mock_tpm):
        df = pd.DataFrame(
            {
                "title": ["English Title"],
                "content": ["Content"],
                "time": ["2024-06-14 10:00:00"],
            }
        )
        mock_ak.stock_info_global_cls.return_value = df
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=df)

        result = await NewsFetcher.get_latest_global_news(limit=1)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_general_exception(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_info_global_cls.side_effect = Exception("api error")
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("api error"))

        result = await NewsFetcher.get_latest_global_news()
        assert result == []


class TestGetUsMajorMoves:
    @pytest.mark.asyncio
    async def test_cached_result(self):
        _US_MOVES_CACHE.clear()
        _US_MOVES_CACHE["result"] = "NVDA: 2.5%, TSLA: -1.2%"
        result = await NewsFetcher.get_us_major_moves()
        assert result == "NVDA: 2.5%, TSLA: -1.2%"
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_success_with_data(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "2.45"}, {"name": "TSLA", "cname": "特斯拉", "price": "200.0", "diff": "-2.0", "chg": "-1.0"}]});'
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "2.45"},
                {"name": "TSLA", "cname": "特斯拉", "price": "200.0", "diff": "-2.0", "chg": "-1.0"},
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        assert "NVDA" in result
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_all_retries_fail(self, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("network error"))

        with patch("data.external.news_fetcher.asyncio.sleep", new_callable=AsyncMock):
            result = await NewsFetcher.get_us_major_moves()
            assert isinstance(result, str)
            assert "unavailable" in result or "error" in result.lower()
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_jsonp_parse_failure(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = "invalid response without jsonp"
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_high_pct_movers(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = (
            'IO({"data": [{"name": "UNKNOWN", "cname": "未知", "price": "10.0", "diff": "0.5", "chg": "5.0"}]});'
        )
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {"name": "UNKNOWN", "cname": "未知", "price": "10.0", "diff": "0.5", "chg": "5.0"},
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert "UNKNOWN" in result
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_no_giants_fallback(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
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
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_invalid_chg_value(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = (
            'IO({"data": [{"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "invalid"}]});'
        )
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value=[
                {"name": "NVDA", "cname": "英伟达", "price": "135.2", "diff": "3.2", "chg": "invalid"},
            ]
        )

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_json_decode_error(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = "IO(not valid json);"
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        _US_MOVES_CACHE.clear()

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.requests.get")
    async def test_exception_in_processing(self, mock_get, mock_tpm):
        _US_MOVES_CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.text = 'IO({"data": [{"name": "NVDA"}]});'
        mock_get.return_value = mock_resp
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[{"name": "NVDA"}])

        result = await NewsFetcher.get_us_major_moves()
        assert isinstance(result, str)
        _US_MOVES_CACHE.clear()


class TestGetHotConcepts:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    async def test_none_df(self, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=None)

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
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
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_sina_exception(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_sector_spot.side_effect = Exception("sina error")
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("sina error"))

        result = await NewsFetcher.get_hot_concepts()
        assert result == []

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_general_exception(self, mock_ak, mock_run, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("general error"))

        result = await NewsFetcher.get_hot_concepts()
        assert result == []


class TestNewsFetcherGetLatestGlobalNews:
    @pytest.mark.asyncio
    @patch("data.external.news_fetcher._run_with_python_string_storage")
    @patch("data.external.news_fetcher.ThreadPoolManager")
    async def test_get_latest_global_news(self, mock_tpm, mock_run):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=[])
        result = await NewsFetcher.get_latest_global_news()
        assert isinstance(result, (list, str))


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
