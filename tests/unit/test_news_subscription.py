import inspect


from data.external.news_subscription import NewsSubscriptionService


class TestNotifyListenersAsync:
    """NewsSubscription._notify_listeners 异步调用机制"""

    def test_notify_listeners_is_async(self):
        assert inspect.iscoroutinefunction(NewsSubscriptionService._notify_listeners)

    def test_notify_listeners_uses_run_in_executor(self):
        source = inspect.getsource(NewsSubscriptionService._notify_listeners)
        assert "run_in_executor" in source
        assert "iscoroutinefunction" in source

    def test_notify_listeners_lambda_closure_safe(self):
        source = inspect.getsource(NewsSubscriptionService._notify_listeners)
        lambda_lines = [line for line in source.split("\n") if "lambda" in line]
        for line in lambda_lines:
            assert "_l=" in line, f"Lambda must use default-arg binding for closure safety: {line.strip()}"
