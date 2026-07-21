"""ui/components/confirm_dialog.py 声明式契约 + 组件体测试 (P1-4 批次 2)."""

from pathlib import Path

import flet as ft
import pytest

from ui.components.confirm_dialog import ConfirmDialog
from ui.theme import AppStyles

pytestmark = pytest.mark.unit


class TestConfirmDialogContract:
    """声明式组件契约守护测试。"""

    def test_is_ft_component(self):
        assert hasattr(ConfirmDialog, "__wrapped__")

    def test_no_class_inheritance(self):
        source = Path("ui/components/confirm_dialog.py").read_text(encoding="utf-8")
        assert "class ConfirmDialog(" not in source

    def test_no_did_mount_will_unmount_update(self):
        source = Path("ui/components/confirm_dialog.py").read_text(encoding="utf-8")
        assert "did_mount" not in source
        assert "will_unmount" not in source
        assert ".update()" not in source

    def test_subscribes_i18n(self):
        source = Path("ui/components/confirm_dialog.py").read_text(encoding="utf-8")
        assert "get_observable_state" in source

    def test_subscribes_app_colors(self):
        source = Path("ui/components/confirm_dialog.py").read_text(encoding="utf-8")
        assert "AppColors.get_observable_state" in source

    def test_uses_use_dialog(self):
        source = Path("ui/components/confirm_dialog.py").read_text(encoding="utf-8")
        assert "ft.use_dialog" in source

    def test_exports_all(self):
        from ui.components import confirm_dialog

        assert "ConfirmDialog" in confirm_dialog.__all__


class TestConfirmDialogRender:
    """ConfirmDialog 组件体渲染测试。"""

    def test_open_state_true_renders_dialog(self, mock_i18n_state, mock_app_colors_state):
        """open_state=True 时 use_dialog 挂载 AlertDialog 到 page._dialogs."""
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        c = make_component(
            ConfirmDialog,
            open_state=True,
            title="Confirm",
            body="Are you sure?",
            on_confirm=lambda: None,
            on_cancel=lambda: None,
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        # use_dialog 挂载到 page._dialogs.controls
        assert len(page._dialogs.controls) == 1
        dialog = page._dialogs.controls[0]
        assert isinstance(dialog, ft.AlertDialog)
        assert dialog.title.value == "Confirm"
        assert dialog.content.value == "Are you sure?"
        # actions: [cancel_btn, confirm_btn]
        assert len(dialog.actions) == 2
        run_unmount_effects(c)

    def test_open_state_false_does_not_render_dialog(self, mock_i18n_state, mock_app_colors_state):
        """open_state=False 时 dialog=None, use_dialog 不挂载."""
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        c = make_component(
            ConfirmDialog,
            open_state=False,
            title="Confirm",
            body="Are you sure?",
        )
        page = run_mount_effects(c)
        # open_state=False 时 dialog=None, use_dialog 不挂载
        assert len(page._dialogs.controls) == 0
        run_unmount_effects(c)


class TestConfirmDialogCallbacks:
    """on_confirm / on_cancel 回调测试。"""

    def test_on_confirm_triggered(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        called = []
        c = make_component(
            ConfirmDialog,
            open_state=True,
            on_confirm=lambda: called.append("confirm"),
            on_cancel=lambda: called.append("cancel"),
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        # actions: [cancel_btn, confirm_btn]
        confirm_btn = dialog.actions[1]
        confirm_btn.on_click(None)
        assert called == ["confirm"]
        run_unmount_effects(c)

    def test_on_cancel_triggered(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        called = []
        c = make_component(
            ConfirmDialog,
            open_state=True,
            on_confirm=lambda: called.append("confirm"),
            on_cancel=lambda: called.append("cancel"),
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        cancel_btn = dialog.actions[0]
        cancel_btn.on_click(None)
        assert called == ["cancel"]
        run_unmount_effects(c)

    def test_confirm_btn_uses_danger_button_style(self, mock_i18n_state, mock_app_colors_state):
        """P2-9: confirm_btn 必须使用 AppStyles.danger_button() 样式."""
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        c = make_component(
            ConfirmDialog,
            open_state=True,
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        confirm_btn = dialog.actions[1]
        # danger_button() 返回 ButtonStyle 实例
        assert confirm_btn.style is not None
        assert isinstance(confirm_btn.style, ft.ButtonStyle)
        # 验证与 AppStyles.danger_button() 返回值类型一致
        expected_style = AppStyles.danger_button()
        assert isinstance(expected_style, ft.ButtonStyle)
        run_unmount_effects(c)

    def test_cancel_btn_uses_primary_color(self, mock_i18n_state, mock_app_colors_state):
        """cancel_btn 应使用 PRIMARY 色调 (非 danger)."""
        from ui.theme import AppColors
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        c = make_component(
            ConfirmDialog,
            open_state=True,
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        cancel_btn = dialog.actions[0]
        # cancel_btn 是 TextButton, style.color 应为 AppColors.PRIMARY
        assert cancel_btn.style is not None
        assert cancel_btn.style.color == AppColors.PRIMARY
        run_unmount_effects(c)
