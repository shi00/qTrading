# pyright: reportArgumentType=false
# 本文件含 mock/monkey-patch 模式，pyright 无法验证替身类兼容性；局部禁用告警。

import datetime

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from data.external.news_fetcher import NewsFetcher, _parse_news_time
from utils.time_utils import to_utc_for_db

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


# ---------- _parse_news_time 时间口径 ----------
class TestParseNewsTime:
    def test_full_datetime_cst_to_utc_naive(self):
        dt = _parse_news_time("2024-06-15 10:00:00")
        expected = to_utc_for_db(datetime.datetime(2024, 6, 15, 10, 0, 0))
        assert dt == expected
        assert dt.tzinfo is None  # DB 存 UTC naive

    def test_day_only_appends_midnight(self):
        dt = _parse_news_time("2024-08-30", day_only=True)
        assert dt == to_utc_for_db(datetime.datetime(2024, 8, 30, 0, 0, 0))

    def test_day_only_without_flag_is_none(self):
        # 非 day_only 且只给日期（不含时间）→ 无法解析 → None
        assert _parse_news_time("2024-08-30") is None

    def test_empty_returns_none(self):
        assert _parse_news_time("") is None
        assert _parse_news_time(None, day_only=True) is None

    def test_bad_format_returns_none(self):
        assert _parse_news_time("not-a-time") is None


# ---------- get_stock_news_documents 合并双源 ----------
class TestGetStockNewsDocuments:
    @pytest.mark.asyncio
    async def test_empty_ts_code(self):
        result = await NewsFetcher.get_stock_news_documents("")
        assert result["docs"] == []
        assert result["coverage"] == {}

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_merges_both_sources(self, mock_ak, mock_run, mock_tpm):
        today = datetime.date.today()
        diff = datetime.timedelta(days=1)
        cninfo = pd.DataFrame(
            {
                "代码": ["600519"],
                "简称": ["贵州茅台"],
                "公告标题": ["2024年半年度报告"],
                "公告时间": [(today - diff).strftime("%Y-%m-%d")],
                "公告链接": ["http://cninfo/a"],
            }
        )
        em = pd.DataFrame(
            {
                "新闻标题": ["银行股上涨"],
                "新闻内容": ["详细内容"],
                "新闻时间": [today.strftime("%Y-%m-%d 10:00:00")],
                "新闻链接": ["http://em/1"],
                "文章来源": ["东财"],
            }
        )
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = cninfo
        mock_ak.stock_news_em.return_value = em

        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        # run_async 直接执行传入闭包，wait_for await 同一 coroutine 得到 (docs, coverage)
        mock_tpm_instance.run_async = AsyncMock(side_effect=lambda task_type, fn, *a, **k: fn() or (None, None))

        # 真实 wait_for 直接 await run_async 返回的 coroutine
        result = await NewsFetcher.get_stock_news_documents("600519.SH", window_days=30, limit=50)

        c = result["coverage"]
        assert c["announcement"] == "ok"
        assert c["news"] == "ok"
        kinds = sorted({d["source_kind"] for d in result["docs"]})
        assert kinds == ["announcement", "news"]
        ann = next(d for d in result["docs"] if d["source_kind"] == "announcement")
        nws = next(d for d in result["docs"] if d["source_kind"] == "news")
        assert ann["title"] == "2024年半年度报告"
        assert ann["url"] == "http://cninfo/a"
        assert ann["content"] == ""
        assert ann["publish_time"].tzinfo is None
        # review08-D1：公告仅日期（CST）→ 补 00:00:00 后转 UTC naive（前日 16:00），与 get_stock_news 同口径
        assert ann["publish_time"] == to_utc_for_db(datetime.datetime.combine(today - diff, datetime.time()))
        assert nws["url"] == "http://em/1"
        assert nws["content"] == "详细内容"
        assert nws["publish_time"] == to_utc_for_db(datetime.datetime.combine(today, datetime.time(10, 0, 0)))

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_limits_after_window_filter(self, mock_ak, mock_run, mock_tpm):
        """排序降序 + 窗口过滤后限量。"""
        today = datetime.date.today()
        em = pd.DataFrame(
            {
                "新闻标题": ["t1", "t2", "t3"],
                "新闻内容": ["c1", "c2", "c3"],
                "新闻时间": [
                    today.strftime("%Y-%m-%d 12:00:00"),
                    today.strftime("%Y-%m-%d 11:00:00"),
                    today.strftime("%Y-%m-%d 10:00:00"),
                ],
                "新闻链接": ["http://e/1", "http://e/2", "http://e/3"],
            }
        )
        mock_ak.stock_news_em.return_value = em
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame(
            {"公告标题": [], "公告时间": [], "公告链接": []}
        )

        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=lambda task_type, fn, *a, **k: fn() or (None, None))

        result = await NewsFetcher.get_stock_news_documents("000001.SZ", window_days=30, limit=2)
        titles = [d["title"] for d in result["docs"]]
        assert len(titles) == 2
        assert titles == ["t1", "t2"]  # 降序取前 2

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch("data.external.news_fetcher.ak")
    async def test_cninfo_fail_still_returns_em_with_coverage(self, mock_ak, mock_run, mock_tpm):
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo down")
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        em = pd.DataFrame(
            {
                "新闻标题": ["东财消息"],
                "新闻内容": ["x"],
                "新闻时间": [today],
                "新闻链接": ["http://em/1"],
            }
        )
        mock_ak.stock_news_em.return_value = em
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=lambda task_type, fn, *a, **k: fn() or (None, None))

        result = await NewsFetcher.get_stock_news_documents("000001.SZ")
        assert result["coverage"]["announcement"] == "fail"
        assert result["coverage"]["news"] == "ok"
        assert [d["source_kind"] for d in result["docs"]] == ["news"]

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher.ak")
    async def test_timeout_returns_empty(self, mock_ak, mock_tpm):
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(return_value=MagicMock())
        with patch(
            "data.external.news_fetcher.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: (_ for _ in ()).throw(TimeoutError()),
        ):
            result = await NewsFetcher.get_stock_news_documents("000001.SZ")
        assert result["docs"] == []
        assert result["coverage"] == {}
