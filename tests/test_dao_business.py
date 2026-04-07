"""
Tests for DAO business logic.

验证具体 DAO 类的业务数据读写逻辑，包括复杂查询、UPSERT、批量操作等。
"""

import datetime
import os
import sys

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.macro_dao import MacroDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.sync_dao import SyncDao

TEST_DB_URL = "postgresql+asyncpg://postgres:123456@localhost:5432/test_astock"


@pytest_asyncio.fixture(scope="function")
async def dao_engine():
    """创建测试用数据库引擎"""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def clean_db(dao_engine):
    """每个测试前清理数据库"""
    async with dao_engine.begin() as conn:
        await conn.execute(text("DELETE FROM daily_indicators"))
        await conn.execute(text("DELETE FROM daily_quotes"))
        await conn.execute(text("DELETE FROM stock_basic"))
        await conn.execute(text("DELETE FROM financial_reports"))
        await conn.execute(text("DELETE FROM moneyflow_hsgt"))
        await conn.execute(text("DELETE FROM index_weight"))
        await conn.execute(text("DELETE FROM market_news"))
        await conn.execute(text("DELETE FROM screening_history"))
        await conn.execute(text("DELETE FROM stk_holdernumber"))
        await conn.execute(text("DELETE FROM top10_holders"))
        await conn.execute(text("DELETE FROM macro_economy"))
        await conn.execute(text("DELETE FROM shibor_daily"))
        await conn.execute(text("DELETE FROM sync_status"))
        await conn.execute(text("DELETE FROM stock_sync_status"))
    yield


@pytest_asyncio.fixture
async def market_dao(dao_engine):
    return MarketDao(dao_engine)


@pytest_asyncio.fixture
async def screener_dao(dao_engine):
    return ScreenerDao(dao_engine)


@pytest_asyncio.fixture
async def holder_dao(dao_engine):
    return HolderDao(dao_engine)


@pytest_asyncio.fixture
async def macro_dao(dao_engine):
    return MacroDao(dao_engine)


@pytest_asyncio.fixture
async def sync_dao(dao_engine):
    return SyncDao(dao_engine)


@pytest_asyncio.fixture
async def setup_stock_data(dao_engine):
    """准备股票基础数据和日线数据用于复杂查询测试"""
    async with dao_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO stock_basic (ts_code, symbol, name, industry, list_status) "
                "VALUES ('000001.SZ', '000001', '平安银行', '银行', 'L')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO stock_basic (ts_code, symbol, name, industry, list_status) "
                "VALUES ('000002.SZ', '000002', '万科A', '房地产', 'L')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                "VALUES ('000001.SZ', '2024-03-21', 10.0, 2.0, 1000000, 10000000)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO daily_indicators (ts_code, trade_date, pe_ttm, pb, total_mv, turnover_rate) "
                "VALUES ('000001.SZ', '2024-03-21', 5.0, 0.5, 1000000000, 1.5)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO financial_reports (ts_code, end_date, ann_date, roe, grossprofit_margin, debt_to_assets) "
                "VALUES ('000001.SZ', '2023-12-31', '2024-03-20', 12.0, 30.0, 80.0)"
            )
        )


