
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import flet as ft
from ui.views.screener_view import ScreenerView

class TestDeepLinking(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        # Mock run_task to execute coroutines immediately or store them?
        # Ideally we run them using asyncio.run logic for testing
        
        async def mock_run_task(coro, *args):
            if asyncio.iscoroutine(coro):
                await coro
            elif asyncio.iscoroutinefunction(coro):
                await coro(*args)
        
        self.page.run_task = mock_run_task
        
    def test_pending_strategy_execution(self):
        async def run_test():
            # 1. Init View
            with patch('ui.views.screener_view.ScreenerViewModel') as mock_vm_cls:
                mock_vm = mock_vm_cls.return_value
                mock_vm.get_strategies = AsyncMock(return_value={'strategy_a': 'Description A'})
                # Mock run_strategy
                mock_vm.run_strategy = AsyncMock()
                
                view = ScreenerView(self.page)
                # Mock update methods to avoid "Control must be added to the page" error
                view.strategy_dropdown.update = MagicMock()
                view.run_btn.update = MagicMock()
                view.log_view.update = MagicMock()
                view.status_text.update = MagicMock()
                
                # Verify initial state
                self.assertIsNone(view._pending_strategy_key)
                self.assertEqual(len(view.strategy_dropdown.options), 0)
                
                # 2. Simulate Deep Link Call (strategies NOT loaded yet)
                await view.select_and_run_strategy('strategy_a')
                
                # Verify Pending State
                self.assertEqual(view._pending_strategy_key, 'strategy_a')
                mock_vm.run_strategy.assert_not_called()
                
                # 3. Simulate Load Strategies (mimic did_mount -> _load_strategies)
                await view._load_strategies()
                
                # Verify Execution
                self.assertIsNone(view._pending_strategy_key) # Should be cleared
                self.assertEqual(view.strategy_dropdown.value, 'strategy_a')
                mock_vm.run_strategy.assert_called_with('strategy_a')
                
        asyncio.run(run_test())

    def test_immediate_strategy_execution(self):
        async def run_test():
            with patch('ui.views.screener_view.ScreenerViewModel') as mock_vm_cls:
                mock_vm = mock_vm_cls.return_value
                mock_vm.get_strategies = AsyncMock(return_value={'strategy_b': 'Description B'})
                mock_vm.run_strategy = AsyncMock()
                
                view = ScreenerView(self.page)
                view.strategy_dropdown.update = MagicMock()
                view.run_btn.update = MagicMock()
                view.log_view.update = MagicMock()
                
                # 1. Pre-load strategies
                await view._load_strategies()
                
                # 2. Call Deep Link
                await view.select_and_run_strategy('strategy_b')
                
                # Verify Immediate Execution
                self.assertIsNone(view._pending_strategy_key)
                mock_vm.run_strategy.assert_called_with('strategy_b')
                
        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
