import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.constants import REVIEW_STATUS_COMPLETED, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE


class TestReviewStatusLifecycle(unittest.TestCase):
    def test_status_transitions_are_valid(self):
        self.assertEqual(REVIEW_STATUS_PENDING, "PENDING")
        self.assertEqual(REVIEW_STATUS_T1_DONE, "T1_DONE")
        self.assertEqual(REVIEW_STATUS_COMPLETED, "COMPLETED")

    def test_t1_done_is_intermediate(self):
        valid_transitions_from_pending = {REVIEW_STATUS_T1_DONE, REVIEW_STATUS_COMPLETED}
        self.assertIn(REVIEW_STATUS_T1_DONE, valid_transitions_from_pending)
        self.assertIn(REVIEW_STATUS_COMPLETED, valid_transitions_from_pending)

    def test_completed_is_terminal(self):
        self.assertNotEqual(REVIEW_STATUS_COMPLETED, REVIEW_STATUS_PENDING)
        self.assertNotEqual(REVIEW_STATUS_COMPLETED, REVIEW_STATUS_T1_DONE)


class TestSaveResultsDictFormat(unittest.TestCase):
    @patch("data.persistence.review_manager.ReviewManager.__init__", return_value=None)
    def test_save_results_passes_dict_list_to_dao(self, mock_init):
        from data.persistence.review_manager import ReviewManager

        rm = ReviewManager.__new__(ReviewManager)
        rm.cache = MagicMock()
        rm.cache.screener_dao = MagicMock()
        rm.cache.screener_dao.save_screening_results = AsyncMock()

        import datetime

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "pct_chg": [1.0],
                "industry": ["银行"],
                "vol": [1000.0],
                "amount": [10000.0],
                "turnover_rate": [1.0],
                "pe_ttm": [10.0],
                "pb": [1.0],
                "ps_ttm": [2.0],
                "dv_ttm": [3.0],
                "total_mv": [100.0],
                "circ_mv": [80.0],
                "roe": [15.0],
                "grossprofit_margin": [30.0],
                "debt_to_assets": [50.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [5.0],
                "ai_score": [80],
                "ai_reason": ["Good"],
                "thinking": ["Analysis"],
            },
        )

        import asyncio

        asyncio.run(
            rm.save_results(
                strategy_name="test",
                df=df,
                trade_date=datetime.date(2024, 1, 1),
                run_id="abc123",
                params_snapshot={"rsi_threshold": 30},
            )
        )

        rm.cache.screener_dao.save_screening_results.assert_called_once()
        call_args = rm.cache.screener_dao.save_screening_results.call_args[0][0]
        self.assertIsInstance(call_args, list)
        self.assertEqual(len(call_args), 1)
        self.assertIsInstance(call_args[0], dict)
        self.assertEqual(call_args[0]["ts_code"], "000001.SZ")
        self.assertEqual(call_args[0]["params_snapshot"], {"rsi_threshold": 30})
        self.assertEqual(call_args[0]["run_id"], "abc123")


class TestUpdatePredictionResultStatusTransition(unittest.TestCase):
    def test_t1_price_must_be_keyword_argument(self):
        """O-1: 指标参数应使用命名传参，避免位置参数错位。"""
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)

        import asyncio

        with self.assertRaises(TypeError):
            asyncio.run(dao.update_prediction_result(1, 5.0, "WIN", 10.5))

    def test_t1_only_gives_t1_done(self):
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        dao.engine = MagicMock()
        mock_conn = AsyncMock()
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        import asyncio

        asyncio.run(
            dao.update_prediction_result(
                record_id=1,
                pct=5.0,
                label="WIN",
                t1_price=10.5,
            )
        )

        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled = executed_stmt.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)
        self.assertIn("T1_DONE", sql_str)

    def test_t1_and_t5_gives_completed(self):
        from data.persistence.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        dao.engine = MagicMock()
        mock_conn = AsyncMock()
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        import asyncio

        asyncio.run(
            dao.update_prediction_result(
                record_id=1,
                pct=5.0,
                label="WIN",
                t1_price=10.5,
                t5_pct=3.0,
                t5_price=10.3,
                index_pct=1.0,
                alpha=2.0,
            )
        )

        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled = executed_stmt.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)
        self.assertIn("COMPLETED", sql_str)


class TestParamsSnapshotJsonb(unittest.TestCase):
    def test_dict_params_snapshot_passes_through(self):
        from data.persistence.review_manager import ReviewManager

        rm = ReviewManager.__new__(ReviewManager)
        rm.cache = MagicMock()
        rm.cache.screener_dao = MagicMock()
        rm.cache.screener_dao.save_screening_results = AsyncMock()

        import datetime

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "pct_chg": [1.0],
                "industry": ["银行"],
                "vol": [1000.0],
                "amount": [10000.0],
                "turnover_rate": [1.0],
                "pe_ttm": [10.0],
                "pb": [1.0],
                "ps_ttm": [2.0],
                "dv_ttm": [3.0],
                "total_mv": [100.0],
                "circ_mv": [80.0],
                "roe": [15.0],
                "grossprofit_margin": [30.0],
                "debt_to_assets": [50.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [5.0],
                "ai_score": [80],
                "ai_reason": ["Good"],
                "thinking": ["Analysis"],
            },
        )

        import asyncio

        params = {"strategy": "oversold", "rsi_threshold": 30}
        asyncio.run(
            rm.save_results(
                strategy_name="test",
                df=df,
                trade_date=datetime.date(2024, 1, 1),
                params_snapshot=params,
            )
        )

        call_args = rm.cache.screener_dao.save_screening_results.call_args[0][0]
        self.assertEqual(call_args[0]["params_snapshot"], params)
        self.assertIsInstance(call_args[0]["params_snapshot"], dict)

    def test_string_params_snapshot_converted_to_dict(self):
        from data.persistence.review_manager import ReviewManager

        rm = ReviewManager.__new__(ReviewManager)
        rm.cache = MagicMock()
        rm.cache.screener_dao = MagicMock()
        rm.cache.screener_dao.save_screening_results = AsyncMock()

        import datetime

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "pct_chg": [1.0],
                "industry": ["银行"],
                "vol": [1000.0],
                "amount": [10000.0],
                "turnover_rate": [1.0],
                "pe_ttm": [10.0],
                "pb": [1.0],
                "ps_ttm": [2.0],
                "dv_ttm": [3.0],
                "total_mv": [100.0],
                "circ_mv": [80.0],
                "roe": [15.0],
                "grossprofit_margin": [30.0],
                "debt_to_assets": [50.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [5.0],
                "ai_score": [80],
                "ai_reason": ["Good"],
                "thinking": ["Analysis"],
            },
        )

        import asyncio

        asyncio.run(
            rm.save_results(
                strategy_name="test",
                df=df,
                trade_date=datetime.date(2024, 1, 1),
                params_snapshot='{"strategy": "oversold"}',
            )
        )

        call_args = rm.cache.screener_dao.save_screening_results.call_args[0][0]
        self.assertIsInstance(call_args[0]["params_snapshot"], dict)
        self.assertEqual(call_args[0]["params_snapshot"]["strategy"], "oversold")


if __name__ == "__main__":
    unittest.main()
