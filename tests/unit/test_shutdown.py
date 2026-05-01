import inspect


from utils.shutdown import ShutdownCoordinator


class TestWatchdogCancelOnCleanup:
    """cleanup 完成后取消 watchdog"""

    def test_execute_cleanup_cancels_watchdog(self):
        source = inspect.getsource(ShutdownCoordinator._execute_cleanup)
        assert "cancel_watchdog" in source
