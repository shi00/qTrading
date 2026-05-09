"""
Tests for DAO business logic.

验证具体 DAO 类的业务数据读写逻辑，包括复杂查询、UPSERT、批量操作等。
"""

import datetime
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_TODAY = date.today()
_RECENT_DATE = _TODAY - timedelta(days=1)
_RECENT_DATE_MINUS_2 = _TODAY - timedelta(days=3)
_RECENT_DATE_MINUS_90 = _TODAY - timedelta(days=91)
_RECENT_DATE_STR = _RECENT_DATE.strftime("%Y%m%d")
_RECENT_DATE_MINUS_2_STR = _RECENT_DATE_MINUS_2.strftime("%Y%m%d")
_RECENT_DATE_MINUS_90_STR = _RECENT_DATE_MINUS_90.strftime("%Y%m%d")

from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.macro_dao import MacroDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.sync_dao import SyncDao


@pytest_asyncio.fixture(scope="function", autouse=True)
async def clean_db(test_engine: AsyncEngine):
    """每个测试前清理数据库（容错处理表不存在）"""
    import contextlib

    tables = [
        "daily_indicators",
        "daily_quotes",
        "stock_basic",
        "financial_reports",
        "moneyflow_hsgt",
        "index_weight",
        "market_news",
        "screening_history",
        "stk_holdernumber",
        "top10_holders",
        "macro_economy",
        "shibor_daily",
        "sync_status",
        "stock_sync_status",
    ]
    async with test_engine.begin() as conn:
        for table in tables:
            with contextlib.suppress(Exception):
                await conn.execute(text(f"DELETE FROM {table}"))
    yield


@pytest_asyncio.fixture
async def market_dao(test_engine: AsyncEngine):
    return MarketDao(test_engine)


@pytest_asyncio.fixture
async def screener_dao(test_engine: AsyncEngine):
    return ScreenerDao(test_engine)


@pytest_asyncio.fixture
async def quote_dao(test_engine: AsyncEngine):
    return QuoteDao(test_engine)


@pytest_asyncio.fixture
async def holder_dao(test_engine: AsyncEngine):
    return HolderDao(test_engine)


@pytest_asyncio.fixture
async def macro_dao(test_engine: AsyncEngine):
    return MacroDao(test_engine)


@pytest_asyncio.fixture
async def sync_dao(test_engine: AsyncEngine):
    return SyncDao(test_engine)


@pytest_asyncio.fixture
async def setup_stock_data(test_engine: AsyncEngine):
    """准备股票基础数据和日线数据用于复杂查询测试"""
    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO stock_basic (ts_code, symbol, name, industry, list_status, list_date) "
                "VALUES ('000001.SZ', '000001', '平安银行', '银行', 'L', '2020-01-01')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO stock_basic (ts_code, symbol, name, industry, list_status, list_date) "
                "VALUES ('000002.SZ', '000002', '万科A', '房地产', 'L', '2020-01-01')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                "VALUES ('000001.SZ', :trade_date, 10.0, 2.0, 1000000, 10000000)"
            ),
            {"trade_date": _RECENT_DATE},
        )
        await conn.execute(
            text(
                "INSERT INTO daily_indicators (ts_code, trade_date, pe_ttm, pb, total_mv, turnover_rate) "
                "VALUES ('000001.SZ', :trade_date, 5.0, 0.5, 1000000000, 1.5)"
            ),
            {"trade_date": _RECENT_DATE},
        )
        await conn.execute(
            text(
                "INSERT INTO financial_reports (ts_code, end_date, ann_date, roe, grossprofit_margin, debt_to_assets) "
                "VALUES ('000001.SZ', :end_date, :ann_date, 12.0, 30.0, 80.0)"
            ),
            {"end_date": _RECENT_DATE_MINUS_90, "ann_date": _RECENT_DATE_MINUS_2},
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
                    "trade_date": _RECENT_DATE_STR,
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
                    "trade_date": _RECENT_DATE_STR,
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
                    "trade_date": _RECENT_DATE_STR,
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
                    "trade_date": _RECENT_DATE_STR,
                    "pe_ttm": 5.0,
                    "pb": 0.5,
                    "turnover_rate": 1.5,
                },
                {
                    "ts_code": "000002.SZ",
                    "trade_date": _RECENT_DATE_STR,
                    "pe_ttm": 10.0,
                    "pb": 1.0,
                    "turnover_rate": 2.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "trade_date": _RECENT_DATE_STR,
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
                    "trade_date": _RECENT_DATE_STR,
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

        result = await market_dao.get_moneyflow_hsgt(trade_date=_RECENT_DATE_STR)
        assert not result.empty
        assert result["north_money"].iloc[0] == 110000.0

    async def test_save_and_get_index_weights(self, market_dao, clean_db):
        """保存并查询指数权重"""
        df = pd.DataFrame(
            [
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": _RECENT_DATE_STR,
                    "weight": 1.5,
                },
                {
                    "index_code": "000300.SH",
                    "con_code": "000002.SZ",
                    "trade_date": _RECENT_DATE_STR,
                    "weight": 2.0,
                },
            ]
        )

        saved = await market_dao.save_index_weights(df)
        assert saved == 2

        result = await market_dao.get_index_weights(index_code="000300.SH", trade_date=_RECENT_DATE_STR)
        assert len(result) == 2

    async def test_get_latest_index_weight_date(self, market_dao, clean_db):
        """获取最新指数权重日期"""
        df = pd.DataFrame(
            [
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": _RECENT_DATE_MINUS_2_STR,
                    "weight": 1.5,
                },
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": _RECENT_DATE_STR,
                    "weight": 1.6,
                },
            ]
        )
        await market_dao.save_index_weights(df)

        result = await market_dao.get_latest_index_weight_date()
        assert result == _RECENT_DATE


