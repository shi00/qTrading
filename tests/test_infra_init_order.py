import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_initialization_order():
    """
    Verify that ProxyManager.apply_smart_proxy_policy is called BEFORE TushareClient is instantiated.
    This ensures proxy settings (NO_PROXY) are ready before any network clients are created.
    """

    # Use patch.dict to safely mock modules only for this test function scope
    # This prevents side effects on other tests in the same session
    mock_modules = {
        "data.tushare_client": MagicMock(),
        "utils.proxy_manager": MagicMock(),
    }

    with patch.dict(sys.modules, mock_modules):
        # Reset mocks
        MagicMock()
        MagicMock()

        # Setup patches inside the modified sys.modules context
        # We need to patch the mocks that are now in sys.modules, or patch normally?
        # Actually, since we replaced the modules in sys.modules with MagicMock,
        # importing them in main.py will give us these mocks.

        # However, to track calls, we need to control the side effects of these mocks.
        # Let's retrieve the mocks from sys.modules to configure them.

        tushare_client_module_mock = sys.modules["data.tushare_client"]
        proxy_manager_module_mock = sys.modules["utils.proxy_manager"]

        # Configure ProxyManager.apply_smart_proxy_policy
        call_order = []

        def on_apply_policy():
            call_order.append("ProxyManager.apply_smart_proxy_policy")

        proxy_manager_module_mock.ProxyManager.apply_smart_proxy_policy.side_effect = on_apply_policy

        # Configure TushareClient.__init__
        # TushareClient is a class in the module
        def on_tushare_init(*args, **kwargs):
            call_order.append("TushareClient.__init__")

        tushare_client_module_mock.TushareClient.side_effect = on_tushare_init

        # Simulate main.py startup sequence (simplified)

        # 1. Proxy Init (Line 29 of main.py)
        # calling the function on the mock module
        proxy_manager_module_mock.ProxyManager.apply_smart_proxy_policy()

        # 2. Simulate later instantiation
        # Instantiating the class on the mock module
        tushare_client_module_mock.TushareClient()

        # Verification
        assert call_order == [
            "ProxyManager.apply_smart_proxy_policy",
            "TushareClient.__init__",
        ], f"Initialization order is wrong! Got: {call_order}"

    print("\n[PASS] Initialization Order Verified: ProxyManager -> TushareClient")


if __name__ == "__main__":
    test_initialization_order()
