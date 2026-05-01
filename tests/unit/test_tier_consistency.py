import unittest

from data.constants import (
    TIER_FIN_FRESH_RATIO_GOLD,
    TIER_FIN_FRESH_RATIO_MIN,
    TIER_FIN_FRESH_RATIO_NEUTRAL,
    TIER_FINANCIAL_FRESHNESS_DAYS,
    TIER_FUNDAMENTAL_HIGH_THRESHOLD,
    TIER_FUNDAMENTAL_LOW_THRESHOLD,
    TIER_QUOTE_FRESHNESS_DAYS,
)
from data.mixins.health_mixin import _compute_tier


class TestComputeTierConsistency(unittest.TestCase):
    def test_critical_when_missing_critical_tables(self):
        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=1.0, missing_critical=True), 0)

    def test_bronze_when_quotes_stale(self):
        self.assertEqual(
            _compute_tier(lag_days=TIER_QUOTE_FRESHNESS_DAYS + 1, fin_fresh_ratio=1.0, missing_critical=False), 1
        )

    def test_silver_when_quotes_fresh_but_low_fundamental(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=0.6, missing_critical=False, avg_fundamental=0.1),
            2,
        )

    def test_gold_when_all_fresh_and_high_fundamental(self):
        self.assertEqual(
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=0.95,
                missing_critical=False,
                fin_lag_days=10,
                avg_fundamental=0.8,
            ),
            3,
        )

    def test_gold_without_fin_lag_if_ratio_above_threshold(self):
        self.assertEqual(
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=TIER_FIN_FRESH_RATIO_GOLD + 0.01,
                missing_critical=False,
                avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.01,
            ),
            3,
        )

    def test_silver_when_fin_ok_but_fundamental_below_gold(self):
        self.assertEqual(
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=0.95,
                missing_critical=False,
                fin_lag_days=10,
                avg_fundamental=0.4,
            ),
            2,
        )

    def test_silver_when_fin_ratio_above_neutral(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL + 0.01, missing_critical=False),
            2,
        )

    def test_silver_when_quotes_fresh_and_min_fin_ratio(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN, missing_critical=False),
            2,
        )

    def test_bronze_when_quotes_fresh_but_very_low_fin_ratio(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=TIER_FIN_FRESH_RATIO_MIN - 0.05, missing_critical=False),
            1,
        )

    def test_silver_when_fin_lag_ok_but_ratio_at_neutral(self):
        self.assertEqual(
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL,
                missing_critical=False,
                fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
                avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.01,
            ),
            3,
        )

    def test_no_gold_when_fin_lag_too_large(self):
        self.assertEqual(
            _compute_tier(
                lag_days=0,
                fin_fresh_ratio=0.95,
                missing_critical=False,
                fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS + 1,
                avg_fundamental=0.9,
            ),
            2,
        )

    def test_constants_relationship(self):
        self.assertGreater(TIER_FIN_FRESH_RATIO_GOLD, TIER_FIN_FRESH_RATIO_NEUTRAL)
        self.assertGreater(TIER_FIN_FRESH_RATIO_NEUTRAL, TIER_FIN_FRESH_RATIO_MIN)
        self.assertGreater(TIER_FUNDAMENTAL_HIGH_THRESHOLD, TIER_FUNDAMENTAL_LOW_THRESHOLD)
        self.assertGreater(TIER_FINANCIAL_FRESHNESS_DAYS, TIER_QUOTE_FRESHNESS_DAYS)


class TestTierBoundaryValues(unittest.TestCase):
    def test_lag_exactly_at_threshold_is_silver(self):
        self.assertEqual(
            _compute_tier(lag_days=TIER_QUOTE_FRESHNESS_DAYS, fin_fresh_ratio=0.6, missing_critical=False),
            2,
        )

    def test_lag_one_over_threshold_is_bronze(self):
        self.assertEqual(
            _compute_tier(lag_days=TIER_QUOTE_FRESHNESS_DAYS + 1, fin_fresh_ratio=0.6, missing_critical=False),
            1,
        )

    def test_fin_lag_exactly_at_threshold_with_good_ratio(self):
        result = _compute_tier(
            lag_days=0,
            fin_fresh_ratio=TIER_FIN_FRESH_RATIO_NEUTRAL,
            missing_critical=False,
            fin_lag_days=TIER_FINANCIAL_FRESHNESS_DAYS - 1,
            avg_fundamental=TIER_FUNDAMENTAL_HIGH_THRESHOLD + 0.01,
        )
        self.assertEqual(result, 3)

    def test_zero_lag_zero_ratio_is_bronze(self):
        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.0, missing_critical=False), 1)

    def test_avg_fundamental_none_uses_ratio_only(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=0.6, missing_critical=False, avg_fundamental=None),
            2,
        )

    def test_fin_fresh_ratio_none_fresh_quotes_is_silver(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=None, missing_critical=False),
            2,
        )

    def test_fin_fresh_ratio_none_stale_quotes_is_bronze(self):
        self.assertEqual(
            _compute_tier(lag_days=TIER_QUOTE_FRESHNESS_DAYS + 1, fin_fresh_ratio=None, missing_critical=False),
            1,
        )

    def test_fin_fresh_ratio_none_never_gold(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=None, missing_critical=False, avg_fundamental=0.9),
            2,
        )

    def test_fin_fresh_ratio_none_missing_critical_still_critical(self):
        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=None, missing_critical=True),
            0,
        )


if __name__ == "__main__":
    unittest.main()
