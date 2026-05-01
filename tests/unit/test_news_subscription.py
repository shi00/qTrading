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


class TestAlertListenersAsync:
    """NewsSubscription alert_listeners 异步调用机制"""

    def test_alert_listeners_use_run_in_executor(self):
        source = inspect.getsource(NewsSubscriptionService._processing_loop)
        alert_block = []
        in_alert = False
        for line in source.split("\n"):
            if "alert_listeners" in line:
                in_alert = True
            if in_alert:
                alert_block.append(line)
                if "except Exception" in line and len(alert_block) > 3:
                    break
        alert_src = "\n".join(alert_block)
        assert "run_in_executor" in alert_src, "Alert listeners must use run_in_executor to avoid blocking event loop"

    def test_alert_listeners_lambda_closure_safe(self):
        source = inspect.getsource(NewsSubscriptionService._processing_loop)
        alert_block = []
        in_alert = False
        for line in source.split("\n"):
            if "alert_listeners" in line:
                in_alert = True
            if in_alert:
                alert_block.append(line)
                if "except Exception" in line and len(alert_block) > 3:
                    break
        alert_src = "\n".join(alert_block)
        for line in alert_src.split("\n"):
            if "lambda" in line:
                assert "_l=" in line, f"Lambda must use default-arg binding: {line.strip()}"

    def test_alert_listeners_have_timeout(self):
        source = inspect.getsource(NewsSubscriptionService._processing_loop)
        alert_block = []
        in_alert = False
        for line in source.split("\n"):
            if "alert_listeners" in line:
                in_alert = True
            if in_alert:
                alert_block.append(line)
                if "except Exception" in line and len(alert_block) > 3:
                    break
        alert_src = "\n".join(alert_block)
        assert "wait_for" in alert_src, "Alert listeners must have timeout via asyncio.wait_for"
        assert "TimeoutError" in alert_src, "Must handle TimeoutError for alert listeners"
