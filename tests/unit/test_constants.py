import pandas as pd
import pytest

from data.constants import (
    CRITICAL_EMPTY_TABLES,
    EARNINGS_SEASON_MONTHS,
    FINANCIAL_BATCH_TABLES,
    FINANCIAL_STOCK_TABLES,
    HEALTH_CHECK_TABLES,
    HEALTH_REPORT_ORDER,
    MAJOR_INDICES,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_T1_DONE,
    REVIEW_STATUS_COMPLETED,
    SYNC_RESULT_EMPTY,
    SYNC_RESULT_FETCH_FAILED,
    SYNC_RESULT_HAS_DATA,
    SYNC_RESULT_SAVE_FAILED,
    TOP_LIST_COLUMN_UNITS,
    TOP_LIST_COLUMN_UNIT_SOURCES,
    TOP_LIST_NET_AMOUNT_UNIT,
    attach_column_units,
    attach_column_unit_sources,
    attach_top_list_column_units,
    get_column_unit,
    get_column_unit_source,
    get_health_depth_full_trade_days,
)

pytestmark = pytest.mark.unit


class TestAttachColumnUnits:
    def test_none_df(self):
        assert attach_column_units(None, {"col": "yuan"}) is None

    def test_attach_to_empty_df(self):
        df = pd.DataFrame()
        result = attach_column_units(df, {"net_amount": "yuan"})
        assert result.attrs["column_units"]["net_amount"] == "yuan"

    def test_attach_preserves_existing(self):
        df = pd.DataFrame()
        df.attrs["column_units"] = {"col1": "kg"}
        result = attach_column_units(df, {"col2": "yuan"})
        assert result.attrs["column_units"]["col1"] == "kg"
        assert result.attrs["column_units"]["col2"] == "yuan"

    def test_attach_overwrites_existing(self):
        df = pd.DataFrame()
        df.attrs["column_units"] = {"col1": "kg"}
        result = attach_column_units(df, {"col1": "yuan"})
        assert result.attrs["column_units"]["col1"] == "yuan"


class TestAttachColumnUnitSources:
    def test_none_df(self):
        assert attach_column_unit_sources(None, {"col": {"provider": "test"}}) is None

    def test_attach_to_empty_df(self):
        df = pd.DataFrame()
        source = {"provider": "tushare", "doc_url": "https://example.com"}
        result = attach_column_unit_sources(df, {"net_amount": source})
        assert result.attrs["column_unit_sources"]["net_amount"]["provider"] == "tushare"

    def test_preserves_existing(self):
        df = pd.DataFrame()
        df.attrs["column_unit_sources"] = {"col1": {"provider": "a"}}
        result = attach_column_unit_sources(df, {"col2": {"provider": "b"}})
        assert result.attrs["column_unit_sources"]["col1"]["provider"] == "a"
        assert result.attrs["column_unit_sources"]["col2"]["provider"] == "b"


class TestAttachTopListColumnUnits:
    def test_attaches_units_and_sources(self):
        df = pd.DataFrame({"net_amount": [100.0]})
        result = attach_top_list_column_units(df)
        assert result.attrs["column_units"]["net_amount"] == "yuan"
        assert "net_amount" in result.attrs["column_unit_sources"]


class TestAttachHsgtColumnUnits:
    def test_attaches_units_and_sources(self):
        from data.constants import (
            attach_hsgt_column_units,
            DATAFRAME_ATTR_COLUMN_UNITS,
            DATAFRAME_ATTR_COLUMN_UNIT_SOURCES,
        )

        df = pd.DataFrame({"north_money": [100.0, 200.0]})
        df = attach_hsgt_column_units(df)
        assert df.attrs[DATAFRAME_ATTR_COLUMN_UNITS]["north_money"] == "million_cny"
        assert df.attrs[DATAFRAME_ATTR_COLUMN_UNIT_SOURCES]["north_money"]["provider"] == "tushare.moneyflow_hsgt"


class TestGetColumnUnit:
    def test_none_df(self):
        assert get_column_unit(None, "col") is None

    def test_none_df_with_default(self):
        assert get_column_unit(None, "col", "default") == "default"

    def test_existing_column(self):
        df = pd.DataFrame()
        df.attrs["column_units"] = {"net_amount": "yuan"}
        assert get_column_unit(df, "net_amount") == "yuan"

    def test_missing_column(self):
        df = pd.DataFrame()
        df.attrs["column_units"] = {}
        assert get_column_unit(df, "missing") is None

    def test_missing_column_with_default(self):
        df = pd.DataFrame()
        df.attrs["column_units"] = {}
        assert get_column_unit(df, "missing", "default") == "default"


