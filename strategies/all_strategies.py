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

from strategies.base_strategy import _REGISTRY_LOCK, _STRATEGY_REGISTRY, get_strategy_registry
from core.i18n import I18n
from utils.singleton_registry import register_singleton

logger = logging.getLogger(__name__)

_strategies_imported = False

# M10-004: 首次真实导入后的策略注册表快照。Python import 幂等，清空注册表后
# 重新 import 不会重新触发 @register_strategy，因此必须以快照方式保留真实策略，
# 供 _reset_strategy_registry 恢复，同时保证类身份不变（isinstance 不失配）。
_real_strategy_snapshot: dict[str, type] | None = None


def _import_all_strategies():
    """Import all strategy modules to trigger @register_strategy.

    This is called lazily by StrategyManager.__init__ to avoid
    import-time side effects.
    """
    global _strategies_imported, _real_strategy_snapshot
    if _strategies_imported:
        return
    _strategies_imported = True

    import strategies.ai_strategy  # noqa: E402
    import strategies.fundamental  # noqa: E402
    import strategies.market  # noqa: E402
    import strategies.oversold_strategy  # noqa: E402, F401

    # 仅首次导入时缓存真实策略快照，之后不再更新（恢复目标固定为真实策略集合）。
    # 守卫保证快照只捕获一次：即使某测试在注入 mock 后触发真实导入，也不会把
    # mock 吸入全局快照，避免持久污染后续测试的恢复目标。
    # M10 回归修复：快照捕获直接读 _STRATEGY_REGISTRY 内部状态，绕过可被 mock 的
    # get_strategy_registry()（经公开函数捕获时，测试 patch 可注入空 dict，导致空快照
    # 被吸入全局快照，后续 _reset_strategy_registry 用空快照清空真实注册表）。
    # 加非空校验兜底：空注册表时保持快照为 None，_reset_strategy_registry 走 noop。
    if _real_strategy_snapshot is None:
        with _REGISTRY_LOCK:
            if _STRATEGY_REGISTRY:
                _real_strategy_snapshot = dict(_STRATEGY_REGISTRY)


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
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_strategy_registry(cls):
        """Restore _STRATEGY_REGISTRY to the real-strategy snapshot (R7 / M10-004).

        测试隔离：_reset_singleton 后注册表恢复到首次导入时的真实策略快照，清除
        测试期间直接写入注册表的 mock 策略，同时复用真实策略类对象（类身份不变，
        isinstance 不失配）。快照未建立（从未真实导入）时不做任何事，幂等安全。
        """
        from strategies.base_strategy import _STRATEGY_REGISTRY, _REGISTRY_LOCK

        if _real_strategy_snapshot is None:
            return
        with _REGISTRY_LOCK:
            _STRATEGY_REGISTRY.clear()
            _STRATEGY_REGISTRY.update(_real_strategy_snapshot)

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production.

        R7 合规：除重置 _instance/_initialized 外，同时重置模块级 _strategies_imported
        标志，并恢复策略注册表到真实策略快照（清除测试期间 mock 策略），确保下次
        实例化时重新触发 _import_all_strategies() 且真实策略完整保留。
        """
        global _strategies_imported
        with cls._lock:
            cls._instance = None
            cls._initialized = False
            _strategies_imported = False
        cls._reset_strategy_registry()

    @classmethod
    def _atexit_cleanup(cls):
        """No-op: strategies are stateless instances, no persistent resources to clean."""
        pass

    def __init__(self):
        # CON-01: double-checked locking。__new__ 持锁创建实例，但 __init__ 在锁外执行会
        # 导致并发首次访问时两线程都见 _initialized=False 而重复初始化（策略实例被重复构造）。
        # 初始化整体移入锁内并二次检查，保证恰好执行一次。
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
                "[StrategyManager] Loaded %d strategies: %s",
                len(self.strategies),
                list(self.strategies.keys()),
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
                    "[StrategyManager] Missing i18n key: '%s' (strategy: %s)",
                    s.name_key,
                    key,
                )
            if desc_val == s.desc_key:
                logger.warning(
                    "[StrategyManager] Missing i18n key: '%s' (strategy: %s)",
                    s.desc_key,
                    key,
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
