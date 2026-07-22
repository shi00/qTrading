"""OnboardingWizard embedded 模式 database step 单元测试 (P3-12).

P3-12 改造 OnboardingWizard STEP_CONFIGS[1] database step 支持 embedded 模式:
- embedded 模式: validate_before_next 走 _validate_database_embedded (always-true)
- external 模式: validate_before_next 走 database_vm.save_config (原行为)
- R-A4: 保留 STEP_CONFIGS[1] 索引不变, 通过 VM 切换 validate 行为

测试聚焦 _validate_database_embedded + _resolve_database_validator 行为契约,
不渲染整个 OnboardingWizard (避免 4 个 config panel VM 完整初始化开销)。
"""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ui.viewmodels.onboarding_view_model import STEP_CONFIGS
from ui.views import onboarding_wizard as wizard_module
from ui.views.onboarding_wizard import (
    _resolve_database_validator,
    _validate_database_embedded,
)

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 onboarding_wizard.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    return Path(wizard_module.__file__).read_text(encoding="utf-8")


class _FakeDatabaseVm:
    """模拟 DatabaseConfigPanelViewModel, 仅暴露 save_config bound method 契约."""

    def __init__(self) -> None:
        self.save_config = MagicMock(return_value=True)


# ============================================================================
# _validate_database_embedded: embedded 模式 validator 行为契约
# ============================================================================


