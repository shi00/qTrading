"""LocalModelConfigPanel._on_locale_change 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel

pytestmark = pytest.mark.unit


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config_handler():
    with patch("ui.components.config_panels.local_model_config_panel.ConfigHandler") as m:
        m.get_local_ai_config.return_value = {
            "local_model_path": "",
            "n_threads": 4,
            "n_gpu_layers": -1,
            "n_batch": 512,
            "n_ctx": 4096,
            "flash_attn": True,
        }
        m.get_local_ai_timeout.return_value = 300
        yield m


@pytest.fixture
def mock_i18n():
    with patch("ui.components.config_panels.local_model_config_panel.I18n") as m:
        m.get.side_effect = lambda key, default="", **kw: default or key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.config_panels.local_model_config_panel.AppColors") as m:
        m.SUCCESS = "#4caf50"
        m.WARNING = "#ff9800"
        m.ERROR = "#f44336"
        m.PRIMARY = "#1976d2"
        m.TEXT_SECONDARY = "#999"
        yield m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.config_panels.local_model_config_panel.AppStyles") as m:
        m.primary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        m.secondary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        yield m


@pytest.fixture
def mock_section_header():
    with patch("ui.components.config_panels.local_model_config_panel.SectionHeader") as m:
        # 使用 plain MagicMock 以支持 update_locale() 自定义方法
        m.return_value = MagicMock()
        yield m


@pytest.fixture
def mock_page():
    page = MagicMock(spec=ft.Page)
    page.overlay = []
    page.update = MagicMock(spec=[])
    return page


def _make_panel(
    mock_config_handler,
    mock_i18n,
    mock_app_colors,
    mock_app_styles,
    mock_section_header,
    mock_page,
    **kwargs,
):
    """创建 LocalModelConfigPanel 实例并绑定 mock page"""
    kwargs.setdefault("on_verify_model", AsyncMock(return_value=True))
    panel = LocalModelConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


# ════════════════════════════════════════════════════════════════════════════
# TestLocalModelConfigPanelLocaleChange
# ════════════════════════════════════════════════════════════════════════════


class TestLocalModelConfigPanelLocaleChange:
    """测试 _on_locale_change 方法（直接更新控件 i18n 文本，不重建控件）"""

    def test_on_locale_change_does_not_call_build_ui(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_locale_change 不应调用 _build_ui（避免触发 ConfigHandler.get_local_ai_config 等 IO）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        initial_cfg_count = mock_config_handler.get_local_ai_config.call_count
        initial_timeout_count = mock_config_handler.get_local_ai_timeout.call_count
        with (
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_build_ui") as mock_build_ui,
        ):
            panel._on_locale_change()
        mock_build_ui.assert_not_called()
        # 不应再调用 ConfigHandler（即不触发 IO）
        assert mock_config_handler.get_local_ai_config.call_count == initial_cfg_count
        assert mock_config_handler.get_local_ai_timeout.call_count == initial_timeout_count

    def test_on_locale_change_updates_text_labels(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 7 个控件（model_path_input、btn_select_file、timeout_input、gpu_auto_switch、batch_input、ctx_input、flash_attn_switch）的 label/text 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.model_path_input.label == "settings_local_model_path"
        assert panel.btn_select_file.content == "settings_btn_select_file"
        assert panel.timeout_input.label == "settings_local_ai_timeout"
        assert panel.gpu_auto_switch.label == "settings_local_gpu_auto"
        assert panel.batch_input.label == "settings_local_batch"
        assert panel.ctx_input.label == "settings_local_ctx"
        assert panel.flash_attn_switch.label == "settings_local_flash_attn"

    def test_on_locale_change_updates_buttons(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 verify_button、save_button 的 text 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.verify_button.content == "wizard_btn_verify_model"
        assert panel.save_button.content == "settings_save_config"

    def test_on_locale_change_preserves_input_values(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证控件当前 value 不变（关键：不重建控件）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 设置自定义 value
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "120"
        panel.batch_input.value = "1024"
        panel.ctx_input.value = "8192"
        panel.gpu_auto_switch.value = False
        panel.flash_attn_switch.value = False
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        # value 不应变
        assert panel.model_path_input.value == "/models/test.gguf"
        assert panel.timeout_input.value == "120"
        assert panel.batch_input.value == "1024"
        assert panel.ctx_input.value == "8192"
        assert panel.gpu_auto_switch.value is False
        assert panel.flash_attn_switch.value is False

    def test_on_locale_change_updates_header_text(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 _header_text.update_locale() 被调用"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        panel._header_text.update_locale.assert_called_once()

    def test_on_locale_change_updates_desc_and_advanced(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 _desc_text.value、_advanced_title.value、_advanced_subtitle.value 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel._desc_text.value == "settings_local_ai_desc"
        assert panel._advanced_title.value == "ai_advanced_settings"
        assert panel._advanced_subtitle.value == "settings_hint_restart"

    def test_on_locale_change_calls_safe_update(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证最后调用 _safe_update"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update") as mock_safe_update:
            panel._on_locale_change()
        mock_safe_update.assert_called_once()

    def test_on_locale_change_preserves_file_picker_instance(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 file_picker 实例不变（关键：不重建控件）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        original_file_picker = panel.file_picker
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.file_picker is original_file_picker
