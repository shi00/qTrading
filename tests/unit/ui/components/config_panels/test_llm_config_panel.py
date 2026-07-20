"""LLMConfigPanel 组件运行时测试 (Task 3.1).

覆盖:
1. 契约守护: 声明式范式合规性 + _render_message R9 守卫 (不含 api_key)
2. 模块级纯函数: _get_provider_name / _build_provider_options / _build_model_options /
   _build_links_row / _render_message
3. 工厂函数: _on_test_click_factory / _on_save_click_factory / _on_refresh_click_factory /
   _on_provider_change_factory 的 page 可用/None/RuntimeError 守卫
4. 组件运行时: compact / show_save_button / show_register_link / is_azure / show_custom_model_input
5. provider 切换时 model_options 重算
6. 验证 MODELS_API_COMPATIBLE 常量成员

test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
本文件聚焦运行时行为 + R9 守卫 + factory 函数 + 组件体渲染, 不重复基础契约检查。
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
    render_once,
    run_mount_effects,
)
from ui.components.config_panels import llm_config_panel as panel_module
from ui.components.config_panels.llm_config_panel import (
    LLMConfigPanel,
    MODELS_API_COMPATIBLE,
    _build_links_row,
    _build_model_options,
    _build_provider_options,
    _get_provider_name,
    _on_provider_change_factory,
    _on_refresh_click_factory,
    _on_save_click_factory,
    _on_test_click_factory,
    _render_message,
)
from ui.viewmodels import Message
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel, LLMConfigState

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 llm_config_panel.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(panel_module.__file__).read_text(encoding="utf-8")


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _make_event(value: str | None = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树 (含 controls/items/content)。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归)。
    """
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


