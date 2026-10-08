"""新浪 7x24 解析内核单测（N1-1）。

覆盖：``ext`` JSON 字符串解析、A 股 ``ts_code`` 映射规则、指数 / 板块识别、条目与
响应体解析（含录制样本 fixture）、时间口径（CST → UTC naive）与抓取（假 httpx 客户端）。
"""

import datetime
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from data.external.news_sources import sina_7x24

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "news_sources" / "sina_7x24_page.json"


def _parse_item(item: object) -> dict[str, Any]:
    """解析单条并断言非 ``None``（收窄 ``dict | None``，供后续下标访问）。"""
    doc = sina_7x24.parse_feed_item(item)
    assert doc is not None
    return doc


@pytest.fixture(scope="module")
def recorded_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# build_feed_params / build_client_kwargs
# --------------------------------------------------------------------------- #
def test_build_feed_params_matches_spec_url():
    params = sina_7x24.build_feed_params(page=2, page_size=50)
    assert params == {
        "page": 2,
        "page_size": 50,
        "zhibo_id": sina_7x24.SINA_7X24_ZHIBO_ID,
        "tag_id": 0,
        "dire": "f",
        "dpc": 1,
        "type": 0,
    }


def test_build_client_kwargs_merges_proxy_timeout_headers(monkeypatch):
    monkeypatch.setattr(
        sina_7x24.ProxyManager,
        "get_httpx_proxy_kwargs",
        staticmethod(lambda hostname: {"trust_env": False}),
    )
    kwargs = sina_7x24.build_client_kwargs()
    assert kwargs["trust_env"] is False
    assert kwargs["timeout"] == sina_7x24.FETCH_TIMEOUT_SECONDS
    assert kwargs["headers"]["User-Agent"] == sina_7x24.DEFAULT_USER_AGENT
    assert kwargs["headers"]["Referer"] == sina_7x24.SINA_7X24_REFERER


# --------------------------------------------------------------------------- #
# parse_ext
# --------------------------------------------------------------------------- #
def test_parse_ext_from_json_string():
    raw = '{"stocks":[{"market":"cn","symbol":"sz000001","key":"平安银行"}],"docurl":"https://x/y"}'
    assert sina_7x24.parse_ext(raw)["stocks"][0]["symbol"] == "sz000001"


def test_parse_ext_passthrough_dict():
    ext = {"stocks": []}
    assert sina_7x24.parse_ext(ext) is ext


@pytest.mark.parametrize("raw", [None, "", "   ", "not-json", "[1,2,3]", "123", 42])
def test_parse_ext_invalid_returns_empty(raw):
    assert sina_7x24.parse_ext(raw) == {}


# --------------------------------------------------------------------------- #
# map_symbol_to_ts_code
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("sh688333", "688333.SH"),
        ("sz002544", "002544.SZ"),
        ("bj920670", "920670.BJ"),
        ("SH600519", "600519.SH"),  # 前缀大小写不敏感
        ("sz000568", "000568.SZ"),
        ("sh000001", "000001.SH"),  # 指数亦可映射为合法 ts_code（语义由 is_index_code 判定）
        ("si930997", None),  # 新浪板块码
        ("sih30192", None),
        ("159352", None),  # 基金裸码
        ("u", None),  # 美股字母码
        ("sh68833", None),  # 位数不足
        ("sh6883330", None),  # 位数过多
        ("", None),
        (None, None),
        (12345, None),
    ],
)
def test_map_symbol_to_ts_code(symbol, expected):
    assert sina_7x24.map_symbol_to_ts_code(symbol) == expected


# --------------------------------------------------------------------------- #
# is_index_code
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "symbol",
    [
        "sh000001",
        "sh000905",
        "sh000159",
        "sz399006",
        "sz399417",
        "bj899050",
        "si930997",
        "sih30192",
        "siH30535",
        "SI000941",
    ],
)
def test_is_index_code_true(symbol):
    assert sina_7x24.is_index_code(symbol) is True


@pytest.mark.parametrize(
    "symbol",
    ["sh688333", "sh600519", "sz000568", "sz002544", "sz300750", "bj920670", "159352", "u", "", None, 12345],
)
def test_is_index_code_false(symbol):
    assert sina_7x24.is_index_code(symbol) is False


