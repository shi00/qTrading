"""巨潮资讯公告（hisAnnouncement/query）解析内核与抓取（N1-3）。

数据源
------
``POST https://www.cninfo.com.cn/new/hisAnnouncement/query``，表单参数
``column`` / ``tabName=fulltext`` / ``seDate=YYYY-MM-DD~YYYY-MM-DD`` / ``pageNum`` /
``pageSize``（规格 §3.1；沪市用 ``column=sse``，深市 ``szse``，北交所 ``bj``）。
实测 2026-10-09：单日深市 ``totalRecordNum=743``、单页 30 条；``isHLtitle=true`` 时命中
关键词（``searchkey``）的标题片段被 ``<em>`` 包裹。

响应结构
--------
顶层含 ``announcements[]`` / ``totalRecordNum`` / ``hasMore`` / ``totalpages``；每条关键字段
（实测）::

    announcementId        # 去重键（规格 §3.3）
    secCode / secName     # 6 位代码 + 简称（如 300638 / 广和通）
    announcementTitle      # 标题，可能含 <em> 高亮
    announcementTime       # 毫秒时间戳（UTC 绝对时刻）
    adjunctUrl             # 相对 PDF 路径（finalpage/YYYY-MM-DD/{id}.PDF）
    pageColumn             # 板块列（实测 SZCY 深创业板 / SZZB 深主板 / SHKCB 沪科创 / BJS 北交所）
    announcementTypeName   # 公告类型名（实测多为 null）

时间口径
--------
``announcementTime`` 为**毫秒时间戳（UTC 绝对时刻）**，与新浪源的无时区文本不同：须先构造
**tz-aware(UTC)** 再经 :func:`utils.time_utils.to_utc_for_db` 转 UTC 去 tzinfo，
**不得按北京时间二次换算**（否则会偏移 8 小时）。

标题清洗
--------
:func:`clean_title` 依次：① 去 HTML 标签 / 实体（含 ``<em>``）；② 去公司全称前缀；
③ 去「关于……的公告」外壳，保留事件主体（规格 §4.2）。

边界说明
--------
- 本模块**不 import** ``data.external.news_fetcher``（避免 ``news_sources`` ↔
  ``news_fetcher`` 循环导入，并为链 D 的 N3-4 修改 ``news_fetcher.py`` 留出并发空间）。
- 仅做结构解析与字段归一；模板语清洗 / 近重复去重 / 入库由语料库任务 N1-4 承担。
"""

import datetime
import html
import math
import re
from typing import Any

import httpx

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.proxy_manager import ProxyManager
from utils.time_utils import to_utc_for_db

SOURCE_NAME = "cninfo"
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_HOST = "www.cninfo.com.cn"
CNINFO_REFERER = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn/"
FETCH_TIMEOUT_SECONDS = 10.0
DEFAULT_PAGE_SIZE = 30
DEFAULT_COLUMN = "szse"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 6 位证券代码；非此形态不做 ts_code 映射
_SEC_CODE_RE = re.compile(r"^\d{6}$")
# pageColumn 板块前缀 → Tushare 交易所后缀（实测 SZCY/SZZB/SHKCB/BJS）
_EXCHANGE_BY_COLUMN_PREFIX = {"SZ": "SZ", "SH": "SH", "BJ": "BJ"}
# HTML 标签（含 <em> 高亮）与空白归一
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
# 公司全称前缀：仅当「…公司」后紧跟「关于」时剥离（保守判定，避免吃掉事件主体）
_COMPANY_PREFIX_RE = re.compile(r"^[^\s关于]{2,25}?(?:股份有限公司|有限责任公司|有限公司|公司)(?=关于)")
# 「关于……的公告」外壳：仅整体匹配时剥离，保留其中的事件主体
_ABOUT_SHELL_RE = re.compile(r"^关于(.+?)的公告$")


