import unittest
import pandas as pd
import numpy as np
import datetime
from utils.technical_analysis import TechnicalAnalysis

# Mocking data structures
class MockContext(dict):
    pass

class TestTechnicalAnalysis(unittest.TestCase):
    def test_get_rsi_uptrend(self):
        """Test RSI on an uptrend"""
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        df = pd.DataFrame({'close': prices, 'adj_factor': [1]*10})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        # In a perfect uptrend, gains are constant, losses are 0. RSI should be 100.
        self.assertAlmostEqual(rsi, 100.0)

    def test_get_rsi_downtrend(self):
        """Test RSI on a downtrend"""
        prices = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11]
        df = pd.DataFrame({'close': prices, 'adj_factor': [1]*10})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        # In a perfect downtrend, gains are 0, losses are constant. RSI should be 0.
        self.assertAlmostEqual(rsi, 0.0)
        
    def test_get_rsi_mixed(self):
        """Test RSI on mixed data"""
        # Up 1, Down 1, Up 1, Down 1...
        prices = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11] 
        df = pd.DataFrame({'close': prices, 'adj_factor': [1]*10})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        # Should be around 50, but ending on an up move (11) pulls it higher.
        # 65 is acceptable for period=6
        self.assertTrue(30 < rsi < 70, f"RSI {rsi} should be balanced around 50")

    def test_get_qfq(self):
        """Test QFQ adjustment"""
        # Close: 10, 20. Factor: 2, 1.
        # Latest factor is 1. Ratio for first row is 2/1=2. Adjusted Close = 10*2 = 20.
        df = pd.DataFrame({
            'close': [10, 20],
            'high': [10, 20],
            'low': [10, 20], 
            'open': [10, 20],
            'adj_factor': [2, 1]
        })
        adj_df = TechnicalAnalysis._get_qfq_df(df)
        self.assertEqual(adj_df['close'].iloc[0], 20)
        self.assertEqual(adj_df['close'].iloc[1], 20)

if __name__ == '__main__':
    unittest.main()
