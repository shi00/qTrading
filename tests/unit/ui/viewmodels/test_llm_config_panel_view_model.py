"""LLMConfigPanelViewModel 单元测试（Phase 3.2.3 LLMConfigPanel 声明式重写）。

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，消费方（AIBrainTab/OnboardingWizard）直接实例化以调用 commands。
"""

from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels import Message
from ui.viewmodels.llm_config_panel_view_model import (
    LLMConfigPanelViewModel,
    LLMConfigState,
)
from utils.llm_providers import AZURE_DEFAULT_API_VERSION

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_test_connection():
    """注入的 on_test_connection 回调 mock，默认返回成功。"""
    return AsyncMock(return_value={"success": True})


@pytest.fixture
def mock_config_handler(monkeypatch):
    """Mock ConfigHandler 模块级 patch（VM 构造时同步加载配置）。

    默认返回 deepseek + 非已知 model，触发 show_custom_model_input 分支。
    """
    m = MagicMock()
    m.get_llm_config.return_value = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-test",
        "custom_models": {},
    }
    m.get_provider_credential.return_value = {"api_key": "", "base_url": ""}
    m.load_config.return_value = {}
    m.save_llm_config.return_value = None
    m.save_config.return_value = None
    m.save_provider_credential.return_value = None
    monkeypatch.setattr("ui.viewmodels.llm_config_panel_view_model.ConfigHandler", m)
    yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough（offload 同步 IO）。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.llm_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_test_connection,
    *,
    on_save: Callable[[], None] | None = None,
    on_reload_service: Callable[[], Awaitable[None]] | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
) -> LLMConfigPanelViewModel:
    """构造 VM（在 mock_config_handler 上下文中调用）。"""
    return LLMConfigPanelViewModel(
        on_test_connection=mock_test_connection,
        on_save=on_save,
        on_reload_service=on_reload_service,
        on_loading_change=on_loading_change,
    )


@pytest.fixture
def vm(mock_test_connection, mock_config_handler) -> LLMConfigPanelViewModel:
    """默认 VM 实例（使用 mock_config_handler 默认配置）。"""
    return LLMConfigPanelViewModel(on_test_connection=mock_test_connection)


# --- Init / 初始 state ---


