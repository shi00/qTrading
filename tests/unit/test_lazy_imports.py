import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestDataInit:
    def test_getattr_cache_manager(self):
        import data

        result = data.CacheManager
        from data.cache.cache_manager import CacheManager

        assert result is CacheManager

    def test_getattr_data_processor(self):
        import data

        result = data.DataProcessor
        from data.data_processor import DataProcessor

        assert result is DataProcessor

    def test_getattr_unknown_raises(self):
        import data

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.NonExistent


class TestCacheInit:
    def test_getattr_cache_manager(self):
        import data.cache

        result = data.cache.CacheManager
        from data.cache.cache_manager import CacheManager

        assert result is CacheManager

    def test_getattr_unknown_raises(self):
        import data.cache

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.cache.NonExistent


class TestDomainServicesInit:
    def test_getattr_market_data_service(self):
        import data.domain_services

        result = data.domain_services.MarketDataService
        from data.domain_services.market_data_service import MarketDataService

        assert result is MarketDataService

    def test_getattr_offline_calendar(self):
        import data.domain_services

        result = data.domain_services.OfflineCalendar
        from data.domain_services.offline_calendar import OfflineCalendar

        assert result is OfflineCalendar

    def test_getattr_trade_calendar_service(self):
        import data.domain_services

        result = data.domain_services.TradeCalendarService
        from data.domain_services.trade_calendar_service import TradeCalendarService

        assert result is TradeCalendarService

    def test_getattr_unknown_raises(self):
        import data.domain_services

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.domain_services.NonExistent


class TestExternalInit:
    def test_getattr_news_fetcher(self):
        import data.external

        result = data.external.NewsFetcher
        from data.external.news_fetcher import NewsFetcher

        assert result is NewsFetcher

    def test_getattr_tushare_client(self):
        import data.external

        result = data.external.TushareClient
        from data.external.tushare_client import TushareClient

        assert result is TushareClient

    def test_getattr_unknown_raises(self):
        import data.external

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.external.NonExistent


class TestExternalLazyDependencies:
    def test_news_fetcher_import_does_not_load_akshare(self):
        """review08-B5: import data.external.news_fetcher 不加载 akshare（顶层 import 已改 _get_akshare() 惰性）。

        子进程执行，避免当前测试进程（conftest autouse mock / 其他用例）已导入 akshare
        造成干扰；并断言调用 ``_get_akshare()`` 能正确触发惰性导入。
        """
        subprocess_code = (
            "import sys\n"
            "import data.external.news_fetcher as nf\n"
            "assert 'akshare' not in sys.modules, 'news_fetcher import 不应加载 akshare'\n"
            "ak = nf._get_akshare()\n"
            "assert 'akshare' in sys.modules, '调用 _get_akshare 应触发惰性导入'\n"
            "assert ak is sys.modules['akshare']\n"
            "print('OK: akshare not loaded at import time')\n"
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # test_lazy_imports.py 位于 tests/unit/，上溯 2 级即仓库根，用作子进程 cwd，
        # 保证子进程从仓库根解析 data.external.news_fetcher。
        _repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-c", subprocess_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(_repo_root),
        )
        assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
        assert "OK: akshare not loaded at import time" in result.stdout

    def test_get_akshare_returns_real_module(self):
        """review08-B5: ``_get_akshare()`` 直接契约 —— 返回真实 akshare 模块（seam 单一入口）。"""
        import akshare

        import data.external.news_fetcher as nf

        assert nf._get_akshare() is akshare


class TestPersistenceInit:
    def test_getattr_data_explorer_query_client(self):
        import data.persistence

        result = data.persistence.DataExplorerQueryClient
        from data.persistence.data_explorer_query_client import DataExplorerQueryClient

        assert result is DataExplorerQueryClient

    def test_getattr_base(self):
        import data.persistence

        result = data.persistence.Base
        from data.persistence.models import Base

        assert result is Base

    def test_getattr_unknown_raises(self):
        import data.persistence

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.persistence.NonExistent


class TestDaosInit:
    def test_getattr_base_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.BaseDao
        from data.persistence.daos.base_dao import BaseDao

        assert result is BaseDao

    def test_getattr_financial_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.FinancialDao
        from data.persistence.daos.financial_dao import FinancialDao

        assert result is FinancialDao

    def test_getattr_holder_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.HolderDao
        from data.persistence.daos.holder_dao import HolderDao

        assert result is HolderDao

    def test_getattr_macro_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.MacroDao
        from data.persistence.daos.macro_dao import MacroDao

        assert result is MacroDao

    def test_getattr_market_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.MarketDao
        from data.persistence.daos.market_dao import MarketDao

        assert result is MarketDao

    def test_getattr_quote_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.QuoteDao
        from data.persistence.daos.quote_dao import QuoteDao

        assert result is QuoteDao

    def test_getattr_screener_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.ScreenerDao
        from data.persistence.daos.screener_dao import ScreenerDao

        assert result is ScreenerDao

    def test_getattr_stock_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.StockDao
        from data.persistence.daos.stock_dao import StockDao

        assert result is StockDao

    def test_getattr_sync_dao(self):
        import data.persistence.daos

        result = data.persistence.daos.SyncDao
        from data.persistence.daos.sync_dao import SyncDao

        assert result is SyncDao

    def test_getattr_unknown_raises(self):
        import data.persistence.daos

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = data.persistence.daos.NonExistent
