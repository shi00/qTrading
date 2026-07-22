"""DatabaseConfigPanel 路由容器单元测试 (P3-9).

P3-9 将 DatabaseConfigPanel 拆分为 EmbeddedStatusCard + ExternalPgForm,
原 DatabaseConfigPanel 改为路由容器按 ConfigHandler.is_embedded_mode() 切换:

- embedded 模式 → 渲染 EmbeddedStatusCard (只读状态)
- external 模式 → 渲染 ExternalPgForm (host/port/user/password/database 表单)

表单行为测试 (form fields / status display / button handlers / VM lifecycle)
已迁移至 test_external_pg_form.py, 本文件聚焦路由行为 + 模式切换契约。

路由容器返回子组件 Component 实例 (未渲染), _walk_controls 无法穿透。
改用 mock 子组件验证路由调用契约, 聚焦路由行为不重复子组件内部渲染测试
(子组件内部渲染测试在 test_external_pg_form.py / test_embedded_status_card.py)。
"""

import contextlib
import inspect
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    run_mount_effects,
)
from ui.components.config_panels import database_config_panel as panel_module
from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigState

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 database_config_panel.py 源码。"""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


class _FakeDatabaseConfigPanelVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。"""

    def __init__(
        self,
        state: DatabaseConfigState | None = None,
        is_embedded: bool = False,
    ) -> None:
        self._state = state if state is not None else DatabaseConfigState()
        self._subscribers: list[Any] = []
        self.is_embedded_mode = is_embedded
        self.test_connection = MagicMock()
        self.save_config = MagicMock()
        self.update_host = MagicMock()
        self.update_port = MagicMock()
        self.update_user = MagicMock()
        self.update_password = MagicMock()
        self.update_database = MagicMock()
        self.update_create_if_not_exists = MagicMock()

    @property
    def state(self) -> DatabaseConfigState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _render_panel_with_mocked_children(
    *,
    embedded_mode: bool,
    vm: _FakeDatabaseConfigPanelVM | None = None,
    show_header: bool = True,
    compact: bool = False,
    show_save_button: bool = True,
) -> tuple[MagicMock, MagicMock, Any, _FakeDatabaseConfigPanelVM]:
    """渲染 DatabaseConfigPanel, mock 子组件, 返回 (mock_external, mock_embedded, result, vm)。

    路由容器返回子组件 Component 实例 (未渲染), _walk_controls 无法穿透。
    改用 mock 子组件验证路由调用契约, 聚焦路由行为不重复子组件内部渲染测试。

    注意: run_mount_effects 内部已调用 render_once, 不需再显式调用 (否则 mock 被调用 2 次)。
    """
    if vm is None:
        vm = _FakeDatabaseConfigPanelVM(is_embedded=embedded_mode)
    else:
        vm.is_embedded_mode = embedded_mode
    page = FakePage()
    cast(Any, page).run_task = MagicMock()

    mock_external = MagicMock(return_value=ft.Container(content=ft.Text("EXTERNAL_MOCK")))
    mock_embedded = MagicMock(return_value=ft.Container(content=ft.Text("EMBEDDED_MOCK")))

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(panel_module, "ExternalPgForm", mock_external))
        stack.enter_context(patch.object(panel_module, "EmbeddedStatusCard", mock_embedded))
        # 通过 vm.is_embedded_mode 属性控制路由 (不再 patch ConfigHandler, MVVM 契约)
        component = make_component(
            DatabaseConfigPanel,
            vm=vm,
            show_header=show_header,
            compact=compact,
            show_save_button=show_save_button,
        )
        # run_mount_effects 内部已调用 render_once, 不再显式调用 (避免重复渲染)
        run_mount_effects(component, page=page)

    return mock_external, mock_embedded, None, vm


# ============================================================================
# 契约守护测试 (router)
# ============================================================================


class TestDatabaseConfigPanelRouterContract:
    """DatabaseConfigPanel 路由容器契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: DatabaseConfigPanel 必须被 @ft.component 装饰。"""
        assert hasattr(DatabaseConfigPanel, "__wrapped__"), "DatabaseConfigPanel 必须用 @ft.component 装饰"

    def test_signature_accepts_vm_and_flags(self) -> None:
        """DoD: DatabaseConfigPanel 签名保持兼容 (vm + show_header + compact + show_save_button)。"""
        sig = inspect.signature(DatabaseConfigPanel)
        assert "vm" in sig.parameters
        assert "show_header" in sig.parameters
        assert sig.parameters["show_header"].default is True
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True

    def test_router_calls_is_embedded_mode(self) -> None:
        """DoD: 路由必须调用 ConfigHandler.is_embedded_mode() 决定渲染分支。"""
        source = _read_source()
        assert "is_embedded_mode" in source

    def test_router_renders_external_pg_form(self) -> None:
        """DoD: 路由分支引用 ExternalPgForm (external 模式渲染目标)。"""
        source = _read_source()
        assert "ExternalPgForm" in source

    def test_router_renders_embedded_status_card(self) -> None:
        """DoD: 路由分支引用 EmbeddedStatusCard (embedded 模式渲染目标)。"""
        source = _read_source()
        assert "EmbeddedStatusCard" in source


