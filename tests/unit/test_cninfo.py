"""巨潮资讯公告解析内核单测（N1-3）。

覆盖：表单参数构造、``<em>`` 与 HTML 清洗、标题外壳（公司全称前缀 / 「关于……的公告」）清洗、
毫秒时间戳→UTC tz-naive（**不按北京时间换算**）、``secCode`` + ``pageColumn`` → ``ts_code`` 映射、
``announcements[]`` 归一化与分页信息、抓取（假 httpx 客户端）。
"""

import datetime
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from data.external.news_sources import cninfo

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "news_sources"
FIXTURE_SZSE = FIXTURES / "cninfo_szse_20260928_page1.json"
FIXTURE_SEARCH = FIXTURES / "cninfo_search_repo_20260928_page1.json"


@pytest.fixture(scope="module")
def szse_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_SZSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def search_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_SEARCH.read_text(encoding="utf-8"))


def _find(docs: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    return next(doc for doc in docs if doc["source_id"] == source_id)


# --------------------------------------------------------------------------- #
# build_query_params
# --------------------------------------------------------------------------- #
def test_build_query_params_defaults_match_spec():
    assert cninfo.build_query_params("szse", "2026-09-28", "2026-09-28") == {
        "column": "szse",
        "tabName": "fulltext",
        "seDate": "2026-09-28~2026-09-28",
        "pageNum": "1",
        "pageSize": "30",
        "isHLtitle": "true",
    }


def test_build_query_params_adds_searchkey_only_when_present():
    with_key = cninfo.build_query_params("sse", "2026-09-28", "2026-09-28", search_key="回购")
    assert with_key["searchkey"] == "回购"
    assert "searchkey" not in cninfo.build_query_params("sse", "2026-09-28", "2026-09-28")


def test_build_query_params_accepts_date_objects_and_page_args():
    params = cninfo.build_query_params(
        "bj",
        datetime.date(2026, 9, 28),
        datetime.date(2026, 9, 29),
        page_num=3,
        page_size=50,
        is_hl_title=False,
    )
    assert params["seDate"] == "2026-09-28~2026-09-29"
    assert params["pageNum"] == "3"
    assert params["pageSize"] == "50"
    assert params["isHLtitle"] == "false"


# --------------------------------------------------------------------------- #
# build_client_kwargs
# --------------------------------------------------------------------------- #
def test_build_client_kwargs_merges_proxy_timeout_headers(monkeypatch):
    monkeypatch.setattr(
        cninfo.ProxyManager,
        "get_httpx_proxy_kwargs",
        staticmethod(lambda hostname: {"trust_env": False}),
    )
    kwargs = cninfo.build_client_kwargs()
    assert kwargs["trust_env"] is False
    assert kwargs["timeout"] == cninfo.FETCH_TIMEOUT_SECONDS
    assert kwargs["headers"]["User-Agent"] == cninfo.DEFAULT_USER_AGENT
    assert kwargs["headers"]["Referer"] == cninfo.CNINFO_REFERER
    assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"


# --------------------------------------------------------------------------- #
# strip_html
# --------------------------------------------------------------------------- #
def test_strip_html_removes_em_highlight():
    assert cninfo.strip_html("关于<em>回购</em>注销的公告") == "关于回购注销的公告"


def test_strip_html_unescapes_entities_and_normalizes_whitespace():
    assert cninfo.strip_html("A&nbsp;B&amp;C") == "A B&C"
    assert cninfo.strip_html("  贵州　茅台  ") == "贵州 茅台"


@pytest.mark.parametrize("raw", [None, ""])
def test_strip_html_empty_input_returns_empty_string(raw):
    assert cninfo.strip_html(raw) == ""


# --------------------------------------------------------------------------- #
# clean_title
# --------------------------------------------------------------------------- #
def test_clean_title_strips_em_and_about_shell():
    assert (
        cninfo.clean_title("关于<em>回购</em>注销部分限制性股票减少注册资本暨通知债权人的公告")
        == "回购注销部分限制性股票减少注册资本暨通知债权人"
    )


def test_clean_title_strips_company_full_name_prefix():
    assert cninfo.clean_title("四川百利天恒药业股份有限公司关于获得药品注册证书的公告") == "获得药品注册证书"


def test_clean_title_keeps_prefix_without_about_shell():
    title = "创业黑马科技集团股份有限公司发行股份及支付现金购买资产报告书（注册稿）"
    assert cninfo.clean_title(title) == title


def test_clean_title_keeps_title_without_shell():
    assert cninfo.clean_title("<em>回购</em>股份报告书") == "回购股份报告书"
    assert cninfo.clean_title("第四届董事会第十二次会议决议公告") == "第四届董事会第十二次会议决议公告"


def test_clean_title_empty_input_returns_empty_string():
    assert cninfo.clean_title(None) == ""


# --------------------------------------------------------------------------- #
# parse_publish_time（毫秒时间戳 → UTC tz-naive，不按北京时间换算）
# --------------------------------------------------------------------------- #
def test_parse_publish_time_milliseconds_to_utc_naive():
    result = cninfo.parse_publish_time(1790600428000)
    assert result == datetime.datetime(2026, 9, 28, 13, 0, 28)  # noqa: DTZ001 - UTC naive 期望值
    assert result.tzinfo is None


def test_parse_publish_time_epoch_is_utc_not_beijing():
    # 0 ms = 1970-01-01T00:00:00Z；若误按北京时间换算会得到 08:00:00
    result = cninfo.parse_publish_time(0)
    assert result == datetime.datetime(1970, 1, 1, 0, 0, 0)  # noqa: DTZ001 - UTC naive 期望值
    assert result != datetime.datetime(1970, 1, 1, 8, 0, 0)  # noqa: DTZ001 - 反例：北京时间换算值


def test_parse_publish_time_does_not_apply_beijing_offset():
    result = cninfo.parse_publish_time(1790600428000)
    assert result != datetime.datetime(2026, 9, 28, 21, 0, 28)  # noqa: DTZ001 - 反例：CST 换算会得到该值


@pytest.mark.parametrize("raw", [None, "", "not-a-time", float("nan"), float("inf"), [1], 10**20])
def test_parse_publish_time_invalid_returns_none(raw):
    assert cninfo.parse_publish_time(raw) is None


# --------------------------------------------------------------------------- #
# map_to_ts_code
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("sec_code", "page_column", "expected"),
    [
        ("300638", "SZCY", "300638.SZ"),
        ("000711", "SZZB", "000711.SZ"),
        ("688608", "SHKCB", "688608.SH"),
        ("920212", "BJS", "920212.BJ"),
        ("300638", "szcy", "300638.SZ"),
        ("30063", "SZCY", None),
        ("300638", "UNKNOWN", None),
        ("300638", None, None),
        (None, "SZCY", None),
    ],
)
def test_map_to_ts_code(sec_code, page_column, expected):
    assert cninfo.map_to_ts_code(sec_code, page_column) == expected


