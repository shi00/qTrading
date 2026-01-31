import os
import unittest
from unittest.mock import patch
from utils.proxy_manager import ProxyManager

class TestProxyManager(unittest.TestCase):
    
    def test_bypass_proxy_for_domestic_adds_no_proxy(self):
        """Test that the context manager adds the domain to NO_PROXY"""
        with patch.dict(os.environ, {"NO_PROXY": "localhost"}):
            with ProxyManager.bypass_proxy_for_domestic("example.com"):
                self.assertIn("example.com", os.environ["NO_PROXY"])
                self.assertIn("localhost", os.environ["NO_PROXY"])
    
    def test_bypass_proxy_for_domestic_restores_no_proxy(self):
        """Test that the context manager restores NO_PROXY after exit"""
        initial_proxy = "localhost"
        with patch.dict(os.environ, {"NO_PROXY": initial_proxy}):
            with ProxyManager.bypass_proxy_for_domestic("example.com"):
                pass
            self.assertEqual(os.environ["NO_PROXY"], initial_proxy)

    def test_bypass_proxy_handles_empty_env(self):
        """Test with no initial NO_PROXY"""
        with patch.dict(os.environ, {}, clear=True):
            with ProxyManager.bypass_proxy_for_domestic("example.com"):
                 self.assertEqual(os.environ["NO_PROXY"], "example.com")
            
            self.assertIsNone(os.environ.get("NO_PROXY"))

if __name__ == '__main__':
    unittest.main()
