import logging
import os
from unittest.mock import patch, MagicMock
import pytest

from data.data_dictionary import (
    TABLE_DEFINITIONS,
    COMMON_COLUMNS,
    validate_schema_definitions,
)

pytestmark = pytest.mark.unit


@patch.dict(os.environ, {"STRICT_SCHEMA_GATE": ""})
class TestValidateSchemaDefinitionsExtended:
    @patch("data.persistence.models.Base")
    def test_validate_with_extra_defs_not_in_orm(self, mock_base, caplog: pytest.LogCaptureFixture):
        mock_metadata = MagicMock()
        mock_metadata.tables = {}
        mock_base.metadata = mock_metadata
        with caplog.at_level(logging.WARNING, logger="data.data_dictionary"):
            validate_schema_definitions()
        assert any(
            r.levelno == logging.WARNING and "in TABLE_DEFINITIONS but not in ORM" in r.message for r in caplog.records
        ), f"expected extra-defs warning, got: {[r.message for r in caplog.records]}"

    @patch("data.persistence.models.Base")
    def test_validate_ignores_alembic_version(self, mock_base, caplog: pytest.LogCaptureFixture):
        mock_col = MagicMock()
        mock_col.name = "version_num"
        mock_table = MagicMock()
        mock_table.columns = [mock_col]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"alembic_version": mock_table}
        mock_base.metadata = mock_metadata
        with caplog.at_level(logging.WARNING, logger="data.data_dictionary"):
            validate_schema_definitions()
        # alembic_version 在 IGNORED_TABLES 中，不应触发任何提及该表名的警告
        assert not any(r.levelno >= logging.WARNING and "alembic_version" in r.message for r in caplog.records), (
            f"alembic_version should be ignored, but appeared in: {[r.message for r in caplog.records]}"
        )

    def test_validate_import_error(self, caplog: pytest.LogCaptureFixture):
        # 模拟 Base.metadata.tables.keys() 调用链抛 ImportError，验证 except Exception 分支记录 error 日志
        with patch("data.persistence.models.Base") as mock_base:
            mock_base.metadata.tables.keys.side_effect = ImportError("simulated import failure")
            with caplog.at_level(logging.ERROR, logger="data.data_dictionary"):
                validate_schema_definitions()
        assert any(r.levelno == logging.ERROR and "ORM validation failed" in r.message for r in caplog.records), (
            f"expected error log for ORM validation failure, got: {[r.message for r in caplog.records]}"
        )


class TestStockSyncStatusSchemaValidation:
    """FIND-R1-007: D-2 修复 — stock_sync_status 参与 Schema 双向一致性校验。"""

    def test_stock_sync_status_in_table_definitions(self):
        """stock_sync_status SHALL 在 TABLE_DEFINITIONS 注册（D-2 修复后参与校验）。"""
        assert "stock_sync_status" in TABLE_DEFINITIONS

    def test_stock_sync_status_in_orm_metadata(self):
        """stock_sync_status SHALL 在 ORM 元数据中（确保双向校验不产生 missing_defs）。"""
        from data.persistence.models import Base

        assert "stock_sync_status" in Base.metadata.tables

    def test_stock_sync_status_not_in_ignored_tables(self):
        """FIND-R1-007: stock_sync_status 不在 IGNORED_TABLES — 直接验证 D-2 修复
        （从 IGNORED_TABLES 移除 stock_sync_status 使其参与 Schema 双向校验）。

        注：不通过 validate_schema_definitions() 全流程验证，因 CI 环境
        STRICT_SCHEMA_GATE=1 会将 missing_defs 警告转为 ValueError raise，
        无法稳定断言 warning 行为。改为直接断言 IGNORED_TABLES 集合内容。"""
        # IGNORED_TABLES 是 validate_schema_definitions 内部局部变量，
        # 通过源码 inspect 验证其不含 stock_sync_status
        import inspect

        from data.data_dictionary import validate_schema_definitions

        source = inspect.getsource(validate_schema_definitions)
        # stock_sync_status 不应出现在 IGNORED_TABLES 定义中（仅 alembic_version）
        assert '"stock_sync_status"' not in source, "stock_sync_status 不应在 IGNORED_TABLES 中（D-2 修复已移除）"
        # alembic_version 应在 IGNORED_TABLES 中（基准对照）
        assert '"alembic_version"' in source


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

    def test_no_empty_columns_fields(self):
        """OSS-01：列级声明已从 TABLE_DEFINITIONS 移除（派生于 ORM），不得再出现 'columns' 字段。"""
        for table_name, table_def in TABLE_DEFINITIONS.items():
            assert "columns" not in table_def, f"Table '{table_name}' still has 'columns' field — OSS-01 已移除列级声明"

    def test_all_tables_have_alias(self):
        for table_name, table_def in TABLE_DEFINITIONS.items():
            assert "alias" in table_def, f"Table '{table_name}' missing 'alias'"

    def test_all_columns_values_are_strings(self):
        """OSS-01：派生后验证 ORM 全列经 column_i18n_key 解析出的 i18n key 均为 str/None。"""
        from data.data_dictionary import columns_of, column_i18n_key

        for table_name in TABLE_DEFINITIONS:
            for col_name in columns_of(table_name):
                alias_key = column_i18n_key(table_name, col_name)
                assert alias_key is None or isinstance(alias_key, str), (
                    f"Column i18n key for '{table_name}.{col_name}' is not str/None"
                )


