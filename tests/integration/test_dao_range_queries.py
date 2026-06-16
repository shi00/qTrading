"""
Integration tests for range query methods in DAOs.
Verifies that database schema matches, raw SQL compilation succeeds, and no database errors occur.
"""

import pytest
import pandas as pd

from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.market_dao import MarketDao


class TestDaoRangeQueriesIntegration:
    @pytest.fixture
    def screener_dao(self, test_engine):
        return ScreenerDao(test_engine)

    @pytest.fixture
    def quote_dao(self, test_engine):
        return QuoteDao(test_engine)

    @pytest.fixture
    def market_dao(self, test_engine):
        return MarketDao(test_engine)

    @pytest.mark.asyncio
    async def test_screener_dao_range_queries(self, screener_dao):
        """Verify ScreenerDao range methods compile and run successfully."""
        # 1. Test get_screening_data_range
        df = await screener_dao.get_screening_data_range("20240101", "20240105")
        assert df is not None
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert "ts_code" in df.columns
            assert "trade_date" in df.columns
            assert "close" in df.columns

        # 2. Test get_fundamental_screening_data_range
        df_f = await screener_dao.get_fundamental_screening_data_range("20240101", "20240105")
        assert df_f is not None
        assert isinstance(df_f, pd.DataFrame)
        if not df_f.empty:
            assert "ts_code" in df_f.columns
            assert "trade_date" in df_f.columns

    @pytest.mark.asyncio
    async def test_market_dao_range_queries(self, market_dao):
        """Verify MarketDao range methods compile and run successfully."""
        df = await market_dao.get_moneyflow_hsgt_range("20240101", "20240105")
        assert df is not None
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert "trade_date" in df.columns

    @pytest.mark.asyncio
    async def test_quote_dao_range_queries(self, quote_dao):
        """Verify QuoteDao range methods compile and run successfully."""
        # 1. Test get_block_trade_range
        df_block = await quote_dao.get_block_trade_range("20240101", "20240105")
        assert df_block is not None
        assert isinstance(df_block, pd.DataFrame)
        if not df_block.empty:
            assert "trade_date" in df_block.columns

        # 2. Test get_top_list_range
        df_top = await quote_dao.get_top_list_range("20240101", "20240105")
        assert df_top is not None
        assert isinstance(df_top, pd.DataFrame)
        if not df_top.empty:
            assert "trade_date" in df_top.columns

        # 3. Test get_moneyflow_range
        df_flow = await quote_dao.get_moneyflow_range("20240101", "20240105")
        assert df_flow is not None
        assert isinstance(df_flow, pd.DataFrame)
        if not df_flow.empty:
            assert "trade_date" in df_flow.columns

        # 4. Test get_northbound_range
        df_north = await quote_dao.get_northbound_range("20240101", "20240105")
        assert df_north is not None
        assert isinstance(df_north, pd.DataFrame)
        if not df_north.empty:
            assert "trade_date" in df_north.columns
