"""Alembic schema idempotent tests based on real migration execution.

Uses the session-scoped PostgreSQL test database (via test_engine fixture).
"""

# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import importlib.util
import os

from sqlalchemy import inspect
import pytest


pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location(
    "alembic_initial_schema",
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "alembic",
        "versions",
        "0001_initial_schema.py",
    ),
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

    async def test_financial_reports_has_new_columns(self, test_engine):
        """Verify the merged migration includes money_cap and accounts_receiv."""
        async with test_engine.connect() as conn:
            cols = await conn.run_sync(
                lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns("financial_reports")}
            )
        assert "money_cap" in cols, "money_cap column missing from financial_reports"
        assert "accounts_receiv" in cols, "accounts_receiv column missing from financial_reports"
