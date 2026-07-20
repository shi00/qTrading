# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import sqlalchemy as sa

from data.persistence.data_explorer_query_client import DataExplorerQueryClient

pytestmark = pytest.mark.unit


def _make_dm():
    """创建带 mock 引擎的 DataExplorerQueryClient 实例。

    注意：_engine 是只读 property，返回类级别 _shared_engine。
    必须设置 DataExplorerQueryClient._shared_engine 而非实例属性。
    """
    DataExplorerQueryClient._shared_engine = MagicMock()
    return DataExplorerQueryClient()


class TestDataExplorerQueryClientInit:
    def test_init(self):
        dm = DataExplorerQueryClient()
        assert dm._engine is None  # _shared_engine 为 None


class TestEnsureEngine:
    @patch("data.persistence.data_explorer_query_client.ConfigHandler")
    def test_no_db_url(self, mock_ch):
        mock_ch.get_db_url.return_value = None
        dm = DataExplorerQueryClient()
        with pytest.raises(RuntimeError, match="not configured"):
            dm._ensure_engine()

    @patch("data.persistence.data_explorer_query_client.ConfigHandler")
    def test_empty_string_db_url(self, mock_ch):
        """get_db_url() 返回空字符串（非 None）也应抛 RuntimeError"""
        mock_ch.get_db_url.return_value = ""
        dm = DataExplorerQueryClient()
        with pytest.raises(RuntimeError, match="not configured"):
            dm._ensure_engine()

    @patch("data.persistence.data_explorer_query_client.get_db_pool_config")
    @patch("data.persistence.data_explorer_query_client.sa.create_engine")
    @patch("data.persistence.data_explorer_query_client.ConfigHandler")
    def test_success(self, mock_ch, mock_create, mock_pool_config):
        mock_ch.get_db_url.return_value = "postgresql+asyncpg://user:pass@host/db"
        mock_pool_config.return_value = {
            "pool_size": 10,
            "max_overflow": 5,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
        mock_create.return_value = MagicMock()
        dm = DataExplorerQueryClient()
        dm._ensure_engine()
        # Verify sync URL is used (asyncpg driver stripped)
        mock_create.assert_called_once()
        call_url = mock_create.call_args[0][0]
        assert "+asyncpg" not in call_url

    def test_already_initialized(self):
        dm = _make_dm()
        old_engine = dm._engine
        dm._ensure_engine()
        assert dm._engine is old_engine


class TestClose:
    def test_close_is_noop_does_not_dispose_shared_engine(self):
        """close() 是空操作，不释放共享引擎（由 close_all 统一管理）。"""
        dm = _make_dm()
        engine = dm._engine
        dm.close()
        # 引擎仍然存在
        assert dm._engine is engine

    def test_close_no_engine(self):
        """无引擎时 close() 不抛异常。"""
        dm = DataExplorerQueryClient()
        dm.close()  # 不应抛异常


class TestCloseAll:
    """验证 DataExplorerQueryClient.close_all() 关闭共享引擎。"""

    def teardown_method(self):
        DataExplorerQueryClient.close_all()

    def test_close_all_disposes_shared_engine(self):
        """close_all() 应 dispose 共享引擎并置为 None。"""
        dm = _make_dm()
        engine = DataExplorerQueryClient._shared_engine

        DataExplorerQueryClient.close_all()

        engine.dispose.assert_called_once()  # type: ignore[union-attr]
        assert DataExplorerQueryClient._shared_engine is None
        assert dm._engine is None  # property 返回 None

    def test_close_all_idempotent(self):
        """close_all() 幂等：多次调用不抛异常。"""
        _make_dm()
        DataExplorerQueryClient.close_all()
        DataExplorerQueryClient.close_all()  # 第二次调用不抛异常
        assert DataExplorerQueryClient._shared_engine is None

    def test_close_all_no_engine(self):
        """无引擎时 close_all() 不抛异常。"""
        DataExplorerQueryClient.close_all()  # 不应抛异常
        assert DataExplorerQueryClient._shared_engine is None


class TestGetAllTables:
    def test_success(self):
        dm = _make_dm()
        mock_insp = MagicMock()
        mock_insp.get_table_names.return_value = ["stock_basic", "daily_quotes"]
        with patch("data.persistence.data_explorer_query_client.sa.inspect", return_value=mock_insp):
            result = dm.get_all_tables()
            assert result == ["daily_quotes", "stock_basic"]

    def test_exception(self):
        dm = _make_dm()
        with patch(
            "data.persistence.data_explorer_query_client.sa.inspect",
            side_effect=Exception("error"),
        ):
            result = dm.get_all_tables()
            assert result == []


class TestGetTableSchema:
    def test_success(self):
        dm = _make_dm()
        mock_insp = MagicMock()
        mock_insp.get_columns.return_value = [
            {"name": "ts_code", "type": sa.String()},
            {"name": "trade_date", "type": sa.String()},
        ]
        with (
            patch.object(dm, "_validate_table_name"),
            patch("data.persistence.data_explorer_query_client.sa.inspect", return_value=mock_insp),
        ):
            result = dm.get_table_schema("stock_basic")
            assert len(result) == 2
            assert result[0]["name"] == "ts_code"

    def test_invalid_table(self):
        dm = _make_dm()
        with patch.object(dm, "_validate_table_name", side_effect=ValueError("Invalid")):
            result = dm.get_table_schema("invalid")
            assert result == []

    def test_exception(self):
        dm = _make_dm()
        with (
            patch.object(dm, "_validate_table_name"),
            patch(
                "data.persistence.data_explorer_query_client.sa.inspect",
                side_effect=Exception("error"),
            ),
        ):
            result = dm.get_table_schema("stock_basic")
            assert result == []


class TestGetTableCount:
    def test_success(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 100
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.get_table_count("stock_basic")
            assert result == 100

    def test_with_filters(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 50
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.get_table_count("stock_basic", filters=[("ts_code", "=", "000001.SZ")])
            assert result == 50

    def test_exception(self):
        dm = _make_dm()
        with patch.object(dm, "_validate_table_name", side_effect=Exception("error")):
            result = dm.get_table_count("invalid")
            assert result == 0


class TestValidateTableName:
    def test_valid(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic", "daily_quotes"]):
            dm._validate_table_name("stock_basic")

    def test_invalid(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("nonexistent")

    def test_sql_injection_drop_table(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("stock_basic; DROP TABLE stock_basic;--")

    def test_sql_injection_union(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("stock_basic UNION SELECT * FROM users--")

    def test_empty_string(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("")

    def test_whitespace_only(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("   ")

    def test_semicolon_injection(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name(";")

    def test_comment_injection(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("--")

    def test_case_sensitive_rejection(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("STOCK_BASIC")

    def test_partial_name_rejection(self):
        dm = _make_dm()
        with patch.object(dm, "get_all_tables", return_value=["stock_basic"]):
            with pytest.raises(ValueError, match="Invalid"):
                dm._validate_table_name("stock")


class TestApplyFilters:
    def test_no_filters(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, None)
        assert result is stmt

    def test_empty_filters(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [])
        assert result is stmt

    def test_with_eq_filter(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [("ts_code", "=", "000001.SZ")])
        assert isinstance(result, sa.Select)

    def test_with_like_filter(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [("name", "LIKE", "银行")])
        assert isinstance(result, sa.Select)

    def test_with_like_already_has_wildcard(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [("name", "LIKE", "%银行%")])
        assert isinstance(result, sa.Select)

    def test_unsupported_operator(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [("col", "XOR", "val")])
        assert result is stmt

    def test_schema_col_whitelist(self):
        stmt = sa.select(sa.text("*"))
        result = DataExplorerQueryClient._apply_filters(stmt, [("invalid_col", "=", "val")], schema_cols={"ts_code"})
        assert result is stmt

    def test_all_operators(self):
        for op in [">", "<", ">=", "<=", "!="]:
            stmt = sa.select(sa.text("*"))
            result = DataExplorerQueryClient._apply_filters(stmt, [("col", op, "val")], schema_cols={"col"})
            assert isinstance(result, sa.Select)


class TestQueryTable:
    def test_success(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("000001.SZ",)]
        mock_result.keys.return_value = ["ts_code"]
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("stock_basic")
            assert isinstance(result, pd.DataFrame)

    def test_empty_result(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("stock_basic")
            assert result.empty

    def test_with_sort(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("000001.SZ",)]
        mock_result.keys.return_value = ["ts_code"]
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("stock_basic", sort_col="ts_code", sort_asc=True)
            assert isinstance(result, pd.DataFrame)

    def test_with_invalid_sort_col(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("000001.SZ",)]
        mock_result.keys.return_value = ["ts_code"]
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("stock_basic", sort_col="invalid_col")
            assert isinstance(result, pd.DataFrame)

    def test_daily_quotes_default_sort(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("000001.SZ",)]
        mock_result.keys.return_value = ["ts_code"]
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("daily_quotes")
            assert isinstance(result, pd.DataFrame)

    def test_with_filters(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("000001.SZ",)]
        mock_result.keys.return_value = ["ts_code"]
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(dm, "_validate_table_name"),
            patch.object(dm, "get_table_schema", return_value=[{"name": "ts_code"}]),
        ):
            result = dm.query_table("stock_basic", filters=[("ts_code", "=", "000001.SZ")])
            assert isinstance(result, pd.DataFrame)

    def test_exception(self):
        dm = _make_dm()
        with patch.object(dm, "_validate_table_name", side_effect=Exception("error")):
            result = dm.query_table("invalid")
            assert result.empty


class TestExecuteSql:
    def test_no_engine(self):
        dm = DataExplorerQueryClient()
        with patch.object(dm, "_ensure_engine", side_effect=RuntimeError("not configured")):
            result = dm.execute_sql("SELECT 1")
            assert result["success"] is False
            assert "not configured" in result["error"]

    def test_empty_query(self):
        dm = _make_dm()
        result = dm.execute_sql("")
        assert result["success"] is False
        assert "Empty" in result["error"]

    def test_non_select_statement(self):
        dm = _make_dm()
        result = dm.execute_sql("DELETE FROM stock_basic")
        assert result["success"] is False
        assert "SELECT" in result["error"]

    def test_dangerous_keyword(self):
        dm = _make_dm()
        result = dm.execute_sql("DROP TABLE stock_basic")
        assert result["success"] is False
        assert "Security" in result["error"]

    def test_select_success(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["ts_code"]
        mock_result.fetchmany.return_value = [("000001.SZ",)]
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = dm.execute_sql("SELECT * FROM stock_basic")
        assert result["success"] is True
        assert isinstance(result["data"], pd.DataFrame)

    def test_select_truncated(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["ts_code"]
        mock_result.fetchmany.return_value = [("000001.SZ",)] * 2000
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = dm.execute_sql("SELECT * FROM stock_basic")
        assert result["success"] is True
        assert "truncated" in result["error"].lower()

    def test_sql_parse_error(self):
        dm = _make_dm()
        with patch(
            "data.persistence.data_explorer_query_client.sqlparse.parse",
            side_effect=Exception("parse error"),
        ):
            result = dm.execute_sql("SELECT 1")
            assert result["success"] is False
            assert "Parse" in result["error"]

    def test_execution_error(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.side_effect = Exception("exec error")
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = dm.execute_sql("SELECT * FROM nonexistent")
        assert result["success"] is False

    def test_insert_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("INSERT INTO stock_basic VALUES (1)")
        assert result["success"] is False

    def test_update_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("UPDATE stock_basic SET name='x'")
        assert result["success"] is False

    def test_alter_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("ALTER TABLE stock_basic ADD col INT")
        assert result["success"] is False

    def test_create_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("CREATE TABLE test (id INT)")
        assert result["success"] is False

    def test_truncate_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("TRUNCATE TABLE stock_basic")
        assert result["success"] is False

    def test_execute_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("EXECUTE sp_test")
        assert result["success"] is False

    def test_grant_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("GRANT ALL ON stock_basic TO user")
        assert result["success"] is False

    def test_revoke_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("REVOKE ALL ON stock_basic FROM user")
        assert result["success"] is False

    def test_read_only_transaction_enforced(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["col"]
        mock_result.fetchmany.return_value = [(1,)]
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = dm.execute_sql("SELECT 1")
        assert result["success"] is True

        mock_conn.execution_options.assert_called_once_with(isolation_level="REPEATABLE READ")

        execute_calls = mock_conn.execute.call_args_list
        assert len(execute_calls) >= 2
        first_sql = str(execute_calls[0][0][0])
        assert "READ ONLY" in first_sql.upper()

    def test_read_only_transaction_rejects_write(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.side_effect = Exception("cannot execute INSERT in a read-only transaction")
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = dm.execute_sql("SELECT INTO temp_table FROM stock_basic")
        assert result["success"] is False
        assert "read-only" in result["error"].lower()

    def test_dangerous_keyword_tab_bypass_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("DROP\tstock_basic")
        assert result["success"] is False
        assert "DROP" in result["error"]

    def test_dangerous_keyword_newline_bypass_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("DELETE\nFROM stock_basic")
        assert result["success"] is False
        assert "DELETE" in result["error"]

    def test_dangerous_keyword_comment_bypass_blocked(self):
        dm = _make_dm()
        result = dm.execute_sql("ALTER(--comment)stock_basic ADD col INT")
        assert result["success"] is False

    def test_dangerous_keyword_case_insensitive(self):
        dm = _make_dm()
        result = dm.execute_sql("drop table stock_basic")
        assert result["success"] is False
        assert "DROP" in result["error"]

    def test_safe_word_not_blocked(self):
        dm = _make_dm()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["col"]
        mock_result.fetchmany.return_value = [(1,)]
        mock_conn.execution_options.return_value = mock_conn
        mock_conn.execute.return_value = mock_result
        dm._engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        dm._engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = dm.execute_sql("SELECT * FROM stock_basic WHERE name = 'updated record'")
        assert result["success"] is True


class TestDatabaseConfigServiceSQLInjection:
    @pytest.mark.asyncio
    async def test_create_database_rejects_invalid_name_semicolon(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database(
                "localhost", 5432, "user", "pass", "test; DROP TABLE stock_basic"
            )
            assert ok is False
            assert "test; DROP TABLE stock_basic" in msg

    @pytest.mark.asyncio
    async def test_create_database_rejects_invalid_name_dash(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "test-db")
            assert ok is False
            assert "test-db" in msg

    @pytest.mark.asyncio
    async def test_create_database_rejects_invalid_name_space(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "test db")
            assert ok is False
            assert "test db" in msg

    @pytest.mark.asyncio
    async def test_create_database_rejects_invalid_name_starts_with_digit(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "1test")
            assert ok is False
            assert "1test" in msg

    @pytest.mark.asyncio
    async def test_create_database_accepts_valid_name(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "astock")
            assert ok is True

    @pytest.mark.asyncio
    async def test_create_database_accepts_underscore_name(self):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch("data.persistence.db_config_service.asyncpg.connect") as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_connect.return_value = mock_conn
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "my_database_v2")
            assert ok is True
