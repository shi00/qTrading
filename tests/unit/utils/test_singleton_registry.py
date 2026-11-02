"""
Tests for singleton_registry module.

S5-2: Unified singleton reset registry.
"""

# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest

pytestmark = pytest.mark.unit


class TestSingletonRegistry:
    """S5-2: Singleton registry for unified reset"""

    @pytest.fixture(autouse=True)
    def _inject_caplog(self, caplog):
        self.caplog = caplog

    @pytest.fixture
    def _dedicated_registry(self):
        """Temporarily swap the global registry for a test-local one.

        reset_all_singletons() iterates the module-level ``_registry`` list. Swapping
        it keeps this test from touching real singleton instances/resources, and the
        ``finally`` block restores the original object even if an assertion fails.
        """
        import utils.singleton_registry as singleton_registry

        with singleton_registry._lock:
            original_registry = singleton_registry._registry
            dedicated_registry: list[type[object]] = []
            singleton_registry._registry = dedicated_registry
        try:
            yield dedicated_registry
        finally:
            with singleton_registry._lock:
                singleton_registry._registry = original_registry

    def test_register_singleton_decorator(self):
        """register_singleton should add class to registry"""
        from utils.singleton_registry import register_singleton, _registry, _lock

        @register_singleton
        class DummySingleton:
            _instance = None

            @classmethod
            def _reset_singleton(cls):
                cls._instance = None

        assert DummySingleton in _registry

        with _lock:
            _registry.remove(DummySingleton)

    def test_reset_all_singletons(self):
        """reset_all_singletons should call _reset_singleton on all registered"""
        from utils.singleton_registry import (
            register_singleton,
            reset_all_singletons,
            _registry,
            _lock,
        )

        reset_called = []

        @register_singleton
        class DummyForReset:
            _instance = None

            @classmethod
            def _reset_singleton(cls):
                reset_called.append(cls.__name__)
                cls._instance = None

        reset_all_singletons()

        assert "DummyForReset" in reset_called

        with _lock:
            _registry.remove(DummyForReset)

    def test_reset_all_handles_missing_reset(self):
        """reset_all_singletons should handle classes without _reset_singleton"""
        from utils.singleton_registry import (
            register_singleton,
            reset_all_singletons,
            _registry,
            _lock,
        )

        @register_singleton
        class DummyNoReset:
            _instance = None

        reset_all_singletons()

        assert DummyNoReset._instance is None

        with _lock:
            _registry.remove(DummyNoReset)

    def test_get_registered_singletons(self):
        """get_registered_singletons should return class names"""
        from utils.singleton_registry import (
            register_singleton,
            get_registered_singletons,
            _registry,
            _lock,
        )

        @register_singleton
        class DummyForList:
            _instance = None

            @classmethod
            def _reset_singleton(cls):
                cls._instance = None

        names = get_registered_singletons()
        assert "DummyForList" in names

        with _lock:
            _registry.remove(DummyForList)

    def test_reset_all_handles_exception(self):
        """reset_all_singletons should not crash if _reset_singleton raises"""
        from utils.singleton_registry import (
            register_singleton,
            reset_all_singletons,
            _registry,
            _lock,
        )

        @register_singleton
        class DummyCrashy:
            _instance = None

            @classmethod
            def _reset_singleton(cls):
                raise RuntimeError("boom")

        reset_all_singletons()

        with _lock:
            _registry.remove(DummyCrashy)

    def test_reset_logs_error_for_missing_reset_singleton(self):
        """A-P1-5: reset_all_singletons should log ERROR when singleton lacks _reset_singleton"""
        import logging

        from utils.singleton_registry import (
            register_singleton,
            reset_all_singletons,
            _registry,
            _lock,
        )

        @register_singleton
        class DummyNoResetMethod:
            _instance = "some_instance"

        with _lock:
            _registry.remove(DummyNoResetMethod)

        with _lock:
            _registry.append(DummyNoResetMethod)

        with self.caplog.at_level(logging.ERROR, logger="utils.singleton_registry"):
            reset_all_singletons()

        assert any(
            "lacks _reset_singleton" in r.message or "no _reset_singleton" in r.message.lower()
            for r in self.caplog.records
        ), f"Expected ERROR log about missing _reset_singleton, got: {[r.message for r in self.caplog.records]}"

        with _lock:
            _registry.remove(DummyNoResetMethod)

    def test_reset_calls_close_before_nuking_instance(self):
        """A-P1-5: reset_all_singletons should call close() if available before setting _instance=None"""
        from utils.singleton_registry import (
            register_singleton,
            reset_all_singletons,
            _registry,
            _lock,
        )

        close_called = []

        @register_singleton
        class DummyWithClose:
            _instance = None

            def close(self):
                close_called.append(True)

        DummyWithClose._instance = DummyWithClose()

        with _lock:
            _registry.remove(DummyWithClose)
        with _lock:
            _registry.append(DummyWithClose)

        reset_all_singletons()

        assert close_called, "close() should have been called before _instance was set to None"
        assert DummyWithClose._instance is None

        with _lock:
            _registry.remove(DummyWithClose)

    def test_reset_handles_close_exception(self, _dedicated_registry):
        """A-P1-5: reset_all_singletons should handle close() exceptions gracefully."""
        import logging

        from utils.singleton_registry import reset_all_singletons

        class FailingCloseSingleton:
            _instance = None
            close_calls: list[str] = []

            def close(self):
                type(self).close_calls.append("close")
                raise RuntimeError("close failed")

        class NextResetSingleton:
            _instance = None
            reset_calls: list[str] = []

            @classmethod
            def _reset_singleton(cls):
                cls.reset_calls.append("reset")
                cls._instance = None

        FailingCloseSingleton._instance = FailingCloseSingleton()
        _dedicated_registry.extend([FailingCloseSingleton, NextResetSingleton])

        with self.caplog.at_level(logging.WARNING, logger="utils.singleton_registry"):
            reset_all_singletons()

        assert FailingCloseSingleton.close_calls == ["close"], "close() should be called exactly once"
        assert FailingCloseSingleton._instance is None, "_instance must be cleared after close() raises"
        assert NextResetSingleton.reset_calls == ["reset"], "reset must continue after close() raises"
        assert NextResetSingleton._instance is None

        warning_messages = [
            record.getMessage()
            for record in self.caplog.records
            if record.name == "utils.singleton_registry" and record.levelno == logging.WARNING
        ]
        assert any("FailingCloseSingleton.close() failed" in message for message in warning_messages), (
            f"Expected fallback close warning, got: {warning_messages}"
        )