def _find_dropdown(root: Any, label: str) -> ft.Dropdown:
    """通过 label 查找 Dropdown 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"Dropdown with label={label} not found")


def _find_text_field(root: Any, label: str) -> ft.TextField:
    """通过 label 查找 TextField 控件。"""
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == label:
            return ctrl
    raise AssertionError(f"TextField with label={label} not found")


def _page_run_task(page: FakePage) -> MagicMock:
    """获取 page.run_task mock (动态注入, pyright safe)。

    FakePage 类不定义 run_task 属性, _render_panel 通过实例属性动态注入 MagicMock。
    用 cast(Any, page) 绕过 reportAttributeAccessIssue (ruff B009 禁止 getattr 常量属性)。
    """
    return cast(MagicMock, cast(Any, page).run_task)


# ============================================================================
# 契约守护测试 (扩展 test_config_panels.py 基础契约)
# ============================================================================


class TestLLMConfigPanelContractExtension:
    """LLMConfigPanel 契约守护扩展测试。

    test_config_panels.py 已覆盖基础契约 (@ft.component / 无 did_mount / 无 .update() 等),
    此处补充 factory 函数守卫 + use_viewmodel 外部 VM 模式 + ft.context.page 访问 + 常量验证。
    """

    def test_is_ft_component(self) -> None:
        """DoD: LLMConfigPanel 必须被 @ft.component 装饰。"""
        assert hasattr(LLMConfigPanel, "__wrapped__"), "LLMConfigPanel 必须用 @ft.component 装饰"

    def test_uses_use_viewmodel_external_vm_mode(self) -> None:
        """DoD: 必须通过 use_viewmodel(vm=vm) 外部 VM 模式订阅 (CLAUDE.md §3.3)。"""
        source = _read_source()
        assert "use_viewmodel(vm=vm)" in source

    def test_uses_ft_context_page(self) -> None:
        """DoD: page 访问用 ft.context.page, try/except 守卫 RuntimeError。"""
        source = _read_source()
        assert "ft.context.page" in source
        assert "RuntimeError" in source

    def test_factory_functions_defined(self) -> None:
        """DoD: 4 个 factory 函数必须存在。"""
        source = _read_source()
        assert "def _on_test_click_factory(" in source
        assert "def _on_save_click_factory(" in source
        assert "def _on_refresh_click_factory(" in source
        assert "def _on_provider_change_factory(" in source

    def test_models_api_compatible_constant_members(self) -> None:
        """DoD: MODELS_API_COMPATIBLE 必须包含 8 个供应商 ID。"""
        expected = {
            "openai",
            "deepseek",
            "qwen",
            "zhipu",
            "moonshot",
            "mistral",
            "minimax",
            "custom",
        }
        assert expected == MODELS_API_COMPATIBLE

    def test_panel_signature_accepts_vm_and_flags(self) -> None:
        """DoD: LLMConfigPanel 签名应接收 vm + show_save_button + compact + show_register_link。"""
        sig = inspect.signature(LLMConfigPanel)
        assert "vm" in sig.parameters
        assert "show_save_button" in sig.parameters
        assert sig.parameters["show_save_button"].default is True
        assert "compact" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert "show_register_link" in sig.parameters
        assert sig.parameters["show_register_link"].default is True


class TestRenderMessageR9Guard:
    """R9 红线: _render_message 不应接触 api_key (View 层不主动泄露敏感信息)。"""

    def test_render_message_none_returns_empty(self) -> None:
        """_render_message(None) 返回空字符串。"""
        assert _render_message(None) == ""

    def test_render_message_with_default_param(self) -> None:
        """_render_message(Message) 调 I18n.get 返回本地化文本。"""
        msg = Message("_raw_msg_", {"default": "raw error text"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "raw error text"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("_raw_msg_", default="raw error text")
        assert result == "raw error text"

    def test_render_message_with_format_params(self) -> None:
        """_render_message 透传 msg.params 给 I18n.get。"""
        msg = Message("llm_switch_provider_hint", {"provider": "DeepSeek"})
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.return_value = "已切换到 DeepSeek"
            result = _render_message(msg)
        mock_i18n.get.assert_called_once_with("llm_switch_provider_hint", provider="DeepSeek")
        assert result == "已切换到 DeepSeek"

    def test_render_message_function_body_has_no_api_key_reference(self) -> None:
        """R9: _render_message 函数源码不应直接引用 api_key 字段。

        _render_message 只调 I18n.get(msg.key, **msg.params), 不应:
        - 显式访问 state.api_key
        - 将 api_key 作为参数传给 I18n.get
        - 在日志中记录 api_key
        View 透传 msg.params, VM 负责避免将 api_key 放入 params。
        """
        import ast

        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_render_message":
                func_start = node.lineno
                func_end = node.end_lineno or node.lineno
                func_lines = source.splitlines()[func_start - 1 : func_end]
                func_body = "\n".join(func_lines)
                assert "api_key" not in func_body, (
                    "_render_message 不应直接引用 api_key (R9): api_key 应由 VM 管理, 不应进入 View 层"
                )
                return
        raise AssertionError("_render_message 函数未找到")


# ============================================================================
# 模块级纯函数: _get_provider_name
# ============================================================================


class TestGetProviderName:
    """_get_provider_name: 获取供应商显示名称 (locale 感知)。"""

    def test_zh_cn_returns_name(self) -> None:
        """zh_CN locale 返回 provider['name']。"""
        provider = {"name": "智谱 AI", "name_en": "Zhipu AI"}
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.current_locale.return_value = "zh_CN"
            result = _get_provider_name(provider, "zhipu")
        assert result == "智谱 AI"

    def test_non_zh_cn_returns_name_en(self) -> None:
        """非 zh_CN locale 返回 provider['name_en']。"""
        provider = {"name": "智谱 AI", "name_en": "Zhipu AI"}
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.current_locale.return_value = "en_US"
            result = _get_provider_name(provider, "zhipu")
        assert result == "Zhipu AI"

    def test_non_zh_cn_falls_back_to_name_when_name_en_missing(self) -> None:
        """非 zh_CN locale 但 name_en 缺失时 fallback 到 name。"""
        provider = {"name": "智谱 AI"}
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.current_locale.return_value = "en_US"
            result = _get_provider_name(provider, "zhipu")
        assert result == "智谱 AI"

    def test_zh_cn_name_missing_falls_back_to_provider_id(self) -> None:
        """zh_CN locale 但 name 缺失时 fallback 到 provider_id。"""
        provider: dict[str, str] = {}
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.current_locale.return_value = "zh_CN"
            result = _get_provider_name(provider, "unknown")
        assert result == "unknown"

    def test_non_zh_cn_name_en_and_name_both_missing_falls_back_to_provider_id(self) -> None:
        """非 zh_CN locale 但 name_en 和 name 都缺失时 fallback 到 provider_id。"""
        provider: dict[str, str] = {}
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.current_locale.return_value = "en_US"
            result = _get_provider_name(provider, "unknown")
        assert result == "unknown"


# ============================================================================
# 模块级纯函数: _build_provider_options
# ============================================================================


class TestBuildProviderOptions:
    """_build_provider_options: 构建供应商下拉选项 (国内/国际/自定义分组)。"""

    def test_returns_dropdown_option_list(self) -> None:
        """返回 ft.dropdown.Option 列表。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        assert isinstance(options, list)
        assert all(isinstance(o, ft.dropdown.Option) for o in options)

    def test_domestic_group_header_disabled(self) -> None:
        """国内分组标题 disabled=True。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        # 第一个 option 是国内分组标题
        assert options[0].disabled is True
        assert options[0].key == "llm_provider_domestic"

    def test_international_group_header_disabled(self) -> None:
        """国际分组标题 disabled=True。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        intl_header = next(o for o in options if o.key == "llm_provider_international")
        assert intl_header.disabled is True

    def test_custom_group_header_disabled(self) -> None:
        """自定义分组标题 disabled=True。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        custom_header = next(o for o in options if o.key == "llm_provider_custom_group")
        assert custom_header.disabled is True

    def test_includes_all_domestic_providers(self) -> None:
        """国内分组包含 deepseek/qwen/zhipu/moonshot/minimax。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        keys = {o.key for o in options}
        assert {"deepseek", "qwen", "zhipu", "moonshot", "minimax"} <= keys

    def test_includes_all_international_providers(self) -> None:
        """国际分组包含 openai/azure/anthropic/google/mistral。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        keys = {o.key for o in options}
        assert {"openai", "azure", "anthropic", "google", "mistral"} <= keys

    def test_includes_custom_provider(self) -> None:
        """自定义分组包含 custom。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        keys = {o.key for o in options}
        assert "custom" in keys

    def test_skips_missing_domestic_provider(self) -> None:
        """缺失的 domestic provider_id (LLM_PROVIDERS 中不存在) 应被跳过。"""
        fake_providers = {
            "deepseek": {"name": "DeepSeek", "name_en": "DeepSeek"},
            "custom": {"name": "自定义", "name_en": "Custom"},
        }
        with (
            patch.object(panel_module, "I18n") as mock_i18n,
            patch.object(panel_module, "LLM_PROVIDERS", fake_providers),
        ):
            mock_i18n.get.side_effect = lambda key, **kw: key
            mock_i18n.current_locale.return_value = "zh_CN"
            options = _build_provider_options()
        keys = {o.key for o in options}
        assert "deepseek" in keys
        assert "custom" in keys
        # qwen/openai 在 LLM_PROVIDERS 中被移除, 应被跳过
        assert "qwen" not in keys
        assert "openai" not in keys


# ============================================================================
# 模块级纯函数: _build_model_options
# ============================================================================


