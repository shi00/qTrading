"""Cache layer - 缓存管理"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager


def __getattr__(name):
    if name == "CacheManager":
        from data.cache.cache_manager import CacheManager

        return CacheManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["CacheManager"]