class TestAtexitCleanupAll:
    """Tests for _atexit_cleanup_all centralized atexit handler"""

    @pytest.fixture(autouse=True)
    def _reset_module_flags(self):
        """重置模块级标志，避免 _graceful_shutdown_completed / _atexit_fired 跨测试泄漏。"""
        import utils.singleton_registry as mod

        mod._atexit_fired = False
        mod._graceful_shutdown_completed = False
        yield
        mod._atexit_fired = False
        mod._graceful_shutdown_completed = False

    def _cleanup(self, *classes):
        import utils.singleton_registry as mod

        with mod._lock:
            for cls in classes:
                if cls in mod._registry:
                    mod._registry.remove(cls)

    def test_skips_cleanup_when_graceful_shutdown_completed(self):
        """B10: 优雅停机已完成时，atexit 兜底短路，不再执行单例 _atexit_cleanup。"""
        import utils.singleton_registry as mod
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        cleanup_called = []

        class DummyAfterGraceful:
            @classmethod
            def _atexit_cleanup(cls):
                cleanup_called.append(cls.__name__)

        with _lock:
            _registry.append(DummyAfterGraceful)

        mod.mark_graceful_shutdown_completed()
        _atexit_cleanup_all()

        assert cleanup_called == [], "优雅停机完成后 atexit 兜底不应重复清理"

        self._cleanup(DummyAfterGraceful)

    def test_runs_cleanup_when_graceful_shutdown_not_completed(self):
        """B10: 未完成优雅停机（异常退出）时，atexit 兜底照常执行。"""
        import utils.singleton_registry as mod
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        cleanup_called = []

        class DummyNoGraceful:
            @classmethod
            def _atexit_cleanup(cls):
                cleanup_called.append(cls.__name__)

        with _lock:
            _registry.append(DummyNoGraceful)

        assert mod.is_graceful_shutdown_completed() is False
        _atexit_cleanup_all()

        assert "DummyNoGraceful" in cleanup_called

        self._cleanup(DummyNoGraceful)

    def test_mark_and_is_graceful_shutdown_completed(self):
        """B10: mark_graceful_shutdown_completed 置位后 is_graceful_shutdown_completed 返回 True。"""
        import utils.singleton_registry as mod

        mod._graceful_shutdown_completed = False
        assert mod.is_graceful_shutdown_completed() is False

        mod.mark_graceful_shutdown_completed()
        assert mod.is_graceful_shutdown_completed() is True

        mod._graceful_shutdown_completed = False

    def test_calls_atexit_cleanup_on_registered_singletons(self):
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        cleanup_called = []

        class DummyWithCleanup:
            @classmethod
            def _atexit_cleanup(cls):
                cleanup_called.append(cls.__name__)

        with _lock:
            _registry.append(DummyWithCleanup)

        _atexit_cleanup_all()

        assert "DummyWithCleanup" in cleanup_called

        self._cleanup(DummyWithCleanup)

    def test_skips_singletons_without_atexit_cleanup(self):
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        class DummyNoCleanup:
            pass

        with _lock:
            _registry.append(DummyNoCleanup)

        _atexit_cleanup_all()

        self._cleanup(DummyNoCleanup)

    def test_handles_exceptions_from_atexit_cleanup_gracefully(self):
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        class DummyCrashyCleanup:
            @classmethod
            def _atexit_cleanup(cls):
                raise RuntimeError("cleanup boom")

        with _lock:
            _registry.append(DummyCrashyCleanup)

        _atexit_cleanup_all()

        self._cleanup(DummyCrashyCleanup)

    def test_only_runs_once(self):
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        cleanup_count = []

        class DummyOnce:
            @classmethod
            def _atexit_cleanup(cls):
                cleanup_count.append(1)

        with _lock:
            _registry.append(DummyOnce)

        _atexit_cleanup_all()
        _atexit_cleanup_all()

        assert len(cleanup_count) == 1

        self._cleanup(DummyOnce)

    def test_iterates_in_reverse_registration_order(self):
        from utils.singleton_registry import _atexit_cleanup_all, _registry, _lock

        call_order = []

        class DummyFirst:
            @classmethod
            def _atexit_cleanup(cls):
                call_order.append("first")

        class DummySecond:
            @classmethod
            def _atexit_cleanup(cls):
                call_order.append("second")

        class DummyThird:
            @classmethod
            def _atexit_cleanup(cls):
                call_order.append("third")

        with _lock:
            _registry.append(DummyFirst)
            _registry.append(DummySecond)
            _registry.append(DummyThird)

        _atexit_cleanup_all()

        assert call_order == ["third", "second", "first"]

        self._cleanup(DummyFirst, DummySecond, DummyThird)
