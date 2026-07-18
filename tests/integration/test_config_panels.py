"""
Integration Tests for Onboarding Wizard Configuration Panels

声明式重写后，组件实例化需要 Renderer 上下文，集成测试改为直接测试
ViewModel 的业务逻辑（连接测试、配置保存、验证），覆盖原命令式 API 场景。

- DatabaseConfigPanelViewModel: get_config / validate / save_config / on_change
- LocalModelConfigPanelViewModel: get_current_config / save_config / verify_model / on_change
- DatabaseConfigService: run_migrations / ensure_tables_exist（service 层，保留真实 DB）
"""

import os
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params
from tests.integration.test_data_db_migrator import (
    _create_isolated_db,
    _drop_isolated_db,
)
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from ui.viewmodels.local_model_config_panel_view_model import (
    LocalModelConfigPanelViewModel,
)

# no_db: 跳过 db_schema_ready 中的 test_engine 加载（本文件的 DB 测试使用 isolated_db
# 自建独立 DB，无需共享 test_engine；UI/VM 测试不需要 DB）
pytestmark = [pytest.mark.integration, pytest.mark.no_db]


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared"""
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_config_{uuid.uuid4().hex}.json"
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
def mock_verify_model():
    """on_verify_model 回调 mock，默认返回成功。"""
    return AsyncMock(return_value=True)


@pytest.fixture
def mock_db_thread_pool():
    """Mock DatabaseConfigPanelViewModel 模块的 ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.database_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


