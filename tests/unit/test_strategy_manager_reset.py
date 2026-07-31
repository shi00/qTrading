"""Contract tests for StrategyManager._reset_singleton module-level state reset.

Verifies M10-003 fix: _reset_singleton now resets the module-level
_strategies_imported flag (in addition to _instance/_initialized) so that
the next StrategyManager() instantiation re-triggers _import_all_strategies().

Scope note:
    _STRATEGY_REGISTRY is intentionally NOT cleared by _reset_singleton.
    Python import is idempotent — clearing the registry would prevent real
    strategies from being re-registered (the @register_strategy decorator
    only fires on first import). Tests needing isolated strategy registration
    should patch get_strategy_registry or use unique mock keys that don't
    collide with real strategy keys.

These tests validate the contract documented in
strategies/all_strategies.py:StrategyManager._reset_singleton docstring.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class TestStrategyManagerResetModuleState:
    """Validate _reset_singleton resets module-level _strategies_imported flag.

    M10-003 root cause: prior implementation only reset _instance/_initialized,
    leaving _strategies_imported=True persisted across tests, which short-circuited
    _import_all_strategies() on subsequent instantiations.
    """

    def test_reset_singleton_resets_strategies_imported_flag(self):
        """_reset_singleton must reset _strategies_imported to False."""
        from strategies import all_strategies
        from strategies.all_strategies import StrategyManager

        all_strategies._strategies_imported = True
        assert all_strategies._strategies_imported is True

        StrategyManager._reset_singleton()

        assert all_strategies._strategies_imported is False

    def test_reset_singleton_clears_instance(self):
        """_reset_singleton must reset _instance to None (legacy contract).

        Note: _initialized is an instance attribute (set via self._initialized
        in __init__), not a class attribute. After reset, _instance is None,
        so the instance (and its _initialized flag) is no longer reachable.
        """
        from strategies.all_strategies import StrategyManager

        with patch("strategies.all_strategies._import_all_strategies"):
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_registry.return_value = {}
                with patch.object(StrategyManager, "_validate_i18n"):
                    mgr = StrategyManager()
                    assert StrategyManager._instance is mgr
                    assert mgr._initialized is True

        StrategyManager._reset_singleton()

        assert StrategyManager._instance is None

    def test_reset_singleton_allows_reimport_on_next_instantiation(self):
        """After reset, next StrategyManager() must re-trigger _import_all_strategies.

        This is the regression contract for M10-003: previously
        _strategies_imported=True persisted, so _import_all_strategies was
        short-circuited on subsequent instantiations.
        """
        from strategies.all_strategies import StrategyManager

        StrategyManager._reset_singleton()

        with patch("strategies.all_strategies._import_all_strategies") as mock_import:
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_registry.return_value = {}
                with patch.object(StrategyManager, "_validate_i18n"):
                    StrategyManager()

                    mock_import.assert_called_once()  # noqa: weak-assertion _import_all_strategies() 无参数，调用次数是唯一可验证契约

    def test_reset_singleton_is_thread_safe(self):
        """_reset_singleton must hold _lock during state reset (R11/best practice)."""
        from strategies.all_strategies import StrategyManager

        @patch.object(StrategyManager, "_lock")
        def _assert_lock_used(mock_lock):
            mock_lock.__enter__.return_value = None
            StrategyManager._reset_singleton()
            mock_lock.__enter__.assert_called_once()  # noqa: weak-assertion Lock.__enter__() 无参数，调用次数是唯一可验证契约
            mock_lock.__exit__.assert_called_once_with(None, None, None)  # 无异常时 exc_type/exc_val/exc_tb 均为 None

        _assert_lock_used()

    def test_strategies_imported_flag_set_to_true_after_real_instantiation(self):
        """_strategies_imported must be True after StrategyManager() instantiation.

        This complements the reset test: verifying the flag lifecycle is
        False (initial) -> True (after instantiation) -> False (after reset).
        """
        from strategies import all_strategies
        from strategies.all_strategies import StrategyManager

        StrategyManager._reset_singleton()
        assert all_strategies._strategies_imported is False

        with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
            mock_registry.return_value = {}
            with patch.object(StrategyManager, "_validate_i18n"):
                StrategyManager()

        assert all_strategies._strategies_imported is True
