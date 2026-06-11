import asyncio
import inspect
import logging
import time
from collections.abc import Callable

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.persistence.quality_gate import QualityGateError
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from strategies.all_strategies import StrategyManager
from ui.i18n import I18n
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

# Language-neutral constant for task name matching between ViewModel and View.
# Must NOT be i18n'd — both sides use this as a programmatic identifier.
TASK_NAME_PREFIX = "strategy_screening"


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
        self._full_results: pd.DataFrame | None = None
        self.page_no = 1
        self.page_size = 50
        self.total_pages = 0
        self.total_items = 0

        # Sorting State
        self.sort_column: str | None = None
        self.sort_ascending = True

        # AI Stream Buffer
        self._ai_buffer = []
        self._discarded_buffer = []  # U-3 fix: Buffer for discarded items during HISTORY mode
        self._last_ai_update = 0
        self.AI_UPDATE_INTERVAL = 0.5  # Seconds
        self._flush_pending = False

        # History Mode State
        self.mode = "REALTIME"  # "REALTIME" or "HISTORY"
        self._realtime_snapshot = None  # Snapshot for mode switching

        # Callbacks (View binders)
        self.on_update: Callable | None = None
        self.on_log: Callable[[str, int, str], None] | None = None
        self.on_status: Callable[[str, str], None] | None = None
        self.on_progress: Callable[[float], None] | None = None
        self.on_log_stream_start: Callable[[str], Callable] | None = None
        self.on_ai_card_start: Callable[[str], None] | None = None
        self.on_task_unlock: Callable[[], None] | None = None
        self._main_loop = None
        self._background_tasks: set = set()
        self._threadsafe_futures: set = set()

        # TaskManager subscription state
        self._strategy_submitted = False

    def bind(
        self,
        on_update,
        on_log,
        on_status,
        on_progress,
        on_log_stream_start=None,
        on_ai_card_start=None,
        on_task_unlock=None,
    ):
        self.on_update = on_update
        self.on_log = on_log
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_log_stream_start = on_log_stream_start
        self.on_ai_card_start = on_ai_card_start
        self.on_task_unlock = on_task_unlock
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("ScreenerViewModel bound freely without loop")

    def init(self):
        """Initialize resources"""
        pass

    def dispose(self):
        """Cleanup resources and ensure aggressive GC of large dataframes"""
        self.unsubscribe_task_manager()
        self.on_update = None
        self.on_log = None
        self.on_status = None
        self.on_progress = None
        self.on_log_stream_start = None
        self.on_ai_card_start = None
        self.on_task_unlock = None
        self._main_loop = None

        for f in self._threadsafe_futures:
            f.cancel()
        self._threadsafe_futures.clear()

        self._full_results = None
        self._ai_buffer = []
        self._realtime_snapshot = None

    # --- Data Actions ---

    async def get_strategies(self) -> dict[str, str]:
        return self.strategy_mgr.get_all_names()

    def get_strategy_desc(self, key: str) -> str:
        st = self.strategy_mgr.get_strategy(key)
        return I18n.get(st.desc_key) if st else ""

    def get_strategy_params(self, key: str) -> list:
        """Get dynamic parameter definitions for a strategy."""
        # Defensive copy to prevent mutating a strategy's cached class attributes
        params = list(self.strategy_mgr.get_strategy_params(key))

        # Inject AI System Prompt override parameter globally so ALL strategies can use it.
        # Check to avoid duplicate if a strategy still happens to implement it natively.
        if not any(p.get("name") == "ai_system_prompt" for p in params):
            params.append(
                {
                    "name": "ai_system_prompt",
                    "label_key": "ai_system_prompt",
                    "type": "textarea",
                    "default": "",  # UI uses get_base_prompt to map the value dynamically
                },
            )

        return params

    async def run_strategy(
        self,
        strategy_key: str,
        save_results: bool = True,
        params: dict = None,  # type: ignore[untyped]
    ):
        """Execute strategy screening via the global TaskManager."""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        strategy = self.strategy_mgr.get_strategy(strategy_key)
        if not strategy:
            logger.error(f"[ScreenerVM] Strategy not found: {strategy_key}")
            if self.on_status:
                self.on_status(I18n.get("screener_strategy_not_found"), "red")
            return

        # Define the inner coroutine for the task manager
        async def _execute_screening(task_id: str, **kwargs):
            try:
                # 1. Prepare Context (may trigger massive data load)
                TaskManager().update_progress(
                    task_id,
                    0.05,
                    I18n.get("task_loading_data"),
                )
                context = await self.data_processor.get_strategy_data()
                if not context:
                    TaskManager().update_progress(
                        task_id,
                        0.1,
                        I18n.get("task_cache_empty_init"),
                    )
                    await self.data_processor.init_data()
                    context = await self.data_processor.get_strategy_data()

                if not context or "screening_data" not in context or context["screening_data"].empty:
                    raise RuntimeError("No valid screening data available")

                diagnostics = context.get("_diagnostics") if isinstance(context, dict) else None
                if isinstance(diagnostics, dict) and diagnostics.get("strategy_ready") is False:
                    table_status = diagnostics.get("table_status") or {}
                    not_ready = [
                        key
                        for key, status in table_status.items()
                        if isinstance(status, dict) and not status.get("ready", True)
                    ]
                    if self.on_status:
                        detail = f": {', '.join(not_ready)}" if not_ready else ""
                        self.on_status(f"{I18n.get('strategy_dep_degraded')}{detail}", "orange")

                context["data_processor"] = self.data_processor
                context["params"] = params or {}  # Dynamic strategy parameters from UI

                # Setup AI Callbacks
                # (Forward updates both to ViewModel local UI and Global TaskManager)
                def _combined_ai_progress(current, total, msg):
                    self._on_ai_progress(current, total, msg)  # For local View UI
                    TaskManager().update_progress(
                        task_id,
                        current / total if total > 0 else 0,
                        f"[{current}/{total}] {msg}",
                    )

                context["on_progress"] = _combined_ai_progress
                context["on_result"] = self._on_ai_result_stream
                if self.on_log_stream_start:
                    context["on_stream_start"] = self.on_log_stream_start
                if self.on_ai_card_start:
                    context["on_card_start"] = self.on_ai_card_start

                # We inject the task_id into context so deep AI tasks can check cancellation
                context["_task_id"] = task_id

                TaskManager().update_progress(
                    task_id,
                    0.2,
                    I18n.get("task_executing_strategy", name=I18n.get(strategy.name_key)),
                )

                if inspect.iscoroutinefunction(strategy.filter):
                    # Async strategy (e.g. PolarsBaseStrategy) — CPU-intensive work
                    # is already offloaded inside the strategy's filter() method,
                    # so awaiting here only blocks for IO (thread pool result, AI API calls)
                    result_df = await strategy.filter(context)
                else:
                    # Sync strategy — offload entire filter() to CPU thread pool
                    result_df = await ThreadPoolManager().run_async(
                        TaskType.CPU,
                        strategy.filter,
                        context,
                    )

                TaskManager().update_progress(
                    task_id,
                    0.95,
                    I18n.get("task_aggregating_results"),
                )

                if result_df is not None and not result_df.empty:
                    self._full_results = result_df
                    self._update_pagination()

                    if save_results:
                        analysis_trade_date = context.get("trade_date")
                        if not analysis_trade_date:
                            raise RuntimeError(
                                "Missing analysis trade_date in screening context; refusing to save results",
                            )
                        import uuid as _uuid

                        run_id = _uuid.uuid4().hex[:16]
                        await self.review_mgr.save_results(
                            I18n.get(strategy.name_key),
                            result_df,
                            trade_date=analysis_trade_date,
                            run_id=run_id,
                            params_snapshot=params or {},
                        )

                    # Update Local View
                    if self.on_update:
                        self.on_update()
                    if self.on_status:
                        self.on_status(
                            I18n.get("screener_done_saved").format(
                                count=len(result_df),
                            ),
                            "green",
                        )
                    if self.on_progress:
                        self.on_progress(False)
                    return I18n.get("task_screening_success", count=len(result_df))
                self._full_results = pd.DataFrame()
                if self.on_update:
                    self.on_update()
                if self.on_status:
                    self.on_status(I18n.get("screener_no_results"), "orange")
                if self.on_progress:
                    self.on_progress(False)
                return I18n.get("screener_no_results")

            except asyncio.CancelledError:
                if self.on_status:
                    self.on_status(I18n.get("screener_cancelled"), "orange")
                if self.on_progress:
                    self.on_progress(False)
                raise
            except QualityGateError as e:
                logger.warning(
                    f"[ScreenerVM] Strategy execution blocked by Quality Gate: {e}",
                )
                if self.on_status:
                    self.on_status(
                        I18n.get("screener_blocked", reason=str(e)),
                        "orange",
                    )
                if self.on_progress:
                    self.on_progress(False)
                return I18n.get("screener_blocked", reason=str(e))
            except Exception as e:
                logger.error(
                    f"[ScreenerVM] Strategy execution failed: {e}",
                    exc_info=True,
                )
                # Show generic user-friendly message, avoid raw traceback on UI
                safe_msg = I18n.get("screener_internal_error")
                if self.on_status:
                    self.on_status(
                        I18n.get("screener_exec_error").format(error=safe_msg),
                        "red",
                    )
                if self.on_progress:
                    self.on_progress(False)
                raise RuntimeError(f"Strategy execution crashed: {e}") from e

        # Reset Local UI State
        self._full_results = None
        self.page_no = 1
        self._ai_buffer = []
        if self.on_progress:
            self.on_progress(True)
        if self.on_status:
            self.on_status(
                I18n.get("screener_running_strategy").format(name=I18n.get(strategy.name_key)),
                "blue",
            )

        # Dispatch to TaskManager!
        task_id = TaskManager().submit_task(
            name=f"{TASK_NAME_PREFIX}: {I18n.get(strategy.name_key)}",
            task_type=I18n.get("task_type_ai_screening"),
            coroutine_factory=_execute_screening,
            cancellable=True,
        )

        if task_id is None:
            if self.on_progress:
                self.on_progress(False)
            if self.on_status:
                self.on_status(I18n.get("screener_task_rejected"), "orange")
        else:
            self._strategy_submitted = True

    # --- Sorting & Pagination ---

    async def sort_data(self, column_key: str, ascending: bool | None = None):
        """Sort data using ThreadPool to avoid blocking UI"""
        if self._full_results is None or self._full_results.empty:
            return

        if ascending is not None:
            self.sort_column = column_key
            self.sort_ascending = ascending
        elif self.sort_column == column_key:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column_key
            self.sort_ascending = True

        if self.on_progress:
            self.on_progress(True)

        try:
            # Offload sorting to thread
            sorted_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._sort_helper,
                self._full_results,
                column_key,
                self.sort_ascending,
            )

            self._full_results = sorted_df
            self.page_no = 1  # Reset to first page
            if self.on_update:
                self.on_update()

        except Exception as e:
            logger.error(f"Sort failed: {e}", exc_info=True)
        finally:
            if self.on_progress:
                self.on_progress(False)

    @staticmethod
    def _sort_helper(df, col, ascending):
        """Static helper for pickling/thread safety"""
        try:
            return df.sort_values(by=col, ascending=ascending, na_position="last")
        except KeyError:
            return df

    def change_page(self, delta: int):
        new_page = self.page_no + delta
        if 1 <= new_page <= self.total_pages:
            self.page_no = new_page
            if self.on_update:
                self.on_update()

    def change_page_size(self, new_size: int):
        """Update pagination size and jump back to page 1."""
        if new_size > 0 and new_size != self.page_size:
            self.page_size = new_size
            self.page_no = 1
            self._update_pagination()
            if self.on_update:
                self.on_update()

    def get_current_page_data(self):
        """Get data for current page (Synchronous, fast slicing)"""
        if self._full_results is None or self._full_results.empty:
            return pd.DataFrame()

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
            self.on_status(
                I18n.get("screener_ai_analyzing").format(
                    done=current,
                    total=total,
                    msg=msg,
                ),
                "blue",
            )

    def _on_ai_result_stream(self, row_data):
        """Buffer incoming AI results and update in batches"""
        if not row_data:
            return

        # 1. Update Log immediately (Log is strictly append, low cost if virtualized)
        if self.on_log:
            name = row_data.get("name", "Unknown")
            score = row_data.get("ai_score", 0)
            thinking = str(row_data.get("thinking", ""))
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
                    if not self._main_loop:
                        self._main_loop = loop
                    task = loop.create_task(self._flush_ai_buffer())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                except RuntimeError:
                    if self._main_loop and self._main_loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self._flush_ai_buffer(),
                            self._main_loop,
                        )
                        self._threadsafe_futures.add(future)
                        future.add_done_callback(lambda f: self._threadsafe_futures.discard(f))
                    else:
                        self._flush_pending = False
                        logger.error("Cannot schedule flush: No event loop available")

    async def _flush_ai_buffer(self):
        """Flush buffer to main DataFrame"""
        try:
            if not self._ai_buffer:
                return
            # U-3 fix: Race guard - save buffer to discarded_buffer if user has switched to history mode
            if self.mode != "REALTIME":
                self._discarded_buffer.extend(self._ai_buffer)
                self._ai_buffer = []
                self._flush_pending = False
                logger.debug(
                    f"[ScreenerVM] Saved {len(self._discarded_buffer)} items to discarded_buffer during HISTORY mode"
                )
                return

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
                    ignore_index=True,
                )

            # Sort by Score (Best on top)
            if "ai_score" in self._full_results.columns:
                self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._sort_helper,
                    self._full_results,
                    "ai_score",
                    False,
                )

                # Pin ai_score and ai_reason to the front (after name)
                cols = list(self._full_results.columns)  # type: ignore[untyped]
                # Remove if exists
                if "ai_score" in cols:
                    cols.remove("ai_score")
                if "ai_reason" in cols:
                    cols.remove("ai_reason")

                # Find insertion index (after 'name', or else at idx 1)
                insert_idx = cols.index("name") + 1 if "name" in cols else 1

                # Insert back
                cols.insert(insert_idx, "ai_score")
                cols.insert(insert_idx + 1, "ai_reason")

                self._full_results = self._full_results[cols]  # type: ignore[untyped]
            self._update_pagination()

            # Notify View to Repaint and re-eval button states (like export disabled)
            if self.on_update:
                self.on_update()

            self._last_ai_update = time.time()

        except Exception as e:
            logger.error(f"Error flushing AI buffer: {e}", exc_info=True)
        finally:
            self._flush_pending = False

    # --- History Mode ---

    def switch_to_history(self):
        """Switch to HISTORY mode, snapshot current realtime state."""
        if self.mode == "HISTORY":
            return
        # Snapshot realtime state
        self._realtime_snapshot = {
            "full_results": self._full_results,
            "page_no": self.page_no,
            "sort_column": self.sort_column,
            "sort_ascending": self.sort_ascending,
            "ai_buffer": self._ai_buffer[:],
        }
        # Clear for history data
        self._full_results = None
        self.page_no = 1
        self.sort_column = None
        self.sort_ascending = True
        self._ai_buffer = []
        self._update_pagination()
        self.mode = "HISTORY"
        logger.info("[ScreenerVM] Switched to HISTORY mode")

    def switch_to_realtime(self):
        """Switch back to REALTIME mode, restore snapshot."""
        if self.mode == "REALTIME":
            return
        # Restore snapshot
        if self._realtime_snapshot:
            self._full_results = self._realtime_snapshot["full_results"]
            self.page_no = self._realtime_snapshot["page_no"]
            self.sort_column = self._realtime_snapshot["sort_column"]
            self.sort_ascending = self._realtime_snapshot["sort_ascending"]
            self._ai_buffer = self._realtime_snapshot["ai_buffer"]
            self._realtime_snapshot = None
            # U-3 fix: Merge discarded_buffer back to ai_buffer
            if self._discarded_buffer:
                self._ai_buffer.extend(self._discarded_buffer)
                logger.debug(f"[ScreenerVM] Merged {len(self._discarded_buffer)} discarded items back to ai_buffer")
                self._discarded_buffer = []
            self._update_pagination()
        self.mode = "REALTIME"
        logger.info("[ScreenerVM] Switched to REALTIME mode")
        if self.on_update:
            self.on_update()

    async def load_history_tree(self, offset=0):
        """Load tree data for the history sidebar."""
        cache = CacheManager()
        df = await cache.get_history_tree(offset=offset)
        if df is None or df.empty:
            return {}
        # Group by trade_date -> {date: [{run_id, strategy_name, cnt}, ...]}
        tree = {}
        for _, row in df.iterrows():
            date = str(row["trade_date"])
            if date not in tree:
                tree[date] = []
            tree[date].append(
                {"run_id": row["run_id"], "strategy_name": row["strategy_name"], "cnt": int(row["cnt"])},  # type: ignore[untyped]
            )
        return tree

    async def load_history_data(self, trade_date: str, strategy_name: str = None, run_id: str = None):  # type: ignore[untyped]
        """Load historical screening records for a specific run_id, or fall back to trade_date/strategy_name."""
        cache = CacheManager()
        df = await cache.get_history_records(trade_date, strategy_name, run_id)
        if df is not None and not df.empty:
            self._full_results = df
        else:
            self._full_results = pd.DataFrame()
        self.page_no = 1
        if df is not None and not df.empty and "ai_score" in df.columns:
            self.sort_column = "ai_score"
        else:
            self.sort_column = None
        self.sort_ascending = False
        self._update_pagination()
        if self.on_update:
            self.on_update()

    def get_export_data(self):
        """Get the current results DataFrame for export"""
        if self._full_results is None or self._full_results.empty:
            return None
        return self._full_results

    async def export_results(self, filepath):
        """Export current results to CSV at the specified path"""
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_csv,
                filepath,
                index=False,
                encoding="utf-8-sig",
            )
            return filepath, None
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            return None, str(e)

    # --- TaskManager Subscription ---

    def subscribe_task_manager(self):
        """Subscribe to TaskManager for strategy task monitoring."""
        TaskManager().subscribe(self._on_tasks_updated)

    def unsubscribe_task_manager(self):
        """Unsubscribe from TaskManager."""
        TaskManager().unsubscribe(self._on_tasks_updated)

    def _on_tasks_updated(self, tasks: list):
        """TaskManager subscriber: detect strategy task completion and notify View."""
        running = [t for t in tasks if TASK_NAME_PREFIX in t.name and t.status.name in ("RUNNING", "QUEUED")]
        if not running and self._strategy_submitted:
            self._strategy_submitted = False
            if self.on_task_unlock:
                self.on_task_unlock()
