import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from ui.i18n import I18n

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
# ============================================================================

_STRATEGY_REGISTRY: Dict[str, type] = {}


def register_strategy(key: str):
    """
    Decorator to auto-register a strategy class.
    The key is used as the strategy identifier in StrategyManager.
    """
    def decorator(cls):
        if key in _STRATEGY_REGISTRY:
            logger.warning(f"[StrategyRegistry] Duplicate key '{key}' — overwriting {_STRATEGY_REGISTRY[key].__name__} with {cls.__name__}")
        _STRATEGY_REGISTRY[key] = cls
        return cls
    return decorator


class BaseStrategy(ABC):
    # Declare min trading days of history required for this strategy to run correctly.
    # Subclasses override as needed. 0 = snapshot-only, no historical dependency.
    required_history_days: int = 0

    def __init__(self, name_key: str, desc_key: str):
        self._name_key = name_key
        self._desc_key = desc_key

    @property
    def name(self) -> str:
        return I18n.get(self._name_key)
    
    @property
    def description(self) -> str:
        return I18n.get(self._desc_key)

    def get_dynamic_description(self, current_params: dict) -> str:
        """
        Return a dynamic description based on current UI parameters.
        By default, it just returns the static I18n description.
        Override in subclasses if the description needs to reflect slider values.
        """
        return self.description

    def get_parameters(self) -> List[Dict[str, Any]]:
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

    @abstractmethod
    async def filter(self, context: dict):
        """
        Execute strategy logic.
        :param context: Dict containing 'screening_data' DataFrame,
                        'data_processor', 'params' (user-defined parameters), etc.
        :return: Filtered DataFrame
        """
        pass
