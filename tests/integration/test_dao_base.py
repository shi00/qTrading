"""
Tests for DAO layer methods.

验证底层 DAO 方法的正确性，包括 CRUD 操作、参数转换、边界条件等。
使用 conftest.py 中的共享 test_engine 和事务隔离。
"""

import datetime

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import BaseDao
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.stock_dao import StockDao
from tests.integration.test_infra_base import make_clean_db_fixture


class TestBaseDaoConvertParam:
    """测试 BaseDao._convert_param_for_asyncpg 方法"""

    def test_convert_none(self):
        """None 值保持不变"""
        result = BaseDao._convert_param_for_asyncpg(None)
        assert result is None

    def test_convert_date_string_yyyymmdd(self):
        """YYYYMMDD 格式字符串转换为 date"""
        result = BaseDao._convert_param_for_asyncpg("20240321")
        assert result == datetime.date(2024, 3, 21)

    def test_convert_date_string_yyyy_mm_dd(self):
        """YYYY-MM-DD 格式字符串转换为 date"""
        result = BaseDao._convert_param_for_asyncpg("2024-03-21")
        assert result == datetime.date(2024, 3, 21)

    def test_convert_invalid_date_string(self):
        """无效日期字符串保持不变"""
        result = BaseDao._convert_param_for_asyncpg("invalid")
        assert result == "invalid"

    def test_convert_short_string(self):
        """短字符串保持不变"""
        result = BaseDao._convert_param_for_asyncpg("abc")
        assert result == "abc"

    def test_convert_date_object(self):
        """date 对象保持不变"""
        d = datetime.date(2024, 3, 21)
        result = BaseDao._convert_param_for_asyncpg(d)
        assert result == d

    def test_convert_integer(self):
        """整数保持不变"""
        result = BaseDao._convert_param_for_asyncpg(123)
        assert result == 123

    def test_convert_float(self):
        """浮点数保持不变"""
        result = BaseDao._convert_param_for_asyncpg(123.45)
        assert result == 123.45


class TestBaseDaoToDateStr:
    """测试 BaseDao._to_date_str 方法"""

    def test_none(self):
        result = BaseDao._to_date_str(None)
        assert result is None

    def test_string_passthrough(self):
        result = BaseDao._to_date_str("20240321")
        assert result == "20240321"

    def test_empty_string(self):
        result = BaseDao._to_date_str("")
        assert result == ""

    def test_date_object(self):
        d = datetime.date(2024, 3, 21)
        result = BaseDao._to_date_str(d)
        assert result == "20240321"

    def test_date_object_padding(self):
        d = datetime.date(2024, 1, 5)
        result = BaseDao._to_date_str(d)
        assert result == "20240105"


class TestBaseDaoQuoteColumns:
    """测试 BaseDao._quote_columns 方法"""

    def test_quote_single_column(self):
        """单列引用"""
        result = BaseDao._quote_columns(["col1"])
        assert result == '"col1"'

    def test_quote_multiple_columns(self):
        """多列引用"""
        result = BaseDao._quote_columns(["col1", "col2", "col3"])
        assert result == '"col1","col2","col3"'

    def test_quote_reserved_word(self):
        """保留字列名正确引用"""
        result = BaseDao._quote_columns(["date", "on", "order"])
        assert result == '"date","on","order"'


@pytest_asyncio.fixture
async def quote_dao(test_engine: AsyncEngine):
    """创建 QuoteDao 实例，使用共享测试引擎"""
    return QuoteDao(test_engine)


@pytest_asyncio.fixture
async def stock_dao(test_engine: AsyncEngine):
    """创建 StockDao 实例，使用共享测试引擎"""
    return StockDao(test_engine)


@pytest_asyncio.fixture
async def financial_dao(test_engine: AsyncEngine):
    """创建 FinancialDao 实例，使用共享测试引擎"""
    return FinancialDao(test_engine)


# 共享 clean_db fixture：表清单从 ORM metadata 动态生成（INT-P2-1）
clean_db = make_clean_db_fixture()


