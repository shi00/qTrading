"""FailoverConfigPanel 和 ProviderCredentialDialog 单元测试"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels.failover_config_panel import (
    FailoverConfigPanel,
    FailoverItem,
    ProviderCredentialDialog,
)

pytestmark = pytest.mark.unit


async def _run_async_passthrough(task_type, func, *args, **kwargs):
    """Mock helper: 立即同步执行 func 并返回结果，模拟线程池 offload。"""
    return func(*args, **kwargs)


@pytest.fixture(autouse=True)
def _patch_thread_pool():
    """Patch failover_config_panel 模块级 ThreadPoolManager，run_async 直接同步执行。"""
    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_run_async_passthrough)
    with patch("ui.components.config_panels.failover_config_panel.ThreadPoolManager", return_value=mock_tpm):
        yield


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
    with patch(
        "ui.components.config_panels.failover_config_panel.LLM_PROVIDERS",
        MOCK_LLM_PROVIDERS,
    ):
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
        m.primary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        m.secondary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        yield m


@pytest.fixture
def mock_section_header():
    with patch("ui.components.config_panels.failover_config_panel.SectionHeader") as m:
        m.return_value = MagicMock(spec=ft.Control)
        yield m


@pytest.fixture
def mock_page():
    page = MagicMock(spec=ft.Page)
    page.services = []
    page.show_dialog = MagicMock(spec=[])
    page.pop_dialog = MagicMock(spec=[])
    page.launch_url = MagicMock(spec=[])
    page.update = MagicMock(spec=[])

    def _run_task(coro_func, *args, **kwargs):
        """同步执行协程，模拟 Flet page.run_task 调度。"""
        asyncio.run(coro_func(*args, **kwargs))

    page.run_task = _run_task
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
    kwargs.setdefault("on_test_connection", AsyncMock(return_value={"success": True}))
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
    kwargs.setdefault("on_test_connection", AsyncMock(return_value={"success": True}))
    dialog = ProviderCredentialDialog(page=mock_page, **kwargs)
    return dialog


# ════════════════════════════════════════════════════════════════════════════
# 1. TestFailoverItem
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverItem:
    def test_to_config_string(self):
        item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        assert item.to_config_string() == "deepseek/deepseek-chat"

    def test_to_config_string_with_slash_in_model(self):
        """模型名含 '/' 时，split('/', 1) 保证只分割第一段"""
        item = FailoverItem(
            provider="openai",
            model="gpt-4o/mini",
            display_name="OpenAI",
            has_credential=True,
        )
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
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
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
            {"api_key": "test_token_deepseek_mock_padding_1234", "base_url": ""}
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
        assert panel._failover_items[0].api_key_masked == "tes***1234"
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
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
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
        panel._on_add_click(MagicMock(spec=[]))
        mock_page.show_dialog.assert_called_once()
        dialog = mock_page.show_dialog.call_args[0][0]
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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_mock",
            "base_url": "",
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
        panel._on_add_click(MagicMock(spec=[]))
        dialog = mock_page.show_dialog.call_args[0][0]
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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_mock",
            "base_url": "",
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
        panel._on_edit_item(0)
        mock_page.show_dialog.assert_called_once()
        dialog = mock_page.show_dialog.call_args[0][0]
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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "",
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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "",
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
        # 手动修改 _failover_items 使其与配置不同步
        panel._failover_items.append(
            FailoverItem(
                provider="zhipu",
                model="glm-4",
                display_name="智谱",
                has_credential=False,
            )
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
            "llm_failover_models": [
                "deepseek/deepseek-chat",
                "openai/gpt-4o",
                "zhipu/glm-4",
            ],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "",
        }
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
        panel._on_validate_all(MagicMock(spec=[]))
        # 无缺失时显示成功 SnackBar
        assert mock_page.show_dialog.called

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
        mock_config_handler.validate_failover_credentials.return_value = [
            "openai",
            "zhipu",
        ]
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._on_validate_all(MagicMock(spec=[]))
        # 有缺失时显示警告 SnackBar
        assert mock_page.show_dialog.called

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
        on_save = MagicMock(spec=lambda *a, **k: None)
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
        panel._on_save_click(MagicMock(spec=[]))
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
        panel._on_save_click(MagicMock(spec=[]))
        assert mock_page.show_dialog.called


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
        initial_count = mock_config_handler.load_config.call_count
        # _on_locale_change 仅重建 UI（_build_ui + _render_list），不重新加载配置（避免 keyring IO）
        with (
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_build_ui") as mock_build_ui,
            patch.object(panel, "_render_list") as mock_render_list,
        ):
            panel._on_locale_change()
        mock_build_ui.assert_called_once()
        mock_render_list.assert_called_once()
        # 不应再调用 load_config（即不触发 _load_config）
        assert mock_config_handler.load_config.call_count == initial_count

    def test_on_locale_change_preserves_failover_items(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """§5.8 规范 3：_on_locale_change 仅重建 UI 文本，必须保留已有 _failover_items 数据"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_mock",
            "base_url": "",
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
        # 记录调用前 _failover_items 的内容快照
        items_before = [(item.provider, item.model, item.has_credential) for item in panel._failover_items]
        assert len(items_before) == 2

        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()

        # 调用后 _failover_items 内容必须保持不变（不重新 _load_config）
        items_after = [(item.provider, item.model, item.has_credential) for item in panel._failover_items]
        assert items_after == items_before

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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_mock",
            "base_url": "",
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
        dialog._on_cancel(MagicMock(spec=[]))
        mock_page.pop_dialog.assert_called_once()

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
        e = MagicMock(spec=[])
        e.control = MagicMock(spec=[])
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
        e = MagicMock(spec=[])
        e.control = MagicMock(spec=[])
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
        e = MagicMock(spec=[])
        e.control = MagicMock(spec=[])
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
        dialog._on_confirm_click(MagicMock(spec=[]))
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
        dialog._on_confirm_click(MagicMock(spec=[]))
        mock_config_handler.save_provider_credential.assert_not_called()
        # 应显示警告 SnackBar
        assert mock_page.show_dialog.called

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
        dialog._on_confirm_click(MagicMock(spec=[]))
        mock_config_handler.save_provider_credential.assert_not_called()
        # 应显示警告 SnackBar
        assert mock_page.show_dialog.called

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
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_old",
            "base_url": "",
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
        # 修改模型
        dialog.custom_model_input.value = "gpt-4o-mini"
        dialog.model_dropdown.value = None
        dialog.api_key_input.value = "test_token_new"
        dialog.base_url_input.value = "https://api.openai.com/v1"
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
        dialog._on_confirm_click(MagicMock(spec=[]))
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
        dialog._on_confirm_click(MagicMock(spec=[]))
        mock_config_handler.save_provider_credential.assert_not_called()

        # model 为空
        dialog._provider = "openai"
        dialog.model_dropdown.value = None
        dialog.custom_model_input.value = ""
        dialog._on_confirm_click(MagicMock(spec=[]))
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
        mock_callback = AsyncMock(return_value={"success": True})
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=mock_callback,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "test_token_mock"
        dialog.base_url_input.value = "https://api.deepseek.com"

        await ProviderCredentialDialog._on_test_connection(dialog, MagicMock(spec=[]))

        mock_callback.assert_called_once_with(
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key="test_token_mock",
        )
        # 成功时显示 SnackBar
        assert mock_page.show_dialog.called

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
        mock_callback = AsyncMock(return_value={"success": False, "error": "auth failed"})
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=mock_callback,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "test_token_mock"
        dialog.base_url_input.value = "https://api.deepseek.com"

        await ProviderCredentialDialog._on_test_connection(dialog, MagicMock(spec=[]))

        mock_callback.assert_called_once()
        # 失败时显示 SnackBar
        assert mock_page.show_dialog.called

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
        """缺少必要字段时直接返回，不调用 on_test_connection 回调"""
        mock_callback = AsyncMock(return_value={"success": True})
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=mock_callback,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = ""  # 空 API Key
        dialog.base_url_input.value = ""

        await ProviderCredentialDialog._on_test_connection(dialog, MagicMock(spec=[]))

        mock_callback.assert_not_called()


