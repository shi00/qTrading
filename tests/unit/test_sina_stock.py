"""新浪个股新闻解析内核单测（N1-2）。

覆盖：GB18030 解码（含录制样本字节）、无秒时间补齐并对齐
``news_fetcher._parse_news_time``、列表行正则提取（单引号 href / 忽略双引号导航链接）、
URL 去重、``ts_code`` 关联映射、条目 schema、抓取（假 httpx 客户端）。
"""

import datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from data.external.news_fetcher import _parse_news_time
from data.external.news_sources import sina_stock

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "news_sources" / "sina_stock_sh600519_page1.html"


@pytest.fixture(scope="module")
def recorded_html() -> str:
    return sina_stock.decode_html(FIXTURE.read_bytes())


# --------------------------------------------------------------------------- #
# build_url / build_client_kwargs
# --------------------------------------------------------------------------- #
def test_build_url_page1_matches_spec():
    assert sina_stock.build_url("sh600519") == (
        "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/sh600519.phtml"
    )


def test_build_url_page_gt1_appends_page_param():
    assert sina_stock.build_url("sz000001", 3).endswith("symbol/sz000001.phtml?Page=3")


def test_build_client_kwargs_merges_proxy_timeout_headers(monkeypatch):
    monkeypatch.setattr(
        sina_stock.ProxyManager,
        "get_httpx_proxy_kwargs",
        staticmethod(lambda hostname: {"trust_env": False}),
    )
    kwargs = sina_stock.build_client_kwargs()
    assert kwargs["trust_env"] is False
    assert kwargs["timeout"] == sina_stock.FETCH_TIMEOUT_SECONDS
    assert kwargs["headers"]["User-Agent"] == sina_stock.DEFAULT_USER_AGENT
    assert kwargs["headers"]["Referer"] == sina_stock.SINA_STOCK_REFERER


# --------------------------------------------------------------------------- #
# decode_html
# --------------------------------------------------------------------------- #
def test_decode_html_gb18030_roundtrip():
    assert sina_stock.decode_html("贵州茅台".encode("gb18030")) == "贵州茅台"


def test_decode_html_str_passthrough():
    assert sina_stock.decode_html("already-text") == "already-text"


def test_decode_html_never_raises_on_invalid_bytes():
    assert isinstance(sina_stock.decode_html(b"\xff\xff\xff\xff"), str) is True


def test_fixture_is_gb18030_encoded():
    raw = FIXTURE.read_bytes()
    assert isinstance(raw, bytes) is True
    assert (raw.decode(sina_stock.NEWS_ENCODING).count("&nbsp;")) > 0


# --------------------------------------------------------------------------- #
# parse_publish_time（含与 news_fetcher._parse_news_time 的口径对齐）
# --------------------------------------------------------------------------- #
def test_parse_publish_time_pads_missing_seconds():
    result = sina_stock.parse_publish_time("2026-10-08 18:03")
    assert result is not None
    assert result == datetime.datetime(2026, 10, 8, 10, 3, 0)  # noqa: DTZ001 - UTC naive 期望值
    assert result.tzinfo is None


def test_parse_publish_time_accepts_seconds():
    assert sina_stock.parse_publish_time("2026-10-08 18:03:30") == datetime.datetime(  # noqa: DTZ001 - UTC naive 期望值
        2026, 10, 8, 10, 3, 30
    )


@pytest.mark.parametrize("raw", [None, "", "2026-10-08", "2026/10/08 18:03", "not-a-time", 20261008])
def test_parse_publish_time_invalid_returns_none(raw):
    assert sina_stock.parse_publish_time(raw) is None


def test_padded_time_aligns_with_parse_news_time():
    # 规格 §3.1：补 :00 至 19 位后与 news_fetcher._parse_news_time 口径一致
    assert sina_stock.parse_publish_time("2026-10-08 18:03") == _parse_news_time("2026-10-08 18:03:00")


def test_unpadded_time_is_rejected_by_parse_news_time():
    # 说明补齐的必要性：16 位长度会被 _parse_news_time 判为非法，直接传入会丢失时间
    assert _parse_news_time("2026-10-08 18:03") is None


