import unittest

import pandas as pd
import numpy as np

from utils.technical_analysis import TechnicalAnalysis


class TestQfqNumericalCorrectness(unittest.TestCase):
    def test_qfq_formula_correct(self):
        df = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "high": [10.5, 11.5, 12.5],
                "low": [9.5, 10.5, 11.5],
                "close": [10.0, 11.0, 12.0],
                "adj_factor": [1.0, 1.1, 1.2],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        latest_factor = 1.2
        np.testing.assert_almost_equal(result["close"].iloc[0], 10.0 * 1.0 / latest_factor)
        np.testing.assert_almost_equal(result["close"].iloc[1], 11.0 * 1.1 / latest_factor)
        np.testing.assert_almost_equal(result["close"].iloc[2], 12.0 * 1.2 / latest_factor)

    def test_qfq_no_adj_factor_returns_original(self):
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        self.assertEqual(result["close"].iloc[0], 10.0)

    def test_qfq_single_row_returns_original(self):
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "adj_factor": [1.5],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        self.assertEqual(result["close"].iloc[0], 10.0)

    def test_qfq_nan_adj_factor_forward_filled(self):
        df = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "high": [10.5, 11.5, 12.5],
                "low": [9.5, 10.5, 11.5],
                "close": [10.0, 11.0, 12.0],
                "adj_factor": [1.0, np.nan, 1.2],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        latest_factor = 1.2
        np.testing.assert_almost_equal(result["close"].iloc[1], 11.0 * 1.0 / latest_factor)

    def test_qfq_zero_latest_factor_returns_original(self):
        df = pd.DataFrame(
            {
                "open": [10.0, 11.0],
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.0, 11.0],
                "adj_factor": [1.0, 0.0],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        self.assertEqual(result["close"].iloc[0], 10.0)
        self.assertEqual(result["close"].iloc[1], 11.0)

    def test_qfq_all_same_factor_returns_original(self):
        df = pd.DataFrame(
            {
                "open": [10.0, 11.0],
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.0, 11.0],
                "adj_factor": [1.5, 1.5],
            }
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        self.assertEqual(result["close"].iloc[0], 10.0)
        self.assertEqual(result["close"].iloc[1], 11.0)


if __name__ == "__main__":
    unittest.main()
