"""DatabaseTab 3 面板 + 高级模式开关测试 (P3-13).

覆盖:
1. 默认渲染含 EmbeddedStatusCard + DatabaseStatusPanel + BackupRestorePanel
2. db_show_advanced=False 时不渲染 ExternalPgForm
3. 开启后渲染 ExternalPgForm
4. 切换开关调用 ConfigHandler.save_config 持久化 db_show_advanced
5. use_effect 从 AppConfig 读取初始状态
6. 渲染含"离线维护工具"文本

测试策略: patch 子组件为 MagicMock (避免实际实例化内部 VM),
通过 mock 调用次数验证渲染分支。
"""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
)

pytestmark = pytest.mark.unit


class _FakeDatabaseVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel hook 契约。"""

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()
        self.reload_config = MagicMock()
        self.dispose_called = False
        self.load_show_advanced = MagicMock(return_value=False)
        self.save_show_advanced = AsyncMock()

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()


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


def _render_tab(
    *,
    db_show_advanced: bool = False,
) -> tuple[Any, dict[str, MagicMock], Any]:
    """渲染 DatabaseTab, 返回 (result, mock_components, component)。

    mock_components 含:
        - embedded_status_card
        - database_status_panel
        - backup_restore_panel
        - external_pg_form
        - database_config_vm_cls (DatabaseConfigPanelViewModel mock)
        - load_config_mock (fake_vm.load_show_advanced)
        - save_config_mock (fake_vm.save_show_advanced)

    通过 fake_vm 的 load_show_advanced/save_show_advanced 方法控制 db_show_advanced
    配置读写 (MVVM: View 不直接 import ConfigHandler)。
    """
    from ui.views.settings_tabs import database_tab as mod

    fake_vm = _FakeDatabaseVM()
    fake_vm.load_show_advanced.return_value = db_show_advanced
    mock_components: dict[str, MagicMock] = {}

    with (
        patch.object(mod, "EmbeddedStatusCard") as mock_esc,
        patch.object(mod, "DatabaseStatusPanel") as mock_dsp,
        patch.object(mod, "BackupRestorePanel") as mock_brp,
        patch.object(mod, "ExternalPgForm") as mock_epf,
        patch.object(mod, "DatabaseConfigPanelViewModel", return_value=fake_vm) as mock_vm_cls,
    ):
        component = make_component(mod.DatabaseTab, show_snack_callback=MagicMock())
        run_mount_effects(component)
        result = render_once(component)

        mock_components["embedded_status_card"] = mock_esc
        mock_components["database_status_panel"] = mock_dsp
        mock_components["backup_restore_panel"] = mock_brp
        mock_components["external_pg_form"] = mock_epf
        mock_components["database_config_vm_cls"] = mock_vm_cls
        mock_components["load_config_mock"] = fake_vm.load_show_advanced
        mock_components["save_config_mock"] = fake_vm.save_show_advanced

    return result, mock_components, component


# ============================================================================
# 测试用例
# ============================================================================


class TestDatabaseTab3Panel:
    """DatabaseTab 3 面板默认显示 + 高级模式开关测试 (P3-13)。"""

    def test_default_renders_three_panels(self, mock_i18n_state: Any, mock_app_colors_state: Any) -> None:
        """DoD 1: 默认渲染含 EmbeddedStatusCard + DatabaseStatusPanel + BackupRestorePanel。"""
        _, mocks, _ = _render_tab(db_show_advanced=False)

        assert mocks["embedded_status_card"].call_count >= 1, "默认应渲染 EmbeddedStatusCard"
        assert mocks["database_status_panel"].call_count >= 1, "默认应渲染 DatabaseStatusPanel"
        assert mocks["backup_restore_panel"].call_count >= 1, "默认应渲染 BackupRestorePanel"

    def test_advanced_toggle_off_by_default(self, mock_i18n_state: Any, mock_app_colors_state: Any) -> None:
        """DoD 2: db_show_advanced=False 时不渲染 ExternalPgForm。"""
        _, mocks, _ = _render_tab(db_show_advanced=False)

        assert not mocks["external_pg_form"].called, "高级模式关闭时不应渲染 ExternalPgForm"

    def test_advanced_toggle_on_renders_external_pg_form(
        self, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        """DoD 3: 开启后渲染 ExternalPgForm。"""
        _, mocks, _ = _render_tab(db_show_advanced=True)

        assert mocks["external_pg_form"].call_count >= 1, "高级模式开启时应渲染 ExternalPgForm"
        # 验证 ExternalPgForm 接收正确的参数
        call_kwargs = mocks["external_pg_form"].call_args.kwargs
        assert call_kwargs.get("show_header") is True
        assert call_kwargs.get("show_save_button") is True

    def test_toggle_persists_to_appconfig(self, mock_i18n_state: Any, mock_app_colors_state: Any) -> None:
        """DoD 4: 切换开关通过 page.run_task 调度 async handler 持久化 db_show_advanced (R16)."""
        from ui.views.settings_tabs import database_tab as mod

        fake_vm = _FakeDatabaseVM()
        fake_vm.load_show_advanced.return_value = False

        with (
            patch.object(mod, "EmbeddedStatusCard"),
            patch.object(mod, "DatabaseStatusPanel"),
            patch.object(mod, "BackupRestorePanel"),
            patch.object(mod, "ExternalPgForm"),
            patch.object(mod, "DatabaseConfigPanelViewModel", return_value=fake_vm),
        ):
            component = make_component(mod.DatabaseTab, show_snack_callback=MagicMock())
            # 创建带 run_task 的 fake page (R16: sync wrapper + page.run_task 模式)
            page = FakePage()
            page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
            run_mount_effects(component, page=page)
            result = render_once(component)

            # 在 mock 上下文内查找 advanced_switch 并触发 on_change
            switches = [c for c in _walk_controls(result) if isinstance(c, ft.Switch)]
            assert len(switches) >= 1, "应渲染至少 1 个 ft.Switch (高级模式开关)"
            advanced_switch = switches[0]
            assert advanced_switch.on_change is not None, "Switch 必须有 on_change 处理器"

            # 构造 ControlEvent mock 并触发 sync wrapper
            e = MagicMock()
            e.control.value = True
            page.run_task.reset_mock()
            cast(Callable[[Any], Any], advanced_switch.on_change)(e)

            # 验证 page.run_task 被调用, handler 为 async 函数, 参数为 (True,)
            assert page.run_task.call_args is not None, "page.run_task 应被调用 (R16)"
            handler = page.run_task.call_args.args[0]
            assert inspect.iscoroutinefunction(handler), "handler 应为 async 函数"
            handler_args = page.run_task.call_args.args[1:]
            assert handler_args == (True,), f"run_task 参数应为 (True,), 实际 {handler_args}"

            # 驱动 async handler 执行, 验证 vm.save_show_advanced 被调用
            asyncio.run(handler(*handler_args))

        # 验证 save_show_advanced 被调用, 参数为 True
        fake_vm.save_show_advanced.assert_called_once_with(True)

    def test_loads_advanced_state_from_config_on_mount(self, mock_i18n_state: Any, mock_app_colors_state: Any) -> None:
        """DoD 5: use_effect 从 AppConfig 读取初始状态。"""
        # load_config 返回 True, use_effect 应将 show_advanced 设为 True,
        # 从而触发 ExternalPgForm 渲染
        _, mocks, _ = _render_tab(db_show_advanced=True)

        # 验证 load_config 被调用 (use_effect 挂载时执行)
        assert mocks["load_config_mock"].call_count >= 1
        # 验证 ExternalPgForm 被渲染 (说明 show_advanced 已被 use_effect 设置为 True)
        assert mocks["external_pg_form"].call_count >= 1, (
            "use_effect 应从 AppConfig 读取 db_show_advanced=True 并触发 ExternalPgForm 渲染"
        )

    def test_offline_maintenance_link_section_renders(self, mock_i18n_state: Any, mock_app_colors_state: Any) -> None:
        """DoD 6: 渲染含"离线维护工具"文本。"""
        result, _, _ = _render_tab(db_show_advanced=False)

        # 遍历控件树找含 settings_db_offline_maintenance_title 的 Text
        texts = [c for c in _walk_controls(result) if isinstance(c, ft.Text)]
        # I18n.get 会返回真实字符串 (mock_i18n_state 注入 locale=DEFAULT_LOCALE=zh)
        offline_texts = [t for t in texts if getattr(t, "value", None) and "离线维护工具" in str(t.value)]
        assert len(offline_texts) >= 1, "应渲染含'离线维护工具'的 Text 控件"

        # 同时验证描述文本存在
        desc_texts = [t for t in texts if getattr(t, "value", None) and "sidecar" in str(t.value)]
        assert len(desc_texts) >= 1, "应渲染含'sidecar'的描述 Text 控件"


# ============================================================================
# 模块级纯函数: _get_page + _do_save_show_advanced 异常路径
# ============================================================================


class TestGetPageRuntimeErrorGuard:
    """_get_page: ft.context.page 抛 RuntimeError 时返回 None (M12-001 diff-coverage)."""

    def test_get_page_returns_none_on_runtime_error(self) -> None:
        """DoD: ft.context.page 抛 RuntimeError → 返回 None, 不传播异常.

        ft.context.page 内部读 _context_page ContextVar, 为 None 时抛 RuntimeError.
        测试不在 Flet app callback 内, _context_page 默认为 None, 直接调用即触发 RuntimeError.
        """
        from flet.controls.context import _context_page

        from ui.views.settings_tabs import database_tab as mod

        saved = _context_page.get()
        _context_page.set(None)  # 模拟不在 Flet context
        try:
            result = mod._get_page()
        finally:
            _context_page.set(saved)
        assert result is None

    def test_get_page_returns_page_when_available(self) -> None:
        """DoD: ft.context.page 可用时返回 page 实例."""
        from flet.controls.context import _context_page

        from ui.views.settings_tabs import database_tab as mod

        mock_page = MagicMock()
        saved = _context_page.get()
        _context_page.set(mock_page)  # 模拟 Flet context 内
        try:
            result = mod._get_page()
        finally:
            _context_page.set(saved)
        assert result is mock_page


class TestDoSaveShowAdvancedExceptionPath:
    """_do_save_show_advanced: 异常路径覆盖 (M12-001 diff-coverage)."""

    @pytest.mark.asyncio
    async def test_do_save_show_advanced_exception_branch_via_closure(
        self, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        """DoD: 通过 page.run_task 捕获 closure, 验证 Exception 路径被吞没且 logger.debug 被调用.

        R2: CancelledError 必须传播 (单独测试, 见 test_do_save_show_advanced_propagates_cancelled_error).
        """
        from ui.views.settings_tabs import database_tab as mod

        fake_vm = _FakeDatabaseVM()
        fake_vm.save_show_advanced = AsyncMock(side_effect=RuntimeError("write fail"))

        with (
            patch.object(mod, "EmbeddedStatusCard"),
            patch.object(mod, "DatabaseStatusPanel"),
            patch.object(mod, "BackupRestorePanel"),
            patch.object(mod, "ExternalPgForm"),
            patch.object(mod, "DatabaseConfigPanelViewModel", return_value=fake_vm),
        ):
            component = make_component(mod.DatabaseTab, show_snack_callback=MagicMock())
            page = FakePage()
            page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
            run_mount_effects(component, page=page)
            result = render_once(component)

            # 找到 advanced_switch 并触发 on_change
            switches = [c for c in _walk_controls(result) if isinstance(c, ft.Switch)]
            assert len(switches) >= 1
            advanced_switch = switches[0]
            e = MagicMock()
            e.control.value = True
            cast(Callable[[Any], Any], advanced_switch.on_change)(e)

            # page.run_task 应被调用, 第一个参数是 _do_save_show_advanced closure
            assert page.run_task.call_args is not None
            handler = page.run_task.call_args.args[0]
            assert inspect.iscoroutinefunction(handler)
            handler_args = page.run_task.call_args.args[1:]

            # 驱动 closure 执行: fake_vm.save_show_advanced 抛 RuntimeError
            # except Exception 分支应吞没异常 + logger.debug 被调用
            with patch.object(mod.logger, "debug") as mock_debug:
                # 不应抛异常 (except Exception 吞没)
                await handler(*handler_args)

            # 强断言: 验证 logger.debug 收到 "Failed to persist db_show_advanced" 消息
            assert mock_debug.call_count == 1
            assert "Failed to persist db_show_advanced" in str(mock_debug.call_args)

    @pytest.mark.asyncio
    async def test_do_save_show_advanced_propagates_cancelled_error(
        self, mock_i18n_state: Any, mock_app_colors_state: Any
    ) -> None:
        """R2: vm.save_show_advanced 抛 CancelledError → 必须传播, 不被 except Exception 吞没."""
        from ui.views.settings_tabs import database_tab as mod

        fake_vm = _FakeDatabaseVM()
        fake_vm.save_show_advanced = AsyncMock(side_effect=asyncio.CancelledError())

        with (
            patch.object(mod, "EmbeddedStatusCard"),
            patch.object(mod, "DatabaseStatusPanel"),
            patch.object(mod, "BackupRestorePanel"),
            patch.object(mod, "ExternalPgForm"),
            patch.object(mod, "DatabaseConfigPanelViewModel", return_value=fake_vm),
        ):
            component = make_component(mod.DatabaseTab, show_snack_callback=MagicMock())
            page = FakePage()
            page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
            run_mount_effects(component, page=page)
            result = render_once(component)

            switches = [c for c in _walk_controls(result) if isinstance(c, ft.Switch)]
            advanced_switch = switches[0]
            e = MagicMock()
            e.control.value = True
            cast(Callable[[Any], Any], advanced_switch.on_change)(e)

            handler = page.run_task.call_args.args[0]
            handler_args = page.run_task.call_args.args[1:]

            # R2 红线: CancelledError 必须传播, 不能被 except Exception 吞没
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线测试仅验证异常传播, 无其他状态可断言
                await handler(*handler_args)
