"""BackupRestorePanel 组件单元测试 (P3-11).

覆盖:
1. @ft.component 装饰契约
2. use_viewmodel(factory=...) 内部 VM 模式
3. View 不持有业务状态 (MVVM §3.2)
4. 渲染标题 / 备份按钮 / 恢复按钮
5. 不渲染表单字段 (无 TextField)
6. VM 生命周期 (mount/dispose)
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import backup_restore_panel as panel_module
from ui.components.config_panels.backup_restore_panel import BackupRestorePanel

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
