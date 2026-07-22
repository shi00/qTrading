"""BackupRestorePanel 组件单元测试 (P3-11).

覆盖:
1. @ft.component 装饰契约
2. use_viewmodel(factory=...) 内部 VM 模式
3. View 不持有业务状态 (MVVM §3.2)
4. 渲染标题 / 备份按钮 / 恢复按钮
5. 不渲染表单字段 (无 TextField)
6. VM 生命周期 (mount/dispose)
7. 纯函数 (_render_message / _generate_default_backup_path)
8. Click handler factory (page 可用 / RuntimeError 分支)
9. 状态渲染分支 (pending/confirmed/cancelled/is_backing_up/各 message)
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from core.i18n import Message
from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import backup_restore_panel as panel_module
from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.viewmodels.backup_restore_view_model import BackupRestoreState

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 backup_restore_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)."""
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_walk_controls(content))
    return result


def _render_panel(
    *,
    page: FakePage | None = None,
) -> tuple[Any, FakePage, Any, Any]:
    """渲染 BackupRestorePanel, 返回 (vm, page, result, component).

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    - EmbeddedPgMaintenanceService (dump/restore 返回 mock)
    """
    if page is None:
        page = FakePage()

    mock_dump = MagicMock()
    mock_dump.output_path = "/fake/backup.dump"
    mock_dump.file_size = 1024
    mock_dump.exit_code = 0

    mock_restore = MagicMock()
    mock_restore.target_data_dir = "/fake/target_data"
    mock_restore.exit_code = 0

    mock_svc = MagicMock()
    mock_svc.dump = AsyncMock(return_value=mock_dump)
    mock_svc.restore = AsyncMock(return_value=mock_restore)

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        from ui.theme import AppStyles as _RealAppStyles

        for attr in dir(_RealAppStyles):
            if not attr.startswith("_"):
                val = getattr(_RealAppStyles, attr, None)
                if isinstance(val, (str, int, float)):
                    setattr(mock_styles, attr, val)
        mock_styles.primary_button = MagicMock(return_value=ft.ButtonStyle())
        mock_styles.secondary_button = MagicMock(return_value=ft.ButtonStyle())

        component = make_component(BackupRestorePanel)
        run_mount_effects(component, page=page)
        result = render_once(component)

    return None, page, result, component


# ============================================================================
# 契约守护测试
# ============================================================================


class TestBackupRestorePanelContract:
    """BackupRestorePanel @ft.component 契约守护测试."""

    def test_is_ft_component(self) -> None:
        """DoD: BackupRestorePanel 必须被 @ft.component 装饰."""
        assert hasattr(BackupRestorePanel, "__wrapped__"), "BackupRestorePanel 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_internal_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(factory=...) 内部 VM 模式订阅 (CLAUDE.md §3.3)."""
        source = _read_source()
        assert "use_viewmodel(factory=" in source

    def test_no_business_state_in_view(self) -> None:
        """DoD: View 不持有业务状态 (MVVM §3.2) — 不应有 use_state 持有业务字段."""
        source = _read_source()
        forbidden = ["backup_path", "restore_path", "confirm_state", "is_backing_up"]
        for field in forbidden:
            assert f"use_state(lambda: {field}" not in source, f"View 不应通过 use_state 持有业务字段 {field}"

    def test_signature_no_required_business_params(self) -> None:
        """DoD: BackupRestorePanel 签名不接受 vm 参数 (内部 VM 模式)."""
        sig = inspect.signature(BackupRestorePanel)
        if "vm" in sig.parameters:
            assert sig.parameters["vm"].default is not inspect.Parameter.empty, (
                "vm 不应是必需参数 (BackupRestorePanel 用内部 VM)"
            )


# ============================================================================
# 渲染测试
# ============================================================================


