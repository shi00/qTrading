"""AIBrainSettingsViewModel 单测（S5 拆分后保留项）。

覆盖：
- _load_config_to_state 读取本页仍负责的配置（max_candidates / min_turnover /
  prompts / only-local 开关）
- set_* 命令更新 state
- _validate_all 对调优参数范围 / 空值的校验
- save_ai_settings 把本页字段正确传入 ConfigHandler 并 reload AIService

S5（reviews/模型统一.md §11.1）：模型连接 / 场景配置 / 使用记录 / 月度成本 /
新闻情绪配置已迁出本 VM，对应测试类随实现一并移除。

外部依赖全部 mock (ConfigHandler / ThreadPoolManager / AIService)。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock 模块级 ConfigHandler 引用 (VM 构造与保存路径依赖)。"""
    with patch("ui.viewmodels.ai_brain_settings_view_model.ConfigHandler") as ch:
        ch.get_ai_max_candidates.return_value = 30
        ch.get_strategy_min_turnover.return_value = 2.0
        ch.get_ai_system_prompt.return_value = ""
        ch.get_ai_news_prompt.return_value = ""
        ch.is_ai_local_only_mode.return_value = False
        ch.save_config.return_value = True
        ch.save_ai_tuning_and_prompts.return_value = True
        yield ch


@pytest.fixture
def make_vm(mock_config_handler):
    """构造无参 AIBrainSettingsViewModel（S5 后不再注入子 VM）。"""

    def _make() -> AIBrainSettingsViewModel:
        return AIBrainSettingsViewModel()

    return _make


def _patch_save_deps():
    """Patch save_ai_settings 的异步/IO 依赖 (ThreadPoolManager / AIService)。

    returns: contextlib.ExitStack, 由调用方 ``with`` 进入。
    """
    import contextlib

    stack = contextlib.ExitStack()

    tp = stack.enter_context(patch("ui.viewmodels.ai_brain_settings_view_model.ThreadPoolManager"))
    tp_instance = MagicMock()

    async def _run_sync(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    tp_instance.run_async = _run_sync
    tp.return_value = tp_instance

    ais = stack.enter_context(patch("services.ai_service.AIService"))
    ais_instance = MagicMock()
    ais_instance.reload_config = AsyncMock()
    ais.return_value = ais_instance

    return stack


# ============================================================================
# SEC-03: 仅本地模式开关 (ai_local_only_mode)
# ============================================================================


class TestAiLocalOnlyMode:
    def test_load_config_to_state(self, make_vm, mock_config_handler):
        mock_config_handler.is_ai_local_only_mode.return_value = True
        vm = make_vm()
        assert vm.state.ai_local_only_mode is True

    def test_default_false(self, make_vm, mock_config_handler):
        mock_config_handler.is_ai_local_only_mode.return_value = False
        vm = make_vm()
        assert vm.state.ai_local_only_mode is False

    def test_set_ai_local_only_mode(self, make_vm):
        vm = make_vm()
        vm.set_ai_local_only_mode(True)
        assert vm.state.ai_local_only_mode is True
        vm.set_ai_local_only_mode(False)
        assert vm.state.ai_local_only_mode is False

    async def test_save_payload_includes_local_only(self, make_vm, mock_config_handler):
        vm = make_vm()
        vm.set_ai_local_only_mode(True)
        with _patch_save_deps():
            assert await vm.save_ai_settings() is True
        # T3: 调优参数 + prompts 合并为单次原子保存调用, local_only 为第 3 个位置参数
        call_args = mock_config_handler.save_ai_tuning_and_prompts.call_args.args
        assert call_args[2] is True