@pytest.mark.asyncio
class TestQuoteDao:
    """测试 QuoteDao 异步方法"""

    async def test_get_daily_quotes_empty(self, quote_dao, clean_db):
        """空数据库查询返回空 DataFrame"""
        result = await quote_dao.get_daily_quotes(ts_code="000001.SZ")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    async def test_save_and_get_daily_quotes(self, quote_dao, clean_db):
        """保存并查询日线数据"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.2,
                    "pre_close": 10.0,
                    "change": 0.2,
                    "pct_chg": 2.0,
                    "vol": 1000000,
                    "amount": 10100000.0,
                    "adj_factor": 1.0,
                }
            ]
        )

        saved = await quote_dao.save_daily_quotes(df)
        assert saved == 1

        result = await quote_dao.get_daily_quotes(ts_code="000001.SZ")
        assert not result.empty
        assert len(result) == 1
        assert result["ts_code"].iloc[0] == "000001.SZ"

    async def test_get_daily_quotes_with_date_range(self, quote_dao, clean_db):
        """按日期范围查询"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240320",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "pre_close": 9.8,
                    "change": 0.2,
                    "pct_chg": 2.0,
                    "vol": 1000000,
                    "amount": 10000000.0,
                    "adj_factor": 1.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.2,
                    "pre_close": 10.0,
                    "change": 0.2,
                    "pct_chg": 2.0,
                    "vol": 1000000,
                    "amount": 10100000.0,
                    "adj_factor": 1.0,
                },
            ]
        )

        await quote_dao.save_daily_quotes(df)

        result = await quote_dao.get_daily_quotes(ts_code="000001.SZ", start_date="20240321", end_date="20240321")
        assert len(result) == 1
        assert result["trade_date"].iloc[0] == datetime.date(2024, 3, 21)

    async def test_get_daily_quotes_with_code_list(self, quote_dao, clean_db):
        """按代码列表查询"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "change": 0.0,
                    "pct_chg": 0.0,
                    "vol": 1000000,
                    "amount": 10000000.0,
                    "adj_factor": 1.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "trade_date": "20240321",
                    "open": 20.0,
                    "high": 20.5,
                    "low": 19.5,
                    "close": 20.0,
                    "pre_close": 20.0,
                    "change": 0.0,
                    "pct_chg": 0.0,
                    "vol": 2000000,
                    "amount": 40000000.0,
                    "adj_factor": 1.0,
                },
            ]
        )

        await quote_dao.save_daily_quotes(df)

        result = await quote_dao.get_daily_quotes(ts_code_list=["000001.SZ", "000002.SZ"])
        assert len(result) == 2

    async def test_check_data_exists(self, quote_dao, clean_db):
        """检查数据是否存在 - 使用 tables 参数只检查 quotes 表"""
        assert not await quote_dao.check_data_exists("20240321", tables=["daily_quotes"])

        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "change": 0.0,
                    "pct_chg": 0.0,
                    "vol": 1000000,
                    "amount": 10000000.0,
                    "adj_factor": 1.0,
                }
            ]
        )
        await quote_dao.save_daily_quotes(df)

        assert await quote_dao.check_data_exists("20240321", tables=["daily_quotes"])

    async def test_get_latest_trade_date(self, quote_dao, clean_db):
        """获取最新交易日期"""
        result = await quote_dao.get_latest_trade_date()
        assert result is None

        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "change": 0.0,
                    "pct_chg": 0.0,
                    "vol": 1000000,
                    "amount": 10000000.0,
                    "adj_factor": 1.0,
                }
            ]
        )
        await quote_dao.save_daily_quotes(df)

        result = await quote_dao.get_latest_trade_date()
        assert result == datetime.date(2024, 3, 21)

    async def test_upsert_same_record(self, quote_dao, clean_db):
        """相同主键记录更新"""
        df1 = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "change": 0.0,
                    "pct_chg": 0.0,
                    "vol": 1000000,
                    "amount": 10000000.0,
                    "adj_factor": 1.0,
                }
            ]
        )
        await quote_dao.save_daily_quotes(df1)

        df2 = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240321",
                    "open": 11.0,
                    "high": 11.5,
                    "low": 10.5,
                    "close": 11.0,
                    "pre_close": 10.0,
                    "change": 1.0,
                    "pct_chg": 10.0,
                    "vol": 2000000,
                    "amount": 22000000.0,
                    "adj_factor": 1.0,
                }
            ]
        )
        await quote_dao.save_daily_quotes(df2)

        result = await quote_dao.get_daily_quotes(ts_code="000001.SZ")
        assert len(result) == 1
        assert result["close"].iloc[0] == 11.0


@pytest.mark.asyncio
class TestStockDao:
    """测试 StockDao 异步方法"""

    async def test_save_and_get_stock_basic(self, stock_dao, clean_db):
        """保存并查询股票基本信息"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "area": "深圳",
                    "industry": "银行",
                    "market": "主板",
                    "list_date": "19910403",
                    "list_status": "L",
                }
            ]
        )

        saved = await stock_dao.save_stock_basic(df)
        assert saved == 1

        result = await stock_dao.get_stock_basic()
        assert not result.empty
        assert result["ts_code"].iloc[0] == "000001.SZ"

    async def test_get_active_stock_count(self, stock_dao, clean_db):
        """获取活跃股票数量"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "list_status": "L",
                },
                {
                    "ts_code": "000002.SZ",
                    "symbol": "000002",
                    "name": "万科A",
                    "list_status": "L",
                },
                {
                    "ts_code": "000003.SZ",
                    "symbol": "000003",
                    "name": "退市股票",
                    "list_status": "D",
                },
            ]
        )
        await stock_dao.save_stock_basic(df)

        count = await stock_dao.get_active_stock_count()
        assert count == 2

    async def test_save_and_get_trade_cal(self, stock_dao, clean_db):
        """保存并查询交易日历"""
        df = pd.DataFrame(
            [
                {
                    "cal_date": "20240321",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240320",
                },
                {
                    "cal_date": "20240322",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240321",
                },
                {
                    "cal_date": "20240323",
                    "exchange": "SSE",
                    "is_open": "0",
                    "pretrade_date": "20240322",
                },
            ]
        )

        saved = await stock_dao.save_trade_cal(df)
        assert saved == 3

        result = await stock_dao.get_trade_cal(start_date="20240321", end_date="20240322", is_open="1")
        assert len(result) == 2

    async def test_count_trade_days(self, stock_dao, clean_db):
        """统计交易日数量"""
        df = pd.DataFrame(
            [
                {
                    "cal_date": "20240318",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240315",
                },
                {
                    "cal_date": "20240319",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240318",
                },
                {
                    "cal_date": "20240320",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240319",
                },
                {
                    "cal_date": "20240321",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240320",
                },
                {
                    "cal_date": "20240322",
                    "exchange": "SSE",
                    "is_open": "0",
                    "pretrade_date": "20240321",
                },
            ]
        )
        await stock_dao.save_trade_cal(df)

        count = await stock_dao.count_trade_days(datetime.date(2024, 3, 18), datetime.date(2024, 3, 21))
        assert count == 4

    async def test_get_start_date_by_trade_days(self, stock_dao, clean_db):
        """根据交易日回溯起始日期"""
        df = pd.DataFrame(
            [
                {
                    "cal_date": "20240318",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240315",
                },
                {
                    "cal_date": "20240319",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240318",
                },
                {
                    "cal_date": "20240320",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240319",
                },
                {
                    "cal_date": "20240321",
                    "exchange": "SSE",
                    "is_open": "1",
                    "pretrade_date": "20240320",
                },
            ]
        )
        await stock_dao.save_trade_cal(df)

        result = await stock_dao.get_start_date_by_trade_days(datetime.date(2024, 3, 21), 3)
        assert result == datetime.date(2024, 3, 19)

    async def test_save_and_get_concepts(self, stock_dao, clean_db):
        """保存并查询概念"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "concept_name": "金融科技",
                    "concept_id": "TS001",
                },
                {
                    "ts_code": "000001.SZ",
                    "concept_name": "数字货币",
                    "concept_id": "TS002",
                },
            ]
        )

        saved = await stock_dao.save_concepts(df)
        assert saved == 2

        result = await stock_dao.get_concepts(ts_codes=["000001.SZ"])
        assert "000001.SZ" in result
        assert len(result["000001.SZ"]) == 2