# --------------------------------------------------------------------------- #
# parse_news_list
# --------------------------------------------------------------------------- #
def test_parse_news_list_from_recorded_sample(recorded_html):
    docs = sina_stock.parse_news_list(recorded_html, symbol="sh600519")
    assert len(docs) == sina_stock.PAGE_SIZE
    assert len({doc["source_id"] for doc in docs}) == sina_stock.PAGE_SIZE  # 页内 URL 唯一
    assert all(doc["source"] == "sina_stock" for doc in docs)
    assert all(doc["symbol"] == "sh600519" for doc in docs)
    assert all(doc["ts_code"] == "600519.SH" for doc in docs)
    assert all(doc["text"] for doc in docs)
    assert all(doc["source_url"].startswith("http") for doc in docs)
    assert all(doc["publish_time"] is not None and doc["publish_time"].tzinfo is None for doc in docs)


def test_parse_news_list_ignores_double_quoted_href():
    html = (
        '<a href="https://nav/1">导航</a>'
        "2026-10-08&nbsp;18:03&nbsp;&nbsp;"
        "<a target=\"_blank\" href='https://news/2'>新闻标题</a>"
    )
    docs = sina_stock.parse_news_list(html)
    assert [doc["source_url"] for doc in docs] == ["https://news/2"]


def test_parse_news_list_dedupes_by_url_keeping_first():
    row = "2026-10-08&nbsp;18:03&nbsp;&nbsp;<a href='{url}'>{title}</a>"
    html = row.format(url="https://news/1", title="A") + row.format(url="https://news/1", title="B")
    html += row.format(url="https://news/2", title="C")
    docs = sina_stock.parse_news_list(html)
    assert [doc["source_id"] for doc in docs] == ["https://news/1", "https://news/2"]
    assert [doc["text"] for doc in docs] == ["A", "C"]


def test_parse_news_list_strips_title_whitespace():
    html = "2026-10-08&nbsp;18:03&nbsp;&nbsp;<a href='https://news/1'>  带空格标题  </a>"
    assert sina_stock.parse_news_list(html)[0]["text"] == "带空格标题"


def test_parse_news_list_without_symbol_has_no_association():
    html = "2026-10-08&nbsp;18:03&nbsp;&nbsp;<a href='https://news/1'>A</a>"
    doc = sina_stock.parse_news_list(html)[0]
    assert doc["symbol"] is None
    assert doc["ts_code"] is None


def test_parse_news_list_non_mappable_symbol_keeps_symbol_only():
    html = "2026-10-08&nbsp;18:03&nbsp;&nbsp;<a href='https://news/1'>A</a>"
    doc = sina_stock.parse_news_list(html, symbol="us_aapl")[0]
    assert doc["symbol"] == "us_aapl"
    assert doc["ts_code"] is None


def test_parse_news_list_row_without_seconds_still_parses_time():
    html = "2026-10-08&nbsp;09:05&nbsp;&nbsp;<a href='https://news/1'>A</a>"
    assert sina_stock.parse_news_list(html)[0]["publish_time"] == datetime.datetime(  # noqa: DTZ001 - UTC naive 期望值
        2026, 10, 8, 1, 5, 0
    )


@pytest.mark.parametrize("html", [None, "", 123, "<html>no rows here</html>"])
def test_parse_news_list_bad_input_returns_empty(html):
    assert sina_stock.parse_news_list(html) == []


# --------------------------------------------------------------------------- #
# fetch_page
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, content: bytes, *, raise_exc: Exception | None = None):
        self.content = content
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[str] = []

    async def get(self, url: str):
        self.calls.append(url)
        return self._response


def _as_client(fake: _FakeClient) -> httpx.AsyncClient:
    """把假客户端伪装为 httpx.AsyncClient（仅满足 fetch_page 的形参类型）。"""
    return cast("httpx.AsyncClient", fake)


async def test_fetch_page_decodes_gb18030_and_builds_url():
    payload: bytes = "贵州茅台 2026-10-08".encode("gb18030")
    client = _FakeClient(_FakeResponse(payload))
    html = await sina_stock.fetch_page(_as_client(client), "sh600519", page=2)
    assert "贵州茅台" in html
    assert client.calls == [sina_stock.build_url("sh600519", 2)]


async def test_fetch_page_propagates_http_status_error():
    request = httpx.Request("GET", sina_stock.build_url("sh600519"))
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    client = _FakeClient(_FakeResponse(b"", raise_exc=exc))
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await sina_stock.fetch_page(_as_client(client), "sh600519")
    assert excinfo.value.response.status_code == 500
