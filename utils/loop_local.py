import asyncio
import logging
import weakref
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_stores: dict[str, weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]] = {}


def _get_store(key: str) -> weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]:
    if key not in _stores:
        _stores[key] = weakref.WeakKeyDictionary()
    return _stores[key]


def get_loop_local(key: str, factory: Callable[[], Any], *, strict: bool = False) -> Any:
    store = _get_store(key)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        if strict:
            raise RuntimeError(
                f"get_loop_local('{key}') called outside event loop in strict mode. "
                f"Callers must ensure they are inside an async context."
            ) from exc
        logger.error(
            f"[loop_local] get_loop_local('{key}') called outside event loop; "
            f"factory() invoked but result will NOT be cached. "
            f"Callers must ensure they are inside an async context.",
        )
        return factory()

    if loop not in store:
        store[loop] = factory()
    return store[loop]


def del_loop_local(key: str) -> None:
    store = _stores.get(key)
    if store is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    store.pop(loop, None)


def clear_all_loop_locals() -> None:
    _stores.clear()