class TestBackupRestorePanelRendering:
    """BackupRestorePanel 渲染测试."""

    def test_returns_container(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认渲染返回 ft.Container."""
        _, _, result, _ = _render_panel()
        assert isinstance(result, ft.Container)

    def test_renders_title_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染标题 (backup_restore_title)."""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        titles = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "backup_restore_title"]
        assert len(titles) >= 1

    def test_renders_backup_button(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染手动备份按钮 (backup_button).

        按钮用 ft.Button(content=I18n.get(...)), content 是字符串 (mock 返回 key).
        """
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        backup_btns = [b for b in buttons if getattr(b, "content", None) == "backup_button"]
        assert len(backup_btns) >= 1

    def test_renders_restore_button(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染恢复向导按钮 (restore_button)."""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        restore_btns = [b for b in buttons if getattr(b, "content", None) == "restore_button"]
        assert len(restore_btns) >= 1

    def test_renders_step1_hint_when_idle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: confirm_state=idle 时渲染 Step 1 提示 (restore_step1_select)."""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        hints = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "restore_step1_select"]
        assert len(hints) >= 1

    def test_no_form_fields_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: BackupRestorePanel 不渲染表单字段 (无 TextField)."""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        text_fields = [c for c in ctrls if isinstance(c, ft.TextField)]
        assert len(text_fields) == 0

    def test_no_confirm_dialog_when_idle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: confirm_state=idle 时不渲染确认对话框按钮."""
        _, _, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        buttons = [c for c in ctrls if isinstance(c, ft.Button)]
        confirm_btns = [b for b in buttons if getattr(b, "content", None) == "confirm_restore_button"]
        cancel_btns = [b for b in buttons if getattr(b, "content", None) == "cancel_restore_button"]
        assert len(confirm_btns) == 0
        assert len(cancel_btns) == 0


# ============================================================================
# VM 生命周期测试
# ============================================================================


class TestBackupRestorePanelVMLifecycle:
    """BackupRestorePanel 内部 VM 生命周期测试 (use_viewmodel factory 模式)."""

    def test_mount_initializes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后通过 use_viewmodel(factory=...) 实例化内部 VM."""
        _, _, _, component = _render_panel()
        assert component is not None
        assert hasattr(component, "fn"), "Component 应已实例化 (有 fn 属性)"

    def test_unmount_disposes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 卸载时 dispose 内部 VM (use_viewmodel factory 模式默认 dispose_on_unmount=True)."""
        with patch("ui.components.config_panels.backup_restore_panel.BackupRestoreViewModel.dispose") as mock_dispose:
            _, _, _, component = _render_panel()
            run_unmount_effects(component)
        mock_dispose.assert_called_once_with()


# ============================================================================
# 纯函数测试
# ============================================================================


class TestRenderMessage:
    """_render_message 纯函数测试."""

    def test_none_returns_empty(self, mock_i18n_state) -> None:
        """None 输入返回空字符串."""
        assert panel_module._render_message(None) == ""

    def test_message_returns_translated_text(self, mock_i18n_state) -> None:
        """Message 输入调 I18n.get(key, **params) 返回翻译文本."""
        msg = Message("backup_success", params={"path": "/tmp/backup.dump"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: f"translated:{key}"
            result = panel_module._render_message(msg)
        assert result == "translated:backup_success"
        mock_i18n.get.assert_called_once_with("backup_success", path="/tmp/backup.dump")


class TestGenerateDefaultBackupPath:
    """_generate_default_backup_path 纯函数测试."""

    def test_returns_path_with_timestamp(self) -> None:
        """返回带时间戳的 Path, 格式 qtrading-backup-YYYYMMDD-HHMMSS.dump."""
        path = panel_module._generate_default_backup_path()
        assert isinstance(path, Path)
        assert path.name.startswith("qtrading-backup-")
        assert path.suffix == ".dump"

    def test_name_contains_timestamp_pattern(self) -> None:
        """文件名含时间戳 (8 位日期 + 6 位时间)."""
        import re

        path = panel_module._generate_default_backup_path()
        # qtrading-backup-20260722-151430.dump
        assert re.match(r"^qtrading-backup-\d{8}-\d{6}\.dump$", path.name), path.name


# ============================================================================
# Click handler factory 测试
# ============================================================================


class TestClickHandlers:
    """Click handler factory 测试 — 覆盖 page available / RuntimeError 分支."""

    def test_on_backup_click_calls_start_backup(self) -> None:
        """page 可用时调 page.run_task(vm.start_backup, path)."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        mock_page = MagicMock()
        _context_page.set(mock_page)
        try:
            handler = panel_module._on_backup_click_factory(vm)
            handler(MagicMock())
        finally:
            _context_page.set(None)

        mock_page.run_task.assert_called_once()
        args = mock_page.run_task.call_args[0]
        assert args[0] == vm.start_backup
        assert isinstance(args[1], Path)

    def test_on_backup_click_silent_on_runtime_error(self) -> None:
        """page 不可用 (RuntimeError, _context_page 为 None) 时静默处理, 不调 vm.start_backup."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        # _context_page.set(None) 让 ft.context.page property 抛 RuntimeError
        _context_page.set(None)
        try:
            handler = panel_module._on_backup_click_factory(vm)
            handler(MagicMock())  # 不应抛异常
        finally:
            _context_page.set(None)
        vm.start_backup.assert_not_called()

    def test_on_backup_click_silent_when_page_none(self) -> None:
        """page 为 None 时静默处理 (ft.context.page 抛 RuntimeError 由 except 捕获)."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        _context_page.set(None)
        try:
            handler = panel_module._on_backup_click_factory(vm)
            handler(MagicMock())
        finally:
            _context_page.set(None)
        vm.start_backup.assert_not_called()

    def test_on_restore_wizard_click_uses_default_path_when_backup_path_none(self) -> None:
        """state.backup_path 为 None 时用默认路径调 vm.start_restore_wizard."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        vm.state.backup_path = None
        mock_page = MagicMock()
        _context_page.set(mock_page)
        try:
            handler = panel_module._on_restore_wizard_click_factory(vm)
            handler(MagicMock())
        finally:
            _context_page.set(None)

        args = mock_page.run_task.call_args[0]
        assert args[0] == vm.start_restore_wizard
        assert isinstance(args[1], Path)
        assert args[1].name.startswith("qtrading-backup-")

    def test_on_restore_wizard_click_uses_state_backup_path_when_set(self) -> None:
        """state.backup_path 非 None 时用该路径调 vm.start_restore_wizard."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        vm.state.backup_path = "/existing/backup.dump"
        mock_page = MagicMock()
        _context_page.set(mock_page)
        try:
            handler = panel_module._on_restore_wizard_click_factory(vm)
            handler(MagicMock())
        finally:
            _context_page.set(None)

        args = mock_page.run_task.call_args[0]
        assert args[1] == Path("/existing/backup.dump")

    def test_on_restore_wizard_click_silent_on_runtime_error(self) -> None:
        """page 不可用时静默处理 (ft.context.page 抛 RuntimeError)."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        _context_page.set(None)
        try:
            handler = panel_module._on_restore_wizard_click_factory(vm)
            handler(MagicMock())  # 不应抛异常
        finally:
            _context_page.set(None)
        vm.start_restore_wizard.assert_not_called()

    def test_on_confirm_restore_click_calls_confirm_restore(self) -> None:
        """page 可用时调 page.run_task(vm.confirm_restore)."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        mock_page = MagicMock()
        _context_page.set(mock_page)
        try:
            handler = panel_module._on_confirm_restore_click_factory(vm)
            handler(MagicMock())
        finally:
            _context_page.set(None)

        mock_page.run_task.assert_called_once_with(vm.confirm_restore)

    def test_on_confirm_restore_click_silent_on_runtime_error(self) -> None:
        """page 不可用时静默处理 (ft.context.page 抛 RuntimeError)."""
        from flet.controls.context import _context_page

        vm = MagicMock()
        _context_page.set(None)
        try:
            handler = panel_module._on_confirm_restore_click_factory(vm)
            handler(MagicMock())  # 不应抛异常
        finally:
            _context_page.set(None)
        vm.confirm_restore.assert_not_called()

    def test_on_cancel_restore_click_calls_cancel_restore(self) -> None:
        """cancel_restore 同步调用, 直接调 vm.cancel_restore()."""
        vm = MagicMock()
        handler = panel_module._on_cancel_restore_click_factory(vm)
        handler(MagicMock())
        vm.cancel_restore.assert_called_once_with()


# ============================================================================
# 状态渲染分支测试
# ============================================================================


def _render_panel_with_state(
    state: BackupRestoreState,
    *,
    page: FakePage | None = None,
) -> tuple[Any, FakePage, Any, Any]:
    """用自定义 state 渲染 BackupRestorePanel (mock use_viewmodel 返回 state)."""
    if page is None:
        page = FakePage()

    mock_vm = MagicMock()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        from ui.theme import AppStyles as _RealAppStyles

        for attr in dir(_RealAppStyles):
            if not attr.startswith("_"):
                val = getattr(_RealAppStyles, attr, None)
                if isinstance(val, (str, int, float)):
                    setattr(mock_styles, attr, val)
        mock_styles.primary_button = MagicMock(return_value=ft.ButtonStyle())
        mock_styles.secondary_button = MagicMock(return_value=ft.ButtonStyle())

        stack.enter_context(patch.object(panel_module, "use_viewmodel", return_value=(state, mock_vm)))

        component = make_component(BackupRestorePanel)
        run_mount_effects(component, page=page)
        result = render_once(component)

    return mock_vm, page, result, component


class TestBackupRestorePanelStateRendering:
    """BackupRestorePanel 各 state 渲染分支测试."""

    def test_renders_step2_dialog_when_confirm_pending(self, mock_i18n_state, mock_app_colors_state) -> None:
        """confirm_state=pending 时渲染 Step 2 确认对话框 (确认/取消按钮 + 文案)."""
        state = BackupRestoreState(confirm_state="pending")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        confirm_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "confirm_restore_button"
        ]
        cancel_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "cancel_restore_button"
        ]
        assert len(confirm_btns) == 1
        assert len(cancel_btns) == 1
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_step2_confirm" in text_values
        assert "restore_confirm_title" in text_values
        assert "restore_confirm_message" in text_values

    def test_renders_step3_executing_when_confirm_confirmed(self, mock_i18n_state, mock_app_colors_state) -> None:
        """confirm_state=confirmed 时渲染 Step 3 执行中."""
        state = BackupRestoreState(confirm_state="confirmed")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_step3_executing" in text_values

    def test_renders_step3_executing_when_is_restoring(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_restoring=True 时渲染 Step 3 执行中."""
        state = BackupRestoreState(is_restoring=True)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_step3_executing" in text_values

    def test_renders_cancelled_when_confirm_cancelled(self, mock_i18n_state, mock_app_colors_state) -> None:
        """confirm_state=cancelled 时渲染 restore_cancelled."""
        state = BackupRestoreState(confirm_state="cancelled")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_cancelled" in text_values

    def test_renders_backup_progress_when_backing_up(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_backing_up=True 时渲染 backup_in_progress 进度文本."""
        state = BackupRestoreState(
            is_backing_up=True,
            progress_message=Message("backup_in_progress"),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "backup_in_progress" in text_values

    def test_renders_backup_path_when_set(self, mock_i18n_state, mock_app_colors_state) -> None:
        """backup_path 非 None 时渲染备份路径文本 (db_status_data_dir)."""
        state = BackupRestoreState(backup_path="/tmp/backup.dump")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "db_status_data_dir" in text_values

    def test_renders_backup_success_message(self, mock_i18n_state, mock_app_colors_state) -> None:
        """backup_success_message 非 None 时渲染翻译文本."""
        state = BackupRestoreState(
            backup_success_message=Message("backup_success", params={"path": "/x.dump"}),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "backup_success" in text_values

    def test_renders_restore_success_message(self, mock_i18n_state, mock_app_colors_state) -> None:
        """restore_success_message 非 None 时渲染翻译文本."""
        state = BackupRestoreState(
            restore_success_message=Message("restore_success"),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_success" in text_values

    def test_renders_error_message(self, mock_i18n_state, mock_app_colors_state) -> None:
        """error_message 非 None 时渲染翻译文本."""
        state = BackupRestoreState(
            error_message=Message("backup_failed"),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "backup_failed" in text_values

    def test_renders_restore_progress_when_restoring(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_restoring=True 且 progress_message 非 None 时渲染 restore_in_progress."""
        state = BackupRestoreState(
            is_restoring=True,
            progress_message=Message("restore_in_progress"),
        )
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        text_values = [getattr(c, "value", None) for c in ctrls if isinstance(c, ft.Text)]
        assert "restore_in_progress" in text_values

    def test_backup_button_disabled_when_backing_up(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_backing_up=True 时 backup_button disabled=True."""
        state = BackupRestoreState(is_backing_up=True)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        backup_btns = [b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "backup_button"]
        assert len(backup_btns) == 1
        assert backup_btns[0].disabled is True

    def test_restore_button_disabled_when_restoring(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_restoring=True 时 restore_button disabled=True."""
        state = BackupRestoreState(is_restoring=True)
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        restore_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "restore_button"
        ]
        assert len(restore_btns) == 1
        assert restore_btns[0].disabled is True

    def test_restore_button_hidden_when_pending(self, mock_i18n_state, mock_app_colors_state) -> None:
        """confirm_state=pending 时 restore_button visible=False."""
        state = BackupRestoreState(confirm_state="pending")
        _, _, result, _ = _render_panel_with_state(state)
        ctrls = _walk_controls(result)
        restore_btns = [
            b for b in ctrls if isinstance(b, ft.Button) and getattr(b, "content", None) == "restore_button"
        ]
        assert len(restore_btns) == 1
        assert restore_btns[0].visible is False
