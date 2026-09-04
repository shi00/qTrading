"""
Integration tests for range query methods in DAOs.
Verifies that database schema matches, raw SQL compilation succeeds, and no database errors occur.
"""

from decimal import Decimal

import pandas as pd
import polars as pl
import pytest

from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.market_dao import MarketDao

pytestmark = pytest.mark.integration


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
    @pytest.mark.usefixtures("mvd_data")
    async def test_range_decimal_normalized_and_polars_consumable(self, screener_dao):
        """DAT-10/11: 区间预载读取边界 Decimal → float64 归一化，且可被 Polars 策略直接消费。"""
        # MVD 行情日期范围 2026-06-18 ~ 2026-06-25（见 fixtures/mvd_data.py）
        df = await screener_dao.get_screening_data_range("20260618", "20260625")

        assert df is not None
        assert not df.empty, "MVD 区间应返回行情数据"
        # 归一化：Numeric 列（close/amount/pct_chg）为 float64，而非 object/Decimal
        # vol 在 daily_quotes 为 BIGINT（整数股数），读回 int64，不在归一化范围
        for col in ("close", "amount", "pct_chg"):
            assert df[col].dtype == "float64", f"{col} 应为 float64，实际 {df[col].dtype}"
        # 无 object 列残留 Decimal 对象
        for col in df.columns:
            if df[col].dtype == object:
                assert not any(isinstance(v, Decimal) for v in df[col].dropna().head(50)), f"{col} 仍含 Decimal 对象"

        # Polars 消费：pl.from_pandas 可转换且 Numeric 列为 Float64
        pl_df = pl.from_pandas(df)
        assert pl_df.schema["close"] == pl.Float64
        assert pl_df.schema["amount"] == pl.Float64

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