@pytest.mark.asyncio
class TestScreenerDao:
    """测试 ScreenerDao 业务方法"""

    async def test_save_and_get_screening_history(self, screener_dao, clean_db):
        """保存并查询筛选历史"""
        records = [
            {
                "run_id": "RUN001",
                "trade_date": _RECENT_DATE,
                "strategy_name": "oversold",
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "close": 10.0,
                "pct_chg": 2.0,
                "industry": "银行",
                "vol": 1000000,
                "amount": 10000000,
                "turnover_rate": 1.5,
                "pe_ttm": 5.0,
                "pb": 0.5,
                "ps_ttm": 1.0,
                "dv_ttm": 2.0,
                "total_mv": 1000000000.0,
                "circ_mv": 500000000.0,
                "roe": 12.0,
                "grossprofit_margin": 30.0,
                "debt_to_assets": 80.0,
                "or_yoy": 5.0,
                "netprofit_yoy": 8.0,
                "ai_score": 85,
                "ai_reason": "AI推荐理由",
                "thinking": "思考过程",
                "params_snapshot": None,
            }
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
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
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
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        result = await screener_dao.get_pending_reviews()
        assert len(result) == 1

    async def test_get_learning_examples(self, screener_dao, clean_db):
        """获取学习样本"""
        records = [
            (
                "RUN001",
                _RECENT_DATE_MINUS_2,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        record_id = history["id"].iloc[0]

        await screener_dao.update_prediction_result(
            record_id,
            5.0,
            "WIN",
            t1_price=10.5,
            t5_pct=12.0,
            t5_price=11.2,
            index_pct=1.2,
            alpha=3.8,
        )

        wins, losses = await screener_dao.get_learning_examples(limit=3)
        assert len(wins) == 1
        assert wins["prediction_result"].iloc[0] == "WIN"
        assert wins["alpha"].iloc[0] == 3.8

    async def test_save_screening_results_sets_review_status_pending(self, screener_dao, clean_db):
        """保存筛选结果时，review_status 应自动设为 PENDING"""
        from data.constants import REVIEW_STATUS_PENDING

        records = [
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        assert history["review_status"].iloc[0] == REVIEW_STATUS_PENDING

    async def test_update_prediction_result_sets_review_status_t1_done(self, screener_dao, clean_db):
        """更新预测结果时，review_status 应设为 T1_DONE"""
        from data.constants import REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE

        records = [
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        record_id = history["id"].iloc[0]
        assert history["review_status"].iloc[0] == REVIEW_STATUS_PENDING

        await screener_dao.update_prediction_result(record_id, 5.0, "WIN", t1_price=10.5)

        history_updated = await screener_dao.get_screening_history()
        assert history_updated["review_status"].iloc[0] == REVIEW_STATUS_T1_DONE
        assert history_updated["t1_price"].iloc[0] == 10.5

    async def test_update_prediction_result_sets_completed_with_t5_metrics(self, screener_dao, clean_db):
        """T+5 指标就绪后应落库并推进到 COMPLETED"""
        from data.constants import REVIEW_STATUS_COMPLETED

        records = [
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        history = await screener_dao.get_screening_history()
        record_id = history["id"].iloc[0]

        await screener_dao.update_prediction_result(
            record_id,
            5.0,
            "WIN",
            t1_price=10.5,
            t5_pct=12.0,
            t5_price=11.2,
            index_pct=1.4,
            alpha=3.6,
        )

        history_updated = await screener_dao.get_screening_history()
        assert history_updated["review_status"].iloc[0] == REVIEW_STATUS_COMPLETED
        assert history_updated["t5_price"].iloc[0] == 11.2
        assert history_updated["t5_pct"].iloc[0] == 12.0
        assert history_updated["index_pct"].iloc[0] == 1.4
        assert history_updated["alpha"].iloc[0] == 3.6

    async def test_get_pending_reviews_filters_by_review_status(self, screener_dao, clean_db):
        """get_pending_reviews 应返回未完成复盘的记录并排除 COMPLETED"""

        records_pending = [
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records_pending)

        records_t1_done = [
            (
                "RUN002",
                _RECENT_DATE,
                "oversold",
                "000002.SZ",
                "万科A",
                15.0,
                3.0,
                "房地产",
                2000000,
                20000000,
                2.0,
                6.0,
                0.6,
                1.5,
                2.5,
                2000000000.0,
                1000000000.0,
                15.0,
                35.0,
                75.0,
                6.0,
                9.0,
                80,
                "理由2",
                "思考2",
                None,
            ),
        ]
        await screener_dao.save_screening_results(records_t1_done)

        records_completed = [
            (
                "RUN003",
                _RECENT_DATE,
                "oversold",
                "000003.SZ",
                "招商银行",
                18.0,
                1.5,
                "银行",
                1500000,
                15000000,
                1.8,
                5.8,
                0.7,
                1.4,
                2.2,
                1500000000.0,
                700000000.0,
                14.0,
                32.0,
                70.0,
                4.5,
                7.5,
                82,
                "理由3",
                "思考3",
                None,
            ),
        ]
        await screener_dao.save_screening_results(records_completed)

        history = await screener_dao.get_screening_history()
        t1_done_id = history[history["ts_code"] == "000002.SZ"]["id"].iloc[0]
        completed_id = history[history["ts_code"] == "000003.SZ"]["id"].iloc[0]
        await screener_dao.update_prediction_result(t1_done_id, 3.0, "LOSS", t1_price=15.4)
        await screener_dao.update_prediction_result(
            completed_id,
            4.0,
            "WIN",
            t1_price=18.5,
            t5_pct=9.0,
            t5_price=19.2,
            index_pct=1.0,
            alpha=3.0,
        )

        pending = await screener_dao.get_pending_reviews()
        pending_codes = [r["ts_code"] for r in pending]
        assert "000001.SZ" in pending_codes
        assert "000002.SZ" in pending_codes
        assert "000003.SZ" not in pending_codes

    async def test_get_screening_data_complex_join(self, screener_dao, clean_db, setup_stock_data):
        """测试复杂 JOIN 查询获取筛选数据"""
        result = await screener_dao.get_screening_data(trade_date=_RECENT_DATE)

        assert not result.empty
        assert result["ts_code"].iloc[0] == "000001.SZ"
        assert result["close"].iloc[0] == 10.0
        assert result["pe_ttm"].iloc[0] == 5.0
        assert result["roe"].iloc[0] == 12.0

    async def test_save_screening_results_column_order(self, screener_dao, clean_db):
        """验证动态列推导与 tuple 顺序一致，防止列错位"""
        from data.persistence.models import ScreeningHistory, get_model_columns

        all_cols = get_model_columns(
            ScreeningHistory,
            exclude={
                "id",
                "updated_at",
                "created_at",
                "t1_price",
                "t1_pct",
                "t5_price",
                "t5_pct",
                "index_pct",
                "alpha",
                "prediction_result",
                "review_status",
            },
        )
        expected_order = [
            "run_id",
            "trade_date",
            "strategy_name",
            "ts_code",
            "name",
            "close",
            "pct_chg",
            "industry",
            "vol",
            "amount",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ttm",
            "total_mv",
            "circ_mv",
            "roe",
            "grossprofit_margin",
            "debt_to_assets",
            "or_yoy",
            "netprofit_yoy",
            "ai_score",
            "ai_reason",
            "params_snapshot",
        ]
        assert all_cols == expected_order, f"Column order mismatch: {all_cols}"

        records = [
            (
                "RUN001",
                _RECENT_DATE,
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
                None,
            ),
        ]
        await screener_dao.save_screening_results(records)

        cols_str = ", ".join(all_cols)
        result = await screener_dao._read_db(
            f"SELECT {cols_str} FROM screening_history WHERE strategy_name=$1", ["oversold"]
        )
        assert not result.empty
        assert result["name"].iloc[0] == "平安银行"
        assert result["close"].iloc[0] == 10.0
        assert result["industry"].iloc[0] == "银行"
        assert result["ai_score"].iloc[0] == 85
        assert result["ai_reason"].iloc[0] == "AI推荐理由"

    async def test_get_fundamental_screening_data_includes_suspended(self, screener_dao, clean_db, setup_stock_data):
        """基本面筛选数据应包含无行情/停牌股票，且包含is_tradable列"""
        result = await screener_dao.get_fundamental_screening_data(trade_date=_RECENT_DATE)

        assert not result.empty
        ts_codes = set(result["ts_code"].tolist())
        assert "000001.SZ" in ts_codes
        assert "000002.SZ" in ts_codes
        assert "is_tradable" in result.columns

    async def test_get_field_completeness(self, quote_dao, clean_db, setup_stock_data):
        """字段级基本面完整度查询"""
        result = await quote_dao.get_field_completeness(trade_date=_RECENT_DATE)

        assert isinstance(result, dict)
        assert "roe" in result
        assert result["roe"] > 0
        assert "pe_ttm" in result
        assert result["pe_ttm"] > 0
        assert "or_yoy" in result
        assert result["or_yoy"] == 0.0


@pytest.mark.asyncio
class TestHolderDao:
    """测试 HolderDao 业务方法"""

    async def test_save_holder_number(self, holder_dao, clean_db):
        """保存股东人数（仅一条记录时change/ratio为NULL）"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 100000,
                }
            ]
        )

        saved = await holder_dao.save_holder_number(df)
        assert saved == 1

        result = await holder_dao.get_stk_holdernumber("000001.SZ")
        assert len(result) == 1
        assert pd.isna(result["holder_num_change"].iloc[0])
        assert pd.isna(result["holder_num_ratio"].iloc[0])

    async def test_save_top10_holders(self, holder_dao, clean_db):
        """保存前十大股东"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_name": "中国平安保险",
                    "hold_amount": 1000000000,
                    "hold_ratio": 10.0,
                    "holder_type": "公司",
                }
            ]
        )

        saved = await holder_dao.save_top10_holders(df)
        assert saved == 1

    async def test_holder_number_change_calculation(self, holder_dao, clean_db):
        """测试股东户数变化计算逻辑"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "end_date": "20230930", "ann_date": "20231025", "holder_num": 100000},
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 95000,
                },
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 90000,
                },
            ]
        )

        saved = await holder_dao.save_holder_number(df)
        assert saved == 3

        result = await holder_dao.get_stk_holdernumber("000001.SZ")
        assert len(result) == 3

        result = result.sort_values("end_date")

        assert result["holder_num_change"].iloc[0] is None or pd.isna(result["holder_num_change"].iloc[0])
        assert result["holder_num_change"].iloc[1] == -5000
        assert result["holder_num_change"].iloc[2] == -5000

        assert result["holder_num_ratio"].iloc[0] is None or pd.isna(result["holder_num_ratio"].iloc[0])
        assert abs(result["holder_num_ratio"].iloc[1] - (-5.0)) < 0.01
        assert abs(result["holder_num_ratio"].iloc[2] - (-5.26)) < 0.1

    async def test_holder_number_change_multi_stock(self, holder_dao, clean_db):
        """测试多股票股东户数变化计算互不干扰"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "end_date": "20230930", "ann_date": "20231025", "holder_num": 100000},
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 90000,
                },
                {"ts_code": "000002.SZ", "end_date": "20230930", "ann_date": "20231025", "holder_num": 50000},
                {
                    "ts_code": "000002.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 60000,
                },
            ]
        )

        saved = await holder_dao.save_holder_number(df)
        assert saved == 4

        result_1 = await holder_dao.get_stk_holdernumber("000001.SZ")
        result_1 = result_1.sort_values("end_date")
        assert result_1["holder_num_change"].iloc[1] == -10000
        assert abs(result_1["holder_num_ratio"].iloc[1] - (-10.0)) < 0.01

        result_2 = await holder_dao.get_stk_holdernumber("000002.SZ")
        result_2 = result_2.sort_values("end_date")
        assert result_2["holder_num_change"].iloc[1] == 10000
        assert abs(result_2["holder_num_ratio"].iloc[1] - 20.0) < 0.01

    async def test_holder_number_change_incremental_update(self, holder_dao, clean_db):
        """测试增量更新时历史数据的变化率被正确重算"""
        df1 = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "end_date": "20230930", "ann_date": "20231025", "holder_num": 100000},
            ]
        )
        await holder_dao.save_holder_number(df1)

        result = await holder_dao.get_stk_holdernumber("000001.SZ")
        assert len(result) == 1
        assert pd.isna(result["holder_num_change"].iloc[0])
        assert pd.isna(result["holder_num_ratio"].iloc[0])

        df2 = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": _RECENT_DATE_MINUS_90_STR,
                    "ann_date": _RECENT_DATE_STR,
                    "holder_num": 90000,
                },
            ]
        )
        await holder_dao.save_holder_number(df2)

        result = await holder_dao.get_stk_holdernumber("000001.SZ")
        result = result.sort_values("end_date")
        assert len(result) == 2
        assert pd.isna(result["holder_num_change"].iloc[0])
        assert result["holder_num_change"].iloc[1] == -10000
        assert abs(result["holder_num_ratio"].iloc[1] - (-10.0)) < 0.01