class TestGetColumnUnitSource:
    def test_none_df(self):
        assert get_column_unit_source(None, "col") is None

    def test_none_df_with_default(self):
        assert get_column_unit_source(None, "col", {"default": True}) == {"default": True}

    def test_existing_source(self):
        df = pd.DataFrame()
        df.attrs["column_unit_sources"] = {"net_amount": {"provider": "tushare"}}
        result = get_column_unit_source(df, "net_amount")
        assert result["provider"] == "tushare"

    def test_missing_source(self):
        df = pd.DataFrame()
        df.attrs["column_unit_sources"] = {}
        assert get_column_unit_source(df, "missing") is None

    def test_returns_copy(self):
        df = pd.DataFrame()
        original = {"provider": "tushare"}
        df.attrs["column_unit_sources"] = {"net_amount": original}
        result = get_column_unit_source(df, "net_amount")
        assert result is not original


class TestGetHealthDepthFullTradeDays:
    def test_returns_int(self):
        with pytest.MonkeyPatch.context() as m:
            m.setattr("utils.config_handler.ConfigHandler.get_init_history_years", lambda: 3)
            result = get_health_depth_full_trade_days()
            assert result == 750

    def test_different_years(self):
        with pytest.MonkeyPatch.context() as m:
            m.setattr("utils.config_handler.ConfigHandler.get_init_history_years", lambda: 5)
            result = get_health_depth_full_trade_days()
            assert result == 1250


class TestConstantsValues:
    def test_critical_empty_tables(self):
        assert "daily_quotes" in CRITICAL_EMPTY_TABLES
        assert "daily_indicators" in CRITICAL_EMPTY_TABLES

    def test_major_indices(self):
        assert "000001.SH" in MAJOR_INDICES
        assert "399001.SZ" in MAJOR_INDICES
        assert len(MAJOR_INDICES) >= 5

    def test_sync_result_constants(self):
        assert SYNC_RESULT_HAS_DATA == "HAS_DATA"
        assert SYNC_RESULT_EMPTY == "EMPTY"
        assert SYNC_RESULT_FETCH_FAILED == "FETCH_FAILED"
        assert SYNC_RESULT_SAVE_FAILED == "SAVE_FAILED"

    def test_review_status_constants(self):
        assert REVIEW_STATUS_PENDING == "PENDING"
        assert REVIEW_STATUS_T1_DONE == "T1_DONE"
        assert REVIEW_STATUS_COMPLETED == "COMPLETED"

    def test_earnings_season_months(self):
        assert EARNINGS_SEASON_MONTHS == [1, 4, 7, 10]

    def test_top_list_net_amount_unit(self):
        assert TOP_LIST_NET_AMOUNT_UNIT == "yuan"

    def test_top_list_column_units(self):
        assert "net_amount" in TOP_LIST_COLUMN_UNITS
        assert TOP_LIST_COLUMN_UNITS["net_amount"] == "yuan"

    def test_top_list_column_unit_sources(self):
        assert "net_amount" in TOP_LIST_COLUMN_UNIT_SOURCES
        assert "provider" in TOP_LIST_COLUMN_UNIT_SOURCES["net_amount"]

    def test_financial_batch_tables_keys(self):
        assert "fina_forecast" in FINANCIAL_BATCH_TABLES
        assert "dividend" in FINANCIAL_BATCH_TABLES
        assert "repurchase" in FINANCIAL_BATCH_TABLES

    def test_financial_stock_tables_keys(self):
        assert "fina_mainbz" in FINANCIAL_STOCK_TABLES
        assert "fina_audit" in FINANCIAL_STOCK_TABLES

    def test_health_check_tables_combined(self):
        assert set(HEALTH_CHECK_TABLES.keys()) == (
            set(FINANCIAL_BATCH_TABLES.keys())
            | set(FINANCIAL_STOCK_TABLES.keys())
            | {
                "financial_reports",
                "daily_indicators",
                "moneyflow_daily",
                "margin_daily",
                "suspend_d",
            }
            | {
                "stk_holdernumber",
                "top10_holders",
                "macro_economy",
                "shibor_daily",
                "moneyflow_hsgt",
            }
        )

    def test_health_report_order_covers_all(self):
        assert set(HEALTH_CHECK_TABLES.keys()).issubset(set(HEALTH_REPORT_ORDER))

    def test_financial_batch_tables_have_required_keys(self):
        for name, cfg in FINANCIAL_BATCH_TABLES.items():
            assert "api" in cfg, f"{name} missing api"
            assert "date_col" in cfg, f"{name} missing date_col"
            assert "key" in cfg, f"{name} missing key"
            assert "desc" in cfg, f"{name} missing desc"
