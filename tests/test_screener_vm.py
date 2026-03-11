import unittest
import asyncio
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from ui.viewmodels.screener_view_model import ScreenerViewModel


class TestScreenerViewModel(unittest.TestCase):
    def setUp(self):
        self.vm = ScreenerViewModel()
        # Mock dependencies
        self.vm.data_processor = AsyncMock()
        self.vm.strategy_mgr = MagicMock()
        self.vm.review_mgr = AsyncMock()

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


if __name__ == "__main__":
    unittest.main()
