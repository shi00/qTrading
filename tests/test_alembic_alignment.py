"""Alembic schema alignment tests based on real migration execution."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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

from .helpers import get_model_db_columns

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


def _make_cfg(db_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture(scope="module")
def migrated_db_url(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("alembic_alignment") / "aligned.db"
    db_url = f"sqlite:///{db_file}"
    import config as cfg_mod

    # env.py prefers config.DB_URL over sqlalchemy.url; force it to the temp DB.
    original_db_url = cfg_mod.DB_URL
    original_env_db_url = os.environ.get("DATABASE_URL")
    cfg_mod.DB_URL = db_url
    os.environ["DATABASE_URL"] = db_url
    command.upgrade(_make_cfg(db_url), "head")
    yield db_url
    cfg_mod.DB_URL = original_db_url
    if original_env_db_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_env_db_url


def _get_table_columns(db_url: str, table_name: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return {col["name"] for col in inspect(engine).get_columns(table_name)}
    finally:
        engine.dispose()


class TestAlembicMigrationAlignment:
    """Ensure ORM models match reflected schema after running migrations."""

    @pytest.mark.parametrize("model_class,table_name", ALL_MODELS)
    def test_model_matches_reflected_schema(self, migrated_db_url, model_class, table_name):
        orm_cols = get_model_db_columns(model_class) - EXCLUDED_COLS
        reflected_cols = _get_table_columns(migrated_db_url, table_name) - EXCLUDED_COLS

        missing_in_db = orm_cols - reflected_cols
        extra_in_db = reflected_cols - orm_cols

        assert not missing_in_db, f"DB missing columns for {table_name}: {missing_in_db}"
        assert not extra_in_db, f"DB has extra columns for {table_name}: {extra_in_db}"

    def test_screening_history_pending_index_exists(self, migrated_db_url):
        engine = create_engine(migrated_db_url)
        try:
            idx_names = {idx["name"] for idx in inspect(engine).get_indexes("screening_history")}
        finally:
            engine.dispose()
        assert "idx_sh_pending" in idx_names


class TestOrmAlembicDaoConsistency:
    """Ensure ORM, migrated schema, and DataDictionary stay in sync."""

    @pytest.mark.parametrize("model_class,table_name", ALL_MODELS)
    def test_orm_reflected_data_dict_consistency(self, migrated_db_url, model_class, table_name):
        orm_cols = get_model_db_columns(model_class) - EXCLUDED_COLS
        reflected_cols = _get_table_columns(migrated_db_url, table_name) - EXCLUDED_COLS

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
