"""AKShare East-Money concept board client.

Wraps sync AKShare concept board APIs as async, rate-limited, thread-pool-offloaded
coroutines for use inside the application's async pipeline.

Design notes:
- Singleton registered via @register_singleton (R15).
- AKShare is lazily imported to avoid forcing a hard startup dependency on the SDK.
- All sync AKShare calls are submitted to ThreadPoolManager IO pool (R16).
- TokenBucket limits call rate to 1 QPS (capacity=2 to absorb short bursts).
- @log_async_operation triggers slow-operation warnings past EXTERNAL_NETWORK threshold.
- asyncio.CancelledError is never caught: BaseException bypasses ``except Exception``
  in the decorator, so it propagates naturally (R2).
"""

import logging
import threading
from typing import Any

import pandas as pd

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.rate_limiter import TokenBucket
from utils.singleton_registry import register_singleton
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@register_singleton
class AkshareConceptClient:
    """Singleton client for AKShare East-Money concept board APIs.

    Provides async wrappers around ``ak.stock_board_concept_name_em()`` (list of
    concept boards) and ``ak.stock_board_concept_cons_em(symbol=...)`` (constituents
    of a concept board).

    Rate limiting: 1 QPS with burst capacity of 2 (东财公开接口反爬较松，仍需限速).
    """

    _instance: "AkshareConceptClient | None" = None
    _initialized: bool = False
    _lock = threading.Lock()

    # 1 QPS, capacity 2 — keeps well under 东财 concept endpoint soft limits.
    _RATE_LIMIT_PER_SEC: float = 1.0
    _RATE_LIMIT_CAPACITY: float = 2.0

    def __new__(cls, *args: Any, **kwargs: Any) -> "AkshareConceptClient":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def _reset_singleton(cls) -> None:
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    @classmethod
    def _atexit_cleanup(cls) -> None:
        """No persistent resources to release; defined for registry protocol compliance.

        TokenBucket holds only in-process state, so there is nothing to dispose at
        process exit. Defined as a no-op so ``singleton_registry._atexit_cleanup_all``
        can iterate uniformly over all registered singletons.
        """
        return

    def __init__(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._rate_limiter: TokenBucket = self._build_rate_limiter()
            self.__class__._initialized = True
            logger.info(
                "[AkshareConceptClient] initialized: rate=%.1f QPS, capacity=%.0f",
                self._RATE_LIMIT_PER_SEC,
                self._RATE_LIMIT_CAPACITY,
            )

    def _build_rate_limiter(self) -> TokenBucket:
        """Build the TokenBucket used to throttle AKShare calls.

        Separated as a method so tests can patch the limiter without re-implementing
        ``__init__``.
        """
        return TokenBucket(
            start_tokens=self._RATE_LIMIT_CAPACITY,
            capacity=self._RATE_LIMIT_CAPACITY,
            rate=self._RATE_LIMIT_PER_SEC,
        )

    @staticmethod
    def _get_akshare() -> Any:
        """Lazy-import the akshare module.

        Imported lazily so that the application startup path does not pay the
        (significant) import cost of akshare unless concept data is actually
        requested. Tests patch this method to inject a mock module.
        """
        import akshare as ak  # local import: avoid startup-time hard dependency

        return ak

    @log_async_operation(
        operation_name="akshare_get_concept_list",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_concept_list(self) -> pd.DataFrame:
        """Fetch the list of East-Money concept boards (东财概念板块列表).

        Returns:
            DataFrame with concept board metadata (板块名称, 板块代码, ...).

        Raises:
            Exception: Any AKShare/transport error propagates to the caller after
                being logged by ``@log_async_operation``.
        """
        await self._rate_limiter.consume_async(1)

        def _fetch() -> pd.DataFrame:
            ak = self._get_akshare()
            return ak.stock_board_concept_name_em()

        return await ThreadPoolManager().run_async(TaskType.IO, _fetch)

    @log_async_operation(
        operation_name="akshare_get_concept_constituents",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_concept_constituents(self, symbol: str) -> pd.DataFrame:
        """Fetch constituents of a specific East-Money concept board.

        Args:
            symbol: Concept board name (e.g. ``"锂电池"``).

        Returns:
            DataFrame with constituent stock metadata (代码, 名称, ...).

        Raises:
            Exception: Any AKShare/transport error propagates to the caller after
                being logged by ``@log_async_operation``.
        """
        await self._rate_limiter.consume_async(1)

        def _fetch() -> pd.DataFrame:
            ak = self._get_akshare()
            return ak.stock_board_concept_cons_em(symbol=symbol)

        return await ThreadPoolManager().run_async(TaskType.IO, _fetch)
