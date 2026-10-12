"""NewsSentimentConfigPanel 声明式契约守护测试 (N5-4).

面板为声明式组件（含 use_state/use_ref，无 renderer 下无法实例化渲染），
与 test_ai_brain_tab_contract.py 同范式：源码扫描契约 + 常量断言。

覆盖：
1. @ft.component 声明式范式（禁止命令式生命周期/手动刷新）
2. FilePicker 生命周期（use_ref 持久化 + use_effect 注册 page.services + cleanup 移除）
3. 三类产物扩展名（gguf/npz/json）经 _FILE_TARGETS 常量断言
4. 外部 VM 模式（use_viewmodel(vm=...)，消费方持有生命周期）
5. i18n/theme 订阅自动重渲染
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _panel_source() -> str:
    import ui.components.config_panels.news_sentiment_config_panel as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


class TestNewsSentimentConfigPanelContract:
    """NewsSentimentConfigPanel 声明式契约守护（N5-4）。"""

    def test_is_ft_component(self):
        """DoD: 必须用 @ft.component 装饰。"""
        assert "@ft.component" in _panel_source(), "面板必须用 @ft.component 装饰"
        assert "class NewsSentimentConfigPanel(" not in _panel_source(), "面板不应是 class (命令式)"

    def test_no_imperative_lifecycle(self):
        """DoD: 禁止命令式生命周期回调与手动刷新。"""
        source = _panel_source()
        for banned in ("did_mount", "will_unmount", ".update()", "PageRefMixin", "_page_ref"):
            assert banned not in source, f"不应使用 {banned} (命令式)"

    def test_file_picker_lifecycle(self):
        """DoD: FilePicker 经 use_ref 持久化 + use_effect 注册 page.services + cleanup 移除。"""
        source = _panel_source()
        assert "ft.use_ref(lambda: ft.FilePicker())" in source, "FilePicker 须 use_ref 持久化"
        assert "page.services.append(file_picker)" in source, "须注册到 page.services"
        assert "page.services.remove(file_picker)" in source, "cleanup 须从 page.services 移除"
        assert "ft.use_effect(" in source and "cleanup=" in source, "须 use_effect + cleanup"

    def test_file_targets_extensions(self):
        """DoD: 三类产物扩展名覆盖 gguf/npz/json（N5-4 文件选择器）。"""
        from ui.components.config_panels.news_sentiment_config_panel import _FILE_TARGETS

        extensions = {target[2] for target in _FILE_TARGETS}
        assert extensions == {"gguf", "npz", "json"}
        setters = {target[1] for target in _FILE_TARGETS}
        assert setters == {
            "set_news_sentiment_model_path",
            "set_news_sentiment_head_path",
            "set_news_sentiment_tokenizer_path",
        }

    def test_external_vm_mode(self):
        """DoD: 外部 VM 模式 use_viewmodel(vm=...)，VM 生命周期由消费方管理。"""
        source = _panel_source()
        assert "use_viewmodel(vm=vm)" in source, "须外部 VM 模式订阅"
        assert ".dispose()" not in source, "外部模式不调用 dispose（消费方持有）"
        assert "dispose_on_unmount" not in source, "外部模式不传 dispose_on_unmount（消费方持有）"

    def test_subscribes_i18n_and_theme(self):
        """DoD: 订阅 i18n 与 theme observable（热切换自动重渲染）。"""
        source = _panel_source()
        assert "get_observable_state" in source, "须订阅 i18n observable"
        assert "AppColors.get_observable_state" in source, "须订阅 theme observable"

    def test_no_business_validation_in_view(self):
        """DoD: View 不做业务校验（GGUF/head/tokenizer 校验在 VM.save_ai_settings）。"""
        source = _panel_source()
        assert "_validate_model_file" not in source, "View 层不得引入模型校验业务（VM 职责）"
        assert "load_head_npz" not in source, "View 层不得引入 head 校验业务（VM 职责）"

    def test_no_bare_ui_tokens(self):
        """DoD: R23——颜色/字号必须经 AppColors/AppStyles token。"""
        source = _panel_source()
        assert "ft.Colors." not in source, "裸 ft.Colors 色值违反 R23"
