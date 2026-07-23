"""Onboarding Wizard ViewModel/Service 集成测试 — Task 6.1 (P3-WinE2E-Skip, 见 docs/debt/known-technical-debt.md).

替代 Windows E2E skip 路径（test_wizard_db_validation_success），提供非跳过的自动化测试覆盖。
覆盖 OnboardingViewModel + DatabaseConfigPanelViewModel + DatabaseConfigService（mock）集成：
- DB 验证成功 → OnboardingViewModel 状态前进到 token 步骤（状态前进）
- DB 验证失败 → OnboardingViewModel 状态停留在 database 步骤（错误恢复）
- DB 验证异常 → DatabaseConfigPanelViewModel 捕获并显示错误，OnboardingViewModel 不前进

不依赖真实 PostgreSQL（mock DatabaseConfigService），可在 Windows/Linux/macOS 任意平台运行。
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.persistence.db_config_service import ConnectionResult, ConnectionStatus
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from ui.viewmodels.onboarding_view_model import OnboardingViewModel

# no_db: 跳过 db_schema_ready 中的 test_engine 加载（本文件 mock DatabaseConfigService，不需要真实 DB）
pytestmark = [pytest.mark.integration, pytest.mark.no_db]


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared。

    复用 test_config_panels.py 的同名 fixture 模式，避免 ConfigHandler 全局状态污染。
    """
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_onboarding_int_{uuid.uuid4().hex}.json"
    test_config_file = str(tmp_path / unique_name)
    original_config_file = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = test_config_file

    ConfigHandler._clear_cache()

    if os.path.exists(test_config_file):
        os.remove(test_config_file)

    yield test_config_file

    ConfigHandler._clear_cache()
    config_module.CONFIG_FILE = original_config_file


@pytest.fixture
def mock_db_thread_pool():
    """Mock DatabaseConfigPanelViewModel 模块的 ThreadPoolManager.run_async 为同步 passthrough。

    save_config 调用 ConfigHandler.save_db_config（同步 IO），通过 ThreadPoolManager offload；
    测试中改为同步 passthrough，避免线程池调度的不确定性。
    """

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.database_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_db_vm(isolated_config) -> DatabaseConfigPanelViewModel:  # noqa: ARG001 - fixture 上下文激活
    """构造 DatabaseConfigPanelViewModel（在 isolated_config 上下文中调用）。"""
    return DatabaseConfigPanelViewModel()


def _make_onboarding_vm_with_db(db_vm: DatabaseConfigPanelViewModel) -> OnboardingViewModel:
    """构造 OnboardingViewModel，将 fn_validate_database 绑定到 db_vm.save_config。

    模拟 OnboardingWizard 中的绑定：``fn_validate_database=database_vm.save_config``。
    其他 validator 用 AsyncMock 返回 True，避免干扰 DB 路径测试。
    """
    vm = OnboardingViewModel()
    vm.bind(
        fn_validate_database=db_vm.save_config,
        fn_validate_token=AsyncMock(return_value=True),
        fn_validate_cloud_ai=AsyncMock(return_value=True),
        fn_validate_local_model=AsyncMock(return_value=True),
        fn_push_schedule_state=MagicMock(),
        on_complete=AsyncMock(),
    )
    return vm


def _fill_valid_db_config(db_vm: DatabaseConfigPanelViewModel) -> None:
    """填充有效 DB 配置到 DatabaseConfigPanelViewModel。"""
    db_vm.update_host("localhost")
    db_vm.update_port("5432")
    db_vm.update_user("postgres")
    db_vm.update_password("password")
    db_vm.update_database("testdb")


