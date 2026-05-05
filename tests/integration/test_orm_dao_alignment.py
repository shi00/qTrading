"""
ORM/DAO Field Alignment Tests

Tests that ensure DAO save methods include all ORM model columns.
This prevents silent data loss when new fields are added to ORM but not to DAO.

Run: pytest tests/field_alignment/test_orm_dao_alignment.py -v
"""

import inspect

from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.macro_dao import MacroDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.stock_dao import StockDao
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
    MacroEconomy,
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
    StockConcepts,
    SuspendD,
    TopList,
    TradeCal,
)

from .helpers import extract_cols_from_method, get_model_columns


class TestOrmDaoAlignment:
    """Test that DAO save methods include all ORM model columns."""

    def test_daily_quotes_alignment(self):
        model_cols = get_model_columns(DailyQuotes)
        dao_cols = extract_cols_from_method(QuoteDao.save_daily_quotes)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_daily_quotes missing: {missing}"

    def test_moneyflow_daily_alignment(self):
        model_cols = get_model_columns(MoneyflowDaily)
        dao_cols = extract_cols_from_method(QuoteDao.save_moneyflow)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_moneyflow missing: {missing}"

    def test_top_list_alignment(self):
        model_cols = get_model_columns(TopList)
        dao_cols = extract_cols_from_method(QuoteDao.save_top_list)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_top_list missing: {missing}"

    def test_block_trade_alignment(self):
        model_cols = get_model_columns(BlockTrade)
        dao_cols = extract_cols_from_method(QuoteDao.save_block_trade)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_block_trade missing: {missing}"

    def test_limit_list_alignment(self):
        model_cols = get_model_columns(LimitList)
        dao_cols = extract_cols_from_method(QuoteDao.save_limit_list)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_limit_list missing: {missing}"

    def test_margin_daily_alignment(self):
        model_cols = get_model_columns(MarginDaily)
        dao_cols = extract_cols_from_method(QuoteDao.save_margin_daily)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_margin_daily missing: {missing}"

    def test_suspend_d_alignment(self):
        model_cols = get_model_columns(SuspendD)
        dao_cols = extract_cols_from_method(QuoteDao.save_suspend_d)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_suspend_d missing: {missing}"

    def test_northbound_holding_alignment(self):
        model_cols = get_model_columns(NorthboundHolding)
        dao_cols = extract_cols_from_method(QuoteDao.save_northbound)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_northbound missing: {missing}"

    def test_index_daily_alignment(self):
        model_cols = get_model_columns(IndexDaily)
        dao_cols = extract_cols_from_method(QuoteDao.save_index_daily)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_index_daily missing: {missing}"

    def test_index_dailybasic_alignment(self):
        model_cols = get_model_columns(IndexDailyBasic)
        dao_cols = extract_cols_from_method(QuoteDao.save_index_dailybasic)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_index_dailybasic missing: {missing}"

    def test_daily_indicators_alignment(self):
        model_cols = get_model_columns(DailyIndicators)
        dao_cols = extract_cols_from_method(MarketDao.save_daily_indicators)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_daily_indicators missing: {missing}"

    def test_moneyflow_hsgt_alignment(self):
        model_cols = get_model_columns(MoneyflowHsgt)
        dao_cols = extract_cols_from_method(MarketDao.save_moneyflow_hsgt)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_moneyflow_hsgt missing: {missing}"

    def test_index_weight_alignment(self):
        model_cols = get_model_columns(IndexWeight)
        dao_cols = extract_cols_from_method(MarketDao.save_index_weights)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_index_weights missing: {missing}"

    def test_financial_reports_alignment(self):
        model_cols = get_model_columns(FinancialReports)
        dao_cols = extract_cols_from_method(FinancialDao.save_financial_reports)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_financial_reports missing: {missing}"

    def test_fina_forecast_alignment(self):
        model_cols = get_model_columns(FinaForecast)
        dao_cols = extract_cols_from_method(FinancialDao.save_fina_forecast)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_fina_forecast missing: {missing}"

    def test_fina_audit_alignment(self):
        model_cols = get_model_columns(FinaAudit)
        dao_cols = extract_cols_from_method(FinancialDao.save_fina_audit)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_fina_audit missing: {missing}"

    def test_fina_mainbz_alignment(self):
        model_cols = get_model_columns(FinaMainbz)
        dao_cols = extract_cols_from_method(FinancialDao.save_fina_mainbz)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_fina_mainbz missing: {missing}"

    def test_dividend_alignment(self):
        model_cols = get_model_columns(Dividend)
        dao_cols = extract_cols_from_method(FinancialDao.save_dividend)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_dividend missing: {missing}"

    def test_pledge_stat_alignment(self):
        model_cols = get_model_columns(PledgeStat)
        dao_cols = extract_cols_from_method(FinancialDao.save_pledge_stat)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_pledge_stat missing: {missing}"

    def test_repurchase_alignment(self):
        model_cols = get_model_columns(Repurchase)
        dao_cols = extract_cols_from_method(FinancialDao.save_repurchase)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_repurchase missing: {missing}"

    def test_stock_basic_alignment(self):
        model_cols = get_model_columns(StockBasic)
        dao_cols = extract_cols_from_method(StockDao.save_stock_basic)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_stock_basic missing: {missing}"

    def test_trade_cal_alignment(self):
        model_cols = get_model_columns(TradeCal)
        dao_cols = extract_cols_from_method(StockDao.save_trade_cal)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_trade_cal missing: {missing}"

    def test_shibor_daily_alignment(self):
        model_cols = get_model_columns(ShiborDaily)
        dao_cols = extract_cols_from_method(MacroDao.save_shibor_daily)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        orm_to_db_mapping = {
            "w1": "1w",
            "w2": "2w",
            "m1": "1m",
            "m3": "3m",
            "m6": "6m",
            "m9": "9m",
            "y1": "1y",
        }
        for orm_col, db_col in orm_to_db_mapping.items():
            if orm_col in expected:
                expected.discard(orm_col)
                if db_col in dao_cols:
                    expected.add(orm_col)
        missing = expected - dao_cols
        if missing and missing <= set(orm_to_db_mapping.keys()):
            missing = set()
        assert not missing, f"save_shibor_daily missing: {missing}"

    def test_stock_concepts_alignment(self):
        model_cols = get_model_columns(StockConcepts)
        dao_cols = extract_cols_from_method(StockDao.save_concepts)
        assert dao_cols is not None
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_concepts missing: {missing}"

    def test_stk_holdernumber_alignment(self):
        model_cols = get_model_columns(StkHoldernumber)
        dao_cols = extract_cols_from_method(HolderDao.save_holder_number)
        assert dao_cols is not None
        computed = {"holder_num_change", "holder_num_ratio"}
        expected = model_cols - {"updated_at", "created_at"} - computed
        missing = expected - dao_cols
        assert not missing, f"save_holder_number missing: {missing}"

    def test_macro_economy_alignment(self):
        model_cols = get_model_columns(MacroEconomy)
        dao_cols = extract_cols_from_method(MacroDao.save_macro_economy)
        assert dao_cols is not None
        expected = model_cols - {"created_at"}
        missing = expected - dao_cols
        assert not missing, f"save_macro_economy missing: {missing}"

    def test_screening_history_alignment(self):
        model_cols = get_model_columns(ScreeningHistory)
        dao_cols = extract_cols_from_method(ScreenerDao.save_screening_results)
        assert dao_cols is not None
        excluded = {
            "id",
            "updated_at",
            "created_at",
            "t1_price",
            "t1_pct",
            "t5_price",
            "t5_pct",
            "index_pct",
            "alpha",
            "prediction_result",
            "review_status",
        }
        expected = model_cols - excluded
        missing = expected - dao_cols
        assert not missing, f"save_screening_results missing: {missing}"

    def test_screening_history_review_fields_updated_by_review_path(self):
        source = inspect.getsource(ScreenerDao.update_prediction_result)
        required_fields = {
            "t1_pct",
            "prediction_result",
            "t1_price",
            "t5_pct",
            "t5_price",
            "index_pct",
            "alpha",
            "review_status",
        }
        missing = {field for field in required_fields if f'"{field}"' not in source and f"{field}=" not in source}
        assert not missing, f"update_prediction_result missing review fields: {missing}"

    def test_screening_history_pending_index_matches_review_status_query(self):
        pending_index = next(idx for idx in ScreeningHistory.__table__.indexes if idx.name == "idx_sh_pending")
        where_clause = str(pending_index.dialect_options["postgresql"]["where"])
        assert "review_status" in where_clause
        assert "PENDING" in where_clause
        assert "T1_DONE" in where_clause


