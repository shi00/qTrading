import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.persistence.daos.quote_dao import QuoteDao, _is_safe_identifier, _normalize_trade_date


class TestIsSafeIdentifier:
    def test_valid(self):
        assert _is_safe_identifier("daily_quotes") is True
        assert _is_safe_identifier("stock_basic") is True

    def test_invalid(self):
        assert _is_safe_identifier("DROP TABLE") is False
        assert _is_safe_identifier("1table") is False
        assert _is_safe_identifier("") is False


class TestNormalizeTradeDate:
    def test_date(self):
        d = datetime.date(2024, 6, 15)
        assert _normalize_trade_date(d) == d

    def test_datetime(self):
        dt = datetime.datetime(2024, 6, 15, 10, 30)
        result = _normalize_trade_date(dt)
        assert result == datetime.date(2024, 6, 15)

    def test_string(self):
        result = _normalize_trade_date("20240615")
        assert result == datetime.date(2024, 6, 15)

    def test_invalid_string(self):
        result = _normalize_trade_date("invalid")
        assert result == "invalid"


class TestQuoteDaoSaveDailyQuotes:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_daily_quotes(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 5


class TestQuoteDaoGetDailyQuotes:
    @pytest.mark.asyncio
    async def test_by_code(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_dates(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(start_date="20240101", end_date="20240630")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_code_list(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(ts_code_list=["000001.SZ", "000002.SZ"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_large_code_list_chunked(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        codes = [f"{i:06d}.SZ" for i in range(600)]
        result = await dao.get_daily_quotes(ts_code_list=codes)
        assert result is not None


class TestQuoteDaoGetLatestTradeDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": ["20240615"]}))
        result = await dao.get_latest_trade_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": []}))
        result = await dao.get_latest_trade_date()
        assert result is None


class TestQuoteDaoGetCachedTradeDates:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615", "20240614"]}))
        result = await dao.get_cached_trade_dates()
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_cached_trade_dates()
        assert result == set()


class TestQuoteDaoGetDateRange:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "min_date": ["20240101"],
                    "max_date": ["20240615"],
                }
            )
        )
        min_d, max_d = await dao.get_date_range()
        assert min_d == "20240101"
        assert max_d == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        min_d, max_d = await dao.get_date_range()
        assert min_d is None
        assert max_d is None


class TestQuoteDaoSaveIndexDaily:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_index_daily(pd.DataFrame({"ts_code": ["000001.SH"]}))
        assert result == 3


class TestQuoteDaoGetIndexDaily:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily(ts_code="000001.SH")
        assert result is not None


class TestQuoteDaoGetIndexDailyRange:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_index_daily_range([])
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_codes(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily_range(["000001.SH"], start_date="20240101")
        assert result is not None


class TestQuoteDaoSaveBlockTrade:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=2)
        result = await dao.save_block_trade(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 2


class TestQuoteDaoGetBlockTrade:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_block_trade(trade_date="20240615")
        assert result is not None


class TestQuoteDaoSaveLimitList:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_limit_list(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoSaveTopList:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_top_list(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetTopList:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_top_list(trade_date="20240615")
        assert result is not None


class TestQuoteDaoSaveMarginDaily:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_margin_daily(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoSaveSuspendD:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_suspend_d(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoSaveMoneyflow:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_moneyflow(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetMoneyflow:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_moneyflow(trade_date="20240615")
        assert result is not None


class TestQuoteDaoSaveNorthbound:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_northbound(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetNorthbound:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_northbound(trade_date="20240615")
        assert result is not None


class TestQuoteDaoGetLatestNorthbound:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_latest_northbound()
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_latest_northbound()
        assert isinstance(result, pd.DataFrame)


class TestQuoteDaoGetCachedDatesForTable:
    @pytest.mark.asyncio
    async def test_valid_table(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"]}))
        result = await dao.get_cached_dates_for_table("daily_quotes")
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_invalid_table(self):
        dao = QuoteDao(MagicMock())
        result = await dao.get_cached_dates_for_table("invalid_table")
        assert result == set()

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_cached_dates_for_table("daily_quotes")
        assert result == set()


class TestQuoteDaoCheckDataExists:
    @pytest.mark.asyncio
    async def test_all_tables_have_data(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            dao._read_db = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes"],
                        "val": [1],
                    }
                )
            )
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is True

    @pytest.mark.asyncio
    async def test_missing_data(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            dao._read_db = AsyncMock(return_value=pd.DataFrame())
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is False

    @pytest.mark.asyncio
    async def test_invalid_table(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            result = await dao.check_data_exists("20240615", tables=["invalid_table"])
            assert result is False

    @pytest.mark.asyncio
    async def test_empty_tables(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=[]):
            result = await dao.check_data_exists("20240615", tables=[])
            assert result is True


class TestQuoteDaoGetExpectedStockCount:
    @pytest.mark.asyncio
    async def test_trading_day(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "is_trade_day": [1],
                    "cnt": [5000],
                }
            )
        )
        result = await dao.get_expected_stock_count("20240615")
        assert result == 5000

    @pytest.mark.asyncio
    async def test_non_trading_day(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "is_trade_day": [0],
                    "cnt": [5000],
                }
            )
        )
        result = await dao.get_expected_stock_count("20240616")
        assert result == 0

    @pytest.mark.asyncio
    async def test_error(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_expected_stock_count("20240615")
        assert result == 0


class TestQuoteDaoGetBulkTableCounts:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            dao._read_db = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "trade_date": ["20240615"],
                        "cnt": [100],
                    }
                )
            )
            result = await dao.get_bulk_table_counts("daily_quotes", "20240615", "20240615")
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_invalid_table(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            result = await dao.get_bulk_table_counts("invalid_table", "20240615", "20240615")
            assert result == {}

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            dao._read_db = AsyncMock(return_value=pd.DataFrame())
            result = await dao.get_bulk_table_counts("daily_quotes", "20240615", "20240615")
            assert result == {}


class TestQuoteDaoGetFieldCompleteness:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "total": [100],
                    "indicators_available": [80],
                    "roe_count": [70],
                    "or_yoy_count": [60],
                    "netprofit_yoy_count": [50],
                    "dv_ttm_count": [40],
                    "pe_ttm_count": [80],
                    "pb_count": [80],
                    "debt_to_assets_count": [60],
                }
            )
        )
        result = await dao.get_field_completeness("20240615")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_error(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_field_completeness("20240615")
        assert result == {}


class TestQuoteDaoSaveIndexDailybasic:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = QuoteDao(MagicMock())
        result = await dao.save_index_dailybasic(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = QuoteDao(MagicMock())
        result = await dao.save_index_dailybasic(pd.DataFrame())
        assert result == 0


class TestQuoteDaoGetBulkExpectedStockCounts:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240615", "20240614"],
                    "expected_count": [5000, 4990],
                }
            )
        )
        result = await dao.get_bulk_expected_stock_counts("20240614", "20240615")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_bulk_expected_stock_counts("20240614", "20240615")
        assert result == {}

    @pytest.mark.asyncio
    async def test_error(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_bulk_expected_stock_counts("20240614", "20240615")
        assert result == {}

    @pytest.mark.asyncio
    async def test_sql_uses_alive_ranges_cte(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_bulk_expected_stock_counts("20240101", "20240601")
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "alive_ranges" in sql
        assert "COALESCE" in sql
        assert "start_date" in sql
        assert "end_date" in sql

    @pytest.mark.asyncio
    async def test_sql_params_order(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_bulk_expected_stock_counts("20240101", "20240601")
        call_args = dao._read_db.call_args
        params = call_args[0][1]
        assert params[0] == "20240101"
        assert params[1] == "20240601"

    @pytest.mark.asyncio
    async def test_date_normalization(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": [datetime.date(2024, 6, 15)],
                    "expected_count": [5000],
                }
            )
        )
        result = await dao.get_bulk_expected_stock_counts("20240615", "20240615")
        assert datetime.date(2024, 6, 15) in result

    @pytest.mark.asyncio
    async def test_alive_ranges_prefilters_by_date_range(self):
        dao = QuoteDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_bulk_expected_stock_counts("20240101", "20240601")
        sql = dao._read_db.call_args[0][0]
        assert "list_date <= $2" in sql
        assert "COALESCE(delist_date, '2099-12-31'::date) > $1" in sql


class TestQuoteDaoGetSyncQualityScore:
    @pytest.mark.asyncio
    async def test_with_string_date(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_sync_quality_scores = AsyncMock(
            return_value={datetime.date(2024, 6, 15): {"score": 80, "expected_base": 5000, "tables": {}, "issues": []}}
        )
        result = await dao.get_sync_quality_score("20240615")
        assert result["score"] == 80

    @pytest.mark.asyncio
    async def test_with_date_object(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_sync_quality_scores = AsyncMock(
            return_value={datetime.date(2024, 6, 15): {"score": 90, "expected_base": 5000, "tables": {}, "issues": []}}
        )
        result = await dao.get_sync_quality_score(datetime.date(2024, 6, 15))
        assert result["score"] == 90

    @pytest.mark.asyncio
    async def test_no_result(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_sync_quality_scores = AsyncMock(return_value={})
        result = await dao.get_sync_quality_score("20240615")
        assert result["score"] == 0


class TestQuoteDaoGetBulkSyncQualityScores:
    @pytest.mark.asyncio
    async def test_no_expected_bases(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={})
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            result = await dao.get_bulk_sync_quality_scores("20240614", "20240615")
            assert result == {}

    @pytest.mark.asyncio
    async def test_with_zero_expected_base(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 0})
        dao.get_bulk_table_counts = AsyncMock(return_value={})
        with patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]):
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert datetime.date(2024, 6, 15) in result
            assert result[datetime.date(2024, 6, 15)]["score"] == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch("data.persistence.daos.quote_dao._get_default_synced_tables", return_value=["daily_quotes"]),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert datetime.date(2024, 6, 15) in result
            assert result[datetime.date(2024, 6, 15)]["score"] > 0

    @pytest.mark.asyncio
    async def test_with_low_frequency_tables(self):
        dao = QuoteDao(MagicMock())
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_default_synced_tables",
                return_value=["daily_quotes", "limit_list"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "limit_list": 2},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert result[datetime.date(2024, 6, 15)]["tables"]["limit_list"].get("exempt") is True