def _opt_str(value: object) -> str | None:
    """归一化可选文本字段：``None`` / 空白 → ``None``，否则去首尾空白后的字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fmt_date(value: object) -> str:
    """把日期参数格式化为 ``YYYY-MM-DD``（接受 ``datetime.date`` / 字符串）。"""
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def build_query_params(
    column: str = DEFAULT_COLUMN,
    start_date: object = "",
    end_date: object = "",
    page_num: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search_key: str = "",
    is_hl_title: bool = True,
) -> dict[str, str]:
    """构造 ``hisAnnouncement/query`` 表单参数（与规格 §3.1 实测一致）。

    ``search_key`` 非空时附加 ``searchkey``（服务端会跨板块检索并给命中片段加 ``<em>``）。
    """
    params = {
        "column": column,
        "tabName": "fulltext",
        "seDate": f"{_fmt_date(start_date)}~{_fmt_date(end_date)}",
        "pageNum": str(page_num),
        "pageSize": str(page_size),
        "isHLtitle": "true" if is_hl_title else "false",
    }
    if search_key:
        params["searchkey"] = search_key
    return params


def build_client_kwargs() -> dict[str, Any]:
    """构造 ``httpx.AsyncClient`` 的 kwargs：按 NO_PROXY 语义注入代理 + 常规 UA / Referer / 表单头。"""
    kwargs: dict[str, Any] = dict(ProxyManager.get_httpx_proxy_kwargs(CNINFO_HOST))
    kwargs["timeout"] = FETCH_TIMEOUT_SECONDS
    kwargs["headers"] = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": CNINFO_REFERER,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    return kwargs


def strip_html(text: object) -> str:
    """去 HTML 标签（含 ``<em>`` 高亮）、反转义实体、归一空白（含 ``&nbsp;`` / 全角空格）。"""
    if text is None:
        return ""
    stripped = _HTML_TAG_RE.sub("", str(text))
    unescaped = html.unescape(stripped).replace("\u3000", " ")
    return _WS_RE.sub(" ", unescaped).strip()


def clean_title(raw: object) -> str:
    """公告标题清洗：去 HTML/``<em>`` → 去公司全称前缀 → 去「关于……的公告」外壳。

    规格 §4.2：保留事件主体。示例：``四川百利天恒药业股份有限公司关于获得药品注册证书的公告``
    → ``获得药品注册证书``。

    NOTE(lazy): 公司全称前缀仅在「…公司」后紧跟「关于」时剥离（保守判定）.
    ceiling: 形如「<公司全称>发行股份……报告书」无「关于」外壳的标题不剥离前缀.
    upgrade: 语料库（N1-4）实测出现大面积未剥离前缀且影响标注，改为按 secName / 全称词表匹配.
    """
    title = strip_html(raw)
    title = _COMPANY_PREFIX_RE.sub("", title, count=1)
    shell = _ABOUT_SHELL_RE.match(title)
    if shell:
        title = shell.group(1)
    return title.strip()


def parse_publish_time(raw: object) -> datetime.datetime | None:
    """``announcementTime``（毫秒时间戳，UTC 绝对时刻）→ UTC tz-naive。

    先构造 **tz-aware(UTC)** 再交 :func:`to_utc_for_db`（其对 tz-aware 输入只做 UTC 归一，不再按
    CST 换算），故**不按北京时间二次偏移**。非数值 / 非有限 / 超范围返回 ``None``。
    """
    if raw is None:
        return None
    try:
        milliseconds = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(milliseconds):
        return None
    try:
        aware = datetime.datetime.fromtimestamp(milliseconds / 1000.0, tz=datetime.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return to_utc_for_db(aware)


def map_to_ts_code(sec_code: object, page_column: object) -> str | None:
    """``secCode``（6 位）+ ``pageColumn``（板块前缀）→ Tushare ``ts_code``。

    ``pageColumn`` 前缀 ``SZ``/``SH``/``BJ`` 分别映射 ``.SZ``/``.SH``/``.BJ``；``secCode`` 非 6 位
    或 ``pageColumn`` 缺失 / 前缀未知时返回 ``None``（不臆造交易所）。
    """
    code = str(sec_code).strip() if sec_code is not None else ""
    if not _SEC_CODE_RE.match(code):
        return None
    column = str(page_column).strip().upper() if page_column is not None else ""
    for prefix, suffix in _EXCHANGE_BY_COLUMN_PREFIX.items():
        if column.startswith(prefix):
            return f"{code}.{suffix}"
    return None


def _build_source_url(adjunct_url: object) -> str:
    """相对 ``adjunctUrl`` → 巨潮静态 PDF 绝对 URL（实测 200 ``application/pdf``）；缺失返回空串。"""
    relative = _opt_str(adjunct_url)
    if not relative:
        return ""
    return CNINFO_STATIC_BASE + relative.lstrip("/")


def parse_announcement(item: object) -> dict[str, Any] | None:
    """把 ``announcements[]`` 单条归一化为统一文档；非 dict 或缺 ``announcementId`` 返回 ``None``。

    归一化 schema::

        {
            "source": "cninfo",
            "source_id": str,                 # 去重键（规格 §3.3）= announcementId
            "text": str,                      # clean_title（事件主体）
            "publish_time": datetime | None,   # UTC tz-naive
            "source_url": str,                # 巨潮静态 PDF URL
            "sec_code": str | None,           # 6 位代码
            "sec_name": str | None,           # 简称
            "ts_code": str | None,            # 由 secCode + pageColumn 映射
            "announcement_type_name": str | None,
        }
    """
    if not isinstance(item, dict):
        return None
    announcement_id = _opt_str(item.get("announcementId"))
    if announcement_id is None:
        return None
    sec_code = _opt_str(item.get("secCode"))
    return {
        "source": SOURCE_NAME,
        "source_id": announcement_id,
        "text": clean_title(item.get("announcementTitle")),
        "publish_time": parse_publish_time(item.get("announcementTime")),
        "source_url": _build_source_url(item.get("adjunctUrl")),
        "sec_code": sec_code,
        "sec_name": _opt_str(item.get("secName")),
        "ts_code": map_to_ts_code(sec_code, item.get("pageColumn")),
        "announcement_type_name": _opt_str(item.get("announcementTypeName")),
    }


def parse_announcement_payload(payload: object) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """解析响应体为 ``(归一化文档列表, 分页信息)``；结构不符时返回 ``([], {})``。

    分页信息键：``total``（``totalRecordNum``）/ ``has_more``（``hasMore``）/ ``total_pages``
    （``totalpages``）。
    """
    if not isinstance(payload, dict):
        return [], {}
    raw_list = payload.get("announcements")
    if not isinstance(raw_list, list):
        return [], {}
    docs: list[dict[str, Any]] = []
    for item in raw_list:
        doc = parse_announcement(item)
        if doc is not None:
            docs.append(doc)
    page_info = {
        "total": payload.get("totalRecordNum"),
        "has_more": payload.get("hasMore"),
        "total_pages": payload.get("totalpages"),
    }
    return docs, page_info


@log_async_operation(operation_name="cninfo_fetch_page", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
async def fetch_page(
    client: httpx.AsyncClient,
    column: str = DEFAULT_COLUMN,
    start_date: object = "",
    end_date: object = "",
    page_num: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search_key: str = "",
) -> dict[str, Any]:
    """请求单页公告并返回**原始** JSON payload（不解析）。

    仅发 POST 表单请求、不解析（解析交给 :func:`parse_announcement_payload`）；异常向上抛，由
    调用方决定限速 / 熔断 / 降级。httpx async-native，可被外层 ``asyncio.wait_for`` 取消。
    """
    params = build_query_params(column, start_date, end_date, page_num, page_size, search_key)
    response = await client.post(CNINFO_QUERY_URL, data=params)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}
