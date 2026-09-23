# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import datetime

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import sqlalchemy as sa

from data.persistence.daos.stock_dao import StockDao

pytestmark = pytest.mark.unit


def _make_dao():
    dao = StockDao(MagicMock())
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=None)
    dao._write_db = AsyncMock(return_value=0)
    dao._get_maintenance_event = MagicMock()
    dao._get_maintenance_event.return_value.wait = AsyncMock()
    dao.engine = MagicMock()
    dao._prepare_data_params = MagicMock(return_value=[["val1"]])
    dao._quote_columns = MagicMock(return_value="ts_code, concept_id, concept_name, updated_at")
    return dao


class TestSaveStockBasic:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = _make_dao()
        assert await dao.save_stock_basic(None) == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = _make_dao()
        assert await dao.save_stock_basic(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        result = await dao.save_stock_basic(df)
        assert result == 5


class TestGetStockBasic:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_stock_basic()
        assert len(result) == 1


class TestGetTsCodeMap:
    """review08 D2: 构建 symbol → ts_code 权威映射，供概念同步取代前缀猜测。"""

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame({"symbol": ["000001", "600000"], "ts_code": ["000001.SZ", "600000.SH"]}),
        )
        assert await dao.get_ts_code_map() == {"000001": "000001.SZ", "600000": "600000.SH"}

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock(return_value=None)
        assert await dao.get_ts_code_map() == {}

    @pytest.mark.asyncio
    async def test_skips_missing_symbol(self):
        dao = _make_dao()
        # symbol 为 None/空的行应被跳过，不产出 key
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {"symbol": [None, "600000"], "ts_code": ["000001.SZ", "600000.SH"]},
            ),
        )
        assert await dao.get_ts_code_map() == {"600000": "600000.SH"}

    @pytest.mark.asyncio
    async def test_db_error_propagates(self):
        """review08-D2 复核：DB 读取失败必须传播，不可吞成空映射。

        默认 ``_read_db_select(suppress_errors=True)`` 会把 DB 故障吞成空映射，
        概念同步将全部代码误标为 "not in stock_basic" 并误报 SUCCESS（R21 缺失值
        伪装）。显式 ``suppress_errors=False`` 后异常必须传播到调用方。
        """
        from data.persistence.daos.base_dao import DatabaseQueryError

        dao = _make_dao()
        dao._read_db_select = AsyncMock(side_effect=DatabaseQueryError("db down"))
        with pytest.raises(DatabaseQueryError, match="db down"):
            await dao.get_ts_code_map()
        # 精确断言：必须以 suppress_errors=False 调用，防回归为默认吞错
        assert dao._read_db_select.call_args.kwargs["suppress_errors"] is False


class TestGetActiveStockCount:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [100]}))
        assert await dao.get_active_stock_count() == 100

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_active_stock_count() == 0


class TestSaveTradeCal:
    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"cal_date": ["20240615"], "is_open": [1]})
        result = await dao.save_trade_cal(df)
        assert result == 5


class TestGetTradeCal:
    @pytest.mark.asyncio
    async def test_no_filters(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240615"]}))
        result = await dao.get_trade_cal()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_start_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_trade_cal(start_date="20240101")
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_with_all_filters(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_trade_cal(start_date="20240101", end_date="20240630", is_open="1")
        assert isinstance(result, pd.DataFrame)


class TestGetTradeCalRange:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"min_d": ["20200101"], "max_d": ["20241231"]}))
        assert await dao.get_trade_cal_range() == ("20200101", "20241231")

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_trade_cal_range() == (None, None)


class TestCountTradeDays:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [120]}))
        assert await dao.count_trade_days("20240101", "20240630") == 120

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.count_trade_days("20240101", "20240630") == 0


class TestGetStartDateByTradeDays:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240103", "20240102", "20240101"]}))
        result = await dao.get_start_date_by_trade_days("20240103", 3)
        assert result == "20240101"

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240101"]}))
        result = await dao.get_start_date_by_trade_days("20240103", 3)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_start_date_by_trade_days("20240103", 3)
        assert result is None


class TestCountExpectedRows:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"expected": [5000]}))
        assert await dao.count_expected_rows("20240101", "20240630") == 5000

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.count_expected_rows("20240101", "20240630") == 1