class TestLLMConfigPanelViewModelInit:
    def test_initial_state_values(self, mock_test_connection, mock_config_handler):
        """默认配置加载后 state 字段正确（deepseek + 非已知 model 触发 custom 分支）。"""
        vm = _make_vm(mock_test_connection)
        assert vm.state.provider == "deepseek"
        assert vm.state.model == "deepseek-chat"
        assert vm.state.custom_model == "deepseek-chat"  # 非已知 model → custom_model
        assert vm.state.base_url == "https://api.deepseek.com"
        assert vm.state.api_key == "sk-test"
        assert vm.state.is_azure is False
        assert vm.state.base_url_read_only is True
        assert vm.state.show_custom_model_input is True
        assert vm.state.show_refresh_button is True
        assert vm.state.is_verifying is False
        assert vm.state.is_saving is False
        assert vm.state.is_refreshing is False
        assert vm.state.api_key_modified is False
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"
        assert vm.state.custom_model_options == ()
        assert vm.state.azure_api_version == AZURE_DEFAULT_API_VERSION

    def test_subscribe_returns_unsubscribe_callable(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        unsub = vm.subscribe(lambda s: None)
        assert callable(unsub)

    def test_subscribe_receives_initial_notification_on_next_change(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model("new-model")
        assert len(received) == 1
        assert received[0].model == "new-model"

    def test_initial_state_has_no_status(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"


# --- State snapshot / _set_state / _notify ---


class TestLLMConfigPanelViewModelState:
    def test_state_is_frozen(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        with pytest.raises(FrozenInstanceError):
            vm.state.model = "modified"  # type: ignore[misc]

    def test_set_state_updates_field(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(model="updated")  # type: ignore[attr-defined]
        assert vm.state.model == "updated"

    def test_set_state_notifies_subscribers(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._set_state(base_url="https://new.url")  # type: ignore[attr-defined]
        assert len(received) == 1
        assert received[0].base_url == "https://new.url"

    def test_notify_calls_all_subscribers(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received_a: list[LLMConfigState] = []
        received_b: list[LLMConfigState] = []
        vm.subscribe(lambda s: received_a.append(s))
        vm.subscribe(lambda s: received_b.append(s))
        vm.update_api_key("new-key")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_subscribers_receive_new_snapshot_instance(self, mock_test_connection, mock_config_handler):
        """_notify 传入的 snapshot 应与当前 state 是同一不可变对象。"""
        vm = _make_vm(mock_test_connection)
        captured: list[LLMConfigState] = []
        vm.subscribe(lambda s: captured.append(s))
        vm.update_model("x")
        assert captured[0] is vm.state

    def test_unsubscribe_stops_receiving(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.update_model("x")
        assert len(received) == 0


# --- reload_config ---


class TestLLMConfigPanelViewModelReload:
    def test_reload_config_reloads_from_config_handler(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_model("modified")
        assert vm.state.model == "modified"
        # ConfigHandler 返回新配置
        mock_config_handler.get_llm_config.return_value = {
            "provider": "qwen",
            "model": "qwen3.6-plus",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-new",
            "custom_models": {},
        }
        vm.reload_config()
        assert vm.state.provider == "qwen"
        assert vm.state.model == "qwen3.6-plus"
        assert vm.state.api_key == "sk-new"

    def test_reload_config_notifies_subscribers(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.reload_config()
        assert len(received) == 1

    def test_reload_config_azure_provider(self, mock_test_connection, mock_config_handler):
        mock_config_handler.get_llm_config.return_value = {
            "provider": "azure",
            "model": "gpt-deployment",
            "base_url": "",
            "api_key": "sk-azure",
            "azure_resource_name": "my-resource",
            "azure_deployment_name": "my-deployment",
            "api_version": "2024-10-21",
            "custom_models": {},
        }
        vm = _make_vm(mock_test_connection)
        vm.reload_config()
        assert vm.state.is_azure is True
        assert vm.state.azure_resource_name == "my-resource"
        assert vm.state.azure_deployment_name == "my-deployment"
        assert vm.state.azure_api_version == "2024-10-21"
        assert vm.state.base_url == ""
        assert vm.state.show_refresh_button is False

    def test_reload_config_custom_provider(self, mock_test_connection, mock_config_handler):
        mock_config_handler.get_llm_config.return_value = {
            "provider": "custom",
            "model": "my-custom-model",
            "base_url": "https://custom.api/v1",
            "api_key": "sk-custom",
            "custom_models": {"custom": ["my-custom-model", "other"]},
        }
        vm = _make_vm(mock_test_connection)
        vm.reload_config()
        assert vm.state.show_custom_model_input is True
        assert vm.state.base_url_read_only is False
        assert vm.state.custom_model == "my-custom-model"
        assert vm.state.custom_model_options == ("my-custom-model", "other")


# --- Update fields ---


class TestLLMConfigPanelViewModelUpdateFields:
    def test_update_model(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_model("new-model")
        assert vm.state.model == "new-model"

    def test_update_custom_model(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_custom_model("my-custom")
        assert vm.state.custom_model == "my-custom"

    def test_update_base_url(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_base_url("https://new.url/v1")
        assert vm.state.base_url == "https://new.url/v1"

    def test_update_api_key_sets_modified_flag(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        assert vm.state.api_key_modified is False
        vm.update_api_key("sk-new")
        assert vm.state.api_key == "sk-new"
        assert vm.state.api_key_modified is True

    def test_update_azure_resource(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_azure_resource("my-resource")
        assert vm.state.azure_resource_name == "my-resource"

    def test_update_azure_deployment(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_azure_deployment("my-deployment")
        assert vm.state.azure_deployment_name == "my-deployment"

    def test_update_azure_version(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_azure_version("2024-10-21")
        assert vm.state.azure_api_version == "2024-10-21"

    def test_update_triggers_subscriber_notification(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model("new")
        assert len(received) == 1
        assert received[0].model == "new"

    def test_get_current_config_non_azure(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.update_api_key("  sk-test  ")  # 含空格 + 标记 modified
        config = vm.get_current_config()
        assert config["provider"] == "deepseek"
        assert config["model"] == "deepseek-chat"
        assert config["base_url"] == "https://api.deepseek.com"
        assert config["api_key"] == "sk-test"  # strip 去除空格
        assert "azure_resource_name" not in config

    def test_get_current_config_azure(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(  # type: ignore[attr-defined]
            is_azure=True,
            azure_resource_name="res",
            azure_deployment_name="dep",
            azure_api_version="2024-10-21",
            base_url="https://should.be.ignored",
            model="ignored",
            api_key="sk-azure",
            api_key_modified=True,
        )
        config = vm.get_current_config()
        assert config["provider"] == "deepseek"
        assert config["model"] == "dep"
        assert config["base_url"] == ""  # azure 不用 base_url
        assert config["api_key"] == "sk-azure"
        assert config["api_version"] == "2024-10-21"
        assert config["azure_resource_name"] == "res"
        assert config["azure_deployment_name"] == "dep"

    def test_get_current_config_strips_empty_api_key(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(api_key="   ", api_key_modified=True)  # type: ignore[attr-defined]
        config = vm.get_current_config()
        assert config["api_key"] == ""


# --- update_provider (async) ---


class TestLLMConfigPanelViewModelUpdateProvider:
    @pytest.mark.asyncio
    async def test_update_provider_to_deepseek_loads_recommended_model(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("deepseek")
        assert vm.state.provider == "deepseek"
        assert vm.state.model == "deepseek-v4-flash"  # tag_recommend
        assert vm.state.is_azure is False
        assert vm.state.show_custom_model_input is False
        assert vm.state.show_refresh_button is True
        assert vm.state.base_url == "https://api.deepseek.com"

    @pytest.mark.asyncio
    async def test_update_provider_to_azure_sets_azure_flags(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("azure")
        assert vm.state.provider == "azure"
        assert vm.state.is_azure is True
        assert vm.state.base_url == ""
        assert vm.state.show_refresh_button is False
        assert vm.state.show_custom_model_input is False
        assert vm.state.model == ""

    @pytest.mark.asyncio
    async def test_update_provider_to_custom_shows_custom_input(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "https://custom.base/v1",
        }
        mock_config_handler.get_llm_config.return_value = {
            "provider": "custom",
            "model": "",
            "base_url": "",
            "api_key": "",
            "custom_models": {"custom": ["m1", "m2"]},
        }
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("custom")
        assert vm.state.provider == "custom"
        assert vm.state.show_custom_model_input is True
        assert vm.state.base_url_read_only is False
        assert vm.state.base_url == "https://custom.base/v1"
        assert vm.state.show_refresh_button is True
        assert vm.state.custom_model_options == ("m1", "m2")
        assert vm.state.model == ""

    @pytest.mark.asyncio
    async def test_update_provider_loads_stored_credential(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "sk-stored",
            "base_url": "https://stored.url",
        }
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("deepseek")
        assert vm.state.api_key == "sk-stored"
        assert vm.state.api_key_modified is False
        # stored base_url 优先于 provider 默认
        assert vm.state.base_url == "https://stored.url"
        mock_config_handler.get_provider_credential.assert_called_with("deepseek", fallback_to_global=False)

    @pytest.mark.asyncio
    async def test_update_provider_notifies_with_hint(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        await vm.update_provider("deepseek")
        # 多次 _set_state：provider 切换 + hint
        assert len(received) >= 2
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_switch_provider_hint"
        assert vm.state.status_type == "info"

    @pytest.mark.asyncio
    async def test_update_provider_unknown_provider_falls_back_to_id(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        """未知 provider：name 回退为 provider_id，model 为空。"""
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("unknown-provider")
        assert vm.state.provider == "unknown-provider"
        assert vm.state.model == ""
        assert vm.state.show_refresh_button is False  # 不在 MODELS_API_COMPATIBLE


# --- verify_connection (async) ---


class TestLLMConfigPanelViewModelVerifyConnection:
    @pytest.mark.asyncio
    async def test_verify_connection_empty_api_key_returns_false(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(api_key="")  # type: ignore[attr-defined]
        result = await vm.verify_connection()
        assert result is False
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_test_need_key"

    @pytest.mark.asyncio
    async def test_verify_connection_missing_model_returns_false(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(model="", custom_model="")  # type: ignore[attr-defined]
        result = await vm.verify_connection()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "wizard_err_provider_model_required"

    @pytest.mark.asyncio
    async def test_verify_connection_success(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        result = await vm.verify_connection()
        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_test_success"
        assert vm.state.is_verifying is False
        mock_test_connection.assert_awaited_once()
        call_kwargs = mock_test_connection.call_args.kwargs
        assert call_kwargs["provider"] == "deepseek"
        assert call_kwargs["model"] == "deepseek-chat"
        assert call_kwargs["base_url"] == "https://api.deepseek.com"
        assert call_kwargs["api_key"] == "sk-test"

    @pytest.mark.asyncio
    async def test_verify_connection_failure_shows_error(self, mock_test_connection, mock_config_handler):
        mock_test_connection.return_value = {"success": False, "message": "llm_err_auth_failed"}
        vm = _make_vm(mock_test_connection)
        result = await vm.verify_connection()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message == Message("llm_err_auth_failed")

    @pytest.mark.asyncio
    async def test_verify_connection_reentry_guard(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(is_verifying=True)  # type: ignore[attr-defined]
        result = await vm.verify_connection()
        assert result is False
        mock_test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_connection_azure_validates_fields(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("azure")
        vm.update_api_key("sk-azure")
        vm.update_azure_deployment("my-deployment")  # model 非空，通过 model 检查
        # resource_name 为空 → azure 字段校验失败
        result = await vm.verify_connection()
        assert result is False
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_azure_need_resource"

    @pytest.mark.asyncio
    async def test_verify_connection_azure_success_passes_azure_kwargs(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("azure")
        vm.update_api_key("sk-azure")
        vm.update_azure_resource("my-resource")
        vm.update_azure_deployment("my-deployment")
        vm.update_azure_version("2024-10-21")
        result = await vm.verify_connection()
        assert result is True
        mock_test_connection.assert_awaited_once()
        call_kwargs = mock_test_connection.call_args.kwargs
        assert call_kwargs["provider"] == "azure"
        assert call_kwargs["model"] == "my-deployment"
        assert call_kwargs["base_url"] == ""
        assert call_kwargs["azure_resource_name"] == "my-resource"
        assert call_kwargs["api_version"] == "2024-10-21"

    @pytest.mark.asyncio
    async def test_verify_connection_exception_returns_false(self, mock_test_connection, mock_config_handler):
        mock_test_connection.side_effect = RuntimeError("network error")
        vm = _make_vm(mock_test_connection)
        result = await vm.verify_connection()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_connection_triggers_on_loading_change(self, mock_test_connection, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(mock_test_connection, on_loading_change=on_loading_change)
        await vm.verify_connection()
        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)


# --- save_config (async) ---


class TestLLMConfigPanelViewModelSaveConfig:
    @pytest.mark.asyncio
    async def test_save_config_success(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        result = await vm.save_config()
        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "settings_verify_success"
        assert vm.state.is_saving is False
        mock_config_handler.save_llm_config.assert_called_once()
        call_kwargs = mock_config_handler.save_llm_config.call_args.kwargs
        assert call_kwargs["provider"] == "deepseek"
        assert call_kwargs["model"] == "deepseek-chat"
        assert call_kwargs["base_url"] == "https://api.deepseek.com"
        assert call_kwargs["api_key"] is None  # api_key_modified=False → None
        # 非已知 model → 写入 custom_models 历史
        assert call_kwargs["custom_models"] == {"deepseek": ["deepseek-chat"]}

    @pytest.mark.asyncio
    async def test_save_config_resets_api_key_modified(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        vm.update_api_key("sk-new")  # 标记 modified
        assert vm.state.api_key_modified is True
        result = await vm.save_config()
        assert result is True
        assert vm.state.api_key_modified is False
        # modified 时传具体 key（strip）
        call_kwargs = mock_config_handler.save_llm_config.call_args.kwargs
        assert call_kwargs["api_key"] == "sk-new"

    @pytest.mark.asyncio
    async def test_save_config_azure_success(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("azure")
        vm.update_azure_resource("my-resource")
        vm.update_azure_deployment("my-deployment")
        vm.update_azure_version("2024-10-21")
        vm.update_api_key("sk-azure")
        result = await vm.save_config()
        assert result is True
        mock_config_handler.save_llm_config.assert_called_once()
        call_kwargs = mock_config_handler.save_llm_config.call_args.kwargs
        assert call_kwargs["provider"] == "azure"
        assert call_kwargs["model"] == "my-deployment"
        assert call_kwargs["base_url"] == ""
        assert call_kwargs["api_key"] == "sk-azure"
        assert call_kwargs["api_version"] == "2024-10-21"
        assert call_kwargs["azure_resource_name"] == "my-resource"
        assert call_kwargs["azure_deployment_name"] == "my-deployment"
        # azure 不写 custom_models
        assert "custom_models" not in call_kwargs

    @pytest.mark.asyncio
    async def test_save_config_azure_validation_failure(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_test_connection)
        await vm.update_provider("azure")
        vm.update_azure_deployment("my-deployment")  # resource_name 为空
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_azure_need_resource"
        assert vm.state.is_saving is False
        mock_config_handler.save_llm_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_failure_returns_false(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_llm_config.side_effect = RuntimeError("DB error")
        vm = _make_vm(mock_test_connection)
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "settings_save_failed"
        assert vm.state.is_saving is False

    @pytest.mark.asyncio
    async def test_save_config_reentry_guard(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_test_connection)
        vm._set_state(is_saving=True)  # type: ignore[attr-defined]
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_saving_in_progress"
        mock_config_handler.save_llm_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_triggers_on_save(self, mock_test_connection, mock_config_handler, mock_thread_pool):
        on_save = MagicMock()
        vm = _make_vm(mock_test_connection, on_save=on_save)
        result = await vm.save_config()
        assert result is True
        on_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_triggers_on_reload_service(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        on_reload_service = AsyncMock()
        vm = _make_vm(mock_test_connection, on_reload_service=on_reload_service)
        result = await vm.save_config()
        assert result is True
        on_reload_service.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_config_removes_primary_from_failover(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        """failover 同步：从 llm_failover_models 移除主供应商模型。"""
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["deepseek/model-a", "openai/model-b"]}
        vm = _make_vm(mock_test_connection)
        result = await vm.save_config()
        assert result is True
        mock_config_handler.save_config.assert_called_once_with({"llm_failover_models": ["openai/model-b"]})

    @pytest.mark.asyncio
    async def test_save_config_syncs_credential_to_failover(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        """provider 在 failover_models 中时，同步凭证到 llm_provider_credentials。"""
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["deepseek/model-a"]}
        vm = _make_vm(mock_test_connection)
        vm.update_api_key("sk-new")
        result = await vm.save_config()
        assert result is True
        mock_config_handler.save_provider_credential.assert_called_once()
        call_kwargs = mock_config_handler.save_provider_credential.call_args.kwargs
        assert call_kwargs["provider"] == "deepseek"
        assert call_kwargs["api_key"] == "sk-new"

    @pytest.mark.asyncio
    async def test_save_config_skips_custom_models_for_known_model(
        self, mock_test_connection, mock_config_handler, mock_thread_pool
    ):
        """provider 已知 model 不写入 custom_models 历史。"""
        vm = _make_vm(mock_test_connection)
        vm._set_state(model="deepseek-v4-flash")  # type: ignore[attr-defined]  # 已知 model
        result = await vm.save_config()
        assert result is True
        call_kwargs = mock_config_handler.save_llm_config.call_args.kwargs
        assert "custom_models" not in call_kwargs


# --- refresh_models (async) ---


def _make_httpx_client(data: list | None = None, *, get_side_effect=None) -> MagicMock:
    """构造 httpx.AsyncClient mock（async context manager，__aenter__ 返回自身）。

    data 非 None 时 get 返回带该 data 的 response；get_side_effect 非 None 时 get 抛异常。
    """
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": data if data is not None else []}
        mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


class TestLLMConfigPanelViewModelRefreshModels:
    @pytest.mark.asyncio
    async def test_refresh_models_success(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        mock_client = _make_httpx_client([{"id": "model-a"}, {"id": "model-b"}])
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_refresh_success"
        assert vm.state.status_message.params["count"] == 2
        # 当前 model 不在列表 → 切到第一个
        assert vm.state.model == "model-a"
        assert vm.state.is_refreshing is False

    @pytest.mark.asyncio
    async def test_refresh_models_keeps_current_model_if_in_list(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(model="model-b")  # type: ignore[attr-defined]
        mock_client = _make_httpx_client([{"id": "model-a"}, {"id": "model-b"}])
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        assert vm.state.model == "model-b"  # 保留当前

    @pytest.mark.asyncio
    async def test_refresh_models_empty_api_key(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(api_key="")  # type: ignore[attr-defined]
        await vm.refresh_models()
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_refresh_need_key"

    @pytest.mark.asyncio
    async def test_refresh_models_empty_base_url(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm._set_state(base_url="")  # type: ignore[attr-defined]
        await vm.refresh_models()
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_refresh_need_url"

    @pytest.mark.asyncio
    async def test_refresh_models_empty_response(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        mock_client = _make_httpx_client([])
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "llm_refresh_empty"

    @pytest.mark.asyncio
    async def test_refresh_models_http_failure(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        mock_client = _make_httpx_client(get_side_effect=RuntimeError("HTTP error"))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "_raw_msg_"
        assert vm.state.is_refreshing is False

    @pytest.mark.asyncio
    async def test_refresh_models_triggers_on_loading_change(self, mock_test_connection, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(mock_test_connection, on_loading_change=on_loading_change)
        mock_client = _make_httpx_client([{"id": "model-a"}])
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_refresh_models_uses_normalized_base_url(self, mock_test_connection, mock_config_handler):
        """refresh_models 应对 base_url 做 normalize 后拼接 /models。"""
        vm = _make_vm(mock_test_connection)
        vm._set_state(base_url="https://api.deepseek.com/chat/completions")  # type: ignore[attr-defined]
        mock_client = _make_httpx_client([{"id": "model-a"}])
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "utils.proxy_manager.ProxyManager.get_httpx_proxy_config",
                return_value={},
            ),
        ):
            await vm.refresh_models()
        # /chat/completions 被 strip → /models 拼接到 https://api.deepseek.com
        call_args = mock_client.get.call_args
        assert call_args.args[0] == "https://api.deepseek.com/models"
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


# --- static methods ---


class TestLLMConfigPanelViewModelStaticMethods:
    def test_normalize_base_url_empty(self):
        assert LLMConfigPanelViewModel._normalize_base_url("") == ""

    def test_normalize_base_url_strips_trailing_slash(self):
        assert LLMConfigPanelViewModel._normalize_base_url("https://api.x.com/") == "https://api.x.com"

    def test_normalize_base_url_adds_scheme(self):
        assert LLMConfigPanelViewModel._normalize_base_url("api.x.com") == "https://api.x.com"

    def test_normalize_base_url_strips_chat_completions(self):
        assert LLMConfigPanelViewModel._normalize_base_url("https://api.x.com/chat/completions") == "https://api.x.com"

    def test_normalize_base_url_strips_completions(self):
        assert LLMConfigPanelViewModel._normalize_base_url("https://api.x.com/completions") == "https://api.x.com"

    def test_normalize_base_url_strips_embeddings(self):
        assert LLMConfigPanelViewModel._normalize_base_url("https://api.x.com/embeddings") == "https://api.x.com"

    def test_normalize_base_url_preserves_compatible_mode_path(self):
        """保留 /compatible-mode/v1 这类必要 base path。"""
        assert (
            LLMConfigPanelViewModel._normalize_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def test_normalize_base_url_preserves_http_scheme(self):
        assert LLMConfigPanelViewModel._normalize_base_url("http://localhost:8080/v1") == "http://localhost:8080/v1"

    def test_build_custom_models_update_empty_model(self, mock_config_handler):
        assert LLMConfigPanelViewModel._build_custom_models_update("deepseek", "") is None

    def test_build_custom_models_update_azure(self, mock_config_handler):
        assert LLMConfigPanelViewModel._build_custom_models_update("azure", "gpt-4", is_azure=True) is None

    def test_build_custom_models_update_known_model(self, mock_config_handler):
        """provider 已知 model 不写入 custom_models。"""
        assert LLMConfigPanelViewModel._build_custom_models_update("deepseek", "deepseek-v4-flash") is None

    def test_build_custom_models_update_appends_new_model(self, mock_config_handler):
        mock_config_handler.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "",
            "api_key": "",
            "custom_models": {},
        }
        result = LLMConfigPanelViewModel._build_custom_models_update("deepseek", "my-custom-model")
        assert result == {"deepseek": ["my-custom-model"]}

    def test_build_custom_models_update_custom_provider(self, mock_config_handler):
        mock_config_handler.get_llm_config.return_value = {
            "provider": "custom",
            "model": "",
            "base_url": "",
            "api_key": "",
            "custom_models": {},
        }
        result = LLMConfigPanelViewModel._build_custom_models_update("custom", "my-model")
        assert result == {"custom": ["my-model"]}

    def test_build_custom_models_update_dedupes(self, mock_config_handler):
        mock_config_handler.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "",
            "api_key": "",
            "custom_models": {"deepseek": ["existing"]},
        }
        result = LLMConfigPanelViewModel._build_custom_models_update("deepseek", "existing")
        assert result == {"deepseek": ["existing"]}  # 不重复

    def test_build_custom_models_update_respects_max_limit(self, mock_config_handler):
        """超过 _MAX_CUSTOM_MODELS 上限时裁剪到最近 N 条。"""
        from ui.viewmodels.llm_config_panel_view_model import _MAX_CUSTOM_MODELS

        existing = [f"model-{i}" for i in range(_MAX_CUSTOM_MODELS)]
        mock_config_handler.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "",
            "api_key": "",
            "custom_models": {"deepseek": existing},
        }
        result = LLMConfigPanelViewModel._build_custom_models_update("deepseek", "new-model")
        assert result is not None
        assert len(result["deepseek"]) == _MAX_CUSTOM_MODELS
        assert result["deepseek"][-1] == "new-model"
        assert "model-0" not in result["deepseek"]  # 最早的被裁剪

    def test_remove_primary_from_failover_removes_matching(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["deepseek/model-a", "openai/model-b", "deepseek/model-c"]
        }
        LLMConfigPanelViewModel._remove_primary_from_failover("deepseek")
        mock_config_handler.save_config.assert_called_once_with({"llm_failover_models": ["openai/model-b"]})

    def test_remove_primary_from_failover_no_change(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["openai/model-b"]}
        LLMConfigPanelViewModel._remove_primary_from_failover("deepseek")
        mock_config_handler.save_config.assert_not_called()

    def test_remove_primary_from_failover_empty_list(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {}
        LLMConfigPanelViewModel._remove_primary_from_failover("deepseek")
        mock_config_handler.save_config.assert_not_called()

    def test_sync_provider_credential_to_failover_when_provider_in_failover(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["deepseek/model-a"]}
        LLMConfigPanelViewModel._sync_provider_credential_to_failover(
            "deepseek", "sk-new", "https://api.x.com", ["model-a"]
        )
        mock_config_handler.save_provider_credential.assert_called_once()
        call_kwargs = mock_config_handler.save_provider_credential.call_args.kwargs
        assert call_kwargs["provider"] == "deepseek"
        assert call_kwargs["api_key"] == "sk-new"
        assert call_kwargs["base_url"] == "https://api.x.com"
        assert call_kwargs["models"] == ["model-a"]

    def test_sync_provider_credential_to_failover_when_provider_not_in_failover(self, mock_config_handler):
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["openai/model-b"]}
        LLMConfigPanelViewModel._sync_provider_credential_to_failover("deepseek", "sk-new", "https://api.x.com", None)
        mock_config_handler.save_provider_credential.assert_not_called()

    def test_sync_provider_credential_to_failover_api_key_none_reads_existing(self, mock_config_handler):
        """api_key=None 表示未修改，读取现有凭证避免覆盖。"""
        mock_config_handler.load_config.return_value = {"llm_failover_models": ["deepseek/model-a"]}
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "sk-existing",
            "base_url": "old",
        }
        LLMConfigPanelViewModel._sync_provider_credential_to_failover("deepseek", None, "https://api.x.com", None)
        mock_config_handler.get_provider_credential.assert_called_once_with("deepseek")
        call_kwargs = mock_config_handler.save_provider_credential.call_args.kwargs
        assert call_kwargs["api_key"] == "sk-existing"

    def test_sync_provider_credential_to_failover_swallows_exception(self, mock_config_handler):
        """load_config 异常不应抛出（内部 try/except）。"""
        mock_config_handler.load_config.side_effect = RuntimeError("boom")
        # 不应抛异常
        LLMConfigPanelViewModel._sync_provider_credential_to_failover("deepseek", "sk-new", "https://api.x.com", None)
        mock_config_handler.save_provider_credential.assert_not_called()


# --- dispose ---


class TestLLMConfigPanelViewModelDispose:
    def test_dispose_clears_subscribers(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.update_model("new")
        assert len(received) == 0

    def test_dispose_idempotent(self, mock_test_connection, mock_config_handler):
        vm = _make_vm(mock_test_connection)
        vm.subscribe(lambda s: None)
        vm.dispose()
        vm.dispose()  # 不应抛异常

    def test_dispose_then_subscribe_works(self, mock_test_connection, mock_config_handler):
        """dispose 后仍可重新订阅（清理的是旧订阅者列表）。"""
        vm = _make_vm(mock_test_connection)
        vm.dispose()
        received: list[LLMConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model("new")
        assert len(received) == 1