class TestProviderCredentialDialogEditModeClearApiKey:
    """测试编辑模式下清空 API Key 的警告提示"""

    def test_edit_mode_clear_api_key_shows_warning(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """编辑模式下清空 API Key 时显示警告 SnackBar"""
        edit_item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        # 原有凭证有 API Key
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "test_token_existing",
            "base_url": "",
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
        # 用户清空 API Key
        dialog.api_key_input.value = ""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "openai",
        }
        dialog._on_confirm_click(MagicMock(spec=[]))
        # 应显示警告 SnackBar
        assert mock_page.show_dialog.called
        # 应仍然保存（不阻止操作）
        mock_config_handler.save_provider_credential.assert_called_once()

    def test_edit_mode_clear_api_key_no_existing_credential(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """编辑模式下清空 API Key，但原有凭证本就为空，不显示警告"""
        edit_item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=False,
        )
        # 原有凭证没有 API Key
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "",
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
        dialog.api_key_input.value = ""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "openai",
        }
        dialog._on_confirm_click(MagicMock(spec=[]))
        # 不显示警告（原有凭证本就为空）
        assert not mock_page.show_dialog.called


class TestProviderCredentialDialogNormalizeBaseUrl:
    """测试 ProviderCredentialDialog._normalize_base_url"""

    def test_normalize_strips_chat_completions(self):
        from ui.components.config_panels.failover_config_panel import (
            ProviderCredentialDialog,
        )

        assert (
            ProviderCredentialDialog._normalize_base_url("https://api.deepseek.com/v1/chat/completions")
            == "https://api.deepseek.com/v1"
        )

    def test_normalize_strips_completions(self):
        from ui.components.config_panels.failover_config_panel import (
            ProviderCredentialDialog,
        )

        assert (
            ProviderCredentialDialog._normalize_base_url("https://api.example.com/completions")
            == "https://api.example.com"
        )

    def test_normalize_adds_https_prefix(self):
        from ui.components.config_panels.failover_config_panel import (
            ProviderCredentialDialog,
        )

        assert ProviderCredentialDialog._normalize_base_url("api.example.com/v1") == "https://api.example.com/v1"

    def test_normalize_empty_string(self):
        from ui.components.config_panels.failover_config_panel import (
            ProviderCredentialDialog,
        )

        assert ProviderCredentialDialog._normalize_base_url("") == ""

    def test_normalize_preserves_base_path(self):
        from ui.components.config_panels.failover_config_panel import (
            ProviderCredentialDialog,
        )

        assert (
            ProviderCredentialDialog._normalize_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )


