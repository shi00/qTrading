# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import logging
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

    @pytest.mark.asyncio
    async def test_code_list_branch_sorts(self):
        """证伪 D3-M5: ts_code_list 分支 concat 后必须按 (ts_code, trade_date) 升序返回。

        直查分支由 SQL ``ORDER BY`` 保证（见 TestQuoteDaoGetDailyQuotesNoParams.test_no_params），
        分块分支由方法内 concat 后 sort_values 保证——两条路径行序一致为 DAO 契约（D3-M3）。
        此处 mock chunked_in_query 返回乱序数据，断言 get_daily_quotes 归一为契约行序。
        """
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.chunked_in_query = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000002.SZ", "000001.SZ", "000002.SZ", "000001.SZ"],
                    "trade_date": ["20240105", "20240103", "20240102", "20240102"],
                }
            )
        )
        result = await dao.get_daily_quotes(ts_code_list=["000001.SZ", "000002.SZ"])
        assert result is not None
        # 先 ts_code 升序；同 ts_code 内 trade_date 升序
        assert list(result["ts_code"]) == ["000001.SZ", "000001.SZ", "000002.SZ", "000002.SZ"]
        assert list(result["trade_date"]) == ["20240102", "20240103", "20240102", "20240105"]


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("table", ["top_inst", "stk_limit"])
    async def test_review09_24_major01_tables_registered(self, table, caplog):
        """review09-24 dim05 MAJOR-01：新增同步表不得再走「Invalid table name rejected」分支。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"]}))
        with caplog.at_level(logging.WARNING, logger="data.persistence.daos.quote_dao"):
            result = await dao.get_cached_dates_for_table(table)
        assert "Invalid table name rejected" not in caplog.text
        assert result == {"20240615"}


class TestQuoteDaoCheckDataExists:
    @pytest.mark.asyncio
    async def test_all_tables_have_data(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes"],
                        "cnt": [5400],
                    }
                )
            )
            result = await dao.check_data_exists("20240615", tables=["daily_quotes"])
            assert result is True
            call_args = dao._read_db_select.call_args
            assert isinstance(call_args[0][0], sa.Select)
            assert call_args[1] == {"suppress_errors": True}

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
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes"],
                        "cnt": [5400],
                    }
                )
            )
            result = await dao.check_data_exists("20240615", tables=["daily_quotes", "daily_indicators"])
            assert result is False

    @pytest.mark.asyncio
    async def test_review09_24_major01_stk_limit_visible_to_existence_check(self):
        """review09-24 dim05 MAJOR-01：stk_limit 缺数据时存在性检查必须可见并返回 False（不再被白名单静默过滤）。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.check_data_exists("20240615", tables=["daily_quotes", "stk_limit"])
        assert result is False

        dao.get_expected_stock_count = AsyncMock(return_value=5400)
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame({"tbl": ["daily_quotes", "stk_limit"], "cnt": [5400, 5400]})
        )
        result = await dao.check_data_exists("20240615", tables=["daily_quotes", "stk_limit"])
        assert result is True

    def test_review09_24_major01_default_synced_tables_include_new_tables(self):
        """review09-24 dim05 MAJOR-01：白名单补齐后，新表须出现在默认同步表集合中。"""
        from data.persistence.daos.quote_dao import _get_default_synced_tables

        default_tables = set(_get_default_synced_tables())
        assert {"stk_limit", "top_inst"} <= default_tables

    @pytest.mark.asyncio
    async def test_uses_sqlalchemy_core_not_raw_sql(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "cnt": [5400]}))
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
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes", "daily_indicators", "moneyflow_daily"],
                        "cnt": [5400, 5400, 5400],
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
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "tbl": ["daily_quotes", "daily_indicators"],
                        "cnt": [5400, 5400],
                    }
                )
            )
            result = await dao.check_data_exists(
                "20240615",
                tables=["daily_quotes", "daily_indicators", "moneyflow_daily"],
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_review09_24_major02_truncated_dense_table_returns_false(self):
        """review09-24 dim05 MAJOR-02 报告验证方式：daily_quotes 当日仅 200 行、
        expected_base=5400 → 判定为未完整同步。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "cnt": [200]}))
            assert await dao.check_data_exists("20240615", tables=["daily_quotes"]) is False

    @pytest.mark.asyncio
    async def test_review09_24_major02_dense_threshold_boundary(self):
        """稠密表阈值边界：int(5400*0.95)=5130，5129 未达标、5130 达标。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "cnt": [5129]}))
            assert await dao.check_data_exists("20240615", tables=["daily_quotes"]) is False
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "cnt": [5130]}))
            assert await dao.check_data_exists("20240615", tables=["daily_quotes"]) is True

    @pytest.mark.asyncio
    async def test_review09_24_major02_sparse_table_keeps_existence_only(self):
        """结构性覆盖子集表（margin_daily）保留存在性判定：仅 1 行即通过，且不得查询理论股票数。

        get_expected_stock_count 被 mock 为 0——若实现误入稠密分支会返回 False，
        因此断言 True 同时证明该分支未被触发。
        """
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["margin_daily"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=0)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["margin_daily"], "cnt": [1]}))
            assert await dao.check_data_exists("20240615", tables=["margin_daily"]) is True
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
            assert await dao.check_data_exists("20240615", tables=["margin_daily"]) is False

    @pytest.mark.asyncio
    async def test_review09_24_major02_low_frequency_table_keeps_existence_only(self):
        """低频事件表（limit_list）保留存在性判定，不因当日事件稀少而被判未完整。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["limit_list"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=0)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["limit_list"], "cnt": [1]}))
            assert await dao.check_data_exists("20240615", tables=["limit_list"]) is True

    @pytest.mark.asyncio
    async def test_review09_24_major02_unknown_expected_base_returns_false(self):
        """理论股票数不可确定（0：非交易日 / stock_basic 为空 / 查询失败）时保守判未完整，
        不得因当日行数充足而跳过整日。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with patch(
            "data.persistence.daos.quote_dao._get_default_synced_tables",
            return_value=["daily_quotes"],
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=0)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["daily_quotes"], "cnt": [9999]}))
            assert await dao.check_data_exists("20240615", tables=["daily_quotes"]) is False

    @pytest.mark.asyncio
    async def test_review09_24_major02_index_daily_requires_all_indices(self):
        """index_daily 期望行数 = len(indices_to_sync())（DS-01 口径），只落部分指数判未完整。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_default_synced_tables",
                return_value=["index_daily"],
            ),
            patch(
                "data.persistence.daos.quote_dao.indices_to_sync",
                return_value=["000001.SH", "399001.SZ", "000300.SH"],
            ),
        ):
            dao.get_expected_stock_count = AsyncMock(return_value=5400)
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["index_daily"], "cnt": [2]}))
            assert await dao.check_data_exists("20240615", tables=["index_daily"]) is False
            dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"tbl": ["index_daily"], "cnt": [3]}))
            assert await dao.check_data_exists("20240615", tables=["index_daily"]) is True


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
    async def test_sql_orders_financial_by_end_date(self):
        """DAT-03: get_field_completeness 财务子查询必须按 end_date DESC 取最新报告期，禁止回退。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        mock_read = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "total": [2],
                    "indicators_available": [0],
                    "roe_count": [2],
                    "or_yoy_count": [1],
                    "netprofit_yoy_count": [1],
                    "dv_ttm_count": [0],
                    "pe_ttm_count": [0],
                    "pb_count": [0],
                    "debt_to_assets_count": [1],
                }
            )
        )
        dao._read_db = mock_read
        await dao.get_field_completeness("20241120")
        sql = mock_read.call_args[0][0]
        assert "ORDER BY end_date DESC, ann_date DESC" in sql
        assert "ORDER BY ann_date DESC, end_date DESC" not in sql

    @pytest.mark.asyncio
    async def test_error(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_field_completeness("20240615")
        assert result == {}

    @pytest.mark.asyncio
    async def test_sql_includes_ann_date_not_null(self):
        """DAT-06: 字段完整度财务子查询必须显式 ann_date IS NOT NULL，防回退。"""
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
        await dao.get_field_completeness("20240615")
        sql = dao._read_db.call_args[0][0]
        assert "ann_date IS NOT NULL AND ann_date <=" in sql


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
        assert params[0] == datetime.date(2024, 1, 1)
        assert params[1] == datetime.date(2024, 6, 1)

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
        # DAT-01: 存活判定经 stock_alive_condition() 唯一正本渲染，区间 join 结构保留
        assert "COALESCE(delist_date, '2099-12-31'::date) AS end_date" in sql
        assert "delist_date > $1" in sql


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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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

    @pytest.mark.asyncio
    async def test_attempted_upto_exempts_zero_count_sparse_table(self):
        """D1-1：水位覆盖某日且稀疏表当日 count==0 → 判为"已尝试合法空"，exempt 不计分。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): 4800 if table == "daily_quotes" else 0}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "moneyflow_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "moneyflow_daily": 5},
            }
            result = await dao.get_bulk_sync_quality_scores(
                "20240615",
                "20240615",
                attempted_upto={"moneyflow_daily": "20240615"},
            )
            mf = result[datetime.date(2024, 6, 15)]["tables"]["moneyflow_daily"]
            assert mf.get("exempt") is True
            assert result[datetime.date(2024, 6, 15)]["score"] > 0

    @pytest.mark.asyncio
    async def test_attempted_upto_not_exempt_when_count_positive(self):
        """D1-1：水位存在但稀疏表当日 count>0 → 仍正常计分（不豁免）。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): 4800 if table == "daily_quotes" else 100}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "moneyflow_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "moneyflow_daily": 5},
            }
            result = await dao.get_bulk_sync_quality_scores(
                "20240615",
                "20240615",
                attempted_upto={"moneyflow_daily": "20240615"},
            )
            mf = result[datetime.date(2024, 6, 15)]["tables"]["moneyflow_daily"]
            assert mf.get("exempt") is not True
            assert mf["ratio"] is not None
            assert mf["count"] == 100

    @pytest.mark.asyncio
    async def test_attempted_upto_beyond_coverage_still_scores_zero(self):
        """D1-1：水位未覆盖该日（date > attempted_upto）→ 不作为豁免，按正常 count==0 计分拉低。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): 4800 if table == "daily_quotes" else 0}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "moneyflow_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "moneyflow_daily": 5},
            }
            result = await dao.get_bulk_sync_quality_scores(
                "20240615",
                "20240615",
                attempted_upto={"moneyflow_daily": "20240614"},
            )
            mf = result[datetime.date(2024, 6, 15)]["tables"]["moneyflow_daily"]
            assert mf.get("exempt") is not True
            assert mf["ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_review09_24_major01_new_tables_registered_in_scoring(self):
        """review09-24 dim05 MAJOR-01：stk_limit 计入加权评分（非豁免），top_inst 走低频豁免。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): 4800 if table == "daily_quotes" else 0}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "stk_limit", "top_inst"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quotes_tolerance_ratio": 0.90,
                "indicators_tolerance_ratio": 0.80,
                "moneyflow_tolerance_ratio": 0.70,
                "quality_weights": {"daily_quotes": 10, "stk_limit": 5},
            }
            result = await dao.get_bulk_sync_quality_scores("20240615", "20240615")
            day = result[datetime.date(2024, 6, 15)]

            stk_limit = day["tables"]["stk_limit"]
            assert stk_limit.get("exempt") is not True
            # CRITICAL-01：期望行数以理论股票数 expected_base(5000) 为分母，
            # 而非当日实际 quotes_count(4800)，避免整批残缺时完整性退化为一致性。
            assert stk_limit["expected"] == int(5000 * 0.90)
            assert stk_limit["passed"] is False
            assert any(issue.startswith("stk_limit:") for issue in day["issues"])

            top_inst = day["tables"]["top_inst"]
            assert top_inst.get("exempt") is True
            assert top_inst["ratio"] is None

    @pytest.mark.asyncio
    async def test_review09_24_major04_stale_stock_basic_adds_issue(self):
        """MAJOR-04：stock_basic 陈旧（分母不可信）时须在 issues 中显式告警。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 5, 1))
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
            day = result[datetime.date(2024, 6, 15)]
            assert day["tables"]["daily_quotes"]["passed"] is True
            assert day["issues"] == [
                "stock_basic 最近更新 2024-05-01，早于交易日 2024-06-15 超过 7 天，理论股票数可能偏低"
            ]
            # warn-only：告警不调低 score（stock_basic 不在 SYNCED_TABLES，降分只会引发无效重抓）。
            # 96 = 覆盖率 0.96(4800/5000) × 权重 10 / 权重 10；陈旧告警不额外扣分。
            assert day["score"] == 96

    @pytest.mark.parametrize(
        ("updated", "expect_stale"),
        [
            (datetime.date(2024, 6, 8), False),  # 恰好 7 天（== 阈值）不告警
            (datetime.date(2024, 6, 7), True),  # 超过 7 天告警
        ],
    )
    @pytest.mark.asyncio
    async def test_review09_24_major04_staleness_threshold_boundary(self, updated, expect_stale):
        """MAJOR-04：陈旧判据为严格大于 `_STOCK_BASIC_STALENESS_MAX_DAYS`(7) 天。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=updated)
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
            stale_issues = [
                issue
                for issue in result[datetime.date(2024, 6, 15)]["issues"]
                if issue.startswith("stock_basic 最近更新")
            ]
            assert bool(stale_issues) is expect_stale

    @pytest.mark.asyncio
    async def test_review09_24_major04_fresh_stock_basic_no_staleness_issue(self):
        """MAJOR-04：stock_basic 新鲜（滞后不超阈值）时不产生陈旧告警。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
            assert result[datetime.date(2024, 6, 15)]["issues"] == []

    @pytest.mark.asyncio
    async def test_review09_24_major04_future_updated_no_staleness_issue(self):
        """MAJOR-04：stock_basic 更新时刻晚于交易日（时钟偏差/历史回填）时不得误报陈旧。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 20))
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
            assert result[datetime.date(2024, 6, 15)]["issues"] == []

    @pytest.mark.asyncio
    async def test_review09_24_major04_unknown_freshness_adds_issue(self):
        """MAJOR-04 / R21：无法判定 stock_basic 新鲜度（None）时不得静默放行，须显式告警。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 4800})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=None)
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
            assert result[datetime.date(2024, 6, 15)]["issues"] == [
                "无法确认 stock_basic 新鲜度（查询失败或表为空），理论股票数可能不准"
            ]

    @pytest.mark.asyncio
    async def test_review09_24_major04_quotes_exceed_expected_base_warns(self):
        """MAJOR-04：daily_quotes 行数超过理论股票数（分母漏了标的）时须告警，避免被截顶掩盖。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5000})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5200})
        dao.get_field_completeness = AsyncMock(return_value={})
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
            day = result[datetime.date(2024, 6, 15)]
            assert day["tables"]["daily_quotes"]["ratio"] == 1.0
            assert day["issues"] == ["daily_quotes 行数(5200)超过理论股票数(5000)，stock_basic 可能陈旧"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("quotes_ratio_value", [0.30, 0.50, 0.94, 0.95])
    async def test_review09_24_critical01_daily_quotes_ratio_gates_resync(self, quotes_ratio_value):
        """review09-24 dim05 CRITICAL-01：daily_quotes 覆盖率决定是否被判为低质量而重同步。

        修复前其余表以缩水后的 quotes_count 为参照（自我参照分母），daily_quotes 覆盖约 50%
        时（其余表满额）总分仍可达 ~82（≥80）而被断点续传永久跳过。修复后 daily_quotes 未达
        quotes_tolerance_ratio(0.95) 即一票否决，得分封顶到阈值以下强制重同步。
        其余表按理论股票数满额写入，以隔离 daily_quotes 单项对总分的影响。
        注：r=0.30/0.95 为边界用例（修复前后结论一致，仅作边界锁定）；真正区分修复效果的是
        r=0.50/0.94（修复前 ≥80、修复后 <80）。
        """
        expected_base = 5400
        quotes_count = int(expected_base * quotes_ratio_value)
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): quotes_count if table == "daily_quotes" else expected_base}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): expected_base})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "daily_indicators", "moneyflow_daily", "margin_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quality_threshold": 80,
                "quotes_tolerance_ratio": 0.95,
                "indicators_tolerance_ratio": 0.90,
                "moneyflow_tolerance_ratio": 0.80,
                "quality_weights": {
                    "daily_quotes": 30,
                    "daily_indicators": 25,
                    "moneyflow_daily": 20,
                    "margin_daily": 10,
                },
            }
            day = (await dao.get_bulk_sync_quality_scores("20240615", "20240615"))[datetime.date(2024, 6, 15)]

        assert day["tables"]["daily_quotes"]["passed"] is (quotes_ratio_value >= 0.95)
        if quotes_ratio_value >= 0.95:
            assert day["score"] >= 80
        else:
            assert day["score"] < 80
            assert any(issue.startswith("daily_quotes:") for issue in day["issues"])

    @pytest.mark.asyncio
    async def test_review09_24_critical01_truncated_batch_not_self_referential(self):
        """整批残缺（daily_quotes 与其余表同比例缩水）不得因"各表彼此一致"被判合格。

        修复前其余表以 quotes_count=2700 为参照 → 各表 ratio≈1.0 → 总分约 82 ≥ 80，残缺日被跳过。
        修复后参照改为 expected_base=5400 → 其余表 ratio 显著 <1，叠加一票否决 → 得分 <80 强制重同步。
        """
        expected_base = 5400
        dao = QuoteDao(MagicMock(spec=AsyncEngine))

        async def _counts(table, start, end):
            return {datetime.date(2024, 6, 15): 2700 if table != "margin_daily" else 2500}

        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): expected_base})
        dao.get_bulk_table_counts = AsyncMock(side_effect=_counts)
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_quotes", "daily_indicators", "moneyflow_daily", "margin_daily"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quality_threshold": 80,
                "quotes_tolerance_ratio": 0.95,
                "indicators_tolerance_ratio": 0.90,
                "moneyflow_tolerance_ratio": 0.80,
                "quality_weights": {
                    "daily_quotes": 30,
                    "daily_indicators": 25,
                    "moneyflow_daily": 20,
                    "margin_daily": 10,
                },
            }
            day = (await dao.get_bulk_sync_quality_scores("20240615", "20240615"))[datetime.date(2024, 6, 15)]

        assert day["tables"]["daily_quotes"]["passed"] is False
        assert day["score"] < 80
        assert any(issue.startswith("daily_quotes:") for issue in day["issues"])

    @pytest.mark.asyncio
    async def test_review09_24_critical01_veto_skipped_when_daily_quotes_excluded(self):
        """本次评估显式排除 daily_quotes 时不得触发一票否决（避免误否决只评部分表的调用）。"""
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.get_bulk_expected_stock_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5400})
        dao.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 6, 15): 5400})
        dao.get_field_completeness = AsyncMock(return_value={})
        with (
            patch(
                "data.persistence.daos.quote_dao._get_effective_synced_tables",
                return_value=["daily_indicators"],
            ),
            patch("utils.config_handler.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_sync_integrity_config.return_value = {
                "quality_threshold": 80,
                "quotes_tolerance_ratio": 0.95,
                "indicators_tolerance_ratio": 0.90,
                "moneyflow_tolerance_ratio": 0.80,
                "quality_weights": {"daily_indicators": 25},
            }
            day = (await dao.get_bulk_sync_quality_scores("20240615", "20240615"))[datetime.date(2024, 6, 15)]

        # daily_quotes 不在 evaluated tables 内（其 count=0 源自未查询），不参与一票否决：
        # daily_indicators 满额 → 得分 ≥80。若误加否决会被封顶到 79 而导致该断言失败。
        assert day["score"] >= 80


class TestQuoteDaoStockBasicLatestUpdatedDate:
    """MAJOR-04：stock_basic 新鲜度判据查询方法的边界与异常传播。"""

    def _make_dao(self, read_result=None, side_effect=None):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        if side_effect is not None:
            dao._read_db = AsyncMock(side_effect=side_effect)
        else:
            dao._read_db = AsyncMock(return_value=read_result)
        return dao

    @pytest.mark.asyncio
    async def test_timestamp_value_returns_date(self):
        dao = self._make_dao(read_result=pd.DataFrame({"latest_updated": [pd.Timestamp("2024-06-15 08:30:00")]}))
        assert await dao.get_stock_basic_latest_updated_date() == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_plain_date_value_returns_date(self):
        dao = self._make_dao(read_result=pd.DataFrame({"latest_updated": [datetime.date(2024, 6, 15)]}))
        assert await dao.get_stock_basic_latest_updated_date() == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_nat_value_returns_none(self):
        dao = self._make_dao(read_result=pd.DataFrame({"latest_updated": [pd.NaT]}))
        assert await dao.get_stock_basic_latest_updated_date() is None

    @pytest.mark.asyncio
    async def test_empty_result_returns_none(self):
        dao = self._make_dao(read_result=pd.DataFrame())
        assert await dao.get_stock_basic_latest_updated_date() is None

    @pytest.mark.asyncio
    async def test_query_error_returns_none(self):
        dao = self._make_dao(side_effect=Exception("db error"))
        assert await dao.get_stock_basic_latest_updated_date() is None

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed(self):
        dao = self._make_dao(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_stock_basic_latest_updated_date()

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self):
        """R2：asyncio.CancelledError 不得被吞，必须向上传播以配合优雅停机。"""
        cancelled = asyncio.CancelledError()
        dao = self._make_dao(side_effect=cancelled)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await dao.get_stock_basic_latest_updated_date()
        assert exc_info.value is cancelled


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
        dao._read_db.assert_called_once_with(
            "SELECT ts_code, trade_date, limit_type, name, close, pct_chg FROM limit_list WHERE 1=1 AND trade_date>=$1 AND trade_date<=$2 ORDER BY trade_date, ts_code",
            [datetime.date(2024, 1, 1), datetime.date(2024, 6, 30)],
        )

    @pytest.mark.asyncio
    async def test_get_suspend_d_with_date_range(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_suspend_d(start_date="20240101", end_date="20240630")
        assert result is not None
        dao._read_db.assert_called_once_with(
            "SELECT ts_code, trade_date, suspend_timing, suspend_type FROM suspend_d WHERE 1=1 AND trade_date>=$1 AND trade_date<=$2 ORDER BY trade_date, ts_code",
            [datetime.date(2024, 1, 1), datetime.date(2024, 6, 30)],
        )

    @pytest.mark.asyncio
    async def test_get_suspend_d_with_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_suspend_d(trade_date="20240615")
        assert result is not None
        dao._read_db.assert_called_once_with(
            "SELECT ts_code, trade_date, suspend_timing, suspend_type FROM suspend_d WHERE trade_date=$1",
            [datetime.date(2024, 6, 15)],
        )

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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
        dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
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
        assert dao._read_db.call_count == 1
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
        dao._read_db.assert_called_once_with(
            "SELECT * FROM top_list WHERE trade_date >= $1 AND trade_date <= $2",
            [datetime.date(2024, 6, 1), datetime.date(2024, 6, 15)],
        )

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
        assert dao._read_db.call_count == 1
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
        assert dao._read_db.call_count == 1
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
        assert dao._read_db.call_count == 1
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date=$1" in sql

    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_limit_list()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once_with(
            "SELECT ts_code, trade_date, limit_type, name, close, pct_chg FROM limit_list WHERE 1=1 ORDER BY trade_date, ts_code",
            [],
        )


class TestQuoteDaoGetBlockTradeNoParams:
    @pytest.mark.asyncio
    async def test_no_trade_date(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_block_trade()
        assert isinstance(result, pd.DataFrame)
        assert dao._read_db.call_count == 1
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
        dao._read_db.assert_called_once_with(
            "SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor FROM daily_quotes WHERE 1=1 ORDER BY ts_code, trade_date",
            [],
            suppress_errors=True,
        )


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
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.check_data_exists("20240615", tables=["daily_quotes"], raise_on_error=False)

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_expected_stock_count("20240615")

    @pytest.mark.asyncio
    async def test_get_cached_dates_for_table_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_cached_dates_for_table("daily_quotes")

    @pytest.mark.asyncio
    async def test_get_bulk_table_counts_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_bulk_table_counts("daily_quotes", "20240101", "20240131")

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_bulk_expected_stock_counts("20240101", "20240131")

    @pytest.mark.asyncio
    async def test_get_field_completeness_propagates_engine_disposed(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
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
                dao.get_stock_basic_latest_updated_date = AsyncMock(return_value=datetime.date(2024, 1, 15))
                dao.get_field_completeness = AsyncMock(side_effect=EngineDisposedError("disposed"))
                with pytest.raises(EngineDisposedError, match="disposed"):
                    await dao.get_bulk_sync_quality_scores("20240115", "20240115")

    @pytest.mark.asyncio
    async def test_check_data_exists_database_query_error_still_degrades(self):
        """普通 DatabaseQueryError 在 raise_on_error=False 时仍返回 False（不破坏原行为）。"""
        from data.persistence.daos.base_dao import DatabaseQueryError

        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=DatabaseQueryError("db error"))
        result = await dao.check_data_exists("20240615", tables=["daily_quotes"], raise_on_error=False)
        assert result is False


class TestQuoteDaoCrossValidation:
    """DAT-12: 跨表/跨字段一致性校验 DAO 方法（生产健康检查，外键替代）。"""

    def _make_dao(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [0]}))
        return dao

    @pytest.mark.asyncio
    async def test_count_orphan_ts_codes_with_violations(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [3]}))
        assert await dao.count_orphan_ts_codes() == 3
        # 参数化绑定（R4）：查询含 $1 窗口占位符，窗口值经 params 传递而非拼接
        sql = dao._read_db.call_args.args[0]
        assert "$1" in sql
        assert len(dao._read_db.call_args.args[1]) == 1

    @pytest.mark.asyncio
    async def test_count_orphan_ts_codes_empty(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": []}))
        assert await dao.count_orphan_ts_codes() == 0

    @pytest.mark.asyncio
    async def test_count_orphan_ts_codes_none(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.count_orphan_ts_codes() == 0

    @pytest.mark.asyncio
    async def test_count_price_range_violations(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [5]}))
        assert await dao.count_price_range_violations() == 5

    @pytest.mark.asyncio
    async def test_count_moneyflow_net_mismatch(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [7]}))
        assert await dao.count_moneyflow_net_mismatch() == 7
        # 容差作为 $2 参数绑定（R4），非拼接进 SQL
        args = dao._read_db.call_args.args
        assert len(args[1]) == 2

    @pytest.mark.asyncio
    async def test_count_adj_factor_monotonic_violations(self):
        dao = self._make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [2]}))
        assert await dao.count_adj_factor_monotonic_violations() == 2

    @pytest.mark.asyncio
    async def test_get_index_daily_coverage_summary(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        df = pd.DataFrame({"ts_code": ["000001.SH"], "row_count": [100], "latest_trade_date": ["20240614"]})
        dao._read_db = AsyncMock(return_value=df)
        result = await dao.get_index_daily_coverage_summary()
        assert "ts_code" in result.columns and "latest_trade_date" in result.columns


class TestQuoteDaoGetLatestQuotesBulk:
    """UX-09 MAJOR-04: 关注列表按 ts_code 批量取最新行情（DISTINCT ON，规避 N+1）。"""

    @pytest.mark.asyncio
    async def test_empty_codes_returns_typed_empty_df(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao.chunked_in_query = AsyncMock()
        result = await dao.get_latest_quotes_bulk([])
        assert result.empty
        assert list(result.columns) == ["ts_code", "trade_date", "close", "pct_chg", "vol", "amount"]
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_distinct_on_query_shape(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [12.0], "pct_chg": [1.0]})
        dao.chunked_in_query = AsyncMock(return_value=df)
        result = await dao.get_latest_quotes_bulk(["000001.SZ", "600000.SH"])
        assert not result.empty
        sql_template = dao.chunked_in_query.call_args.args[1]
        assert "DISTINCT ON (ts_code)" in sql_template
        assert "ORDER BY ts_code, trade_date DESC" in sql_template
        assert "{placeholders}" in sql_template
        assert dao.chunked_in_query.call_args.args[2] == ["000001.SZ", "600000.SH"]


class TestQuoteDaoGetRecentQuotes:
    """UX-09 MAJOR-04: 详情 K 线按代码取近 N 日行情，归一为 trade_date 升序。"""

    @pytest.mark.asyncio
    async def test_sorts_ascending_and_truncates(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": ["000001.SZ"] * 3, "trade_date": ["20240103", "20240101", "20240102"]}
            )
        )
        result = await dao.get_recent_quotes("000001.SZ", days=365)
        assert list(result["trade_date"]) == ["20240101", "20240102", "20240103"]

    @pytest.mark.asyncio
    async def test_empty_returns_empty_df(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_recent_quotes("000001.SZ")
        assert result.empty
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_none_returns_empty_df(self):
        dao = QuoteDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=None)
        result = await dao.get_recent_quotes("000001.SZ")
        assert isinstance(result, pd.DataFrame) and result.empty
