import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

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


class TestClearAllDoubaoConcepts:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=10)
        result = await dao.clear_all_doubao_concepts()
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

        async def mock_read_db(sql, params=None):
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
