import pytest
import asyncio

from utils.async_utils import gather_return_exceptions_propagating_cancel, gather_for_shutdown_cleanup


class TestGatherReturnExceptionsPropagatingCancel:
    """业务并发用：普通异常保留在结果中，CancelledError 必须重新抛出。"""

    @pytest.mark.asyncio
    async def test_all_success(self):
        async def a():
            return 1

        async def b():
            return 2

        results = await gather_return_exceptions_propagating_cancel(a(), b())
        assert results == [1, 2]

    @pytest.mark.asyncio
    async def test_business_exception_kept_in_results(self):
        async def ok():
            return "ok"

        async def fail():
            raise ValueError("business error")

        results = await gather_return_exceptions_propagating_cancel(ok(), fail())
        assert results[0] == "ok"
        assert isinstance(results[1], ValueError)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagated(self):
        async def ok():
            return "ok"

        async def cancel():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await gather_return_exceptions_propagating_cancel(ok(), cancel())

    @pytest.mark.asyncio
    async def test_mixed_cancel_and_exception_propagates_cancel(self):
        async def fail():
            raise RuntimeError("oops")

        async def cancel():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await gather_return_exceptions_propagating_cancel(fail(), cancel())

    @pytest.mark.asyncio
    async def test_empty_coros(self):
        results = await gather_return_exceptions_propagating_cancel()
        assert results == []

    @pytest.mark.asyncio
    async def test_accepts_tasks(self):
        async def ok():
            return "task-ok"

        task = asyncio.create_task(ok())
        results = await gather_return_exceptions_propagating_cancel(task)
        assert results == ["task-ok"]


class TestGatherForShutdownCleanup:
    """关机清理用：CancelledError 视为预期结果不抛出，普通异常记录日志。"""

    @pytest.mark.asyncio
    async def test_cancelled_error_not_raised(self):
        async def cancelled():
            raise asyncio.CancelledError()

        results = await gather_for_shutdown_cleanup(cancelled())
        assert isinstance(results[0], asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_business_exception_logged_not_raised(self):
        async def fail():
            raise RuntimeError("cleanup error")

        results = await gather_for_shutdown_cleanup(fail())
        assert isinstance(results[0], RuntimeError)

    @pytest.mark.asyncio
    async def test_mixed_cancel_and_exception(self):
        async def cancelled():
            raise asyncio.CancelledError()

        async def fail():
            raise RuntimeError("cleanup error")

        results = await gather_for_shutdown_cleanup(cancelled(), fail())
        assert isinstance(results[0], asyncio.CancelledError)
        assert isinstance(results[1], RuntimeError)

    @pytest.mark.asyncio
    async def test_all_success(self):
        async def ok():
            return "done"

        results = await gather_for_shutdown_cleanup(ok())
        assert results == ["done"]

    @pytest.mark.asyncio
    async def test_empty_coros(self):
        results = await gather_for_shutdown_cleanup()
        assert results == []

    @pytest.mark.asyncio
    async def test_accepts_cancelled_tasks(self):
        async def wait_forever():
            await asyncio.Event().wait()

        task = asyncio.create_task(wait_forever())
        task.cancel()
        results = await gather_for_shutdown_cleanup(task)
        assert isinstance(results[0], asyncio.CancelledError)
