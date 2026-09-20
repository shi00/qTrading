"""AIBrainSettingsViewModel 单测 — AI-03 完整版 T7 (月度 AI 成本).

覆盖 task:before
- _load_config_to_state 读取 ai_cost_limit_cny (None/0/正数 → 输入文本映射)
- set_ai_cost_limit_value 更新 state
- _validate_all 对成本上限 (空串/非负浮点/负数/非数字) 的校验
- save_ai_settings 把 ai_cost_limit_cny 正确传入 ConfigHandler.save_config (空串→None, 数值→float)
- load_month_cost_cny 读取 engine_provider + AIUsageTracker → month_cost_cny(元)

外部依赖全部 mock (ConfigHandler / engine_provider / AIUsageTracker / ThreadPoolManager /
AIService / LocalModelManager / 子 VM), 参考 test_data_source_view_model.py 的 patch 风格。
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
        ch.get_ai_max_concurrent_analysis.return_value = 5
        ch.get_ai_max_candidates.return_value = 30
        ch.get_strategy_min_turnover.return_value = 2.0
        ch.get_ai_news_max_concurrent.return_value = 1
        ch.get_ai_system_prompt.return_value = ""
        ch.get_ai_news_prompt.return_value = ""
        ch.get_setting.return_value = None
        ch.save_local_ai_config.return_value = True
        ch.save_config.return_value = True
        ch.save_ai_system_prompt.return_value = True
        ch.set_ai_news_prompt.return_value = True
        yield ch


@pytest.fixture
def make_vm(mock_config_handler):
    """构造 AIBrainSettingsViewModel (子 VM 用 MagicMock, 成本字段加载由 get_setting 驱动)。"""

    def _make() -> AIBrainSettingsViewModel:
        llm_vm = MagicMock()
        llm_vm.save_config = AsyncMock(return_value=True)
        local_vm = MagicMock()
        local_vm.get_current_config.return_value = {
            "model_path": "",
            "timeout": 300,
            "n_threads": 4,
            "n_batch": 512,
            "n_ctx": 2048,
            "flash_attn": False,
            "n_gpu_layers": 0,
        }
        return AIBrainSettingsViewModel(llm_vm=llm_vm, failover_vm=MagicMock(), local_vm=local_vm)

    return _make


def _patch_save_deps():
    """Patch save_ai_settings 的异步/IO 依赖 (ThreadPoolManager/AIService/LocalModelManager)。

    returns: (stack 之外的实参约定) 直接以 contextlib.ExitStack 返回, 由调用方进入。
    """
    import contextlib

    stack = contextlib.ExitStack()

    tp = stack.enter_context(patch("ui.viewmodels.ai_brain_settings_view_model.ThreadPoolManager"))
    tp_instance = MagicMock()

    async def _run_sync(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    tp_instance.run_async = _run_sync
    tp.return_value = tp_instance

    lmm = stack.enter_context(patch("services.local_model_manager.LocalModelManager"))
    lmm.commit_verification_if_active = MagicMock()

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
        call_data = mock_config_handler.save_config.call_args.args[0]
        assert call_data["ai_local_only_mode"] is True


# ============================================================================
# _load_config_to_state: ai_cost_limit_cny 读取映射
# ============================================================================


class TestLoadConfigToStateCostLimit:
    def test_none_limit_maps_to_empty(self, make_vm, mock_config_handler):
        mock_config_handler.get_setting.return_value = None
        vm = make_vm()
        assert vm.state.ai_cost_limit_value == ""

    def test_zero_limit_maps_to_empty(self, make_vm, mock_config_handler):
        mock_config_handler.get_setting.return_value = 0
        vm = make_vm()
        assert vm.state.ai_cost_limit_value == ""

    def test_positive_limit_maps_to_str(self, make_vm, mock_config_handler):
        mock_config_handler.get_setting.return_value = 100.0
        vm = make_vm()
        assert vm.state.ai_cost_limit_value == "100.0"


# ============================================================================
# set_ai_cost_limit_value
# ============================================================================


class TestSetAiCostLimitValue:
    def test_updates_state(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("200")
        assert vm.state.ai_cost_limit_value == "200"


# ============================================================================
# _validate_all: 成本上限校验
# ============================================================================


class TestValidateAllCostLimit:
    def test_empty_string_valid(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("")
        assert vm._validate_all() == (True, "")

    def test_non_negative_float_valid(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("50.5")
        assert vm._validate_all() == (True, "")

    def test_zero_valid(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("0")
        assert vm._validate_all() == (True, "")

    def test_negative_invalid(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("-1")
        ok, err_key = vm._validate_all()
        assert ok is False
        assert err_key == "ai_snack_param_err"

    def test_non_numeric_invalid(self, make_vm):
        vm = make_vm()
        vm.set_ai_cost_limit_value("abc")
        ok, err_key = vm._validate_all()
        assert ok is False
        assert err_key == "ai_snack_param_err"


# ============================================================================
# save_ai_settings: ai_cost_limit_cny 持久化
# ============================================================================


class TestSaveAiSettingsCostLimit:
    async def test_empty_string_maps_to_none(self, make_vm, mock_config_handler):
        vm = make_vm()
        vm.set_ai_cost_limit_value("")
        with _patch_save_deps():
            assert await vm.save_ai_settings() is True
        call_data = mock_config_handler.save_config.call_args.args[0]
        assert "ai_cost_limit_cny" in call_data
        assert call_data["ai_cost_limit_cny"] is None

    async def test_numeric_maps_to_float(self, make_vm, mock_config_handler):
        vm = make_vm()
        vm.set_ai_cost_limit_value("500")
        with _patch_save_deps():
            assert await vm.save_ai_settings() is True
        call_data = mock_config_handler.save_config.call_args.args[0]
        assert call_data["ai_cost_limit_cny"] == 500.0

    async def test_decimal_value_preserved(self, make_vm, mock_config_handler):
        vm = make_vm()
        vm.set_ai_cost_limit_value("12.34")
        with _patch_save_deps():
            assert await vm.save_ai_settings() is True
        call_data = mock_config_handler.save_config.call_args.args[0]
        assert call_data["ai_cost_limit_cny"] == 12.34


# ============================================================================
# load_month_cost_cny: 本月累计成本加载
# ============================================================================


def _enter_cost_tracker(engine_value, is_disposed_value, get_result, get_side_effect=None):
    """Patch 引擎与成本追踪依赖, 返回 (ExitStack, AIUsageTracker instance)。

    测试使用方式: ``stack, instance = _enter_cost_tracker(...); with stack: await ...``。
    get_month_unpriced 同步 mock 返回 (0, 0)：AI-01 引入的可计价外统计，
    本 fixture 关注月份成本，非本项。
    """
    import contextlib

    stack = contextlib.ExitStack()
    stack.enter_context(patch("data.persistence.engine_provider.get_engine", return_value=engine_value))
    stack.enter_context(patch("data.persistence.engine_provider.is_disposed", return_value=is_disposed_value))
    tracker_cls = stack.enter_context(patch("services.ai_service.usage_tracker.AIUsageTracker"))
    instance = MagicMock()
    if get_side_effect is not None:
        instance.get_month_cost_cny = AsyncMock(side_effect=get_side_effect)
    else:
        instance.get_month_cost_cny = AsyncMock(return_value=get_result)
    instance.get_month_unpriced = AsyncMock(return_value=(0, 0))
    tracker_cls.return_value = instance
    return stack, instance


class TestLoadMonthCostCny:
    async def test_success_sets_cost_in_yuan(self, make_vm):
        """2500 分 → 25.0 元。"""
        vm = make_vm()
        engine = MagicMock()
        stack, _instance = _enter_cost_tracker(engine, False, 2500)
        with stack:
            await vm.load_month_cost_cny()
        assert vm.state.month_cost_cny == 25.0

    async def test_zero_cost(self, make_vm):
        vm = make_vm()
        engine = MagicMock()
        stack, _instance = _enter_cost_tracker(engine, False, 0)
        with stack:
            await vm.load_month_cost_cny()
        assert vm.state.month_cost_cny == 0.0

    async def test_engine_none_sets_none(self, make_vm):
        vm = make_vm()
        stack, _instance = _enter_cost_tracker(None, False, 999)
        with stack:
            await vm.load_month_cost_cny()
        assert vm.state.month_cost_cny is None

    async def test_engine_disposed_sets_none(self, make_vm):
        vm = make_vm()
        engine = MagicMock()
        stack, _instance = _enter_cost_tracker(engine, True, 999)
        with stack:
            await vm.load_month_cost_cny()
        assert vm.state.month_cost_cny is None

    async def test_tracker_exception_sets_none(self, make_vm):
        vm = make_vm()
        engine = MagicMock()
        stack, _instance = _enter_cost_tracker(engine, False, None, get_side_effect=RuntimeError("db down"))
        with stack:
            await vm.load_month_cost_cny()
        assert vm.state.month_cost_cny is None