# --------------------------------------------------------------------------- #
# normalize_stocks
# --------------------------------------------------------------------------- #
def test_normalize_stocks_cn_and_non_cn():
    ext = {
        "stocks": [
            {"market": "cn", "symbol": "sh688333", "key": "铂力特"},
            {"market": "fund", "symbol": "159352", "key": "投资", "sym_party_status": "1"},
            {"market": "us", "symbol": "u", "key": "Unity"},
            {"market": "cn", "symbol": "sz399417", "key": "新能源车"},
        ]
    }
    stocks = sina_7x24.normalize_stocks(ext)
    assert stocks[0] == {
        "market": "cn",
        "symbol": "sh688333",
        "name": "铂力特",
        "ts_code": "688333.SH",
        "is_index": False,
    }
    # 非 A 股不映射 ts_code、不判指数
    assert stocks[1]["ts_code"] is None
    assert stocks[1]["is_index"] is False
    assert stocks[2]["market"] == "us"
    assert stocks[2]["ts_code"] is None
    # cn 市场中的指数：可映射但标记 is_index
    assert stocks[3]["ts_code"] == "399417.SZ"
    assert stocks[3]["is_index"] is True


def test_normalize_stocks_market_lowercased():
    stocks = sina_7x24.normalize_stocks({"stocks": [{"market": "CFF", "symbol": "if2210", "key": "IF"}]})
    assert stocks[0]["market"] == "cff"


def test_normalize_stocks_skips_invalid_entries():
    ext = {"stocks": ["not-a-dict", {"market": "cn"}, {"symbol": "sz000001"}, {"market": "", "symbol": ""}]}
    assert sina_7x24.normalize_stocks(ext) == []


@pytest.mark.parametrize("ext", [{}, {"stocks": None}, {"stocks": "x"}])
def test_normalize_stocks_missing_or_bad_stocks(ext):
    assert sina_7x24.normalize_stocks(ext) == []


# --------------------------------------------------------------------------- #
# parse_publish_time
# --------------------------------------------------------------------------- #
def test_parse_publish_time_cst_to_utc_naive():
    result = sina_7x24.parse_publish_time("2026-10-08 19:22:23")
    assert result == datetime.datetime(2026, 10, 8, 11, 22, 23)  # noqa: DTZ001 - 期望为 UTC naive 字面量
    assert result is not None and result.tzinfo is None


@pytest.mark.parametrize("raw", [None, "", "2026-10-08", "2026/10/08 19:22:23", "not-a-time", 20261008])
def test_parse_publish_time_invalid_returns_none(raw):
    assert sina_7x24.parse_publish_time(raw) is None


# --------------------------------------------------------------------------- #
# parse_feed_item
# --------------------------------------------------------------------------- #
def test_parse_feed_item_full_schema():
    item = {
        "id": 5132355,
        "rich_text": "  【普天科技：公司银行账户部分资金被冻结】  ",
        "create_time": "2026-10-08 19:20:09",
        "tag": [{"id": "3", "name": "公司"}],
        "docurl": "https://finance.sina.cn/7x24/2026-10-08/detail-x.d.html",
        "ext": '{"stocks":[{"market":"cn","symbol":"sz002544","key":"普天科技"}],"docurl":"https://finance.sina.com.cn/x.shtml"}',
    }
    doc = _parse_item(item)
    assert doc["source"] == "sina_7x24"
    assert doc["source_id"] == "5132355"  # 去重键统一为字符串
    assert doc["text"].startswith("【普天科技") and doc["text"].endswith("】")
    assert doc["publish_time"] == datetime.datetime(2026, 10, 8, 11, 20, 9)  # noqa: DTZ001 - UTC naive
    assert doc["tags"] == ["公司"]
    assert doc["source_url"].endswith("detail-x.d.html")  # 优先取条目级 docurl
    assert doc["stocks"][0]["ts_code"] == "002544.SZ"


def test_parse_feed_item_source_url_falls_back_to_ext():
    item = {"id": 1, "rich_text": "x", "create_time": "2026-10-08 10:00:00", "ext": '{"docurl":"https://ext/y"}'}
    assert _parse_item(item)["source_url"] == "https://ext/y"


def test_parse_feed_item_tags_tolerate_strings_and_garbage():
    item = {"id": 1, "rich_text": "x", "tag": ["市场", {"name": "公司"}, {"id": "1"}, 5, None]}
    assert _parse_item(item)["tags"] == ["市场", "公司"]


