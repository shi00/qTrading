import asyncio
import datetime
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from ui.viewmodels.screener_view_model import ScreenerViewModel


class TestScreenerViewModel(unittest.TestCase):
    def setUp(self):
        self.vm = ScreenerViewModel()
        # Mock dependencies
        self.vm.data_processor = AsyncMock()
        self.vm.strategy_mgr = MagicMock()
        self.vm.review_mgr = AsyncMock()

    def test_dispose_clears_large_references_and_callbacks(self):
        self.vm.on_update = MagicMock()
        self.vm.on_log = MagicMock()
        self.vm.on_status = MagicMock()
        self.vm.on_progress = MagicMock()
        self.vm.on_log_stream_start = MagicMock()
        self.vm._full_results = pd.DataFrame({"ts_code": ["000001.SZ"]})
        self.vm._ai_buffer = [{"ts_code": "000001.SZ"}]
        self.vm._realtime_snapshot = {"ts_code": "000001.SZ"}

        self.vm.dispose()

        self.assertIsNone(self.vm.on_update)
        self.assertIsNone(self.vm.on_log)
        self.assertIsNone(self.vm.on_status)
        self.assertIsNone(self.vm.on_progress)
        self.assertIsNone(self.vm.on_log_stream_start)
        self.assertIsNone(self.vm._full_results)
        self.assertEqual(self.vm._ai_buffer, [])
        self.assertIsNone(self.vm._realtime_snapshot)

    def test_pagination(self):
        # Setup dummy data
        df = pd.DataFrame({"A": range(100)})
        self.vm._full_results = df
        self.vm._update_pagination()

        self.assertEqual(self.vm.total_items, 100)
        self.assertEqual(self.vm.total_pages, 2)  # 50 per page

        # Initial page
        self.assertEqual(self.vm.page_no, 1)
        page_data = self.vm.get_current_page_data()
        self.assertEqual(len(page_data), 50)
        self.assertEqual(page_data.iloc[0]["A"], 0)

        # Next page
        self.vm.change_page(1)  # Removed asyncio.run
        self.assertEqual(self.vm.page_no, 2)
        self.vm._update_pagination()  # Call it directly as change_page is throttled and needs event loop
        page_data = self.vm.get_current_page_data()
        self.assertEqual(len(page_data), 50)
        self.assertEqual(page_data.iloc[0]["A"], 50)

        # Ignore out of bounds
        self.vm.change_page(1)  # Removed asyncio.run
        self.vm._update_pagination()
        self.assertEqual(self.vm.page_no, 2)

    def test_sorting(self):
        async def run_test():
            # Setup dummy data
            df = pd.DataFrame({"A": [3, 1, 2], "B": ["c", "a", "b"]})
            self.vm._full_results = df

            # Sort by A (asc)
            # Note: ThreadPoolManager in test might need real execution or mock
            # If ThreadPoolManager uses real threads, this should work.
            await self.vm.sort_data("A")

            self.assertEqual(self.vm.sort_column, "A")
            self.assertFalse(self.vm.sort_ascending)  # First click sets desc usually?
            # Wait, implementation says: if col==sort_col toggle, else sort_col=new, asc=False
            # Let's check implementation:
            # if self.sort_column == column_key:
            #    self.sort_ascending = not self.sort_ascending
            # else:
            #    self.sort_column = column_key
            #    self.sort_ascending = False

            # Implementation sets False (Descending) on first click?
            # Let's check: self.sort_column starts as None.
            # So first click -> asc=False (Desc). Correct.

            self.assertEqual(self.vm._full_results.iloc[0]["A"], 3)

            # Sort by A again (toggle -> Ascending)
            await self.vm.sort_data("A")
            self.assertTrue(self.vm.sort_ascending)
            self.assertEqual(self.vm._full_results.iloc[0]["A"], 1)

        asyncio.run(run_test())

    def test_ai_streaming_buffer(self):
        async def run_test():
            # 1. Setup
            self.vm.on_update = MagicMock()
            self.vm.on_log = MagicMock()
            self.vm._full_results = pd.DataFrame(columns=["name", "ai_score"])

            # Simulate bind capturing loop
            self.vm._main_loop = asyncio.get_running_loop()

            # 2. Simulate Stream
            row1 = {"name": "S1", "ai_score": 90}
            row2 = {"name": "S2", "ai_score": 80}

            # This call will now use create_task on the running loop
            self.vm._on_ai_result_stream(row1)
            self.vm._on_ai_result_stream(row2)

            # Buffer should have 2 items (because flush is async/scheduled, likely not ran yet)
            # However, since we are in the same loop and haven't yielded, the task created by create_task
            # hasn't started yet. So buffer is still there.
            self.assertEqual(len(self.vm._ai_buffer), 2)

            # Log should be called immediately
            self.assertEqual(self.vm.on_log.call_count, 2)

            # 3. Force Flush via await (bypass scheduler)
            await self.vm._flush_ai_buffer()

            # Buffer cleared
            self.assertEqual(len(self.vm._ai_buffer), 0)

            # Dataframe updated
            self.assertEqual(len(self.vm._full_results), 2)
            self.assertEqual(self.vm._full_results.iloc[0]["name"], "S1")

        asyncio.run(run_test())

    def test_get_export_data_none_when_empty(self):
        self.vm._full_results = None
        self.assertIsNone(self.vm.get_export_data())

    def test_get_export_data_none_when_empty_df(self):
        self.vm._full_results = pd.DataFrame()
        self.assertIsNone(self.vm.get_export_data())

    def test_get_export_data_returns_df(self):
        df = pd.DataFrame({"A": [1, 2, 3]})
        self.vm._full_results = df
        result = self.vm.get_export_data()
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)

    def test_export_results_no_data(self):
        async def run_test():
            self.vm._full_results = None
            path, error = await self.vm.export_results("/tmp/test.csv")
            self.assertIsNone(path)
            self.assertEqual(error, "No data to export")

        asyncio.run(run_test())

    def test_export_results_success(self):
        async def run_test():
            self.vm._full_results = pd.DataFrame({"A": [1, 2, 3]})
            with tempfile.TemporaryDirectory() as tmpdir:
                filepath = os.path.join(tmpdir, "test_export.csv")
                with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tm:
                    mock_tm.return_value.run_async = AsyncMock(
                        side_effect=lambda tt, func, *args, **kwargs: func(*args, **kwargs),
                    )
                    path, error = await self.vm.export_results(filepath)
                    self.assertEqual(path, filepath)
                    self.assertIsNone(error)

        asyncio.run(run_test())

    def test_run_strategy_passes_trade_date_to_save_results(self):
        """验证 run_strategy 从 context 获取 trade_date 并传给 save_results，同时透传 run_id 和 params_snapshot"""

        async def run_test():
            analysis_date = datetime.date(2024, 12, 27)
            result_df = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "close": [10.5],
                    "pct_chg": [2.5],
                    "ai_score": [85],
                    "ai_reason": ["test"],
                    "thinking": ["test"],
                }
            )

            mock_strategy = MagicMock()
            mock_strategy.name = "test_strategy"
            mock_strategy.filter = AsyncMock(return_value=result_df)
            self.vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

            test_params = {"rsi_threshold": 30, "volume_ratio": 2.0}
            self.vm.data_processor.get_strategy_data = AsyncMock(
                return_value={
                    "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                    "trade_date": analysis_date,
                }
            )

            submitted_coro = []

            def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
                submitted_coro.append(coroutine_factory(task_id="test_task_id"))
                return "test_task_id"

            with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
                mock_tm.return_value.update_progress = MagicMock()
                mock_tm.return_value.submit_task = mock_submit_task
                await self.vm.run_strategy("test_strategy", save_results=True, params=test_params)

            for coro in submitted_coro:
                await coro

            self.vm.review_mgr.save_results.assert_called_once()
            call_kwargs = self.vm.review_mgr.save_results.call_args
            passed_trade_date = call_kwargs.kwargs.get("trade_date")
            self.assertEqual(
                passed_trade_date,
                analysis_date,
                f"save_results should receive trade_date={analysis_date}, got {passed_trade_date}",
            )
            passed_run_id = call_kwargs.kwargs.get("run_id")
            self.assertIsNotNone(passed_run_id, "save_results should receive a non-None run_id")
            self.assertEqual(len(passed_run_id), 16, f"run_id should be 16 chars, got {len(passed_run_id)}")
            passed_params = call_kwargs.kwargs.get("params_snapshot")
            self.assertEqual(
                passed_params, test_params, "save_results should receive params_snapshot matching the UI params"
            )

        asyncio.run(run_test())

    def test_run_strategy_raises_when_trade_date_missing_before_save(self):
        """验证保存前缺失 trade_date 时，run_strategy 会明确失败"""

        async def run_test():
            result_df = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "close": [10.5],
                    "pct_chg": [2.5],
                }
            )

            mock_strategy = MagicMock()
            mock_strategy.name = "test_strategy"
            mock_strategy.filter = AsyncMock(return_value=result_df)
            self.vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
            self.vm.data_processor.get_strategy_data = AsyncMock(
                return_value={
                    "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                }
            )

            submitted_coro = []

            def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
                submitted_coro.append(coroutine_factory(task_id="test_task_id"))
                return "test_task_id"

            with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
                mock_tm.return_value.update_progress = MagicMock()
                mock_tm.return_value.submit_task = mock_submit_task
                await self.vm.run_strategy("test_strategy", save_results=True)

            self.assertEqual(len(submitted_coro), 1)
            with self.assertRaises(RuntimeError):
                await submitted_coro[0]
            self.vm.review_mgr.save_results.assert_not_called()

        asyncio.run(run_test())

    def test_run_strategy_reports_degraded_context_status(self):
        """N-2: context diagnostics.strategy_ready=False 时应提示降级状态。"""

        async def run_test():
            result_df = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "close": [10.5],
                    "pct_chg": [2.5],
                }
            )

            mock_strategy = MagicMock()
            mock_strategy.name = "test_strategy"
            mock_strategy.filter = AsyncMock(return_value=result_df)
            self.vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

            self.vm.data_processor.get_strategy_data = AsyncMock(
                return_value={
                    "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                    "trade_date": datetime.date(2024, 12, 31),
                    "_diagnostics": {
                        "strategy_ready": False,
                        "table_status": {
                            "northbound_data": {"ready": False, "rows": 0},
                            "moneyflow_data": {"ready": True, "rows": 1},
                        },
                    },
                }
            )
            self.vm.on_status = MagicMock()

            submitted_coro = []

            def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
                submitted_coro.append(coroutine_factory(task_id="test_task_id"))
                return "test_task_id"

            with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
                mock_tm.return_value.update_progress = MagicMock()
                mock_tm.return_value.submit_task = mock_submit_task
                await self.vm.run_strategy("test_strategy", save_results=False)

            self.assertEqual(len(submitted_coro), 1)
            await submitted_coro[0]
            status_calls = self.vm.on_status.call_args_list
            self.assertTrue(
                any(("策略降级运行" in (args[0] if args else "") and args[1] == "orange") for args, _ in status_calls),
                "N-2: should report degraded strategy status in orange",
            )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