class TestSaveConcepts:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = _make_dao()
        assert await dao.save_concepts(None) == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = _make_dao()
        assert await dao.save_concepts(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["C1"], "concept_name": ["概念1"]})
        result = await dao.save_concepts(df)
        assert result == 5


class TestOverwriteConcepts:
    @pytest.mark.asyncio
    async def test_none_df(self):
        dao = _make_dao()
        assert await dao.overwrite_concepts(None) == 0

    @pytest.mark.asyncio
    async def test_empty_df(self):
        dao = _make_dao()
        assert await dao.overwrite_concepts(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["C1"], "concept_name": ["概念1"]})
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=[["val1"]])
            result = await dao.overwrite_concepts(df)
            assert result == 1

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["C1"], "concept_name": ["概念1"]})
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(side_effect=Exception("db error"))
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=[["val1"]])
            with pytest.raises(Exception, match="db error"):
                await dao.overwrite_concepts(df)


class TestClearAllAiLlmConcepts:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=10)
        result = await dao.clear_all_ai_llm_concepts()
        assert result == 10


class TestGetStocksWithoutAiConcepts:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                }
            )
        )
        result = await dao.get_stocks_without_ai_concepts(batch_size=10)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_stocks_without_ai_concepts(batch_size=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_with_exclude(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "name": ["平安银行", "万科A"],
                }
            )
        )
        result = await dao.get_stocks_without_ai_concepts(batch_size=10, exclude_codes=["000001.SZ"])
        assert len(result) == 1


class TestGetConcepts:
    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_concepts()
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_concepts()
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "concept_name": ["概念1", "概念2"],
                }
            )
        )
        result = await dao.get_concepts()
        assert "000001.SZ" in result
        assert len(result["000001.SZ"]) == 2
        # review08-D3：全表分支必须带 NOT LIKE 过滤 LIMIT_ 前缀（R4 参数化）
        sql_arg = dao._read_db.call_args.args[0]
        assert "concept_id NOT LIKE $1" in sql_arg
        assert dao._read_db.call_args.args[1] == [f"{StockDao.LIMIT_CONCEPT_PREFIX}%"]

    @pytest.mark.asyncio
    async def test_filters_limit_concepts_sql(self):
        """review08-D3：单码分支 SQL 带 NOT LIKE 排除 LIMIT_ 前缀（R4 参数化）。

        前缀过滤在 DB 层执行（SQL 层），此处验证 SQL 与参数正确下发。
        """
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_name": ["银行"],  # 仅返回真实概念（模拟 DB 过滤后结果）
                }
            )
        )
        result = await dao.get_concepts(ts_codes=["000001.SZ"])
        assert "000001.SZ" in result
        # 单码分支 SQL 带 NOT LIKE $2
        sql_arg = dao._read_db.call_args.args[0]
        assert "concept_id NOT LIKE $2" in sql_arg
        assert dao._read_db.call_args.args[1] == ["000001.SZ", f"{StockDao.LIMIT_CONCEPT_PREFIX}%"]

    @pytest.mark.asyncio
    async def test_keeps_real_concepts(self):
        """review08-D3：单码分支过滤 LIMIT_ 后，真实概念名仍保留。"""
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "concept_name": [
                        "银行",
                        "深圳本地股",
                    ],  # 真实概念名（EM_/AI_LLM_ 前缀在 concept_id，查询只返回 name）
                }
            )
        )
        result = await dao.get_concepts(ts_codes=["000001.SZ"])
        assert "000001.SZ" in result
        assert result["000001.SZ"] == ["银行", "深圳本地股"]

    @pytest.mark.asyncio
    async def test_filters_placeholder_concept(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_name": ["已扫描无强概念"],
                }
            )
        )
        result = await dao.get_concepts()
        assert "000001.SZ" not in result

    @pytest.mark.asyncio
    async def test_with_single_ts_code(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_name": ["概念1"],
                }
            )
        )
        result = await dao.get_concepts(ts_codes=["000001.SZ"])
        assert "000001.SZ" in result

    @pytest.mark.asyncio
    async def test_with_multiple_ts_codes(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_name": ["概念1"],
                }
            )
        )
        result = await dao.get_concepts(ts_codes=["000001.SZ", "000002.SZ"])
        assert "000001.SZ" in result

    @pytest.mark.asyncio
    async def test_empty_ts_codes_returns_empty(self):
        dao = _make_dao()
        result = await dao.get_concepts(ts_codes=[])
        assert result == {}
        dao._read_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_large_ts_codes_chunked(self):
        dao = _make_dao()
        codes = [f"{i:06d}.SZ" for i in range(1, 1201)]
        call_count = 0

        async def mock_read_db(sql, params=None, **kwargs):
            nonlocal call_count
            call_count += 1
            n = len(params) if params else 10
            return pd.DataFrame(
                {
                    "ts_code": [f"{i:06d}.SZ" for i in range(1, min(n + 1, 10))],
                    "concept_name": ["概念A"] * min(n, 9),
                }
            )

        dao._read_db = AsyncMock(side_effect=mock_read_db)
        await dao.get_concepts(ts_codes=codes)
        assert call_count == 3


