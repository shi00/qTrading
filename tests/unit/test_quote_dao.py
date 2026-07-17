import pytest
import datetime
import sqlalchemy as sa
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.quote_dao import (
    QuoteDao,
    _is_safe_identifier,
    _normalize_trade_date,
)

pytestmark = pytest.mark.unit


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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_daily_quotes(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 5


class TestQuoteDaoGetDailyQuotes:
    @pytest.mark.asyncio
    async def test_by_code(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_dates(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(start_date="20240101", end_date="20240630")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_code_list(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes(ts_code_list=["000001.SZ", "000002.SZ"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_large_code_list_chunked(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        codes = [f"{i:06d}.SZ" for i in range(600)]
        result = await dao.get_daily_quotes(ts_code_list=codes)
        assert isinstance(result, pd.DataFrame) and "ts_code" in result.columns


class TestQuoteDaoGetLatestTradeDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": ["20240615"]}))
        result = await dao.get_latest_trade_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": []}))
        result = await dao.get_latest_trade_date()
        assert result is None


class TestQuoteDaoGetCachedTradeDates:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615", "20240614"]}))
        result = await dao.get_cached_trade_dates()
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_cached_trade_dates()
        assert result == set()


class TestQuoteDaoGetDateRange:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        min_d, max_d = await dao.get_date_range()
        assert min_d is None
        assert max_d is None


class TestQuoteDaoSaveIndexDaily:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_index_daily(pd.DataFrame({"ts_code": ["000001.SH"]}))
        assert result == 3


class TestQuoteDaoGetIndexDaily:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily(ts_code="000001.SH")
        assert isinstance(result, pd.DataFrame) and "ts_code" in result.columns


class TestQuoteDaoGetIndexDailyRange:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_index_daily_range([])
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_codes(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily_range(["000001.SH"], start_date="20240101")
        assert result is not None


class TestQuoteDaoSaveBlockTrade:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=2)
        result = await dao.save_block_trade(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 2


class TestQuoteDaoGetBlockTrade:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_block_trade(trade_date="20240615")
        assert result is not None


class TestQuoteDaoSaveLimitList:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_limit_list(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1

    @pytest.mark.asyncio
    async def test_save_renames_tushare_limit_column(self):
        """R17: Tushare API 返回 'limit' 列（SQL 保留字），写入前需重命名为 'limit_type'。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        df_in = pd.DataFrame({"ts_code": ["000001.SZ"], "limit": ["U"]})
        await dao.save_limit_list(df_in)
        # 验证传给 _save_upsert 的 DataFrame 已包含 limit_type 列，不含 limit 列
        df_passed = dao._save_upsert.call_args.args[0]
        assert "limit_type" in df_passed.columns
        assert "limit" not in df_passed.columns
        assert df_passed["limit_type"].iloc[0] == "U"

    @pytest.mark.asyncio
    async def test_save_preserves_existing_limit_type_column(self):
        """若 DataFrame 已含 limit_type 列（非 Tushare 原始字段），不应触发重命名。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        df_in = pd.DataFrame({"ts_code": ["000001.SZ"], "limit_type": ["D"]})
        await dao.save_limit_list(df_in)
        df_passed = dao._save_upsert.call_args.args[0]
        assert "limit_type" in df_passed.columns
        assert df_passed["limit_type"].iloc[0] == "D"

    @pytest.mark.asyncio
    async def test_save_handles_empty_df(self):
        """空 DataFrame 不应触发重命名逻辑，避免 KeyError。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=0)
        result = await dao.save_limit_list(pd.DataFrame())
        assert result == 0


class TestQuoteDaoSaveTopList:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_top_list(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetTopList:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_top_list(trade_date="20240615")
        assert result is not None


class TestQuoteDaoSaveMarginDaily:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_margin_daily(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoSaveSuspendD:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_suspend_d(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoSaveMoneyflow:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_moneyflow(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetMoneyflow:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_moneyflow(trade_date="20240615")
        assert isinstance(result, pd.DataFrame) and "ts_code" in result.columns


class TestQuoteDaoSaveNorthbound:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        result = await dao.save_northbound(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 1


class TestQuoteDaoGetNorthbound:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_northbound(trade_date="20240615")
        assert result is not None


class TestQuoteDaoGetLatestNorthbound:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_latest_northbound()
        assert isinstance(result, pd.DataFrame) and "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_latest_northbound()
        assert isinstance(result, pd.DataFrame)


class TestQuoteDaoGetCachedDatesForTable:
    @pytest.mark.asyncio
    async def test_valid_table(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"]}))
        result = await dao.get_cached_dates_for_table("daily_quotes")
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_invalid_table(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        result = await dao.get_cached_dates_for_table("invalid_table")
        assert result == set()

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_cached_dates_for_table("daily_quotes")
        assert result == set()


class TestQuoteDaoCheckDataExists:
    @pytest.mark.asyncio
    async def test_all_tables_have_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes"],
                        "val": [1],
                    }
                )
            )
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is True
            dao._read_db_select.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is False

    @pytest.mark.asyncio
    async def test_invalid_table(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            result = await dao.check_data_exists("20240615", tables=["invalid_table"])
            assert result is False

    @pytest.mark.asyncio
    async def test_empty_tables(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=[],
        ):
            result = await dao.check_data_exists("20240615", tables=[])
            assert result is False

    @pytest.mark.asyncio
    async def test_none_trade_date_returns_false(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
            result = await dao.check_data_exists(None, tables=["daily_quotes"])
            assert result is False

    @pytest.mark.asyncio
    async def test_sql_injection_table_rejected(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            result = await dao.check_data_exists("20240615", tables=["daily_quotes; DROP TABLE users--"])
            assert result is False

    @pytest.mark.asyncio
    async def test_partial_data_returns_false(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes", "daily_indicators"],
        ):
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes"],
                        "val": [1],
                    }
                )
            )
            result = await dao.check_data_exists("20240615", tables=["daily_quotes", "daily_indicators"])
            assert result is False

    @pytest.mark.asyncio
    async def test_uses_sqlalchemy_core_not_raw_sql(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "val": [1]}))
            await dao.check_data_exists("20240615", tables=["daily_quotes"])
            call_args = dao._read_db_select.call_args
            stmt = call_args[0][0]

            assert isinstance(stmt, sa.Select)

    @pytest.mark.asyncio
    async def test_three_tables_all_present(self):
        """3+ 表场景：验证 sa.union_all 正确构造，不触发 AttributeError。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes", "daily_indicators", "moneyflow_daily"],
        ):
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes", "daily_indicators", "moneyflow_daily"],
                        "val": [1, 1, 1],
                    }
                )
            )
            result = await dao.check_data_exists(
                "20240615",
                tables=["daily_quotes", "daily_indicators", "moneyflow_daily"],
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_three_tables_partial_missing(self):
        """3+ 表部分缺失：验证 UNION ALL 查询正确返回缺失标记。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes", "daily_indicators", "moneyflow_daily"],
        ):
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes", "daily_indicators"],
                        "val": [1, 1],
                    }
                )
            )
            result = await dao.check_data_exists(
                "20240615",
                tables=["daily_quotes", "daily_indicators", "moneyflow_daily"],
            )
            assert result is False


class TestQuoteDaoGetExpectedStockCount:
    @pytest.mark.asyncio
    async def test_trading_day(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_expected_stock_count("20240615")
        assert result == 0


class TestQuoteDaoGetBulkTableCounts:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            result = await dao.get_bulk_table_counts("invalid_table", "20240615", "20240615")
            assert result == {}

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
            result = await dao.get_bulk_table_counts("daily_quotes", "20240615", "20240615")
            assert result == {}


class TestQuoteDaoGetFieldCompleteness:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_field_completeness("20240615")
        assert result == {}


class TestQuoteDaoSaveIndexDailybasic:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_dailybasic(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_dailybasic(pd.DataFrame())
        assert result == 0


class TestQuoteDaoGetBulkExpectedStockCounts:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_bulk_expected_stock_counts("20240614", "20240615")
        assert result == {}

    @pytest.mark.asyncio
    async def test_error(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_bulk_expected_stock_counts("20240614", "20240615")
        assert result == {}

    @pytest.mark.asyncio
    async def test_sql_uses_alive_ranges_cte(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_bulk_expected_stock_counts("20240101", "20240601")
        call_args = dao._read_db.call_args
        params = call_args[0][1]
        assert params[0] == "20240101"
        assert params[1] == "20240601"

    @pytest.mark.asyncio
    async def test_date_normalization(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_bulk_expected_stock_counts("20240101", "20240601")
        sql = dao._read_db.call_args[0][0]
        assert "list_date <= $2" in sql
        assert "COALESCE(delist_date, '2099-12-31'::date) > $1" in sql


class TestQuoteDaoGetSyncQualityScore:
    @pytest.mark.asyncio
    async def test_with_string_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 15): {
                    "score": 80,
                    "expected_base": 5000,
                    "tables": {},
                    "issues": [],
                }
            }
        )
        result = await dao.get_sync_quality_score("20240615")
        assert result["score"] == 80

    @pytest.mark.asyncio
    async def test_with_date_object(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 15): {
                    "score": 90,
                    "expected_base": 5000,
                    "tables": {},
                    "issues": [],
                }
            }
        )
        result = await dao.get_sync_quality_score(datetime.date(2024, 6, 15))
        assert result["score"] == 90

    @pytest.mark.asyncio
    async def test_no_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_sync_quality_scores = AsyncMock(return_value={})
        result = await dao.get_sync_quality_score("20240615")
        assert result["score"] == 0


class TestQuoteDaoGetBulkSyncQualityScores:
    @pytest.mark.asyncio
    async def test_no_expected_bases(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={})
        with patch(
            "data.persistence.daos.quote_dao._get_effective_synced_tables",
            return_value=["daily_quotes"],
        ):
            result = await dao.get_bulk_sync_quality_scores("20240614", "20240615")
            assert result == {}

    @pytest.mark.asyncio
    async def test_with_zero_expected_base(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 0})
        dao.get_bulk_table_counts = AsyncMock(return_value={})
        with patch(
            "data.persistence.daos.quote_dao._get_effective_synced_tables",
            return_value=["daily_quotes"],
        ):
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert datetime.date(2024, 6, 15) in result
            assert result[datetime.date(2024, 6, 15)]["score"] == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes"],
            ),
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
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
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


class TestQuoteDaoCoverageGaps:
    @pytest.mark.asyncio
    async def test_get_bulk_table_counts_exception(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(side_effect=Exception("DB error"))
            result = await dao.get_bulk_table_counts("daily_quotes", "20240615", "20240615")
            assert result == {}

    @pytest.mark.asyncio
    async def test_get_cached_dates_for_table_exception(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=Exception("DB error"))
        result = await dao.get_cached_dates_for_table("daily_quotes")
        assert result == set()

    @pytest.mark.asyncio
    async def test_check_data_exists_exception_with_raise_on_error(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao._read_db_select = AsyncMock(side_effect=Exception("DB error"))
            with pytest.raises(Exception, match="DB error"):
                await dao.check_data_exists("20240615", tables=["daily_quotes"], raise_on_error=True)

    @pytest.mark.asyncio
    async def test_check_data_exists_table_not_in_metadata(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_default_synced_tables",
                return_value=["daily_quotes"],
            ),
            patch("data.persistence.daos.quote_dao.Base") as mock_base,
        ):
            mock_base.metadata.tables.get.return_value = None
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is False

    @pytest.mark.asyncio
    async def test_get_limit_list_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_limit_list(start_date="20240101", end_date="20240630")
        assert result is not None
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_suspend_d_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_suspend_d(start_date="20240101", end_date="20240630")
        assert result is not None
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_suspend_d_with_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_suspend_d(trade_date="20240615")
        assert result is not None
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_index_daily_range_chunked(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        codes = [f"{i:06d}.SH" for i in range(600)]
        result = await dao.get_index_daily_range(codes, start_date="20240101", end_date="20240630")
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores_with_fixed_expected_tables(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(
            return_value={
                datetime.date(2024, 6, 15): 4800,
            }
        )
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "index_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "index_daily": 5},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert datetime.date(2024, 6, 15) in result
            assert "index_daily" in result[datetime.date(2024, 6, 15)]["tables"]

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores_table_not_passed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 100})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "daily_indicators"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "daily_indicators": 5},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert len(result[datetime.date(2024, 6, 15)]["issues"]) > 0

    @pytest.mark.asyncio
    async def test_get_field_completeness_low_indicator_coverage(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "total": [100],
                    "indicators_available": [30],
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
        assert result["dv_ttm"] is None
        assert result["pe_ttm"] is None
        assert result["pb"] is None

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores_with_field_completeness(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={"roe": 0.8, "pe_ttm": 0.9})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            assert result[datetime.date(2024, 6, 15)]["field_completeness"] == {
                "roe": 0.8,
                "pe_ttm": 0.9,
            }


class TestQuoteDaoGetBlockTradeRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240615"]}))
        result = await dao.get_block_trade_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date >= $1" in sql
        assert "trade_date <= $2" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_block_trade_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestQuoteDaoGetTopListRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240615"]}))
        result = await dao.get_top_list_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_top_list_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestQuoteDaoGetMoneyflowRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240615"]}))
        result = await dao.get_moneyflow_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date >= $1" in sql
        assert "trade_date <= $2" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_moneyflow_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestQuoteDaoGetNorthboundRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240615"]}))
        result = await dao.get_northbound_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date >= $1" in sql
        assert "trade_date <= $2" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_northbound_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestQuoteDaoGetLimitListWithTradeDate:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "limit_type": ["U"]}))
        result = await dao.get_limit_list(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date=$1" in sql

    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_limit_list()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once()


class TestQuoteDaoGetBlockTradeNoParams:
    @pytest.mark.asyncio
    async def test_no_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_block_trade()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "WHERE 1=1" in sql


class TestQuoteDaoGetMoneyflowWithTsCode:
    @pytest.mark.asyncio
    async def test_with_ts_code(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_moneyflow(ts_code="000001.SZ")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_moneyflow()
        assert isinstance(result, pd.DataFrame)


class TestQuoteDaoGetNorthboundWithTsCode:
    @pytest.mark.asyncio
    async def test_with_ts_code(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_northbound(ts_code="000001.SZ")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_northbound()
        assert isinstance(result, pd.DataFrame)


class TestQuoteDaoGetDailyQuotesNoParams:
    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_quotes()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once()


class TestQuoteDaoGetIndexDailyWithTradeDate:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await dao.get_index_daily()
        assert isinstance(result, pd.DataFrame)


class TestQuoteDaoGetCachedTradeDatesNone:
    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_cached_trade_dates()
        assert result == set()


class TestQuoteDaoEngineDisposedErrorPropagation:
    """R5: EngineDisposedError 必须原样传播，不可被 except Exception 吞为空值返回。"""

    @pytest.mark.asyncio
    async def test_check_data_exists_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.check_data_exists("20240615", tables=["daily_quotes"], raise_on_error=False)

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_expected_stock_count("20240615")

    @pytest.mark.asyncio
    async def test_get_cached_dates_for_table_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_cached_dates_for_table("daily_quotes")

    @pytest.mark.asyncio
    async def test_get_bulk_table_counts_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_bulk_table_counts("daily_quotes", "20240101", "20240131")

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_bulk_expected_stock_counts("20240101", "20240131")

    @pytest.mark.asyncio
    async def test_get_field_completeness_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_field_completeness("20240615")

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores_propagates_engine_disposed(self):
        """get_bulk_sync_quality_scores 内层 field_completeness 也必须传播 EngineDisposedError。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch("data.persistence.daos.quote_dao._get_effective_synced_tables", return_value=[]):
            with patch("utils.config_handler.ConfigHandler.get_sync_integrity_config") as mock_cfg:
                mock_cfg.return_value = {
                    "quotes_tolerance_ratio": 0.9,
                    "indicators_tolerance_ratio": 0.9,
                    "moneyflow_tolerance_ratio": 0.9,
                    "quality_weights": {},
                }
                dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 1, 15): 100})
                dao.get_bulk_table_counts = AsyncMock(return_value={})
                dao.get_field_completeness = AsyncMock(side_effect=EngineDisposedError("disposed"))
                with pytest.raises(EngineDisposedError):
                    await dao.get_bulk_sync_quality_scores("20240115", "20240115")

    @pytest.mark.asyncio
    async def test_check_data_exists_database_query_error_still_degrades(self):
        """普通 DatabaseQueryError 在 raise_on_error=False 时仍返回 False（不破坏原行为）。"""
        from data.persistence.daos.base_dao import DatabaseQueryError

        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=DatabaseQueryError("db error"))
        result = await dao.check_data_exists("20240615", tables=["daily_quotes"], raise_on_error=False)
        assert result is False
