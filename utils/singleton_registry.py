"""
S5-2 fix: Singleton Registry for unified reset across tests.

All singleton classes with _reset_singleton should register here
so tests can call reset_all_singletons() to guarantee isolation.

C-P2-3 fix: Centralized atexit cleanup protocol.
Instead of each singleton registering its own atexit handler,
the registry provides a single atexit handler that iterates through
all registered singletons and calls their _atexit_cleanup() method.
This ensures cleanup order is controlled centrally and avoids
atexit firing in the wrong order relative to ShutdownCoordinator.
"""

import atexit
import logging
import threading

logger = logging.getLogger(__name__)

_registry: list[type[object]] = []
_lock = threading.Lock()
_atexit_fired = False


def _atexit_cleanup_all() -> None:
    """Centralized atexit handler. Called once by Python at process exit.

    Iterates through registered singletons in reverse registration order
    and calls _atexit_cleanup() if available. This replaces individual
    atexit registrations in each singleton.
    """
    global _atexit_fired
    if _atexit_fired:
        return
    _atexit_fired = True

    with _lock:
        for cls in reversed(list(_registry)):
            if hasattr(cls, "_atexit_cleanup"):
                try:
                    cls._atexit_cleanup()  # type: ignore[attr-defined]
                except Exception as e:
                    logger.warning(
                        "[SingletonRegistry] atexit cleanup failed for %s: %s", cls.__name__, e, exc_info=True
                    )


atexit.register(_atexit_cleanup_all)


def register_singleton[TClass: type](cls: TClass) -> TClass:
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
                    cls._reset_singleton()  # type: ignore[attr-defined]
                except Exception as e:
                    logger.warning("[SingletonRegistry] Failed to reset %s: %s", cls.__name__, e, exc_info=True)
            elif hasattr(cls, "_instance"):
                logger.error(
                    "[SingletonRegistry] %s lacks _reset_singleton — "
                    "falling back to _instance = None (resources may leak). "
                    "Implement _reset_singleton for proper cleanup.",
                    cls.__name__,
                )
                instance = cls._instance  # type: ignore[attr-defined]
                if instance is not None and hasattr(instance, "close"):
                    try:
                        instance.close()
                    except Exception as e:
                        logger.warning("[SingletonRegistry] %s.close() failed: %s", cls.__name__, e, exc_info=True)
                cls._instance = None  # type: ignore[attr-defined]


def get_registered_singletons() -> list[str]:
    """Return names of all registered singletons (for diagnostics)."""
    with _lock:
        return [cls.__name__ for cls in _registry]
