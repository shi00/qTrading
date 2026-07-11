"""FailoverConfigPanel + ProviderCredentialDialog 契约守护测试（Phase D.1 声明式重写）。

测试策略（参考 3.2.1-3.2.7 声明式范式）：
1. 模块级纯函数单测（_render_message/_build_provider_options/_build_model_options/
   _build_links_row/_run_task_factory/_run_task_no_args/_build_list_item）
2. 契约守护测试（grep 命令式禁止模式 = 0 + 验证声明式 API）

声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化。
VM 由消费方实例化，View 通过 use_viewmodel(vm=vm) hook 订阅。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels import failover_config_panel as panel_module
from ui.components.config_panels.failover_config_panel import (
    FailoverConfigPanel,
    ProviderCredentialDialog,
    _build_links_row,
    _build_list_item,
    _build_model_options,
    _build_provider_options,
    _render_message,
    _run_task_factory,
    _run_task_no_args,
)
from ui.viewmodels import Message
from ui.viewmodels.failover_config_panel_view_model import (
    FailoverConfigPanelViewModel,
    FailoverItem,
)

pytestmark = pytest.mark.unit


PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "ui"
    / "components"
    / "config_panels"
    / "failover_config_panel.py"
)


# --- 模块级纯函数：_render_message ---


class TestRenderMessage:
    """_render_message: Message → 本地化文本渲染（View 层，调 I18n.get）。"""

    def test_none_message_returns_empty(self):
        assert _render_message(None) == ""

    def test_message_without_params_calls_i18n_get(self):
        msg = Message(key="failover_test_success")
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "测试成功"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("failover_test_success")
        assert result == "测试成功"

    def test_message_with_detail_param_appends_detail(self):
        msg = Message(key="failover_test_failed", params={"detail": "Invalid key"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "测试失败"
            result = _render_message(msg)
        # 调用 I18n.get 时应过滤掉 detail 参数
        mock_i18n.get.assert_called_once_with("failover_test_failed")
        assert result == "测试失败: Invalid key"

    def test_message_with_format_params_passes_to_i18n(self):
        msg = Message(key="failover_validation_missing", params={"providers": "deepseek, qwen"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "缺失凭证: deepseek, qwen"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("failover_validation_missing", providers="deepseek, qwen")
        assert result == "缺失凭证: deepseek, qwen"

    def test_message_with_detail_and_format_params(self):
        msg = Message(key="failover_test_failed", params={"detail": "Network error", "provider": "deepseek"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "deepseek 测试失败"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("failover_test_failed", provider="deepseek")
        assert "Network error" in result
        assert "deepseek 测试失败" in result


# --- 模块级纯函数：_build_provider_options ---


class TestBuildProviderOptions:
    """_build_provider_options: 构建供应商下拉选项（排除 custom + 已存在供应商）。"""

    def test_excludes_custom_provider(self):
        options = _build_provider_options(is_edit=False, existing_providers=())
        keys = [opt.key for opt in options]
        assert "custom" not in keys

    def test_excludes_existing_providers_in_add_mode(self):
        options = _build_provider_options(is_edit=False, existing_providers=("deepseek",))
        keys = [opt.key for opt in options]
        assert "deepseek" not in keys
        # 其他供应商仍应存在
        assert "qwen" in keys

    def test_includes_existing_providers_in_edit_mode(self):
        options = _build_provider_options(is_edit=True, existing_providers=("deepseek",))
        keys = [opt.key for opt in options]
        # 编辑模式下应包含当前编辑的供应商
        assert "deepseek" in keys

    def test_returns_options_with_text_label(self):
        options = _build_provider_options(is_edit=False, existing_providers=())
        for opt in options:
            assert opt.text is not None
            assert isinstance(opt.text, str)


# --- 模块级纯函数：_build_model_options ---


class TestBuildModelOptions:
    """_build_model_options: 构建指定供应商的模型下拉选项。"""

    def test_returns_models_for_known_provider(self):
        options = _build_model_options("deepseek")
        keys = [opt.key for opt in options]
        assert len(keys) > 0
        # deepseek 应有 deepseek-v4-pro / deepseek-v4-flash
        assert "deepseek-v4-pro" in keys
        assert "deepseek-v4-flash" in keys

    def test_returns_empty_for_unknown_provider(self):
        options = _build_model_options("unknown-provider")
        assert options == []

    def test_returns_empty_for_custom_provider(self):
        # custom 供应商无 models 列表
        options = _build_model_options("custom")
        assert options == []

    def test_label_includes_display_tag_when_present(self):
        options = _build_model_options("deepseek")
        # 至少一个模型应有 tag，label 应包含 "(...)"
        labels = [opt.text for opt in options if opt.text]
        # deepseek-v4-flash 有 tag_recommend
        flash_label = next((label for label in labels if "deepseek-v4-flash" in label), None)
        assert flash_label is not None
        # tag_recommend 应被翻译为 display_tag，附加在 label 后
        assert "(" in flash_label


# --- 模块级纯函数：_build_links_row ---


class TestBuildLinksRow:
    """_build_links_row: 构建供应商相关链接行。"""

    def test_returns_row_with_links_for_provider_with_urls(self):
        # deepseek 有 console_url / pricing_url / models_url
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek")
        assert isinstance(row, ft.Row)
        # 应有 3 个 TextButton（console / pricing / models）
        assert len(row.controls) == 3
        for ctrl in row.controls:
            assert isinstance(ctrl, ft.TextButton)
            assert ctrl.url  # 每个 button 都应有 url

    def test_returns_empty_row_for_provider_without_urls(self):
        # custom 供应商无任何 URL
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("custom")
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 0

    def test_returns_empty_row_for_unknown_provider(self):
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("unknown")
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 0


# --- 模块级纯函数：_run_task_factory / _run_task_no_args ---


class TestRunTaskFactory:
    """_run_task_factory / _run_task_no_args: 事件处理器工厂，通过 page.run_task 提交 VM 异步命令（R16）。"""

    def test_run_task_no_args_returns_callable(self):
        coro_func = MagicMock()
        handler = _run_task_no_args(coro_func)
        assert callable(handler)

    def test_run_task_factory_returns_callable(self):
        coro_func = MagicMock()
        handler = _run_task_factory(coro_func, "arg1", "arg2")
        assert callable(handler)

    def test_handler_calls_page_run_task_with_args(self):
        coro_func = MagicMock()
        handler = _run_task_factory(coro_func, "arg1", "arg2")

        # Mock ft.context.page 返回 mock page（patch module-level ft.context）
        mock_page = MagicMock()
        with patch("ui.components.config_panels.failover_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            handler(MagicMock())

        mock_page.run_task.assert_called_once_with(coro_func, "arg1", "arg2")

    def test_handler_no_args_calls_page_run_task(self):
        coro_func = MagicMock()
        handler = _run_task_no_args(coro_func)

        mock_page = MagicMock()
        with patch("ui.components.config_panels.failover_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            handler(MagicMock())

        mock_page.run_task.assert_called_once_with(coro_func)

    def test_handler_swallows_runtime_error_when_page_unavailable(self):
        """page 未挂载时 ft.context.page 抛 RuntimeError，handler 应静默处理。"""
        coro_func = MagicMock()
        coro_func.__name__ = "test_coro"
        handler = _run_task_no_args(coro_func)

        # ft.context.page 抛 RuntimeError
        with patch("ui.components.config_panels.failover_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            # 不应抛异常
            handler(MagicMock())

    def test_handler_does_not_call_run_task_when_page_none(self):
        """ft.context.page 抛 RuntimeError 时 handler 静默返回，不调用 coro_func。"""
        coro_func = MagicMock()
        coro_func.__name__ = "test_coro"
        handler = _run_task_no_args(coro_func)

        with patch("ui.components.config_panels.failover_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            handler(MagicMock())
        # coro_func 不应被调用（handler 只调 page.run_task）
        coro_func.assert_not_called()


# --- 模块级纯函数：_build_list_item ---


class TestBuildListItem:
    """_build_list_item: 构建单个 failover 列表项（由 state.failover_items 驱动）。"""

    def _make_item(self, has_credential: bool = True) -> FailoverItem:
        return FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=has_credential,
            api_key_masked="sk-***" if has_credential else "",
        )

    def _collect_icon_buttons(self, container: ft.Container) -> list[ft.IconButton]:
        """从 container.content（Column）中递归收集所有 IconButton。

        结构：Container > Column > [Row[left_section, Container, right_section], Row[status_text]]
        right_section 是 Row[btn_up, btn_down, btn_edit, btn_delete]，需递归搜索。
        """
        assert container.content is not None
        icon_buttons: list[ft.IconButton] = []

        def _walk(ctrl: ft.Control) -> None:
            if isinstance(ctrl, ft.IconButton):
                icon_buttons.append(ctrl)
                return
            controls = getattr(ctrl, "controls", None)
            if controls is None:
                return
            for sub in controls:
                _walk(sub)

        _walk(container.content)
        return icon_buttons

    def test_returns_container(self):
        vm = MagicMock(spec=FailoverConfigPanelViewModel)
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            container = _build_list_item(0, self._make_item(), 1, vm)
        assert isinstance(container, ft.Container)

    def test_index_zero_disables_up_button(self):
        vm = MagicMock(spec=FailoverConfigPanelViewModel)
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            container = _build_list_item(0, self._make_item(), 2, vm)
        icon_buttons = self._collect_icon_buttons(container)
        # 上移按钮应 disabled
        up_btn = next(b for b in icon_buttons if b.icon == ft.Icons.ARROW_UPWARD)
        assert up_btn.disabled is True

    def test_last_index_disables_down_button(self):
        vm = MagicMock(spec=FailoverConfigPanelViewModel)
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            container = _build_list_item(1, self._make_item(), 2, vm)
        icon_buttons = self._collect_icon_buttons(container)
        down_btn = next(b for b in icon_buttons if b.icon == ft.Icons.ARROW_DOWNWARD)
        assert down_btn.disabled is True

    def test_middle_index_enables_both_buttons(self):
        vm = MagicMock(spec=FailoverConfigPanelViewModel)
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            container = _build_list_item(1, self._make_item(), 3, vm)
        icon_buttons = self._collect_icon_buttons(container)
        up_btn = next(b for b in icon_buttons if b.icon == ft.Icons.ARROW_UPWARD)
        down_btn = next(b for b in icon_buttons if b.icon == ft.Icons.ARROW_DOWNWARD)
        assert up_btn.disabled is False
        assert down_btn.disabled is False


# --- 契约守护测试：声明式组件禁止命令式模式 ---


class TestFailoverConfigPanelContract:
    """契约守护测试：声明式组件禁止命令式模式。"""

    def _read_panel_content(self) -> str:
        return PANEL_PATH.read_text(encoding="utf-8")

    def test_no_imperative_patterns(self) -> None:
        """grep 命令式禁止模式 = 0（did_mount/will_unmount/refresh_locale/_on_locale_change/_safe_update/.update()/class X(ft.Container)/class X(ft.AlertDialog)/PageRefMixin/_page_ref/page.show_dialog/page.pop_dialog）。"""
        content = self._read_panel_content()
        forbidden_patterns = [
            "def did_mount",
            "def will_unmount",
            "def refresh_locale",
            "def _on_locale_change",
            "def _safe_update",
            "self.update()",
            "class FailoverConfigPanel(ft.Container)",
            "class FailoverConfigPanel(ft.AlertDialog)",
            "class FailoverConfigPanel(ft.UserControl)",
            "class FailoverConfigPanel(PageRefMixin",
            "class ProviderCredentialDialog(ft.Container)",
            "class ProviderCredentialDialog(ft.AlertDialog)",
            "class ProviderCredentialDialog(ft.UserControl)",
            "class ProviderCredentialDialog(PageRefMixin",
            "PageRefMixin",
            "_page_ref",
            "page.show_dialog",
            "page.pop_dialog",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"禁止命令式模式: {pattern}"

    def test_is_declarative_component(self) -> None:
        """验证 FailoverConfigPanel 和 ProviderCredentialDialog 都是 @ft.component 声明式组件。"""
        content = self._read_panel_content()
        assert "@ft.component" in content
        assert "def FailoverConfigPanel(" in content
        assert "def ProviderCredentialDialog(" in content

    def test_uses_use_viewmodel_external_vm_mode(self) -> None:
        """验证通过 use_viewmodel(vm=vm) 外部 VM 模式订阅（CLAUDE.md §3.3）。"""
        content = self._read_panel_content()
        assert "use_viewmodel(vm=vm)" in content

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 自动重渲染。"""
        content = self._read_panel_content()
        assert "ft.use_state(I18n.get_observable_state)" in content

    def test_uses_use_dialog_hook(self) -> None:
        """验证通过 ft.use_dialog 自动挂载/卸载 dialog（Phase 3.0.2 模式）。"""
        content = self._read_panel_content()
        assert "ft.use_dialog(" in content

    def test_uses_ft_context_page(self) -> None:
        """验证通过 ft.context.page 访问 page（try/except RuntimeError 守卫）。"""
        content = self._read_panel_content()
        assert "ft.context.page" in content
        assert "RuntimeError" in content

    def test_dialog_uses_conditional_render(self) -> None:
        """验证 Dialog 用条件渲染（state.dialog_open 驱动），而非 show_dialog/pop_dialog。"""
        content = self._read_panel_content()
        assert "if state.dialog_open" in content

    def test_no_use_ref_caching_imperative_instances(self) -> None:
        """验证禁止用 use_ref 缓存命令式实例。"""
        content = self._read_panel_content()
        assert "use_ref" not in content

    def test_module_exports_preserved(self) -> None:
        """验证模块导出 API：FailoverConfigPanel + ProviderCredentialDialog。"""
        content = self._read_panel_content()
        assert "def FailoverConfigPanel(" in content
        assert "def ProviderCredentialDialog(" in content

    def test_pure_helper_functions_preserved(self) -> None:
        """验证模块级纯函数保留导出。"""
        content = self._read_panel_content()
        assert "def _render_message(" in content
        assert "def _build_provider_options(" in content
        assert "def _build_model_options(" in content
        assert "def _build_links_row(" in content
        assert "def _build_list_item(" in content
        assert "def _run_task_factory(" in content
        assert "def _run_task_no_args(" in content

    def test_status_display_config_preserved(self) -> None:
        """验证状态显示配置保留（_STATUS_ICON_MAP / _STATUS_COLOR_MAP）。"""
        content = self._read_panel_content()
        assert "_STATUS_ICON_MAP" in content
        assert "_STATUS_COLOR_MAP" in content


