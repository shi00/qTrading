"""
ORM/DAO Field Alignment Tests

Tests that ensure DAO save methods include all ORM model columns.
This prevents silent data loss when new fields are added to ORM but not to DAO.

Run: pytest tests/field_alignment/test_orm_dao_alignment.py -v
"""

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

from tests._helpers import extract_cols_from_method, get_model_columns
import pytest


pytestmark = pytest.mark.integration


class TestOrmDaoAlignment:
    """Test that DAO save methods include all ORM model columns."""

    def _assert_cols_aligned(self, method, model_cls, excluded=None, custom_gmc=None):
        if excluded is None:
            excluded = {"updated_at", "created_at"}
        if custom_gmc:
            model_cols = set(custom_gmc(model_cls))
        else:
            model_cols = get_model_columns(model_cls)
        dao_cols = extract_cols_from_method(method)
        name = getattr(method, "__name__", str(method))
        assert dao_cols, f"Failed to extract columns from {name}"
        expected = model_cols - excluded
        missing = expected - dao_cols
        assert not missing, f"{name} missing: {missing}"

    def test_daily_quotes_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_daily_quotes, DailyQuotes)

    def test_moneyflow_daily_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_moneyflow, MoneyflowDaily)

    def test_top_list_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_top_list, TopList)

    def test_block_trade_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_block_trade, BlockTrade)

    def test_limit_list_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_limit_list, LimitList)

    def test_margin_daily_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_margin_daily, MarginDaily)

    def test_suspend_d_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_suspend_d, SuspendD)

    def test_northbound_holding_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_northbound, NorthboundHolding)

    def test_index_daily_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_index_daily, IndexDaily)

    def test_index_dailybasic_alignment(self):
        self._assert_cols_aligned(QuoteDao.save_index_dailybasic, IndexDailyBasic)

    def test_daily_indicators_alignment(self):
        self._assert_cols_aligned(MarketDao.save_daily_indicators, DailyIndicators)

    def test_moneyflow_hsgt_alignment(self):
        self._assert_cols_aligned(MarketDao.save_moneyflow_hsgt, MoneyflowHsgt)

    def test_index_weight_alignment(self):
        self._assert_cols_aligned(MarketDao.save_index_weights, IndexWeight)

    def test_financial_reports_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_financial_reports, FinancialReports)

    def test_fina_forecast_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_fina_forecast, FinaForecast)

    def test_fina_audit_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_fina_audit, FinaAudit)

    def test_fina_mainbz_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_fina_mainbz, FinaMainbz)

    def test_dividend_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_dividend, Dividend)

    def test_pledge_stat_alignment(self):
        # ann_date excluded: Tushare pledge_stat API does not return it (MD-001)
        self._assert_cols_aligned(
            FinancialDao.save_pledge_stat,
            PledgeStat,
            excluded={"updated_at", "created_at", "ann_date"},
        )

    def test_repurchase_alignment(self):
        self._assert_cols_aligned(FinancialDao.save_repurchase, Repurchase)

    def test_stock_basic_alignment(self):
        self._assert_cols_aligned(StockDao.save_stock_basic, StockBasic)

    def test_trade_cal_alignment(self):
        self._assert_cols_aligned(StockDao.save_trade_cal, TradeCal)

    def test_shibor_daily_alignment(self):
        # R17（迁移 0015）：属性名与列名一致，无需 orm_to_db_mapping
        self._assert_cols_aligned(MacroDao.save_shibor_daily, ShiborDaily)

    def test_stock_concepts_alignment(self):
        self._assert_cols_aligned(StockDao.save_concepts, StockConcepts)

    def test_stk_holdernumber_alignment(self):
        from data.persistence.models import get_model_columns as gmc_filtered

        self._assert_cols_aligned(HolderDao.save_holder_number, StkHoldernumber, custom_gmc=gmc_filtered)

    def test_macro_economy_alignment(self):
        # get_model_columns excludes updated_at by default; created_at excluded here
        self._assert_cols_aligned(MacroDao.save_macro_economy, MacroEconomy)

    def test_screening_history_alignment(self):
        from data.persistence.models import get_model_columns as gmc_filtered

        self._assert_cols_aligned(
            ScreenerDao.save_screening_results,
            ScreeningHistory,
            excluded={"id", "updated_at", "created_at"},
            custom_gmc=gmc_filtered,
        )

    def test_screening_history_review_fields_updated_by_review_path(self):
        from data.persistence.models import ScreeningHistory

        # 直接检查 __table__.columns，因为 computed 列会被 get_model_columns 自动排除
        model_cols = {c.name for c in ScreeningHistory.__table__.columns}
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
        missing = required_fields - model_cols
        assert not missing, f"ScreeningHistory model missing review fields: {missing}"

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
        assert dao_cols, "Failed to extract columns from QuoteDao.save_daily_quotes"
        qfq_cols = {"qfq_open", "qfq_high", "qfq_low", "qfq_close"}
        assert dao_cols.isdisjoint(qfq_cols), f"save_daily_quotes should not persist qfq columns: {dao_cols & qfq_cols}"


class TestMoneyflowVolFields:
    """Test that all moneyflow volume fields are saved."""

    def test_all_vol_fields_in_save_cols(self):
        dao_cols = extract_cols_from_method(QuoteDao.save_moneyflow)
        assert dao_cols, "Failed to extract columns from QuoteDao.save_moneyflow"
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
        assert vol_cols.issubset(dao_cols), f"save_moneyflow missing volume columns: {vol_cols - dao_cols}"


class TestDaoSaveMethodCompleteness:
    """Test that all DAO save methods delegate to _save_upsert which handles edge cases."""

    def test_save_daily_quotes_uses_save_upsert(self):
        assert hasattr(QuoteDao, "save_daily_quotes"), "save_daily_quotes should exist"
        assert hasattr(QuoteDao, "_save_upsert"), (
            "save_daily_quotes should delegate to _save_upsert for consistent handling"
        )

    def test_save_moneyflow_uses_save_upsert(self):
        assert hasattr(QuoteDao, "save_moneyflow"), "save_moneyflow should exist"
        assert hasattr(QuoteDao, "_save_upsert"), (
            "save_moneyflow should delegate to _save_upsert for consistent handling"
        )

    def test_save_top_list_uses_save_upsert(self):
        assert hasattr(QuoteDao, "save_top_list"), "save_top_list should exist"
        assert hasattr(QuoteDao, "_save_upsert"), (
            "save_top_list should delegate to _save_upsert for consistent handling"
        )