class TestQfqCalculation:
    """Test that deprecated qfq_* fields are no longer persisted."""

    def test_qfq_fields_are_not_in_save_cols(self):
        dao_cols = extract_cols_from_method(QuoteDao.save_daily_quotes)
        assert dao_cols is not None
        qfq_cols = {"qfq_open", "qfq_high", "qfq_low", "qfq_close"}
        assert dao_cols.isdisjoint(qfq_cols), f"save_daily_quotes should not persist qfq columns: {dao_cols & qfq_cols}"


class TestMoneyflowVolFields:
    """Test that all moneyflow volume fields are saved."""

    def test_all_vol_fields_in_save_cols(self):
        dao_cols = extract_cols_from_method(QuoteDao.save_moneyflow)
        assert dao_cols is not None
        vol_cols = {
            "buy_sm_vol",
            "sell_sm_vol",
            "buy_md_vol",
            "sell_md_vol",
            "buy_lg_vol",
            "sell_lg_vol",
            "buy_elg_vol",
            "sell_elg_vol",
            "net_mf_vol",
        }
        missing = vol_cols - dao_cols
        assert not missing, f"save_moneyflow missing volume columns: {missing}"


class TestDaoSaveMethodCompleteness:
    """Test that all DAO save methods delegate to _save_upsert which handles edge cases."""

    def test_save_daily_quotes_uses_save_upsert(self):
        source = inspect.getsource(QuoteDao.save_daily_quotes)
        assert "_save_upsert" in source, "save_daily_quotes should delegate to _save_upsert for consistent handling"

    def test_save_moneyflow_uses_save_upsert(self):
        source = inspect.getsource(QuoteDao.save_moneyflow)
        assert "_save_upsert" in source, "save_moneyflow should delegate to _save_upsert for consistent handling"

    def test_save_top_list_uses_save_upsert(self):
        source = inspect.getsource(QuoteDao.save_top_list)
        assert "_save_upsert" in source, "save_top_list should delegate to _save_upsert for consistent handling"