# ════════════════════════════════════════════════════════════════════════════
# 9. TestProviderCredentialDialogBuildOptions (A1: _build_ui 无 test 回调 + _populate_edit_data 早返回)
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogBuildOptions:
    """覆盖 _build_ui 中 on_test_connection=None 分支与 _populate_edit_data 早返回。"""

    def test_build_ui_without_test_connection_callback(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """on_test_connection=None 时 _test_btn 为 None，actions 仅含 cancel + confirm"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=None,
        )
        assert dialog._test_btn is None
        assert len(dialog.actions) == 2

    def test_populate_edit_data_returns_when_no_edit_item(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_edit_item 为 None 时 _populate_edit_data 直接返回（覆盖 line 153）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        assert dialog._edit_item is None
        # 直接调用不应抛出
        dialog._populate_edit_data()


# ════════════════════════════════════════════════════════════════════════════
# 10. TestProviderCredentialDialogLifecycle (A2-A4: did_mount/will_unmount/refresh_locale)
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogLifecycle:
    """覆盖 did_mount/will_unmount/refresh_locale 正常与异常路径。"""

    def test_did_mount_subscribes_i18n(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """did_mount 订阅 I18n（覆盖 line 170）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.did_mount()
        mock_i18n.subscribe.assert_called_once_with(dialog.refresh_locale)

    def test_will_unmount_unsubscribes_and_clears_id(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """will_unmount 取消订阅并清理 id（覆盖 173-175）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.did_mount()
        dialog.will_unmount()
        mock_i18n.unsubscribe.assert_called_once()
        assert dialog._locale_subscription_id is None

    def test_refresh_locale_updates_labels(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """refresh_locale 正常路径更新所有 i18n 文案（覆盖 179-200）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._provider = "deepseek"
        dialog.refresh_locale()
        assert dialog.title.value == "failover_dialog_title"
        assert dialog.provider_dropdown.label == "failover_select_provider"
        assert dialog._cancel_btn.content == "common_cancel"

    def test_refresh_locale_exception_logged_as_warning(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
        caplog,
    ):
        """I18n.get 抛错时 refresh_locale 仅 warning 不抛（覆盖 201-202）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        mock_i18n.get.side_effect = RuntimeError("i18n boom")
        with caplog.at_level(logging.WARNING):
            dialog.refresh_locale()
        assert "refresh_locale failed" in caplog.text


# ════════════════════════════════════════════════════════════════════════════
# 11. TestProviderCredentialDialogProviderChange (A5-A7)
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogProviderChange:
    """覆盖 _on_provider_change_internal update 分支、_on_model_dropdown_change 空值、_update_links_row models_url。"""

    def test_on_provider_change_internal_triggers_control_updates(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """model_dropdown/base_url_input/links_row 有 page 时触发 update（覆盖 229/231/233）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        # 让控件的 page 非 None 以触发 update 分支
        dialog.model_dropdown.page = mock_page
        dialog.base_url_input.page = mock_page
        dialog.links_row.page = mock_page
        dialog._on_provider_change_internal("deepseek")
        assert len(dialog.model_dropdown.options) > 0

    def test_on_model_dropdown_change_none_value_preserves_custom_input(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """e.control.value 为 None 时不清理 custom_model_input（覆盖 236->exit）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.custom_model_input.value = "my-model"
        e = MagicMock(spec=[])
        e.control = MagicMock(spec=[])
        e.control.value = None
        dialog._on_model_dropdown_change(e)
        assert dialog.custom_model_input.value == "my-model"

    def test_update_links_row_with_models_url(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """provider 含 console_url + pricing_url + models_url 时添加 3 个按钮（覆盖 246->253, 261）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        # 临时添加 models_url
        mock_llm_providers["deepseek"]["models_url"] = "https://platform.deepseek.com/models"
        try:
            dialog._update_links_row("deepseek")
            assert len(dialog.links_row.controls) == 3
        finally:
            mock_llm_providers["deepseek"].pop("models_url", None)


# ════════════════════════════════════════════════════════════════════════════
# 12. TestProviderCredentialDialogActions (A8-A13)
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogActions:
    """覆盖 _open_url、_on_cancel、_on_test_connection、_show_snack、_on_confirm_click 边界。"""

    def test_open_url_calls_page_launch_url(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_open_url 调用 page.launch_url（覆盖 269-270）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog._open_url("https://example.com")
        mock_page.launch_url.assert_called_once_with("https://example.com")

    def test_on_cancel_without_page_does_not_raise(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_on_cancel 无 page 时不抛出（覆盖 287->exit）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.page = None
        dialog._on_cancel(MagicMock(spec=[]))

    @pytest.mark.asyncio
    async def test_on_test_connection_no_callback_returns_early(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_on_test_connection 无 callback 时早返回（覆盖 line 300）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=None,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "key"
        await dialog._on_test_connection(MagicMock(spec=[]))
        assert not mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_on_test_connection_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_on_test_connection callback 抛错时显示 SnackBar（覆盖 316-322）"""
        mock_callback = AsyncMock(side_effect=RuntimeError("conn err"))
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_test_connection=mock_callback,
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "key"
        await dialog._on_test_connection(MagicMock(spec=[]))
        assert mock_page.show_dialog.called

    def test_show_snack_without_page_does_not_raise(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_show_snack 无 page 时跳过（覆盖 328->exit）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.page = None
        dialog._show_snack("msg", "#fff")

    def test_on_confirm_click_without_page_does_not_raise(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """_on_confirm_click 无 page 时不调 run_task（覆盖 353->exit）"""
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.page = None
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.api_key_input.value = "key"
        dialog._on_confirm_click(MagicMock(spec=[]))


# ════════════════════════════════════════════════════════════════════════════
# 13. TestProviderCredentialDialogConfirmAsync (A14 + 403->406/407-414)
# ════════════════════════════════════════════════════════════════════════════


class TestProviderCredentialDialogConfirmAsync:
    """覆盖 _do_confirm_click_async 的边界路径。"""

    @pytest.mark.asyncio
    async def test_do_confirm_click_async_existing_entry_not_duplicated(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """新增模式下 entry 已存在时不重复添加（覆盖 385->388）"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["openai/gpt-4o"],
            "llm_provider": "deepseek",
        }
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
        dialog.api_key_input.value = "key"
        await dialog._do_confirm_click_async("openai", "gpt-4o", "", "key")
        mock_config_handler.save_config.assert_called_once_with({"llm_failover_models": ["openai/gpt-4o"]})

    @pytest.mark.asyncio
    async def test_do_confirm_click_async_without_page_skips_close(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """page 为 None 时不调用 page.pop_dialog（覆盖 403->406）"""
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
        )
        dialog.page = None
        await dialog._do_confirm_click_async("openai", "gpt-4o", "", "key")

    @pytest.mark.asyncio
    async def test_do_confirm_click_async_calls_on_confirm(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """on_confirm 回调被调用（覆盖 line 407）"""
        on_confirm = MagicMock()
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        dialog = _make_dialog(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_page,
            on_confirm=on_confirm,
        )
        dialog._provider = "openai"
        dialog.model_dropdown.value = "gpt-4o"
        dialog.api_key_input.value = "key"
        await dialog._do_confirm_click_async("openai", "gpt-4o", "", "key")
        on_confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_confirm_click_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_page,
    ):
        """保存异常时显示错误 SnackBar（覆盖 408-414）"""
        mock_config_handler.save_provider_credential.side_effect = RuntimeError("save err")
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
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
        dialog.api_key_input.value = "key"
        await dialog._do_confirm_click_async("openai", "gpt-4o", "", "key")
        assert mock_page.show_dialog.called


# ════════════════════════════════════════════════════════════════════════════
# 14. TestFailoverConfigPanelNoPageEdgeCases (A15/A17/A19/A21/A23/A25/A27 + 643->exit/667->exit)
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelNoPageEdgeCases:
    """覆盖 panel 各事件处理器在 page=None 时的早返回。"""

    def test_on_add_click_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_add_click 无 page 时不调 run_task（覆盖 626->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._on_add_click(MagicMock(spec=[]))

    def test_on_edit_item_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_edit_item 无 page 时不调 run_task（覆盖 650->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._on_edit_item(0)

    def test_on_delete_item_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_delete_item 无 page 时不调 run_task（覆盖 674->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._on_delete_item(0)

    def test_on_move_up_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_move_up 无 page 时跳过（覆盖 697->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
            FailoverItem(provider="openai", model="gpt-4o", display_name="OpenAI", has_credential=False),
        ]
        panel._on_move_up(1)

    def test_on_move_down_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_move_down 无 page 时跳过（覆盖 703->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
            FailoverItem(provider="openai", model="gpt-4o", display_name="OpenAI", has_credential=False),
        ]
        panel._on_move_down(0)

    def test_on_validate_all_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_validate_all 无 page 时不调 run_task（覆盖 733->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._on_validate_all(MagicMock(spec=[]))

    def test_on_dialog_confirmed_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_dialog_confirmed 无 page 时不调 run_task（覆盖 760-761）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._on_dialog_confirmed()

    def test_show_snack_without_page(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_show_snack 无 page 时跳过（覆盖 771->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        panel._show_snack("msg", "#fff")

    @pytest.mark.asyncio
    async def test_do_add_click_async_without_page_skips_open(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_do_add_click_async 无 page 时不调用 page.show_dialog（覆盖 643->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        await panel._do_add_click_async()

    @pytest.mark.asyncio
    async def test_do_edit_item_async_without_page_skips_open(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_do_edit_item_async 无 page 时不调用 page.show_dialog（覆盖 667->exit）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
        ]
        panel.page = None
        await panel._do_edit_item_async(0)


# ════════════════════════════════════════════════════════════════════════════
# 15. TestFailoverConfigPanelAsyncExceptions (A16/A18/A20/A22/A24/A26)
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelAsyncExceptions:
    """覆盖 panel 各 async 方法的异常路径。"""

    @pytest.mark.asyncio
    async def test_do_add_click_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_do_add_click_async 内部 load_config 抛错时显示 SnackBar（覆盖 645-647）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 在 __init__ 同步加载完成后，再次调用时抛错（_do_add_click_async 内部 lambda 调用 load_config）
        mock_config_handler.load_config.side_effect = RuntimeError("db")
        await panel._do_add_click_async()
        assert mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_do_edit_item_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """get_provider_credential 抛错时显示 SnackBar（覆盖 669-671）"""
        mock_config_handler.get_provider_credential.side_effect = RuntimeError("kr")
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
        ]
        await panel._do_edit_item_async(0)
        assert mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_do_delete_item_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_do_delete_item_async 内部 load_config 抛错时显示 SnackBar（覆盖 690-692）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
        ]
        # _delete_sync 内部会再次调用 load_config，这里设置抛错
        mock_config_handler.load_config.side_effect = RuntimeError("db")
        await panel._do_delete_item_async(0)
        assert mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_do_move_item_async_exception_rolls_back(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """save_config 抛错时回滚顺序并显示 SnackBar（覆盖 720-725）"""
        mock_config_handler.save_config.side_effect = RuntimeError("io")
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._failover_items = [
            FailoverItem(provider="deepseek", model="deepseek-chat", display_name="DeepSeek", has_credential=True),
            FailoverItem(provider="openai", model="gpt-4o", display_name="OpenAI", has_credential=False),
            FailoverItem(provider="zhipu", model="glm-4", display_name="智谱", has_credential=False),
        ]
        original = list(panel._failover_items)
        await panel._do_move_item_async(0, 1)
        # 回滚后顺序应与原始一致
        assert panel._failover_items == original
        assert mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_do_validate_all_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """validate_failover_credentials 抛错时显示 SnackBar（覆盖 750-752）"""
        mock_config_handler.validate_failover_credentials.side_effect = RuntimeError("v")
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        await panel._do_validate_all_async()
        assert mock_page.show_dialog.called

    @pytest.mark.asyncio
    async def test_do_dialog_confirmed_async_exception_shows_snack(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_load_config_async 抛错时显示 SnackBar（覆盖 764-768）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_load_config_async", new=AsyncMock(side_effect=RuntimeError("x"))):
            await panel._do_dialog_confirmed_async()
        assert mock_page.show_dialog.called


# ════════════════════════════════════════════════════════════════════════════
# 16. TestFailoverConfigPanelSafeUpdateAndLocale (A28-A29)
# ════════════════════════════════════════════════════════════════════════════


class TestFailoverConfigPanelSafeUpdateAndLocale:
    """覆盖 _safe_update 与 _on_locale_change 异常路径。"""

    def test_safe_update_exception_logged_as_debug(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """self.update 抛错时仅 debug 日志（覆盖 783-784）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # panel.page 已为 mock_page
        with patch.object(panel, "update", side_effect=RuntimeError("u")):
            panel._safe_update()  # 不应抛出

    def test_on_locale_change_exception_logged_as_warning(
        self,
        mock_config_handler,
        mock_i18n,
        mock_llm_providers,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
        caplog,
    ):
        """_build_ui 抛错时 warning 日志（覆盖 798-799）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_llm_providers,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with (
            patch.object(panel, "_build_ui", side_effect=RuntimeError("b")),
            caplog.at_level(logging.WARNING),
        ):
            panel._on_locale_change()
        assert "_on_locale_change failed" in caplog.text
