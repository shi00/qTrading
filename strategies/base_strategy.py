import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

from core.i18n import I18n
from strategies.utils import StrategyContext

logger = logging.getLogger(__name__)

# ============================================================================
# Strategy Auto-Registration (Decorator Pattern)
# ============================================================================
# Usage:
#   @register_strategy("oversold")
#   class OversoldStrategy(BaseStrategy):
#       ...
#
# StrategyManager reads _STRATEGY_REGISTRY to auto-discover all strategies.
# Thread-safe: uses RLock to protect concurrent registration during import.
# ============================================================================

_STRATEGY_REGISTRY: dict[str, type] = {}
_REGISTRY_LOCK = threading.RLock()


def register_strategy(key: str):
    """
    Decorator to auto-register a strategy class.
    The key is used as the strategy identifier in StrategyManager.
    Thread-safe: uses RLock to protect concurrent registration.
    """

    def decorator(cls):
        with _REGISTRY_LOCK:
            if key in _STRATEGY_REGISTRY:
                logger.warning(
                    "[StrategyRegistry] Duplicate key '%s' — overwriting %s with %s",
                    key,
                    _STRATEGY_REGISTRY[key].__name__,
                    cls.__name__,
                )
            _STRATEGY_REGISTRY[key] = cls
        return cls

    return decorator


def get_strategy_registry() -> dict[str, type]:
    """Return a copy of the strategy registry for thread-safe read access."""
    with _REGISTRY_LOCK:
        return _STRATEGY_REGISTRY.copy()


class BaseStrategy(ABC):
    required_history_days: int = 0

    required_context_keys: tuple[str, ...] = ()
    required_tables: tuple[str, ...] = ()
    required_apis: tuple[str, ...] = ()

    CONTEXT_KEY_TABLE_MAP: dict[str, str] = {
        "northbound_data": "northbound_holding",
        "northbound_flow_data": "moneyflow_hsgt",
        "moneyflow_data": "moneyflow_daily",
        "top_list": "top_list",
        "block_trade": "block_trade",
        "screening_data": "daily_quotes",
        "fundamental_screening_data": "financial_reports",
    }

    def __init__(self, name_key: str, desc_key: str):
        self._name_key = name_key
        self._desc_key = desc_key
        super().__init__()

    @property
    def name_key(self) -> str:
        return self._name_key

    @property
    def desc_key(self) -> str:
        return self._desc_key

    @property
    def name(self) -> str:
        return self._name_key

    @property
    def description(self) -> str:
        return self._desc_key

    def get_dynamic_description(self, current_params: dict) -> str:
        """
        Return a dynamic description based on current UI parameters.
        By default, it just returns the static I18n description.
        Override in subclasses if the description needs to reflect slider values.

        # [PITFALL_WARNING] E2E 测试断言避坑指南
        # 坑点：测试工程师在写 E2E 时，经常图省事直接用 `I18n.get(self.desc_key)` 去页面上断言策略描述。
        # 原因：部分策略（如 OversoldStrategy）会重写此方法，返回带有滑动条参数（如 RSI < 30）的动态字符串。
        # 后果：如果 UI 渲染的是带参动态文本，而测试代码还在傻傻等待静态文本，必然导致 TimeoutError。
        # 正确做法：在 E2E 测试中断言策略描述时，必须去对应策略子类检查其是否重写了此方法，
        #         如果是，必须使用 `.format(...)` 构造出真正的动态预期字符串后再进行 expect_text。
        """
        return I18n.get(self.desc_key)

    def get_parameters(self) -> list[dict[str, Any]]:
        """
        Declare dynamic parameters for this strategy.
        Override in subclasses to expose tunable parameters to the UI.

        Returns a list of parameter definitions, e.g.:
        [
            {
                "name": "rsi_threshold",       # Internal parameter name
                "label_key": "param_rsi_threshold",  # I18n key for UI label
                "type": "slider",              # "slider" | "number" | "dropdown"
                "min": 10,                     # For slider/number
                "max": 40,
                "default": 20,
                "step": 1,                     # For slider
            }
        ]

        Default: returns empty list (no tunable parameters).
        """
        return []

    def check_dependencies(self, context: StrategyContext) -> dict[str, Any]:
        """
        Validate strategy dependencies against the provided context.
        Returns a dict with:
          - 'ready': bool — True if all required dependencies are present
          - 'status': 'ready' | 'degraded' | 'unready'
          - 'missing_keys': list of missing context keys
          - 'missing_tables': list of missing table names
          - 'missing_apis': list of unavailable APIs
          - 'empty_keys': list of context keys present but with empty data
        """
        missing_keys = []
        missing_tables = []
        missing_apis = []
        empty_keys = []

        for key in self.required_context_keys:
            data = context.get(key)
            if data is None:
                missing_keys.append(key)
                table_name = self.CONTEXT_KEY_TABLE_MAP.get(key)
                if table_name and table_name not in missing_tables:
                    missing_tables.append(table_name)
            elif hasattr(data, "empty") and data.empty:
                empty_keys.append(key)

        for table in self.required_tables:
            if table not in missing_tables:
                found = False
                for key, mapped_table in self.CONTEXT_KEY_TABLE_MAP.items():
                    if mapped_table == table:
                        data = context.get(key)
                        if data is not None and not (hasattr(data, "empty") and data.empty):
                            found = True
                            break
                        elif data is None and key not in missing_keys:
                            missing_keys.append(key)
                if not found and table not in missing_tables:
                    missing_tables.append(table)

        for api in self.required_apis:
            from data.external.tushare_client import TushareClient

            client = TushareClient()
            if client.is_api_available(api) is False:
                missing_apis.append(api)

        if missing_keys or missing_apis:
            status = "unready"
        elif empty_keys:
            status = "degraded"
        else:
            status = "ready"

        return {
            "ready": status == "ready",
            "status": status,
            "missing_keys": missing_keys,
            "missing_tables": missing_tables,
            "missing_apis": missing_apis,
            "empty_keys": empty_keys,
        }

    @abstractmethod
    async def filter(self, context: StrategyContext):
        """
        Execute strategy logic.
        :param context: StrategyContext dict (see strategies.utils.StrategyContext)
        :return: Filtered DataFrame
        """
        pass