# --- 契约守护测试：组件签名 ---


class TestComponentSignatures:
    """验证 FailoverConfigPanel 和 ProviderCredentialDialog 的函数签名。"""

    def test_failover_config_panel_accepts_vm_param(self):
        """FailoverConfigPanel 应接收 vm 参数（外部 VM 模式）。"""
        import inspect

        sig = inspect.signature(FailoverConfigPanel)
        assert "vm" in sig.parameters
        assert sig.parameters["vm"].annotation.__name__ == "FailoverConfigPanelViewModel"

    def test_failover_config_panel_accepts_show_save_button_kwarg(self):
        """FailoverConfigPanel 应接收 show_save_button 关键字参数（default=True）。"""
        import inspect

        sig = inspect.signature(FailoverConfigPanel)
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True

    def test_provider_credential_dialog_accepts_vm_param(self):
        """ProviderCredentialDialog 应接收 vm 参数。"""
        import inspect

        sig = inspect.signature(ProviderCredentialDialog)
        assert "vm" in sig.parameters

    def test_failover_config_panel_is_callable(self):
        """FailoverConfigPanel 应是可调用函数（@ft.component 装饰后仍可调用）。"""
        assert callable(FailoverConfigPanel)

    def test_provider_credential_dialog_is_callable(self):
        """ProviderCredentialDialog 应是可调用函数。"""
        assert callable(ProviderCredentialDialog)
