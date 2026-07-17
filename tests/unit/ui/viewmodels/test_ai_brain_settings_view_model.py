"""AIBrainSettingsViewModel 单元测试 (Task 5.2 TDD Red).

测试 VM state/commands, 不依赖 Flet 渲染。
覆盖：
- frozen state 不可变 (AIBrainSettingsState)
- 三阶段保存状态机: idle → saving → success / error
- 验证/保存/重载状态机 (validate → persist → reload AIService)
- 重复提交检测 (is_saving=True 时拒绝)
- 构造注入 LLMConfigPanelViewModel/FailoverConfigPanelViewModel/LocalModelConfigPanelViewModel
- 同步阻塞操作走 ThreadPoolManager (R16)
- R2 CancelledError 显式 raise
- subscribe / _notify / dispose
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.ai_brain_settings_view_model import (
    AIBrainSettingsState,
    AIBrainSettingsViewModel,
)
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    with patch("ui.viewmodels.ai_brain_settings_view_model.ConfigHandler") as m:
        m.get_ai_max_candidates.return_value = 30
        m.get_strategy_min_turnover.return_value = 2.0
        m.get_ai_max_concurrent_analysis.return_value = 5
        m.get_ai_news_max_concurrent.return_value = 1
        m.get_ai_system_prompt.return_value = "default prompt"
        m.get_ai_news_prompt.return_value = "default news prompt"
        m.save_local_ai_config.return_value = True
        m.save_config.return_value = True
        m.save_ai_system_prompt.return_value = True
        m.set_ai_news_prompt.return_value = True
        yield m


@pytest.fixture
def mock_thread_pool():
    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.ai_brain_settings_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_config_handler,
    *,
    llm_vm=None,
    failover_vm=None,
    local_vm=None,
) -> AIBrainSettingsViewModel:
    return AIBrainSettingsViewModel(
        llm_vm=llm_vm or MagicMock(save_config=AsyncMock(return_value=True)),
        failover_vm=failover_vm or MagicMock(save_config=AsyncMock(return_value=True)),
        local_vm=local_vm or MagicMock(get_current_config=MagicMock(return_value={})),
    )


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        with pytest.raises(FrozenInstanceError):
            vm.state.max_candidates_value = "100"  # type: ignore[misc]

    def test_state_default_values(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        assert vm.state.max_candidates_value == "30"
        assert vm.state.min_turnover_value == "2.0"
        assert vm.state.ai_concurrency_value == "5"
        assert vm.state.news_concurrency_value == "1"
        assert vm.state.ai_prompt_value == "default prompt"
        assert vm.state.news_prompt_value == "default news prompt"
        assert vm.state.save_state == "idle"


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[AIBrainSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_max_candidates_value("50")
        assert len(received) == 1
        assert received[0].max_candidates_value == "50"

    def test_dispose_clears_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[AIBrainSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.set_max_candidates_value("50")
        assert len(received) == 0


# --- save_ai_settings 三阶段状态机 ---


class TestSaveAiSettingsStateMachine:
    @pytest.mark.asyncio
    async def test_save_success_transitions_to_success(self, mock_config_handler, mock_thread_pool):
        # local_vm returns valid config
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {
            "model_path": "/tmp/model.gguf",
            "timeout": 300,
            "n_threads": 4,
            "n_batch": 512,
            "n_ctx": 2048,
            "flash_attn": False,
            "n_gpu_layers": 0,
        }
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        # patch AIService.reload_config
        with patch("services.ai_service.AIService") as mock_ai_svc:
            mock_ai_svc.return_value.reload_config = AsyncMock()
            with patch("services.local_model_manager.LocalModelManager") as mock_lm:
                mock_lm.commit_verification_if_active = MagicMock()
                mock_lm.get_instance = AsyncMock(return_value=mock_lm.return_value)
                # local_path empty → skip file check
                local_vm.get_current_config.return_value["model_path"] = ""
                vm = _make_vm(
                    mock_config_handler,
                    llm_vm=llm_vm,
                    local_vm=local_vm,
                )
                result = await vm.save_ai_settings()
                assert result is True
                assert vm.state.save_state == "success"
                llm_vm.save_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_validation_failure_returns_error_state(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        # invalid range: max_candidates > 500
        vm.set_max_candidates_value("9999")
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_llm_failure_returns_error_state(self, mock_config_handler, mock_thread_pool):
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=False)
        vm = _make_vm(mock_config_handler, llm_vm=llm_vm)
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_local_config_failure_returns_error_state(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_local_ai_config.return_value = False
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {"model_path": ""}
        vm = _make_vm(mock_config_handler, llm_vm=llm_vm, local_vm=local_vm)
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_io_exception_returns_error_state(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=RuntimeError("disk full"))
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {"model_path": ""}
        vm = _make_vm(mock_config_handler, llm_vm=llm_vm, local_vm=local_vm)
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_cancelled_error_propagates(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {"model_path": ""}
        vm = _make_vm(mock_config_handler, llm_vm=llm_vm, local_vm=local_vm)
        with pytest.raises(asyncio.CancelledError):
            await vm.save_ai_settings()

    @pytest.mark.asyncio
    async def test_save_duplicate_submit_returns_false(self, mock_config_handler, mock_thread_pool):
        # 模拟 in-flight 状态：is_saving=True
        from dataclasses import replace

        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {"model_path": ""}
        vm = _make_vm(mock_config_handler, llm_vm=llm_vm, local_vm=local_vm)
        vm._state = replace(vm._state, save_state="saving")
        result = await vm.save_ai_settings()
        assert result is False
        llm_vm.save_config.assert_not_called()


# --- ThreadPoolManager 调用契约 (R16) ---


class TestThreadPoolOffloadContract:
    @pytest.mark.asyncio
    async def test_save_ai_settings_uses_thread_pool_for_persist(self, mock_config_handler, mock_thread_pool):
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {"model_path": ""}
        with patch("services.ai_service.AIService") as mock_ai_svc:
            mock_ai_svc.return_value.reload_config = AsyncMock()
            with patch("services.local_model_manager.LocalModelManager") as mock_lm:
                mock_lm.commit_verification_if_active = MagicMock()
                vm = _make_vm(
                    mock_config_handler,
                    llm_vm=llm_vm,
                    local_vm=local_vm,
                )
                await vm.save_ai_settings()
                # ThreadPoolManager.run_async 至少调用一次 (save_local_ai_config + save_config + save_ai_system_prompt + set_ai_news_prompt)
                assert mock_thread_pool.run_async.call_count >= 1
                args, _ = mock_thread_pool.run_async.call_args
                assert args[0] is TaskType.IO
