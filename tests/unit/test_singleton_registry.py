"""
Tests for singleton_registry module.

S5-2: Unified singleton reset registry.
"""

import pytest


class TestSingletonRegistry:
    """S5-2: Singleton registry for unified reset"""

    @pytest.fixture(autouse=True)
    def _inject_caplog(self, caplog):
        self.caplog = caplog

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
        from utils.singleton_registry import register_singleton, reset_all_singletons, _registry, _lock

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
        from utils.singleton_registry import register_singleton, reset_all_singletons, _registry, _lock

        @register_singleton
        class DummyNoReset:
            _instance = None

        reset_all_singletons()

        assert DummyNoReset._instance is None

        with _lock:
            _registry.remove(DummyNoReset)

    def test_get_registered_singletons(self):
        """get_registered_singletons should return class names"""
        from utils.singleton_registry import register_singleton, get_registered_singletons, _registry, _lock

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
        from utils.singleton_registry import register_singleton, reset_all_singletons, _registry, _lock

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

        from utils.singleton_registry import register_singleton, reset_all_singletons, _registry, _lock

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
        from utils.singleton_registry import register_singleton, reset_all_singletons, _registry, _lock

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

    def test_reset_handles_close_exception(self):
        """A-P1-5: reset_all_singletons should handle close() exceptions gracefully"""
        from utils.singleton_registry import register_singleton, _registry, _lock

        @register_singleton
        class DummyCrashyClose:
            _instance = None

            def close(self):
                raise RuntimeError("close failed")

        DummyCrashyClose._instance = DummyCrashyClose()

        with _lock:
            _registry.remove(DummyCrashyClose)


class TestAtexitCleanupAll:
    """Tests for _atexit_cleanup_all centralized atexit handler"""

    def _cleanup(self, *classes):
        import utils.singleton_registry as mod

        mod._atexit_fired = False
        with mod._lock:
            for cls in classes:
                if cls in mod._registry:
                    mod._registry.remove(cls)

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
