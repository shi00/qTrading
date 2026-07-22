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

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

pytestmark = pytest.mark.unit


class _FakeDatabaseVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel hook 契约。"""

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()
        self.reload_config = MagicMock()
        self.dispose_called = False
        self.load_show_advanced = MagicMock(return_value=False)
        self.save_show_advanced = MagicMock()

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
        """DoD 4: 切换开关调用 vm.save_show_advanced 持久化 db_show_advanced。"""
        from ui.views.settings_tabs import database_tab as mod

        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

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
            run_mount_effects(component)
            result = render_once(component)

            # 在 mock 上下文内查找 advanced_switch 并触发 on_change
            switches = [c for c in _walk_controls(result) if isinstance(c, ft.Switch)]
            assert len(switches) >= 1, "应渲染至少 1 个 ft.Switch (高级模式开关)"
            advanced_switch = switches[0]
            assert advanced_switch.on_change is not None, "Switch 必须有 on_change 处理器"

            # 构造 ControlEvent mock
            e = MagicMock()
            e.control.value = True
            # on_change 类型为 ControlEventHandler[Switch] | None，
            # 实际是 _on_advanced_toggle(e: ControlEvent) -> None，接受 1 个参数
            cast(Callable[[Any], Any], advanced_switch.on_change)(e)

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
