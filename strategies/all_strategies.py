"""
Strategy Manager — Auto-discovers strategies via @register_strategy decorator.

Adding a new strategy requires ONLY:
  1. Write the strategy class with @register_strategy("key") decorator
  2. Import the module here (one line)
  3. Add i18n keys for name/desc (validated at startup)
"""

import logging
from ui.i18n import I18n
from strategies.base_strategy import _STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

# ============================================================================
# IMPORTANT: Import each strategy module to trigger @register_strategy.
# This is the ONLY place you need to touch when adding a new strategy file.
# ============================================================================
import strategies.ai_strategy          # noqa: F401
import strategies.oversold_strategy    # noqa: F401
import strategies.fundamental          # noqa: F401
import strategies.market               # noqa: F401


class StrategyManager:
    def __init__(self):
        # Auto-instantiate all registered strategies
        self.strategies = {k: cls() for k, cls in _STRATEGY_REGISTRY.items()}
        logger.info(f"[StrategyManager] Loaded {len(self.strategies)} strategies: {list(self.strategies.keys())}")
        self._validate_i18n()

    def _validate_i18n(self):
        """Startup validation — warn if any strategy is missing i18n keys."""
        for key, s in self.strategies.items():
            name_val = I18n.get(s._name_key)
            desc_val = I18n.get(s._desc_key)
            # I18n.get returns the key itself if not found
            if name_val == s._name_key:
                logger.warning(f"[StrategyManager] ⚠ Missing i18n key: '{s._name_key}' (strategy: {key})")
            if desc_val == s._desc_key:
                logger.warning(f"[StrategyManager] ⚠ Missing i18n key: '{s._desc_key}' (strategy: {key})")

    def get_strategy(self, key):
        return self.strategies.get(key)

    def get_all_names(self):
        return {k: v.name for k, v in self.strategies.items()}

    def get_strategy_params(self, key):
        """Get dynamic parameter definitions for a strategy."""
        s = self.strategies.get(key)
        return s.get_parameters() if s else []
