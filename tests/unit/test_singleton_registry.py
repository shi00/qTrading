"""
Tests for singleton_registry module.

S5-2: Unified singleton reset registry.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestSingletonRegistry:
    """S5-2: Singleton registry for unified reset"""

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