# --------------------------------------------------------------------------- #
# parse_announcement
# --------------------------------------------------------------------------- #
def test_parse_announcement_fields_from_recorded_sample(szse_payload):
    docs, _ = cninfo.parse_announcement_payload(szse_payload)
    doc = _find(docs, "1225586203")
    assert doc["source"] == "cninfo"
    assert doc["source_id"] == "1225586203"
    assert doc["sec_code"] == "300638"
    assert doc["sec_name"] == "广和通"
    assert doc["ts_code"] == "300638.SZ"
    assert doc["announcement_type_name"] is None
    assert doc["publish_time"] == datetime.datetime(2026, 9, 28, 13, 0, 28)  # noqa: DTZ001 - UTC naive 期望值
    assert doc["publish_time"].tzinfo is None
    assert doc["source_url"] == "https://static.cninfo.com.cn/finalpage/2026-09-28/1225586203.PDF"


def test_parse_announcement_cleans_em_highlight(search_payload):
    docs, _ = cninfo.parse_announcement_payload(search_payload)
    doc = _find(docs, "1225585697")
    assert doc["text"] == "回购注销部分限制性股票减少注册资本暨通知债权人"
    assert "<em>" not in doc["text"]


def test_parse_announcement_strips_about_shell(szse_payload):
    docs, _ = cninfo.parse_announcement_payload(szse_payload)
    doc = _find(docs, "1225586105")
    assert doc["text"] == "完成董事会换届选举"