class TestOnboardingDBIntegration:
    """OnboardingViewModel + DatabaseConfigPanelViewModel + DatabaseConfigService 集成测试。

    覆盖状态前进（DB 成功 → token 步骤）与错误恢复（DB 失败/异常 → 状态停留）。
    """

    @pytest.mark.asyncio
    async def test_db_success_advances_to_token_step(self, isolated_config, mock_db_thread_pool):
        """DB 验证成功 → OnboardingViewModel 状态前进到 token 步骤（step index 2）。

        P1-9: 精确断言 - 验证 DatabaseConfigService.test_connection 调用参数、
        ConfigHandler.save_db_config 调用次数与参数、state 字段值（status_message key）。
        """
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        with (
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.test_connection",
                new_callable=AsyncMock,
                return_value=ConnectionResult(
                    status=ConnectionStatus.SUCCESS,
                    message="Connection successful",
                    server_version="16.0",
                    database_exists=True,
                ),
            ) as mock_test_connection,
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.ensure_tables_exist",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.get_database_info",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("utils.config_handler.ConfigHandler.save_db_config") as mock_save_db_config,
        ):
            await onboarding_vm.next_step()

        # P1-9: 精确断言 - 验证 test_connection 调用参数
        mock_test_connection.assert_awaited_once_with(
            host="localhost",
            port=5432,
            user="postgres",
            password="password",
            database="testdb",
        )
        # P1-9: 精确断言 - 验证 save_db_config 被调用一次且参数正确
        mock_save_db_config.assert_called_once_with(
            host="localhost",
            port=5432,
            user="postgres",
            password="password",
            database="testdb",
        )
        assert onboarding_vm.current_step == 2  # token step
        assert onboarding_vm.step_validated["database"] is True
        # DatabaseConfigPanelViewModel 显示保存成功状态
        assert db_vm.state.is_saving is False
        assert db_vm.state.status_type == "success"
        # P1-9: 验证 status_message key（VM 只产出 i18n key, 不感知 locale）
        assert db_vm.state.status_message is not None
        assert db_vm.state.status_message.key == "db_msg_saved"

    @pytest.mark.asyncio
    async def test_db_auth_failure_stays_at_database_step(self, isolated_config, mock_db_thread_pool):
        """DB 验证失败（认证错误）→ OnboardingViewModel 状态停留在 database 步骤。

        P1-9: 精确断言 - 验证 test_connection 调用参数、save_db_config 未被调用、
        status_message key（_raw_msg_ 表示动态错误消息包装）。
        """
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        with (
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.test_connection",
                new_callable=AsyncMock,
                return_value=ConnectionResult(
                    status=ConnectionStatus.AUTHENTICATION_ERROR,
                    message="Authentication failed",
                ),
            ) as mock_test_connection,
            patch("utils.config_handler.ConfigHandler.save_db_config") as mock_save_db_config,
        ):
            await onboarding_vm.next_step()

        # P1-9: 精确断言 - 验证 test_connection 调用参数
        mock_test_connection.assert_awaited_once_with(
            host="localhost",
            port=5432,
            user="postgres",
            password="password",
            database="testdb",
        )
        # P1-9: 失败路径不应保存配置
        mock_save_db_config.assert_not_called()
        assert onboarding_vm.current_step == 1  # stays at database
        assert not onboarding_vm.step_validated.get("database", False)
        # DatabaseConfigPanelViewModel 显示错误状态
        assert db_vm.state.status_type == "error"
        assert db_vm.state.is_saving is False
        # P1-9: 验证 status_message key（_raw_msg_ 表示动态错误消息包装）
        assert db_vm.state.status_message is not None
        assert db_vm.state.status_message.key == "_raw_msg_"

    @pytest.mark.asyncio
    async def test_db_connection_error_stays_at_database_step(self, isolated_config, mock_db_thread_pool):
        """DB 验证失败（连接错误）→ OnboardingViewModel 状态停留在 database 步骤。"""
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        with patch(
            "data.persistence.db_config_service.DatabaseConfigService.test_connection",
            new_callable=AsyncMock,
            return_value=ConnectionResult(
                status=ConnectionStatus.CONNECTION_ERROR,
                message="Connection refused",
            ),
        ):
            await onboarding_vm.next_step()

        assert onboarding_vm.current_step == 1  # stays at database
        assert not onboarding_vm.step_validated.get("database", False)
        assert db_vm.state.status_type == "error"

    @pytest.mark.asyncio
    async def test_db_exception_recovers_to_database_step(self, isolated_config, mock_db_thread_pool):
        """DB 验证抛异常 → DatabaseConfigPanelViewModel 捕获并显示错误，OnboardingViewModel 不前进。

        错误恢复路径：DatabaseConfigPanelViewModel.save_config 内部 try/except 捕获异常，
        返回 False；OnboardingViewModel.validate_and_persist_current_step 看到 False 不前进。
        """
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        with patch(
            "data.persistence.db_config_service.DatabaseConfigService.test_connection",
            new_callable=AsyncMock,
            side_effect=OSError("network unreachable"),
        ):
            await onboarding_vm.next_step()

        assert onboarding_vm.current_step == 1  # stays at database
        assert not onboarding_vm.step_validated.get("database", False)
        # DatabaseConfigPanelViewModel 捕获异常并显示错误
        assert db_vm.state.status_type == "error"
        assert db_vm.state.is_saving is False  # finally resets

    @pytest.mark.asyncio
    async def test_db_validation_in_progress_state_transitions(self, isolated_config, mock_db_thread_pool):
        """DB 验证过程中 validation_in_progress 状态正确切换（True → False）。"""
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        snapshots: list = []
        onboarding_vm.subscribe(lambda s: snapshots.append(s))

        with (
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.test_connection",
                new_callable=AsyncMock,
                return_value=ConnectionResult(
                    status=ConnectionStatus.SUCCESS,
                    message="ok",
                    database_exists=True,
                ),
            ),
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.ensure_tables_exist",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.get_database_info",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await onboarding_vm.next_step()

        # validation_in_progress 至少经历 False → True → False 两次切换
        vip_transitions = 0
        prev = False
        for s in snapshots:
            if s.validation_in_progress != prev:
                vip_transitions += 1
                prev = s.validation_in_progress
        assert vip_transitions == 2  # False→True, True→False

    @pytest.mark.asyncio
    async def test_db_success_then_token_step_can_proceed(self, isolated_config, mock_db_thread_pool):
        """DB 成功后再次调用 next_step 应触发 token 验证（fn_validate_token）。"""
        db_vm = _make_db_vm(isolated_config)
        _fill_valid_db_config(db_vm)

        onboarding_vm = _make_onboarding_vm_with_db(db_vm)
        onboarding_vm.current_step = 1  # database step

        with (
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.test_connection",
                new_callable=AsyncMock,
                return_value=ConnectionResult(
                    status=ConnectionStatus.SUCCESS,
                    message="ok",
                    database_exists=True,
                ),
            ),
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.ensure_tables_exist",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "data.persistence.db_config_service.DatabaseConfigService.get_database_info",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            # 第一次 next_step：DB 验证成功，前进到 token step
            await onboarding_vm.next_step()
            assert onboarding_vm.current_step == 2

            # 第二次 next_step：token 验证（fn_validate_token mock 返回 True），前进到 cloud_ai
            await onboarding_vm.next_step()
            assert onboarding_vm.current_step == 3  # cloud_ai step

        # 验证 fn_validate_token 被调用
        assert onboarding_vm.fn_validate_token is not None
        onboarding_vm.fn_validate_token.assert_awaited_once()