# ============================================================================
# 路由行为测试: embedded / external 模式切换
# ============================================================================


class TestDatabaseConfigPanelRouting:
    """DatabaseConfigPanel 路由行为测试 — embedded/external 模式切换。

    路由容器返回子组件 Component 实例 (未渲染), _walk_controls 无法穿透。
    改用 mock 子组件验证路由调用契约, 聚焦路由行为不重复子组件内部渲染测试
    (子组件内部渲染测试在 test_external_pg_form.py / test_embedded_status_card.py)。
    """

    def test_external_mode_calls_external_pg_form(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """external 模式 → 调用 ExternalPgForm, 不调用 EmbeddedStatusCard。"""
        mock_external, mock_embedded, _, _ = _render_panel_with_mocked_children(embedded_mode=False)
        assert mock_external.call_count >= 1
        assert not mock_embedded.called

    def test_external_mode_passes_vm_and_flags_to_external_pg_form(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """external 模式 → ExternalPgForm 接收 vm + show_header + compact + show_save_button。"""
        vm = _FakeDatabaseConfigPanelVM()
        mock_external, _, _, _ = _render_panel_with_mocked_children(
            embedded_mode=False,
            vm=vm,
            show_header=True,
            compact=False,
            show_save_button=True,
        )
        # run_mount_effects 内部首次渲染 = 1 次调用
        mock_external.assert_called_once_with(
            vm=vm,
            show_header=True,
            compact=False,
            show_save_button=True,
        )

    def test_embedded_mode_calls_embedded_status_card(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """embedded 模式 → 调用 EmbeddedStatusCard, 不调用 ExternalPgForm。"""
        mock_external, mock_embedded, _, _ = _render_panel_with_mocked_children(embedded_mode=True)
        assert mock_embedded.call_count >= 1
        assert not mock_external.called

    def test_embedded_mode_calls_embedded_status_card_without_vm(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """embedded 模式 → EmbeddedStatusCard 不接收 vm 参数 (忽略传入的 vm)。"""
        _, mock_embedded, _, _ = _render_panel_with_mocked_children(embedded_mode=True)
        mock_embedded.assert_called_once_with()

    def test_embedded_mode_ignores_vm(self) -> None:
        """DoD: embedded 模式 → 忽略传入的 vm 参数 (用内部 EmbeddedStatusCardViewModel)。

        这是路由的核心契约: embedded 模式不依赖 DatabaseConfigPanelViewModel。
        通过源码检查 EmbeddedStatusCard() 调用不接受 vm 参数。
        """
        source = _read_source()
        # embedded 分支应调用 EmbeddedStatusCard() 不传 vm
        assert "EmbeddedStatusCard()" in source

    def test_external_mode_passes_vm_to_external_pg_form(self) -> None:
        """DoD: external 模式 → 把 vm 传给 ExternalPgForm (vm=vm)。"""
        source = _read_source()
        # 容忍多行参数格式: ExternalPgForm(\n    vm=vm,
        assert "ExternalPgForm(" in source
        assert "vm=vm" in source


# ============================================================================
# 模式切换契约 (R-B4)
# ============================================================================


class TestDatabaseConfigPanelModeSwitchContract:
    """R-B4 模式切换契约: DatabaseConfigPanel 必须按 is_embedded_mode() 切换渲染。"""

    def test_is_embedded_mode_property_exists_on_vm(self) -> None:
        """DoD: DatabaseConfigPanelViewModel 暴露 is_embedded_mode property 供 View 路由 (MVVM)."""
        from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel

        assert isinstance(
            DatabaseConfigPanelViewModel.is_embedded_mode,
            property,
        ), "VM 应暴露 is_embedded_mode property 供 View 路由 (避免 View 直接 import ConfigHandler)"

    def test_switching_mode_changes_child_component(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ) -> None:
        """DoD: 切换 vm.is_embedded_mode → 调用不同的子组件。"""
        # external 模式: 调用 ExternalPgForm
        mock_ext_1, mock_emb_1, _, _ = _render_panel_with_mocked_children(embedded_mode=False)
        assert mock_ext_1.called and not mock_emb_1.called

        # embedded 模式: 调用 EmbeddedStatusCard
        mock_ext_2, mock_emb_2, _, _ = _render_panel_with_mocked_children(embedded_mode=True)
        assert mock_emb_2.called and not mock_ext_2.called