@pytest.mark.asyncio
class TestMacroDao:
    """测试 MacroDao 业务方法"""

    async def test_save_and_get_macro_economy(self, macro_dao, clean_db):
        """保存并查询宏观经济数据"""
        df = pd.DataFrame(
            [
                {
                    "period": _RECENT_DATE_MINUS_90,
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
        assert result == (_TODAY - timedelta(days=91))

    async def test_save_shibor_daily(self, macro_dao, clean_db):
        """保存 Shibor 利率"""
        df = pd.DataFrame(
            [
                {
                    "date": _RECENT_DATE,
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
        assert result == (_TODAY - timedelta(days=1))


@pytest.mark.asyncio
class TestSyncDao:
    """测试 SyncDao 业务方法"""

    async def test_update_and_get_sync_status(self, sync_dao, clean_db):
        """更新并获取同步状态"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
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
            last_data_date=_RECENT_DATE_MINUS_2,
            record_count=4000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["record_count"] == 5000

    async def test_sync_status_monotonic_date_protection(self, sync_dao, clean_db):
        """last_data_date 单调递增保护：旧日期不应覆盖新日期"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE_MINUS_90,
            record_count=3000,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_data_date"] == _RECENT_DATE
        assert result["record_count"] == 5000

    async def test_sync_status_same_date_updates_count(self, sync_dao, clean_db):
        """同日重跑：last_data_date 不变，record_count 应更新"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5500,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_data_date"] == _RECENT_DATE
        assert result["record_count"] == 5500

    async def test_sync_status_null_recovery(self, sync_dao, clean_db):
        """last_data_date 为 NULL 时，新日期应能正常写入（COALESCE 保护）"""
        await sync_dao._write_db(
            'INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","updated_at") '
            "VALUES ($1, $2, NULL, NULL, 'error', $3)",
            ("daily_quotes", _RECENT_DATE_MINUS_2, datetime.datetime.combine(_RECENT_DATE_MINUS_2, datetime.time())),
        )

        result_before = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result_before["last_data_date"] is pd.NaT or result_before["last_data_date"] is None

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        result_after = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result_after["last_data_date"] == _RECENT_DATE
        assert result_after["record_count"] == 5000
        assert result_after["status"] == "success"

    async def test_sync_status_error_does_not_overwrite_success(self, sync_dao, clean_db):
        """失败状态写入时，last_data_date 和 record_count 应保留上次成功的值"""
        await sync_dao.update_sync_status(
            table_name="daily_indicators",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_indicators",
            last_data_date=_TODAY,
            record_count=0,
            status="permission_denied",
        )

        result = await sync_dao.get_sync_status(table_name="daily_indicators")
        assert result["last_data_date"] == _RECENT_DATE
        assert result["record_count"] == 5000
        assert result["status"] == "permission_denied"

    async def test_sync_status_error_on_null_keeps_null_date(self, sync_dao, clean_db):
        """DB 中 last_data_date 为 NULL 时，失败状态不应推进日期"""
        await sync_dao._write_db(
            'INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","updated_at") '
            "VALUES ($1, $2, NULL, NULL, 'error', $3)",
            ("moneyflow_daily", _RECENT_DATE_MINUS_2, datetime.datetime.combine(_RECENT_DATE_MINUS_2, datetime.time())),
        )

        await sync_dao.update_sync_status(
            table_name="moneyflow_daily",
            last_data_date=_TODAY,
            record_count=0,
            status="permission_denied",
        )

        result = await sync_dao.get_sync_status(table_name="moneyflow_daily")
        assert result["last_data_date"] is pd.NaT or result["last_data_date"] is None
        assert result["status"] == "permission_denied"

    async def test_get_all_sync_status(self, sync_dao, clean_db):
        """获取所有同步状态"""
        await sync_dao.update_sync_status("daily_quotes", _RECENT_DATE, 5000)
        await sync_dao.update_sync_status("stock_basic", _RECENT_DATE, 5000)

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

    async def test_sync_status_last_result_status_has_data(self, sync_dao, clean_db):
        """成功同步有数据时，last_result_status 应为 HAS_DATA"""
        from data.constants import SYNC_RESULT_HAS_DATA

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_result_status"] == SYNC_RESULT_HAS_DATA

    async def test_sync_status_last_result_status_empty(self, sync_dao, clean_db):
        """成功同步空数据时，last_result_status 应为 EMPTY"""
        from data.constants import SYNC_RESULT_EMPTY

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=0,
            status="success",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_result_status"] == SYNC_RESULT_EMPTY

    async def test_sync_status_last_result_status_fetch_failed(self, sync_dao, clean_db):
        """获取失败时，last_result_status 应为 FETCH_FAILED"""
        from data.constants import SYNC_RESULT_FETCH_FAILED

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=0,
            status="fetch_failed",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_result_status"] == SYNC_RESULT_FETCH_FAILED

    async def test_sync_status_last_result_status_save_failed(self, sync_dao, clean_db):
        """保存失败时，last_result_status 应为 SAVE_FAILED"""
        from data.constants import SYNC_RESULT_SAVE_FAILED

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=0,
            status="save_failed",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_result_status"] == SYNC_RESULT_SAVE_FAILED

    async def test_sync_status_empty_status_updates_date(self, sync_dao, clean_db):
        """empty 状态应更新 last_data_date 和 record_count"""
        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_RECENT_DATE,
            record_count=5000,
            status="success",
        )

        await sync_dao.update_sync_status(
            table_name="daily_quotes",
            last_data_date=_TODAY,
            record_count=0,
            status="empty",
        )

        result = await sync_dao.get_sync_status(table_name="daily_quotes")
        assert result["last_data_date"] == _TODAY
        assert result["record_count"] == 0
        assert result["status"] == "empty"


class TestScreenerDaoDynamicCols:
    """Tests for ScreenerDao dynamic column reflection"""

    def test_sh_base_cols_excludes_thinking(self):
        """Verify SH_BASE_COLS dynamically reflects columns and excludes 'thinking'"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        cols_str = dao.SH_BASE_COLS

        col_list = [c.strip() for c in cols_str.split(",")]

        assert "sh.thinking" not in col_list
        assert "sh.id" in col_list
        assert "sh.trade_date" in col_list
        assert "sh.ts_code" in col_list
        assert "sh.ai_score" in col_list
        assert "sh.prediction_result" in col_list

    def test_sh_full_cols_includes_thinking(self):
        """Verify SH_FULL_COLS includes 'thinking' via JOIN and 'params_snapshot'"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        full_cols = dao.SH_FULL_COLS

        assert "st.thinking" in full_cols
        assert "sh.params_snapshot" in full_cols

    def test_sh_base_cols_matches_model(self):
        """Verify SH_BASE_COLS count matches ScreeningHistory columns minus excluded fields"""
        from data.persistence.daos.screener_dao import ScreenerDao
        from data.persistence.models import ScreeningHistory, get_model_columns

        dao = ScreenerDao.__new__(ScreenerDao)
        col_list = [c.strip() for c in dao.SH_BASE_COLS.split(",")]

        expected_cols = get_model_columns(
            ScreeningHistory,
            exclude={"updated_at", "created_at", "params_snapshot"},
        )
        assert len(col_list) == len(expected_cols)

    def test_sh_base_cols_cached(self):
        """Verify cached_property only computes once"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        result1 = dao.SH_BASE_COLS
        result2 = dao.SH_BASE_COLS

        assert result1 is result2
