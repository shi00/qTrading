import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    name: str
    critical: bool
    ok: bool
    timed_out: bool
    elapsed_ms: float
    error: str = ""


_CLEANUP_STEPS = [
    ("Step 0", "_step0_cancel_tasks", True),
    ("Step 1", "_step1_stop_services", True),
    ("Step 2", "_step2_flush_db_writes", True),
    ("Step 3", "_step3_close_processor", True),
    ("Step 4", "_step4_clear_toast", False),
    ("Step 5", "_step5_unload_ai_model", True),
    ("Step 6", "_step6_shutdown_thread_pools", True),
]


class ShutdownCoordinator:
    """
    Centralized shutdown state and cleanup logic.

    Separated from main.py to enable unit testing without Flet dependencies.
    Usage in main.py:
        coordinator = ShutdownCoordinator(page)
        coordinator.start_watchdog(10)
        await coordinator.do_cleanup()
    """

    def __init__(
        self,
        page=None,
        *,
        service_stop_delay: float = 0.5,
        force_exit_callback: Callable[[int], None] | None = None,
        watchdog_timeout_s: float = 15.0,
    ):
        self._page = page
        self._cleanup_started = False
        self._cleanup_done = False
        self._cleanup_running = False
        self._cleanup_success = False
        self._cleanup_task = None
        self._step_results: list[StepResult] = []
        self._watchdog_started = False
        self._watchdog_cancel_event: threading.Event | None = None
        self._service_stop_delay = service_stop_delay
        self._watchdog_timeout_s = watchdog_timeout_s
        self._force_exit = force_exit_callback or self._default_force_exit

    @staticmethod
    def _default_force_exit(code: int) -> None:
        import sys

        for handler in logging.root.handlers:
            try:
                handler.flush()
            except (OSError, ValueError):
                pass
        logger.critical(
            f"[Shutdown] Force-exiting process with code {code}. Call stack and cleanup state have been logged above.",
        )
        try:
            sys.exit(code)
        except SystemExit:
            os._exit(code)

    @property
    def cleanup_done(self) -> bool:
        return self._cleanup_done

    @property
    def cleanup_success(self) -> bool:
        return self._cleanup_success

    @property
    def watchdog_started(self) -> bool:
        return self._watchdog_started

    @property
    def step_results(self) -> list[StepResult]:
        return self._step_results[:]

    def start_watchdog(self, timeout_s: float | None = None):
        if (
            self._watchdog_started
            and self._watchdog_cancel_event is not None
            and not self._watchdog_cancel_event.is_set()
        ):
            return
        effective_timeout = timeout_s if timeout_s is not None else self._watchdog_timeout_s
        self._watchdog_started = True
        self._watchdog_cancel_event = threading.Event()
        cancel_event = self._watchdog_cancel_event
        force_exit = self._force_exit

        def _force_exit_thread():
            if cancel_event.wait(effective_timeout):
                logger.info("[Shutdown] Watchdog canceled before timeout.")
                return
            step_snapshot = list(self._step_results)
            step_summary = [
                f"{r.name}(ok={r.ok}, timed_out={r.timed_out}, elapsed={r.elapsed_ms:.0f}ms"
                f"{', error=' + r.error if r.error else ''})"
                for r in step_snapshot
            ]
            logger.error(
                f"[Shutdown] Watchdog timeout ({effective_timeout}s) — forcing exit. "
                f"cleanup_done={self._cleanup_done}, "
                f"cleanup_running={self._cleanup_running}, "
                f"step_results={step_summary}",
            )
            force_exit(1)

        threading.Thread(target=_force_exit_thread, daemon=True).start()
        logger.info(f"[Shutdown] Watchdog armed ({effective_timeout}s).")

    def cancel_watchdog(self):
        if self._watchdog_cancel_event is not None:
            self._watchdog_cancel_event.set()
        self._watchdog_started = False
        self._watchdog_cancel_event = None

    async def do_cleanup(self, timeout_s: float = 8.0, step_timeout_s: float = 5.0) -> bool:
        """
        Core cleanup coroutine. Stops all background services, flushes DB writes, closes pools.

        Safety rules:
        - Never call singleton factories (e.g. DataProcessor()), only access Class._instance
        - No exit statements (sys.exit / os._exit), caller decides how to exit
        - Each step is independently wrapped; a critical failure is logged but does NOT
          skip remaining steps — resource-release steps (thread pools, AI model) always run
        - Returns False if any critical step failed, even if all steps were executed
        """
        if self._cleanup_done and self._cleanup_task is None:
            logger.info("[Shutdown] Cleanup already completed, skipping.")
            return self._cleanup_success

        if self._cleanup_task is None:
            self._cleanup_started = True
            self._cleanup_running = True
            logging.getLogger("asyncio").setLevel(logging.ERROR)
            logger.info(
                f"[Shutdown] ========== Graceful Shutdown Initiated =========="
                f" (timeout={timeout_s:.1f}s, step_timeout={step_timeout_s:.1f}s) =========="
            )
            self._cleanup_task = asyncio.create_task(
                self._execute_cleanup(timeout_s=timeout_s, step_timeout_s=step_timeout_s)
            )
        else:
            logger.info("[Shutdown] Cleanup already running, joining existing cleanup task.")

        return await asyncio.shield(self._cleanup_task)

    async def _execute_cleanup(self, timeout_s: float, step_timeout_s: float) -> bool:
        try:
            self._step_results = await asyncio.wait_for(
                self._run_cleanup_steps(step_timeout_s=step_timeout_s),
                timeout=timeout_s,
            )
            critical_failures = [r for r in self._step_results if r.critical and not r.ok]
            self._cleanup_success = len(critical_failures) == 0
            if self._cleanup_success:
                logger.info("[Shutdown] ========== Shutdown Sequence Complete ==========")
            else:
                failed_names = ", ".join(r.name for r in critical_failures)
                logger.error(f"[Shutdown] Shutdown completed with CRITICAL step failures: {failed_names}")
            return self._cleanup_success
        except asyncio.CancelledError:
            self._cleanup_success = False
            logger.warning("[Shutdown] Cleanup was cancelled externally.")
            return False
        except TimeoutError:
            self._cleanup_success = False
            logger.error(f"[Shutdown] Cleanup timed out after {timeout_s:.1f}s.")
            return False
        except Exception as ex:
            self._cleanup_success = False
            logger.error(f"[Shutdown] Cleanup failed unexpectedly: {ex}", exc_info=True)
            return False
        finally:
            self._cleanup_done = True
            self._cleanup_running = False
            self._cleanup_task = None
            self.cancel_watchdog()
            for handler in logging.root.handlers:
                try:
                    handler.flush()
                except (OSError, ValueError):
                    pass

    async def _run_cleanup_steps(self, step_timeout_s: float) -> list[StepResult]:
        results: list[StepResult] = []
        for name, method_name, critical in _CLEANUP_STEPS:
            step_func = getattr(self, method_name)
            result = await self._run_async_step(
                name=name,
                step=step_func,
                step_timeout_s=step_timeout_s,
                critical=critical,
            )
            results.append(result)
            if critical and not result.ok:
                logger.error(
                    f"[Shutdown] Critical step '{name}' failed. Continuing remaining steps to release resources."
                )
        return results

    async def _run_async_step(self, name: str, step, step_timeout_s: float, critical: bool) -> StepResult:
        start = time.perf_counter()
        try:
            await asyncio.wait_for(step(), timeout=step_timeout_s)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(f"[Shutdown] {name} done in {elapsed_ms:.1f}ms")
            return StepResult(
                name=name,
                critical=critical,
                ok=True,
                timed_out=False,
                elapsed_ms=elapsed_ms,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(f"[Shutdown] {name} cancelled, continuing with remaining steps")
            return StepResult(
                name=name,
                critical=critical,
                ok=False,
                timed_out=False,
                elapsed_ms=elapsed_ms,
                error="cancelled",
            )
        except TimeoutError as ex:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(f"[Shutdown] {name} timed out after {step_timeout_s:.1f}s")
            return StepResult(
                name=name,
                critical=critical,
                ok=False,
                timed_out=True,
                elapsed_ms=elapsed_ms,
                error=str(ex),
            )
        except Exception as ex:
            logger.error(f"[Shutdown] {name} failed: {ex}", exc_info=True)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return StepResult(
                name=name,
                critical=critical,
                ok=False,
                timed_out=False,
                elapsed_ms=elapsed_ms,
                error=str(ex),
            )

    async def _step0_cancel_tasks(self):
        logger.info("[Shutdown] Step 0: Cancelling managed tasks...")
        from services.task_manager import TaskManager

        if TaskManager._instance is not None:
            await TaskManager._instance.cancel_all_running_async()

    async def _step1_stop_services(self):
        logger.info("[Shutdown] Step 1: Stopping background services...")

        from data.domain_services.market_data_service import MarketDataService
        from data.external.news_subscription import NewsSubscriptionService
        from utils.scheduler_service import SchedulerService

        if SchedulerService._instance is not None:
            svc = SchedulerService._instance
            if hasattr(svc, "scheduler") and svc.scheduler.running:
                logger.info("[Shutdown]   - Scheduler")
                svc.stop()

        if NewsSubscriptionService._instance is not None:
            logger.info("[Shutdown]   - NewsSubscriptionService")
            await NewsSubscriptionService._instance.stop_async()

        if MarketDataService._instance is not None:
            logger.info("[Shutdown]   - MarketDataService")
            await MarketDataService._instance.stop_async()

        if self._service_stop_delay > 0:
            await asyncio.sleep(self._service_stop_delay)

    async def _step2_flush_db_writes(self):
        logger.info("[Shutdown] Step 2: Flushing pending DB writes...")
        from services.task_manager import TaskManager

        if TaskManager._instance is None:
            logger.info("[Shutdown]   - TaskManager not initialized, skipping flush.")
            return

        await TaskManager._instance.flush_persistence(timeout_s=1.5)
        logger.info("[Shutdown]   - Task persistence flush completed.")

    async def _step3_close_processor(self):
        logger.info("[Shutdown] Step 3: Closing DataProcessor...")
        from data.data_processor import DataProcessor

        if DataProcessor._instance is not None:
            await DataProcessor._instance.close()
            logger.info("[Shutdown]   - DataProcessor closed (includes DB engine disposal).")
        else:
            logger.info("[Shutdown]   - DataProcessor not initialized, skipping.")

    async def _step4_clear_toast(self):
        logger.info("[Shutdown] Step 4: Clearing Toast Manager...")
        page = self._page
        toast = getattr(page, "toast", None) if page is not None else None
        if toast is not None and hasattr(toast, "stop_all"):
            try:
                res = toast.stop_all()
                if asyncio.iscoroutine(res):
                    await res
                logger.info("[Shutdown]   - Toast Manager stopped.")
            except Exception as e:
                logger.debug(f"[Shutdown]   - Toast Manager cleanup skipped: {e}")
        else:
            logger.info("[Shutdown]   - Toast Manager not initialized, skipping.")

    async def _step5_unload_ai_model(self):
        logger.info("[Shutdown] Step 5: Unloading AI model...")
        await asyncio.to_thread(self._step5_unload_ai_model_sync)

    def _step5_unload_ai_model_sync(self):
        from services.local_model_manager import LocalModelManager

        if LocalModelManager._instance is not None and (
            LocalModelManager._instance._worker_ready or LocalModelManager._instance._model_path
        ):
            LocalModelManager._instance.unload_model()
            logger.info("[Shutdown]   - Llama.cpp model evicted (subprocess terminated).")
        else:
            logger.info("[Shutdown]   - AI model not loaded, skipping.")

    async def _step6_shutdown_thread_pools(self):
        logger.info("[Shutdown] Step 6: Shutting down Thread Pools...")
        await asyncio.to_thread(self._step6_shutdown_thread_pools_sync)

    def _step6_shutdown_thread_pools_sync(self):
        from utils.thread_pool import ThreadPoolManager

        if ThreadPoolManager._instance is not None:
            ThreadPoolManager._instance.shutdown(wait=False)
            logger.info("[Shutdown]   - Thread pools shut down.")
        else:
            logger.info("[Shutdown]   - Thread pools not initialized, skipping.")
