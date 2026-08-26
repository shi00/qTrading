"""DAO 注册清单与 engine 引用同步（review01-A4 Step2 拆分）。

从 ``CacheManager`` 拆出 DAO 注册职责：承载 ``_DAO_REGISTRY`` 权威清单与
``sync_engines`` 遍历同步。DAO 实例化仍保留在 ``CacheManager.__init__``
（R13 红线静态检查要求 `self.<x>_dao = <ClassName>(...)` 出现在 __init__）。
"""

from __future__ import annotations

from data.persistence.daos.backtest_dao import BacktestDAO
from data.persistence.daos.base_dao import BaseDao
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
from data.persistence.daos.stock_dao import StockDao
from data.persistence.daos.stk_limit_dao import StkLimitDao
from data.persistence.daos.sw_industry_dao import SwIndustryClassifyDao, SwIndustryMemberDao
from data.persistence.daos.top_inst_dao import TopInstDao
from data.persistence.daos.watchlist_dao import WatchlistDao
from data.persistence.daos.sync_dao import SyncDao


class DaoRegistry:
    """DAO 注册清单（权威单一来源）与 engine 引用同步。

    ``sync_engines(holder, engine)`` 遍历注册表，将 ``holder``（宿主对象，即
    CacheManager 组合根）上各 DAO 实例的 ``.engine`` 置为给定引擎（或 None）。
    消除 _create_engine/close 中逐 DAO 手写赋值的重复。
    """

    _DAO_REGISTRY: tuple[tuple[str, type[BaseDao]], ...] = (
        ("stock_dao", StockDao),
        ("quote_dao", QuoteDao),
        ("financial_dao", FinancialDao),
        ("sync_dao", SyncDao),
        ("market_dao", MarketDao),
        ("screener_dao", ScreenerDao),
        ("macro_dao", MacroDao),
        ("holder_dao", HolderDao),
        ("backtest_dao", BacktestDAO),
        ("top_inst_dao", TopInstDao),
        ("stk_limit_dao", StkLimitDao),
        ("pledge_detail_dao", PledgeDetailDao),
        ("share_float_dao", ShareFloatDao),
        ("stk_holdertrade_dao", StkHoldertradeDao),
        ("sw_industry_classify_dao", SwIndustryClassifyDao),
        ("sw_industry_member_dao", SwIndustryMemberDao),
        ("express_dao", ExpressDao),
        ("watchlist_dao", WatchlistDao),
    )

    def sync_engines(self, holder: object, engine) -> None:
        """遍历注册表同步 holder 上 DAO 实例的 ``.engine``（create/dispose 共用）。"""
        for attr_name, _ in self._DAO_REGISTRY:
            getattr(holder, attr_name).engine = engine
