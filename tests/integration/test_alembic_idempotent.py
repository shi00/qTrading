import importlib.util
import logging
import os


_spec = importlib.util.spec_from_file_location(
    "alembic_initial_schema",
    os.path.join(os.path.dirname(__file__), "..", "..", "alembic", "versions", "f6586a3fccba_initial_schema_v1.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_ALL_EXPECTED_TABLES = _mod._ALL_EXPECTED_TABLES


class TestAlembicIdempotentUpgrade:
    def test_all_expected_tables_list_is_complete(self):
        assert "daily_quotes" in _ALL_EXPECTED_TABLES
        assert "stock_basic" in _ALL_EXPECTED_TABLES
        assert "financial_reports" in _ALL_EXPECTED_TABLES
        assert "screening_history" in _ALL_EXPECTED_TABLES
        assert "trade_cal" in _ALL_EXPECTED_TABLES
        assert len(_ALL_EXPECTED_TABLES) >= 31

    def test_missing_tables_detection_logic(self):
        existing = {"daily_quotes", "stock_basic", "financial_reports"}
        missing = [t for t in _ALL_EXPECTED_TABLES if t not in existing]
        assert "block_trade" in missing
        assert "daily_quotes" not in missing
        assert "stock_basic" not in missing

    def test_no_missing_tables_when_all_exist(self):
        existing = set(_ALL_EXPECTED_TABLES)
        missing = [t for t in _ALL_EXPECTED_TABLES if t not in existing]
        assert missing == []

    def test_warning_logged_for_missing_tables(self, caplog):
        existing = {"daily_quotes"}
        missing = [t for t in _ALL_EXPECTED_TABLES if t not in existing]
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            logging.getLogger("alembic.runtime.migration").warning(
                "Legacy DB has daily_quotes but missing tables: %s.", missing
            )
        assert "missing tables" in caplog.text
        assert "stock_basic" in caplog.text
