
import asyncio
import logging
import time
import pandas as pd
from typing import Optional, Dict, List, Callable

from data.data_processor import DataProcessor
from data.review_manager import ReviewManager
from strategies.all_strategies import StrategyManager
from utils.thread_pool import ThreadPoolManager, TaskType
from ui.i18n import I18n

logger = logging.getLogger(__name__)

class ScreenerViewModel:
    """
    ViewModel for ScreenerView.
    Handles data processing, strategy execution, sorting, and pagination.
    Offloads CPU-intensive tasks to ThreadPoolManager.
    """
    
    def __init__(self):
        # Dependencies
        self.data_processor = DataProcessor()
        self.strategy_mgr = StrategyManager()
        self.review_mgr = ReviewManager()
        
        # State
        self._full_results: Optional[pd.DataFrame] = None
        self.page_no = 1
        self.page_size = 50
        self.total_pages = 0
        self.total_items = 0
        
        # Sorting State
        self.sort_column: Optional[str] = None
        self.sort_ascending = True
        
        # AI Stream Buffer
        self._ai_buffer = []
        self._last_ai_update = 0
        self.AI_UPDATE_INTERVAL = 0.5 # Seconds
        self._flush_pending = False
        
        # Callbacks (View binders)
        self.on_update: Optional[Callable] = None
        self.on_log: Optional[Callable[[str, int, str], None]] = None
        self.on_status: Optional[Callable[[str, str], None]] = None
        self.on_progress: Optional[Callable[[float], None]] = None
        self._main_loop = None
        
    def bind(self, on_update, on_log, on_status, on_progress):
        self.on_update = on_update
        self.on_log = on_log
        self.on_status = on_status
        self.on_progress = on_progress
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("ScreenerViewModel bound freely without loop")
        
    def init(self):
        """Initialize resources"""
        pass
        
    def dispose(self):
        """Cleanup resources"""
        self.on_update = None
        self.on_log = None
        
    # --- Data Actions ---
    
    async def get_strategies(self) -> Dict[str, str]:
        return self.strategy_mgr.get_all_names()
        
    def get_strategy_desc(self, key: str) -> str:
        st = self.strategy_mgr.get_strategy(key)
        return st.description if st else ""
        
    async def run_strategy(self, strategy_key: str, save_results: bool = True):
        """Execute strategy screening"""
        strategy = self.strategy_mgr.get_strategy(strategy_key)
        if not strategy:
            logger.error(f"[ScreenerVM] Strategy not found: {strategy_key}")
            if self.on_status: self.on_status(I18n.get("screener_strategy_not_found"), "red")
            return
            
        logger.info(f"[ScreenerVM] Starting strategy: {strategy.name} ({strategy_key})")
            
        # Reset State
        self._full_results = None
        self.page_no = 1
        self._ai_buffer = []
        if self.on_progress: self.on_progress(True) # Show spinner
        if self.on_status: self.on_status(I18n.get("screener_running_strategy").format(name=strategy.name), "blue")
        
        try:
            # 1. Prepare Context (may trigger massive data load)
            # Use data_processor to handle caching/fetching
            context = await self.data_processor.get_strategy_data()
            if not context:
                # Try init if empty
                if self.on_status: self.on_status(I18n.get("screener_loading_data"), "orange")
                await self.data_processor.init_data()
                context = await self.data_processor.get_strategy_data()
            
            if not context or 'screening_data' not in context or context['screening_data'].empty:
                if self.on_status: self.on_status(I18n.get("screener_no_data_context"), "red")
                if self.on_progress: self.on_progress(False)
                return

            context['data_processor'] = self.data_processor
            
            # Setup AI Callbacks
            context['on_progress'] = self._on_ai_progress
            context['on_result'] = self._on_ai_result_stream
            
            # 2. Execute Strategy (Offload if strictly CPU bound, but strategies might be async)
            # Most strategies are async filters or fast pandas ops.
            # If standard key like 'fq' it's fast. If AI, it's async streaming.
            
            if asyncio.iscoroutinefunction(strategy.filter):
                result_df = await strategy.filter(context)
            else:
                # Wrap sync strategy in thread pool
                result_df = await ThreadPoolManager().run_async(TaskType.CPU, strategy.filter, context)
            
            # 3. Process Results
            if result_df is not None and not result_df.empty:
                self._full_results = result_df
                self._update_pagination()
                
                msg = I18n.get("screener_done").format(count=len(result_df))
                if save_results:
                     await self.review_mgr.save_results(strategy.name, result_df)
                     msg = I18n.get("screener_done_saved").format(count=len(result_df))
                if self.on_status: self.on_status(msg, "green")
            else:
                 self._full_results = pd.DataFrame()
                 if self.on_status: self.on_status(I18n.get("screener_no_results"), "orange")
            
            # Force final update
            if self.on_update: self.on_update()
            logger.info(f"[ScreenerVM] Strategy {strategy.name} completed. Items: {len(self._full_results) if self._full_results is not None else 0}")
            
        except Exception as e:
            logger.error(f"[ScreenerVM] Strategy execution failed: {e}", exc_info=True)
            if self.on_status: self.on_status(I18n.get("screener_exec_error").format(error=str(e)), "red")
        finally:
            if self.on_progress: self.on_progress(False)

    # --- Sorting & Pagination ---
    
    async def sort_data(self, column_key: str):
        """Sort data using ThreadPool to avoid blocking UI"""
        if self._full_results is None or self._full_results.empty:
            return
            
        # Toggle sort order
        if self.sort_column == column_key:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column_key
            self.sort_ascending = False # Default desc for numbers usually
            
        if self.on_progress: self.on_progress(True)
        
        try:
            # Offload sorting to thread
            sorted_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._sort_helper,
                self._full_results,
                column_key,
                self.sort_ascending
            )
            
            self._full_results = sorted_df
            self.page_no = 1 # Reset to first page
            if self.on_update: self.on_update()
            
        except Exception as e:
            logger.error(f"Sort failed: {e}", exc_info=True)
        finally:
            if self.on_progress: self.on_progress(False)
            
    @staticmethod
    def _sort_helper(df, col, ascending):
        """Static helper for pickling/thread safety"""
        try:
            return df.sort_values(by=col, ascending=ascending, na_position='last')
        except KeyError:
             return df
    
    async def change_page(self, delta: int):
        new_page = self.page_no + delta
        if 1 <= new_page <= self.total_pages:
            self.page_no = new_page
            if self.on_update: self.on_update()
            
    def get_current_page_data(self):
        """Get data for current page (Synchronous, fast slicing)"""
        if self._full_results is None or self._full_results.empty:
            return []
            
        start = (self.page_no - 1) * self.page_size
        end = start + self.page_size
        # Slicing is fast enough for main thread
        return self._full_results.iloc[start:end]

    def _update_pagination(self):
        if self._full_results is not None:
            self.total_items = len(self._full_results)
            self.total_pages = (self.total_items + self.page_size - 1) // self.page_size
        else:
            self.total_items = 0
            self.total_pages = 0

    # --- AI Streaming Handlers ---
    
    def _on_ai_progress(self, current, total, msg):
        # Pass through status update
        if self.on_status: 
            self.on_status(I18n.get("screener_ai_analyzing").format(done=current, total=total, msg=msg), "blue")

    def _on_ai_result_stream(self, row_data):
        """Buffer incoming AI results and update in batches"""
        if not row_data: return
        
        # 1. Update Log immediately (Log is strictly append, low cost if virtualized)
        if self.on_log:
             name = row_data.get('name', 'Unknown')
             score = row_data.get('ai_score', 0)
             thinking = str(row_data.get('thinking', ''))
             self.on_log(name, score, thinking)
             
        # 2. Buffer for Table Update
        self._ai_buffer.append(row_data)
        
        now = time.time()
        if now - self._last_ai_update > self.AI_UPDATE_INTERVAL or len(self._ai_buffer) >= 20:
             # Trigger Batch Update
             # Note: We trigger a task to run the update on main thread context eventually,
             # but here we are likely in a background thread from AI Strategy?
             # Actually AI Strategy runs awaitable, so we are in async context.
             # We can't await here directly if this is called synchronously.
             # But on_result is usually called from async loop.
             
             # Schedule update if not already pending
             if not self._flush_pending:
                 self._flush_pending = True
                 try:
                     loop = asyncio.get_running_loop()
                     loop.create_task(self._flush_ai_buffer())
                 except RuntimeError:
                     if self._main_loop and self._main_loop.is_running():
                         asyncio.run_coroutine_threadsafe(self._flush_ai_buffer(), self._main_loop)
                     else:
                         self._flush_pending = False
                         logger.error("Cannot schedule flush: No event loop available")


    async def _flush_ai_buffer(self):
        """Flush buffer to main DataFrame"""
        try:
            if not self._ai_buffer: return
            
            # Swap buffer to process safely
            current_batch = self._ai_buffer
            self._ai_buffer = []
            
            new_df = pd.DataFrame(current_batch)
            
            # Offload Concatenation
            if self._full_results is None or self._full_results.empty:
                 self._full_results = new_df
            else:
                 # Append
                 self._full_results = await ThreadPoolManager().run_async(
                     TaskType.CPU, 
                     pd.concat, 
                     [self._full_results, new_df], 
                     ignore_index=True
                 )
                 
            # Sort by Score (Best on top)
            if 'ai_score' in self._full_results.columns:
                 self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._sort_helper,
                    self._full_results,
                    'ai_score',
                    False
                 )
            
            self._update_pagination()
            
            # Notify View to Repaint
            if self.on_update: self.on_update()
            
            self._last_ai_update = time.time()
            
        except Exception as e:
            logger.error(f"Error flushing AI buffer: {e}", exc_info=True)
        finally:
            self._flush_pending = False

    async def export_results(self, folder="exports"):
        """Export current results to CSV"""
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"
            
        import os
        import datetime
        
        try:
            if not os.path.exists(folder):
                os.makedirs(folder)
                
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screener_results_{timestamp}.csv"
            filepath = os.path.join(folder, filename)
            
            # Run in thread
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_csv,
                filepath,
                index=False,
                encoding='utf-8-sig'
            )
            return filepath, None
        except Exception as e:
            logger.error(f"Export failed: {e}", exc_info=True)
            return None, str(e)

