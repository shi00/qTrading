"""
S5-2 fix: Singleton Registry for unified reset across tests.

All singleton classes with _reset_singleton should register here
so tests can call reset_all_singletons() to guarantee isolation.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_registry: list[type] = []
_lock = threading.Lock()


def register_singleton(cls: type) -> type:
    """Class decorator that registers a singleton for unified reset."""
    with _lock:
        if cls not in _registry:
            _registry.append(cls)
    return cls


def reset_all_singletons() -> None:
    """Reset all registered singletons. Intended for test teardown."""
    with _lock:
        for cls in list(_registry):
            if hasattr(cls, "_reset_singleton"):
                try:
                    cls._reset_singleton()
                except Exception as e:
                    logger.warning(f"[SingletonRegistry] Failed to reset {cls.__name__}: {e}")
            elif hasattr(cls, "_instance"):
                cls._instance = None


def get_registered_singletons() -> list[str]:
    """Return names of all registered singletons (for diagnostics)."""
    with _lock:
        return [cls.__name__ for cls in _registry]
