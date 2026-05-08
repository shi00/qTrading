from unittest.mock import patch

from data.persistence.metadata_manager import MetaDataManager
from data.data_dictionary import TABLE_DEFINITIONS


class TestGetTableAlias:
    @patch("core.i18n.I18n")
    def test_table_with_alias(self, mock_i18n):
        mock_i18n.get.return_value = "股票列表"
        result = MetaDataManager.get_table_alias("stock_basic")
        assert "stock_basic" in result
        mock_i18n.get.assert_called_once()

    @patch("core.i18n.I18n")
    def test_table_without_alias(self, mock_i18n):
        result = MetaDataManager.get_table_alias("nonexistent_table")
        assert result == "nonexistent_table"

    @patch("core.i18n.I18n")
    def test_table_alias_format(self, mock_i18n):
        mock_i18n.get.return_value = "翻译"
        result = MetaDataManager.get_table_alias("stock_basic")
        assert "(" in result
        assert ")" in result


class TestGetColumnAlias:
    @patch("core.i18n.I18n")
    def test_column_with_table_specific(self, mock_i18n):
        mock_i18n.get.return_value = "股票代码"
        for table_name, table_def in TABLE_DEFINITIONS.items():
            cols = table_def.get("columns", {})
            if cols:
                col_name = next(iter(cols))
                result = MetaDataManager.get_column_alias(table_name, col_name)
                assert col_name in result
                break

    @patch("core.i18n.I18n")
    def test_column_with_common(self, mock_i18n):
        mock_i18n.get.return_value = "交易日期"
        result = MetaDataManager.get_column_alias(None, "trade_date")
        assert "trade_date" in result

    @patch("core.i18n.I18n")
    def test_column_rsi_dynamic(self, mock_i18n):
        result = MetaDataManager.get_column_alias(None, "rsi_14")
        assert result == "RSI(14)"

    @patch("core.i18n.I18n")
    def test_column_no_match(self, mock_i18n):
        result = MetaDataManager.get_column_alias(None, "unknown_column_xyz")
        assert result == "unknown_column_xyz"

    @patch("core.i18n.I18n")
    def test_column_none_table(self, mock_i18n):
        mock_i18n.get.return_value = "代码"
        result = MetaDataManager.get_column_alias(None, "ts_code")
        assert "ts_code" in result


class TestGetRawAlias:
    @patch("core.i18n.I18n")
    def test_with_context_table(self, mock_i18n):
        mock_i18n.get.return_value = "翻译"
        for table_name, table_def in TABLE_DEFINITIONS.items():
            cols = table_def.get("columns", {})
            if cols:
                col_name = next(iter(cols))
                result = MetaDataManager.get_raw_alias(col_name, context_table=table_name)
                assert result is not None
                break

    @patch("core.i18n.I18n")
    def test_with_common_column(self, mock_i18n):
        mock_i18n.get.return_value = "代码"
        result = MetaDataManager.get_raw_alias("ts_code")
        assert result is not None

    @patch("core.i18n.I18n")
    def test_no_match(self, mock_i18n):
        result = MetaDataManager.get_raw_alias("unknown_term_xyz")
        assert result == "unknown_term_xyz"

    @patch("core.i18n.I18n")
    def test_none_context(self, mock_i18n):
        mock_i18n.get.return_value = "日期"
        result = MetaDataManager.get_raw_alias("trade_date", context_table=None)
        assert result is not None