class TestValidateDatabaseEmbedded:
    """embedded 模式 database step validator 行为契约 (P3-12)."""

    def test_returns_true(self) -> None:
        """DoD: embedded 模式 validator 始终返回 True (always-true).

        bootstrap.py 在 onboarding 之前已启动 sidecar, 走到 onboarding 说明已 ready。
        """
        result = asyncio.run(_validate_database_embedded())
        assert result is True

    def test_is_coroutine_function(self) -> None:
        """DoD: _validate_database_embedded 是 async 函数 (符合 Awaitable[bool] 契约)."""
        assert inspect.iscoroutinefunction(_validate_database_embedded)

    def test_no_required_args(self) -> None:
        """DoD: validator 签名无必填参数 (符合 Callable[[], Awaitable[bool]] 契约).

        OnboardingViewModel.validate_and_persist_current_step 调用 ``await validator()``
        不传参, validator 必须无必填参数。
        """
        sig = inspect.signature(_validate_database_embedded)
        required = [p for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
        assert required == [], f"_validate_database_embedded 不应有必填参数, 实际: {required}"


# ============================================================================
# _resolve_database_validator: embedded/external 模式分支契约
# ============================================================================


class TestResolveDatabaseValidator:
    """_resolve_database_validator 按 is_embedded_mode() 切换 validator (P3-12)."""

    def test_embedded_mode_returns_embedded_validator(self) -> None:
        """DoD: embedded 模式 → 返回 _validate_database_embedded (不调 save_config)."""
        vm = _FakeDatabaseVm()
        with patch.object(wizard_module.ConfigHandler, "is_embedded_mode", return_value=True):
            validator = _resolve_database_validator(vm)
        assert validator is _validate_database_embedded
        # 守护: embedded 模式不调用 save_config
        assert not vm.save_config.called

    def test_external_mode_returns_save_config(self) -> None:
        """DoD: external 模式 → 返回 database_vm.save_config (保留原行为)."""
        vm = _FakeDatabaseVm()
        with patch.object(wizard_module.ConfigHandler, "is_embedded_mode", return_value=False):
            validator = _resolve_database_validator(vm)
        assert validator is vm.save_config

    def test_external_mode_validator_is_not_embedded_validator(self) -> None:
        """DoD: external 模式 validator 不应是 _validate_database_embedded."""
        vm = _FakeDatabaseVm()
        with patch.object(wizard_module.ConfigHandler, "is_embedded_mode", return_value=False):
            validator = _resolve_database_validator(vm)
        assert validator is not _validate_database_embedded

    def test_embedded_mode_calls_is_embedded_mode(self) -> None:
        """DoD: _resolve_database_validator 调用 ConfigHandler.is_embedded_mode() 决策."""
        vm = _FakeDatabaseVm()
        with patch.object(
            wizard_module.ConfigHandler,
            "is_embedded_mode",
            return_value=True,
        ) as mock_mode:
            _resolve_database_validator(vm)
        assert mock_mode.call_count >= 1

    def test_embedded_validator_returns_true_when_invoked(self) -> None:
        """DoD: embedded 模式下完整调用链 → validator() 返回 True.

        模拟 OnboardingViewModel.validate_and_persist_current_step 调用流程:
        resolve → validator → await → bool.
        """
        vm = _FakeDatabaseVm()
        with patch.object(wizard_module.ConfigHandler, "is_embedded_mode", return_value=True):
            validator = _resolve_database_validator(vm)
        result = asyncio.run(validator())
        assert result is True
        # 守护: embedded 模式完整调用链不触发 save_config
        assert not vm.save_config.called


# ============================================================================
# R-A4: STEP_CONFIGS[1] 索引不变契约
# ============================================================================


class TestStepConfigsContract:
    """R-A4: STEP_CONFIGS[1] 索引不变 (database step 保留原位置)."""

    def test_step_configs_index_1_is_database(self) -> None:
        """DoD R-A4: STEP_CONFIGS[1].id == "database" (索引不变)."""
        assert STEP_CONFIGS[1].id == "database"

    def test_step_configs_index_1_validate_before_next(self) -> None:
        """DoD: STEP_CONFIGS[1] 保留 validate_before_next=True (仍需验证)."""
        assert STEP_CONFIGS[1].validate_before_next is True

    def test_step_configs_index_1_required(self) -> None:
        """DoD: STEP_CONFIGS[1] 保留 required=True."""
        assert STEP_CONFIGS[1].required is True

    def test_step_configs_length_unchanged(self) -> None:
        """DoD: STEP_CONFIGS 长度 8 不变 (不增不减 step)."""
        assert len(STEP_CONFIGS) == 8

    def test_step_configs_index_0_is_welcome(self) -> None:
        """守护: STEP_CONFIGS[0] 仍是 welcome (索引结构稳定)."""
        assert STEP_CONFIGS[0].id == "welcome"

    def test_step_configs_index_2_is_token(self) -> None:
        """守护: STEP_CONFIGS[2] 仍是 token (database 后续 step 索引不变)."""
        assert STEP_CONFIGS[2].id == "token"


# ============================================================================
# OnboardingWizard 源码契约守护 (防止 bind 决策被误改/删除)
# ============================================================================


class TestOnboardingWizardSourceContract:
    """OnboardingWizard 源码契约守护: bind 决策使用 _resolve_database_validator."""

    def test_imports_config_handler(self) -> None:
        """DoD: onboarding_wizard.py 导入 ConfigHandler (用于 is_embedded_mode 决策)."""
        source = _read_source()
        assert "from utils.config_handler import ConfigHandler" in source

    def test_defines_validate_database_embedded(self) -> None:
        """DoD: onboarding_wizard.py 定义 _validate_database_embedded 函数."""
        source = _read_source()
        assert "async def _validate_database_embedded" in source

    def test_defines_resolve_database_validator(self) -> None:
        """DoD: onboarding_wizard.py 定义 _resolve_database_validator 函数."""
        source = _read_source()
        assert "def _resolve_database_validator" in source

    def test_bind_uses_resolve_database_validator(self) -> None:
        """DoD: onboarding_vm.bind 调用使用 _resolve_database_validator(database_vm).

        守护 bind 不直接传 database_vm.save_config (必须经过模式路由)。
        """
        source = _read_source()
        assert "fn_validate_database=_resolve_database_validator(database_vm)" in source

    def test_resolve_database_validator_uses_is_embedded_mode(self) -> None:
        """DoD: _resolve_database_validator 内部调用 ConfigHandler.is_embedded_mode()."""
        source = _read_source()
        assert "ConfigHandler.is_embedded_mode()" in source

    def test_no_direct_save_config_in_bind(self) -> None:
        """DoD: bind 不再直接传 database_vm.save_config (必须经路由)."""
        source = _read_source()
        # 守护: 不应出现 fn_validate_database=database_vm.save_config 直接绑定
        assert "fn_validate_database=database_vm.save_config" not in source
