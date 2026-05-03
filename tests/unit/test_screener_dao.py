import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

from data.persistence.daos.screener_dao import ScreenerDao


class TestScreenerDaoGetScreeningHistory:
    @pytest.mark.asyncio
    async def test_with_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history("test_strategy", limit=10)
        assert result is not None

    @pytest.mark.asyncio
    async def test_without_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history(None, limit=10)
        assert result is not None


class TestScreenerDaoGetHistoryTree:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "run_id": ["r1"],
                    "trade_date": ["20240615"],
                    "strategy_name": ["test"],
                    "cnt": [5],
                }
            )
        )
        result = await dao.get_history_tree(offset=0, limit=30)
        assert result is not None


class TestScreenerDaoGetHistoryRecords:
    @pytest.mark.asyncio
    async def test_with_run_id(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date=None, run_id="r1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_strategy_name(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615", strategy_name="test")
        assert result is not None


class TestScreenerDaoGetPendingReviews:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        result = await dao.get_pending_reviews()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_pending_reviews()
        assert result == []


class TestScreenerDaoGetLearningExamples:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [0.5],
                }
            )
        )
        wins, losses = await dao.get_learning_examples(limit=3)
        assert wins is not None
        assert losses is not None


class TestScreenerDaoGetScreeningData:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_screening_data(trade_date="20240615")
        assert result is not None

    @pytest.mark.asyncio
    async def test_without_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_screening_data()
        assert result is not None


class TestScreenerDaoGetPendingPredictions:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "trade_date": ["20240615"],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        result = await dao.get_pending_predictions("20240601")
        assert result is not None

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_pending_predictions("20240601")
        assert isinstance(result, pd.DataFrame)


class TestScreenerDaoGetLearningContext:
    @pytest.mark.asyncio
    async def test_win(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_loss(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [-0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=False)
        assert result is not None


class TestScreenerDaoUpdatePredictionResult:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = ScreenerDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_prediction_result(
            record_id=1,
            pct=5.0,
            label="WIN",
            t1_price=10.0,
            t5_pct=3.0,
            t5_price=10.3,
            index_pct=1.0,
            alpha=4.0,
        )

    @pytest.mark.asyncio
    async def test_with_review_status(self):
        dao = ScreenerDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_prediction_result(
            record_id=1,
            pct=5.0,
            label="WIN",
            review_status="completed",
        )


class TestScreenerDaoSaveScreeningResults:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        dao = ScreenerDao(MagicMock())
        await dao.save_screening_results([])
        await dao.save_screening_results(None)

    @pytest.mark.asyncio
    async def test_with_dict_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [{"run_id": "r1", "ts_code": "000001.SZ", "name": "Test", "trade_date": "20240615"}]
        await dao.save_screening_results(records)

    @pytest.mark.asyncio
    async def test_with_thinking(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        dao._save_thinking = AsyncMock()
        records = [
            {
                "run_id": "r1",
                "ts_code": "000001.SZ",
                "name": "Test",
                "trade_date": "20240615",
                "thinking": "AI analysis",
            }
        ]
        await dao.save_screening_results(records)
        dao._save_thinking.assert_called_once()
