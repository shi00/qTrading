import unittest
from unittest.mock import MagicMock, patch, call
import tushare as ts
# Make sure to import the class to be tested
# Assuming path is setup or we run as module
from data.tushare_client import TushareClient

class TestTushareClient(unittest.TestCase):
    
    def setUp(self):
        # Reset singleton
        TushareClient._instance = None
        
    def tearDown(self):
        TushareClient._instance = None

    @patch('time.sleep')
    @patch('tushare.pro_api')
    @patch('tushare.set_token')
    def test_retry_on_rate_limit(self, mock_set_token, mock_pro_api, mock_sleep):
        """Test retry logic when rate limit is hit"""
        # Setup mock API
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance
        
        # Define side effects: 2 failures then success
        # Failure 1: Rate limit
        # Failure 2: Rate limit
        # Success: Return value
        mock_api_instance.daily.side_effect = [
            Exception("抱歉，您每分钟最多访问"),
            Exception("抱歉，您每分钟最多访问"),
            "Success"
        ]
        
        client = TushareClient(token="dummy")
        result = client.get_daily_quotes(ts_code="000001.SZ")
        
        self.assertEqual(result, "Success")
        self.assertEqual(mock_api_instance.daily.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        
        # Verify backoff (jitter makes it hard to check exact values, but we can check calls)
        # We expect sleep to be called
        
    @patch('time.sleep')
    @patch('tushare.pro_api')
    @patch('tushare.set_token')
    def test_retry_on_network_error(self, mock_set_token, mock_pro_api, mock_sleep):
        """Test retry logic on network error"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance
        
        mock_api_instance.daily.side_effect = [
            Exception("Connection timeout"),
            "Success"
        ]
        
        client = TushareClient(token="dummy")
        result = client.get_daily_quotes(ts_code="000001.SZ")
        
        self.assertEqual(result, "Success")
        self.assertEqual(mock_api_instance.daily.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch('time.sleep')
    @patch('tushare.pro_api')
    @patch('tushare.set_token')
    def test_failure_after_max_retries(self, mock_set_token, mock_pro_api, mock_sleep):
        """Test that it raises exception after max retries"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance
        
        mock_api_instance.daily.side_effect = Exception("General Error")
        
        client = TushareClient(token="dummy")
        
        with self.assertRaises(Exception):
            client.get_daily_quotes(ts_code="000001.SZ")
            
        self.assertEqual(mock_api_instance.daily.call_count, 3) # Max retries default is 3

if __name__ == '__main__':
    unittest.main()