@pytest.mark.asyncio
class TestMarketDao:
    """测试 MarketDao 业务方法"""

    async def test_save_and_get_daily_indicators(self, market_dao, clean_db):
        """保存并查询每日指标"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "pe": 5.0,
                    "pe_ttm": 5.5,
                    "pb": 0.5,
                    "ps": 1.0,
                    "ps_ttm": 1.1,
                    "dv_ratio": 2.0,
                    "dv_ttm": 2.1,
                    "total_mv": 1000000000.0,
                    "circ_mv": 500000000.0,
                    "total_share": 100000000.0,
                    "float_share": 50000000.0,
                    "free_share": 40000000.0,
                    "turnover_rate": 1.5,
                    "turnover_rate_f": 1.6,
                    "volume_ratio": 1.2,
                }
            ]
        )

        saved = await market_dao.save_daily_indicators(df)
        assert saved == 1

        result = await market_dao.get_daily_indicators(ts_code="000001.SZ")
        assert not result.empty
        assert result["pe_ttm"].iloc[0] == 5.5

    async def test_daily_indicators_upsert(self, market_dao, clean_db):
        """相同主键记录更新"""
        df1 = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "pe": 5.0,
                    "pe_ttm": 5.5,
                    "pb": 0.5,
                    "total_mv": 1000000000.0,
                    "turnover_rate": 1.5,
                }
            ]
        )
        await market_dao.save_daily_indicators(df1)

        df2 = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "pe": 6.0,
                    "pe_ttm": 6.5,
                    "pb": 0.6,
                    "total_mv": 1100000000.0,
                    "turnover_rate": 1.6,
                }
            ]
        )
        await market_dao.save_daily_indicators(df2)

        result = await market_dao.get_daily_indicators(ts_code="000001.SZ")
        assert len(result) == 1
        assert result["pe_ttm"].iloc[0] == 6.5

    async def test_get_daily_indicators_bulk(self, market_dao, clean_db):
        """批量获取多只股票指标"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "pe_ttm": 5.0,
                    "pb": 0.5,
                    "turnover_rate": 1.5,
                },
                {
                    "ts_code": "000002.SZ",
                    "trade_date": "20240321",
                    "pe_ttm": 10.0,
                    "pb": 1.0,
                    "turnover_rate": 2.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "trade_date": "20240321",
                    "pe_ttm": 15.0,
                    "pb": 1.5,
                    "turnover_rate": 2.5,
                },
            ]
        )
        await market_dao.save_daily_indicators(df)

        result = await market_dao.get_daily_indicators_bulk(ts_code_list=["000001.SZ", "000002.SZ", "000003.SZ"])
        assert len(result) == 3

    async def test_get_daily_indicators_bulk_empty_list(self, market_dao, clean_db):
        """空代码列表返回空 DataFrame"""
        result = await market_dao.get_daily_indicators_bulk(ts_code_list=[])
        assert result.empty

    async def test_save_and_get_moneyflow_hsgt(self, market_dao, clean_db):
        """保存并查询北向资金"""
        df = pd.DataFrame(
            [
                {
                    "trade_date": "20240321",
                    "ggt_ss": "100000",
                    "ggt_sz": "200000",
                    "hgt": "50000",
                    "sgt": "60000",
                    "north_money": "110000",
                    "south_money": "300000",
                }
            ]
        )

        saved = await market_dao.save_moneyflow_hsgt(df)
        assert saved == 1

        result = await market_dao.get_moneyflow_hsgt(trade_date="20240321")
        assert not result.empty
        assert result["north_money"].iloc[0] == 110000.0

    async def test_save_and_get_index_weights(self, market_dao, clean_db):
        """保存并查询指数权重"""
        df = pd.DataFrame(
            [
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": "20240321",
                    "weight": 1.5,
                },
                {
                    "index_code": "000300.SH",
                    "con_code": "000002.SZ",
                    "trade_date": "20240321",
                    "weight": 2.0,
                },
            ]
        )

        saved = await market_dao.save_index_weights(df)
        assert saved == 2

        result = await market_dao.get_index_weights(index_code="000300.SH", trade_date="20240321")
        assert len(result) == 2

    async def test_get_latest_index_weight_date(self, market_dao, clean_db):
        """获取最新指数权重日期"""
        df = pd.DataFrame(
            [
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": "20240320",
                    "weight": 1.5,
                },
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": "20240321",
                    "weight": 1.6,
                },
            ]
        )
        await market_dao.save_index_weights(df)

        result = await market_dao.get_latest_index_weight_date()
        assert result == datetime.date(2024, 3, 21)


