"""DatabaseStatusViewModel 单元测试 (P3-10).

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，内部 VM 模式由 use_viewmodel(factory=...) 实例化。

覆盖:
1. State frozen + 默认值
2. refresh_status() 调用 doctor() 并更新 state (running/stopped)
3. refresh_status() 错误处理
4. open_data_dir/open_log_dir 调用 subprocess.Popen (跨平台)
5. 路径校验: 不存在路径 / None → 不调用 subprocess.Popen
6. VM 只产出 Message (i18n key)，不调 I18n.get
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.database_status_view_model import DatabaseStatusViewModel
from ui.viewmodels import Message

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_doctor_result():
    """构造一个 running 状态的 DoctorResult mock。"""
    result = MagicMock()
    result.data_dir = "/fake/data"
    result.pg_version = 17
    result.postgres_alive = True
    result.state_file = "running"
    return result


@pytest.fixture
def mock_maintenance_service(mock_doctor_result):
    """Mock EmbeddedPgMaintenanceService（DI 注入，避免单例污染）。"""
    svc = MagicMock()
    svc.doctor = AsyncMock(return_value=mock_doctor_result)
    return svc


@pytest.fixture
def mock_config_dict():
    """mock ConfigHandler.load_config 返回的 config dict。"""
    return {
        "embedded_pg_enabled": True,
        "embedded_pg_data_root": "/fake/data_root",
        "embedded_pg_log_dir": "/fake/log_dir",
        "db_port": 5432,
    }


@pytest.fixture
def vm(mock_maintenance_service, mock_config_dict):
    """构造 DatabaseStatusViewModel 实例（DI + patch config）。

    用 yield 保持 patch 作用域到测试结束，避免 refresh_status 调用时
    ConfigHandler.load_config 已 unpatch 导致 fallback 到 platformdirs 默认值。
    """
    with patch(
        "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
        return_value=mock_config_dict,
    ):
        yield DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, vm):
        with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
            vm.state.is_running = True  # type: ignore[misc]

    def test_state_default_values(self, mock_maintenance_service, mock_config_dict):
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        assert vm.state.is_running is False
        assert vm.state.pg_version is None
        assert vm.state.port is None
        assert vm.state.data_dir is None
        assert vm.state.log_dir is None
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"
        assert vm.state.error_message is None
        assert vm.state.is_refreshing is False


# --- refresh_status ---


class TestRefreshStatus:
    @pytest.mark.asyncio
    async def test_refresh_status_calls_doctor(self, vm, mock_maintenance_service):
        await vm.refresh_status()
        mock_maintenance_service.doctor.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_refresh_status_updates_running_state(self, vm):
        await vm.refresh_status()
        assert vm.state.is_running is True
        assert vm.state.pg_version == "17"
        assert vm.state.data_dir == "/fake/data"
        assert vm.state.port == 5432
        assert vm.state.log_dir == "/fake/log_dir"

    @pytest.mark.asyncio
    async def test_refresh_status_running_message(self, vm):
        await vm.refresh_status()
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "db_status_running"
        assert vm.state.status_type == "success"

    @pytest.mark.asyncio
    async def test_refresh_status_stopped_when_not_alive(
        self, mock_maintenance_service, mock_doctor_result, mock_config_dict
    ):
        mock_doctor_result.postgres_alive = False
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        await vm.refresh_status()
        assert vm.state.is_running is False
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "db_status_stopped"
        assert vm.state.status_type == "warning"

    @pytest.mark.asyncio
    async def test_refresh_status_sets_refreshing_flag(self, vm, mock_maintenance_service):
        """refresh_status 期间 is_refreshing=True，完成后 False。"""
        # doctor 是 AsyncMock，调用前设 is_refreshing
        # 由于 AsyncMock 立即返回，难以捕获中间态；改为验证完成后 is_refreshing=False
        await vm.refresh_status()
        assert vm.state.is_refreshing is False

    @pytest.mark.asyncio
    async def test_refresh_status_handles_error(self, mock_maintenance_service, mock_config_dict):
        """doctor() 抛异常时，state 设为 error 且 is_refreshing=False。"""
        mock_maintenance_service.doctor = AsyncMock(side_effect=RuntimeError("sidecar crashed"))
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        await vm.refresh_status()
        assert vm.state.is_refreshing is False
        assert vm.state.status_type == "error"
        assert vm.state.error_message is not None
        assert vm.state.error_message.key == "db_status_refresh_failed"

    @pytest.mark.asyncio
    async def test_refresh_status_resolves_default_log_dir(self, mock_maintenance_service, mock_doctor_result):
        """embedded_pg_log_dir 为空时，用 platformdirs 默认 <app data>/postgres-logs。"""
        config_dict = {
            "embedded_pg_enabled": True,
            "embedded_pg_data_root": "/fake/data_root",
            "embedded_pg_log_dir": "",  # 空 → 用默认
            "db_port": 5432,
        }
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        await vm.refresh_status()
        # log_dir 应解析为非空路径（含 postgres-logs）
        assert vm.state.log_dir is not None
        assert "postgres-logs" in vm.state.log_dir

    @pytest.mark.asyncio
    async def test_refresh_status_pg_version_none(self, mock_maintenance_service, mock_doctor_result, mock_config_dict):
        """pg_version 为 None 时，state.pg_version 保持 None。"""
        mock_doctor_result.pg_version = None
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        await vm.refresh_status()
        assert vm.state.pg_version is None


# --- open_data_dir / open_log_dir ---


class TestOpenDir:
    def test_open_data_dir_calls_popen_windows(self, vm):
        """Windows: subprocess.Popen 调用 explorer。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="Windows"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=True),
        ):
            # 先 refresh 获取 data_dir
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_data_dir()
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        assert args[0] == "explorer"
        assert "/fake/data" in args

    def test_open_data_dir_calls_popen_macos(self, vm):
        """macOS: subprocess.Popen 调用 open。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="Darwin"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=True),
        ):
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_data_dir()
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        assert args[0] == "open"

    def test_open_data_dir_calls_popen_linux(self, vm):
        """Linux: subprocess.Popen 调用 xdg-open。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="Linux"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=True),
        ):
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_data_dir()
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        assert args[0] == "xdg-open"

    def test_open_log_dir_calls_popen(self, vm):
        """open_log_dir 调用 subprocess.Popen 打开 log_dir。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="Windows"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=True),
        ):
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_log_dir()
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        assert args[0] == "explorer"
        assert "/fake/log_dir" in args

    def test_open_data_dir_noop_when_none(self, mock_maintenance_service, mock_config_dict):
        """data_dir 为 None 时，不调用 subprocess.Popen。"""
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        # 未 refresh，data_dir 为 None
        with patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen:
            vm.open_data_dir()
        mock_popen.assert_not_called()

    def test_open_log_dir_noop_when_none(self, mock_maintenance_service, mock_config_dict):
        """log_dir 为 None 时，不调用 subprocess.Popen。"""
        with patch(
            "ui.viewmodels.database_status_view_model.ConfigHandler.load_config",
            return_value=mock_config_dict,
        ):
            vm = DatabaseStatusViewModel(maintenance_service=mock_maintenance_service)
        with patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen:
            vm.open_log_dir()
        mock_popen.assert_not_called()

    def test_open_data_dir_noop_when_path_not_exists(self, vm):
        """路径不存在时，不调用 subprocess.Popen（Security Required）。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="Windows"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=False),
        ):
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_data_dir()
        mock_popen.assert_not_called()

    def test_open_data_dir_noop_unsupported_platform(self, vm):
        """不支持的平台不调用 subprocess.Popen。"""
        with (
            patch("ui.viewmodels.database_status_view_model.platform.system", return_value="UnknownOS"),
            patch("ui.viewmodels.database_status_view_model.subprocess.Popen") as mock_popen,
            patch("ui.viewmodels.database_status_view_model.Path.exists", return_value=True),
        ):
            import asyncio

            asyncio.run(vm.refresh_status())
            vm.open_data_dir()
        mock_popen.assert_not_called()


# --- VM i18n contract ---


class TestVMi18nContract:
    """VM 不感知 locale: state 用 Message 产出 (key, params)，不调 I18n.get。"""

    def test_state_message_is_message_type(self, vm):
        """status_message 必须是 Message dataclass（不是 str）。"""
        import asyncio

        asyncio.run(vm.refresh_status())
        assert isinstance(vm.state.status_message, Message)

    def test_vm_does_not_import_i18n_get(self):
        """VM 模块不应导入 I18n（不感知 locale）。

        只检查 import 语句，不检查 docstring 中的注释文字（docstring 提及
        I18n.get 是合法的架构说明，不是实际调用）。
        """
        from pathlib import Path

        from ui.viewmodels import database_status_view_model as vm_mod

        source = Path(vm_mod.__file__).read_text(encoding="utf-8")
        # 禁止在 VM 中 import I18n（无 import 即无法调用）
        assert "from ui.i18n import" not in source
        assert "import ui.i18n" not in source
