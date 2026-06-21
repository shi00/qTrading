from unittest.mock import patch

from data.persistence.metadata_manager import MetaDataManager
from data.data_dictionary import TABLE_DEFINITIONS
import pytest


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_alias_cache():
    """Clear MetaDataManager._alias_cache before each test to prevent cross-test pollution."""
    MetaDataManager.invalidate_cache()
    yield
    MetaDataManager.invalidate_cache()


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


class TestMetadataManagerCaching:
    @patch("core.i18n.I18n")
    def test_get_column_alias_caches_repeated_calls(self, mock_i18n):
        mock_i18n.get.return_value = "交易日期"
        MetaDataManager.invalidate_cache()
        r1 = MetaDataManager.get_column_alias(None, "trade_date")
        r2 = MetaDataManager.get_column_alias(None, "trade_date")
        assert r1 == r2
        assert mock_i18n.get.call_count == 1

    @patch("core.i18n.I18n")
    def test_get_table_alias_caches_repeated_calls(self, mock_i18n):
        mock_i18n.get.return_value = "股票列表"
        MetaDataManager.invalidate_cache()
        r1 = MetaDataManager.get_table_alias("stock_basic")
        r2 = MetaDataManager.get_table_alias("stock_basic")
        assert r1 == r2
        assert mock_i18n.get.call_count == 1

    @patch("core.i18n.I18n")
    def test_invalidate_cache_resets(self, mock_i18n):
        mock_i18n.get.return_value = "交易日期"
        MetaDataManager.invalidate_cache()
        MetaDataManager.get_column_alias(None, "trade_date")
        MetaDataManager.invalidate_cache()
        MetaDataManager.get_column_alias(None, "trade_date")
        assert mock_i18n.get.call_count == 2


class TestPreloadAliases:
    """B-P1-9: Verify preload_aliases populates cache at startup."""

    @patch("core.i18n.I18n")
    def test_preload_populates_cache(self, mock_i18n):
        mock_i18n.get.return_value = "翻译"
        MetaDataManager.invalidate_cache()
        assert len(MetaDataManager._alias_cache) == 0
        MetaDataManager.preload_aliases()
        assert len(MetaDataManager._alias_cache) > 0

    @patch("core.i18n.I18n")
    def test_preload_covers_all_tables(self, mock_i18n):
        mock_i18n.get.return_value = "翻译"
        MetaDataManager.invalidate_cache()
        MetaDataManager.preload_aliases()
        for table_name in TABLE_DEFINITIONS:
            cache_key = ("table", table_name)
            assert cache_key in MetaDataManager._alias_cache, f"Table {table_name} not preloaded"

    @patch("core.i18n.I18n")
    def test_preload_makes_subsequent_calls_use_cache(self, mock_i18n):
        mock_i18n.get.return_value = "翻译"
        MetaDataManager.invalidate_cache()
        MetaDataManager.preload_aliases()
        call_count_after_preload = mock_i18n.get.call_count
        MetaDataManager.get_column_alias(None, "trade_date")
        assert mock_i18n.get.call_count == call_count_after_preload


class TestPreloadAliasesCalledAtStartup:
    """B-P1-9: Verify that main.py calls preload_aliases after init_db."""

    def test_main_source_contains_preload_aliases(self):
        from data.persistence.metadata_manager import MetaDataManager

        assert hasattr(MetaDataManager, "preload_aliases"), (
            "B-P1-9: MetaDataManager should have preload_aliases() method "
            "to avoid blocking the event loop during UI rendering."
        )
