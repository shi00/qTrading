"""新浪财经 7x24 快讯（zhibo_id=152）解析内核与抓取（N1-1）。

数据源
------
``GET https://zhibo.sina.com.cn/api/zhibo/feed``（JSON，无签名 / 无 Cookie），
参数 ``page`` / ``page_size`` / ``zhibo_id=152`` / ``tag_id=0`` / ``dire=f`` / ``dpc=1`` / ``type=0``。
响应结构：``result.data.feed.list[]``；``page_info`` 含 ``totalPage`` / ``totalNum``。

条目字段（实测 2026-10-08）：``id``(int) / ``rich_text``(正文) / ``create_time``
(``YYYY-MM-DD HH:MM:SS``，北京时间) / ``tag``(``[{"id","name"}]``) / ``docurl``(详情页)。
``ext`` 为 **JSON 字符串**（须 ``json.loads``），内含 ``stocks`` / ``docurl`` / ``docid`` 等。

A 股映射规则（本次实测确认，替代规格 §3.1「尚未实测」的待决项）
------------------------------------------------------------
``ext.stocks[]`` 每项形如 ``{"market","symbol","key"[,"sym_party_status"]}``；``market``
实测取值含 ``cn``(A 股) / ``us`` / ``hk`` / ``fund`` / ``foreign`` / ``commodity`` /
``global`` / ``worldIndex`` / ``sb`` / ``uk`` / ``CFF``（大小写不统一，见实测样本）。

- **A 股 = ``market == "cn"``**；``symbol`` 形如 ``sh688333`` / ``sz002544`` / ``bj920670``
  （交易所小写前缀 ``sh`` / ``sz`` / ``bj`` + 6 位数字），``key`` 为中文简称。
- ``ts_code`` 映射：``sh``+6 位 → ``NNNNNN.SH``，``sz`` → ``.SZ``，``bj`` → ``.BJ``；其余
  （新浪板块码 ``si*`` / ``sih*``、基金裸码、港美股字母码等）**不可映射**，返回 ``None``。
- **陷阱**：``cn`` 市场**混入指数 / 板块**——``sh000xxx``（上证指数系列，如 ``sh000001``
  上证综指 / ``sh000905`` 中证 500）、``sz399xxx``（深证指数系列）、``bj899xxx``（北证 50）、
  ``si*`` / ``sih*``（新浪板块码，含大写，如 ``siH30535``）。这些仍可映射为合法指数 ``ts_code``
  （指数与个股号段不冲突，如 ``000001.SH`` 为指数、``000001.SZ`` 为平安银行），但语义上
  不是个股，须由 :func:`is_index_code` 区分后由调用方决定是否保留。

时间口径
--------
与 ``data/external/news_fetcher.py::_parse_news_time`` 对齐：``create_time`` 为无时区的
北京时间文本，按 CST 归属本地化后转 **UTC tz-naive** 入库存库（:func:`parse_publish_time`）。

边界说明
--------
- 本模块**不 import** ``data.external.news_fetcher``：既避免 ``news_sources`` ↔ ``news_fetcher``
  循环导入，也为链 D（N3-4 修改 ``news_fetcher.py``）与本模块文件不相交留出并发空间；
  时间解析就地实现（口径一致）。
- 在线集成（``get_latest_global_news`` 新增新浪 7x24）由链 D 的 N3-4 承担；本模块的
  :func:`fetch_page` / :func:`build_client_kwargs` 供离线采集器与后续集成复用。
"""

import datetime
import json
import logging
import re
from typing import Any

import httpx

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.proxy_manager import ProxyManager
from utils.time_utils import to_utc_for_db

logger = logging.getLogger(__name__)

SOURCE_NAME = "sina_7x24"
SINA_7X24_FEED_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
SINA_7X24_HOST = "zhibo.sina.com.cn"
SINA_7X24_REFERER = "https://finance.sina.com.cn/7x24/"
SINA_7X24_ZHIBO_ID = 152
DEFAULT_PAGE_SIZE = 100
FETCH_TIMEOUT_SECONDS = 10.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ts_code 交易所后缀（小写前缀 → Tushare 大写后缀）
_EXCHANGE_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
# cn 市场可映射为 ts_code 的 symbol：sh/sz/bj + 恰好 6 位数字
_CN_SYMBOL_RE = re.compile(r"^(sh|sz|bj)(\d{6})$")
# cn 市场混入的指数 / 板块码（实测四类）
_INDEX_PATTERNS = (
    re.compile(r"^si"),  # 新浪板块 / 概念码（si* / sih*，可能含大写）
    re.compile(
        r"^sh000\d{3}$"
    ),  # 上证指数系列（sh000001 上证综指 / sh000905 中证500 / sh000852 中证1000 / sh000159 沪股通）
    re.compile(r"^sz399\d{3}$"),  # 深证指数系列（sz399006 创业板指 / sz399417 新能源车）
    re.compile(r"^bj899\d{3}$"),  # 北证指数系列（bj899050 北证50）
)


