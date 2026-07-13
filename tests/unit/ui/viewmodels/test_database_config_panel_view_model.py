"""DatabaseConfigPanelViewModel 单元测试（Phase 3.2.1 TDD Red）。

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，消费方（DatabaseTab/OnboardingWizard）直接实例化以调用 commands。
"""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.persistence.db_config_service import ConnectionStatus
from ui.viewmodels.database_config_panel_view_model import (
    DatabaseConfigPanelViewModel,
    DatabaseConfigState,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock ConfigHandler 模块级 patch（VM 构造时加载配置）。"""
    with patch("ui.viewmodels.database_config_panel_view_model.ConfigHandler") as m:
        m.get_db_config.return_value = {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "database": "astock",
        }
        m.get_db_password.return_value = ""
        m.save_db_config.return_value = True
        yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.database_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_config_handler,
    *,
    on_save_callback: Callable | None = None,
    on_test_success_callback: Callable | None = None,
    on_change: Callable | None = None,
    on_loading_change: Callable | None = None,
    load_password: bool = False,
) -> DatabaseConfigPanelViewModel:
    return DatabaseConfigPanelViewModel(
        on_save_callback=on_save_callback,
        on_test_success_callback=on_test_success_callback,
        on_change=on_change,
        on_loading_change=on_loading_change,
        load_password=load_password,
    )


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        with pytest.raises(FrozenInstanceError):
            vm.state.host = "modified"  # type: ignore[misc]

    def test_state_default_values(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        assert vm.state.host == "localhost"
        assert vm.state.port == "5432"
        assert vm.state.user == "postgres"
        assert vm.state.database == "astock"
        assert vm.state.create_if_not_exists is True
        assert vm.state.is_verifying is False
        assert vm.state.is_saving is False
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"
        assert vm.state.db_info is None


# --- Config loading ---


class TestConfigLoading:
    def test_load_config_populates_state(self, mock_config_handler):
        mock_config_handler.get_db_config.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "user": "admin",
            "database": "mydb",
        }
        vm = _make_vm(mock_config_handler)
        assert vm.state.host == "db.example.com"
        assert vm.state.port == "3306"
        assert vm.state.user == "admin"
        assert vm.state.database == "mydb"

    def test_load_config_defaults_when_empty(self, mock_config_handler):
        mock_config_handler.get_db_config.return_value = {}
        vm = _make_vm(mock_config_handler)
        assert vm.state.host == "localhost"
        assert vm.state.port == "5432"
        assert vm.state.user == "postgres"
        assert vm.state.database == "astock"

    def test_load_password_true_loads_password(self, mock_config_handler):
        mock_config_handler.get_db_password.return_value = "secret123"
        vm = _make_vm(mock_config_handler, load_password=True)
        assert vm.state.password == "secret123"

    def test_load_password_false_does_not_load_password(self, mock_config_handler):
        mock_config_handler.get_db_password.return_value = "secret123"
        vm = _make_vm(mock_config_handler, load_password=False)
        assert vm.state.password == ""


# --- Update commands ---


class TestUpdateCommands:
    def test_update_host(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("newhost")
        assert vm.state.host == "newhost"

    def test_update_port_valid(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_port("6543")
        assert vm.state.port == "6543"

    def test_update_port_invalid_keeps_raw_value(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_port("abc")
        assert vm.state.port == "abc"

    def test_update_port_empty_keeps_empty(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_port("")
        assert vm.state.port == ""

    def test_update_user(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_user("newuser")
        assert vm.state.user == "newuser"

    def test_update_password(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_password("newpass")
        assert vm.state.password == "newpass"

    def test_update_database(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_database("newdb")
        assert vm.state.database == "newdb"

    def test_update_create_if_not_exists(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_create_if_not_exists(False)
        assert vm.state.create_if_not_exists is False

    def test_update_triggers_on_change(self, mock_config_handler):
        on_change = MagicMock()
        vm = _make_vm(mock_config_handler, on_change=on_change)
        vm.update_host("newhost")
        on_change.assert_called_once()

    def test_update_without_on_change_no_error(self, mock_config_handler):
        vm = _make_vm(mock_config_handler, on_change=None)
        vm.update_host("newhost")  # 不应抛异常


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[DatabaseConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_host("newhost")
        assert len(received) == 1
        assert received[0].host == "newhost"

    def test_unsubscribe_stops_receiving(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[DatabaseConfigState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.update_host("newhost")
        assert len(received) == 0

    def test_multiple_subscribers_all_notified(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received_a: list[DatabaseConfigState] = []
        received_b: list[DatabaseConfigState] = []
        vm.subscribe(lambda s: received_a.append(s))
        vm.subscribe(lambda s: received_b.append(s))
        vm.update_host("newhost")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_dispose_clears_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[DatabaseConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.update_host("newhost")
        assert len(received) == 0


# --- get_config / set_config ---


class TestGetSetConfig:
    def test_get_config_returns_all_fields(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("myhost")
        vm.update_port("5433")
        vm.update_user("myuser")
        vm.update_password("mypass")
        vm.update_database("mydb")
        vm.update_create_if_not_exists(True)

        config = vm.get_config()

        assert config["host"] == "myhost"
        assert config["port"] == 5433
        assert config["user"] == "myuser"
        assert config["password"] == "mypass"
        assert config["database"] == "mydb"
        assert config["create_if_not_exists"] is True

    def test_set_config_updates_state(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.set_config(
            {
                "host": "newhost",
                "port": 3306,
                "user": "newuser",
                "password": "newpass",
                "database": "newdb",
                "create_if_not_exists": False,
            }
        )
        assert vm.state.host == "newhost"
        assert vm.state.port == "3306"
        assert vm.state.user == "newuser"
        assert vm.state.password == "newpass"
        assert vm.state.database == "newdb"
        assert vm.state.create_if_not_exists is False

    def test_set_config_notifies_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[DatabaseConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_config({"host": "newhost"})
        assert len(received) == 1


# --- reload_config ---


class TestReloadConfig:
    def test_reload_config_reloads_from_config_handler(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        # 修改 state
        vm.update_host("modified")
        assert vm.state.host == "modified"
        # ConfigHandler 返回新值
        mock_config_handler.get_db_config.return_value = {
            "host": "reloaded.com",
            "port": 5432,
            "user": "postgres",
            "database": "astock",
        }
        vm.reload_config()
        assert vm.state.host == "reloaded.com"

    def test_reload_config_notifies_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[DatabaseConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.reload_config()
        assert len(received) == 1


# --- validate ---


class TestValidate:
    def test_validate_empty_host_returns_false(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("")
        is_valid, msg = vm.validate()
        assert is_valid is False
        assert msg is not None
        assert msg.key == "wizard_err_host_required"

    def test_validate_empty_user_returns_false(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_user("")
        is_valid, msg = vm.validate()
        assert is_valid is False
        assert msg is not None
        assert msg.key == "wizard_err_user_required"

    def test_validate_empty_database_returns_false(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_user("postgres")
        vm.update_database("")
        is_valid, msg = vm.validate()
        assert is_valid is False
        assert msg is not None
        assert msg.key == "wizard_err_db_required"

    def test_validate_invalid_port_returns_false(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("abc")
        vm.update_user("postgres")
        vm.update_database("astock")
        is_valid, msg = vm.validate()
        assert is_valid is False
        assert msg is not None
        assert msg.key == "wizard_err_port_number"

    def test_validate_port_out_of_range_returns_false(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("99999")
        vm.update_user("postgres")
        vm.update_database("astock")
        is_valid, msg = vm.validate()
        assert is_valid is False
        assert msg is not None
        assert msg.key == "wizard_err_port_range"

    def test_validate_valid_config_returns_true(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_database("astock")
        is_valid, msg = vm.validate()
        assert is_valid is True
        assert msg is None


# --- test_connection (async) ---


class TestConnectionCommand:
    @pytest.mark.asyncio
    async def test_test_connection_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "Connection successful"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.get_database_info = AsyncMock(return_value=None)
            result = await vm.test_connection()

        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_test_connection_database_not_found_with_create(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm.update_create_if_not_exists(True)

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "Database not found"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await vm.test_connection()

        assert result is True
        assert vm.state.status_type == "warning"

    @pytest.mark.asyncio
    async def test_test_connection_database_not_found_without_create(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm.update_create_if_not_exists(False)

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "Database not found"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await vm.test_connection()

        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.CONNECTION_ERROR
        mock_result.message = "Connection failed"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await vm.test_connection()

        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_test_connection_validation_failure(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("")  # 空 host 导致 validation 失败
        result = await vm.test_connection()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_test_connection_triggers_on_loading_change(self, mock_config_handler, mock_thread_pool):
        on_loading_change = MagicMock()
        vm = _make_vm(mock_config_handler, on_loading_change=on_loading_change)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.get_database_info = AsyncMock(return_value=None)
            await vm.test_connection()

        # on_loading_change(True) at start, on_loading_change(False) at end
        assert on_loading_change.call_count == 2
        assert on_loading_change.call_args_list[0][0][0] is True
        assert on_loading_change.call_args_list[1][0][0] is False

    @pytest.mark.asyncio
    async def test_test_connection_success_triggers_on_test_success_callback(
        self, mock_config_handler, mock_thread_pool
    ):
        on_test_success = MagicMock()
        vm = _make_vm(mock_config_handler, on_test_success_callback=on_test_success)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.get_database_info = AsyncMock(return_value=None)
            await vm.test_connection()

        on_test_success.assert_called_once()


# --- save_config (async) ---


class TestSaveConfigCommand:
    @pytest.mark.asyncio
    async def test_save_config_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, ""))
            result = await vm.save_config()

        assert result is True
        assert vm.state.status_type == "success"
        mock_config_handler.save_db_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_database_not_found_with_create(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm.update_create_if_not_exists(True)

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "not found"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.create_database = AsyncMock(return_value=(True, ""))
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, ""))
            result = await vm.save_config()

        assert result is True
        mock_svc.create_database.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_failure(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.CONNECTION_ERROR
        mock_result.message = "failed"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await vm.save_config()

        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_save_config_validation_failure(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_host("")  # 空 host
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_save_config_success_triggers_on_save_callback(self, mock_config_handler, mock_thread_pool):
        on_save = MagicMock()
        vm = _make_vm(mock_config_handler, on_save_callback=on_save)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, ""))
            await vm.save_config()

        on_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_already_saving_returns_false(self, mock_config_handler, mock_thread_pool):
        """重入守卫：is_saving=True 时直接返回 False，不执行保存流程。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        # 模拟正在保存中
        vm._set_state(is_saving=True)  # type: ignore[attr-defined]

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock()
            mock_svc.ensure_tables_exist = AsyncMock()
            result = await vm.save_config()

        assert result is False
        assert vm.state.status_type == "warning"
        # 不应触达 service 层
        mock_svc.test_connection.assert_not_called()
        mock_svc.ensure_tables_exist.assert_not_called()


# --- 覆盖率补全：subscribe/_unsubscribe 边界 + get_config 异常分支 ---


class TestSubscribeNotifyEdgeCases:
    def test_unsubscribe_twice_does_not_raise(self, mock_config_handler):
        """重复退订不应抛异常（覆盖 _unsubscribe 中 callback 已不在列表的 False 分支）。"""
        vm = _make_vm(mock_config_handler)
        unsub = vm.subscribe(lambda s: None)
        unsub()
        unsub()  # 第二次退订：callback 已不在 _subscribers
        assert vm._subscribers == []  # type: ignore[attr-defined]


class TestGetConfigEdgeCases:
    def test_get_config_invalid_port_falls_back_to_5432(self, mock_config_handler):
        """port 无法解析为 int 时 fallback 5432（覆盖 get_config except 分支）。"""
        vm = _make_vm(mock_config_handler)
        vm.update_port("abc")
        config = vm.get_config()
        assert config["port"] == 5432


# --- 覆盖率补全：test_connection 边界与异常路径 ---


class TestConnectionEdgeCases:
    @pytest.mark.asyncio
    async def test_test_connection_already_verifying_returns_false(self, mock_config_handler, mock_thread_pool):
        """重入守卫：is_verifying=True 时直接返回 False，不执行测试流程。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm._set_state(is_verifying=True)  # type: ignore[attr-defined]

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock()
            result = await vm.test_connection()

        assert result is False
        assert vm.state.status_type == "warning"
        mock_svc.test_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_test_connection_success_with_db_info(self, mock_config_handler, mock_thread_pool):
        """SUCCESS 且 get_database_info 返回非空时，db_info 写入 state。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        mock_info = MagicMock()
        mock_info.version = "PostgreSQL 16.0"
        mock_info.size = "100 MB"
        mock_info.table_count = 42

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.get_database_info = AsyncMock(return_value=mock_info)
            result = await vm.test_connection()

        assert result is True
        assert vm.state.db_info is not None
        assert vm.state.db_info.params["version"] == "PostgreSQL 16.0"
        assert vm.state.db_info.params["size"] == "100 MB"
        assert vm.state.db_info.params["tables"] == 42

    @pytest.mark.asyncio
    async def test_test_connection_db_not_found_with_create_triggers_callback(
        self, mock_config_handler, mock_thread_pool
    ):
        """DATABASE_NOT_FOUND + create_if_not_exists=True 时触发 on_test_success_callback。"""
        on_test_success = MagicMock()
        vm = _make_vm(mock_config_handler, on_test_success_callback=on_test_success)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm.update_create_if_not_exists(True)

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "not found"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await vm.test_connection()

        assert result is True
        on_test_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_value_error_exception(self, mock_config_handler, mock_thread_pool):
        """test_connection 抛 ValueError 时分类错误并显示 error 状态。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(side_effect=ValueError("bad value"))
            result = await vm.test_connection()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_test_connection_generic_exception(self, mock_config_handler, mock_thread_pool):
        """test_connection 抛通用 Exception 时分类错误并显示 error 状态。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(side_effect=RuntimeError("boom"))
            result = await vm.test_connection()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.is_verifying is False


# --- 覆盖率补全：save_config 异常路径 ---


class TestSaveConfigEdgeCases:
    @pytest.mark.asyncio
    async def test_save_config_create_database_fails(self, mock_config_handler, mock_thread_pool):
        """DATABASE_NOT_FOUND + create_if_not_exists=True 但 create_database 失败时返回 False。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")
        vm.update_create_if_not_exists(True)

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "not found"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.create_database = AsyncMock(return_value=(False, "create failed"))
            result = await vm.save_config()

        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_save_config_ensure_tables_fails(self, mock_config_handler, mock_thread_pool):
        """ensure_tables_exist 失败时返回 False 并显示 error。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "ok"

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(False, "tables failed"))
            result = await vm.save_config()

        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_save_config_exception(self, mock_config_handler, mock_thread_pool):
        """save_config 流程抛通用 Exception 时分类错误并返回 False。"""
        vm = _make_vm(mock_config_handler)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("pass")
        vm.update_database("astock")

        with patch("ui.viewmodels.database_config_panel_view_model.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(side_effect=RuntimeError("boom"))
            result = await vm.save_config()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.is_saving is False