@pytest.mark.asyncio
class TestFinancialDao:
    """测试 FinancialDao 异步方法"""

    async def test_save_and_get_financial_reports(self, financial_dao, clean_db):
        """保存并查询财务报告"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240330",
                    "report_type": "1",
                    "total_revenue": 1000000000.0,
                    "revenue": 900000000.0,
                    "n_income": 100000000.0,
                    "n_income_attr_p": 95000000.0,
                    "total_assets": 5000000000.0,
                    "total_liab": 4000000000.0,
                    "total_hldr_eqy_exc_min_int": 1000000000.0,
                    "roe": 10.0,
                    "roe_dt": 9.5,
                    "grossprofit_margin": 30.0,
                    "netprofit_margin": 10.0,
                    "debt_to_assets": 80.0,
                    "or_yoy": 5.0,
                    "netprofit_yoy": 8.0,
                    "goodwill": 0.0,
                }
            ]
        )

        saved = await financial_dao.save_financial_reports(df)
        assert saved == 1

        result = await financial_dao.get_cached_financial_records(period="20231231")
        assert len(result) == 1
        assert ("000001.SZ", datetime.date(2023, 12, 31)) in result

    async def test_get_cached_financial_records_empty(self, financial_dao, clean_db):
        """空数据库查询返回空集合"""
        result = await financial_dao.get_cached_financial_records()
        assert result == set()

    async def test_get_latest_indicators_empty(self, financial_dao, clean_db):
        """空数据库查询最新指标返回空 DataFrame"""
        result = await financial_dao.get_latest_indicators()
        assert result.empty

    async def test_get_latest_indicators_prefers_quote_aligned_date(self, financial_dao, test_engine, clean_db):
        """未指定日期时，应避免返回晚于行情表最新日期的指标数据。"""
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO daily_quotes
                    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
                    VALUES
                    ('000001.SZ', '2024-01-05', 10.0, 10.5, 9.8, 10.2, 10.0, 0.2, 2.0, 1000, 10000, 1.0)
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO daily_indicators
                    (ts_code, trade_date, pe, pe_ttm, pb, total_mv, circ_mv, total_share, float_share, free_share)
                    VALUES
                    ('000001.SZ', '2024-01-05', 10.0, 9.5, 1.2, 100000, 50000, 1000, 900, 800),
                    ('000001.SZ', '2024-01-08', 20.0, 19.5, 2.2, 110000, 55000, 1000, 900, 800)
                    """
                )
            )

        result = await financial_dao.get_latest_indicators()

        assert len(result) == 1
        assert result.iloc[0]["trade_date"] == datetime.date(2024, 1, 5)
        assert result.iloc[0]["pe"] == 10.0
