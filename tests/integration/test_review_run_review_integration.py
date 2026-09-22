# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.constants import REVIEW_STATUS_T1_DONE

# no_db: 本文件全部测试为 MagicMock 风格（ReviewManager.__new__ + mock cache/api），
# 不触达真实 DB，跳过 test_engine 创建与 schema 初始化（与 test_service_review_manager.py 同模式）。
pytestmark = [pytest.mark.integration, pytest.mark.no_db]


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
        manager.label_horizon = "t5"  # D4-M4: __new__ 不跑 __init__，须显式设置默认标签窗口
        manager.cache = MagicMock()
        manager.cache.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240318")
        manager.cache.stock_dao.get_trade_cal = AsyncMock(
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
        manager.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=quotes_df)
        manager.cache.quote_dao.get_index_daily = AsyncMock(return_value=index_df)
        manager.cache.get_index_daily_range = AsyncMock(return_value=None)
        manager.api = MagicMock()
        manager.api.get_index_daily = AsyncMock(return_value=index_df)
        manager.alpha_win_threshold = 3.0
        manager.alpha_loss_threshold = 3.0
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
                "open": [10.0, 10.0],
                "pct_chg": [1.0, 5.0],
            }
        )
        index_df = pd.DataFrame(
            {"open": [100.0], "close": [100.0], "pct_chg": [1.0]}
        )  # RV-02: 含 open/close（窗口收益=0，alpha=t5_pct）
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_called_once()
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        # D4-M4: T+5 未成熟时回填 T+1 数值、打 DRAW 占位（alpha/index 留待 T+5 定稿）。
        assert call_args[0][2] == "DRAW"
        assert call_args.kwargs["t1_price"] == 10.5
        assert call_args.kwargs["t5_pct"] is None
        assert call_args.kwargs["index_pct"] is None
        assert call_args.kwargs["alpha"] is None

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
                "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
                "pct_chg": [1.0, 5.0, -1.9, 4.85, 1.85, 1.82],
            }
        )
        index_df = pd.DataFrame(
            {"open": [100.0], "close": [100.0], "pct_chg": [1.0]}
        )  # RV-02: 含 open/close（窗口收益=0，alpha=t5_pct）
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_called_once()
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        assert call_args[0][2] == "WIN"
        assert call_args.kwargs["t5_price"] == 11.2
        assert abs(call_args.kwargs["t5_pct"] - 12.0) < 1e-6

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_index_unavailable_stages_numeric_only(self, mock_config):
        """RV-04: T+5 已成熟但基准不可得时不再整条丢弃——数值-only 写入
        （t5_pct 照写，label/alpha/index_pct NULL、review_status=T1_DONE
        留在补标签通道 get_unlabeled_predictions，基准恢复后由 backfill 定稿）。"""
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
                "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
                "pct_chg": [1.0, 5.0, -1.9, 4.85, 1.85, 1.82],
            }
        )
        manager = self._make_manager(pending_df, quotes_df, None)
        manager.cache.quote_dao.get_index_daily = AsyncMock(return_value=None)
        manager.api.get_index_daily = AsyncMock(return_value=None)

        asyncio.run(manager.run_review())
        assert manager.cache.screener_dao.update_prediction_result.await_count == 1
        call_args = manager.cache.screener_dao.update_prediction_result.call_args
        assert call_args[0][2] is None  # label 未定稿（R21 NULL 不伪造）
        assert call_args.kwargs["t5_pct"] == pytest.approx(12.0)  # (11.2/10.0 - 1) × 100
        assert call_args.kwargs["index_pct"] is None
        assert call_args.kwargs["alpha"] is None
        assert call_args.kwargs["review_status"] == REVIEW_STATUS_T1_DONE

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_t0_close_zero_skips_and_stays_pending(self, mock_config):
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
        index_df = pd.DataFrame(
            {"open": [100.0], "close": [100.0], "pct_chg": [1.0]}
        )  # RV-02: 含 open/close（窗口收益=0，alpha=t5_pct）
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        # D2-3：t0 收盘无效（0）时无可靠收益基准，跳过避免基于脏数据打标签，保持 pending。
        manager.cache.screener_dao.update_prediction_result.assert_not_called()

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
        index_df = pd.DataFrame(
            {"open": [100.0], "close": [100.0], "pct_chg": [1.0]}
        )  # RV-02: 含 open/close（窗口收益=0，alpha=t5_pct）
        manager = self._make_manager(pending_df, quotes_df, index_df)

        asyncio.run(manager.run_review())
        manager.cache.screener_dao.update_prediction_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
