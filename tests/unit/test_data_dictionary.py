from unittest.mock import patch, MagicMock
from data.data_dictionary import (
    TABLE_DEFINITIONS,
    COMMON_COLUMNS,
    validate_schema_definitions,
)


class TestValidateSchemaDefinitionsExtended:
    @patch("data.persistence.models.Base")
    def test_validate_with_orm_columns_missing_from_dict(self, mock_base):
        mock_col = MagicMock()
        mock_col.name = "orm_only_col"
        mock_table = MagicMock()
        mock_table.columns = [mock_col]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_with_extra_defs_not_in_orm(self, mock_base):
        mock_metadata = MagicMock()
        mock_metadata.tables = {}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_ignores_stock_sync_status(self, mock_base):
        mock_col = MagicMock()
        mock_col.name = "step4_completed_at"
        mock_table = MagicMock()
        mock_table.columns = [mock_col]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_sync_status": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_ignores_alembic_version(self, mock_base):
        mock_col = MagicMock()
        mock_col.name = "version_num"
        mock_table = MagicMock()
        mock_table.columns = [mock_col]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"alembic_version": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_skips_updated_at_created_at(self, mock_base):
        mock_col1 = MagicMock()
        mock_col1.name = "updated_at"
        mock_col2 = MagicMock()
        mock_col2.name = "created_at"
        mock_col3 = MagicMock()
        mock_col3.name = "real_col"
        mock_table = MagicMock()
        mock_table.columns = [mock_col1, mock_col2, mock_col3]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    def test_validate_import_error(self):
        with patch("data.persistence.models.Base", side_effect=ImportError):
            validate_schema_definitions()


class TestTableDefinitionsQualityConfig:
    def test_critical_tables_have_quality_config(self):
        critical_tables = [
            name for name, meta in TABLE_DEFINITIONS.items() if meta.get("quality_config", {}).get("critical")
        ]
        assert "daily_quotes" in critical_tables
        assert "daily_indicators" in critical_tables
        assert "financial_reports" in critical_tables

    def test_tables_with_sync_config_have_strategy(self):
        for name, meta in TABLE_DEFINITIONS.items():
            if "sync_config" in meta:
                assert "strategy" in meta["sync_config"], f"{name} sync_config missing strategy"

    def test_all_quality_config_tiers_are_valid(self):
        for name, meta in TABLE_DEFINITIONS.items():
            qc = meta.get("quality_config")
            if qc:
                assert qc.get("tier") in (0, 1, 2, 3), f"{name} has invalid tier"

    def test_all_aliases_are_strings(self):
        for name, meta in TABLE_DEFINITIONS.items():
            assert isinstance(meta.get("alias", ""), str), f"{name} alias is not string"

    def test_financial_reports_has_desc(self):
        assert "desc" in TABLE_DEFINITIONS["financial_reports"]

    def test_moneyflow_daily_has_quality_config(self):
        qc = TABLE_DEFINITIONS["moneyflow_daily"].get("quality_config", {})
        assert qc.get("critical") is True


class TestTableDefinitions:
    def test_table_definitions_is_dict(self):
        assert isinstance(TABLE_DEFINITIONS, dict)

    def test_stock_basic_exists(self):
        assert "stock_basic" in TABLE_DEFINITIONS

    def test_stock_basic_has_alias(self):
        assert "alias" in TABLE_DEFINITIONS["stock_basic"]

    def test_stock_basic_has_columns(self):
        assert "columns" in TABLE_DEFINITIONS["stock_basic"]

    def test_all_tables_have_alias(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            assert "alias" in table_def, f"Table '{table_name}' missing 'alias'"

    def test_all_columns_values_are_strings(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            for col_name, alias_key in table_def.get("columns", {}).items():
                assert isinstance(alias_key, str), f"Column alias_key in '{table_name}.{col_name}' is not str"


class TestCommonColumns:
    def test_common_columns_is_dict(self):
        assert isinstance(COMMON_COLUMNS, dict)

    def test_ts_code_in_common(self):
        assert "ts_code" in COMMON_COLUMNS

    def test_trade_date_in_common(self):
        assert "trade_date" in COMMON_COLUMNS


class TestValidateSchemaDefinitions:
    @patch("data.persistence.models.Base")
    def test_validate_runs_without_error(self, mock_base):
        mock_metadata = MagicMock()
        mock_metadata.tables = {}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_with_matching_tables(self, mock_base):
        mock_table = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.name = "ts_code"
        mock_col2 = MagicMock()
        mock_col2.name = "trade_date"
        mock_table.columns = [mock_col1, mock_col2]

        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_with_missing_table_def(self, mock_base):
        mock_table = MagicMock()
        mock_table.columns = []
        mock_metadata = MagicMock()
        mock_metadata.tables = {"unknown_table": mock_table}
        mock_base.metadata = mock_metadata
        validate_schema_definitions()

    @patch("data.persistence.models.Base")
    def test_validate_with_phantom_columns(self, mock_base):
        mock_table = MagicMock()
        mock_col = MagicMock()
        mock_col.name = "real_col"
        mock_table.columns = [mock_col]

        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table}
        mock_base.metadata = mock_metadata

        TABLE_DEFINITIONS.get("stock_basic", {}).get("columns", {})
        TABLE_DEFINITIONS.setdefault("stock_basic", {})["columns"]["phantom_col"] = "test_phantom"
        try:
            validate_schema_definitions()
        finally:
            if "phantom_col" in TABLE_DEFINITIONS.get("stock_basic", {}).get("columns", {}):
                del TABLE_DEFINITIONS["stock_basic"]["columns"]["phantom_col"]


class TestDataDictionaryConstants:
    def test_common_columns_has_ts_code(self):
        assert "ts_code" in COMMON_COLUMNS

    def test_common_columns_has_trade_date(self):
        assert "trade_date" in COMMON_COLUMNS

    def test_common_columns_has_close(self):
        assert "close" in COMMON_COLUMNS

    def test_common_columns_values_are_i18n_keys(self):
        for col, key in COMMON_COLUMNS.items():
            assert isinstance(key, str), f"Column {col} value should be string"
            assert key.startswith("col_"), f"Column {col} i18n key should start with 'col_'"

    def test_table_definitions_is_dict(self):
        assert isinstance(TABLE_DEFINITIONS, dict)

    def test_table_definitions_has_daily_quotes(self):
        assert "daily_quotes" in TABLE_DEFINITIONS

    def test_table_definitions_has_stock_basic(self):
        assert "stock_basic" in TABLE_DEFINITIONS

    def test_table_definitions_entry_has_alias(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            assert "alias" in table_def, f"Table {table_name} missing 'alias' key"

    def test_table_definitions_entry_has_columns(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            assert "columns" in table_def, f"Table {table_name} missing 'columns' key"

    def test_table_definitions_columns_values_are_i18n_keys(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            columns = table_def.get("columns", {})
            for col, key in columns.items():
                assert isinstance(key, str), f"Table {table_name}, column {col} value should be string"
