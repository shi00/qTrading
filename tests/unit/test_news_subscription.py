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

    def _extract_alert_block(self, source: str) -> str:
        """Extract the code block related to _alert_listeners invocation."""
        alert_block = []
        in_alert = False
        indent_level = None
        for line in source.split("\n"):
            if "_alert_listeners" in line:
                in_alert = True
                # Determine the indent level of the for-loop that iterates alert_listeners
                stripped = line.lstrip()
                if stripped.startswith("for listener in list(self._alert_listeners):"):
                    indent_level = len(line) - len(stripped)
            if in_alert:
                alert_block.append(line)
                # Stop when we encounter a line that is dedented back to or below the for-loop level
                # and is not empty/whitespace, indicating the block has ended
                if indent_level is not None:
                    stripped = line.lstrip()
                    if stripped and (len(line) - len(stripped)) <= indent_level:
                        # If this line is the for-loop line itself, don't stop yet
                        if not stripped.startswith("for listener in list(self._alert_listeners):"):
                            break
        return "\n".join(alert_block)

    def test_alert_listeners_use_run_in_executor(self):
        source = inspect.getsource(NewsSubscriptionService._fetch_and_notify)
        alert_src = self._extract_alert_block(source)
        assert "run_in_executor" in alert_src, "Alert listeners must use run_in_executor to avoid blocking event loop"

    def test_alert_listeners_lambda_closure_safe(self):
        source = inspect.getsource(NewsSubscriptionService._fetch_and_notify)
        alert_src = self._extract_alert_block(source)
        for line in alert_src.split("\n"):
            if "lambda" in line:
                assert "_l=" in line, f"Lambda must use default-arg binding: {line.strip()}"

    def test_alert_listeners_have_timeout(self):
        source = inspect.getsource(NewsSubscriptionService._fetch_and_notify)
        alert_src = self._extract_alert_block(source)
        assert "wait_for" in alert_src, "Alert listeners must have timeout via asyncio.wait_for"
        assert "TimeoutError" in alert_src, "Must handle TimeoutError for alert listeners"
