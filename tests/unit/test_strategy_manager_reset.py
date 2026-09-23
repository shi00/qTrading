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

import ast
from pathlib import Path
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

    def test_real_snapshot_not_polluted_by_mocked_registry(self):
        """M10 回归契约：快照捕获直接读内部 _STRATEGY_REGISTRY，不受 mock 污染。

        复现 #542 CI 失败场景：get_strategy_registry 被测试 patch 返回空 dict 时，
        若 _real_strategy_snapshot 经该公开函数捕获会吸入空快照，导致
        _reset_strategy_registry 用空快照清空真实注册表（Python import 幂等，
        模块已缓存无法重新触发 @register_strategy，注册表永久为空）。
        修复后必须直接读内部注册表，且仅在非空时捕获。
        """
        from strategies import all_strategies, base_strategy
        from strategies.all_strategies import StrategyManager

        # 确保真实策略模块已导入、注册表非空（幂等：已导入则无副作用）
        import strategies.ai_strategy  # noqa: F401
        import strategies.fundamental  # noqa: F401
        import strategies.market  # noqa: F401
        import strategies.oversold_strategy  # noqa: F401

        with base_strategy._REGISTRY_LOCK:
            assert base_strategy._STRATEGY_REGISTRY, "真实策略注册表不应为空"

        # 重置到「首次导入」状态：快照未建立 + 导入标志复位
        StrategyManager._reset_singleton()
        with base_strategy._REGISTRY_LOCK:
            all_strategies._real_strategy_snapshot = None

        # 模拟 M10 回归场景：get_strategy_registry 被 mock 返回空 dict
        with patch("strategies.all_strategies.get_strategy_registry") as mock_get_reg:
            mock_get_reg.return_value = {}
            with patch.object(StrategyManager, "_validate_i18n"):
                StrategyManager()  # 触发真实导入（_import_all_strategies 未 patch）

        # 快照必须捕获内部真实注册表，而非被 mock 的空 dict
        assert all_strategies._real_strategy_snapshot is not None, "注册表非空时应捕获快照"
        assert all_strategies._real_strategy_snapshot, "快照不应为空 dict（不应被 mock 污染）"
        assert "value" in all_strategies._real_strategy_snapshot, "快照应包含真实策略 'value'"

        # 重置链验证：清空注册表后 reset 应能从真实快照完整恢复（R7 契约）
        with base_strategy._REGISTRY_LOCK:
            base_strategy._STRATEGY_REGISTRY.clear()
        StrategyManager._reset_singleton()
        with base_strategy._REGISTRY_LOCK:
            restored = dict(base_strategy._STRATEGY_REGISTRY)
        assert restored == all_strategies._real_strategy_snapshot, "注册表应恢复到真实快照"


class TestImportAllStrategiesSideEffects:
    """守卫 _import_all_strategies 的副作用导入完整性（OSS E1 回归）。

    背景（PR #1149 检视 P1）：ruff RUF100 清理死 noqa 时误删了函数体内 4 个
    策略模块的副作用导入；因生产链路仅此一处导入点（无 strategies/__init__.py），
    删除导致 11/12 策略在生产环境静默消失，而单测因顶层直接 import 掩盖了缺口。
    本测试用静态守卫 + 运行时契约双向防护，lint 自动清理再次删除时立即红灯。
    """

    # 与 strategies/all_strategies.py: _import_all_strategies 中副作用导入一一对应
    _SIDE_EFFECT_MODULES = (
        "strategies.ai_strategy",
        "strategies.fundamental",
        "strategies.market",
        "strategies.oversold_strategy",
    )
    # 上述模块 @register_strategy 注册的策略 key（装饰器注册名，见各模块头部）
    _EXPECTED_STRATEGY_KEYS = {
        "ai_active",
        "value",
        "growth",
        "dividend",
        "cashflow",
        "large_pe",
        "volume_breakout",
        "northbound_holding",
        "northbound_flow",
        "institutional",
        "block_trade",
        "oversold",
    }

    def _all_strategies_source(self) -> str:
        """定位 all_strategies.py 源文件并返回文本（不依赖 cwd）。"""
        source_path = Path(__file__).resolve().parents[2] / "strategies" / "all_strategies.py"
        assert source_path.exists(), f"all_strategies.py 源文件不存在: {source_path}"
        return source_path.read_text(encoding="utf-8")

    def test_side_effect_imports_present_in_source(self):
        """AST 守卫：_import_all_strategies 函数体必须保留全部副作用导入。

        Import 存在性守卫：若 lint 自动清理再次删除这些 import（本次回归根因），
        本测试直接红灯，不依赖测试进程是否已导入模块。
        """
        tree = ast.parse(self._all_strategies_source())
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_import_all_strategies")
        imported = [n.names[0].name for n in ast.walk(func) if isinstance(n, ast.Import)]
        for mod in self._SIDE_EFFECT_MODULES:
            assert mod in imported, f"_import_all_strategies 缺少副作用导入 {mod}（生产策略将不注册）"

    def test_registry_contains_all_production_strategies_after_import(self):
        """运行时契约：真实导入后注册表必须包含全部生产策略 key（子集断言防 mock 干扰）。"""
        from strategies import all_strategies, base_strategy
        from strategies.all_strategies import StrategyManager

        StrategyManager._reset_singleton()  # 复位导入标志，强制走一次真实导入
        with patch.object(StrategyManager, "_validate_i18n"):
            StrategyManager()
        with base_strategy._REGISTRY_LOCK:
            registered = set(base_strategy._STRATEGY_REGISTRY.keys())
        missing = self._EXPECTED_STRATEGY_KEYS - registered
        assert not missing, f"以下策略未注册（副作用导入缺失）：{sorted(missing)}"
