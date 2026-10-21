"""AIBrainSettingsViewModel 单元测试 (S5 拆分后)。

测试 VM state/commands, 不依赖 Flet 渲染。
覆盖：
- frozen state 不可变 (AIBrainSettingsState)
- 三阶段保存状态机: idle → saving → success / error
- 验证/保存/重载状态机 (validate → persist → reload AIService)
- 重复提交检测 (save_state=saving 时拒绝)
- 同步阻塞操作走 ThreadPoolManager (R16)
- R2 CancelledError 显式 raise
- subscribe / dispose
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
        m.get_ai_system_prompt.return_value = "default prompt"
        m.get_ai_news_prompt.return_value = "default news prompt"
        m.is_ai_local_only_mode.return_value = False
        m.save_config.return_value = True
        m.save_ai_tuning_and_prompts.return_value = True
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


def _make_vm(mock_config_handler) -> AIBrainSettingsViewModel:
    return AIBrainSettingsViewModel()


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
        assert vm.state.ai_prompt_value == "default prompt"
        assert vm.state.news_prompt_value == "default news prompt"
        assert vm.state.save_state == "idle"
        assert vm.state.ai_local_only_mode is False
        assert vm.state.warning_message == ""


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
        with patch("services.ai_service.AIService") as mock_ai_svc:
            mock_ai_svc.return_value.reload_config = AsyncMock()
            vm = _make_vm(mock_config_handler)
            result = await vm.save_ai_settings()
        assert result is True
        assert vm.state.save_state == "success"
        # T3: 调优参数 + prompts 合并为单次原子保存调用（五参数一次提交）
        mock_config_handler.save_ai_tuning_and_prompts.assert_called_once_with(
            30, 2.0, False, "default prompt", "default news prompt"
        )
        # VM 不再绕过组合入口直接写盘（三次独立写链路已废弃）
        mock_config_handler.save_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_validation_failure_returns_error_state(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        # invalid range: max_candidates > 500
        vm.set_max_candidates_value("9999")
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_persist_failure_returns_error_state(self, mock_config_handler, mock_thread_pool):
        # T3: 组合入口返回 False（prompt 校验失败 / 磁盘损坏 fail-closed）→ 整体未提交
        mock_config_handler.save_ai_tuning_and_prompts.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"
        # 无部分成功：不存在绕过组合入口的直接写盘
        mock_config_handler.save_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_io_exception_returns_error_state(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=RuntimeError("disk full"))
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_settings()
        assert result is False
        assert vm.state.save_state == "error"

    @pytest.mark.asyncio
    async def test_save_cancelled_error_propagates(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        vm = _make_vm(mock_config_handler)
        with pytest.raises(asyncio.CancelledError):
            await vm.save_ai_settings()

    @pytest.mark.asyncio
    async def test_save_duplicate_submit_returns_false(self, mock_config_handler, mock_thread_pool):
        from dataclasses import replace

        vm = _make_vm(mock_config_handler)
        vm._state = replace(vm._state, save_state="saving")
        result = await vm.save_ai_settings()
        assert result is False
        mock_config_handler.save_ai_tuning_and_prompts.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_exception_sanitizes_sensitive_path_in_log(self, mock_config_handler, mock_thread_pool, caplog):
        """F4-U-1: R9 异常消息脱敏 — 含敏感路径的异常消息体不应原样传给 logger format string。"""
        import logging

        sensitive_path = "C:\\Users\\secret\\api_key.txt"
        mock_thread_pool.run_async = AsyncMock(side_effect=OSError(f"disk error accessing {sensitive_path}"))
        vm = _make_vm(mock_config_handler)

        with (
            caplog.at_level(logging.ERROR, logger="ui.viewmodels.ai_brain_settings_view_model"),
            patch("ui.viewmodels.ai_brain_settings_view_model.logger") as mock_logger,
        ):
            result = await vm.save_ai_settings()

        assert result is False
        assert vm.state.save_state == "error"
        for call in mock_logger.error.call_args_list + mock_logger.critical.call_args_list:
            args = call.args
            if len(args) >= 2:
                sanitized_arg = args[1]
                assert sensitive_path not in str(sanitized_arg), (
                    f"R9 违规：原始路径出现在 logger format 参数中: {sanitized_arg}"
                )


# --- ThreadPoolManager 调用契约 (R16) ---


class TestThreadPoolOffloadContract:
    @pytest.mark.asyncio
    async def test_save_ai_settings_uses_thread_pool_for_persist(self, mock_config_handler, mock_thread_pool):
        with patch("services.ai_service.AIService") as mock_ai_svc:
            mock_ai_svc.return_value.reload_config = AsyncMock()
            vm = _make_vm(mock_config_handler)
            await vm.save_ai_settings()
        assert mock_thread_pool.run_async.call_count >= 1
        args, _ = mock_thread_pool.run_async.call_args
        assert args[0] is TaskType.IO
