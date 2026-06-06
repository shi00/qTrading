"""FailoverConfigPanel 和 ProviderCredentialDialog 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels.failover_config_panel import (
    FailoverConfigPanel,
    FailoverItem,
    ProviderCredentialDialog,
)


# ── 通用 Mock 数据 ──────────────────────────────────────────────────────────

MOCK_LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "tag": "推荐"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "tag": ""},
        ],
        "console_url": "https://platform.deepseek.com",
        "pricing_url": "https://platform.deepseek.com/pricing",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
        ],
        "console_url": "https://platform.openai.com",
    },
    "zhipu": {
        "name": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-4", "name": "GLM-4"},
        ],
    },
    "custom": {
        "name": "Custom",
        "base_url": "",
        "models": [],
    },
}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config_handler():
    with patch("ui.components.config_panels.failover_config_panel.ConfigHandler") as m:
        m.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        m.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        m.save_provider_credential.return_value = None
        m.save_config.return_value = None
        m.validate_failover_credentials.return_value = []
        yield m


@pytest.fixture
def mock_i18n():
    with patch("ui.components.config_panels.failover_config_panel.I18n") as m:
        m.get.side_effect = lambda key, default="", **kw: default or key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_llm_providers():
    with patch("ui.components.config_panels.failover_config_panel.LLM_PROVIDERS", MOCK_LLM_PROVIDERS):
        yield MOCK_LLM_PROVIDERS


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.config_panels.failover_config_panel.AppColors") as m:
        m.SUCCESS = "#4caf50"
        m.WARNING = "#ff9800"
        m.ERROR = "#f44336"
        m.PRIMARY = "#1976d2"
        m.TEXT_HINT = "#999"
        yield m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.config_panels.failover_config_panel.AppStyles") as m:
        m.primary_button.return_value = MagicMock()
        m.secondary_button.return_value = MagicMock()
        yield m


@pytest.fixture
def mock_section_header():
    with patch("ui.components.config_panels.failover_config_panel.SectionHeader") as m:
        m.return_value = MagicMock(spec=ft.Control)
        yield m


@pytest.fixture
def mock_page():
    page = MagicMock(spec=ft.Page)
    page.overlay = []
    page.open = MagicMock()
    page.close = MagicMock()
    page.launch_url = MagicMock()
    page.update = MagicMock()
    return page


def _make_panel(
    mock_config_handler,
    mock_i18n,
    mock_llm_providers,
    mock_app_colors,
    mock_app_styles,
    mock_section_header,
    mock_page,
    **kwargs,
):
    """创建 FailoverConfigPanel 实例并绑定 mock page"""
    panel = FailoverConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


def _make_dialog(
    mock_config_handler,
    mock_i18n,
    mock_llm_providers,
    mock_app_colors,
    mock_app_styles,
    mock_page,
    **kwargs,
):
    """创建 ProviderCredentialDialog 实例并绑定 mock page"""
    dialog = ProviderCredentialDialog(page=mock_page, **kwargs)
    return dialog


# ════════════════════════════════════════════════════════════════════════════
# 1. TestFailoverItem
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverItem:
    def test_to_config_string(self):
        item = FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True)
        assert item.to_config_string() == "deepseek/deepseek-chat"

    def test_to_config_string_with_slash_in_model(self):
        """模型名含 '/' 时，split('/', 1) 保证只分割第一段"""
        item = FailoverItem(provider="openai", model="gpt-4o/mini", display_name="OpenAI", has_credential=True)
        assert item.to_config_string() == "openai/gpt-4o/mini"


# ════════════════════════════════════════════════════════════════════════════
# 2. TestFailoverConfigPanelLoadAndRender
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelLoadAndRender:
    def test_load_empty_failover_list(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {"llm_failover_models": [], "llm_provider": "deepseek"}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert panel._failover_items == []

    def test_load_failover_list_with_credentials(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.side_effect = lambda p: (
            {"api_key": "test_token_deepseek_mock", "base_url": ""}
            if p == "deepseek"
            else {"api_key": "", "base_url": ""}
        )
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert len(panel._failover_items) == 2
        assert panel._failover_items[0].provider == "deepseek"
        assert panel._failover_items[0].has_credential is True
        assert panel._failover_items[0].api_key_masked == "test...mock"
        assert panel._failover_items[1].provider == "openai"
        assert panel._failover_items[1].has_credential is False

    def test_load_invalid_entry_skipped(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """不含 '/' 的条目被跳过"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["invalid_entry", "deepseek/deepseek-chat"],
            "llm_provider": "deepseek",
        }
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert len(panel._failover_items) == 1
        assert panel._failover_items[0].provider == "deepseek"

    def test_render_list_shows_empty_hint_when_no_items(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {"llm_failover_models": [], "llm_provider": "deepseek"}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 空列表时 _list_column 应有 1 个控件 (空提示)
        assert len(panel._list_column.controls) == 1

    def test_render_list_shows_items_when_present(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert len(panel._list_column.controls) == 2


# ════════════════════════════════════════════════════════════════════════════
# 3. TestFailoverConfigPanelAddEditDelete
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelAddEditDelete:
    def test_on_add_click_opens_dialog(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_add_click(MagicMock())
        mock_page.open.assert_called_once()
        dialog = mock_page.open.call_args[0][0]
        assert isinstance(dialog, ProviderCredentialDialog)

    def test_on_add_click_excludes_existing_providers(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """添加对话框排除已存在和主供应商"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "openai",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "test_token_mock", "base_url": ""}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_add_click(MagicMock())
        dialog = mock_page.open.call_args[0][0]
        # deepseek 和 openai 都在 existing_providers 中，zhipu 不在
        assert "deepseek" in dialog._existing_providers
        assert "openai" in dialog._existing_providers

    def test_on_edit_item_opens_dialog_with_edit_item(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "test_token_mock", "base_url": ""}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_edit_item(0)
        mock_page.open.assert_called_once()
        dialog = mock_page.open.call_args[0][0]
        assert isinstance(dialog, ProviderCredentialDialog)
        assert dialog._is_edit is True
        assert dialog._edit_item.provider == "deepseek"

    def test_on_delete_item_removes_and_persists(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert len(panel._failover_items) == 2
        # 删除第一个
        panel._on_delete_item(0)
        # 应保存移除后的列表
        mock_config_handler.save_config.assert_called_once()
        saved = mock_config_handler.save_config.call_args[0][0]
        assert "deepseek/deepseek-chat" not in saved["llm_failover_models"]

    def test_on_delete_item_entry_not_in_config_skips_save(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """如果条目不在配置列表中，删除时不应调用 save_config"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 手动修改 _failover_items 使其与配置不同步
        panel._failover_items.append(
            FailoverItem(provider="zhipu", model="glm-4", display_name="智谱", has_credential=False)
        )
        # 重置 save_config 调用计数
        mock_config_handler.save_config.reset_mock()
        # 删除不在配置中的 zhipu 条目 (index=1)
        panel._on_delete_item(1)
        # load_config 返回的列表中没有 zhipu/glm-4，所以不会调用 save_config
        mock_config_handler.save_config.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# 4. TestFailoverConfigPanelReorder
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelReorder:
    def _make_panel_with_items(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o", "zhipu/glm-4"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        return _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )

    def test_on_move_up_swaps_items(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = self._make_panel_with_items(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert panel._failover_items[0].provider == "deepseek"
        assert panel._failover_items[1].provider == "openai"
        panel._on_move_up(1)
        assert panel._failover_items[0].provider == "openai"
        assert panel._failover_items[1].provider == "deepseek"

    def test_on_move_down_swaps_items(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = self._make_panel_with_items(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        assert panel._failover_items[1].provider == "openai"
        assert panel._failover_items[2].provider == "zhipu"
        panel._on_move_down(1)
        assert panel._failover_items[1].provider == "zhipu"
        assert panel._failover_items[2].provider == "openai"

    def test_on_move_up_first_item_ignored(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = self._make_panel_with_items(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_move_up(0)
        # 顺序不变
        assert panel._failover_items[0].provider == "deepseek"

    def test_on_move_down_last_item_ignored(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = self._make_panel_with_items(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_move_down(2)
        # 顺序不变
        assert panel._failover_items[2].provider == "zhipu"

    def test_persist_order_saves_correct_sequence(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = self._make_panel_with_items(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        mock_config_handler.save_config.reset_mock()
        panel._persist_order()
        mock_config_handler.save_config.assert_called_once_with(
            {"llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o", "zhipu/glm-4"]}
        )


# ════════════════════════════════════════════════════════════════════════════
# 5. TestFailoverConfigPanelValidateAndSave
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelValidateAndSave:
    def test_on_validate_all_no_missing(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.validate_failover_credentials.return_value = []
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_validate_all(MagicMock())
        # 无缺失时显示成功 SnackBar
        assert len(mock_page.overlay) == 1

    def test_on_validate_all_missing_credentials(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        mock_config_handler.validate_failover_credentials.return_value = ["openai", "zhipu"]
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_validate_all(MagicMock())
        # 有缺失时显示警告 SnackBar
        assert len(mock_page.overlay) == 1

    def test_on_save_click_calls_callback(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        on_save = MagicMock()
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
            on_save=on_save,
        )
        panel._on_save_click(MagicMock())
        on_save.assert_called_once()

    def test_on_save_click_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_save_click(MagicMock())
        assert len(mock_page.overlay) == 1


# ════════════════════════════════════════════════════════════════════════════
# 6. TestFailoverConfigPanelLifecycle
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelLifecycle:
    def test_did_mount_subscribes_i18n(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.did_mount()
        mock_i18n.subscribe.assert_called_once_with(panel._on_locale_change)

    def test_will_unmount_unsubscribes_i18n(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.did_mount()
        panel.will_unmount()
        mock_i18n.unsubscribe.assert_called_once()

    def test_on_locale_change_rebuilds(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # _on_locale_change 会调用 _build_ui 和 _load_config
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change("zh_CN")
        # load_config 应被再次调用 (通过 _load_config)
        assert mock_config_handler.load_config.call_count >= 2

    def test_reload_config(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        initial_count = mock_config_handler.load_config.call_count
        panel.reload_config()
        assert mock_config_handler.load_config.call_count == initial_count + 1


# ════════════════════════════════════════════════════════════════════════════
# 7. TestProviderCredentialDialog
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialog:
    def test_build_ui_with_provider_options(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        # custom 被排除，剩余 deepseek / openai / zhipu
        assert dialog.provider_dropdown.options is not None
        provider_keys = [opt.key for opt in dialog.provider_dropdown.options]
        assert "deepseek" in provider_keys
        assert "openai" in provider_keys
        assert "zhipu" in provider_keys
        assert "custom" not in provider_keys

    def test_provider_dropdown_disabled_in_edit_mode(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        edit_item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        mock_config_handler.get_provider_credential.return_value = {"api_key": "test_token_mock", "base_url": ""}
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            edit_item=edit_item,
        )
        assert dialog.provider_dropdown.disabled is True

    def test_populate_edit_data(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        edit_item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_deepseek_mock",
            "base_url": "https://api.deepseek.com",
        }
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            edit_item=edit_item,
        )
        assert dialog.provider_dropdown.value == "deepseek"
        assert dialog.model_dropdown.value == "deepseek-chat"
        assert dialog.api_key_input.value == "test_token_deepseek_mock"
        assert dialog.base_url_input.value == "https://api.deepseek.com"

    def test_on_cancel_closes_dialog(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._on_cancel(MagicMock())
        mock_page.close.assert_called_once_with(dialog)

    def test_on_provider_change_updates_model_list(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        e = MagicMock()
        e.control.value = "deepseek"
        dialog._on_provider_change(e)
        assert dialog.model_dropdown.options is not None
        model_keys = [opt.key for opt in dialog.model_dropdown.options]
        assert "deepseek-chat" in model_keys
        assert "deepseek-reasoner" in model_keys

    def test_on_provider_change_updates_base_url(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        e = MagicMock()
        e.control.value = "deepseek"
        dialog._on_provider_change(e)
        assert dialog.base_url_input.value == "https://api.deepseek.com"

    def test_on_model_dropdown_change_clears_custom_input(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.custom_model_input.value = "my-custom-model"
        e = MagicMock()
        e.control.value = "deepseek-chat"
        dialog._on_model_dropdown_change(e)
        assert dialog.custom_model_input.value == ""

    def test_on_confirm_click_saves_credential(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """新增模式：保存凭证并添加到 failover 列表"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "openai"
        dialog.model_dropdown.value = "gpt-4o"
        dialog.api_key_input.value = "test_token_openai_key"
        dialog.base_url_input.value = "https://api.openai.com/v1"
        # load_config 返回当前 failover 列表
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_provider_credential.assert_called_once_with(
            provider="openai",
            api_key="test_token_openai_key",
            base_url="https://api.openai.com/v1",
            models=["gpt-4o"],
        )
        mock_config_handler.save_config.assert_called_once_with({"llm_failover_models": ["openai/gpt-4o"]})

    def test_on_confirm_click_missing_api_key_rejected(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """新增模式下 API Key 为空时拒绝"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "openai"
        dialog.model_dropdown.value = "gpt-4o"
        dialog.api_key_input.value = ""
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_provider_credential.assert_not_called()
        # 应显示警告 SnackBar
        assert len(mock_page.overlay) == 1

    def test_on_confirm_click_primary_provider_rejected(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """不允许添加与主供应商相同的 failover"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "test_token_mock"
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_provider_credential.assert_not_called()
        # 应显示警告 SnackBar
        assert len(mock_page.overlay) == 1

    def test_on_confirm_click_edit_mode_updates_entry(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """编辑模式：替换旧条目"""
        edit_item = FailoverItem(
            provider="openai",
            model="gpt-4o",
            display_name="OpenAI",
            has_credential=True,
        )
        mock_config_handler.get_provider_credential.return_value = {"api_key": "test_token_old", "base_url": ""}
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            edit_item=edit_item,
        )
        # 修改模型
        dialog.custom_model_input.value = "gpt-4o-mini"
        dialog.model_dropdown.value = None
        dialog.api_key_input.value = "test_token_new"
        dialog.base_url_input.value = "https://api.openai.com/v1"
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_config.assert_called_once_with({"llm_failover_models": ["openai/gpt-4o-mini"]})

    def test_on_confirm_click_missing_provider_or_model_returns(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """provider 或 model 为空时直接返回"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = ""
        dialog.model_dropdown.value = "gpt-4o"
        dialog.api_key_input.value = "test_token_mock"
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_provider_credential.assert_not_called()

        # model 为空
        dialog._provider = "openai"
        dialog.model_dropdown.value = None
        dialog.custom_model_input.value = ""
        dialog._on_confirm_click(MagicMock())
        mock_config_handler.save_provider_credential.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# 8. TestProviderCredentialDialogTestConnection
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogTestConnection:
    @pytest.mark.asyncio
    async def test_on_test_connection_success(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "test_token_mock"
        dialog.base_url_input.value = "https://api.deepseek.com"

        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            await dialog._on_test_connection(MagicMock())

        # 成功时显示 SnackBar
        assert len(mock_page.overlay) == 1

    @pytest.mark.asyncio
    async def test_on_test_connection_failure(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "test_token_mock"
        dialog.base_url_input.value = "https://api.deepseek.com"

        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": False, "error": "auth failed"})
            await dialog._on_test_connection(MagicMock())

        # 失败时显示 SnackBar
        assert len(mock_page.overlay) == 1

    @pytest.mark.asyncio
    async def test_on_test_connection_missing_fields_returns(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """缺少必要字段时直接返回，不调用 AIService"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = ""  # 空 API Key
        dialog.base_url_input.value = ""

        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock()
            await dialog._on_test_connection(MagicMock())

        mock_ai.test_connection.assert_not_called()
