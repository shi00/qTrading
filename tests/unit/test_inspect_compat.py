import inspect


class TestIsCoroutineFunctionDetection:
    """验证 inspect.iscoroutinefunction 正确识别协程/同步/lambda"""

    def test_async_def_detected(self):
        async def coro():
            pass

        assert inspect.iscoroutinefunction(coro)

    def test_sync_def_not_detected(self):
        def func():
            pass

        assert not inspect.iscoroutinefunction(func)

    def test_lambda_not_detected(self):
        assert not inspect.iscoroutinefunction(lambda: None)
