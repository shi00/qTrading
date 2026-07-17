"""LocalModelConfigPanelViewModel 单元测试（Phase 3.2.4 LocalModelConfigPanel 声明式重写）。

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，消费方（AIBrainTab/OnboardingWizard）直接实例化以调用 commands。
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.local_model_config_panel_view_model import (
    LocalModelConfigPanelViewModel,
    LocalModelConfigState,
)
from ui.viewmodels import Message

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_verify_model():
    """注入的 on_verify_model 回调 mock，默认返回成功。"""
    return AsyncMock(return_value=True)


@pytest.fixture
def mock_config_handler(monkeypatch):
    """Mock ConfigHandler 模块级 patch（VM 构造时同步加载配置）。"""
    m = MagicMock()
    m.get_local_ai_config.return_value = {
        "local_model_path": "",
        "n_threads": 4,
        "n_gpu_layers": -1,
        "n_batch": 512,
        "n_ctx": 4096,
        "flash_attn": True,
    }
    m.get_local_ai_timeout.return_value = 300
    m.save_local_ai_config.return_value = True
    monkeypatch.setattr("ui.viewmodels.local_model_config_panel_view_model.ConfigHandler", m)
    yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough（offload 同步 IO）。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.local_model_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_verify_model,
    *,
    on_save: Callable[[], None] | None = None,
    on_verify_success: Callable[[], None] | None = None,
    on_change: Callable[[], None] | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
    show_internal_loading: bool = True,
) -> LocalModelConfigPanelViewModel:
    """构造 VM（在 mock_config_handler 上下文中调用）。"""
    return LocalModelConfigPanelViewModel(
        on_verify_model=mock_verify_model,
        on_save=on_save,
        on_verify_success=on_verify_success,
        on_change=on_change,
        on_loading_change=on_loading_change,
        show_internal_loading=show_internal_loading,
    )


@pytest.fixture
def vm(mock_verify_model, mock_config_handler) -> LocalModelConfigPanelViewModel:
    """默认 VM 实例（使用 mock_config_handler 默认配置）。"""
    return LocalModelConfigPanelViewModel(on_verify_model=mock_verify_model)


# --- Init / 初始 state ---


class TestLocalModelConfigPanelViewModelInit:
    def test_initial_state_values(self, mock_verify_model, mock_config_handler):
        """默认配置加载后 state 字段正确。"""
        vm = _make_vm(mock_verify_model)
        assert vm.state.model_path == ""
        assert vm.state.timeout == "300"
        assert vm.state.n_threads == 4
        assert vm.state.n_gpu_layers == -1  # auto
        assert vm.state.n_batch == 512
        assert vm.state.n_ctx == 4096
        assert vm.state.flash_attn is True
        assert vm.state.is_verifying is False
        assert vm.state.is_saving is False
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"

    def test_initial_state_loads_custom_config(self, mock_verify_model, mock_config_handler):
        """ConfigHandler 返回自定义配置时正确加载到 state。"""
        mock_config_handler.get_local_ai_config.return_value = {
            "local_model_path": "/models/test.gguf",
            "n_threads": 8,
            "n_gpu_layers": 2,
            "n_batch": 1024,
            "n_ctx": 8192,
            "flash_attn": False,
        }
        mock_config_handler.get_local_ai_timeout.return_value = 120
        vm = _make_vm(mock_verify_model)
        assert vm.state.model_path == "/models/test.gguf"
        assert vm.state.timeout == "120"
        assert vm.state.n_threads == 8
        assert vm.state.n_gpu_layers == 2
        assert vm.state.n_batch == 1024
        assert vm.state.n_ctx == 8192
        assert vm.state.flash_attn is False

    def test_subscribe_returns_unsubscribe_callable(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        unsub = vm.subscribe(lambda s: None)
        assert callable(unsub)

    def test_subscribe_receives_notification_on_next_change(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model_path("/new/path.gguf")
        assert len(received) == 1
        assert received[0].model_path == "/new/path.gguf"


# --- State snapshot / _set_state / _notify ---


class TestLocalModelConfigPanelViewModelState:
    def test_state_is_frozen(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        with pytest.raises(FrozenInstanceError):
            vm.state.model_path = "modified"  # type: ignore[misc]

    def test_set_state_updates_field(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm._set_state(model_path="/updated.gguf")  # type: ignore[attr-defined]
        assert vm.state.model_path == "/updated.gguf"

    def test_set_state_notifies_subscribers(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._set_state(n_threads=16)  # type: ignore[attr-defined]
        assert len(received) == 1
        assert received[0].n_threads == 16

    def test_notify_calls_all_subscribers(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received_a: list[LocalModelConfigState] = []
        received_b: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received_a.append(s))
        vm.subscribe(lambda s: received_b.append(s))
        vm.update_model_path("/x.gguf")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_unsubscribe_stops_receiving(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.update_model_path("/x.gguf")
        assert len(received) == 0


# --- reload_config ---


class TestLocalModelConfigPanelViewModelReload:
    def test_reload_config_reloads_from_config_handler(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/modified.gguf")
        assert vm.state.model_path == "/modified.gguf"
        # ConfigHandler 返回新配置
        mock_config_handler.get_local_ai_config.return_value = {
            "local_model_path": "/new/path.gguf",
            "n_threads": 8,
            "n_gpu_layers": 2,
            "n_batch": 1024,
            "n_ctx": 8192,
            "flash_attn": False,
        }
        mock_config_handler.get_local_ai_timeout.return_value = 60
        vm.reload_config()
        assert vm.state.model_path == "/new/path.gguf"
        assert vm.state.timeout == "60"
        assert vm.state.n_threads == 8

    def test_reload_config_notifies_subscribers(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.reload_config()
        assert len(received) == 1


# --- Update fields ---


class TestLocalModelConfigPanelViewModelUpdateFields:
    def test_update_model_path(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/new/path.gguf")
        assert vm.state.model_path == "/new/path.gguf"

    def test_update_timeout(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_timeout("120")
        assert vm.state.timeout == "120"

    def test_update_threads(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_threads(8)
        assert vm.state.n_threads == 8

    def test_update_threads_accepts_float(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_threads(4.5)
        assert vm.state.n_threads == 4  # int 截断

    def test_update_gpu_auto_true_sets_negative_one(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_gpu_layers(5)  # 先设非 auto
        assert vm.state.n_gpu_layers == 5
        vm.update_gpu_auto(True)
        assert vm.state.n_gpu_layers == -1

    def test_update_gpu_auto_false_sets_zero(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_gpu_auto(False)
        assert vm.state.n_gpu_layers == 0

    def test_update_gpu_layers(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_gpu_layers(10)
        assert vm.state.n_gpu_layers == 10

    def test_update_batch_valid(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_batch("2048")
        assert vm.state.n_batch == 2048

    def test_update_batch_invalid_no_change(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        original_batch = vm.state.n_batch
        vm.update_batch("not_a_number")
        assert vm.state.n_batch == original_batch

    def test_update_ctx_valid(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_ctx("8192")
        assert vm.state.n_ctx == 8192

    def test_update_ctx_invalid_no_change(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        original_ctx = vm.state.n_ctx
        vm.update_ctx("invalid")
        assert vm.state.n_ctx == original_ctx

    def test_update_flash_attn(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_flash_attn(False)
        assert vm.state.flash_attn is False

    def test_update_triggers_on_change_callback(self, mock_verify_model, mock_config_handler):
        on_change = MagicMock()
        vm = _make_vm(mock_verify_model, on_change=on_change)
        vm.update_model_path("/x.gguf")
        on_change.assert_called_once()

    def test_update_triggers_subscriber_notification(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model_path("/new.gguf")
        assert len(received) == 1
        assert received[0].model_path == "/new.gguf"


# --- get_current_config / set_config ---


class TestLocalModelConfigPanelViewModelGetSetConfig:
    def test_get_current_config_returns_all_fields(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("  /path/model.gguf  ")
        vm.update_timeout("60")
        vm.update_threads(6)
        vm.update_gpu_auto(False)
        vm.update_gpu_layers(10)
        vm.update_batch("2048")
        vm.update_ctx("16384")
        vm.update_flash_attn(False)
        config = vm.get_current_config()
        assert config["model_path"] == "/path/model.gguf"  # strip
        assert config["timeout"] == 60
        assert config["n_threads"] == 6
        assert config["n_gpu_layers"] == 10
        assert config["n_batch"] == 2048
        assert config["n_ctx"] == 16384
        assert config["flash_attn"] is False

    def test_get_current_config_invalid_timeout_falls_back_to_300(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_timeout("abc")
        config = vm.get_current_config()
        assert config["timeout"] == 300

    def test_get_current_config_empty_timeout_falls_back_to_300(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_timeout("")
        config = vm.get_current_config()
        assert config["timeout"] == 300

    def test_get_current_config_gpu_auto_returns_negative_one(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_gpu_auto(True)
        config = vm.get_current_config()
        assert config["n_gpu_layers"] == -1

    def test_set_config_updates_all_fields(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.set_config(
            {
                "model_path": "/new/model.gguf",
                "timeout": 200,
                "n_threads": 12,
                "n_gpu_layers": -1,
                "n_batch": 4096,
                "n_ctx": 32768,
                "flash_attn": True,
            }
        )
        assert vm.state.model_path == "/new/model.gguf"
        assert vm.state.timeout == "200"
        assert vm.state.n_threads == 12
        assert vm.state.n_gpu_layers == -1
        assert vm.state.n_batch == 4096
        assert vm.state.n_ctx == 32768
        assert vm.state.flash_attn is True


# --- _validate_for_verify ---


class TestLocalModelConfigPanelViewModelValidate:
    def test_validate_empty_path_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("")
        is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is False
        assert error is not None
        assert error.key == "wizard_err_model_required"

    def test_validate_nonexistent_path_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/nonexistent/model.gguf")
        with patch("os.path.exists", return_value=False):
            is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is False
        assert error is not None
        assert error.key == "wizard_err_model_not_found"

    def test_validate_wrong_extension_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.bin")
        with patch("os.path.exists", return_value=True):
            is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is False
        assert error is not None
        assert error.key == "wizard_err_model_format"

    def test_validate_invalid_timeout_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.gguf")
        vm.update_timeout("abc")
        with patch("os.path.exists", return_value=True):
            is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is False
        assert error is not None
        assert error.key == "ai_snack_invalid_range"

    def test_validate_timeout_out_of_range_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.gguf")
        vm.update_timeout("9999")
        with patch("os.path.exists", return_value=True):
            is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is False
        assert error is not None
        assert error.key == "ai_snack_invalid_range"

    def test_validate_valid_config_returns_true(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.gguf")
        vm.update_timeout("300")
        with patch("os.path.exists", return_value=True):
            is_valid, error = vm._validate_for_verify()  # type: ignore[attr-defined]
        assert is_valid is True
        assert error is None


# --- verify_model (async) ---


class TestLocalModelConfigPanelViewModelVerifyModel:
    @pytest.mark.asyncio
    async def test_verify_model_empty_path_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("")
        result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "wizard_err_model_required"
        mock_verify_model.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_model_nonexistent_path_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/nonexistent/model.gguf")
        with patch("os.path.exists", return_value=False):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_verify_model_wrong_extension_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.bin")
        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_verify_model_invalid_timeout_returns_false(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/model.gguf")
        vm.update_timeout("abc")
        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_verify_model_success_returns_true(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            result = await vm.verify_model()
        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "wizard_model_configured"
        assert vm.state.is_verifying is False
        mock_verify_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_verify_model_callback_returns_false_shows_error(self, mock_verify_model, mock_config_handler):
        mock_verify_model.return_value = False
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "wizard_err_model_load_failed"

    @pytest.mark.asyncio
    async def test_verify_model_reentry_guard(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm._set_state(is_verifying=True)  # type: ignore[attr-defined]
        vm.update_model_path("/models/test.gguf")
        result = await vm.verify_model()
        assert result is False
        mock_verify_model.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_model_no_callback_returns_false(self, mock_config_handler):
        """on_verify_model=None 时返回 False 并显示错误。"""
        vm = LocalModelConfigPanelViewModel(on_verify_model=None)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_verify_model_exception_returns_false(self, mock_verify_model, mock_config_handler):
        mock_verify_model.side_effect = RuntimeError("load error")
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            result = await vm.verify_model()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_model_triggers_on_loading_change(self, mock_verify_model, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(mock_verify_model, on_loading_change=on_loading_change)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            await vm.verify_model()
        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_verify_model_triggers_on_verify_success(self, mock_verify_model, mock_config_handler):
        on_verify_success = MagicMock()
        vm = _make_vm(mock_verify_model, on_verify_success=on_verify_success)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            result = await vm.verify_model()
        assert result is True
        on_verify_success.assert_called_once()


# --- save_config (async) ---


class TestLocalModelConfigPanelViewModelSaveConfig:
    @pytest.mark.asyncio
    async def test_save_config_success(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("120")
        vm.update_threads(8)
        vm.update_gpu_auto(False)
        vm.update_gpu_layers(2)
        vm.update_batch("1024")
        vm.update_ctx("8192")
        vm.update_flash_attn(True)
        result = await vm.save_config()
        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.is_saving is False
        mock_config_handler.save_local_ai_config.assert_called_once_with(
            model_path="/models/test.gguf",
            timeout=120,
            n_threads=8,
            n_batch=1024,
            n_ctx=8192,
            flash_attn=True,
            n_gpu_layers=2,
        )

    @pytest.mark.asyncio
    async def test_save_config_gpu_auto_saves_negative_one(
        self, mock_verify_model, mock_config_handler, mock_thread_pool
    ):
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_gpu_auto(True)
        await vm.save_config()
        call_kwargs = mock_config_handler.save_local_ai_config.call_args.kwargs
        assert call_kwargs["n_gpu_layers"] == -1

    @pytest.mark.asyncio
    async def test_save_config_failure_returns_false(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_local_ai_config.return_value = False
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_snack_save_err"
        assert vm.state.is_saving is False

    @pytest.mark.asyncio
    async def test_save_config_reentry_guard(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_verify_model)
        vm._set_state(is_saving=True)  # type: ignore[attr-defined]
        result = await vm.save_config()
        assert result is False
        mock_config_handler.save_local_ai_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_triggers_on_save(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        on_save = MagicMock()
        vm = _make_vm(mock_verify_model, on_save=on_save)
        vm.update_model_path("/models/test.gguf")
        result = await vm.save_config()
        assert result is True
        on_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_triggers_on_loading_change(
        self, mock_verify_model, mock_config_handler, mock_thread_pool
    ):
        on_loading_change = MagicMock()
        vm = _make_vm(
            mock_verify_model,
            on_loading_change=on_loading_change,
            show_internal_loading=True,
        )
        vm.update_model_path("/models/test.gguf")
        await vm.save_config()
        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_save_config_skips_loading_change_when_internal_disabled(
        self, mock_verify_model, mock_config_handler, mock_thread_pool
    ):
        """show_internal_loading=False 时不触发 on_loading_change。"""
        on_loading_change = MagicMock()
        vm = _make_vm(
            mock_verify_model,
            on_loading_change=on_loading_change,
            show_internal_loading=False,
        )
        vm.update_model_path("/models/test.gguf")
        await vm.save_config()
        on_loading_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_exception_returns_false(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_local_ai_config.side_effect = RuntimeError("DB error")
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        result = await vm.save_config()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.is_saving is False

    @pytest.mark.asyncio
    async def test_save_config_clamps_timeout_to_range(self, mock_verify_model, mock_config_handler, mock_thread_pool):
        """timeout 超出 [1, 3600] 范围时 clamp 到边界。"""
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("99999")
        await vm.save_config()
        call_kwargs = mock_config_handler.save_local_ai_config.call_args.kwargs
        assert call_kwargs["timeout"] == 3600

    @pytest.mark.asyncio
    async def test_save_config_commits_verification_if_active(
        self, mock_verify_model, mock_config_handler, mock_thread_pool
    ):
        """save_config 成功后调用 LocalModelManager.commit_verification_if_active。"""
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        with patch("services.local_model_manager.LocalModelManager.commit_verification_if_active") as mock_commit:
            await vm.save_config()
        mock_commit.assert_called_once()


# --- _raw_message static method ---


class TestLocalModelConfigPanelViewModelRawMessage:
    def test_raw_message_wraps_text(self):
        msg = LocalModelConfigPanelViewModel._raw_message("dynamic error text")
        assert isinstance(msg, Message)
        assert msg.key == "_raw_msg_"
        assert msg.params["default"] == "dynamic error text"


# --- dispose ---


class TestLocalModelConfigPanelViewModelDispose:
    def test_dispose_clears_subscribers(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.update_model_path("/new.gguf")
        assert len(received) == 0

    def test_dispose_idempotent(self, mock_verify_model, mock_config_handler):
        vm = _make_vm(mock_verify_model)
        vm.subscribe(lambda s: None)
        vm.dispose()
        vm.dispose()  # 不应抛异常

    def test_dispose_then_subscribe_works(self, mock_verify_model, mock_config_handler):
        """dispose 后仍可重新订阅（清理的是旧订阅者列表）。"""
        vm = _make_vm(mock_verify_model)
        vm.dispose()
        received: list[LocalModelConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_model_path("/new.gguf")
        assert len(received) == 1


# --- R9: 异常日志脱敏 + R2: CancelledError 传播 ---


# 测试用 secret 必须长度 >= 8 (DataSanitizer._MIN_SECRET_LEN)
_LEAKED_SECRET = "leaked_local_model_secret_abc"


class TestSanitizeErrorAndCancelledError:
    """R9: 异常日志中不得出现明文 secret; R2: CancelledError 必须传播。"""

    @pytest.fixture(autouse=True)
    def _reset_known_secrets(self):
        """每个测试前后清空 DataSanitizer._known_secrets，避免测试间状态污染。"""
        from utils.sanitizers import DataSanitizer

        DataSanitizer._reset_known_secrets()
        yield
        DataSanitizer._reset_known_secrets()

    @pytest.mark.asyncio
    async def test_verify_model_logs_sanitized_error(self, mock_verify_model, mock_config_handler, caplog):
        """verify_model 抛含 secret 的异常时，日志中不得出现明文 secret。

        模拟真实场景：secret 已注册到 DataSanitizer（如 AI API key），
        view_model 的 except 分支用 sanitize_error(e) 脱敏。
        """
        from utils.sanitizers import DataSanitizer

        DataSanitizer.register_secret(_LEAKED_SECRET)
        mock_verify_model.side_effect = RuntimeError(f"model load failed: {_LEAKED_SECRET}")
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
            caplog.at_level(logging.ERROR),
        ):
            result = await vm.verify_model()

        assert result is False
        assert _LEAKED_SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_save_config_logs_sanitized_error(
        self, mock_verify_model, mock_config_handler, mock_thread_pool, caplog
    ):
        """save_config 抛含 secret 的异常时，日志中不得出现明文 secret。"""
        from utils.sanitizers import DataSanitizer

        DataSanitizer.register_secret(_LEAKED_SECRET)
        mock_config_handler.save_local_ai_config.side_effect = RuntimeError(f"persist failed: {_LEAKED_SECRET}")
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        with caplog.at_level(logging.ERROR):
            result = await vm.save_config()

        assert result is False
        assert _LEAKED_SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_verify_model_propagates_cancelled_error(self, mock_verify_model, mock_config_handler):
        """R2: verify_model 中 await 抛 CancelledError 时必须传播，不被 except Exception 吞没。"""
        mock_verify_model.side_effect = asyncio.CancelledError()
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        vm.update_timeout("300")
        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await vm.verify_model()

    @pytest.mark.asyncio
    async def test_save_config_propagates_cancelled_error(
        self, mock_verify_model, mock_config_handler, mock_thread_pool
    ):
        """R2: save_config 中 await 抛 CancelledError 时必须传播，不被 except Exception 吞没。"""
        mock_thread_pool.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        vm = _make_vm(mock_verify_model)
        vm.update_model_path("/models/test.gguf")
        with pytest.raises(asyncio.CancelledError):
            await vm.save_config()
