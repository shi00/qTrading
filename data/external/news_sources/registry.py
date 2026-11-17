"""新闻源采集插件注册表（N1-4，规格 §3.3「采集器按源插件化」）。

每个插件把一个源模块（``sina_7x24`` / ``sina_stock`` / ``cninfo``）适配为统一接口：

- :meth:`SourcePlugin.build_queries`：按源构造请求单元（含分页 / 日期 / 符号展开）；
- :meth:`SourcePlugin.client_kwargs`：``httpx.AsyncClient`` 的 kwargs（代理 + UA + 超时）；
- :meth:`SourcePlugin.request`：发单次请求，返回**原始**响应（JSON dict / HTML 文本）；
- :meth:`SourcePlugin.has_expected_envelope`：校验该源必需的响应骨架（区分真实空与解析失败）；
- :meth:`SourcePlugin.parse_page`：先校验 envelope 再解析，返回带状态的 :class:`ParseOutcome`；
- :meth:`SourcePlugin.parse`：原始响应 → 归一化文档列表。

插件并声明 ``source_kind``（news / announcement）、``text_kind``（flash / title，决定是否截断
120 字）、``rate_limit_seconds``（规格 §3.3「每源 ≥ 2~3 秒 / 请求」）与 ``drop_routine``
（是否过滤例行条目）。

各源解析内核保持独立（N1-1/2/3），本模块仅做适配，**不复制解析逻辑**；位于 ``data/`` 层。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import httpx

from data.external.news_sources import cninfo, sina_7x24, sina_stock

# 规格 §3.3：每源每请求间隔 ≥ 2~3 秒；取中值 2.5s
DEFAULT_RATE_LIMIT_SECONDS = 2.5

SOURCE_KIND_NEWS = "news"
SOURCE_KIND_ANNOUNCEMENT = "announcement"

# 单页解析状态：有效数据 / 真实合法空 / 解析失败（envelope 不符或解析抛错，不得计为成功）
ParseStatus = Literal["ok", "valid_empty", "invalid"]


@dataclass(frozen=True)
class ParseOutcome:
    """单页解析结果：把「真实空」与「解析失败」分开，供采集器选择计数与熔断路径。"""

    docs: list[dict[str, Any]]
    status: ParseStatus


class EnvelopeError(Exception):
    """响应结构不符合该源必需 envelope（HTTP 200 拦截页 / 登录页 / schema 漂移）。

    消息只含响应类型，不回显响应内容（避免把整页 HTML 写入日志）。
    """


@dataclass(frozen=True)
class CollectQuery:
    """单次请求单元（各源按需取用字段，未用字段忽略）。"""

    page: int = 1
    page_size: int | None = None
    start_date: str = ""
    end_date: str = ""
    symbol: str = ""
    column: str = ""
    search_key: str = ""


@dataclass(frozen=True)
class CollectParams:
    """一次采集运行的源无关参数（由 CLI 解析后传入各插件）。"""

    pages: int = 1
    page_size: int | None = None
    start_date: str = ""
    end_date: str = ""
    symbols: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    search_key: str = ""


class SourcePlugin(ABC):
    """新闻源采集插件统一接口。"""

    name: ClassVar[str]
    source_kind: ClassVar[str]
    text_kind: ClassVar[str]
    rate_limit_seconds: ClassVar[float] = DEFAULT_RATE_LIMIT_SECONDS
    drop_routine: ClassVar[bool] = False

    @abstractmethod
    def build_queries(self, params: CollectParams) -> list[CollectQuery]:
        """按源展开请求单元（分页 / 日期 / 符号 / 板块）。"""

    @abstractmethod
    def client_kwargs(self) -> dict[str, Any]:
        """``httpx.AsyncClient`` 的 kwargs（代理 / UA / 超时）。"""

    @abstractmethod
    async def request(self, client: httpx.AsyncClient, query: CollectQuery) -> Any:
        """发单次请求，返回原始响应（JSON dict 或 HTML 文本）；异常向上抛。"""

    @abstractmethod
    def parse(self, raw: Any, query: CollectQuery) -> list[dict[str, Any]]:
        """原始响应 → 归一化文档列表。"""

    @abstractmethod
    def has_expected_envelope(self, raw: Any) -> bool:
        """响应是否具备该源必需的 envelope 结构（区分「真实空」与「解析失败」）。"""

    def parse_page(self, raw: Any, query: CollectQuery) -> ParseOutcome:
        """先校验必需 envelope 再解析，返回带状态的单页结果。

        envelope 不符（登录 / 拦截 HTML、wrong-type、缺必需字段）直接返回 ``invalid``，**不调用**
        :meth:`parse`、不计为成功；envelope 合法但解析出 0 条为 ``valid_empty``（真实空）。
        """
        if not self.has_expected_envelope(raw):
            return ParseOutcome([], "invalid")
        docs = self.parse(raw, query)
        return ParseOutcome(docs, "ok" if docs else "valid_empty")


class Sina7x24Plugin(SourcePlugin):
    """新浪财经 7x24 快讯（快讯正文，截断 120 字）。"""

    name = sina_7x24.SOURCE_NAME
    source_kind = SOURCE_KIND_NEWS
    text_kind = "flash"

    def build_queries(self, params: CollectParams) -> list[CollectQuery]:
        page_size = params.page_size or sina_7x24.DEFAULT_PAGE_SIZE
        return [CollectQuery(page=page, page_size=page_size) for page in range(1, params.pages + 1)]

    def client_kwargs(self) -> dict[str, Any]:
        return sina_7x24.build_client_kwargs()

    async def request(self, client: httpx.AsyncClient, query: CollectQuery) -> Any:
        page_size = query.page_size or sina_7x24.DEFAULT_PAGE_SIZE
        return await sina_7x24.fetch_page(client, query.page, page_size)

    def parse(self, raw: Any, query: CollectQuery) -> list[dict[str, Any]]:
        docs, _ = sina_7x24.parse_feed_payload(raw)
        return docs

    def has_expected_envelope(self, raw: Any) -> bool:
        return sina_7x24.has_expected_envelope(raw)


class SinaStockPlugin(SourcePlugin):
    """新浪个股新闻（标题，原样保留）；需显式提供 symbol 清单。"""

    name = sina_stock.SOURCE_NAME
    source_kind = SOURCE_KIND_NEWS
    text_kind = "title"

    def build_queries(self, params: CollectParams) -> list[CollectQuery]:
        return [
            CollectQuery(symbol=symbol, page=page) for symbol in params.symbols for page in range(1, params.pages + 1)
        ]

    def client_kwargs(self) -> dict[str, Any]:
        return sina_stock.build_client_kwargs()

    async def request(self, client: httpx.AsyncClient, query: CollectQuery) -> Any:
        return await sina_stock.fetch_page(client, query.symbol, query.page)

    def parse(self, raw: Any, query: CollectQuery) -> list[dict[str, Any]]:
        return sina_stock.parse_news_list(raw, symbol=query.symbol or None)

    def has_expected_envelope(self, raw: Any) -> bool:
        return sina_stock.has_expected_envelope(raw)


class CninfoPlugin(SourcePlugin):
    """巨潮资讯公告（公告标题，原样保留；过滤例行条目）。"""

    name = cninfo.SOURCE_NAME
    source_kind = SOURCE_KIND_ANNOUNCEMENT
    text_kind = "title"
    drop_routine = True

    def build_queries(self, params: CollectParams) -> list[CollectQuery]:
        columns = params.columns or (cninfo.DEFAULT_COLUMN,)
        page_size = params.page_size or cninfo.DEFAULT_PAGE_SIZE
        return [
            CollectQuery(
                column=column,
                page=page,
                page_size=page_size,
                start_date=params.start_date,
                end_date=params.end_date,
                search_key=params.search_key,
            )
            for column in columns
            for page in range(1, params.pages + 1)
        ]

    def client_kwargs(self) -> dict[str, Any]:
        return cninfo.build_client_kwargs()

    async def request(self, client: httpx.AsyncClient, query: CollectQuery) -> Any:
        page_size = query.page_size or cninfo.DEFAULT_PAGE_SIZE
        return await cninfo.fetch_page(
            client,
            query.column or cninfo.DEFAULT_COLUMN,
            query.start_date,
            query.end_date,
            page_num=query.page,
            page_size=page_size,
            search_key=query.search_key,
        )

    def parse(self, raw: Any, query: CollectQuery) -> list[dict[str, Any]]:
        docs, _ = cninfo.parse_announcement_payload(raw)
        return docs

    def has_expected_envelope(self, raw: Any) -> bool:
        return cninfo.has_expected_envelope(raw)


_PLUGIN_TYPES: tuple[type[SourcePlugin], ...] = (Sina7x24Plugin, SinaStockPlugin, CninfoPlugin)


def all_plugins() -> list[SourcePlugin]:
    """全部内置插件（每次返回新实例，避免共享可变状态）。"""
    return [plugin_type() for plugin_type in _PLUGIN_TYPES]


def get_plugin(name: str) -> SourcePlugin:
    """按源名取插件；未知源抛 ``ValueError``（列出可选源）。"""
    for plugin in all_plugins():
        if plugin.name == name:
            return plugin
    known = ", ".join(sorted(p.name for p in all_plugins()))
    raise ValueError(f"未知新闻源 '{name}'，可选：{known}")
