"""Contract tests for StrategyManager._reset_singleton module-level state reset.

Verifies M10-003 fix: _reset_singleton now resets the module-level
_strategies_imported flag (in addition to _instance/_initialized) so that
the next StrategyManager() instantiation re-triggers _import_all_strategies().

Verifies M10-004 fix: _reset_singleton additionally restores _STRATEGY_REGISTRY
to the real-strategy snapshot captured at first import, so mock strategies
registered during a test are cleared while real strategy class identity is
preserved (isinstance does not mismatch).

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

    def test_reset_singleton_restores_registry_to_real_snapshot(self):
        """M10-004: reset 后注册表恢复到真实策略快照，mock 策略被清除且类身份不变。

        单测中直接写入注册表的 mock 策略在 _reset_singleton 后消失；真实策略
        集合与类对象均与快照一致（类身份复用，isinstance 不失配）。
        """
        from strategies import all_strategies, base_strategy
        from strategies.all_strategies import StrategyManager

        # 建立快照：触发一次真实导入（不 patch _import_all_strategies / get_strategy_registry）
        StrategyManager._reset_singleton()
        with patch.object(StrategyManager, "_validate_i18n"):
            StrategyManager()
        assert all_strategies._real_strategy_snapshot is not None
        real_keys = set(all_strategies._real_strategy_snapshot.keys())
        assert real_keys, "真实策略快照不应为空"

        # 模拟测试期间的 mock 策略注册
        class _MockStrategy:
            pass

        with base_strategy._REGISTRY_LOCK:
            base_strategy._STRATEGY_REGISTRY["_test_mock_strategy"] = _MockStrategy
        assert "_test_mock_strategy" in base_strategy._STRATEGY_REGISTRY

        StrategyManager._reset_singleton()

        restored = dict(base_strategy._STRATEGY_REGISTRY)
        assert "_test_mock_strategy" not in restored, "mock 策略应被清除"
        assert set(restored.keys()) == real_keys, "真实策略集合应完整保留"
        for k in real_keys:
            assert restored[k] is all_strategies._real_strategy_snapshot[k], (
                f"策略 '{k}' 类身份应与快照一致（isinstance 不失配）"
            )

    def test_reset_strategy_registry_noop_without_snapshot(self):
        """快照未建立时 _reset_strategy_registry 幂等安全，不做任何修改。"""
        from strategies import all_strategies, base_strategy
        from strategies.all_strategies import StrategyManager

        original_snapshot = all_strategies._real_strategy_snapshot
        try:
            all_strategies._real_strategy_snapshot = None
            before = dict(base_strategy._STRATEGY_REGISTRY)
            StrategyManager._reset_singleton()
            assert dict(base_strategy._STRATEGY_REGISTRY) == before, "快照为 None 时注册表不应被修改"
        finally:
            all_strategies._real_strategy_snapshot = original_snapshot