class TestGetConceptCount:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"cnt": [50]}))
        assert await dao.get_concept_count() == 50

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_concept_count() == 0

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        assert await dao.get_concept_count() == 0


class TestUpsertAiConcepts:
    @pytest.mark.asyncio
    async def test_empty_entries(self):
        dao = _make_dao()
        assert await dao.upsert_ai_concepts([]) == 0

    @pytest.mark.asyncio
    async def test_no_ts_code(self):
        dao = _make_dao()
        entries = [{"concepts": ["概念1"]}]
        result = await dao.upsert_ai_concepts(entries)
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_concepts(self):
        dao = _make_dao()
        entries = [{"ts_code": "000001.SZ", "concepts": []}]
        await dao.upsert_ai_concepts(entries)
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_concepts(self):
        dao = _make_dao()
        entries = [{"ts_code": "000001.SZ", "concepts": ["概念1", "概念2"]}]
        await dao.upsert_ai_concepts(entries)
        dao._save_upsert.assert_called_once()


class TestSearchStocks:
    """stock_dao.search_stocks 单元测试（issue #433 添加关注搜索框）."""

    @pytest.mark.asyncio
    async def test_empty_keyword_returns_empty_df(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock()
        result = await dao.search_stocks("")
        assert isinstance(result, pd.DataFrame)
        assert result.empty
        dao._read_db_select.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_keyword_returns_empty_df(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock()
        result = await dao.search_stocks("   ")
        assert isinstance(result, pd.DataFrame)
        assert result.empty
        dao._read_db_select.assert_not_called()

    @pytest.mark.asyncio
    async def test_keyword_queries_db(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]}))
        result = await dao.search_stocks("平安")
        assert len(result) == 1
        assert result.iloc[0]["ts_code"] == "000001.SZ"
        # stmt 参数化查询 (R4): 仅传 SQLAlchemy 语句, 无字符串拼接
        dao._read_db_select.assert_awaited_once()
        stmt = dao._read_db_select.call_args.args[0]
        assert isinstance(stmt, sa.Select)

    @pytest.mark.asyncio
    async def test_keyword_stripped(self):
        dao = _make_dao()
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.search_stocks("  平安  ")
        stmt = dao._read_db_select.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # 关键词去除首尾空白后进入参数化查询
        assert "%平安%" in compiled
        assert "%  平安  %" not in compiled


class TestStockBasicHealthSummary:
    """DAT-13: stock_basic 健康摘要 DAO 方法（active 数 / 新鲜度 / ts_code 格式）。"""

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "active_count": [5200],
                "latest_updated_at": [datetime.datetime(2024, 6, 10, 9, 30)],
                "invalid_ts_code_count": [0],
            }
        )
        dao._read_db = AsyncMock(return_value=df)
        result = await dao.get_stock_basic_health_summary()
        assert result.iloc[0]["active_count"] == 5200
        assert result.iloc[0]["invalid_ts_code_count"] == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_stock_basic_health_summary() is None


class TestGetLatestOpenCalDate:
    """DAT-13: trade_cal 最新 is_open=1 日期 DAO 方法。"""

    @pytest.mark.asyncio
    async def test_with_date(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"latest_cal": [datetime.date(2024, 7, 20)]}))
        result = await dao.get_latest_open_cal_date()
        assert result == datetime.date(2024, 7, 20)

    @pytest.mark.asyncio
    async def test_none(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"latest_cal": [None]}))
        assert await dao.get_latest_open_cal_date() is None

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_latest_open_cal_date() is None
