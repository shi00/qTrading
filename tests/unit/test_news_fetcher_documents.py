# pyright: reportArgumentType=false
# 本文件含 mock/monkey-patch 模式，pyright 无法验证替身类兼容性；局部禁用告警。

import datetime

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from data.external.news_fetcher import NewsFetcher, _parse_news_time
from utils.time_utils import to_utc_for_db

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

# N3-4 patch 目标与 HTML 构造（与 test_news_fetcher.py 同款）
_SINA_FETCH_SYNC = "data.external.news_sources.sina_stock.fetch_page_sync"


def _sina_html_row(date_s: str, time_s: str, url: str, title: str) -> str:
    """构造符合 sina_stock.LIST_ROW_RE 的一行新闻 HTML（与真实页面行格式一致）。"""
    return f"&nbsp;&nbsp;&nbsp;&nbsp;{date_s}&nbsp;{time_s}&nbsp;&nbsp;<a target='_blank' href='{url}'>{title}</a> <br>"


def _sina_html(rows: list[tuple[str, str, str, str]]) -> str:
    """把 (日期, 时间, URL, 标题) 行列表拼为完整 HTML（供 mock fetch_page_sync 返回）。"""
    return "".join(_sina_html_row(*row) for row in rows)


@pytest.fixture(autouse=True)
def _mock_akshare_shared_limiter():
    """review08-B4：mock 共享限速器（与 test_news_fetcher.py 一致）。

    既有测试用 ThreadPoolManager side_effect 在事件循环线程直接执行 _fetch，
    而 TokenBucket.consume() 检测到 running loop 会抛 RuntimeError（生产环境
    _fetch_stock_news_core 在 IO 线程池线程执行、无 running loop，不会触发）。
    统一 mock 掉共享限速器使既有测试不受影响；限速行为由 TestSharedRateLimiter
    单独断言。
    """
    mock_limiter = MagicMock()
    mock_limiter.consume = MagicMock()
    with patch("data.external.news_fetcher.get_akshare_rate_limiter", return_value=mock_limiter):
        yield


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

    def test_day_only_with_existing_time_not_appended(self):
        # review08-D1 复核护栏：已含时间的输入原样解析，不再追加 00:00:00
        dt = _parse_news_time("2024-08-30 09:00:00", day_only=True)
        assert dt == to_utc_for_db(datetime.datetime(2024, 8, 30, 9, 0, 0))

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
    @patch(_SINA_FETCH_SYNC)
    @patch("data.external.news_fetcher._get_akshare")
    async def test_merges_both_sources(self, mock_get_ak, mock_sina_fetch, mock_run, mock_tpm):
        mock_ak = mock_get_ak.return_value  # review08-B5: 惰性导入缝（_get_akshare）→ akshare 模块 mock
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
        # N3-4：news 层为新浪个股列表页（content 恒空串，列表页无正文）
        mock_sina_fetch.return_value = _sina_html(
            [(today.strftime("%Y-%m-%d"), "10:00", "http://sina/1", "银行股上涨")]
        )
        mock_ak.stock_zh_a_disclosure_report_cninfo.return_value = cninfo

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
        assert nws["url"] == "http://sina/1"
        assert nws["content"] == ""
        # N3-4：新浪行内时间无秒补 :00，CST 10:00 → UTC naive 02:00
        assert nws["publish_time"] == to_utc_for_db(datetime.datetime.combine(today, datetime.time(10, 0, 0)))

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._run_with_python_string_storage", side_effect=lambda f: f())
    @patch(_SINA_FETCH_SYNC)
    @patch("data.external.news_fetcher._get_akshare")
    async def test_limits_after_window_filter(self, mock_get_ak, mock_sina_fetch, mock_run, mock_tpm):
        """排序降序 + 窗口过滤后限量。"""
        mock_ak = mock_get_ak.return_value  # review08-B5: 惰性导入缝（_get_akshare）→ akshare 模块 mock
        today = datetime.date.today()
        # N3-4：新浪层三行（列表页按时间降序给出），合并后仍按 publish_time 降序取前 2
        mock_sina_fetch.return_value = _sina_html(
            [
                (today.strftime("%Y-%m-%d"), "12:00", "http://e/1", "t1"),
                (today.strftime("%Y-%m-%d"), "11:00", "http://e/2", "t2"),
                (today.strftime("%Y-%m-%d"), "10:00", "http://e/3", "t3"),
            ]
        )
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
    @patch(_SINA_FETCH_SYNC)
    @patch("data.external.news_fetcher._get_akshare")
    async def test_cninfo_fail_still_returns_sina_with_coverage(self, mock_get_ak, mock_sina_fetch, mock_run, mock_tpm):
        """N3-4：巨潮层失败仍返回新浪个股新闻 + coverage 状态正确。"""
        mock_ak = mock_get_ak.return_value  # review08-B5: 惰性导入缝（_get_akshare）→ akshare 模块 mock
        mock_ak.stock_zh_a_disclosure_report_cninfo.side_effect = Exception("cninfo down")
        now_dt = datetime.datetime.now()
        mock_sina_fetch.return_value = _sina_html(
            [
                (
                    now_dt.strftime("%Y-%m-%d"),
                    now_dt.strftime("%H:%M"),
                    "http://sina/1",
                    "新浪消息",
                )
            ]
        )
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=lambda task_type, fn, *a, **k: fn() or (None, None))

        result = await NewsFetcher.get_stock_news_documents("000001.SZ")
        assert result["coverage"]["announcement"] == "fail"
        assert result["coverage"]["news"] == "ok"
        assert [d["source_kind"] for d in result["docs"]] == ["news"]

    @pytest.mark.asyncio
    @patch("data.external.news_fetcher.ThreadPoolManager")
    @patch("data.external.news_fetcher._get_akshare")
    async def test_timeout_returns_empty(self, mock_get_ak, mock_tpm):
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
