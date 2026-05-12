import logging
import threading

import pytest

from utils.loop_local import get_loop_local, del_loop_local, clear_all_loop_locals


@pytest.fixture(autouse=True)
def cleanup_stores():
    yield
    clear_all_loop_locals()


class TestGetLoopLocal:
    @pytest.mark.asyncio
    async def test_caches_within_same_loop(self):
        call_count = [0]

        def factory():
            call_count[0] += 1
            return object()

        result1 = get_loop_local("test_key", factory)
        result2 = get_loop_local("test_key", factory)
        assert result1 is result2
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_different_keys_independent(self):
        result_a = get_loop_local("key_a", list)
        result_b = get_loop_local("key_b", list)
        assert result_a is not result_b

    def test_default_strict_outside_event_loop_raises(self):
        with pytest.raises(RuntimeError, match="strict mode"):
            get_loop_local("default_strict_key", list)

    def test_non_strict_outside_event_loop_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="utils.loop_local"):
            result = get_loop_local("no_loop_key", list, strict=False)
            assert isinstance(result, list)
            assert any("outside event loop" in r.message for r in caplog.records)

    def test_non_strict_outside_event_loop_caches_in_fallback(self):
        call_count = [0]

        def factory():
            call_count[0] += 1
            return call_count[0]

        result1 = get_loop_local("fallback_cache_key", factory, strict=False)
        result2 = get_loop_local("fallback_cache_key", factory, strict=False)
        assert result1 == result2
        assert call_count[0] == 1


class TestDelLoopLocal:
    @pytest.mark.asyncio
    async def test_delete_existing_key(self):
        get_loop_local("del_key", list)
        del_loop_local("del_key")
        result = get_loop_local("del_key", list)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key_no_error(self):
        del_loop_local("nonexistent_key")

    def test_delete_outside_event_loop_no_error(self):
        del_loop_local("outside_key")


class TestClearAllLoopLocals:
    @pytest.mark.asyncio
    async def test_clear_all(self):
        get_loop_local("clear_a", list)
        get_loop_local("clear_b", dict)
        clear_all_loop_locals()
        result = get_loop_local("clear_a", list)
        assert isinstance(result, list)


class TestGetLoopLocalStrict:
    def test_strict_outside_event_loop_raises(self):
        with pytest.raises(RuntimeError, match="strict mode"):
            get_loop_local("strict_key", list, strict=True)

    @pytest.mark.asyncio
    async def test_strict_inside_event_loop_works(self):
        result = get_loop_local("strict_in_loop", list, strict=True)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_strict_caches_within_same_loop(self):
        call_count = [0]

        def factory():
            call_count[0] += 1
            return object()

        result1 = get_loop_local("strict_cache", factory, strict=True)
        result2 = get_loop_local("strict_cache", factory, strict=True)
        assert result1 is result2
        assert call_count[0] == 1

    def test_non_strict_outside_event_loop_still_works(self):
        result = get_loop_local("non_strict_key", list, strict=False)
        assert isinstance(result, list)

    def test_strict_error_message_contains_key(self):
        with pytest.raises(RuntimeError, match="my_special_key"):
            get_loop_local("my_special_key", list, strict=True)


class TestLoopLocalFallbackThreadSafety:
    """C-P0-3/D-P0-1: Verify fallback cache is thread-safe when accessed
    from multiple threads outside an event loop."""

    def test_concurrent_fallback_access_single_factory_call(self):
        call_count = [0]

        def factory():
            call_count[0] += 1
            return object()

        results = []
        errors = []

        def worker():
            try:
                r = get_loop_local("thread_safe_key", factory, strict=False)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors in threads: {errors}"
        assert call_count[0] == 1, f"Factory called {call_count[0]} times, expected 1"
        assert all(r is results[0] for r in results), "All threads should get the same cached object"

    def test_del_loop_local_clears_fallback_from_another_thread(self):
        get_loop_local("del_fallback_key", list, strict=False)
        del_loop_local("del_fallback_key")

        call_count = [0]

        def factory():
            call_count[0] += 1
            return list()

        result = get_loop_local("del_fallback_key", factory, strict=False)
        assert isinstance(result, list)
        assert call_count[0] == 1

    def test_clear_all_resets_fallback_across_threads(self):
        get_loop_local("clear_fallback_a", list, strict=False)
        get_loop_local("clear_fallback_b", dict, strict=False)
        clear_all_loop_locals()

        call_count = [0]

        def factory():
            call_count[0] += 1
            return list()

        result = get_loop_local("clear_fallback_a", factory, strict=False)
        assert isinstance(result, list)
        assert call_count[0] == 1