def test_parse_announcement_missing_id_returns_none():
    assert cninfo.parse_announcement({"secCode": "300638"}) is None
    assert cninfo.parse_announcement("not-a-dict") is None


def test_parse_announcement_without_adjunct_url_has_empty_source_url():
    doc = cninfo.parse_announcement({"announcementId": "1", "secCode": "300638", "pageColumn": "SZCY"})
    assert doc is not None
    assert doc["source_url"] == ""
    assert doc["publish_time"] is None


# --------------------------------------------------------------------------- #
# parse_announcement_payload
# --------------------------------------------------------------------------- #
def test_parse_announcement_payload_from_recorded_sample(szse_payload):
    docs, page_info = cninfo.parse_announcement_payload(szse_payload)
    assert len(docs) == 30
    assert len({doc["source_id"] for doc in docs}) == 30  # 页内 announcementId 唯一
    assert page_info == {"total": 743, "has_more": True, "total_pages": 24}
    assert all(doc["publish_time"] is not None and doc["publish_time"].tzinfo is None for doc in docs)
    assert all(doc["source"] == "cninfo" for doc in docs)


def test_parse_announcement_payload_maps_all_exchange_columns(search_payload):
    docs, _ = cninfo.parse_announcement_payload(search_payload)
    ts_codes = {doc["ts_code"] for doc in docs}
    assert "300227.SZ" in ts_codes
    assert "000711.SZ" in ts_codes
    assert "688608.SH" in ts_codes
    assert "920212.BJ" in ts_codes


@pytest.mark.parametrize("payload", [None, 123, "x", {}, {"announcements": "bad"}])
def test_parse_announcement_payload_bad_input_returns_empty(payload):
    assert cninfo.parse_announcement_payload(payload) == ([], {})


def test_parse_announcement_payload_skips_malformed_items():
    payload = {"announcements": [None, "x", {"secCode": "300638"}, {"announcementId": "42", "secCode": "300638"}]}
    docs, _ = cninfo.parse_announcement_payload(payload)
    assert [doc["source_id"] for doc in docs] == ["42"]


# --------------------------------------------------------------------------- #
# fetch_page
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload: Any, *, raise_exc: Exception | None = None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post(self, url: str, data: dict[str, str]):
        self.calls.append((url, data))
        return self._response


def _as_client(fake: _FakeClient) -> httpx.AsyncClient:
    """把假客户端伪装为 httpx.AsyncClient（仅满足 fetch_page 的形参类型）。"""
    return cast("httpx.AsyncClient", fake)


async def test_fetch_page_posts_query_url_and_form_params():
    payload = {"announcements": [], "totalRecordNum": 0}
    client = _FakeClient(_FakeResponse(payload))
    out = await cninfo.fetch_page(
        _as_client(client), "sse", "2026-09-28", "2026-09-28", page_num=2, page_size=10, search_key="回购"
    )
    assert out == payload
    url, data = client.calls[0]
    assert url == cninfo.CNINFO_QUERY_URL
    assert data["column"] == "sse"
    assert data["seDate"] == "2026-09-28~2026-09-28"
    assert data["searchkey"] == "回购"
    assert data["pageNum"] == "2"
    assert data["pageSize"] == "10"


async def test_fetch_page_non_dict_json_returns_empty_dict():
    client = _FakeClient(_FakeResponse(["not", "a", "dict"]))
    assert await cninfo.fetch_page(_as_client(client)) == {}


async def test_fetch_page_propagates_http_status_error():
    request = httpx.Request("POST", cninfo.CNINFO_QUERY_URL)
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    client = _FakeClient(_FakeResponse({}, raise_exc=exc))
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await cninfo.fetch_page(_as_client(client))
    assert excinfo.value.response.status_code == 500