def test_parse_feed_item_bad_ext_yields_no_stocks():
    item = {"id": 1, "rich_text": "x", "create_time": "2026-10-08 10:00:00", "ext": "{not json"}
    assert _parse_item(item)["stocks"] == []


@pytest.mark.parametrize("item", [None, "x", [], {}, {"rich_text": "no id"}])
def test_parse_feed_item_missing_id_returns_none(item):
    assert sina_7x24.parse_feed_item(item) is None


# --------------------------------------------------------------------------- #
# parse_feed_payload（含录制样本）
# --------------------------------------------------------------------------- #
def test_parse_feed_payload_from_recorded_sample(recorded_payload):
    docs, page_info = sina_7x24.parse_feed_payload(recorded_payload)
    assert len(docs) == 7
    assert page_info["totalPage"] == 9
    assert page_info["totalNum"] == 900
    assert page_info["page"] == 1

    by_id = {doc["source_id"]: doc for doc in docs}
    # cn 个股 + fund 多标的
    multi = by_id["5132361"]
    assert {s["symbol"] for s in multi["stocks"]} == {"sh688333", "159352"}
    assert multi["stocks"][0]["ts_code"] == "688333.SH"
    # cn 指数 + 新浪板块码（均可映射/识别）
    idx = by_id["5132331"]
    symbols = {s["symbol"]: s for s in idx["stocks"]}
    assert symbols["sz399417"]["ts_code"] == "399417.SZ" and symbols["sz399417"]["is_index"] is True
    assert symbols["si930997"]["ts_code"] is None and symbols["si930997"]["is_index"] is True
    # 仅板块码
    assert by_id["5132311"]["stocks"][0]["is_index"] is True
    # 美股条目不映射
    us = by_id["5132368"]
    assert us["stocks"] and all(s["ts_code"] is None and s["is_index"] is False for s in us["stocks"])
    # 空 stocks
    assert by_id["5132367"]["stocks"] == []
    # 时间口径：全部为 UTC naive 且早于北京时间 8 小时
    assert all(doc["publish_time"] is not None and doc["publish_time"].tzinfo is None for doc in docs)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "x",
        [],
        {},
        {"result": None},
        {"result": {"data": None}},
        {"result": {"data": {}}},
        {"result": {"data": {"feed": "x"}}},
        {"result": {"data": {"feed": {"list": "x"}}}},
    ],
)
def test_parse_feed_payload_bad_structure(payload):
    assert sina_7x24.parse_feed_payload(payload) == ([], {})


def test_parse_feed_payload_skips_non_dict_items():
    payload = {"result": {"data": {"feed": {"list": ["x", {"id": 2, "rich_text": "ok"}], "page_info": {"page": 1}}}}}
    docs, page_info = sina_7x24.parse_feed_payload(payload)
    assert [doc["source_id"] for doc in docs] == ["2"]
    assert page_info == {"page": 1}


# --------------------------------------------------------------------------- #
# fetch_page
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload, *, raise_exc: Exception | None = None):
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
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, params: dict[str, Any] | None = None):
        self.calls.append((url, params or {}))
        return self._response


def _as_client(fake: _FakeClient) -> httpx.AsyncClient:
    """把假客户端伪装为 httpx.AsyncClient（仅满足 fetch_page 的形参类型）。"""
    return cast("httpx.AsyncClient", fake)


async def test_fetch_page_returns_payload_and_sends_params():
    payload = {"result": {"data": {"feed": {"list": [], "page_info": {"page": 3}}}}}
    client = _FakeClient(_FakeResponse(payload))
    result = await sina_7x24.fetch_page(_as_client(client), page=3, page_size=20)
    assert result is payload
    url, params = client.calls[0]
    assert url == sina_7x24.SINA_7X24_FEED_URL
    assert params["page"] == 3 and params["page_size"] == 20


async def test_fetch_page_non_dict_json_returns_empty():
    client = _FakeClient(_FakeResponse(["not", "a", "dict"]))
    assert await sina_7x24.fetch_page(_as_client(client)) == {}


async def test_fetch_page_propagates_http_status_error():
    request = httpx.Request("GET", sina_7x24.SINA_7X24_FEED_URL)
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    client = _FakeClient(_FakeResponse({}, raise_exc=exc))
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await sina_7x24.fetch_page(_as_client(client))
    assert excinfo.value.response.status_code == 500
