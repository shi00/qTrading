import asyncio
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_stores: dict[str, weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]] = {}
_fallback_store: dict[str, Any] = {}
# 单一锁同时守护 _stores 与 _fallback_store 两个模块级 dict（无嵌套获取，无死锁风险）
_stores_lock = threading.Lock()


def _get_store(key: str) -> weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]:
    # 加锁原子化 get-or-create，避免并发首次访问同一 key 产生孤儿 store
    with _stores_lock:
        return _stores.setdefault(key, weakref.WeakKeyDictionary())


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
                "[loop_local] get_loop_local('%s') called outside event loop; using module-level fallback cache.",
                key,
            )
        except (ValueError, OSError):
            pass
        with _stores_lock:
            if key not in _fallback_store:
                _fallback_store[key] = factory()
            return _fallback_store[key]

    if loop not in store:
        with _stores_lock:
            # Double-check pattern to prevent race condition
            if loop in store:
                # Another thread already created the instance while we were waiting for the lock
                return store[loop]
            store[loop] = factory()
    return store[loop]


def del_loop_local(key: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 循环外：仅清 fallback，不触碰 loop store 条目
        with _stores_lock:
            _fallback_store.pop(key, None)
        return
    # 循环内：只清当前 loop 条目，绝不触碰 fallback（CON-11，R7 测试隔离语义）
    store = _stores.get(key)
    if store is not None:
        # 良性无锁单次原子 dict 操作（GIL 保证），无需加锁
        store.pop(loop, None)


def clear_all_loop_locals() -> None:
    # 与在途 get_loop_local 并发时，已取得 store 引用的调用方可能把实例写进
    # 已脱离 _stores 的孤儿 store——该对象随引用释放被 GC，无泄漏、无跨测试污染，
    # 且 clear 仅用于测试隔离/停机路径，不与之并发，属可接受窗口（方案已知边界 m-3）。
    with _stores_lock:
        _stores.clear()
        _fallback_store.clear()
