"""EmbeddedStatusCard 组件单元测试 (P3-9).

覆盖:
1. @ft.component 装饰契约
2. 渲染返回 ft.Container with ft.Column
3. 显示 status_message (icon + text)
4. 显示 info_message
5. 使用 use_viewmodel(factory=...) 内部 VM 模式
6. View 不持有业务状态 (MVVM §3.2)
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.config_panels import embedded_status_card as card_module
from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 embedded_status_card.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(card_module.__file__).read_text(encoding="utf-8")


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)。"""
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


def _render_card(
    *,
    page: FakePage | None = None,
) -> tuple[Any, FakePage, Any, Any]:
    """渲染 EmbeddedStatusCard, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key)
    - AppColors / AppStyles (颜色 / 样式 token)
    """
    if page is None:
        page = FakePage()

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(card_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        stack.enter_context(patch.object(card_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(card_module, "AppStyles"))
        from ui.theme import AppStyles as _RealAppStyles

        mock_styles.FONT_SIZE_TITLE = _RealAppStyles.FONT_SIZE_TITLE
        mock_styles.FONT_SIZE_BODY_SM = _RealAppStyles.FONT_SIZE_BODY_SM
        mock_styles.FONT_SIZE_CAPTION = _RealAppStyles.FONT_SIZE_CAPTION
        mock_styles.FONT_SIZE_LG = _RealAppStyles.FONT_SIZE_LG

        component = make_component(EmbeddedStatusCard)
        run_mount_effects(component, page=page)
        result = render_once(component)

    # 内部 VM 通过 hook 实例化, 从 component context 提取
    # 测试断言基于渲染结果, 不直接访问 VM
    return None, page, result, component


# ============================================================================
# 契约守护测试
# ============================================================================


class TestEmbeddedStatusCardContract:
    """EmbeddedStatusCard @ft.component 契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: EmbeddedStatusCard 必须被 @ft.component 装饰。"""
        assert hasattr(EmbeddedStatusCard, "__wrapped__"), "EmbeddedStatusCard 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_internal_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(factory=...) 内部 VM 模式订阅 (CLAUDE.md §3.3)。"""
        source = _read_source()
        assert "use_viewmodel(factory=" in source

    def test_no_business_state_in_view(self) -> None:
        """DoD: View 不持有业务状态 (MVVM §3.2) — 不应有 self. 业务字段赋值。

        @ft.component 函数式组件无 self, 通过源码检查 use_state 用于 i18n 订阅而非
        业务状态。
        """
        source = _read_source()
        # 禁止: use_state 持有业务数据 (host/port/user/password 等)
        forbidden = ["host", "port", "user", "password", "database"]
        for field in forbidden:
            # 允许出现在 i18n key 字符串中, 但不允许 use_state(lambda: host) 模式
            assert f"use_state(lambda: {field}" not in source, f"View 不应通过 use_state 持有业务字段 {field}"

    def test_signature_no_required_business_params(self) -> None:
        """DoD: EmbeddedStatusCard 签名不接受 vm 参数 (内部 VM 模式, 无业务依赖)。"""
        sig = inspect.signature(EmbeddedStatusCard)
        # 不应有 vm 必需参数
        if "vm" in sig.parameters:
            assert sig.parameters["vm"].default is not inspect.Parameter.empty, (
                "vm 不应是必需参数 (EmbeddedStatusCard 用内部 VM)"
            )


# ============================================================================
# 渲染测试
# ============================================================================


class TestEmbeddedStatusCardRendering:
    """EmbeddedStatusCard 渲染测试。"""

    def test_returns_container_with_column_content(self, mock_i18n_state, mock_app_colors_state) -> None:
        """默认渲染返回 ft.Container, content 为 ft.Column。"""
        _, _, result, _ = _render_card()
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_renders_status_message_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染 status_message Message 为 ft.Text (value=I18n.get(key))。"""
        _, _, result, _ = _render_card()
        ctrls = _walk_controls(result)
        # mock_i18n.get 返回 key, 所以 text.value == "embedded_pg_ready"
        status_texts = [c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "embedded_pg_ready"]
        assert len(status_texts) >= 1

    def test_renders_info_message_text(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染 info_message Message 为 ft.Text。"""
        _, _, result, _ = _render_card()
        ctrls = _walk_controls(result)
        info_texts = [
            c for c in ctrls if isinstance(c, ft.Text) and getattr(c, "value", None) == "embedded_pg_no_config_needed"
        ]
        assert len(info_texts) >= 1

    def test_renders_status_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 渲染 status icon (CHECK_CIRCLE for success)。"""
        _, _, result, _ = _render_card()
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) >= 1

    def test_no_form_fields_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: EmbeddedStatusCard 不渲染表单字段 (无 TextField/Checkbox)。"""
        _, _, result, _ = _render_card()
        ctrls = _walk_controls(result)
        text_fields = [c for c in ctrls if isinstance(c, ft.TextField)]
        checkboxes = [c for c in ctrls if isinstance(c, ft.Checkbox)]
        assert len(text_fields) == 0
        assert len(checkboxes) == 0


# ============================================================================
# VM 生命周期测试
# ============================================================================


class TestEmbeddedStatusCardVMLifecycle:
    """EmbeddedStatusCard 内部 VM 生命周期测试 (use_viewmodel factory 模式)。"""

    def test_mount_initializes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后通过 use_viewmodel(factory=...) 实例化内部 VM。"""
        _, _, _, component = _render_card()
        # 渲染成功即证明内部 VM 已实例化 (否则会 raise)
        assert component is not None

    def test_unmount_disposes_internal_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """DoD: 卸载时 dispose 内部 VM (use_viewmodel factory 模式默认 dispose_on_unmount=True)。"""
        with patch(
            "ui.components.config_panels.embedded_status_card.EmbeddedStatusCardViewModel.dispose"
        ) as mock_dispose:
            _, _, _, component = _render_card()
            run_unmount_effects(component)
        mock_dispose.assert_called_once()
