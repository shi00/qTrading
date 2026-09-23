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

from data.external.akshare_rate_limiter import (
    AKSHARE_RATE_LIMIT_CAPACITY,
    AKSHARE_RATE_LIMIT_PER_SEC,
    get_akshare_rate_limiter,
)
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

    Rate limiting: module-level shared TokenBucket (1 QPS with burst capacity of 2,
    review08-B4) — NewsFetcher's sync akshare kernel consumes the same bucket, so
    all akshare outbound calls share one limiter (东财公开接口反爬较松，仍需限速).
    """

    _instance: "AkshareConceptClient | None" = None
    _initialized: bool = False
    _lock = threading.Lock()

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
                "[AkshareConceptClient] initialized: rate=%.1f QPS, capacity=%.0f (module-level shared)",
                AKSHARE_RATE_LIMIT_PER_SEC,
                AKSHARE_RATE_LIMIT_CAPACITY,
            )

    def _build_rate_limiter(self) -> TokenBucket:
        """Return the module-level shared TokenBucket (review08-B4).

        All akshare outbound calls (concept client + NewsFetcher sync kernel)
        consume the same bucket. Kept as a method so tests can patch the limiter
        without re-implementing ``__init__``.
        """
        return get_akshare_rate_limiter()

    @staticmethod
    def _get_akshare() -> Any:
        """Lazy-import the akshare module.

        Imported lazily so that the application startup path does not pay the
        (significant) import cost of akshare unless concept data is actually
        requested. Tests patch this method to inject a mock module.
        """
        import akshare as ak  # local import: avoid startup-time hard dependency

        return ak

    @staticmethod
    def _clear_concept_list_cache() -> None:
        """Clear AKShare's process-level lru_cache on the East-Money concept list.

        AKShare's ``stock_board_concept_name_em()`` returns
        ``__stock_board_concept_name_em().copy()`` where the inner loader is
        ``@lru_cache``-decorated with no TTL and a single cache slot. That cache
        outlives the whole process (no ``cache_clear`` call anywhere in the
        module), so a second call in a long-lived run would otherwise return the
        first-call snapshot instead of hitting the network — silently freezing
        the concept list (review08-B1, P0).

        We therefore clear it right before each fetch so scheduler / nightly
        jobs always see fresh data. Depending on AKShare's private function name
        is intentional and differs from the review08-B3 anti-pattern: B3 coupled
        four layers of internals and degraded *silently* to the (correct-by-luck)
        value it wanted; here we depend on a single internal symbol, and
        ``pyproject.toml`` pins ``akshare==1.18.97``. A structure change only
        breaks the cache-clear (logged at WARNING), never silently corrupts the
        fetched data.
        """
        try:
            import akshare.stock.stock_board_concept_em as _em

            # 必须经 getattr 取 "__stock_board_concept_name_em"：在类方法内直接写
            # ``_em.__stock_board_concept_name_em`` 会被 Python name-mangle 成
            # ``_em._AkshareConceptClient__stock_board_concept_name_em``，永远
            # AttributeError，导致缓存清理静默失效。字符串字面量不做 mangle。
            getattr(_em, "__stock_board_concept_name_em").cache_clear()
        except (ImportError, AttributeError) as exc:
            # AKShare 内部实现变化/被裁剪时降级：仅记录，不让同步流程崩溃。
            logger.warning(
                "[AkshareConceptClient] 无法清理概念列表缓存（akshare 结构变更?），本次可能返回陈旧列表: %s",
                exc,
            )

    @log_async_operation(
        operation_name="akshare_get_concept_list",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_concept_list(self) -> pd.DataFrame:
        """Fetch the list of East-Money concept boards (东财概念板块列表).

        AKShare's ``stock_board_concept_name_em()`` wraps a module-level
        ``@lru_cache``-decorated loader with no TTL and a single cache slot (see
        ``_clear_concept_list_cache``). This client clears that internal cache
        before each fetch so long-lived processes (scheduler + nightly jobs)
        always get fresh data instead of the first-call snapshot.

        Returns:
            DataFrame with concept board metadata (板块名称, 板块代码, ...).

        Raises:
            Exception: Any AKShare/transport error propagates to the caller after
                being logged by ``@log_async_operation``.
        """
        await self._rate_limiter.consume_async(1)

        def _fetch() -> pd.DataFrame:
            ak = self._get_akshare()
            self._clear_concept_list_cache()
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
