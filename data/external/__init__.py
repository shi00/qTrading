"""External services layer - 外部服务层"""


def __getattr__(name):
    if name == "NewsFetcher":
        from data.external.news_fetcher import NewsFetcher

        return NewsFetcher
    if name == "NewsSubscriptionService":
        from data.external.news_subscription import NewsSubscriptionService

        return NewsSubscriptionService
    if name == "TushareClient":
        from data.external.tushare_client import TushareClient

        return TushareClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TushareClient", "NewsFetcher", "NewsSubscriptionService"]
