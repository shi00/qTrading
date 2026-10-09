"""新浪个股新闻（vCB_AllNewsStock）解析内核与抓取（N1-2）。

数据源
------
``GET https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{sh600519}.phtml``
HTML，**GB18030 编码**（实测响应 charset 声明 ``gbk``，属 GB18030 子集），每页 40 条，
实测 2026-10-08 约 1.7 s。分页参数 ``Page``（``?Page=2`` 实测 200 / 40 条）。

列表结构（实测）
----------------
``<div class="datelist"><ul>`` 内每行形如::

    &nbsp;&nbsp;&nbsp;&nbsp;2026-10-08&nbsp;18:03&nbsp;&nbsp;<a target='_blank' href='URL'>标题</a> <br>

新闻行 ``href`` 用**单引号**；页面其余导航链接用双引号（实测 161 处），故规格正则
（仅匹配单引号 ``href='...'``）天然只命中新闻行，无需额外限定 ``datelist`` 区块。

时间口径
--------
行内时间 ``YYYY-MM-DD HH:MM`` **无秒**，换算前须补 ``:00`` 至 19 位（规格 §3.1），否则
按 16 位长度直接交解析会丢失时间。与 ``data/external/news_fetcher.py::_parse_news_time``
口径对齐：无时区文本按 CST 归属后转 **UTC tz-naive** 入库（:func:`parse_publish_time`）。

去重键
------
URL（规格 §3.3）。同页/跨页重复条目按 URL 去重（:func:`parse_news_list` 负责页内去重，
跨页由调用方按 ``source_id`` 汇总）。

边界说明
--------
- 本模块**不 import** ``data.external.news_fetcher``：既避免 ``news_sources`` ↔
  ``news_fetcher`` 循环导入，也为链 D（N3-4 修改 ``news_fetcher.py``）与本模块文件
  不相交留出并发空间；时间解析就地实现（口径一致，由单测与 ``_parse_news_time`` 对齐）。
- 关联标的：新浪个股页按代码抓取，标的由请求方已知；:func:`parse_news_list` 可传入
  ``symbol``（``sh``/``sz``/``bj`` + 6 位）并复用同包
  :func:`data.external.news_sources.sina_7x24.map_symbol_to_ts_code` 得到 ``ts_code``。
- 清洗（去 HTML 标签 / 实体 / 模板语、近重复去重）由语料库任务 N1-4 承担，本模块只做
  结构解析与字段归一。
"""

import datetime
import re
from typing import Any

import httpx

from data.external.news_sources.sina_7x24 import map_symbol_to_ts_code
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.proxy_manager import ProxyManager
from utils.time_utils import to_utc_for_db

SOURCE_NAME = "sina_stock"
SINA_STOCK_BASE_URL = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol"
SINA_STOCK_HOST = "vip.stock.finance.sina.com.cn"
SINA_STOCK_REFERER = "https://vip.stock.finance.sina.com.cn/"
FETCH_TIMEOUT_SECONDS = 10.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PAGE_SIZE = 40
NEWS_ENCODING = "gb18030"

# 规格 §3.1 列表行正则：无秒时间 + 单引号 href（页面导航链接为双引号，天然不命中）
LIST_ROW_RE = re.compile(r"(\d{4}-\d{2}-\d{2})&nbsp;(\d{2}:\d{2})&nbsp;&nbsp;<a[^>]*href='([^']+)'[^>]*>([^<]+)</a>")


def build_url(symbol: str, page: int = 1) -> str:
    """构造个股新闻列表页 URL。

    - ``page == 1``：规格 §3.1 的干净 URL（``.../symbol/{symbol}.phtml``）；
    - ``page > 1``：追加实测确认的分页参数（``?Page={page}``）。
    """
    base = f"{SINA_STOCK_BASE_URL}/{symbol}.phtml"
    return base if page <= 1 else f"{base}?Page={page}"