def build_feed_params(
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    *,
    zhibo_id: int = SINA_7X24_ZHIBO_ID,
) -> dict[str, int | str]:
    """构造 feed 请求参数（与规格 §3.1 的实测 URL 一致）。"""
    return {
        "page": page,
        "page_size": page_size,
        "zhibo_id": zhibo_id,
        "tag_id": 0,
        "dire": "f",
        "dpc": 1,
        "type": 0,
    }


def build_client_kwargs() -> dict[str, Any]:
    """构造 ``httpx.AsyncClient`` 的 kwargs：按 NO_PROXY 语义注入代理 + 常规 UA / timeout。"""
    kwargs: dict[str, Any] = dict(ProxyManager.get_httpx_proxy_kwargs(SINA_7X24_HOST))
    kwargs["timeout"] = FETCH_TIMEOUT_SECONDS
    kwargs["headers"] = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": SINA_7X24_REFERER,
    }
    return kwargs


def parse_ext(raw: object) -> dict[str, Any]:
    """把条目 ``ext`` 字段解析为 dict。

    ``ext`` 是 **JSON 字符串**（规格 §3.1）；同时容忍已是 dict 的形态。空串 / 非法 JSON /
    非 dict 结果一律返回 ``{}``（调用方按「无关联标的」处理，不影响正文入库）。
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def map_symbol_to_ts_code(symbol: object) -> str | None:
    """``cn`` 市场 ``symbol``（``sh``/``sz``/``bj`` + 6 位）→ Tushare ``ts_code``。

    - 仅接受 ``sh``/``sz``/``bj`` 前缀 + 恰好 6 位数字，输出 ``NNNNNN.SH`` / ``.SZ`` / ``.BJ``。
    - 其余（新浪板块码 ``si*``/``sih*``、基金裸码、港美股字母码等）返回 ``None``（不可映射）。
    - **不区分指数与个股**：指数 ``ts_code`` 亦为合法格式（如 ``sh000001`` → ``000001.SH``），
      调用方须结合 :func:`is_index_code` 判断语义。
    """
    if not isinstance(symbol, str):
        return None
    matched = _CN_SYMBOL_RE.match(symbol.strip().lower())
    if not matched:
        return None
    return f"{matched.group(2)}.{_EXCHANGE_SUFFIX[matched.group(1)]}"


def is_index_code(symbol: object) -> bool:
    """判断 ``cn`` 市场 ``symbol`` 是否为指数 / 板块码（非个股）。

    实测 ``cn`` 市场混入四类非个股码：``sh000xxx``（上证指数系列）/ ``sz399xxx``（深证指数
    系列）/ ``bj899xxx``（北证 50）/ ``si*``·``sih*``（新浪板块码）。

    NOTE(lazy): 采用「指数前缀白名单」判定，而非「交易所个股号段白名单」正向判定.
    ceiling: 仅覆盖实测到的四类指数 / 板块前缀，若新浪新增未见过前缀会漏判为个股.
    upgrade: 实测出现白名单外的指数码，或下游发现指数被误当个股，即改为按
      sh/sz/bj 前缀 + 各交易所个股号段白名单的正向判定.
    """
    if not isinstance(symbol, str):
        return False
    normalized = symbol.strip().lower()
    return any(pattern.match(normalized) for pattern in _INDEX_PATTERNS)


def normalize_stocks(ext: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ``ext.stocks`` 归一化关联标的列表（含 ``ts_code`` 映射与指数标记）。

    返回项：``{"market","symbol","name","ts_code","is_index"}``；``ts_code`` / ``is_index``
    仅对 ``market == "cn"`` 有意义（非 A 股恒为 ``None`` / ``False``）。跳过缺 market/symbol
    或非 dict 的项。
    """
    stocks = ext.get("stocks")
    if not isinstance(stocks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for entry in stocks:
        if not isinstance(entry, dict):
            continue
        market = str(entry.get("market") or "").strip().lower()
        symbol_raw = entry.get("symbol")
        symbol = str(symbol_raw).strip() if symbol_raw is not None else ""
        if not market or not symbol:
            continue
        is_cn = market == "cn"
        normalized.append(
            {
                "market": market,
                "symbol": symbol,
                "name": str(entry.get("key") or "").strip(),
                "ts_code": map_symbol_to_ts_code(symbol) if is_cn else None,
                "is_index": is_index_code(symbol) if is_cn else False,
            }
        )
    return normalized


def parse_publish_time(raw: object) -> datetime.datetime | None:
    """``create_time``（``YYYY-MM-DD HH:MM:SS``，北京时间）→ UTC tz-naive。

    口径与 ``news_fetcher._parse_news_time`` 一致：无时区文本按 CST 归属后转 UTC 去 tzinfo；
    长度不在 19~23 位或格式不符返回 ``None``（调用方按缺失时间处理）。
    """
    text = str(raw).strip() if raw is not None else ""
    if len(text) < 19 or len(text) > 23:
        return None
    try:
        # create_time 为北京时间文本、无时区字面量；下方 to_utc_for_db 按 CST 归属
        parsed = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007  CST 本地时间文本无时区字面量
    except ValueError:
        return None
    return to_utc_for_db(parsed)


def _normalize_tags(raw: object) -> list[str]:
    """``tag``（``[{"id","name"}]``）归一化为 name 字符串列表；容忍字符串元素。"""
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
        elif isinstance(entry, str):
            name = entry.strip()
        else:
            name = ""
        if name:
            names.append(name)
    return names


def parse_feed_item(item: object) -> dict[str, Any] | None:
    """把 ``feed.list[]`` 单条归一化为统一文档；结构异常或缺 ``id`` 返回 ``None``。

    归一化 schema::

        {
            "source": "sina_7x24",
            "source_id": str,               # 去重键（规格 §3.3）
            "text": str,                    # rich_text 正文
            "publish_time": datetime | None,  # UTC tz-naive
            "tags": list[str],
            "source_url": str,              # 详情页 URL
            "stocks": list[dict],           # 见 normalize_stocks
        }
    """
    if not isinstance(item, dict):
        return None
    source_id = item.get("id")
    if source_id is None:
        return None
    ext = parse_ext(item.get("ext"))
    source_url = str(item.get("docurl") or ext.get("docurl") or "").strip()
    return {
        "source": SOURCE_NAME,
        "source_id": str(source_id),
        "text": str(item.get("rich_text") or "").strip(),
        "publish_time": parse_publish_time(item.get("create_time")),
        "tags": _normalize_tags(item.get("tag")),
        "source_url": source_url,
        "stocks": normalize_stocks(ext),
    }


def _extract_feed(payload: object) -> dict[str, Any]:
    """定位 ``result.data.feed``；任一层级缺失 / 类型不符返回 ``{}``。"""
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    if not isinstance(data, dict):
        return {}
    feed = data.get("feed")
    return feed if isinstance(feed, dict) else {}


def parse_feed_payload(payload: object) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """解析 feed 响应体为 ``(归一化文档列表, page_info)``。

    ``page_info`` 含 ``totalPage`` / ``totalNum`` / ``page`` 等；结构不符时返回 ``([], {})``。
    """
    feed = _extract_feed(payload)
    if not feed:
        return [], {}
    raw_list = feed.get("list")
    docs: list[dict[str, Any]] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            doc = parse_feed_item(item)
            if doc is not None:
                docs.append(doc)
    page_info = feed.get("page_info")
    return docs, page_info if isinstance(page_info, dict) else {}


@log_async_operation(operation_name="sina_7x24_fetch_page", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
async def fetch_page(
    client: httpx.AsyncClient,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """请求单页 feed，返回**原始** JSON payload（不解析）。

    仅发请求、不解析（解析交给 :func:`parse_feed_payload`）；异常向上抛，由调用方决定
    限速 / 熔断 / 降级。httpx async-native，可被外层 ``asyncio.wait_for`` 取消。
    """
    response = await client.get(SINA_7X24_FEED_URL, params=build_feed_params(page, page_size))
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}
