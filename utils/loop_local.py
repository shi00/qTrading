import asyncio
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_stores: dict[str, weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]] = {}
_fallback_store: dict[str, Any] = {}
_fallback_lock = threading.Lock()


def _get_store(key: str) -> weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]:
    if key not in _stores:
        _stores[key] = weakref.WeakKeyDictionary()
    return _stores[key]


def get_loop_local(key: str, factory: Callable[[], Any], *, strict: bool = True) -> Any:
    store = _get_store(key)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        if strict:
            raise RuntimeError(
                f"get_loop_local('{key}') called outside event loop in strict mode. "
                f"Callers must ensure they are inside an async context."
            ) from exc
        # strict=False: caller explicitly accepts fallback; log at DEBUG to avoid
        # noisy warnings and prevent ValueError during Python shutdown when log
        # streams are already closed (e.g. atexit handlers).
        try:
            logger.debug(
                f"[loop_local] get_loop_local('{key}') called outside event loop; using module-level fallback cache.",
            )
        except (ValueError, OSError):
            pass
        with _fallback_lock:
            if key not in _fallback_store:
                _fallback_store[key] = factory()
            return _fallback_store[key]

    if loop not in store:
        with _fallback_lock:
            # Double-check pattern to prevent race condition
            if loop in store:
                # Another thread already created the instance while we were waiting for the lock
                return store[loop]
            if key in _fallback_store:
                store[loop] = _fallback_store.pop(key)
                logger.debug(f"[loop_local] Migrated fallback instance for '{key}' to loop-local store.")
            else:
                store[loop] = factory()
    return store[loop]


def del_loop_local(key: str) -> None:
    store = _stores.get(key)
    if store is not None:
        try:
            loop = asyncio.get_running_loop()
            store.pop(loop, None)
        except RuntimeError:
            pass
    with _fallback_lock:
        _fallback_store.pop(key, None)


def clear_all_loop_locals() -> None:
    _stores.clear()
    with _fallback_lock:
        _fallback_store.clear()
