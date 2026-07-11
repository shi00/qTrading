"""FailoverConfigPanelViewModel 单元测试（Phase D.1 声明式重写）。

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，消费方（AIBrainTab）直接实例化以调用 commands。
"""

import asyncio
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels import Message
from ui.viewmodels.failover_config_panel_view_model import (
    FailoverConfigPanelViewModel,
    FailoverConfigState,
    FailoverItem,
    _load_failover_items_sync,
    _normalize_base_url,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_test_connection() -> AsyncMock:
    """注入的 on_test_connection 回调 mock，默认返回成功。"""
    return AsyncMock(return_value={"success": True})


@pytest.fixture
def mock_config_handler(monkeypatch):
    """Mock ConfigHandler 模块级 patch（VM 构造时同步加载配置）。"""
    m = MagicMock()
    m.load_config.return_value = {
        "llm_failover_models": ["deepseek/deepseek-chat", "qwen/qwen3.6-plus"],
        "llm_provider": "deepseek",
    }
    m.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
    m.save_provider_credential.return_value = True
    m.save_config.return_value = True
    m.validate_failover_credentials.return_value = []
    monkeypatch.setattr("ui.viewmodels.failover_config_panel_view_model.ConfigHandler", m)
    yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough（offload 同步 IO）。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.failover_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_test_connection,
    *,
    on_save: Callable[[], None] | None = None,
) -> FailoverConfigPanelViewModel:
    """构造 VM（在 mock_config_handler 上下文中调用）。"""
    return FailoverConfigPanelViewModel(
        on_test_connection=mock_test_connection,
        on_save=on_save,
    )


@pytest.fixture
def vm(mock_test_connection, mock_config_handler, mock_thread_pool) -> FailoverConfigPanelViewModel:
    """默认 VM 实例（使用 mock_config_handler 默认配置）。"""
    return FailoverConfigPanelViewModel(on_test_connection=mock_test_connection)


# --- 模块级纯函数 ---


class TestNormalizeBaseUrl:
    """_normalize_base_url 纯函数测试。"""

    def test_strips_chat_completions_suffix(self):
        assert _normalize_base_url("https://api.deepseek.com/v1/chat/completions") == "https://api.deepseek.com/v1"

    def test_strips_completions_suffix(self):
        assert _normalize_base_url("https://api.example.com/completions") == "https://api.example.com"

    def test_strips_embeddings_suffix(self):
        assert _normalize_base_url("https://api.example.com/embeddings") == "https://api.example.com"

    def test_adds_https_prefix_when_missing(self):
        assert _normalize_base_url("api.example.com/v1") == "https://api.example.com/v1"

    def test_strips_trailing_slash(self):
        assert _normalize_base_url("https://api.deepseek.com/") == "https://api.deepseek.com"

    def test_empty_string_returns_empty(self):
        assert _normalize_base_url("") == ""

    def test_preserves_base_path(self):
        assert (
            _normalize_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )


