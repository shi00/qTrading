"""BackupRestoreViewModel 单元测试 (P3-11).

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，内部 VM 模式由 use_viewmodel(factory=...) 实例化。

覆盖:
1. State frozen + 默认值
2. start_backup() 调用 dump() 并更新 state (success/error/already-exists)
3. start_restore_wizard() 设置 confirm_state=pending / 文件不存在处理
4. confirm_restore() 调用 restore() + 状态转换
5. cancel_restore() 清空 restore_path
6. VM 只产出 Message (i18n key)，不调 I18n.get
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels import Message
from ui.viewmodels.backup_restore_view_model import BackupRestoreViewModel

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_dump_result():
    """构造一个 DumpResult mock。"""
    result = MagicMock()
    result.output_path = "/fake/backup.dump"
    result.file_size = 1024
    result.exit_code = 0
    return result


@pytest.fixture
def mock_restore_result():
    """构造一个 RestoreResult mock。"""
    result = MagicMock()
    result.target_data_dir = "/fake/target_data"
    result.exit_code = 0
    return result


@pytest.fixture
def mock_maintenance_service(mock_dump_result, mock_restore_result):
    """Mock EmbeddedPgMaintenanceService（DI 注入，避免单例污染）。"""
    svc = MagicMock()
    svc.dump = AsyncMock(return_value=mock_dump_result)
    svc.restore = AsyncMock(return_value=mock_restore_result)
    return svc


@pytest.fixture
def vm(mock_maintenance_service):
    """构造 BackupRestoreViewModel 实例（DI 注入 mock service）。

    VM 不依赖 ConfigHandler (路径由调用方传入), 不需 patch config.
    """
    yield BackupRestoreViewModel(maintenance_service=mock_maintenance_service)


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, vm):
        with pytest.raises(FrozenInstanceError):
            vm.state.is_backing_up = True  # type: ignore[misc]

    def test_state_default_values(self, mock_maintenance_service):
        vm = BackupRestoreViewModel(maintenance_service=mock_maintenance_service)
        assert vm.state.is_backing_up is False
        assert vm.state.is_restoring is False
        assert vm.state.backup_path is None
        assert vm.state.restore_path is None
        assert vm.state.progress_message is None
        assert vm.state.error_message is None
        assert vm.state.confirm_state == "idle"
        assert vm.state.backup_success_message is None
        assert vm.state.restore_success_message is None


# --- start_backup ---


class TestStartBackup:
    @pytest.mark.asyncio
    async def test_start_backup_calls_dump(self, vm, mock_maintenance_service):
        output_path = Path("/tmp/backup.dump")
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(output_path)
        mock_maintenance_service.dump.assert_called_once_with(output_path)

    @pytest.mark.asyncio
    async def test_start_backup_success_updates_state(self, vm, mock_dump_result):
        output_path = Path("/tmp/backup.dump")
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(output_path)
        assert vm.state.is_backing_up is False
        assert vm.state.backup_path == mock_dump_result.output_path
        assert vm.state.backup_success_message is not None
        assert vm.state.backup_success_message.key == "backup_success"
        assert vm.state.backup_success_message.params == {"path": mock_dump_result.output_path}
        assert vm.state.progress_message is None
        assert vm.state.error_message is None

    @pytest.mark.asyncio
    async def test_start_backup_sets_progress_during_execution(self, vm):
        """start_backup 期间 is_backing_up=True + progress_message 设置.
        AsyncMock 立即返回, 难以捕获中间态; 改为验证完成后 progress_message=None.
        """
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(Path("/tmp/backup.dump"))
        assert vm.state.is_backing_up is False
        assert vm.state.progress_message is None

    @pytest.mark.asyncio
    async def test_start_backup_handles_error(self, mock_maintenance_service):
        """dump() 抛异常时, state 设为 error 且 is_backing_up=False."""
        mock_maintenance_service.dump = AsyncMock(side_effect=RuntimeError("dump failed"))
        vm = BackupRestoreViewModel(maintenance_service=mock_maintenance_service)
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(Path("/tmp/backup.dump"))
        assert vm.state.is_backing_up is False
        assert vm.state.error_message is not None
        assert vm.state.error_message.key == "backup_failed"
        assert vm.state.backup_success_message is None
        assert vm.state.progress_message is None

    @pytest.mark.asyncio
    async def test_start_backup_skip_when_file_exists(self, vm, mock_maintenance_service):
        """output_path 已存在时, 不调用 dump (覆盖保护), 设 error_message."""
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_backup(Path("/tmp/existing.dump"))
        mock_maintenance_service.dump.assert_not_called()
        assert vm.state.is_backing_up is False
        assert vm.state.error_message is not None
        assert vm.state.error_message.key == "backup_failed"

    @pytest.mark.asyncio
    async def test_start_backup_clears_previous_error_on_success(self, vm, mock_maintenance_service):
        """先制造一次 error, 再成功备份, error_message 应被清空."""
        # 第一次: 制造 error
        mock_maintenance_service.dump = AsyncMock(side_effect=RuntimeError("first fail"))
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(Path("/tmp/backup.dump"))
        assert vm.state.error_message is not None

        # 第二次: 恢复成功
        success_result = MagicMock(output_path="/tmp/backup.dump", file_size=100, exit_code=0)
        mock_maintenance_service.dump = AsyncMock(return_value=success_result)
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_backup(Path("/tmp/backup.dump"))
        assert vm.state.error_message is None
        assert vm.state.backup_success_message is not None


# --- start_restore_wizard ---


class TestStartRestoreWizard:
    @pytest.mark.asyncio
    async def test_start_restore_wizard_sets_pending(self, vm):
        input_path = Path("/tmp/backup.dump")
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(input_path)
        assert vm.state.confirm_state == "pending"
        assert vm.state.restore_path == str(input_path)
        assert vm.state.error_message is None
        assert vm.state.restore_success_message is None

    @pytest.mark.asyncio
    async def test_start_restore_wizard_file_not_found(self, vm):
        """input_path 不存在时, confirm_state=idle + 设 error_message."""
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            await vm.start_restore_wizard(Path("/tmp/missing.dump"))
        assert vm.state.confirm_state == "idle"
        assert vm.state.restore_path is None
        assert vm.state.error_message is not None
        assert vm.state.error_message.key == "restore_failed"


# --- confirm_restore ---


class TestConfirmRestore:
    @pytest.mark.asyncio
    async def test_confirm_restore_calls_restore(self, vm, mock_maintenance_service):
        # 先进入 pending
        input_path = Path("/tmp/backup.dump")
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(input_path)
        # 再确认
        await vm.confirm_restore()
        mock_maintenance_service.restore.assert_called_once()
        called_path = mock_maintenance_service.restore.call_args[0][0]
        assert isinstance(called_path, Path)
        # Path("/tmp/...") 在 Windows 上会规范化为 \tmp\..., 比较应用 Path 而非 str
        assert called_path == input_path

    @pytest.mark.asyncio
    async def test_confirm_restore_success_updates_state(self, vm):
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(Path("/tmp/backup.dump"))
        await vm.confirm_restore()
        assert vm.state.is_restoring is False
        assert vm.state.confirm_state == "idle"
        assert vm.state.restore_path is None
        assert vm.state.restore_success_message is not None
        assert vm.state.restore_success_message.key == "restore_success"
        assert vm.state.progress_message is None
        assert vm.state.error_message is None

    @pytest.mark.asyncio
    async def test_confirm_restore_handles_error(self, mock_maintenance_service):
        """restore() 抛异常时, confirm_state=idle + 设 error_message."""
        mock_maintenance_service.restore = AsyncMock(side_effect=RuntimeError("restore failed"))
        vm = BackupRestoreViewModel(maintenance_service=mock_maintenance_service)
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(Path("/tmp/backup.dump"))
        await vm.confirm_restore()
        assert vm.state.is_restoring is False
        assert vm.state.confirm_state == "idle"
        assert vm.state.restore_path is None
        assert vm.state.error_message is not None
        assert vm.state.error_message.key == "restore_failed"
        assert vm.state.restore_success_message is None

    @pytest.mark.asyncio
    async def test_confirm_restore_skip_when_not_pending(self, vm, mock_maintenance_service):
        """confirm_state 不是 pending 时, 不调用 restore (防止重复触发)."""
        # 默认 confirm_state=idle
        await vm.confirm_restore()
        mock_maintenance_service.restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_restore_skip_after_cancel(self, vm, mock_maintenance_service):
        """取消后 confirm_state=cancelled, confirm_restore 不应执行."""
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(Path("/tmp/backup.dump"))
        vm.cancel_restore()
        assert vm.state.confirm_state == "cancelled"
        await vm.confirm_restore()
        mock_maintenance_service.restore.assert_not_called()


# --- cancel_restore ---


class TestCancelRestore:
    @pytest.mark.asyncio
    async def test_cancel_restore_clears_state(self, vm):
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(Path("/tmp/backup.dump"))
        vm.cancel_restore()
        assert vm.state.confirm_state == "cancelled"
        assert vm.state.restore_path is None
        assert vm.state.progress_message is None

    def test_cancel_restore_skip_when_idle(self, mock_maintenance_service):
        """confirm_state=idle 时, cancel_restore 不应改 state."""
        vm = BackupRestoreViewModel(maintenance_service=mock_maintenance_service)
        vm.cancel_restore()
        # state 保持 idle (不变成 cancelled)
        assert vm.state.confirm_state == "idle"

    @pytest.mark.asyncio
    async def test_cancel_restore_skip_when_confirmed(self, vm):
        """confirm_state=confirmed 时, cancel_restore 不应生效 (执行中不可取消)."""
        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=True):
            await vm.start_restore_wizard(Path("/tmp/backup.dump"))
        # 模拟进入 confirmed 状态
        vm._set_state(confirm_state="confirmed")
        vm.cancel_restore()
        # 状态保持 confirmed (取消无效)
        assert vm.state.confirm_state == "confirmed"


# --- VM i18n contract ---


class TestVMi18nContract:
    """VM 不感知 locale: state 用 Message 产出 (key, params)，不调 I18n.get。"""

    def test_state_messages_are_message_type(self, vm):
        """backup_success_message / error_message 必须是 Message dataclass (不是 str)."""
        import asyncio

        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            asyncio.run(vm.start_backup(Path("/tmp/backup.dump")))
        assert isinstance(vm.state.backup_success_message, Message)

    def test_vm_does_not_import_i18n_get(self):
        """VM 模块不应导入 I18n (不感知 locale).

        只检查 import 语句, 不检查 docstring 中的注释文字.
        """
        from pathlib import Path

        from ui.viewmodels import backup_restore_view_model as vm_mod

        source = Path(vm_mod.__file__).read_text(encoding="utf-8")
        assert "from ui.i18n import" not in source
        assert "import ui.i18n" not in source

    def test_state_default_messages_are_none(self, mock_maintenance_service):
        """默认 state 中所有 Message 字段应为 None."""
        vm = BackupRestoreViewModel(maintenance_service=mock_maintenance_service)
        assert vm.state.progress_message is None
        assert vm.state.error_message is None
        assert vm.state.backup_success_message is None
        assert vm.state.restore_success_message is None

    def test_backup_success_message_has_path_param(self, vm, mock_dump_result):
        """backup_success_message 必须携带 path 参数 (供 View 渲染 I18n.get)."""
        import asyncio

        with patch("ui.viewmodels.backup_restore_view_model.Path.exists", return_value=False):
            asyncio.run(vm.start_backup(Path("/tmp/backup.dump")))
        msg = vm.state.backup_success_message
        assert msg is not None
        assert "path" in msg.params
        assert msg.params["path"] == mock_dump_result.output_path
