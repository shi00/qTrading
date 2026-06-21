import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest


pytestmark = pytest.mark.integration


class TestRunReviewE2E(unittest.TestCase):
    """M-3: run_review end-to-end integration tests."""

    def _pending_df(self, trade_date, ts_code="000001.SZ"):
        if isinstance(trade_date, str):
            td = datetime.datetime.strptime(trade_date, "%Y%m%d").date()
        else:
            td = trade_date
        return pd.DataFrame(
            [
                {
                    "id": 1,
                    "ts_code": ts_code,
                    "strategy_name": "test",
                    "trade_date": td,
                    "prediction_result": "WIN",
                    "ai_score": 80.0,
                }
            ]
        )

    def _make_manager(self, pending_df, quotes_df, index_df):
        from data.persistence.review_manager import ReviewManager

        manager = ReviewManager.__new__(ReviewManager)
        manager.cache = MagicMock()
        manager.cache.get_latest_trade_date = AsyncMock(return_value="20240318")
        manager.cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "cal_date": [
                        "20240308",
                        "20240311",
                        "20240312",
                        "20240313",
                        "20240314",
                        "20240315",
                        "20240318",
                        "20240319",
                        "20240320",
                        "20240321",
                    ],
                    "is_open": [1] * 10,
                }
            )
        )
        manager.cache.screener_dao = MagicMock()
        manager.cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pending_df)
        manager.cache.screener_dao.update_prediction_result = AsyncMock()
        manager.cache.get_daily_quotes = AsyncMock(return_value=quotes_df)
        manager.cache.get_index_daily = AsyncMock(return_value=index_df)
        manager.api = MagicMock()
        manager.api.get_index_daily = AsyncMock(return_value=index_df)
        return manager

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_t1_data_and_index_available_writes_t1_done(self, mock_config):
        mock_config.get_config.return_value = "000001.SH"
        pending_df = self._pending_df("20240315")
        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [datetime.date(2024, 3, 15), datetime.date(2024, 3, 18)],
                "close": [10.0, 10.5],
                "pct_chg": [1.0, 5.0],
            }
        )
        index_df = pd.DataFrame({"pct_chg": [1.0]})
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_called_once()
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        assert call_args[0][2] == "WIN"
        assert call_args.kwargs["t1_price"] == 10.5
        assert call_args.kwargs["t5_pct"] is None

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_t1_t5_index_available_writes_completed(self, mock_config):
        mock_config.get_config.return_value = "000001.SH"
        pending_df = self._pending_df("20240308")
        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 6,
                "trade_date": [
                    datetime.date(2024, 3, 8),
                    datetime.date(2024, 3, 11),
                    datetime.date(2024, 3, 12),
                    datetime.date(2024, 3, 13),
                    datetime.date(2024, 3, 14),
                    datetime.date(2024, 3, 15),
                ],
                "close": [10.0, 10.5, 10.3, 10.8, 11.0, 11.2],
                "pct_chg": [1.0, 5.0, -1.9, 4.85, 1.85, 1.82],
            }
        )
        index_df = pd.DataFrame({"pct_chg": [1.0]})
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_called_once()
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        assert call_args[0][2] == "WIN"
        assert call_args.kwargs["t5_price"] == 11.2
        assert abs(call_args.kwargs["t5_pct"] - 12.0) < 1e-6

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_index_unavailable_skips_and_stays_pending(self, mock_config):
        mock_config.get_config.return_value = "000001.SH"
        pending_df = self._pending_df("20240315")
        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [datetime.date(2024, 3, 15), datetime.date(2024, 3, 18)],
                "close": [10.0, 10.5],
                "pct_chg": [1.0, 5.0],
            }
        )
        manager = self._make_manager(pending_df, quotes_df, None)
        manager.cache.get_index_daily = AsyncMock(return_value=None)
        manager.api.get_index_daily = AsyncMock(return_value=None)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_not_called()

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_t0_close_zero_still_writes_t1(self, mock_config):
        mock_config.get_config.return_value = "000001.SH"
        pending_df = self._pending_df("20240315")
        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [datetime.date(2024, 3, 15), datetime.date(2024, 3, 18)],
                "close": [0.0, 10.5],
                "pct_chg": [0.0, 5.0],
            }
        )
        index_df = pd.DataFrame({"pct_chg": [1.0]})
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_called_once()
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        assert call_args.kwargs["t5_pct"] is None
        assert call_args.kwargs["t1_price"] == 10.5

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_t1_pct_nan_skips_and_stays_pending(self, mock_config):
        mock_config.get_config.return_value = "000001.SH"
        pending_df = self._pending_df("20240315")
        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [datetime.date(2024, 3, 15), datetime.date(2024, 3, 18)],
                "close": [10.0, 10.5],
                "pct_chg": [1.0, float("nan")],
            }
        )
        index_df = pd.DataFrame({"pct_chg": [1.0]})
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
