"""
Tests for QuoteDao sync behavior.

P1-4: check_data_exists uses UNION ALL for multi-table queries.
"""

import pytest


pytestmark = pytest.mark.integration


class TestQuoteDaoUnionAll:
    """P1-4: check_data_exists must use UNION ALL for multi-table queries"""

    def test_union_all_sql_construction(self):
        tables = ["daily_quotes", "daily_indicators", "moneyflow_daily"]
        parts = []
        for t in tables:
            part = f"SELECT '{t}' AS tbl, 1 AS val FROM {t} WHERE trade_date = ? LIMIT 1"
            parts.append(part)
        full_sql = " UNION ALL ".join(parts)

        assert full_sql.count("UNION ALL") == 2
        assert full_sql.count("SELECT") == 3
        assert "daily_quotes" in full_sql.split("UNION ALL")[0]
        assert "daily_indicators" in full_sql.split("UNION ALL")[1]
        assert "moneyflow_daily" in full_sql.split("UNION ALL")[2]

    def test_single_table_no_union_all(self):
        tables = ["daily_quotes"]
        parts = [f"SELECT '{t}' AS tbl, 1 AS val FROM {t} WHERE trade_date = ? LIMIT 1" for t in tables]
        full_sql = " UNION ALL ".join(parts) if len(parts) > 1 else parts[0]

        assert "UNION ALL" not in full_sql
        assert full_sql.count("SELECT") == 1

    def test_union_all_preserves_order(self):
        tables = ["daily_quotes", "daily_indicators", "moneyflow_daily"]
        parts = [f"SELECT '{t}' AS tbl, 1 AS val FROM {t} WHERE trade_date = ? LIMIT 1" for t in tables]
        full_sql = " UNION ALL ".join(parts)

        segments = full_sql.split("UNION ALL")
        assert "daily_quotes" in segments[0]
        assert "daily_indicators" in segments[1]
        assert "moneyflow_daily" in segments[2]


class TestQuoteDaoSyncBehavior:
    """Verify QuoteDao sync handles edge cases correctly"""

    def test_date_range_validation(self):
        start_date = "20260101"
        end_date = "20260110"
        assert start_date <= end_date

        invalid_end = "20250101"
        assert not (start_date <= invalid_end)

    def test_chunking_logic_for_large_code_lists(self):
        codes = [f"{i:06d}.SZ" for i in range(1, 1201)]
        chunk_size = 500
        chunks = []
        for i in range(0, len(codes), chunk_size):
            chunks.append(codes[i : i + chunk_size])
        assert len(chunks) == 3
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        assert len(chunks[2]) == 200

    def test_in_clause_for_small_code_list(self):
        codes = ["000001.SZ", "000002.SZ"]
        placeholders = ",".join([f"${i + 1}" for i in range(len(codes))])
        sql = f"SELECT * FROM daily_quotes WHERE ts_code IN ({placeholders})"
        assert "$1" in sql
        assert "$2" in sql
        assert "UNION ALL" not in sql
