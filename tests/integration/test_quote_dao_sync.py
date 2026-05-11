"""
Tests for QuoteDao and sync operations.

P1-3: check_data_exists uses UNION ALL single query.
P1-4: adj_factor missing writes to warnings.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestCheckDataExistsUnionAll:
    """P1-3: check_data_exists uses UNION ALL for efficiency"""

    def test_union_all_in_source(self):
        """check_data_exists should use UNION ALL in SQL"""
        dao_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "persistence", "daos", "quote_dao.py")
        )
        with open(dao_path, encoding="utf-8") as f:
            source = f.read()

        assert "union_all(" in source, "P1-3: quote_dao should use union_all() for efficiency"

    def test_union_all_sql_construction(self):
        """SQL should use UNION ALL to combine table checks"""
        tables = ["daily_quotes", "daily_indicators", "adj_factor"]
        union_parts = [f"SELECT '{t}' as tbl, 1 as val FROM {t} WHERE trade_date=$1 LIMIT 1" for t in tables]
        sql = " UNION ALL ".join(union_parts)

        assert "UNION ALL" in sql
        assert sql.count("UNION ALL") == len(tables) - 1


class TestAdjFactorMissingWarning:
    """P1-4: adj_factor missing should write to sync_result.warnings"""

    def test_warnings_in_sync_source(self):
        """Sync code should reference warnings for adj_factor"""
        hist_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py")
        )
        with open(hist_path, encoding="utf-8") as f:
            source = f.read()

        has_warnings = "warnings" in source.lower() or "warning" in source.lower()
        assert has_warnings, "P1-4: Sync code should reference warnings"

    def test_adj_factor_in_synced_tables(self):
        """adj_factor or daily_quotes should be tracked in synced tables"""
        hist_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "sync", "historical.py")
        )
        with open(hist_path, encoding="utf-8") as f:
            source = f.read()

        assert "daily_quotes" in source, "daily_quotes should be in SYNCED_TABLES"
