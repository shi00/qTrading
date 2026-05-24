import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.financial_dao import FinancialDao


def _make_dao():
    dao = FinancialDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=None)
    dao._write_db = AsyncMock(return_value=0)
    return dao


class TestSaveFinancialReports:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = _make_dao()
        assert await dao.save_financial_reports(None) == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = _make_dao()
        assert await dao.save_financial_reports(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240630"]})
        result = await dao.save_financial_reports(df)
        assert result == 5
        dao._save_upsert.assert_called_once()


class TestGetCachedFinancialRecords:
    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_cached_financial_records() == set()

    @pytest.mark.asyncio
    async def test_empty_df(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        assert await dao.get_cached_financial_records() == set()

    @pytest.mark.asyncio
    async def test_with_period(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                }
            )
        )
        result = await dao.get_cached_financial_records(period="20240630")
        assert ("000001.SZ", "20240630") in result

    @pytest.mark.asyncio
    async def test_without_period(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "end_date": ["20240630", "20240630"],
                }
            )
        )
        result = await dao.get_cached_financial_records()
        assert len(result) == 2


class TestGetLatestIndicators:
    @pytest.mark.asyncio
    async def test_no_trade_date_no_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_latest_indicators()
        assert result.empty

    @pytest.mark.asyncio
    async def test_no_trade_date_with_max(self):
        dao = _make_dao()
        call_count = 0

        async def mock_read(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame({"max_td": ["20240615"]})
            return pd.DataFrame({"trade_date": ["20240615"], "ts_code": ["000001.SZ"]})

        dao._read_db = mock_read
        result = await dao.get_latest_indicators()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_trade_date_max_is_none(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_latest_indicators()
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240615"],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        result = await dao.get_latest_indicators(trade_date="20240615")
        assert len(result) == 1


class TestGetCachedIndicatorDates:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_cached_indicator_dates() == set()

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        assert await dao.get_cached_indicator_dates() == set()

    @pytest.mark.asyncio
    async def test_with_dates(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615", "20240614"]}))
        assert len(await dao.get_cached_indicator_dates()) == 2


class TestExtraSavers:
    @pytest.mark.asyncio
    async def test_save_fina_forecast_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_fina_forecast(df) == 5

    @pytest.mark.asyncio
    async def test_save_fina_mainbz_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_fina_mainbz(df) == 5

    @pytest.mark.asyncio
    async def test_save_fina_audit_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_fina_audit(df) == 5

    @pytest.mark.asyncio
    async def test_save_pledge_stat_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_pledge_stat(df) == 5

    @pytest.mark.asyncio
    async def test_save_repurchase_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_repurchase(df) == 5

    @pytest.mark.asyncio
    async def test_save_dividend_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_dividend(df) == 5


class TestGetFinancialReportsHistory:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                }
            )
        )
        result = await dao.get_financial_reports_history("000001.SZ")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_financial_reports_history("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_financial_reports_history("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                }
            )
        )
        result = await dao.get_financial_reports_history("000001.SZ", as_of_date="20240701")
        assert not result.empty
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "ann_date <=" in sql
        assert call_args[0][1] == ("000001.SZ", "20240701", 8)

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                }
            )
        )
        result = await dao.get_financial_reports_history("000001.SZ", as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestGetFinancialReportsHistoryBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_financial_reports_history_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                    "rn": [1],
                }
            )
        )
        result = await dao.get_financial_reports_history_batch(["000001.SZ"])
        assert "rn" not in result.columns

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_financial_reports_history_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_financial_reports_history_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                    "rn": [1],
                }
            )
        )
        result = await dao.get_financial_reports_history_batch(["000001.SZ"], as_of_date="20240701")
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240630"],
                    "rn": [1],
                }
            )
        )
        result = await dao.get_financial_reports_history_batch(["000001.SZ"], as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestGetFinaAuditBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_fina_audit_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "audit_result": ["标准无保留意见"],
                }
            )
        )
        result = await dao.get_fina_audit_batch(["000001.SZ"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_fina_audit_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_fina_audit_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "audit_result": ["标准无保留意见"],
                }
            )
        )
        result = await dao.get_fina_audit_batch(["000001.SZ"], as_of_date="20240701")
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "audit_result": ["标准无保留意见"],
                }
            )
        )
        result = await dao.get_fina_audit_batch(["000001.SZ"], as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestGetDividendBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_dividend_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "cash_div": [0.5],
                }
            )
        )
        result = await dao.get_dividend_batch(["000001.SZ"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_dividend_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_dividend_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "cash_div": [0.5],
                }
            )
        )
        result = await dao.get_dividend_batch(["000001.SZ"], as_of_date="20240701")
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "cash_div": [0.5],
                }
            )
        )
        result = await dao.get_dividend_batch(["000001.SZ"], as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestGetPledgeStatBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_pledge_stat_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "pledge_ratio": [10.5],
                }
            )
        )
        result = await dao.get_pledge_stat_batch(["000001.SZ"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_pledge_stat_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_pledge_stat_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "pledge_ratio": [10.5],
                }
            )
        )
        result = await dao.get_pledge_stat_batch(["000001.SZ"], as_of_date="20240701")
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "pledge_ratio": [10.5],
                }
            )
        )
        result = await dao.get_pledge_stat_batch(["000001.SZ"], as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "end_date <=" not in sql


class TestGetFinaMainbz:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                }
            )
        )
        result = await dao.get_fina_mainbz("000001.SZ")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_fina_mainbz("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_fina_mainbz("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                }
            )
        )
        result = await dao.get_fina_mainbz("000001.SZ", as_of_date="20240701")
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                }
            )
        )
        result = await dao.get_fina_mainbz("000001.SZ", as_of_date=None)
        assert not result.empty
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestGetFinaMainbzBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_fina_mainbz_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                    "dr": [1],
                }
            )
        )
        result = await dao.get_fina_mainbz_batch(["000001.SZ"])
        assert "dr" not in result.columns

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_fina_mainbz_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_fina_mainbz_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                    "dr": [1],
                }
            )
        )
        result = await dao.get_fina_mainbz_batch(["000001.SZ"], as_of_date="20240701")
        assert "dr" not in result.columns
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" in sql

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "bz_item": ["主营业务"],
                    "dr": [1],
                }
            )
        )
        result = await dao.get_fina_mainbz_batch(["000001.SZ"], as_of_date=None)
        assert "dr" not in result.columns
        sql = dao._read_db.call_args[0][0]
        assert "ann_date <=" not in sql


