"""DAO layer - 数据访问对象"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.persistence.daos.backtest_dao import BacktestDAO
    from data.persistence.daos.base_dao import BaseDao, EngineDisposedError
    from data.persistence.daos.express_dao import ExpressDao
    from data.persistence.daos.financial_dao import FinancialDao
    from data.persistence.daos.holder_dao import HolderDao
    from data.persistence.daos.macro_dao import MacroDao
    from data.persistence.daos.market_dao import MarketDao
    from data.persistence.daos.pledge_detail_dao import PledgeDetailDao
    from data.persistence.daos.quote_dao import QuoteDao
    from data.persistence.daos.screener_dao import ScreenerDao
    from data.persistence.daos.share_float_dao import ShareFloatDao
    from data.persistence.daos.stk_holdertrade_dao import StkHoldertradeDao
    from data.persistence.daos.stk_limit_dao import StkLimitDao
    from data.persistence.daos.stock_dao import StockDao
    from data.persistence.daos.sw_industry_dao import SwIndustryClassifyDao, SwIndustryMemberDao
    from data.persistence.daos.sync_dao import SyncDao
    from data.persistence.daos.top_inst_dao import TopInstDao


def __getattr__(name):
    _mapping = {
        "BacktestDAO": "data.persistence.daos.backtest_dao",
        "BaseDao": "data.persistence.daos.base_dao",
        "EngineDisposedError": "data.persistence.daos.base_dao",
        "ExpressDao": "data.persistence.daos.express_dao",
        "FinancialDao": "data.persistence.daos.financial_dao",
        "HolderDao": "data.persistence.daos.holder_dao",
        "MacroDao": "data.persistence.daos.macro_dao",
        "MarketDao": "data.persistence.daos.market_dao",
        "PledgeDetailDao": "data.persistence.daos.pledge_detail_dao",
        "QuoteDao": "data.persistence.daos.quote_dao",
        "ScreenerDao": "data.persistence.daos.screener_dao",
        "ShareFloatDao": "data.persistence.daos.share_float_dao",
        "StkHoldertradeDao": "data.persistence.daos.stk_holdertrade_dao",
        "StkLimitDao": "data.persistence.daos.stk_limit_dao",
        "StockDao": "data.persistence.daos.stock_dao",
        "SwIndustryClassifyDao": "data.persistence.daos.sw_industry_dao",
        "SwIndustryMemberDao": "data.persistence.daos.sw_industry_dao",
        "SyncDao": "data.persistence.daos.sync_dao",
        "TopInstDao": "data.persistence.daos.top_inst_dao",
    }
    if name in _mapping:
        import importlib

        mod = importlib.import_module(_mapping[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BacktestDAO",
    "BaseDao",
    "EngineDisposedError",
    "ExpressDao",
    "FinancialDao",
    "HolderDao",
    "MacroDao",
    "MarketDao",
    "PledgeDetailDao",
    "QuoteDao",
    "ScreenerDao",
    "ShareFloatDao",
    "StkHoldertradeDao",
    "StkLimitDao",
    "StockDao",
    "SwIndustryClassifyDao",
    "SwIndustryMemberDao",
    "SyncDao",
    "TopInstDao",
]
