"""
Strategy Manager — Auto-discovers strategies via @register_strategy decorator.

Adding a new strategy requires ONLY:
  1. Write the strategy class with @register_strategy("key") decorator
  2. Import the module in _import_all_strategies() below
  3. Add i18n keys for name/desc (validated at startup)
"""

import logging
import threading
import typing

from strategies.base_strategy import get_strategy_registry
from core.i18n import I18n
from utils.singleton_registry import register_singleton

logger = logging.getLogger(__name__)

_strategies_imported = False


def _import_all_strategies():
    """Import all strategy modules to trigger @register_strategy.

    This is called lazily by StrategyManager.__init__ to avoid
    import-time side effects.
    """
    global _strategies_imported
    if _strategies_imported:
        return
    _strategies_imported = True

    import strategies.ai_strategy  # noqa: E402
    import strategies.fundamental  # noqa: E402
    import strategies.market  # noqa: E402
    import strategies.oversold_strategy  # noqa: E402, F401


@register_singleton
class StrategyManager:
    """
    Singleton manager for strategy instances.

    Provides:
    - Lazy strategy discovery via @register_strategy decorator
    - i18n validation at startup
    - Cached dependency checking for UI performance
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def __init__(self):
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            _import_all_strategies()
            self.strategies = {}
            registry = get_strategy_registry()
            for k, cls in registry.items():
                instance = cls()
                instance.key = k
                self.strategies[k] = instance

            logger.info(
                f"[StrategyManager] Loaded {len(self.strategies)} strategies: {list(self.strategies.keys())}",
            )
            self._validate_i18n()

            self._dependency_cache: dict[str, dict] | None = None

            self._initialized = True

    def _validate_i18n(self):
        """Startup validation — warn if any strategy is missing i18n keys."""
        for key, s in self.strategies.items():
            name_val = I18n.get(s.name_key)
            desc_val = I18n.get(s.desc_key)
            if name_val == s.name_key:
                logger.warning(
                    f"[StrategyManager] Missing i18n key: '{s.name_key}' (strategy: {key})",
                )
            if desc_val == s.desc_key:
                logger.warning(
                    f"[StrategyManager] Missing i18n key: '{s.desc_key}' (strategy: {key})",
                )

    def get_strategy(self, key: typing.Any):
        return self.strategies.get(key)

    def get_all_names(self):
        return {k: I18n.get(v.name_key) for k, v in self.strategies.items()}

    def get_strategy_params(self, key: typing.Any):
        """Get dynamic parameter definitions for a strategy."""
        s = self.strategies.get(key)
        return s.get_parameters() if s else []

    def invalidate_dependency_cache(self) -> None:
        """
        Invalidate the dependency check cache.

        Call this when TushareClient capability cache changes (after probe or token change).
        Thread-safe: uses _lock internally.
        """
        with self._lock:
            self._dependency_cache = None
            logger.debug("[StrategyManager] Dependency cache invalidated")

    def get_all_with_dependencies(self, force_refresh: bool = False) -> dict[str, dict]:
        """
        Get all strategies with API dependency status (cached).

        Results are cached and only recomputed when:
        - force_refresh=True
        - invalidate_dependency_cache() was called
        - Cache is None (first call)

        Thread-safe: uses _lock for cache access.

        Returns:
            {
                key: {
                    "name": str,           # Display name (i18n)
                    "missing_apis": list,  # APIs that are known unavailable
                }
            }

        Note:
            Only checks required_apis, not required_tables or required_context_keys.
            Table/context availability is checked at runtime with actual data.
        """
        with self._lock:
            if self._dependency_cache is not None and not force_refresh:
                return self._dependency_cache

        from data.external.tushare_client import TushareClient

        client = TushareClient()
        results = {}

        for key, strategy in self.strategies.items():
            missing_apis = []
            for api in getattr(strategy, "required_apis", []):
                if client.is_api_available(api) is False:
                    missing_apis.append(api)

            results[key] = {
                "name": I18n.get(strategy.name_key),
                "missing_apis": missing_apis,
            }

        with self._lock:
            if self._dependency_cache is None or force_refresh:
                self._dependency_cache = results
            return self._dependency_cache
