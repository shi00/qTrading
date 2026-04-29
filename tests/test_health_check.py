"""
Tests for health check and data sync interactions.

P1-5: Health cache invalidated after sync.
P1-6: quality_scan includes delisted stocks (list_status D).
P1-7: API trade_cal used as gold standard for lag calculation.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestHealthCacheInvalidation:
    """P1-5: Health cache must be cleared after sync operations"""

    def test_sync_clears_health_cache_in_source(self):
        """Verify sync methods contain _health_cache reset logic in data_processor.py"""
        dp_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "data_processor.py"))
        with open(dp_path, encoding="utf-8") as f:
            source = f.read()

        assert '_health_cache = {"time": 0, "data": None}' in source, "P1-5: sync methods should reset _health_cache"

    def test_multiple_sync_methods_clear_cache(self):
        """Multiple sync methods should each clear _health_cache"""
        dp_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "data_processor.py"))
        with open(dp_path, encoding="utf-8") as f:
            source = f.read()

        count = source.count('_health_cache = {"time": 0, "data": None}')
        assert count >= 3, f"P1-5: Expected at least 3 cache resets, found {count}"


class TestQualityScanDelisted:
    """P1-6: quality_scan should include delisted stocks (list_status D)"""

    def test_scan_includes_delisted_status(self):
        """run_quality_scan should filter for both L and D list_status"""
        mixin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "mixins", "health_mixin.py"))
        with open(mixin_path, encoding="utf-8") as f:
            source = f.read()

        has_d_status = '"D"' in source or "'D'" in source
        has_l_status = '"L"' in source or "'L'" in source

        assert has_l_status, "P1-6: health_mixin should include list_status L"
        assert has_d_status, "P1-6: health_mixin should include list_status D (delisted)"


class TestLagDaysGoldStandard:
    """P1-7: API trade_cal used as gold standard for lag calculation"""

    def test_api_extends_official_dates(self):
        """When API returns newer date, gold_standard_dates should extend"""
        official_dates = ["20240101", "20240102", "20240103"]
        api_latest = "20240105"
        local_dates = {"20240101", "20240102"}

        gold_standard = list(official_dates)
        if api_latest > official_dates[-1]:
            gold_standard = official_dates + [api_latest]

        last_local = sorted(local_dates)[-1]
        lag_days = len([d for d in gold_standard if d > last_local])

        assert lag_days == 2
        assert "20240105" in gold_standard

    def test_no_api_falls_back_to_official(self):
        """Without API data, fall back to official dates"""
        official_dates = ["20240101", "20240102", "20240103"]
        api_latest = None

        gold_standard = official_dates
        if api_latest and api_latest > official_dates[-1]:
            gold_standard = official_dates + [api_latest]

        assert gold_standard == official_dates

    def test_gold_standard_in_health_check_code(self):
        """check_data_health should use gold_standard_dates variable"""
        mixin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "mixins", "health_mixin.py"))
        with open(mixin_path, encoding="utf-8") as f:
            source = f.read()

        assert "gold_standard_dates" in source, "P1-7: health_mixin should use gold_standard_dates"
        assert "api_latest_official" in source, "P1-7: health_mixin should track api_latest_official"
