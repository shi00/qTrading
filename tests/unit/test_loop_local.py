import logging

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

    def test_outside_event_loop_logs_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="utils.loop_local"):
            result = get_loop_local("no_loop_key", list)
            assert isinstance(result, list)
            assert any("outside event loop" in r.message for r in caplog.records)

    def test_outside_event_loop_no_caching(self):
        call_count = [0]

        def factory():
            call_count[0] += 1
            return call_count[0]

        result1 = get_loop_local("no_cache_key", factory)
        result2 = get_loop_local("no_cache_key", factory)
        assert result1 != result2
        assert call_count[0] == 2


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