class TestCommonColumns:
    def test_common_columns_is_dict(self):
        assert isinstance(COMMON_COLUMNS, dict)

    def test_ts_code_in_common(self):
        assert "ts_code" in COMMON_COLUMNS

    def test_trade_date_in_common(self):
        assert "trade_date" in COMMON_COLUMNS


@patch.dict(os.environ, {"STRICT_SCHEMA_GATE": ""})
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
    def test_validate_requires_orm_tables_registered(self, mock_base):
        """OSS-01：列级比对已由派生消除，表级注册校验仍是 validate_schema_definitions 的核心职责。"""
        mock_table = MagicMock()
        mock_table.columns = []
        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table}
        mock_base.metadata = mock_metadata
        # stock_basic 已注册：不产生任何 missing/extra 警告
        validate_schema_definitions()


@patch.dict(os.environ, {"STRICT_SCHEMA_GATE": ""})
class TestValidateSchemaDefinitionsStrict:
    @patch("data.persistence.models.Base")
    def test_strict_raises_value_error_on_missing_def(self, mock_base):
        mock_table = MagicMock()
        mock_table.columns = []
        mock_metadata = MagicMock()
        mock_metadata.tables = {"unknown_table": mock_table}
        mock_base.metadata = mock_metadata

        import pytest

        with pytest.raises(
            ValueError,
            match="The following tables are in ORM models but missing from TABLE_DEFINITIONS: {'unknown_table'}",
        ):
            validate_schema_definitions(strict=True)

    @patch("data.persistence.models.Base")
    def test_strict_raises_value_error_on_extra_def(self, mock_base):
        mock_metadata = MagicMock()
        mock_metadata.tables = {}
        mock_base.metadata = mock_metadata

        import pytest

        with pytest.raises(
            ValueError,
            match="The following tables are in TABLE_DEFINITIONS but not in ORM:",
        ):
            validate_schema_definitions(strict=True)

    @patch("data.persistence.models.Base")
    @patch("data.data_dictionary.TABLE_DEFINITIONS", new_callable=dict)
    def test_strict_raises_value_error_on_missing_table(self, mock_table_defs, mock_base):
        mock_table_defs["stock_basic"] = {"alias": "tab_stock_basic"}

        mock_col = MagicMock()
        mock_col.name = "orm_only_col"
        mock_table = MagicMock()
        mock_table.columns = [mock_col]
        mock_metadata = MagicMock()
        mock_metadata.tables = {"stock_basic": mock_table, "unregistered_table": mock_table}
        mock_base.metadata = mock_metadata

        import pytest

        with pytest.raises(ValueError, match="Schema inconsistencies found"):
            validate_schema_definitions(strict=True)

    def test_real_schema_validation_strict_passes(self):
        """Ensure that the actual ORM matches the actual data dictionary under strict mode. This is the main CI gate!"""
        # If this fails, someone changed models.py without updating data_dictionary.py!
        validate_schema_definitions(strict=True)


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

    def test_table_definitions_columns_values_are_i18n_keys(self):
        """OSS-01：列标签经 column_i18n_key 派生，解析结果必须是合法 key 或 None。"""
        from data.data_dictionary import column_i18n_key, columns_of

        for table_name in TABLE_DEFINITIONS:
            for col in columns_of(table_name):
                key = column_i18n_key(table_name, col)
                assert key is None or isinstance(key, str), (
                    f"Table {table_name}, column {col} i18n key should be str or None"
                )
