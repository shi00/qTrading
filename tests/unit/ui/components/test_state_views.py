"""ui/components/state_views.py 声明式契约 + 组件体测试 (P1-3 批次 2)."""

from pathlib import Path

import flet as ft
import pytest

from ui.components.state_views import EmptyState, ErrorState
from ui.theme import AppColors

pytestmark = pytest.mark.unit


class TestStateViewsContract:
    """声明式组件契约守护测试。"""

    def test_empty_state_is_ft_component(self):
        assert hasattr(EmptyState, "__wrapped__")

    def test_error_state_is_ft_component(self):
        assert hasattr(ErrorState, "__wrapped__")

    def test_no_class_inheritance(self):
        source = Path("ui/components/state_views.py").read_text(encoding="utf-8")
        assert "class EmptyState(" not in source
        assert "class ErrorState(" not in source

    def test_no_did_mount_will_unmount_update(self):
        source = Path("ui/components/state_views.py").read_text(encoding="utf-8")
        assert "did_mount" not in source
        assert "will_unmount" not in source
        assert ".update()" not in source

    def test_subscribes_i18n(self):
        source = Path("ui/components/state_views.py").read_text(encoding="utf-8")
        assert "get_observable_state" in source

    def test_subscribes_app_colors(self):
        source = Path("ui/components/state_views.py").read_text(encoding="utf-8")
        assert "AppColors.get_observable_state" in source

    def test_exports_all(self):
        from ui.components import state_views

        assert "EmptyState" in state_views.__all__
        assert "ErrorState" in state_views.__all__


class TestEmptyStateRender:
    """EmptyState 组件体渲染测试。"""

    def test_full_props_renders_icon_title_message_cta(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(
            EmptyState,
            icon=ft.Icons.INBOX,
            title="No Data",
            message="Please sync first",
            on_cta=lambda: None,
            cta_text="Sync",
        )
        container = render_once(c)
        col = container.content
        # 4 controls: icon + title + message + cta_button
        assert len(col.controls) == 4
        assert isinstance(col.controls[0], ft.Icon)
        assert col.controls[0].icon == ft.Icons.INBOX
        assert isinstance(col.controls[1], ft.Text)
        assert col.controls[1].value == "No Data"
        assert isinstance(col.controls[2], ft.Text)
        assert col.controls[2].value == "Please sync first"
        assert isinstance(col.controls[3], ft.TextButton)

    def test_no_cta_renders_only_icon_title_message(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(EmptyState, icon=ft.Icons.INBOX, title="Empty", message="No data")
        container = render_once(c)
        col = container.content
        assert len(col.controls) == 3

    def test_empty_icon_title_message_renders_empty_column(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(EmptyState)
        container = render_once(c)
        col = container.content
        assert len(col.controls) == 0

    def test_cta_missing_text_does_not_render_button(self, mock_i18n_state, mock_app_colors_state):
        """on_cta 非空但 cta_text 为空时不渲染按钮 (防止无文案按钮)."""
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(EmptyState, icon=ft.Icons.INBOX, title="T", on_cta=lambda: None)
        container = render_once(c)
        col = container.content
        # 仅 icon + title, 无 cta 按钮
        assert len(col.controls) == 2


class TestErrorStateRender:
    """ErrorState 组件体渲染测试。"""

    def test_full_props_renders_icon_title_message_retry(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(
            ErrorState,
            icon=ft.Icons.ERROR_OUTLINE,
            title="Load Failed",
            message="Please retry",
            on_retry=lambda: None,
            retry_text="Retry",
        )
        container = render_once(c)
        col = container.content
        # 4 controls: icon + title + message + retry_button
        assert len(col.controls) == 4
        assert isinstance(col.controls[0], ft.Icon)
        assert col.controls[0].color == AppColors.ERROR

    def test_no_retry_renders_only_icon_title_message(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(ErrorState, icon=ft.Icons.ERROR_OUTLINE, title="Error", message="Failed")
        container = render_once(c)
        col = container.content
        assert len(col.controls) == 3

    def test_with_cta_renders_5_controls(self, mock_i18n_state, mock_app_colors_state):
        """ErrorState 支持 on_cta (次操作, 如导航到设置页)."""
        from tests.unit.ui.component_renderer import make_component, render_once

        c = make_component(
            ErrorState,
            icon=ft.Icons.ERROR_OUTLINE,
            title="Error",
            message="Failed",
            on_retry=lambda: None,
            retry_text="Retry",
            on_cta=lambda: None,
            cta_text="Settings",
        )
        container = render_once(c)
        col = container.content
        # 5 controls: icon + title + message + retry + cta
        assert len(col.controls) == 5
        assert isinstance(col.controls[4], ft.TextButton)


class TestCallbacks:
    """on_cta / on_retry 回调测试。"""

    def test_on_cta_triggered_on_click(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        called = []
        c = make_component(
            EmptyState,
            icon=ft.Icons.INBOX,
            on_cta=lambda: called.append(1),
            cta_text="Go",
        )
        container = render_once(c)
        # icon + cta_button = 2 controls
        cta_btn = container.content.controls[1]
        cta_btn.on_click(None)
        assert called == [1]

    def test_on_retry_triggered_on_click(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        called = []
        c = make_component(
            ErrorState,
            icon=ft.Icons.ERROR_OUTLINE,
            on_retry=lambda: called.append(1),
            retry_text="Retry",
        )
        container = render_once(c)
        # icon + retry_button = 2 controls
        retry_btn = container.content.controls[1]
        retry_btn.on_click(None)
        assert called == [1]

    def test_on_cta_triggered_on_click_in_error_state(self, mock_i18n_state, mock_app_colors_state):
        """ErrorState 的 on_cta 回调触发测试。"""
        from tests.unit.ui.component_renderer import make_component, render_once

        called = []
        c = make_component(
            ErrorState,
            icon=ft.Icons.ERROR_OUTLINE,
            on_retry=lambda: called.append("retry"),
            retry_text="Retry",
            on_cta=lambda: called.append("cta"),
            cta_text="Settings",
        )
        container = render_once(c)
        # 5 controls: icon + title-less + message-less + retry + cta (无 title/message)
        # 实际: icon + retry + cta = 3 controls
        cta_btn = container.content.controls[2]
        cta_btn.on_click(None)
        assert called == ["cta"]
