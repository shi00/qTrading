import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.constants import (
    DATAFRAME_ATTR_COLUMN_UNITS,
    DATAFRAME_ATTR_COLUMN_UNIT_SOURCES,
    TOP_LIST_NET_AMOUNT_UNIT,
    TOP_LIST_NET_AMOUNT_UNIT_SOURCE,
)
from tests.test_infra_base import TestDatabaseBase


class TestCacheManager(TestDatabaseBase):
    async def test_stock_basic(self):
        """Test saving and retrieving stock basic info"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "symbol": ["000001"],
                "name": ["PingAn"],
                "area": ["Shenzhen"],
                "industry": ["Bank"],
                "market": ["Main"],
                "list_date": ["19910403"],
            },
        )

        saved_count = await self.cache.save_stock_basic(df)
        self.assertEqual(saved_count, 1)

        result_df = await self.cache.get_stock_basic()
        self.assertEqual(len(result_df), 1)
        self.assertEqual(result_df.iloc[0]["name"], "PingAn")

    async def test_daily_quotes(self):
        """Test daily quotes operations"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20230101"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "pre_close": [10.0],
                "change": [0.5],
                "pct_chg": [5.0],
                "vol": [1000],
                "amount": [10000],
                "adj_factor": [1.0],
            },
        )

        await self.cache.save_daily_quotes(df)

        res = await self.cache.get_daily_quotes(ts_code="000001.SZ")
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]["close"], 10.5)

        date = await self.cache.get_latest_trade_date()
        self.assertEqual(date, datetime.date(2023, 1, 1))

        dates = await self.cache.get_cached_trade_dates()
        self.assertIn(datetime.date(2023, 1, 1), dates)

    async def test_daily_indicators(self):
        """Test daily indicators operations"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20230101"],
                "pe": [10.0],
                "pe_ttm": [9.5],
                "pb": [1.2],
                "total_mv": [100000],
                "circ_mv": [50000],
                "ps": [1.0],
                "ps_ttm": [1.0],
                "dv_ratio": [2.0],
                "dv_ttm": [2.0],
                "total_share": [1000],
                "float_share": [1000],
                "free_share": [1000],
                "turnover_rate": [1.0],
                "turnover_rate_f": [1.0],
            },
        )

        await self.cache.save_daily_indicators(df)

        dates = await self.cache.get_cached_indicator_dates()
        self.assertIn(datetime.date(2023, 1, 1), dates)

        res = await self.cache.get_latest_indicators(datetime.date(2023, 1, 1))
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]["pe"], 10.0)

    async def test_latest_indicators_default_is_quote_aligned(self):
        """默认最新指标日期应与行情表最新日期对齐，避免前视偏差。"""
        daily_quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20230101"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "pre_close": [10.0],
                "change": [0.5],
                "pct_chg": [5.0],
                "vol": [1000],
                "amount": [10000],
                "adj_factor": [1.0],
            },
        )
        daily_indicators = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": ["20230101", "20230102"],
                "pe": [10.0, 20.0],
                "pe_ttm": [9.5, 19.5],
                "pb": [1.2, 2.2],
                "total_mv": [100000, 110000],
                "circ_mv": [50000, 55000],
                "total_share": [1000, 1000],
                "float_share": [1000, 1000],
                "free_share": [1000, 1000],
                "turnover_rate": [1.0, 1.5],
                "turnover_rate_f": [1.0, 1.5],
            },
        )

        await self.cache.save_daily_quotes(daily_quotes)
        await self.cache.save_daily_indicators(daily_indicators)

        res = await self.cache.get_latest_indicators()

        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]["trade_date"], datetime.date(2023, 1, 1))
        self.assertEqual(res.iloc[0]["pe"], 10.0)

    async def test_financial_reports(self):
        """Test financial reports operations"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20230331"],
                "ann_date": ["20230401"],
                "report_type": ["1"],
                "roe": [15.5],
                "total_revenue": [50000],
                "revenue": [50000],
                "n_income": [1000],
                "n_income_attr_p": [1000],
                "total_assets": [100000],
                "total_liab": [50000],
                "total_hldr_eqy_exc_min_int": [50000],
                "roe_dt": [15.0],
                "grossprofit_margin": [20.0],
                "netprofit_margin": [10.0],
                "debt_to_assets": [50.0],
                "or_yoy": [5.0],
                "netprofit_yoy": [5.0],
                "goodwill": [0.0],
            },
        )

        await self.cache.save_financial_reports(df)

        res = await self.cache.get_cached_financial_records()
        self.assertEqual(len(res), 1)
        self.assertIn(("000001.SZ", datetime.date(2023, 3, 31)), res)

    async def test_moneyflow_northbound(self):
        """Test moneyflow and northbound data"""
        mf_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20230101"],
                "buy_md_amount": [100],
                "buy_sm_vol": [100],
                "buy_sm_amount": [100],
                "sell_sm_amount": [100],
                "sell_md_amount": [100],
                "buy_lg_amount": [100],
                "sell_lg_amount": [100],
                "buy_elg_amount": [100],
                "sell_elg_amount": [100],
                "net_mf_vol": [100],
                "net_mf_amount": [100],
            },
        )
        nb_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20230101"],
                "name": ["PA"],
                "vol": [100],
                "ratio": [5.5],
                "exchange": ["SZ"],
            },
        )

        await self.cache.save_moneyflow(mf_df)
        await self.cache.save_northbound(nb_df)

        res_mf = await self.cache.get_moneyflow(datetime.date(2023, 1, 1))
        self.assertEqual(res_mf.iloc[0]["buy_md_amount"], 100)

        res_latest_nb = await self.cache.get_latest_northbound()
        self.assertEqual(len(res_latest_nb), 1)
        self.assertEqual(res_latest_nb.iloc[0]["ratio"], 5.5)

    async def test_sync_status(self):
        """Test sync status operations"""
        await self.cache.update_sync_status("test_table", datetime.date(2023, 1, 1), 100)

        status = await self.cache.get_sync_status("test_table")
        self.assertEqual(status["record_count"], 100)  # type: ignore
        self.assertEqual(status["status"], "success")  # type: ignore

        all_status = await self.cache.get_sync_status()
        self.assertFalse(all_status.empty)  # type: ignore

    async def test_get_screening_data(self):
        """Test complex join for screening data"""
        stock_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "symbol": ["000001"],
                "name": ["PA"],
                "area": ["Sz"],
                "industry": ["Bank"],
                "market": ["Main"],
                "list_date": ["20000101"],
                "list_status": ["L"],
            },
        )
        daily_quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [datetime.date(2023, 1, 1)],
                "close": [10.0],
                "pct_chg": [1.0],
                "open": [10],
                "high": [11],
                "low": [9],
                "pre_close": [9.9],
                "vol": [100],
                "amount": [1000],
                "change": [0.1],
                "adj_factor": [1],
            },
        )
        daily_ind = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [datetime.date(2023, 1, 1)],
                "pe_ttm": [8.0],
                "pe": [8],
                "pb": [1],
                "ps": [1],
                "ps_ttm": [1],
                "dv_ratio": [1],
                "dv_ttm": [1],
                "total_mv": [100],
                "circ_mv": [100],
                "total_share": [100],
                "float_share": [100],
                "free_share": [100],
                "turnover_rate": [1],
                "turnover_rate_f": [1],
            },
        )
        fina = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": [datetime.date(2022, 12, 31)],
                "ann_date": [datetime.date(2023, 1, 1)],
                "report_type": ["1"],
                "roe": [12.0],
                "total_revenue": [100],
                "revenue": [100],
                "n_income": [10],
                "n_income_attr_p": [10],
                "total_assets": [100],
                "total_liab": [50],
                "total_hldr_eqy_exc_min_int": [50],
                "roe_dt": [12],
                "grossprofit_margin": [10],
                "netprofit_margin": [10],
                "debt_to_assets": [0.5],
                "or_yoy": [1],
                "netprofit_yoy": [1],
                "goodwill": [0],
            },
        )

        await self.cache.save_stock_basic(stock_basic)
        await self.cache.save_daily_quotes(daily_quotes)
        await self.cache.save_daily_indicators(daily_ind)
        await self.cache.save_financial_reports(fina)

        df = await self.cache.get_screening_data(trade_date=datetime.date(2023, 1, 1))

        self.assertFalse(df.empty)
        row = df.iloc[0]
        self.assertEqual(row["ts_code"], "000001.SZ")
        self.assertEqual(row["close"], 10.0)
        self.assertEqual(row["pe_ttm"], 8.0)
        self.assertEqual(row["roe"], 12.0)

    async def test_screening_history(self):
        """Test screening history saving and updating"""
        from sqlalchemy import text

        sql = text("""INSERT INTO screening_history
                 (run_id, trade_date, strategy_name, ts_code, name, close, pct_chg, ai_score, ai_reason)
                 VALUES (:run_id, :trade_date, :strategy_name, :ts_code, :name, :close, :pct_chg, :ai_score, :ai_reason)""")

        async with self.cache.engine.begin() as conn:
            await conn.execute(
                sql,
                [
                    {
                        "run_id": "RUN001",
                        "trade_date": datetime.date(2023, 1, 1),
                        "strategy_name": "value",
                        "ts_code": "000001.SZ",
                        "name": "PA",
                        "close": 10.0,
                        "pct_chg": 1.0,
                        "ai_score": 0,
                        "ai_reason": "",
                    }
                ],
            )

        history = await self.cache.get_screening_history("value")
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["ts_code"], "000001.SZ")

        pending = await self.cache.get_pending_reviews()
        self.assertEqual(len(pending), 1)
        record_id = pending[0]["id"]

        await self.cache.screener_dao.update_prediction_result(
            record_id,
            10.0,
            "WIN",
            t1_price=11.0,
            t5_price=12.0,
            t5_pct=20.0,
            index_pct=1.0,
            alpha=9.0,
        )

        history_updated = await self.cache.get_screening_history("value")
        self.assertEqual(history_updated.iloc[0]["t1_price"], 11.0)

    async def test_clear_cache(self):
        """Test clearing cache"""
        await self.cache.update_sync_status("test", datetime.date(2023, 1, 1), 1)
        await self.cache.clear_all_cache()

        status = await self.cache.get_sync_status("test")
        self.assertIsNone(status)

    async def test_clear_cache_drops_alembic_version(self):
        """Verify clear_all_cache resets alembic_version so init_db can rebuild from scratch."""
        import sqlalchemy as sa

        async with self.cache.engine.begin() as conn:
            await conn.execute(
                sa.text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            )
            await conn.execute(sa.text("DELETE FROM alembic_version"))
            await conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('test_rev')"))

        async with self.cache.engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT count(*) FROM alembic_version"))
            self.assertEqual(result.scalar(), 1)

        await self.cache.clear_all_cache()

        async with self.cache.engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            version = result.scalar()
        self.assertIsNotNone(version)
        self.assertNotEqual(version, "test_rev", "alembic_version should be reset to actual migration version")

    async def test_top_list(self):
        """Test Top List (LHB)"""
        df = pd.DataFrame(
            {
                "trade_date": ["20230101"],
                "ts_code": ["000001.SZ"],
                "net_amount": [1000],
                "name": ["PA"],
                "close": [10],
                "pct_chg": [10],
                "turnover_rate": [1],
                "amount": [1000],
                "l_sell": [0],
                "l_buy": [0],
                "l_amount": [0],
                "net_rate": [0],
                "amount_rate": [0],
                "float_values": [0],
                "reason": ["Test"],
            },
        )
        await self.cache.save_top_list(df)

        res = await self.cache.get_top_list(datetime.date(2023, 1, 1))
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]["net_amount"], 1000)
        self.assertEqual(
            res.attrs[DATAFRAME_ATTR_COLUMN_UNITS]["net_amount"],
            TOP_LIST_NET_AMOUNT_UNIT,
        )
        self.assertEqual(
            res.attrs[DATAFRAME_ATTR_COLUMN_UNIT_SOURCES]["net_amount"],
            TOP_LIST_NET_AMOUNT_UNIT_SOURCE,
        )

    async def test_block_trade(self):
        """Test Block Trade"""
        df = pd.DataFrame(
            {
                "trade_date": ["20230101"],
                "ts_code": ["000001.SZ"],
                "amount": [500],
                "price": [10.0],
                "volume": [50.0],
                "buyer": ["B1"],
                "seller": ["S1"],
            },
        )
        await self.cache.save_block_trade(df)

        res = await self.cache.get_block_trade(datetime.date(2023, 1, 1))
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]["amount"], 500)


if __name__ == "__main__":
    unittest.main()