class TestLoadFailoverItemsSync:
    """_load_failover_items_sync 模块级函数测试。"""

    def test_load_items_from_config(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "qwen/qwen3.6-plus"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "sk-test",
            "base_url": "",
        }
        items = _load_failover_items_sync()
        assert len(items) == 2
        assert items[0].provider == "deepseek"
        assert items[0].model == "deepseek-chat"
        assert items[0].has_credential is True
        assert items[0].api_key_masked  # sanitized token 非空

    def test_load_items_skips_invalid_entries(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "invalid-entry-no-slash"],
            "llm_provider": "",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        items = _load_failover_items_sync()
        assert len(items) == 1
        assert items[0].provider == "deepseek"

    def test_load_items_empty_config(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {"llm_failover_models": []}
        items = _load_failover_items_sync()
        assert items == []

    def test_load_items_no_credential(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
        items = _load_failover_items_sync()
        assert len(items) == 1
        assert items[0].has_credential is False
        assert items[0].api_key_masked == ""


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, vm):
        with pytest.raises(FrozenInstanceError):
            vm.state.is_loading = True  # type: ignore[misc]

    def test_failover_item_is_frozen(self):
        item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        with pytest.raises(FrozenInstanceError):
            item.provider = "modified"  # type: ignore[misc]

    def test_default_state_values(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        # mock_config_handler 默认配置含 2 个 failover 项
        assert len(vm.state.failover_items) == 2
        assert vm.state.is_loading is False
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"
        # Dialog 默认关闭
        assert vm.state.dialog_open is False
        assert vm.state.dialog_is_edit is False
        assert vm.state.dialog_edit_item is None
        assert vm.state.dialog_provider == ""
        assert vm.state.dialog_model == ""
        assert vm.state.dialog_custom_model == ""
        assert vm.state.dialog_base_url == ""
        assert vm.state.dialog_api_key == ""
        assert vm.state.dialog_is_testing is False
        assert vm.state.dialog_is_saving is False
        assert vm.state.dialog_status_message is None
        assert vm.state.dialog_status_type == "info"

    def test_failover_item_to_config_string(self):
        item = FailoverItem(
            provider="deepseek",
            model="deepseek-chat",
            display_name="DeepSeek",
            has_credential=True,
        )
        assert item.to_config_string() == "deepseek/deepseek-chat"


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, vm):
        received: list[FailoverConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_dialog_api_key("new-key")
        assert len(received) == 1
        assert received[0].dialog_api_key == "new-key"

    def test_unsubscribe_stops_receiving(self, vm):
        received: list[FailoverConfigState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.update_dialog_api_key("new-key")
        assert len(received) == 0

    def test_multiple_subscribers_all_notified(self, vm):
        received_a: list[FailoverConfigState] = []
        received_b: list[FailoverConfigState] = []
        vm.subscribe(lambda s: received_a.append(s))
        vm.subscribe(lambda s: received_b.append(s))
        vm.update_dialog_api_key("new-key")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_dispose_clears_subscribers(self, vm):
        received: list[FailoverConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.update_dialog_api_key("new-key")
        assert len(received) == 0


# --- reload_config ---


class TestReloadConfig:
    @pytest.mark.asyncio
    async def test_reload_config_reloads_from_config_handler(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen3.6-plus"],
            "llm_provider": "qwen",
        }
        mock_config_handler.get_provider_credential.return_value = {"api_key": "sk-new", "base_url": ""}
        await vm.reload_config()
        assert len(vm.state.failover_items) == 1
        assert vm.state.failover_items[0].provider == "qwen"

    @pytest.mark.asyncio
    async def test_reload_config_notifies_subscribers(self, vm, mock_config_handler, mock_thread_pool):
        received: list[FailoverConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        await vm.reload_config()
        assert len(received) >= 1

    @pytest.mark.asyncio
    async def test_reload_config_handles_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        # side_effect 必须在 VM 构造后设置（构造时同步加载配置）
        mock_config_handler.load_config.side_effect = RuntimeError("io error")
        await vm.reload_config()
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_snack_save_err"


# --- Dialog open/close ---


class TestDialogOpenClose:
    @pytest.mark.asyncio
    async def test_open_add_dialog_sets_state(self, vm, mock_config_handler, mock_thread_pool):
        await vm.open_add_dialog()
        assert vm.state.dialog_open is True
        assert vm.state.dialog_is_edit is False
        assert vm.state.dialog_edit_item is None
        assert vm.state.dialog_provider == ""
        assert vm.state.dialog_api_key == ""
        # existing_providers 包含 failover 列表中的 + 主供应商（去重）
        assert "deepseek" in vm.state.dialog_existing_providers
        assert "qwen" in vm.state.dialog_existing_providers

    @pytest.mark.asyncio
    async def test_open_add_dialog_includes_primary_provider(self, vm, mock_config_handler, mock_thread_pool):
        # 主供应商不在 failover 列表中时也应加入 existing_providers
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen3.6-plus"],
            "llm_provider": "deepseek",
        }
        await vm.open_add_dialog()
        assert "deepseek" in vm.state.dialog_existing_providers
        assert "qwen" in vm.state.dialog_existing_providers

    @pytest.mark.asyncio
    async def test_open_add_dialog_handles_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        mock_config_handler.load_config.side_effect = RuntimeError("io error")
        await vm.open_add_dialog()
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None

    @pytest.mark.asyncio
    async def test_open_edit_dialog_loads_credential(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "sk-existing",
            "base_url": "https://api.deepseek.com",
        }
        await vm.open_edit_dialog(0)
        assert vm.state.dialog_open is True
        assert vm.state.dialog_is_edit is True
        assert vm.state.dialog_edit_item is not None
        assert vm.state.dialog_provider == "deepseek"
        assert vm.state.dialog_model == "deepseek-chat"
        assert vm.state.dialog_api_key == "sk-existing"
        assert vm.state.dialog_base_url == "https://api.deepseek.com"

    @pytest.mark.asyncio
    async def test_open_edit_dialog_invalid_index_no_change(self, vm, mock_config_handler, mock_thread_pool):
        original_provider = vm.state.dialog_provider
        await vm.open_edit_dialog(99)
        assert vm.state.dialog_open is False
        assert vm.state.dialog_provider == original_provider

    @pytest.mark.asyncio
    async def test_open_edit_dialog_negative_index_no_change(self, vm, mock_config_handler, mock_thread_pool):
        await vm.open_edit_dialog(-1)
        assert vm.state.dialog_open is False

    @pytest.mark.asyncio
    async def test_open_edit_dialog_handles_exception(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        mock_config_handler.get_provider_credential.side_effect = RuntimeError("io error")
        await vm.open_edit_dialog(0)
        assert vm.state.status_type == "error"

    def test_close_dialog_resets_state(self, vm):
        # 先打开 dialog
        vm._set_state(dialog_open=True, dialog_provider="deepseek", dialog_api_key="sk-test")
        vm.close_dialog()
        assert vm.state.dialog_open is False
        assert vm.state.dialog_edit_item is None
        assert vm.state.dialog_provider == ""
        assert vm.state.dialog_model == ""
        assert vm.state.dialog_custom_model == ""
        assert vm.state.dialog_base_url == ""
        assert vm.state.dialog_api_key == ""
        assert vm.state.dialog_status_message is None


# --- Dialog field updates ---


class TestDialogFieldUpdates:
    def test_update_dialog_provider_sets_default_base_url(self, vm):
        vm.update_dialog_provider("deepseek")
        assert vm.state.dialog_provider == "deepseek"
        assert vm.state.dialog_base_url == "https://api.deepseek.com"
        assert vm.state.dialog_model == ""
        assert vm.state.dialog_custom_model == ""

    def test_update_dialog_provider_unknown_provider_empty_base_url(self, vm):
        vm.update_dialog_provider("unknown")
        assert vm.state.dialog_provider == "unknown"
        assert vm.state.dialog_base_url == ""

    def test_update_dialog_model_clears_custom_model(self, vm):
        vm.update_dialog_custom_model("custom-model")
        assert vm.state.dialog_custom_model == "custom-model"
        vm.update_dialog_model("deepseek-chat")
        assert vm.state.dialog_model == "deepseek-chat"
        assert vm.state.dialog_custom_model == ""

    def test_update_dialog_custom_model_clears_model(self, vm):
        vm.update_dialog_model("deepseek-chat")
        assert vm.state.dialog_model == "deepseek-chat"
        vm.update_dialog_custom_model("custom-model")
        assert vm.state.dialog_custom_model == "custom-model"
        assert vm.state.dialog_model == ""

    def test_update_dialog_base_url(self, vm):
        vm.update_dialog_base_url("https://example.com")
        assert vm.state.dialog_base_url == "https://example.com"

    def test_update_dialog_api_key(self, vm):
        vm.update_dialog_api_key("sk-secret")
        assert vm.state.dialog_api_key == "sk-secret"

    def test_update_dialog_triggers_subscriber(self, vm):
        received: list[FailoverConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_dialog_api_key("sk-new")
        assert len(received) == 1


# --- test_credential ---


class TestTestCredential:
    @pytest.mark.asyncio
    async def test_test_credential_success(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        mock_test_connection.return_value = {"success": True}
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="deepseek-chat",
            dialog_api_key="sk-test",
        )
        await vm.test_credential()
        mock_test_connection.assert_awaited_once()
        assert vm.state.dialog_status_type == "success"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "failover_test_success"
        assert vm.state.dialog_is_testing is False

    @pytest.mark.asyncio
    async def test_test_credential_failure_with_detail(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_test_connection.return_value = {"success": False, "error": "Invalid API key"}
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="deepseek-chat",
            dialog_api_key="sk-test",
        )
        await vm.test_credential()
        assert vm.state.dialog_status_type == "error"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "failover_test_failed"
        assert vm.state.dialog_status_message.params.get("detail") == "Invalid API key"

    @pytest.mark.asyncio
    async def test_test_credential_handles_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        mock_test_connection.side_effect = ValueError("network error")
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="deepseek-chat",
            dialog_api_key="sk-test",
        )
        await vm.test_credential()
        assert vm.state.dialog_status_type == "error"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "failover_test_failed"
        # detail 是 sanitized 错误信息
        assert vm.state.dialog_status_message.params.get("detail")
        assert vm.state.dialog_is_testing is False

    @pytest.mark.asyncio
    async def test_test_credential_missing_provider_skips(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(dialog_provider="", dialog_model="deepseek-chat", dialog_api_key="sk-test")
        await vm.test_credential()
        mock_test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_test_credential_missing_model_skips(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(dialog_provider="deepseek", dialog_model="", dialog_api_key="sk-test")
        await vm.test_credential()
        mock_test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_test_credential_missing_api_key_skips(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(dialog_provider="deepseek", dialog_model="deepseek-chat", dialog_api_key="")
        await vm.test_credential()
        mock_test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_test_credential_uses_custom_model_when_set(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="",
            dialog_custom_model="custom-model",
            dialog_api_key="sk-test",
        )
        await vm.test_credential()
        call_kwargs = mock_test_connection.call_args.kwargs
        assert call_kwargs["model"] == "custom-model"


# --- confirm_credential ---


class TestConfirmCredential:
    @pytest.mark.asyncio
    async def test_confirm_credential_add_new_success(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_open=True,
            dialog_is_edit=False,
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="sk-new",
            dialog_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        # 初始 failover 列表 2 项
        await vm.confirm_credential()
        # dialog 关闭 + reload_config 触发
        assert vm.state.dialog_open is False
        mock_config_handler.save_provider_credential.assert_called_once()
        mock_config_handler.save_config.assert_called()

    @pytest.mark.asyncio
    async def test_confirm_credential_add_new_without_api_key_warns(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_open=True,
            dialog_is_edit=False,
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="",
        )
        await vm.confirm_credential()
        assert vm.state.dialog_status_type == "warning"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "llm_test_need_key"
        mock_config_handler.save_provider_credential.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_credential_edit_existing_success(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        edit_item = FailoverItem(
            provider="qwen",
            model="qwen3.6-plus",
            display_name="通义千问",
            has_credential=True,
        )
        vm._set_state(
            dialog_open=True,
            dialog_is_edit=True,
            dialog_edit_item=edit_item,
            dialog_provider="qwen",
            dialog_model="qwen3.6-max-preview",
            dialog_api_key="sk-updated",
            dialog_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        await vm.confirm_credential()
        assert vm.state.dialog_open is False
        mock_config_handler.save_provider_credential.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_credential_primary_provider_rejected(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        # provider == primary_provider 时应返回警告，不保存
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat"],
            "llm_provider": "deepseek",
        }
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_open=True,
            dialog_is_edit=False,
            dialog_provider="deepseek",  # 主供应商
            dialog_model="deepseek-chat",
            dialog_api_key="sk-new",
        )
        await vm.confirm_credential()
        assert vm.state.dialog_status_type == "warning"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "failover_primary_in_list"

    @pytest.mark.asyncio
    async def test_confirm_credential_edit_clear_api_key_warns(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        # 编辑模式清空 API Key 应触发警告（使用非主供应商 qwen 避免触发 primary_in_list）
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "sk-existing",
            "base_url": "",
        }
        vm = _make_vm(mock_test_connection)
        edit_item = FailoverItem(
            provider="qwen",
            model="qwen3.6-plus",
            display_name="通义千问",
            has_credential=True,
        )
        vm._set_state(
            dialog_open=True,
            dialog_is_edit=True,
            dialog_edit_item=edit_item,
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="",  # 清空
        )
        await vm.confirm_credential()
        assert vm.state.dialog_status_type == "warning"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "failover_clear_key_warning"

    @pytest.mark.asyncio
    async def test_confirm_credential_missing_provider_skips(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(dialog_provider="", dialog_model="deepseek-chat", dialog_api_key="sk-test")
        await vm.confirm_credential()
        mock_config_handler.save_provider_credential.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_credential_missing_model_skips(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(dialog_provider="deepseek", dialog_model="", dialog_api_key="sk-test")
        await vm.confirm_credential()
        mock_config_handler.save_provider_credential.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_credential_handles_save_exception(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_config_handler.save_provider_credential.side_effect = RuntimeError("save failed")
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="sk-new",
            dialog_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        await vm.confirm_credential()
        assert vm.state.dialog_status_type == "error"
        assert vm.state.dialog_status_message is not None
        assert vm.state.dialog_status_message.key == "sys_snack_save_err"
        assert vm.state.dialog_is_saving is False

    @pytest.mark.asyncio
    async def test_confirm_credential_normalizes_base_url(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="sk-test",
            dialog_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",  # 含端点后缀
        )
        await vm.confirm_credential()
        call_kwargs = mock_config_handler.save_provider_credential.call_args.kwargs
        assert call_kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


# --- delete_item ---


class TestDeleteItem:
    @pytest.mark.asyncio
    async def test_delete_item_success(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/deepseek-chat", "qwen/qwen3.6-plus"],
            "llm_provider": "deepseek",
        }
        initial_count = len(vm.state.failover_items)
        await vm.delete_item(0)
        # reload_config 触发，应保留 1 项（删除 1 项后）
        assert len(vm.state.failover_items) == initial_count - 1
        mock_config_handler.save_config.assert_called()

    @pytest.mark.asyncio
    async def test_delete_item_invalid_index_no_change(self, vm, mock_config_handler, mock_thread_pool):
        initial_count = len(vm.state.failover_items)
        await vm.delete_item(99)
        assert len(vm.state.failover_items) == initial_count
        mock_config_handler.save_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_item_negative_index_no_change(self, vm, mock_config_handler, mock_thread_pool):
        initial_count = len(vm.state.failover_items)
        await vm.delete_item(-1)
        assert len(vm.state.failover_items) == initial_count

    @pytest.mark.asyncio
    async def test_delete_item_handles_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        mock_config_handler.load_config.side_effect = RuntimeError("io error")
        await vm.delete_item(0)
        assert vm.state.status_type == "error"


# --- move_item ---


class TestMoveItem:
    @pytest.mark.asyncio
    async def test_move_item_up_success(self, vm, mock_config_handler, mock_thread_pool):
        # index 1 上移到 index 0
        original_first = vm.state.failover_items[0]
        original_second = vm.state.failover_items[1]
        await vm.move_item(1, -1)
        assert vm.state.failover_items[0] == original_second
        assert vm.state.failover_items[1] == original_first
        mock_config_handler.save_config.assert_called()

    @pytest.mark.asyncio
    async def test_move_item_down_success(self, vm, mock_config_handler, mock_thread_pool):
        # index 0 下移到 index 1
        original_first = vm.state.failover_items[0]
        original_second = vm.state.failover_items[1]
        await vm.move_item(0, 1)
        assert vm.state.failover_items[0] == original_second
        assert vm.state.failover_items[1] == original_first

    @pytest.mark.asyncio
    async def test_move_item_first_cannot_move_up(self, vm, mock_config_handler, mock_thread_pool):
        original_items = vm.state.failover_items
        await vm.move_item(0, -1)
        assert vm.state.failover_items == original_items
        mock_config_handler.save_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_item_last_cannot_move_down(self, vm, mock_config_handler, mock_thread_pool):
        original_items = vm.state.failover_items
        last_index = len(vm.state.failover_items) - 1
        await vm.move_item(last_index, 1)
        assert vm.state.failover_items == original_items
        mock_config_handler.save_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_item_invalid_index_no_change(self, vm, mock_config_handler, mock_thread_pool):
        original_items = vm.state.failover_items
        await vm.move_item(99, -1)
        assert vm.state.failover_items == original_items

    @pytest.mark.asyncio
    async def test_move_item_rolls_back_on_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_config.side_effect = RuntimeError("save failed")
        vm = _make_vm(mock_test_connection)
        original_items = vm.state.failover_items
        await vm.move_item(0, 1)
        # 回滚到原始顺序
        assert vm.state.failover_items == original_items
        assert vm.state.status_type == "error"


# --- validate_all ---


class TestValidateAll:
    @pytest.mark.asyncio
    async def test_validate_all_complete(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.validate_failover_credentials.return_value = []
        await vm.validate_all()
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "failover_validation_complete"

    @pytest.mark.asyncio
    async def test_validate_all_missing_credentials(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.validate_failover_credentials.return_value = ["deepseek", "qwen"]
        await vm.validate_all()
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "failover_validation_missing"
        assert "deepseek" in vm.state.status_message.params.get("providers", "")
        assert "qwen" in vm.state.status_message.params.get("providers", "")

    @pytest.mark.asyncio
    async def test_validate_all_handles_exception(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        mock_config_handler.validate_failover_credentials.side_effect = RuntimeError("io error")
        vm = _make_vm(mock_test_connection)
        await vm.validate_all()
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_snack_save_err"


# --- save_config ---


class TestSaveConfig:
    def test_save_config_calls_on_save_callback(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        on_save = MagicMock()
        vm = _make_vm(mock_test_connection, on_save=on_save)
        vm.save_config()
        on_save.assert_called_once()

    def test_save_config_without_on_save_no_error(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection, on_save=None)
        vm.save_config()  # 不应抛异常

    def test_save_config_shows_success_status(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        vm.save_config()
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "settings_verify_success"


# --- R2: CancelledError propagation ---


class TestCancelledErrorPropagation:
    """R2 红线：asyncio.CancelledError 必须传播，不得被 except Exception 吞没。

    CancelledError 是 BaseException 子类（Python 3.8+），不被 except Exception 捕获，
    因此 VM 中所有 ``except Exception`` 块自动放行 CancelledError。本测试验证该契约。
    """

    @pytest.mark.asyncio
    async def test_reload_config_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_thread_pool.run_async = AsyncMock(side_effect=_raise_cancelled)
        vm = _make_vm(mock_test_connection)
        with pytest.raises(asyncio.CancelledError):
            await vm.reload_config()

    @pytest.mark.asyncio
    async def test_test_credential_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_test_connection.side_effect = asyncio.CancelledError()
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="deepseek-chat",
            dialog_api_key="sk-test",
        )
        with pytest.raises(asyncio.CancelledError):
            await vm.test_credential()

    @pytest.mark.asyncio
    async def test_confirm_credential_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_thread_pool.run_async = AsyncMock(side_effect=_raise_cancelled)
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="qwen",
            dialog_model="qwen3.6-plus",
            dialog_api_key="sk-test",
            dialog_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        with pytest.raises(asyncio.CancelledError):
            await vm.confirm_credential()

    @pytest.mark.asyncio
    async def test_validate_all_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_thread_pool.run_async = AsyncMock(side_effect=_raise_cancelled)
        vm = _make_vm(mock_test_connection)
        with pytest.raises(asyncio.CancelledError):
            await vm.validate_all()

    @pytest.mark.asyncio
    async def test_delete_item_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_thread_pool.run_async = AsyncMock(side_effect=_raise_cancelled)
        vm = _make_vm(mock_test_connection)
        with pytest.raises(asyncio.CancelledError):
            await vm.delete_item(0)

    @pytest.mark.asyncio
    async def test_move_item_propagates_cancelled_error(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_thread_pool.run_async = AsyncMock(side_effect=_raise_cancelled)
        vm = _make_vm(mock_test_connection)
        with pytest.raises(asyncio.CancelledError):
            await vm.move_item(0, 1)


# --- 消息参数契约（VM 不感知 locale）---


class TestMessageParamsContract:
    """VM 产出的 Message 应只含 i18n key + params，不调用 I18n.get。"""

    def test_status_message_uses_message_dataclass(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        vm.save_config()
        assert isinstance(vm.state.status_message, Message)
        # key 是 i18n 字符串 key，不是本地化文本
        assert vm.state.status_message.key == "settings_verify_success"

    @pytest.mark.asyncio
    async def test_test_credential_failure_message_has_detail_param(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_test_connection.return_value = {"success": False, "error": "boom"}
        vm = _make_vm(mock_test_connection)
        vm._set_state(
            dialog_provider="deepseek",
            dialog_model="deepseek-chat",
            dialog_api_key="sk-test",
        )
        await vm.test_credential()
        msg = vm.state.dialog_status_message
        assert msg is not None
        assert msg.key == "failover_test_failed"
        assert msg.params.get("detail") == "boom"

    @pytest.mark.asyncio
    async def test_validate_all_missing_message_has_providers_param(self, vm, mock_config_handler, mock_thread_pool):
        mock_config_handler.validate_failover_credentials.return_value = ["openai"]
        await vm.validate_all()
        msg = vm.state.status_message
        assert msg is not None
        assert msg.key == "failover_validation_missing"
        assert "openai" in msg.params.get("providers", "")
