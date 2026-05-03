"""DAO layer - 数据访问对象"""


def __getattr__(name):
    _mapping = {
        "BaseDao": "data.persistence.daos.base_dao",
        "FinancialDao": "data.persistence.daos.financial_dao",
        "HolderDao": "data.persistence.daos.holder_dao",
        "MacroDao": "data.persistence.daos.macro_dao",
        "MarketDao": "data.persistence.daos.market_dao",
        "QuoteDao": "data.persistence.daos.quote_dao",
        "ScreenerDao": "data.persistence.daos.screener_dao",
        "StockDao": "data.persistence.daos.stock_dao",
        "SyncDao": "data.persistence.daos.sync_dao",
    }
    if name in _mapping:
        import importlib

        mod = importlib.import_module(_mapping[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseDao",
    "FinancialDao",
    "HolderDao",
    "MacroDao",
    "MarketDao",
    "QuoteDao",
    "ScreenerDao",
    "StockDao",
    "SyncDao",
]
