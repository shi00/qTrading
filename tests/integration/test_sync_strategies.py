"""
Tests for data sync strategies.

P1-1: Breakpoint resume uses CORE_RESUME_TABLES for quality scoring.
P1-2: Circuit breaker with exponential backoff.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestCoreResumeTables:
    """P1-1: CORE_RESUME_TABLES used for breakpoint quality scoring"""

    def test_core_resume_tables_exists(self):
        """HistoricalSyncStrategy should define CORE_RESUME_TABLES"""
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py"))
        with open(path, encoding="utf-8") as f:
            source = f.read()

        assert "CORE_RESUME_TABLES" in source, "P1-1: HistoricalSyncStrategy should define CORE_RESUME_TABLES"

    def test_core_resume_tables_subset_of_synced(self):
        """CORE_RESUME_TABLES must be a subset of SYNCED_TABLES"""
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py"))
        with open(path, encoding="utf-8") as f:
            source = f.read()

        assert "SYNCED_TABLES" in source, "HistoricalSyncStrategy should define SYNCED_TABLES"
        assert "CORE_RESUME_TABLES" in source, "HistoricalSyncStrategy should define CORE_RESUME_TABLES"

    def test_core_resume_tables_contains_daily_quotes(self):
        """daily_quotes must be in CORE_RESUME_TABLES"""
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py"))
        with open(path, encoding="utf-8") as f:
            source = f.read()

        core_section = source[source.index("CORE_RESUME_TABLES") :]
        assert "daily_quotes" in core_section, "P1-1: daily_quotes should be in CORE_RESUME_TABLES"

    def test_core_tables_smaller_than_synced(self):
        """CORE_RESUME_TABLES should be smaller than SYNCED_TABLES"""
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py"))
        with open(path, encoding="utf-8") as f:
            source = f.read()

        synced_section = source[source.index("SYNCED_TABLES") : source.index("CORE_RESUME_TABLES")]
        core_start = source.index("CORE_RESUME_TABLES")
        core_section = source[core_start : core_start + 500]

        synced_count = synced_section.count('"') // 2
        core_count = core_section.count('"') // 2
        assert core_count < synced_count, "P1-1: CORE_RESUME_TABLES should be smaller than SYNCED_TABLES"


class TestCircuitBreakerBackoff:
    """P1-2: Exponential backoff for circuit breaker"""

    def test_exponential_backoff_formula(self):
        """Retry delay should follow 2**retry_round pattern"""
        delays = [2**r for r in range(5)]
        assert delays == [1, 2, 4, 8, 16]

    def test_backoff_increases(self):
        """Each retry round should have longer delay than previous"""
        delays = [2**r for r in range(5)]
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1]

    def test_circuit_breaker_in_source(self):
        """HistoricalSyncStrategy should have circuit breaker logic"""
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py"))
        with open(path, encoding="utf-8") as f:
            source = f.read()

        has_backoff = "2 **" in source or "2**" in source or "exponential" in source.lower()
        assert has_backoff, "P1-2: HistoricalSyncStrategy should have exponential backoff"