class TestBuildModelOptions:
    """_build_model_options: 构建指定供应商的模型下拉选项 (tag 需 i18n)。"""

    def test_returns_options_for_known_provider(self) -> None:
        """已知供应商返回 model 选项列表。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_model_options("deepseek")
        assert len(options) >= 1
        assert all(isinstance(o, ft.dropdown.Option) for o in options)
        keys = {o.key for o in options}
        assert "deepseek-v4-pro" in keys
        assert "deepseek-v4-flash" in keys

    def test_returns_empty_for_unknown_provider(self) -> None:
        """未知供应商返回空列表。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_model_options("unknown-provider")
        assert options == []

    def test_returns_empty_for_custom_provider(self) -> None:
        """custom 供应商无 models 列表, 返回空。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_model_options("custom")
        assert options == []

    def test_returns_empty_for_azure_provider(self) -> None:
        """azure 供应商 models 为空列表, 返回空。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_model_options("azure")
        assert options == []

    def test_tag_translated_via_i18n(self) -> None:
        """模型 tag 通过 I18n.get(get_display_tag(tag)) 翻译, label 含翻译后缀。"""
        with patch.object(panel_module, "I18n") as mock_i18n:

            def fake_get(key, **kw):
                if key == "tag_recommend":
                    return "推荐"
                if "default" in kw:
                    return kw["default"]
                return key

            mock_i18n.get.side_effect = fake_get
            options = _build_model_options("deepseek")
        # deepseek-v4-flash 的 tag 是 "tag_recommend"
        flash_opt = next(o for o in options if o.key == "deepseek-v4-flash")
        assert flash_opt.text is not None
        assert "推荐" in flash_opt.text

    def test_empty_tag_no_parentheses(self) -> None:
        """tag 为空字符串时 get_display_tag 返回空, label 不含括号。"""
        fake_providers = {
            "test_provider": {
                "name": "Test",
                "models": [{"id": "model-no-tag", "name": "Model No Tag", "tag": ""}],
            }
        }
        with (
            patch.object(panel_module, "I18n") as mock_i18n,
            patch.object(panel_module, "LLM_PROVIDERS", fake_providers),
        ):
            # get_display_tag("") 返回 "", I18n.get("", default="") 返回 ""
            mock_i18n.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else ""
            options = _build_model_options("test_provider")
        assert len(options) == 1
        assert options[0].text == "Model No Tag"  # 不应带括号

    def test_model_missing_name_falls_back_to_id(self) -> None:
        """model 缺失 name 字段时 text fallback 到 id。"""
        fake_providers = {
            "test_provider": {
                "name": "Test",
                "models": [{"id": "fallback-id", "tag": ""}],  # 缺 name
            }
        }
        with (
            patch.object(panel_module, "I18n") as mock_i18n,
            patch.object(panel_module, "LLM_PROVIDERS", fake_providers),
        ):
            mock_i18n.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else ""
            options = _build_model_options("test_provider")
        assert len(options) == 1
        assert options[0].text == "fallback-id"

    def test_list_tag_uses_first_non_internal_tag(self) -> None:
        """tag 为 list 时, get_display_tag 返回首个非 internal tag (跳过 reasoning)。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            options = _build_model_options("openai")
        # openai o4-mini 的 tag 是 ["tag_reasoning", "reasoning"]
        # get_display_tag 跳过 "reasoning", 返回 "tag_reasoning"
        o4_opt = next(o for o in options if o.key == "o4-mini")
        assert o4_opt.text is not None
        assert "tag_reasoning" in o4_opt.text


# ============================================================================
# 模块级纯函数: _build_links_row
# ============================================================================


class TestBuildLinksRow:
    """_build_links_row: 构建供应商相关链接行 (console_url/pricing_url/models_url)。"""

    def test_returns_row_with_all_three_links_for_deepseek(self) -> None:
        """deepseek 有 console_url/pricing_url/models_url 三个链接。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek", compact=False)
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 3
        for ctrl in row.controls:
            assert isinstance(ctrl, ft.TextButton)
            assert ctrl.url

    def test_returns_empty_row_for_custom_provider(self) -> None:
        """custom 供应商无任何 URL, 返回空 Row。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("custom", compact=False)
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 0

    def test_returns_empty_row_for_unknown_provider(self) -> None:
        """未知供应商返回空 Row。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("unknown", compact=False)
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 0

    def test_partial_urls_only_includes_present_links(self) -> None:
        """部分 URL 缺失时只包含存在的链接。"""
        fake_providers = {
            "test_provider": {
                "name": "Test",
                "console_url": "https://console.test/",
                # pricing_url 和 models_url 缺失
            }
        }
        with (
            patch.object(panel_module, "I18n") as mock_i18n,
            patch.object(panel_module, "LLM_PROVIDERS", fake_providers),
        ):
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("test_provider", compact=False)
        assert len(row.controls) == 1
        console_btn = cast(ft.TextButton, row.controls[0])
        assert console_btn.url == "https://console.test/"

    def test_compact_row_uses_center_alignment(self) -> None:
        """compact=True 时 Row 使用 MainAxisAlignment.CENTER + wrap=False + spacing=8。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek", compact=True)
        assert row.alignment == ft.MainAxisAlignment.CENTER
        assert row.wrap is False
        assert row.spacing == 8

    def test_non_compact_row_uses_start_alignment(self) -> None:
        """compact=False 时 Row 使用 MainAxisAlignment.START + wrap=True + spacing=10。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek", compact=False)
        assert row.alignment == ft.MainAxisAlignment.START
        assert row.wrap is True
        assert row.spacing == 10

    def test_compact_buttons_have_compact_style(self) -> None:
        """compact=True 时按钮带 compact_btn_style (非 None)。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek", compact=True)
        for ctrl in row.controls:
            assert isinstance(cast(ft.TextButton, ctrl).style, ft.ButtonStyle)

    def test_non_compact_buttons_have_no_style(self) -> None:
        """compact=False 时按钮 style=None。"""
        with patch.object(panel_module, "I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, **kw: key
            row = _build_links_row("deepseek", compact=False)
        for ctrl in row.controls:
            assert cast(ft.TextButton, ctrl).style is None


# ============================================================================
# 工厂函数: page 可用 → page.run_task
# ============================================================================


def _patch_ft_context_page(page: Any) -> Any:
    """patch panel_module.ft.context.page 返回指定 page (用 property 模拟).

    page 为 None / 抛 RuntimeError 的特殊情况用 lambda 控制。
    """
    ctx = patch("ui.components.config_panels.llm_config_panel.ft.context")
    mock_ctx = ctx.__enter__()
    type(mock_ctx).page = property(lambda self: page)
    return ctx


class TestFactoryFunctionsPageAvailable:
    """4 个 factory 函数: page 可用时调用 page.run_task。"""

    def test_on_test_click_factory_calls_run_task(self) -> None:
        """_on_test_click_factory: page 可用 → page.run_task(vm.verify_connection)。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.verify_connection)

    def test_on_save_click_factory_calls_run_task(self) -> None:
        """_on_save_click_factory: page 可用 → page.run_task(vm.save_config)。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.save_config)

    def test_on_refresh_click_factory_calls_run_task(self) -> None:
        """_on_refresh_click_factory: page 可用 → page.run_task(vm.refresh_models)。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_refresh_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_called_once_with(vm.refresh_models)

    def test_on_provider_change_factory_calls_run_task_with_provider_id(self) -> None:
        """_on_provider_change_factory: page 可用 → page.run_task(vm.update_provider, provider_id)。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_provider_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event("qwen"))
        mock_page.run_task.assert_called_once_with(vm.update_provider, "qwen")


class TestFactoryFunctionsPageNone:
    """4 个 factory 函数: page=None 时早返回 (不调 run_task, 不抛异常)。"""

    def test_on_test_click_factory_page_none_skips_run_task(self) -> None:
        """_on_test_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_on_save_click_factory_page_none_skips_run_task(self) -> None:
        """_on_save_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_on_refresh_click_factory_page_none_skips_run_task(self) -> None:
        """_on_refresh_click_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_refresh_click_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event())
        mock_page.run_task.assert_not_called()

    def test_on_provider_change_factory_page_none_skips_run_task(self) -> None:
        """_on_provider_change_factory: page=None → 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_provider_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: None)
            _invoke(handler, _make_event("qwen"))
        mock_page.run_task.assert_not_called()


class TestFactoryFunctionsRuntimeError:
    """4 个 factory 函数: ft.context.page 抛 RuntimeError 时静默处理 (不抛异常)。

    RuntimeError 在 Flet 中表示 page 未挂载到 Renderer 上下文, factory 函数应捕获并静默。
    """

    def test_on_test_click_factory_runtime_error_swallowed(self) -> None:
        """_on_test_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_test_click_factory(vm)
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            # 不应抛异常
            _invoke(handler, _make_event())

    def test_on_save_click_factory_runtime_error_swallowed(self) -> None:
        """_on_save_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_save_click_factory(vm)
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())

    def test_on_refresh_click_factory_runtime_error_swallowed(self) -> None:
        """_on_refresh_click_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_refresh_click_factory(vm)
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event())

    def test_on_provider_change_factory_runtime_error_swallowed(self) -> None:
        """_on_provider_change_factory: RuntimeError 静默处理。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_provider_change_factory(vm)
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            _invoke(handler, _make_event("qwen"))


class TestOnProviderChangeFactoryEdgeCases:
    """_on_provider_change_factory 边界: provider_id=None/空 早返回。"""

    def test_provider_id_none_returns_early(self) -> None:
        """provider_id=None → 早返回, 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_provider_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event(None))
        mock_page.run_task.assert_not_called()

    def test_provider_id_empty_string_returns_early(self) -> None:
        """provider_id='' → 早返回, 不调 run_task。"""
        vm = MagicMock(spec=LLMConfigPanelViewModel)
        handler = _on_provider_change_factory(vm)
        mock_page = MagicMock()
        with patch("ui.components.config_panels.llm_config_panel.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            _invoke(handler, _make_event(""))
        mock_page.run_task.assert_not_called()


# ============================================================================
# 组件运行时测试基础设施: _FakeLLMConfigPanelVM + _render_panel helper
# ============================================================================


class _FakeLLMConfigPanelVM:
    """模拟 LLMConfigPanelViewModel, 满足 use_viewmodel(vm=) 外部 VM 模式契约。

    state 字段可外部注入, command 方法为 MagicMock 便于断言。
    """

    def __init__(self, state: LLMConfigState | None = None) -> None:
        self._state = state if state is not None else LLMConfigState()
        self._subscribers: list[Any] = []
        # command 方法 (MagicMock, 便于断言调用)
        self.verify_connection = MagicMock()
        self.save_config = MagicMock()
        self.refresh_models = MagicMock()
        self.update_provider = MagicMock()
        self.update_model = MagicMock()
        self.update_custom_model = MagicMock()
        self.update_base_url = MagicMock()
        self.update_api_key = MagicMock()
        self.update_azure_resource = MagicMock()
        self.update_azure_deployment = MagicMock()
        self.update_azure_version = MagicMock()

    @property
    def state(self) -> LLMConfigState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _render_panel(
    state: LLMConfigState | None = None,
    *,
    show_save_button: bool = True,
    compact: bool = False,
    show_register_link: bool = True,
    page: FakePage | None = None,
) -> tuple[_FakeLLMConfigPanelVM, FakePage, Any, Any]:
    """渲染 LLMConfigPanel, 返回 (vm, page, result, component)。

    Mock 外部依赖:
    - I18n (模块级导入, get 返回 key 或 default, current_locale 返回 zh_CN)
    - AppColors / AppStyles (颜色 / 样式 token)
    - SectionHeader (子组件, 替换为 MagicMock 避免依赖 settings_widgets)
    """
    vm = _FakeLLMConfigPanelVM(state=state)
    if page is None:
        page = FakePage()
    page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(panel_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else key
        mock_i18n.current_locale.return_value = "zh_CN"
        stack.enter_context(patch.object(panel_module, "AppColors"))
        mock_styles = stack.enter_context(patch.object(panel_module, "AppStyles"))
        mock_styles.primary_button.return_value = ft.ButtonStyle()
        mock_styles.secondary_button.return_value = ft.ButtonStyle()
        stack.enter_context(patch.object(panel_module, "SectionHeader", side_effect=lambda *a, **kw: ft.Container()))

        component = make_component(
            LLMConfigPanel,
            vm=vm,
            show_save_button=show_save_button,
            compact=compact,
            show_register_link=show_register_link,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


# ============================================================================
# 组件运行时测试: 布局 / 可见性 / disabled 状态
# ============================================================================


class TestLLMConfigPanelLayout:
    """LLMConfigPanel 布局测试 (compact / show_save_button / show_register_link)。"""

    def test_returns_column_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非 compact 模式返回 ft.Column。"""
        _, _, result, _ = _render_panel(compact=False)
        assert isinstance(result, ft.Column)

    def test_returns_container_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式返回 ft.Container。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)

    def test_compact_container_has_width(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式 Container 宽度 = input_width(360) + 60 = 420。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        assert result.width == 420

    def test_compact_container_alignment_center(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式 Container.alignment = ft.Alignment.CENTER。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        assert result.alignment == ft.Alignment.CENTER

    def test_section_header_visible_when_not_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """非 compact 模式 SectionHeader.visible=True。"""
        _, _, result, _ = _render_panel(compact=False)
        # SectionHeader 被 mock 为 ft.Container, 是 form_content.controls[0]
        section_header = result.controls[0]
        assert section_header.visible is True

    def test_section_header_hidden_when_compact(self, mock_i18n_state, mock_app_colors_state) -> None:
        """compact 模式 SectionHeader.visible=False。"""
        _, _, result, _ = _render_panel(compact=True)
        assert isinstance(result, ft.Container)
        form_content = result.content
        assert isinstance(form_content, ft.Column)
        section_header = form_content.controls[0]
        assert section_header.visible is False

    def test_save_button_visible_when_show_save_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=True 时保存按钮可见。"""
        _, _, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is True

    def test_save_button_hidden_when_show_save_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_save_button=False 时保存按钮不可见。"""
        _, _, result, _ = _render_panel(show_save_button=False)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].visible is False

    def test_links_row_visible_when_show_register_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_register_link=True 时 links_row 可见。"""
        _, _, result, _ = _render_panel(show_register_link=True)
        ctrls = _walk_controls(result)
        rows = [c for c in ctrls if isinstance(c, ft.Row)]
        # 找含 TextButton 的 Row (links_row)
        links_rows = [r for r in rows if any(isinstance(c, ft.TextButton) for c in r.controls)]
        assert len(links_rows) >= 1
        assert links_rows[0].visible is True

    def test_links_row_hidden_when_show_register_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_register_link=False 时 links_row 不可见。"""
        _, _, result, _ = _render_panel(show_register_link=False)
        ctrls = _walk_controls(result)
        rows = [c for c in ctrls if isinstance(c, ft.Row)]
        links_rows = [r for r in rows if any(isinstance(c, ft.TextButton) for c in r.controls)]
        assert len(links_rows) >= 1
        assert links_rows[0].visible is False


class TestLLMConfigPanelAzureFields:
    """LLMConfigPanel Azure 字段可见性测试 (is_azure)。"""

    def test_azure_fields_visible_when_is_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=True 时 azure 字段可见 (azure_resource/azure_deployment/azure_version)。"""
        state = LLMConfigState(is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        azure_resource = _find_text_field(result, "llm_azure_resource_name")
        azure_deployment = _find_text_field(result, "llm_azure_deployment_name")
        azure_version = _find_dropdown(result, "llm_azure_api_version")
        assert azure_resource.visible is True
        assert azure_deployment.visible is True
        assert azure_version.visible is True

    def test_azure_fields_hidden_when_not_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=False 时 azure 字段不可见。"""
        state = LLMConfigState(is_azure=False, provider="deepseek")
        _, _, result, _ = _render_panel(state=state)
        azure_resource = _find_text_field(result, "llm_azure_resource_name")
        azure_deployment = _find_text_field(result, "llm_azure_deployment_name")
        azure_version = _find_dropdown(result, "llm_azure_api_version")
        assert azure_resource.visible is False
        assert azure_deployment.visible is False
        assert azure_version.visible is False

    def test_azure_row_visible_when_is_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=True 时 azure_row (Column) visible=True。"""
        state = LLMConfigState(is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        # azure_row 是 form_content 中的 Column, visible=is_azure
        # 遍历查找含 azure_resource_input 的 Column
        ctrls = _walk_controls(result)
        # 找父级 Column (azure_row)
        for ctrl in ctrls:
            if isinstance(ctrl, ft.Column):
                if any(
                    isinstance(c, ft.TextField) and getattr(c, "label", None) == "llm_azure_resource_name"
                    for c in ctrl.controls
                ):
                    assert ctrl.visible is True
                    return
        raise AssertionError("azure_row Column not found")


class TestLLMConfigPanelCustomModelInput:
    """LLMConfigPanel custom_model_input 与 base_url_read_only 测试。"""

    def test_custom_model_input_visible_when_show_custom_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_custom_model_input=True 且 is_azure=False 时 custom_model_input 可见。"""
        state = LLMConfigState(show_custom_model_input=True, is_azure=False, provider="custom")
        _, _, result, _ = _render_panel(state=state)
        custom_field = _find_dropdown(result, "llm_custom_model")
        assert custom_field.visible is True

    def test_custom_model_input_hidden_when_show_custom_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_custom_model_input=False 时 custom_model_input 不可见。"""
        state = LLMConfigState(show_custom_model_input=False, provider="deepseek")
        _, _, result, _ = _render_panel(state=state)
        custom_field = _find_dropdown(result, "llm_custom_model")
        assert custom_field.visible is False

    def test_custom_model_input_hidden_when_is_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=True 时 custom_model_input 不可见 (即使 show_custom_model_input=True)。"""
        state = LLMConfigState(show_custom_model_input=True, is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        custom_field = _find_dropdown(result, "llm_custom_model")
        assert custom_field.visible is False

    def test_base_url_read_only_when_flag_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """base_url_read_only=True 时 base_url_input.read_only=True。"""
        state = LLMConfigState(base_url_read_only=True, provider="deepseek")
        _, _, result, _ = _render_panel(state=state)
        base_url = _find_text_field(result, "llm_base_url")
        assert base_url.read_only is True

    def test_base_url_editable_when_flag_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """base_url_read_only=False 时 base_url_input.read_only=False。"""
        state = LLMConfigState(base_url_read_only=False, provider="custom")
        _, _, result, _ = _render_panel(state=state)
        base_url = _find_text_field(result, "llm_base_url")
        assert base_url.read_only is False

    def test_base_url_hidden_when_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=True 时 base_url_input 不可见。"""
        state = LLMConfigState(is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        base_url = _find_text_field(result, "llm_base_url")
        assert base_url.visible is False


class TestLLMConfigPanelModelDropdown:
    """LLMConfigPanel model_dropdown 可见性与 options 测试。"""

    def test_model_dropdown_visible_when_not_azure_and_not_custom(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=False 且 show_custom_model_input=False 时 model_dropdown 可见。"""
        state = LLMConfigState(is_azure=False, show_custom_model_input=False, provider="deepseek")
        _, _, result, _ = _render_panel(state=state)
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.visible is True

    def test_model_dropdown_hidden_when_azure(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_azure=True 时 model_dropdown 不可见。"""
        state = LLMConfigState(is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.visible is False

    def test_model_dropdown_hidden_when_custom_input_shown(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_custom_model_input=True 时 model_dropdown 不可见。"""
        state = LLMConfigState(show_custom_model_input=True, provider="custom")
        _, _, result, _ = _render_panel(state=state)
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.visible is False

    def test_model_dropdown_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """model_dropdown.value 绑定到 state.model。"""
        state = LLMConfigState(provider="deepseek", model="deepseek-v4-flash")
        _, _, result, _ = _render_panel(state=state)
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.value == "deepseek-v4-flash"

    def test_provider_dropdown_value_bound_to_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider_dropdown.value 绑定到 state.provider。"""
        state = LLMConfigState(provider="qwen")
        _, _, result, _ = _render_panel(state=state)
        provider_dd = _find_dropdown(result, "llm_select_provider")
        assert provider_dd.value == "qwen"


# ============================================================================
# provider 切换时 model_options 重算测试
# ============================================================================


class TestProviderSwitchModelOptionsRecompute:
    """provider 切换时 model_options 重算测试。

    验证 _build_model_options(state.provider) 在不同 provider 下产出不同 options。
    """

    def test_model_dropdown_options_for_deepseek(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider=deepseek 时 model_dropdown.options 含 deepseek 模型。"""
        _, _, result, _ = _render_panel(state=LLMConfigState(provider="deepseek"))
        model_dd = _find_dropdown(result, "llm_select_model")
        keys = {o.key for o in model_dd.options}
        assert "deepseek-v4-pro" in keys
        assert "deepseek-v4-flash" in keys

    def test_model_dropdown_options_for_qwen(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider=qwen 时 model_dropdown.options 含 qwen 模型。"""
        _, _, result, _ = _render_panel(state=LLMConfigState(provider="qwen"))
        model_dd = _find_dropdown(result, "llm_select_model")
        keys = {o.key for o in model_dd.options}
        assert "qwen3.6-max-preview" in keys
        assert "qwen3.6-plus" in keys

    def test_model_dropdown_options_for_openai(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider=openai 时 model_dropdown.options 含 openai 模型。"""
        _, _, result, _ = _render_panel(state=LLMConfigState(provider="openai"))
        model_dd = _find_dropdown(result, "llm_select_model")
        keys = {o.key for o in model_dd.options}
        assert "gpt-5.5" in keys
        assert "gpt-5.4" in keys

    def test_model_dropdown_options_for_azure_empty(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider=azure 时 model_dropdown.options 为空 (azure models 列表为空)。"""
        _, _, result, _ = _render_panel(state=LLMConfigState(provider="azure"))
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.options == []

    def test_model_dropdown_options_for_custom_empty(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider=custom 时 model_dropdown.options 为空。"""
        _, _, result, _ = _render_panel(state=LLMConfigState(provider="custom"))
        model_dd = _find_dropdown(result, "llm_select_model")
        assert model_dd.options == []

    def test_custom_model_options_populated_from_state(self, mock_i18n_state, mock_app_colors_state) -> None:
        """custom_model_options 从 state 注入到 custom_model_input.options。"""
        state = LLMConfigState(
            show_custom_model_input=True,
            provider="custom",
            custom_model_options=("model-a", "model-b"),
        )
        _, _, result, _ = _render_panel(state=state)
        custom_field = _find_dropdown(result, "llm_custom_model")
        keys = {o.key for o in custom_field.options}
        assert keys == {"model-a", "model-b"}

    def test_azure_version_options_populated_from_constant(self, mock_i18n_state, mock_app_colors_state) -> None:
        """azure_version dropdown.options 从 AZURE_API_VERSIONS 常量填充。"""
        from utils.llm_providers import AZURE_API_VERSIONS

        state = LLMConfigState(is_azure=True, provider="azure")
        _, _, result, _ = _render_panel(state=state)
        azure_version_dd = _find_dropdown(result, "llm_azure_api_version")
        keys = {o.key for o in azure_version_dd.options}
        assert set(AZURE_API_VERSIONS) == keys

    def test_azure_version_value_falls_back_to_default_when_empty(self, mock_i18n_state, mock_app_colors_state) -> None:
        """azure_api_version 为空时 dropdown.value 回退到 AZURE_DEFAULT_API_VERSION。"""
        from utils.llm_providers import AZURE_DEFAULT_API_VERSION

        state = LLMConfigState(is_azure=True, provider="azure", azure_api_version="")
        _, _, result, _ = _render_panel(state=state)
        azure_version_dd = _find_dropdown(result, "llm_azure_api_version")
        assert azure_version_dd.value == AZURE_DEFAULT_API_VERSION


# ============================================================================
# 组件事件处理器测试: 触发 on_click/on_select/on_change 验证 VM 调用
# ============================================================================


class TestLLMConfigPanelEventHandlers:
    """LLMConfigPanel 事件处理器测试 (page 可用 → page.run_task)。"""

    def test_save_click_calls_page_run_task_with_vm_save_config(self, mock_i18n_state, mock_app_colors_state) -> None:
        """save button on_click → page.run_task(vm.save_config)。"""
        vm, page, result, _ = _render_panel(show_save_button=True)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(save_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.save_config)

    def test_test_click_calls_page_run_task_with_vm_verify_connection(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """test button on_click → page.run_task(vm.verify_connection)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.CABLE]
        assert len(test_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(test_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.verify_connection)

    def test_refresh_click_calls_page_run_task_with_vm_refresh_models(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """refresh button on_click → page.run_task(vm.refresh_models)。"""
        vm, page, result, _ = _render_panel()
        ctrls = _walk_controls(result)
        refresh_btns = [
            c for c in ctrls if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.REFRESH
        ]
        assert len(refresh_btns) == 1

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(refresh_btns[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.refresh_models)

    def test_provider_change_calls_page_run_task_with_vm_update_provider(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """provider dropdown on_select → page.run_task(vm.update_provider, provider_id)。"""
        vm, page, result, _ = _render_panel()
        provider_dd = _find_dropdown(result, "llm_select_provider")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(provider_dd.on_select, _make_event("qwen"))
        run_task.assert_called_once_with(vm.update_provider, "qwen")

    def test_provider_change_empty_value_skips_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider dropdown on_select value=None → 不调 run_task。"""
        _, page, result, _ = _render_panel()
        provider_dd = _find_dropdown(result, "llm_select_provider")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(provider_dd.on_select, _make_event(None))
        run_task.assert_not_called()

    def test_provider_change_empty_string_skips_run_task(self, mock_i18n_state, mock_app_colors_state) -> None:
        """provider dropdown on_select value='' → 不调 run_task。"""
        _, page, result, _ = _render_panel()
        provider_dd = _find_dropdown(result, "llm_select_provider")

        run_task = _page_run_task(page)
        run_task.reset_mock()
        _invoke(provider_dd.on_select, _make_event(""))
        run_task.assert_not_called()


class TestLLMConfigPanelSyncCommands:
    """LLMConfigPanel 同步命令测试 (on_change/on_select 直接调 vm 方法, 非 run_task)。

    model_dropdown / custom_model_input / base_url_input / api_key_input / azure_*
    的 on_change/on_select 是 lambda, 直接调用 vm.update_* 方法 (同步命令)。
    """

    def test_model_change_calls_vm_update_model(self, mock_i18n_state, mock_app_colors_state) -> None:
        """model dropdown on_select → vm.update_model(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(provider="deepseek"))
        model_dd = _find_dropdown(result, "llm_select_model")

        _invoke(model_dd.on_select, _make_event("deepseek-v4-pro"))
        vm.update_model.assert_called_once_with("deepseek-v4-pro")

    def test_model_change_empty_value_skips_update(self, mock_i18n_state, mock_app_colors_state) -> None:
        """model dropdown on_select value=None → 不调 vm.update_model。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(provider="deepseek"))
        model_dd = _find_dropdown(result, "llm_select_model")

        _invoke(model_dd.on_select, _make_event(None))
        vm.update_model.assert_not_called()

    def test_custom_model_change_calls_vm_update_custom_model(self, mock_i18n_state, mock_app_colors_state) -> None:
        """custom_model dropdown on_select → vm.update_custom_model(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(show_custom_model_input=True, provider="custom"))
        custom_field = _find_dropdown(result, "llm_custom_model")

        _invoke(custom_field.on_select, _make_event("custom-model-1"))
        vm.update_custom_model.assert_called_once_with("custom-model-1")

    def test_custom_model_change_empty_value_skips_update(self, mock_i18n_state, mock_app_colors_state) -> None:
        """custom_model dropdown on_select value=None → 不调 vm.update_custom_model。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(show_custom_model_input=True, provider="custom"))
        custom_field = _find_dropdown(result, "llm_custom_model")

        _invoke(custom_field.on_select, _make_event(None))
        vm.update_custom_model.assert_not_called()

    def test_api_key_change_calls_vm_update_api_key(self, mock_i18n_state, mock_app_colors_state) -> None:
        """api_key input on_change → vm.update_api_key(value)。"""
        vm, _, result, _ = _render_panel()
        api_key_field = _find_text_field(result, "llm_api_key")

        _invoke(api_key_field.on_change, _make_event("sk-new-key"))
        vm.update_api_key.assert_called_once_with("sk-new-key")

    def test_base_url_change_calls_vm_update_base_url(self, mock_i18n_state, mock_app_colors_state) -> None:
        """base_url input on_change → vm.update_base_url(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(provider="deepseek"))
        base_url_field = _find_text_field(result, "llm_base_url")

        _invoke(base_url_field.on_change, _make_event("https://api.new.com"))
        vm.update_base_url.assert_called_once_with("https://api.new.com")

    def test_azure_resource_change_calls_vm_update_azure_resource(self, mock_i18n_state, mock_app_colors_state) -> None:
        """azure_resource input on_change → vm.update_azure_resource(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(is_azure=True, provider="azure"))
        azure_resource_field = _find_text_field(result, "llm_azure_resource_name")

        _invoke(azure_resource_field.on_change, _make_event("my-resource"))
        vm.update_azure_resource.assert_called_once_with("my-resource")

    def test_azure_deployment_change_calls_vm_update_azure_deployment(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """azure_deployment input on_change → vm.update_azure_deployment(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(is_azure=True, provider="azure"))
        azure_deployment_field = _find_text_field(result, "llm_azure_deployment_name")

        _invoke(azure_deployment_field.on_change, _make_event("my-deployment"))
        vm.update_azure_deployment.assert_called_once_with("my-deployment")

    def test_azure_version_change_calls_vm_update_azure_version(self, mock_i18n_state, mock_app_colors_state) -> None:
        """azure_version dropdown on_select → vm.update_azure_version(value)。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(is_azure=True, provider="azure"))
        azure_version_dd = _find_dropdown(result, "llm_azure_api_version")

        _invoke(azure_version_dd.on_select, _make_event("2024-10-21"))
        vm.update_azure_version.assert_called_once_with("2024-10-21")

    def test_azure_version_change_empty_value_skips_update(self, mock_i18n_state, mock_app_colors_state) -> None:
        """azure_version dropdown on_select value=None → 不调 vm.update_azure_version。"""
        vm, _, result, _ = _render_panel(state=LLMConfigState(is_azure=True, provider="azure"))
        azure_version_dd = _find_dropdown(result, "llm_azure_api_version")

        _invoke(azure_version_dd.on_select, _make_event(None))
        vm.update_azure_version.assert_not_called()


# ============================================================================
# 状态显示测试: status_message / status_type / disabled 状态
# ============================================================================


class TestLLMConfigPanelStatusDisplay:
    """LLMConfigPanel 状态显示测试 (status icon/text + disabled 状态)。"""

    def test_status_icon_visible_when_status_message_present(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message 非空时 status_icon 可见。"""
        state = LLMConfigState(
            status_message=Message("llm_test_success"),
            status_type="success",
        )
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1
        assert icons[0].visible is True

    def test_status_icon_hidden_when_status_message_none(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_message=None 时 status_icon 不可见。"""
        state = LLMConfigState(status_message=None, status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1
        assert icons[0].visible is False

    def test_status_icon_success_uses_check_circle(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=success → CHECK_CIRCLE icon。"""
        state = LLMConfigState(status_message=Message("ok"), status_type="success")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.CHECK_CIRCLE]
        assert len(icons) == 1

    def test_status_icon_error_uses_error_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=error → ERROR icon。"""
        state = LLMConfigState(status_message=Message("err"), status_type="error")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR]
        assert len(icons) == 1

    def test_status_icon_warning_uses_warning_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=warning → WARNING icon。"""
        state = LLMConfigState(status_message=Message("warn"), status_type="warning")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.WARNING]
        assert len(icons) == 1

    def test_status_icon_info_uses_info_icon(self, mock_i18n_state, mock_app_colors_state) -> None:
        """status_type=info → INFO icon。"""
        state = LLMConfigState(status_message=Message("info"), status_type="info")
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        icons = [c for c in ctrls if isinstance(c, ft.Icon) and c.icon == ft.Icons.INFO]
        assert len(icons) == 1

    def test_test_button_disabled_when_verifying(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_verifying=True 时 test button disabled。"""
        state = LLMConfigState(is_verifying=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        test_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.CABLE]
        assert len(test_btns) == 1
        assert test_btns[0].disabled is True

    def test_save_button_disabled_when_saving(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_saving=True 时 save button disabled。"""
        state = LLMConfigState(is_saving=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        save_btns = [c for c in ctrls if isinstance(c, ft.Button) and getattr(c, "icon", None) == ft.Icons.SAVE]
        assert len(save_btns) == 1
        assert save_btns[0].disabled is True

    def test_refresh_button_disabled_when_refreshing(self, mock_i18n_state, mock_app_colors_state) -> None:
        """is_refreshing=True 时 refresh button disabled。"""
        state = LLMConfigState(is_refreshing=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        refresh_btns = [
            c for c in ctrls if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.REFRESH
        ]
        assert len(refresh_btns) == 1
        assert refresh_btns[0].disabled is True

    def test_refresh_button_hidden_when_show_refresh_false(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_refresh_button=False 时 refresh button 不可见。"""
        state = LLMConfigState(show_refresh_button=False)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        refresh_btns = [
            c for c in ctrls if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.REFRESH
        ]
        assert len(refresh_btns) == 1
        assert refresh_btns[0].visible is False

    def test_refresh_button_visible_when_show_refresh_true(self, mock_i18n_state, mock_app_colors_state) -> None:
        """show_refresh_button=True 时 refresh button 可见。"""
        state = LLMConfigState(show_refresh_button=True)
        _, _, result, _ = _render_panel(state=state)
        ctrls = _walk_controls(result)
        refresh_btns = [
            c for c in ctrls if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.REFRESH
        ]
        assert len(refresh_btns) == 1
        assert refresh_btns[0].visible is True


# ============================================================================
# 组件挂载/卸载 + VM 订阅生命周期
# ============================================================================


class TestLLMConfigPanelVMSifecycle:
    """LLMConfigPanel VM 订阅生命周期测试 (use_viewmodel 外部 VM 模式)。"""

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """挂载后 use_viewmodel 注册 subscribe 到 VM。"""
        vm, _, _, _ = _render_panel()
        # use_viewmodel 外部 VM 模式应注册 subscribe
        assert len(vm._subscribers) > 0

    def test_unmount_unsubscribes_from_vm(self, mock_i18n_state, mock_app_colors_state) -> None:
        """卸载后退订 VM (use_viewmodel cleanup 调用 unsub)。"""
        from tests.unit.ui.component_renderer import run_unmount_effects

        vm, _, _, component = _render_panel()
        assert len(vm._subscribers) > 0
        run_unmount_effects(component)
        assert len(vm._subscribers) == 0

    def test_external_vm_not_disposed_on_unmount(self, mock_i18n_state, mock_app_colors_state) -> None:
        """外部 VM 模式: 卸载不调 vm.dispose() (生命周期由消费方管理)。"""
        from tests.unit.ui.component_renderer import run_unmount_effects

        vm, _, _, component = _render_panel()
        # 用 spy 检测 dispose 调用
        original_dispose = vm.dispose
        dispose_called: list[bool] = []

        def _spy_dispose() -> None:
            dispose_called.append(True)
            original_dispose()

        vm.dispose = _spy_dispose  # type: ignore[method-assign]
        run_unmount_effects(component)
        # 外部 VM 模式不调 dispose
        assert dispose_called == []


# ============================================================================
# 测试隔离守卫 (R7: 单例未污染)
# ============================================================================


class TestLLMConfigPanelIsolation:
    """R7 守卫: 测试间无单例状态污染 (由 conftest _reset_all_singletons autouse 保证)。"""

    def test_no_singleton_state_leakage_between_tests(self, mock_i18n_state, mock_app_colors_state) -> None:
        """连续渲染两个 panel, 第二个不受第一个影响 (VM 独立)。"""
        vm1, _, result1, _ = _render_panel(state=LLMConfigState(provider="deepseek"))
        vm2, _, result2, _ = _render_panel(state=LLMConfigState(provider="qwen"))

        # 两个 VM 应是独立实例
        assert vm1 is not vm2
        # 两个 result 应反映各自 state
        provider_dd1 = _find_dropdown(result1, "llm_select_provider")
        provider_dd2 = _find_dropdown(result2, "llm_select_provider")
        assert provider_dd1.value == "deepseek"
        assert provider_dd2.value == "qwen"