def build_client_kwargs() -> dict[str, Any]:
    """构造 ``httpx`` 客户端 kwargs：按 NO_PROXY 语义注入代理 + 常规 UA / Referer / timeout。"""
    kwargs: dict[str, Any] = dict(ProxyManager.get_httpx_proxy_kwargs(SINA_STOCK_HOST))
    kwargs["timeout"] = FETCH_TIMEOUT_SECONDS
    kwargs["headers"] = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SINA_STOCK_REFERER,
    }
    return kwargs


def decode_html(content: bytes | str) -> str:
    """把响应体解码为文本（GB18030；非法字节用替换符兜底，不抛异常）。

    新浪个股页实测 charset 声明 ``gbk``，GB18030 为其超集，统一按 GB18030 解码。
    """
    if isinstance(content, str):
        return content
    return content.decode(NEWS_ENCODING, errors="replace")


def parse_publish_time(raw: object) -> datetime.datetime | None:
    """行内时间（``YYYY-MM-DD HH:MM``，北京时间，**无秒**）→ UTC tz-naive。

    无秒时先补 ``:00`` 至 19 位再解析（规格 §3.1）；口径与
    ``news_fetcher._parse_news_time`` 一致：无时区文本按 CST 归属后转 UTC、去 tzinfo。
    长度不在 19~23 位或格式不符返回 ``None``（调用方按缺失时间处理）。
    """
    text = str(raw).strip() if raw is not None else ""
    if len(text) == 16:  # YYYY-MM-DD HH:MM（无秒）
        text = f"{text}:00"
    if len(text) < 19 or len(text) > 23:
        return None
    try:
        parsed = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007  行内时间为 CST 本地时间文本，无时区字面量
    except ValueError:
        return None
    return to_utc_for_db(parsed)


def parse_news_list(html: object, *, symbol: str | None = None) -> list[dict[str, Any]]:
    """解析列表页 HTML 为归一化文档列表（按 URL 去重、保留首次出现顺序）。

    归一化 schema::

        {
            "source": "sina_stock",
            "source_id": str,               # 去重键（规格 §3.3）= URL
            "text": str,                    # 标题
            "publish_time": datetime | None,  # UTC tz-naive
            "source_url": str,
            "symbol": str | None,           # 请求所用的新浪代码（如 sh600519）
            "ts_code": str | None,          # 由 symbol 映射（不可映射为 None）
        }

    ``symbol`` 为本次抓取所用的新浪代码（标的由请求方已知）；缺省时 ``symbol``/``ts_code``
    为 ``None``（调用方自行补关联）。
    """
    if not isinstance(html, str) or not html:
        return []
    ts_code = map_symbol_to_ts_code(symbol) if symbol else None
    seen: set[str] = set()
    docs: list[dict[str, Any]] = []
    for date_part, time_part, url, title in LIST_ROW_RE.findall(html):
        source_url = url.strip()
        if not source_url or source_url in seen:
            continue
        seen.add(source_url)
        docs.append(
            {
                "source": SOURCE_NAME,
                "source_id": source_url,
                "text": title.strip(),
                "publish_time": parse_publish_time(f"{date_part} {time_part}"),
                "source_url": source_url,
                "symbol": symbol,
                "ts_code": ts_code,
            }
        )
    return docs


@log_async_operation(operation_name="sina_stock_fetch_page", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
async def fetch_page(
    client: httpx.AsyncClient,
    symbol: str,
    page: int = 1,
) -> str:
    """请求单页个股新闻列表并返回 **GB18030 解码后**的 HTML 文本。

    仅发请求 + 解码，不解析（解析交给 :func:`parse_news_list`）；异常向上抛，由调用方决定
    限速 / 熔断 / 降级。httpx async-native，可被外层 ``asyncio.wait_for`` 取消。
    """
    response = await client.get(build_url(symbol, page))
    response.raise_for_status()
    return decode_html(response.content)
