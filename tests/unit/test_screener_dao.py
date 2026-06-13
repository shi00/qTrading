import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd

from data.persistence.daos.screener_dao import ScreenerDao
from data.constants import REVIEW_STATUS_COMPLETED


class TestScreenerDaoGetScreeningHistory:
    @pytest.mark.asyncio
    async def test_with_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history("test_strategy", limit=10)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history(None, limit=10)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1


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
        assert isinstance(result, pd.DataFrame)
        assert "run_id" in result.columns
        assert len(result) == 1


class TestScreenerDaoGetHistoryRecords:
    @pytest.mark.asyncio
    async def test_with_run_id(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date=None, run_id="r1")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_strategy_name(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615", strategy_name="test")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1


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
        assert len(result) == 1

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
        assert isinstance(wins, pd.DataFrame)
        assert isinstance(losses, pd.DataFrame)


class TestScreenerDaoGetScreeningData:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_screening_data(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

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
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns


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
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

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
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=True)
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_loss(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [-0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=False)
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_as_of_adds_date_filter(self):
        import datetime

        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        as_of_date = datetime.date(2024, 6, 1)
        await dao.get_learning_context(limit=3, is_win=True, as_of=as_of_date)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "trade_date <" in sql
        compiled = stmt.compile()
        assert compiled.params["prediction_result_1"] == "WIN"
        assert compiled.params["review_status_1"] == REVIEW_STATUS_COMPLETED
        assert compiled.params["trade_date_1"] == as_of_date
        assert "LIMIT :PARAM_1" in sql.upper() or "LIMIT 3" in sql.upper()
        assert compiled.params.get("param_1") == 3 or compiled.params.get("limit_1") == 3

    @pytest.mark.asyncio
    async def test_no_as_of_no_date_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "trade_date <" not in sql

    @pytest.mark.asyncio
    async def test_sql_includes_t5_pct_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NOT NULL" in sql

    @pytest.mark.asyncio
    async def test_sql_includes_review_status_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "review_status" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()

    @pytest.mark.asyncio
    async def test_as_of_sql_includes_t5_pct_and_review_status(self):
        import datetime

        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        as_of_date = datetime.date(2024, 6, 1)
        await dao.get_learning_context(limit=3, is_win=True, as_of=as_of_date)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NOT NULL" in sql
        assert "review_status" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()


class TestScreenerDaoUpdatePredictionResult:
    @pytest.mark.asyncio
    async def test_basic(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

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
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_review_status(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

        await dao.update_prediction_result(
            record_id=1,
            pct=5.0,
            label="WIN",
            review_status="completed",
        )
        mock_conn.execute.assert_called_once()


class TestScreenerDaoSaveScreeningResults:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=0)
        await dao.save_screening_results([])
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=0)
        await dao.save_screening_results(None)
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_dict_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [{"run_id": "r1", "ts_code": "000001.SZ", "name": "Test", "trade_date": "20240615"}]
        await dao.save_screening_results(records)
        dao._save_upsert.assert_called_once()

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


class TestScreenerDaoBuildScreeningSql:
    def test_build_sql_with_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql(require_close=True)
        assert "q.close IS NOT NULL" in sql
        assert "b.list_status = 'L'" in sql

    def test_build_sql_without_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql(require_close=False)
        assert "q.close IS NOT NULL" not in sql
        assert "b.list_status = 'L'" in sql

    def test_build_sql_contains_all_joins(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "LEFT JOIN daily_quotes q" in sql
        assert "LEFT JOIN daily_indicators i" in sql
        assert "LEFT JOIN suspend_d s" in sql
        assert "financial_reports" in sql

    def test_build_sql_contains_is_tradable(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "is_tradable" in sql

    def test_build_sql_contains_financial_subquery(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "ROW_NUMBER() OVER" in sql
        assert "PARTITION BY ts_code" in sql


class TestScreenerDaoGetLatestClosedTradeDate:
    @pytest.mark.asyncio
    async def test_returns_date_string(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": ["20240615"]}))
        result = await dao._get_latest_closed_trade_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao._get_latest_closed_trade_date()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_nan(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [float("nan")]}))
        result = await dao._get_latest_closed_trade_date()
        assert result is None


class TestScreenerDaoGetScreeningDataNoTradeDate:
    @pytest.mark.asyncio
    async def test_no_trade_date_and_db_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoGetFundamentalScreeningData:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_fundamental_screening_data(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_without_trade_date_auto_resolve(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_fundamental_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_trade_date_and_db_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_fundamental_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoUpdatePredictionResultEdgeCases:
    @pytest.mark.asyncio
    async def test_table_not_in_metadata(self):
        dao = ScreenerDao(MagicMock())
        dao._check_engine = MagicMock()
        with patch("data.persistence.daos.screener_dao.Base") as mock_base:
            mock_base.metadata.tables.get.return_value = None
            await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")
        mock_base.metadata.tables.get.assert_called()

    @pytest.mark.asyncio
    async def test_engine_not_initialized(self):
        dao = ScreenerDao(MagicMock())
        dao.engine = None
        with patch("data.persistence.daos.screener_dao.sa.update") as mock_update:
            mock_update.return_value.where.return_value.values.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="Engine not initialized"):
                await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")

    @pytest.mark.asyncio
    async def test_default_status_t1_done_when_no_t5(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

        with patch("data.persistence.daos.screener_dao.sa.update") as mock_update:
            mock_update.return_value.where.return_value.values.return_value = MagicMock()
            await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")
        mock_conn.execute.assert_called_once()


class TestScreenerDaoSaveScreeningResultsTuple:
    @pytest.mark.asyncio
    async def test_with_tuple_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        with patch(
            "data.persistence.daos.screener_dao.get_model_columns",
            return_value=["run_id", "ts_code", "name", "trade_date"],
        ):
            records = [("r1", "000001.SZ", "Test", "20240615")]
            await dao.save_screening_results(records)
            dao._save_upsert.assert_called_once()


class TestScreenerDaoSaveThinking:
    @pytest.mark.asyncio
    async def test_save_thinking_with_matching_ids(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1], "run_id": ["r1"], "ts_code": ["000001.SZ"]}))
        dao._save_upsert = AsyncMock(return_value=1)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_thinking_no_matching_ids(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1], "run_id": ["r2"], "ts_code": ["000002.SZ"]}))
        dao._save_upsert = AsyncMock(return_value=0)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_thinking_empty_read(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        dao._save_upsert = AsyncMock(return_value=0)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_not_called()
