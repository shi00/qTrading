"""External services layer - 外部服务层"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.external.news_fetcher import NewsFetcher
    from data.external.tushare_client import TushareClient


def __getattr__(name):
    if name == "NewsFetcher":
        from data.external.news_fetcher import NewsFetcher

        return NewsFetcher
    if name == "TushareClient":
        from data.external.tushare_client import TushareClient

        return TushareClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TushareClient", "NewsFetcher"]