class TestVerifyStockFinancialIntegrity:
    @pytest.mark.asyncio
    async def test_valid(self):
        dao = _make_dao()
        call_count = 0

        async def mock_read(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame({"periods": [5]})
            return pd.DataFrame({"cnt": [10]})

        dao._read_db = mock_read
        result = await dao.verify_stock_financial_integrity("000001.SZ")
        assert result["valid"] is True
        assert result["periods"] == 5

    @pytest.mark.asyncio
    async def test_insufficient_periods(self):
        dao = _make_dao()
        call_count = 0

        async def mock_read(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame({"periods": [2]})
            return pd.DataFrame({"cnt": [10]})

        dao._read_db = mock_read
        result = await dao.verify_stock_financial_integrity("000001.SZ", min_periods=4)
        assert result["valid"] is False
        assert "报告期不足" in result["reason"]

    @pytest.mark.asyncio
    async def test_zero_count(self):
        dao = _make_dao()
        call_count = 0

        async def mock_read(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame({"periods": [5]})
            return pd.DataFrame({"cnt": [0]})

        dao._read_db = mock_read
        result = await dao.verify_stock_financial_integrity("000001.SZ")
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.verify_stock_financial_integrity("000001.SZ")
        assert result["valid"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_audit_exception(self):
        dao = _make_dao()
        call_count = 0

        async def mock_read(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame({"periods": [5]})
            if call_count == 2:
                return pd.DataFrame({"cnt": [10]})
            raise Exception("audit error")

        dao._read_db = mock_read
        result = await dao.verify_stock_financial_integrity("000001.SZ")
        assert result["tables"]["fina_audit"] == 0


class TestGetIncompleteFinancialStocks:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}))
        result = await dao.get_incomplete_financial_stocks()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_incomplete_financial_stocks()
        assert result == set()

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_incomplete_financial_stocks()
        assert result == set()