@pytest.fixture
def mock_local_thread_pool():
    """Mock LocalModelConfigPanelViewModel 模块的 ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.local_model_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_db_vm(
    isolated_config,  # noqa: ARG001 - 确保 isolated_config fixture 激活
    *,
    on_save_callback: Callable | None = None,
    on_test_success_callback: Callable | None = None,
    on_change: Callable | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
    load_password: bool = False,
) -> DatabaseConfigPanelViewModel:
    """构造 DatabaseConfigPanelViewModel（在 isolated_config 上下文中调用）。"""
    return DatabaseConfigPanelViewModel(
        on_save_callback=on_save_callback,
        on_test_success_callback=on_test_success_callback,
        on_change=on_change,
        on_loading_change=on_loading_change,
        load_password=load_password,
    )


def _make_local_vm(
    isolated_config,  # noqa: ARG001 - 确保 isolated_config fixture 激活
    mock_verify_model,
    *,
    on_verify_success: Callable | None = None,
    on_save: Callable | None = None,
    on_change: Callable | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
    show_internal_loading: bool = True,
) -> LocalModelConfigPanelViewModel:
    """构造 LocalModelConfigPanelViewModel（在 isolated_config 上下文中调用）。"""
    return LocalModelConfigPanelViewModel(
        on_verify_model=mock_verify_model,
        on_verify_success=on_verify_success,
        on_save=on_save,
        on_change=on_change,
        on_loading_change=on_loading_change,
        show_internal_loading=show_internal_loading,
    )


class TestDatabaseConfigPanel:
    """Tests for DatabaseConfigPanelViewModel business logic"""

    def test_get_config(self, isolated_config):
        """Test get_config returns correct structure"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        config = vm.get_config()

        assert config["host"] == "localhost"
        assert config["port"] == 5432
        assert config["user"] == "postgres"
        assert config["password"] == "password"
        assert config["database"] == "testdb"

    def test_validate_empty_host(self, isolated_config):
        """Test validation fails with empty host"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        is_valid, error = vm.validate()

        assert is_valid is False
        assert error is not None

    def test_validate_invalid_port(self, isolated_config):
        """Test validation fails with invalid port"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("invalid")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        is_valid, _ = vm.validate()

        assert is_valid is False

    def test_validate_empty_database(self, isolated_config):
        """Test validation fails with empty database name"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("")

        is_valid, _ = vm.validate()

        assert is_valid is False


class TestLocalModelConfigPanel:
    """Tests for LocalModelConfigPanelViewModel business logic"""

    def test_get_current_config(self, isolated_config, mock_verify_model):
        """Test get_current_config returns correct structure"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.gguf")

        config = vm.get_current_config()

        assert config["model_path"] == "/path/to/model.gguf"

    @pytest.mark.asyncio
    async def test_save_config_empty_path(self, isolated_config, mock_verify_model, mock_local_thread_pool):
        """Test save_config returns True for empty path (local model is optional)"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("")
        vm.update_timeout("300")

        with patch("services.local_model_manager.LocalModelManager.commit_verification_if_active"):
            result = await vm.save_config()

        assert result is True

    @pytest.mark.asyncio
    async def test_save_config_nonexistent_path(self, isolated_config, mock_verify_model, mock_local_thread_pool):
        """Test save_config returns True for nonexistent path (validation is in verify)"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/nonexistent/path/model.gguf")
        vm.update_timeout("300")

        with patch("services.local_model_manager.LocalModelManager.commit_verification_if_active"):
            result = await vm.save_config()

        assert result is True

    @pytest.mark.asyncio
    async def test_save_config_wrong_format(self, isolated_config, mock_verify_model, mock_local_thread_pool):
        """Test save_config returns True for wrong file format (validation is in verify)"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.txt")
        vm.update_timeout("300")

        with patch("services.local_model_manager.LocalModelManager.commit_verification_if_active"):
            result = await vm.save_config()

        assert result is True


class TestConfigPanelsIntegration:
    """Integration tests for configuration panels on_change callback"""

    def test_database_panel_on_change_callback(self, isolated_config):
        """Test on_change callback is triggered when VM fields update"""
        callback_called = []

        def on_change():
            callback_called.append(True)

        vm = _make_db_vm(isolated_config, on_change=on_change)
        vm.update_host("newhost")

        assert len(callback_called) > 0

    def test_local_model_panel_on_change_callback(self, isolated_config, mock_verify_model):
        """Test on_change callback is triggered for local model VM"""
        callback_called = []

        def on_change():
            callback_called.append(True)

        vm = _make_local_vm(isolated_config, mock_verify_model, on_change=on_change)
        vm.update_model_path("/new/path.gguf")

        assert len(callback_called) > 0


class TestDatabaseConfigServiceMigrations:
    """Tests for DatabaseConfigService migration methods"""

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_run_migrations_creates_tables(self, isolated_db):
        """Test run_migrations creates all required tables"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.run_migrations(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True
        assert "成功" in msg or "success" in msg.lower()

    @pytest.mark.asyncio
    async def test_run_migrations_invalid_credentials(self):
        """Test run_migrations returns False when init_db raises an exception"""
        from unittest.mock import AsyncMock, patch

        from data.persistence.db_config_service import DatabaseConfigService

        with patch(
            "data.persistence.db_migrator.DatabaseMigrator.init_db",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            success, msg = await DatabaseConfigService.run_migrations(
                host="invalid_host",
                port=5432,
                user="invalid_user",
                password="invalid_password",
                database="invalid_db",
            )

        assert success is False
        assert "failed" in msg.lower() or "失败" in msg or "error" in msg.lower() or "exception" in msg.lower()

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_empty_database(self, isolated_db):
        """Test ensure_tables_exist creates tables for empty database"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_existing_tables(self, isolated_db):
        """Test ensure_tables_exist skips creation when tables exist"""
        from data.persistence.db_config_service import DatabaseConfigService

        engine, db_name, params = isolated_db

        # Run migrations first so alembic_version table exists — this is the
        # authoritative marker that the database is already managed by Alembic.
        # Using Base.metadata.create_all alone does NOT create alembic_version,
        # so ensure_tables_exist would re-run migrations rather than skip.
        success1, _ = await DatabaseConfigService.run_migrations(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )
        assert success1 is True

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True
        assert "exist" in msg.lower() or "存在" in msg

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_connection_failure(self):
        """Test ensure_tables_exist returns False on connection failure"""
        from data.persistence.db_config_service import DatabaseConfigService

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host="invalid_host",
            port=5432,
            user="invalid_user",
            password="invalid_password",
            database="invalid_db",
        )

        assert success is False


class TestDatabaseConfigPanelSaveConfig:
    """Tests for DatabaseConfigPanelViewModel.save_config with table creation"""

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_save_config_calls_ensure_tables_exist(self, isolated_config, isolated_db, mock_db_thread_pool):
        """Test save_config calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        vm = _make_db_vm(isolated_config)
        vm.update_host(params["host"])
        vm.update_port(str(params["port"]))
        vm.update_user(params["user"])
        vm.update_password(params["password"])
        vm.update_database(db_name)
        vm.update_create_if_not_exists(False)

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (True, "Tables created")

            result = await vm.save_config()

        assert result is True
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_handles_table_creation_failure(self, isolated_config, mock_db_thread_pool):
        """Test save_config returns False when table creation fails"""
        from data.persistence.db_config_service import DatabaseConfigService

        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")
        vm.update_create_if_not_exists(False)

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (False, "Table creation failed")

            result = await vm.save_config()

        assert result is False
        mock_ensure.assert_called_once()


class TestOnboardingWizardDatabaseValidation:
    """Tests for OnboardingWizard database validation via DatabaseConfigPanelViewModel.

    OnboardingWizard 声明式重写后，fn_validate_database 绑定为
    database_vm.save_config（见 onboarding_wizard.py L321）。本测试验证该绑定
    契约：通过 VM.save_config 作为 fn_validate_database 调用。
    """

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_validate_and_save_database_calls_ensure_tables(
        self, isolated_config, isolated_db, mock_db_thread_pool
    ):
        """Test fn_validate_database (bound to database_vm.save_config) calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        vm = _make_db_vm(isolated_config)
        vm.update_host(params["host"])
        vm.update_port(str(params["port"]))
        vm.update_user(params["user"])
        vm.update_password(params["password"])
        vm.update_database(db_name)
        vm.update_create_if_not_exists(False)

        # 模拟 OnboardingWizard 的 fn_validate_database 绑定
        fn_validate_database = vm.save_config
        assert fn_validate_database is not None

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (True, "Tables created")

            result = await fn_validate_database()

        assert result is True
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_save_database_handles_failure(self, isolated_config, mock_db_thread_pool):
        """Test fn_validate_database returns False when save_config fails"""
        from data.persistence.db_config_service import DatabaseConfigService

        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")
        vm.update_create_if_not_exists(False)

        # 模拟 OnboardingWizard 的 fn_validate_database 绑定
        fn_validate_database = vm.save_config
        assert fn_validate_database is not None

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (False, "Table creation failed")

            result = await fn_validate_database()

        assert result is False
        mock_ensure.assert_called_once()


class TestLocalModelConfigPanelVerificationState:
    """Tests for LocalModelConfigPanelViewModel verification state management"""

    def test_is_verifying_initial_state(self, isolated_config, mock_verify_model):
        """Test state.is_verifying is initially False"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_double_verify_prevention(self, isolated_config, mock_verify_model):
        """Test that double verification is prevented"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.gguf")
        vm.update_timeout("300")
        # 模拟正在验证中
        vm._set_state(is_verifying=True)  # type: ignore[attr-defined]

        result = await vm.verify_model()

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_success(self, isolated_config, mock_verify_model):
        """Test is_verifying is reset after successful verification"""
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.gguf")
        vm.update_timeout("300")

        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()

        assert result is True
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_failure(self, isolated_config, mock_verify_model):
        """Test is_verifying is reset after failed verification"""
        # on_verify_model 返回 False 模拟验证失败
        mock_verify_model.return_value = False
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.gguf")
        vm.update_timeout("300")

        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()

        assert result is False
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_exception(self, isolated_config, mock_verify_model):
        """Test is_verifying is reset after exception"""
        # on_verify_model 抛异常模拟验证异常
        mock_verify_model.side_effect = Exception("Test error")
        vm = _make_local_vm(isolated_config, mock_verify_model)
        vm.update_model_path("/path/to/model.gguf")
        vm.update_timeout("300")

        with patch("os.path.exists", return_value=True):
            result = await vm.verify_model()

        assert result is False
        assert vm.state.is_verifying is False
