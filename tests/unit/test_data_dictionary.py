from unittest.mock import patch, MagicMock
from data.data_dictionary import (
    TABLE_DEFINITIONS,
    COMMON_COLUMNS,
    validate_schema_definitions,
)


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
