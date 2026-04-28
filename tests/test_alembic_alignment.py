"""
Alembic Alignment Tests

Tests that ensure Alembic migration scripts match ORM model columns.

Run: pytest tests/test_alembic_alignment.py -v
"""

import os
import re

import pytest

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

from .helpers import get_model_columns, get_model_db_columns


class TestAlembicMigrationAlignment:
    """Test that Alembic migration scripts match ORM model columns."""

    @pytest.fixture(autouse=True)
    def setup(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.migration_path = os.path.join(project_root, "alembic", "versions")
        self.migration_files = []

        versions_dir = self.migration_path
        if os.path.exists(versions_dir):
            for f in sorted(os.listdir(versions_dir)):
                if f.endswith(".py") and not f.startswith("__"):
                    self.migration_files.append(os.path.join(versions_dir, f))

    @staticmethod
    def _extract_upgrade_content(content: str) -> str:
        """Extract the body of the upgrade() function from migration content."""
        match = re.search(r"def upgrade\(\)\s*->\s*[^:]*:", content)
        if not match:
            return ""
        start = match.end()
        indent_level = None
        end = start
        for _i, line in enumerate(content[start:].split("\n"), start):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            current_indent = len(line) - len(stripped)
            if indent_level is None:
                indent_level = current_indent
            elif current_indent < indent_level and stripped.startswith("def "):
                end = content.index(line, start)
                break
        else:
            end = len(content)
        return content[start:end]

    def _extract_table_columns_from_migration(self, table_name: str) -> set | None:
        """Extract column names for a table from Alembic migration upgrade functions."""
        if not self.migration_files:
            return None

        all_columns: set = set()

        for migration_file in self.migration_files:
            with open(migration_file, encoding="utf-8") as f:
                content = f.read()

            upgrade_content = self._extract_upgrade_content(content)

            pattern = rf'op\.create_table\(\s*"{table_name}"'
            start_match = re.search(pattern, upgrade_content)
            if start_match:
                start_pos = start_match.end()

                brace_count = 1
                end_pos = start_pos
                for i, char in enumerate(upgrade_content[start_pos:], start_pos):
                    if char == "(":
                        brace_count += 1
                    elif char == ")":
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i
                            break

                table_content = upgrade_content[start_match.start() : end_pos + 1]

                col_pattern = r'sa\.Column\("(\w+)"'
                columns = set(re.findall(col_pattern, table_content))
                all_columns.update(columns)

            add_col_pattern = rf'op\.add_column\(\s*"{table_name}"\s*,\s*sa\.Column\("(\w+)"'
            add_col_matches = re.findall(add_col_pattern, upgrade_content)
            all_columns.update(add_col_matches)

            drop_col_pattern = rf'op\.drop_column\(\s*"{table_name}"\s*,\s*"(\w+)"'
            drop_col_matches = re.findall(drop_col_pattern, upgrade_content)
            all_columns -= set(drop_col_matches)

        return all_columns if all_columns else None

    def test_top_list_alembic_alignment(self):
        model_cols = get_model_columns(TopList) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("top_list")
        assert alembic_cols is not None, "Could not find top_list in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for top_list: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for top_list: {extra_in_alembic}"

    def test_block_trade_alembic_alignment(self):
        model_cols = get_model_columns(BlockTrade) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("block_trade")
        assert alembic_cols is not None, "Could not find block_trade in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for block_trade: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for block_trade: {extra_in_alembic}"

    def test_limit_list_alembic_alignment(self):
        model_cols = get_model_columns(LimitList) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("limit_list")
        assert alembic_cols is not None, "Could not find limit_list in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for limit_list: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for limit_list: {extra_in_alembic}"

    def test_suspend_d_alembic_alignment(self):
        model_cols = get_model_columns(SuspendD) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("suspend_d")
        assert alembic_cols is not None, "Could not find suspend_d in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for suspend_d: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for suspend_d: {extra_in_alembic}"

    def test_daily_quotes_alembic_alignment(self):
        model_cols = get_model_columns(DailyQuotes) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("daily_quotes")
        assert alembic_cols is not None, "Could not find daily_quotes in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for daily_quotes: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for daily_quotes: {extra_in_alembic}"

    def test_moneyflow_daily_alembic_alignment(self):
        model_cols = get_model_columns(MoneyflowDaily) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("moneyflow_daily")
        assert alembic_cols is not None, "Could not find moneyflow_daily in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for moneyflow_daily: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for moneyflow_daily: {extra_in_alembic}"

    def test_stock_basic_alembic_alignment(self):
        model_cols = get_model_columns(StockBasic) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("stock_basic")
        assert alembic_cols is not None, "Could not find stock_basic in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for stock_basic: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for stock_basic: {extra_in_alembic}"

    def test_dividend_alembic_alignment(self):
        model_cols = get_model_columns(Dividend) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("dividend")
        assert alembic_cols is not None, "Could not find dividend in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for dividend: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for dividend: {extra_in_alembic}"

    def test_daily_indicators_alembic_alignment(self):
        model_cols = get_model_columns(DailyIndicators) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("daily_indicators")
        assert alembic_cols is not None, "Could not find daily_indicators in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for daily_indicators: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for daily_indicators: {extra_in_alembic}"

    def test_financial_reports_alembic_alignment(self):
        model_cols = get_model_columns(FinancialReports) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("financial_reports")
        assert alembic_cols is not None, "Could not find financial_reports in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for financial_reports: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for financial_reports: {extra_in_alembic}"

    def test_margin_daily_alembic_alignment(self):
        model_cols = get_model_columns(MarginDaily) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("margin_daily")
        assert alembic_cols is not None, "Could not find margin_daily in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for margin_daily: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for margin_daily: {extra_in_alembic}"

    def test_northbound_holding_alembic_alignment(self):
        model_cols = get_model_columns(NorthboundHolding) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("northbound_holding")
        assert alembic_cols is not None, "Could not find northbound_holding in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for northbound_holding: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for northbound_holding: {extra_in_alembic}"

    def test_index_daily_alembic_alignment(self):
        model_cols = get_model_columns(IndexDaily) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("index_daily")
        assert alembic_cols is not None, "Could not find index_daily in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for index_daily: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for index_daily: {extra_in_alembic}"

    def test_index_dailybasic_alembic_alignment(self):
        model_cols = get_model_columns(IndexDailyBasic) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("index_dailybasic")
        assert alembic_cols is not None, "Could not find index_dailybasic in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for index_dailybasic: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for index_dailybasic: {extra_in_alembic}"

    def test_index_weight_alembic_alignment(self):
        model_cols = get_model_columns(IndexWeight) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("index_weight")
        assert alembic_cols is not None, "Could not find index_weight in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for index_weight: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for index_weight: {extra_in_alembic}"

    def test_moneyflow_hsgt_alembic_alignment(self):
        model_cols = get_model_columns(MoneyflowHsgt) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("moneyflow_hsgt")
        assert alembic_cols is not None, "Could not find moneyflow_hsgt in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for moneyflow_hsgt: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for moneyflow_hsgt: {extra_in_alembic}"

    def test_fina_audit_alembic_alignment(self):
        model_cols = get_model_columns(FinaAudit) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("fina_audit")
        assert alembic_cols is not None, "Could not find fina_audit in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for fina_audit: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for fina_audit: {extra_in_alembic}"

    def test_fina_forecast_alembic_alignment(self):
        model_cols = get_model_columns(FinaForecast) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("fina_forecast")
        assert alembic_cols is not None, "Could not find fina_forecast in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for fina_forecast: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for fina_forecast: {extra_in_alembic}"

    def test_fina_mainbz_alembic_alignment(self):
        model_cols = get_model_columns(FinaMainbz) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("fina_mainbz")
        assert alembic_cols is not None, "Could not find fina_mainbz in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for fina_mainbz: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for fina_mainbz: {extra_in_alembic}"

    def test_pledge_stat_alembic_alignment(self):
        model_cols = get_model_columns(PledgeStat) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("pledge_stat")
        assert alembic_cols is not None, "Could not find pledge_stat in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for pledge_stat: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for pledge_stat: {extra_in_alembic}"

    def test_repurchase_alembic_alignment(self):
        model_cols = get_model_columns(Repurchase) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("repurchase")
        assert alembic_cols is not None, "Could not find repurchase in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for repurchase: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for repurchase: {extra_in_alembic}"

    def test_shibor_daily_alembic_alignment(self):
        model_cols = get_model_columns(ShiborDaily) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("shibor_daily")
        assert alembic_cols is not None, "Could not find shibor_daily in migration script"
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
            if orm_col in model_cols and db_col in alembic_cols:
                model_cols.discard(orm_col)
                model_cols.add(db_col)
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for shibor_daily: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for shibor_daily: {extra_in_alembic}"

    def test_stk_holdernumber_alembic_alignment(self):
        model_cols = get_model_columns(StkHoldernumber) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("stk_holdernumber")
        assert alembic_cols is not None, "Could not find stk_holdernumber in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for stk_holdernumber: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for stk_holdernumber: {extra_in_alembic}"

    def test_top10_holders_alembic_alignment(self):
        model_cols = get_model_columns(Top10Holders) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("top10_holders")
        assert alembic_cols is not None, "Could not find top10_holders in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for top10_holders: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for top10_holders: {extra_in_alembic}"

    def test_trade_cal_alembic_alignment(self):
        model_cols = get_model_columns(TradeCal) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("trade_cal")
        assert alembic_cols is not None, "Could not find trade_cal in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for trade_cal: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for trade_cal: {extra_in_alembic}"

    def test_screening_history_alembic_alignment(self):
        model_cols = get_model_columns(ScreeningHistory) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("screening_history")
        assert alembic_cols is not None, "Could not find screening_history in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for screening_history: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for screening_history: {extra_in_alembic}"

    def test_sync_status_alembic_alignment(self):
        model_cols = get_model_columns(SyncStatus) - {"updated_at", "created_at"}
        alembic_cols = self._extract_table_columns_from_migration("sync_status")
        assert alembic_cols is not None, "Could not find sync_status in migration script"
        missing_in_alembic = model_cols - alembic_cols
        extra_in_alembic = alembic_cols - model_cols - {"updated_at", "created_at"}
        assert not missing_in_alembic, f"Alembic missing columns for sync_status: {missing_in_alembic}"
        assert not extra_in_alembic, f"Alembic has extra columns for sync_status: {extra_in_alembic}"


class TestOrmAlembicDaoConsistency:
    """Test that ORM models, Alembic migrations, and DataDictionary stay in sync."""

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

    @pytest.fixture(autouse=True)
    def setup(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.migration_path = os.path.join(project_root, "alembic", "versions")
        self.migration_files = []
        if os.path.exists(self.migration_path):
            for f in sorted(os.listdir(self.migration_path)):
                if f.endswith(".py") and not f.startswith("__"):
                    self.migration_files.append(os.path.join(self.migration_path, f))

    def _extract_alembic_columns(self, table_name: str) -> set | None:
        extractor = TestAlembicMigrationAlignment()
        extractor.migration_path = self.migration_path
        extractor.migration_files = self.migration_files
        return extractor._extract_table_columns_from_migration(table_name)

    @pytest.mark.parametrize("model_class,table_name", ALL_MODELS)
    def test_orm_alembic_data_dict_consistency(self, model_class, table_name):
        orm_db_cols = get_model_db_columns(model_class) - {"updated_at", "created_at"}
        alembic_cols = self._extract_alembic_columns(table_name)

        from data.data_dictionary import TABLE_DEFINITIONS

        dd_entry = TABLE_DEFINITIONS.get(table_name, {})
        dd_columns = set(dd_entry.get("columns", {}).keys())

        errors = []

        if alembic_cols is not None:
            missing_in_alembic = orm_db_cols - alembic_cols
            if missing_in_alembic:
                errors.append(f"Alembic missing: {missing_in_alembic}")

            extra_in_alembic = alembic_cols - orm_db_cols - {"updated_at", "created_at"}
            if extra_in_alembic:
                errors.append(f"Alembic extra: {extra_in_alembic}")

        if dd_columns:
            missing_in_dd = orm_db_cols - dd_columns
            if missing_in_dd:
                errors.append(f"DataDict missing: {missing_in_dd}")

            extra_in_dd = dd_columns - orm_db_cols
            if extra_in_dd:
                errors.append(f"DataDict extra: {extra_in_dd}")

        assert not errors, f"{table_name} inconsistencies: {'; '.join(errors)}"
