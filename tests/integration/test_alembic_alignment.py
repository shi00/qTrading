"""Alembic schema alignment tests based on real migration execution.

Uses the session-scoped PostgreSQL test database (via test_engine fixture)
instead of a temporary SQLite database.
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.models import (
    BlockTrade,
    DailyIndicators,
    DailyQuotes,
    Dividend,
    FinaAudit,
    FinaForecast,
    FinaMainbz,
    FinancialReports,
    IndexDaily,
    IndexDailyBasic,
    IndexWeight,
    LimitList,
    MarginDaily,
    MoneyflowDaily,
    MoneyflowHsgt,
    NorthboundHolding,
    PledgeStat,
    Repurchase,
    ScreeningHistory,
    ShiborDaily,
    StkHoldernumber,
    StockBasic,
    SuspendD,
    SyncStatus,
    Top10Holders,
    TopList,
    TradeCal,
)

from tests._helpers import get_model_db_columns

EXCLUDED_COLS = {"updated_at", "created_at"}
ALL_MODELS = [
    (StockBasic, "stock_basic"),
    (DailyQuotes, "daily_quotes"),
    (DailyIndicators, "daily_indicators"),
    (FinancialReports, "financial_reports"),
    (Dividend, "dividend"),
    (MoneyflowDaily, "moneyflow_daily"),
    (MoneyflowHsgt, "moneyflow_hsgt"),
    (MarginDaily, "margin_daily"),
    (NorthboundHolding, "northbound_holding"),
    (IndexDaily, "index_daily"),
    (IndexDailyBasic, "index_dailybasic"),
    (IndexWeight, "index_weight"),
    (BlockTrade, "block_trade"),
    (TopList, "top_list"),
    (LimitList, "limit_list"),
    (SuspendD, "suspend_d"),
    (FinaAudit, "fina_audit"),
    (FinaForecast, "fina_forecast"),
    (FinaMainbz, "fina_mainbz"),
    (PledgeStat, "pledge_stat"),
    (Repurchase, "repurchase"),
    (ScreeningHistory, "screening_history"),
    (ShiborDaily, "shibor_daily"),
    (StkHoldernumber, "stk_holdernumber"),
    (SyncStatus, "sync_status"),
    (Top10Holders, "top10_holders"),
    (TradeCal, "trade_cal"),
]


async def _get_table_columns(engine: AsyncEngine, table_name: str) -> set[str]:
    """Reflect column names from the PostgreSQL test database via run_sync."""
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns(table_name)}
        )


async def _get_table_indexes(engine: AsyncEngine, table_name: str) -> set[str]:
    """Reflect index names from the PostgreSQL test database via run_sync."""
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: {
                idx["name"] for idx in inspect(sync_conn).get_indexes(table_name) if idx["name"] is not None
            }
        )


class TestAlembicMigrationAlignment:
    """Ensure ORM models match reflected schema after running migrations."""

    @pytest.mark.parametrize("model_class,table_name", ALL_MODELS)
    async def test_model_matches_reflected_schema(self, test_engine: AsyncEngine, model_class, table_name):
        orm_cols = get_model_db_columns(model_class) - EXCLUDED_COLS
        reflected_cols = await _get_table_columns(test_engine, table_name) - EXCLUDED_COLS

        missing_in_db = orm_cols - reflected_cols
        extra_in_db = reflected_cols - orm_cols

        assert not missing_in_db, f"DB missing columns for {table_name}: {missing_in_db}"
        assert not extra_in_db, f"DB has extra columns for {table_name}: {extra_in_db}"

    async def test_screening_history_pending_index_exists(self, test_engine: AsyncEngine):
        idx_names = await _get_table_indexes(test_engine, "screening_history")
        assert "idx_sh_pending" in idx_names


class TestOrmAlembicDaoConsistency:
    """Ensure ORM, migrated schema, and DataDictionary stay in sync."""

    @pytest.mark.parametrize("model_class,table_name", ALL_MODELS)
    async def test_orm_reflected_data_dict_consistency(self, test_engine: AsyncEngine, model_class, table_name):
        orm_cols = get_model_db_columns(model_class) - EXCLUDED_COLS
        reflected_cols = await _get_table_columns(test_engine, table_name) - EXCLUDED_COLS

        from data.data_dictionary import TABLE_DEFINITIONS

        dd_entry = TABLE_DEFINITIONS.get(table_name, {})
        dd_columns = set(dd_entry.get("columns", {}).keys())

        errors = []
        missing_in_db = orm_cols - reflected_cols
        if missing_in_db:
            errors.append(f"DB missing: {missing_in_db}")
        extra_in_db = reflected_cols - orm_cols
        if extra_in_db:
            errors.append(f"DB extra: {extra_in_db}")

        if dd_columns:
            missing_in_dd = orm_cols - dd_columns
            if missing_in_dd:
                errors.append(f"DataDict missing: {missing_in_dd}")
            extra_in_dd = dd_columns - orm_cols
            if extra_in_dd:
                errors.append(f"DataDict extra: {extra_in_dd}")

        assert not errors, f"{table_name} inconsistencies: {'; '.join(errors)}"
