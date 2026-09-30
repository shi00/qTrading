"""ui/components/confirm_dialog.py 声明式契约 + 组件体测试 (P1-4 批次 2)."""

import ast
from pathlib import Path

import flet as ft
import pytest

from ui.components.confirm_dialog import ConfirmDialog
from ui.theme import AppStyles

pytestmark = pytest.mark.unit


def _confirm_dialog_destructive_flags(rel_path: str) -> list[bool | None]:
    """解析源文件中每个 ConfirmDialog(...) 调用的 destructive 关键字实参值。

    缺省 (未显式传参) 记录为 None, 便于区分「显式 False」与「沿用默认」。
    """
    tree = ast.parse(Path(rel_path).read_text(encoding="utf-8"))
    flags: list[bool | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "ConfirmDialog"):
            continue
        value: bool | None = None
        for kw in node.keywords:
            if kw.arg != "destructive":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                value = kw.value.value
        flags.append(value)
    return flags


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

    def test_confirm_btn_not_danger_when_non_destructive(self, mock_i18n_state, mock_app_colors_state):
        """MINOR-05 DoD①: 非破坏性调用 (AI 外发确认/"继续"等) 确认按钮必须是普通主按钮样式, 非危险色."""
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
        # destructive 缺省值 → primary_button(), 不得沿用危险色 (原实现固定 danger_button)
        assert confirm_btn.style.bgcolor == AppStyles.primary_button().bgcolor
        assert confirm_btn.style.bgcolor != AppStyles.danger_button().bgcolor
        run_unmount_effects(c)

    def test_confirm_btn_danger_when_destructive(self, mock_i18n_state, mock_app_colors_state):
        """MINOR-05 DoD③: 破坏性调用 (destructive=True) 确认按钮仍为危险色."""
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
            destructive=True,
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        confirm_btn = dialog.actions[1]
        assert confirm_btn.style.bgcolor == AppStyles.danger_button().bgcolor
        assert confirm_btn.style.bgcolor != AppStyles.primary_button().bgcolor
        run_unmount_effects(c)

    def test_long_body_uses_scrollable_container_with_max_height(self, mock_i18n_state, mock_app_colors_state):
        """MINOR-05 DoD②: 长正文由对话框可滚动容器承载, 其高度上限由视口约束派生."""
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
            run_unmount_effects,
        )

        long_body = "\n".join(f"line {i}" for i in range(500))
        c = make_component(
            ConfirmDialog,
            open_state=True,
            body=long_body,
            confirm_text="OK",
            cancel_text="Cancel",
        )
        page = run_mount_effects(c)
        dialog = page._dialogs.controls[0]
        # 正文完整渲染, 不截断
        assert dialog.content.value == long_body
        # scrollable=True → Flet 将 title/content 包进可滚动容器, 高度上限由对话框
        # 视口约束 (inset_padding 内) 派生, 长正文在容器内滚动而非撑破对话框
        assert dialog.scrollable is True
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


class TestDestructiveCallSites:
    """MINOR-05: 破坏性调用点必须显式 destructive=True (危险色), 非破坏性保持默认。"""

    def test_watchlist_remove_is_destructive(self):
        """移除自选股 (不可逆删除) 必须传 destructive=True → 危险色。"""
        assert _confirm_dialog_destructive_flags("ui/views/watchlist_view.py") == [True]

    def test_ai_brain_prompt_resets_are_destructive(self):
        """重置 AI / 新闻提示词 (覆盖用户自定义内容) 两处均须 destructive=True。"""
        assert _confirm_dialog_destructive_flags("ui/views/settings_tabs/ai_brain_tab.py") == [True, True]

    def test_non_destructive_call_sites_keep_default(self):
        """非破坏性确认 (AI 外发确认 / 不可计价"继续" / 线程池保存确认) 保持默认 → 非危险色。"""
        assert _confirm_dialog_destructive_flags("ui/views/screener_view.py") == [None, None]
        assert _confirm_dialog_destructive_flags("ui/views/settings_tabs/system_tab.py") == [None]
