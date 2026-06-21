import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.market_dao import MarketDao

pytestmark = pytest.mark.unit


class TestMarketDaoSaveMarketNews:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news(
            {
                "content": "Test news",
                "tags": "finance",
                "publish_time": "2024-06-15 10:00:00",
                "source": "Sina",
            }
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_empty_content(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news({"content": ""})
        assert result == 1

    @pytest.mark.asyncio
    async def test_sql_uses_composite_conflict_key(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        await dao.save_market_news(
            {
                "content": "Test",
                "tags": None,
                "publish_time": "2024-06-15 10:00:00",
                "source": "Sina",
            }
        )
        call_args = dao._write_db.call_args
        sql = call_args[0][0]
        assert 'ON CONFLICT("content_hash","publish_time")' in sql

    @pytest.mark.asyncio
    async def test_sql_updates_content_and_source_on_conflict(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        await dao.save_market_news(
            {
                "content": "Updated news",
                "tags": None,
                "publish_time": "2024-06-15 10:00:00",
                "source": "CLS",
            }
        )
        call_args = dao._write_db.call_args
        sql = call_args[0][0]
        assert '"content" = excluded."content"' in sql
        assert '"source" = excluded."source"' in sql


class TestMarketDaoGetMarketNews:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news(limit=10)
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_min_time(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news(limit=10, min_publish_time="2024-06-15")
        assert result is not None


class TestMarketDaoSaveDailyIndicators:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_daily_indicators(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_daily_indicators(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_daily_indicators(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 5


class TestMarketDaoGetDailyIndicators:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_dates(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(ts_code="000001.SZ", start_date="20240101", end_date="20240630")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_limit(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(limit=10)
        assert result is not None


class TestMarketDaoGetDailyIndicatorsBulk:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_daily_indicators_bulk([])
        assert result is not None

    @pytest.mark.asyncio
    async def test_small_list(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators_bulk(["000001.SZ", "000002.SZ"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_large_list_chunked(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        codes = [f"{i:06d}.SZ" for i in range(600)]
        result = await dao.get_daily_indicators_bulk(codes, start_date="20240101")
        assert result is not None


class TestMarketDaoSaveIndexWeights:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_weights(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_weights(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_index_weights(pd.DataFrame({"index_code": ["000300.SH"]}))
        assert result == 3


class TestMarketDaoGetIndexWeights:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"index_code": ["000300.SH"]}))
        result = await dao.get_index_weights("000300.SH", "20240615")
        assert result is not None


class TestMarketDaoGetLatestIndexWeightDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["20240615"]}))
        result = await dao.get_latest_index_weight_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_latest_index_weight_date()
        assert result is None


class TestMarketDaoSaveMoneyflowHsgt:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_moneyflow_hsgt(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_moneyflow_hsgt(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_moneyflow_hsgt(pd.DataFrame({"trade_date": ["20240615"]}))
        assert result == 3


class TestMarketDaoGetMoneyflowHsgt:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"], "north_money": [100.0]}))
        result = await dao.get_moneyflow_hsgt(trade_date="20240615")
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"

    @pytest.mark.asyncio
    async def test_with_limit(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"], "north_money": [100.0]}))
        result = await dao.get_moneyflow_hsgt(limit=10)
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"


class TestMarketDaoGetMoneyflowHsgtRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame({"trade_date": ["20240615", "20240614"], "north_money": [100.0, 200.0]})
        )
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "trade_date" in result.columns
        assert result.attrs["column_units"]["north_money"] == "million_cny"
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date >= $1" in sql
        assert "trade_date <= $2" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert result is None


class TestMarketDaoSaveMoneyflowHsgtStringCoercion:
    @pytest.mark.asyncio
    async def test_numeric_string_coercion(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        df = pd.DataFrame(
            {
                "trade_date": ["20240615"],
                "north_money": ["100.5"],
                "south_money": ["200.3"],
            }
        )
        result = await dao.save_moneyflow_hsgt(df)
        assert result == 1
        call_args = dao._save_upsert.call_args
        saved_df = call_args[0][0]
        assert saved_df["north_money"].dtype in ["float64", "float32", "int64"]


class TestMarketDaoGetDailyIndicatorsNoParams:
    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once()


class TestMarketDaoGetMarketNewsNoMinTime:
    @pytest.mark.asyncio
    async def test_no_min_time(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news()
        assert result is not None
        dao._read_db.assert_called_once()


class TestMarketDaoGetLatestIndexWeightDateEmpty:
    @pytest.mark.asyncio
    async def test_empty_dataframe(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_latest_index_weight_date()
        assert result is None


class TestMarketDaoSaveMarketNewsNoneContent:
    @pytest.mark.asyncio
    async def test_none_content(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news({"content": None})
        assert result == 1