@pytest.mark.asyncio
class TestScreenerDao:
    """测试 ScreenerDao 业务方法"""

    async def test_save_and_get_screening_history(self, screener_dao, clean_db):
        """保存并查询筛选历史"""
        records = [
            (
                "2024-03-21",
                "oversold",
                "000001.SZ",
                "平安银行",
                10.0,
                2.0,
                "银行",
                1000000,
                10000000,
                1.5,
                5.0,
                0.5,
                1.0,
                2.0,
                1000000000.0,
                500000000.0,
                12.0,
                30.0,
                80.0,
                5.0,
                8.0,
                85,
                "AI推荐理由",
                "思考过程",
            )
        ]
        await screener_dao.save_screening_results(records)

        result = await screener_dao.get_screening_history(strategy_name="oversold")
        assert not result.empty
        assert result["ts_code"].iloc[0] == "000001.SZ"
        assert result["ai_score"].iloc[0] == 85

    async def test_get_history_tree(self, screener_dao, clean_db):
        """获取历史树形数据"""
        records = [
            (
                "2024-03-21",
                "oversold",
                "000001.SZ",
                "平安银行",
                10.0,
                2.0,
                "银行",
                1000000,
                10000000,
                1.5,
                5.0,
                0.5,
                1.0,
                2.0,
                1000000000.0,
                500000000.0,
                12.0,
                30.0,
                80.0,
                5.0,
                8.0,
                85,
                "理由",
                "思考",
            ),
            (
                "2024-03-21",
                "oversold",
                "000002.SZ",
                "万科A",
                20.0,
                1.0,
                "房地产",
                2000000,
                40000000,
                2.0,
                10.0,
                1.0,
                2.0,
                3.0,
                2000000000.0,
                1000000000.0,
                15.0,
                25.0,
                70.0,
                3.0,
                5.0,
                80,
                "理由",
                "思考",
            ),
        ]
        await screener_dao.save_screening_results(records)

        result = await screener_dao.get_history_tree()
        assert not result.empty
        assert len(result) == 1
        assert result["cnt"].iloc[0] == 2

    async def test_get_pending_reviews(self, screener_dao, clean_db):
        """获取待复盘记录"""
        records = [
            (
                "2024-03-21",
                "oversold",
                "000001.SZ",
                "平安银行",
                10.0,
                2.0,
                "银行",
                1000000,
                10000000,
                1.5,
                5.0,
                0.5,
                1.0,
                2.0,
                1000000000.0,
                500000000.0,
                12.0,
                30.0,
                80.0,
                5.0,
                8.0,
                85,
                "理由",
                "思考",
            ),
        ]
        await screener_dao.save_screening_results(records)

        result = await screener_dao.get_pending_reviews()
        assert len(result) == 1

    async def test_update_screening_performance(self, screener_dao, clean_db):
        """更新筛选表现"""
        records = [
            (
                "2024-03-21",
                "oversold",
                "000001.SZ",
                "平安银行",
                10.0,
                2.0,
                "银行",
                1000000,
                10000000,
                1.5,
                5.0,
                0.5,
                1.0,
                2.0,
                1000000000.0,
                500000000.0,
                12.0,
                30.0,
                80.0,
                5.0,
                8.0,
                85,
                "理由",
                "思考",
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        record_id = history["id"].iloc[0]

        updates = [(10.5, 5.0, 11.0, 10.0, record_id)]
        await screener_dao.update_screening_performance(updates)

        result = await screener_dao.get_screening_history()
        assert result["t1_price"].iloc[0] == 10.5
        assert result["t1_pct"].iloc[0] == 5.0

    async def test_get_learning_examples(self, screener_dao, clean_db):
        """获取学习样本"""
        records = [
            (
                "2024-03-20",
                "oversold",
                "000001.SZ",
                "平安银行",
                10.0,
                2.0,
                "银行",
                1000000,
                10000000,
                1.5,
                5.0,
                0.5,
                1.0,
                2.0,
                1000000000.0,
                500000000.0,
                12.0,
                30.0,
                80.0,
                5.0,
                8.0,
                85,
                "理由",
                "思考",
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        record_id = history["id"].iloc[0]

        await screener_dao.update_prediction_result(record_id, 5.0, "WIN")

        wins, losses = await screener_dao.get_learning_examples(limit=3)
        assert len(wins) == 1
        assert wins["prediction_result"].iloc[0] == "WIN"

    async def test_get_screening_data_complex_join(self, screener_dao, clean_db, setup_stock_data):
        """测试复杂 JOIN 查询获取筛选数据"""
        result = await screener_dao.get_screening_data(trade_date="2024-03-21")

        assert not result.empty
        assert result["ts_code"].iloc[0] == "000001.SZ"
        assert result["close"].iloc[0] == 10.0
        assert result["pe_ttm"].iloc[0] == 5.0
        assert result["roe"].iloc[0] == 12.0


@pytest.mark.asyncio
class TestHolderDao:
    """测试 HolderDao 业务方法"""

    async def test_save_holder_number(self, holder_dao, clean_db):
        """保存股东人数"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240330",
                    "holder_num": 100000,
                    "holder_num_change": 5000,
                    "holder_num_ratio": 5.0,
                }
            ]
        )

        saved = await holder_dao.save_holder_number(df)
        assert saved == 1

    async def test_save_top10_holders(self, holder_dao, clean_db):
        """保存前十大股东"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240330",
                    "holder_name": "中国平安保险",
                    "hold_amount": 1000000000,
                    "hold_ratio": 10.0,
                    "holder_type": "公司",
                }
            ]
        )

        saved = await holder_dao.save_top10_holders(df)
        assert saved == 1


@pytest.mark.asyncio
class TestMacroDao:
    """测试 MacroDao 业务方法"""

    async def test_save_and_get_macro_economy(self, macro_dao, clean_db):
        """保存并查询宏观经济数据"""
        df = pd.DataFrame(
            [
                {
                    "period": "2024-03-01",
                    "m2": 3000000.0,
                    "m2_yoy": 8.0,
                    "m1": 1000000.0,
                    "m1_yoy": 5.0,
                    "m0": 500000.0,
                    "m0_yoy": 3.0,
                    "cpi": 0.5,
                    "ppi": -1.0,
                }
            ]
        )

        saved = await macro_dao.save_macro_economy(df)
        assert saved == 1

        result = await macro_dao.get_macro_latest_date()
        assert result == datetime.date(2024, 3, 1)

    async def test_save_shibor_daily(self, macro_dao, clean_db):
        """保存 Shibor 利率"""
        df = pd.DataFrame(
            [
                {
                    "date": "2024-03-21",
                    "on": 1.5,
                    "1w": 1.8,
                    "2w": 2.0,
                    "1m": 2.2,
                    "3m": 2.5,
                    "6m": 2.7,
                    "9m": 2.8,
                    "1y": 2.9,
                }
            ]
        )

        saved = await macro_dao.save_shibor_daily(df)
        assert saved == 1

        result = await macro_dao.get_shibor_latest_date()
        assert result == datetime.date(2024, 3, 21)


@pytest.mark.asyncio
class TestSyncDao:
    """测试 SyncDao 业务方法"""

    async def test_update_and_get_sync_status(self, sync_dao, clean_db):
        """更新并获取同步状态"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=datetime.date(2024, 3, 21),
            record_count=5000,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result is not None
        assert result["table_name"] == "daily_quotes"
        assert result["record_count"] == 5000

    async def test_sync_status_upsert(self, sync_dao, clean_db):
        """同步状态更新"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=datetime.date(2024, 3, 20),
            record_count=4000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=datetime.date(2024, 3, 21),
            record_count=5000,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["record_count"] == 5000

    async def test_get_all_sync_status(self, sync_dao, clean_db):
        """获取所有同步状态"""
        await sync_dao.update_sync_status("daily_quotes", datetime.date(2024, 3, 21), 5000)
        await sync_dao.update_sync_status("stock_basic", datetime.date(2024, 3, 21), 5000)

        result = await sync_dao.get_sync_status()
        assert not result.empty
        assert len(result) == 2

    async def test_stock_sync_status(self, sync_dao, clean_db):
        """测试股票同步状态"""
        await sync_dao.mark_stock_step4_completed("000001.SZ", sync_version=1)
        await sync_dao.mark_stock_step4_completed("000002.SZ", sync_version=1)

        completed = await sync_dao.get_completed_step4_stocks(sync_version=1)
        assert "000001.SZ" in completed
        assert "000002.SZ" in completed

        await sync_dao.clear_step4_sync_status()
        completed = await sync_dao.get_completed_step4_stocks(sync_version=1)
        assert len(completed) == 0


class TestScreenerDaoDynamicCols:
    """Tests for ScreenerDao dynamic column reflection"""

    def test_sh_base_cols_excludes_thinking(self):
        """Verify SH_BASE_COLS dynamically reflects columns and excludes 'thinking'"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        cols_str = dao.SH_BASE_COLS

        col_list = [c.strip() for c in cols_str.split(",")]

        assert "thinking" not in col_list
        assert "id" in col_list
        assert "trade_date" in col_list
        assert "ts_code" in col_list
        assert "ai_score" in col_list
        assert "prediction_result" in col_list

    def test_sh_full_cols_includes_thinking(self):
        """Verify SH_FULL_COLS includes 'thinking'"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        full_cols = dao.SH_FULL_COLS

        assert "thinking" in full_cols
        assert full_cols.endswith(", thinking")

    def test_sh_base_cols_matches_model(self):
        """Verify SH_BASE_COLS count matches ScreeningHistory columns minus 'thinking'"""
        from data.persistence.daos.screener_dao import ScreenerDao
        from data.persistence.models import ScreeningHistory

        dao = ScreenerDao.__new__(ScreenerDao)
        col_list = [c.strip() for c in dao.SH_BASE_COLS.split(",")]

        expected_count = len(ScreeningHistory.__table__.columns) - 1
        assert len(col_list) == expected_count

    def test_sh_base_cols_cached(self):
        """Verify cached_property only computes once"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        result1 = dao.SH_BASE_COLS
        result2 = dao.SH_BASE_COLS

        assert result1 is result2
