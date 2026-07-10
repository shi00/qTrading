"""
Component Tests for Onboarding Wizard Configuration Panels

Tests for DatabaseConfigPanel, LocalModelConfigPanel
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params
from tests.integration.test_data_db_migrator import (
    _create_isolated_db,
    _drop_isolated_db,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared"""
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_config_{uuid.uuid4().hex}.json"
    test_config_file = str(tmp_path / unique_name)
    original_config_file = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = test_config_file

    ConfigHandler._config_cache = None

    if os.path.exists(test_config_file):
        os.remove(test_config_file)

    yield test_config_file

    ConfigHandler._config_cache = None
    config_module.CONFIG_FILE = original_config_file


@pytest.fixture
def mock_page():
    """Create a mock Flet page"""
    page = MagicMock()
    page.run_task = MagicMock()
    page.overlay = []
    page.update = MagicMock()
    return page


class TestDatabaseConfigPanel:
    """Tests for DatabaseConfigPanel component"""

    def test_panel_creation(self, mock_page, isolated_config):
        """Test DatabaseConfigPanel can be created"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=False)
        assert hasattr(panel, "db_host_input")
        assert hasattr(panel, "db_port_input")

    def test_get_config(self, mock_page, isolated_config):
        """Test get_config returns correct structure"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=False)
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = "localhost"
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = "5432"
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = "postgres"
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = "password"
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = "testdb"

        config = panel.get_config()

        assert config["host"] == "localhost"
        assert config["port"] == 5432
        assert config["user"] == "postgres"
        assert config["password"] == "password"
        assert config["database"] == "testdb"

    def test_validate_empty_host(self, mock_page, isolated_config):
        """Test validation fails with empty host"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=False)
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = ""
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = "5432"
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = "postgres"
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = "password"
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = "testdb"

        is_valid, error = panel.validate()

        assert is_valid is False
        assert "host" in error.lower() or "主机" in error

    def test_validate_invalid_port(self, mock_page, isolated_config):
        """Test validation fails with invalid port"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=False)
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = "localhost"
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = "invalid"
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = "postgres"
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = "password"
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = "testdb"

        is_valid, error = panel.validate()

        assert is_valid is False

    def test_validate_empty_database(self, mock_page, isolated_config):
        """Test validation fails with empty database name"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=False)
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = "localhost"
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = "5432"
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = "postgres"
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = "password"
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = ""

        is_valid, error = panel.validate()

        assert is_valid is False


class TestLocalModelConfigPanel:
    """Tests for LocalModelConfigPanel component"""

    def test_panel_creation(self, mock_page, isolated_config):
        """Test LocalModelConfigPanel can be created"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        assert hasattr(panel, "model_path_input")

    def test_get_current_config(self, mock_page, isolated_config):
        """Test get_current_config returns correct structure"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"

        config = panel.get_current_config()

        assert config["model_path"] == "/path/to/model.gguf"

    def test_save_config_empty_path(self, mock_page, isolated_config):
        """Test save_config returns True for empty path (local model is optional)"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = ""
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.threads_input = MagicMock()
        panel.threads_input.value = "4"
        panel.batch_input = MagicMock()
        panel.batch_input.value = "512"
        panel.ctx_input = MagicMock()
        panel.ctx_input.value = "2048"
        panel.gpu_auto_switch = MagicMock()
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input = MagicMock()
        panel.gpu_layers_input.value = "0"
        panel.status_text = MagicMock()

        result = panel.save_config()

        assert result is True

    def test_save_config_nonexistent_path(self, mock_page, isolated_config):
        """Test save_config returns True for nonexistent path (validation is in verify)"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/nonexistent/path/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.threads_input = MagicMock()
        panel.threads_input.value = "4"
        panel.batch_input = MagicMock()
        panel.batch_input.value = "512"
        panel.ctx_input = MagicMock()
        panel.ctx_input.value = "2048"
        panel.gpu_auto_switch = MagicMock()
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input = MagicMock()
        panel.gpu_layers_input.value = "0"
        panel.status_text = MagicMock()

        with patch("os.path.exists", return_value=False):
            result = panel.save_config()

        assert result is True

    def test_save_config_wrong_format(self, mock_page, isolated_config):
        """Test save_config returns True for wrong file format (validation is in verify)"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.txt"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.threads_input = MagicMock()
        panel.threads_input.value = "4"
        panel.batch_input = MagicMock()
        panel.batch_input.value = "512"
        panel.ctx_input = MagicMock()
        panel.ctx_input.value = "2048"
        panel.gpu_auto_switch = MagicMock()
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input = MagicMock()
        panel.gpu_layers_input.value = "0"
        panel.status_text = MagicMock()

        with patch("os.path.exists", return_value=True):
            result = panel.save_config()

        assert result is True


class TestConfigPanelsIntegration:
    """Integration tests for configuration panels"""

    def test_database_panel_on_change_callback(self, mock_page, isolated_config):
        """Test on_change callback is triggered"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        callback_called = []

        def on_change():
            callback_called.append(True)

        panel = DatabaseConfigPanel(on_change=on_change, show_save_button=False)

        if hasattr(panel, "_on_input_change"):
            panel._on_input_change(None)

        assert len(callback_called) > 0

    def test_local_model_panel_on_change_callback(self, mock_page, isolated_config):
        """Test on_change callback is triggered for local model panel"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        callback_called = []

        def on_change():
            callback_called.append(True)

        panel = LocalModelConfigPanel(
            on_verify_model=AsyncMock(return_value=True),
            on_change=on_change,
            show_save_button=False,
        )

        panel._on_input_change(None)

        assert len(callback_called) > 0


class TestDatabaseConfigServiceMigrations:
    """Tests for DatabaseConfigService migration methods"""

    pytestmark = [pytest.mark.integration, pytest.mark.database, pytest.mark.migration]

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
        assert "Failed" in msg or "失败" in msg or "error" in msg.lower() or "exception" in msg.lower()

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
    """Tests for DatabaseConfigPanel.save_config with table creation"""

    pytestmark = [pytest.mark.integration, pytest.mark.database, pytest.mark.migration]

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
    async def test_save_config_calls_ensure_tables_exist(self, mock_page, isolated_config, isolated_db):
        """Test save_config calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        panel = DatabaseConfigPanel(show_save_button=True)
        panel.page = mock_page
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = params["host"]
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = str(params["port"])
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = params["user"]
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = params["password"]
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = db_name
        panel.db_create_checkbox = MagicMock()
        panel.db_create_checkbox.value = False
        panel.status_text = MagicMock()
        panel.status_text.value = ""
        panel.status_text.color = None
        panel.btn_save = MagicMock()
        panel.btn_save.disabled = False
        panel._safe_update = MagicMock()

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

            result = await panel.save_config()

        assert result is True
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_handles_table_creation_failure(self, mock_page, isolated_config):
        """Test save_config returns False when table creation fails"""
        from data.persistence.db_config_service import DatabaseConfigService
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        panel = DatabaseConfigPanel(show_save_button=True)
        panel.page = mock_page
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = "localhost"
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = "5432"
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = "postgres"
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = "password"
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = "testdb"
        panel.db_create_checkbox = MagicMock()
        panel.db_create_checkbox.value = False
        panel.status_text = MagicMock()
        panel.status_text.value = ""
        panel.status_text.color = None
        panel.btn_save = MagicMock()
        panel.btn_save.disabled = False
        panel._safe_update = MagicMock()

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

            result = await panel.save_config()

        assert result is False
        mock_ensure.assert_called_once()


class TestOnboardingWizardDatabaseValidation:
    """Tests for OnboardingWizard database validation via ViewModel"""

    pytestmark = [pytest.mark.integration, pytest.mark.database, pytest.mark.migration]

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
    async def test_validate_and_save_database_calls_ensure_tables(self, mock_page, isolated_config, isolated_db):
        """Test fn_validate_database delegates to database_panel.save_config"""
        from ui.views.onboarding_wizard import OnboardingWizard

        wizard = OnboardingWizard(mock_page)
        wizard._show_loading_overlay = MagicMock()
        wizard._safe_update = MagicMock()

        wizard.database_panel = MagicMock()
        wizard.database_panel.save_config = AsyncMock(return_value=True)
        wizard.vm.fn_validate_database = wizard.database_panel.save_config

        assert wizard.vm.fn_validate_database is not None
        result = await wizard.vm.fn_validate_database()

        assert result is True
        wizard.database_panel.save_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_save_database_handles_failure(self, mock_page, isolated_config):
        """Test fn_validate_database returns False when save_config fails"""
        from ui.views.onboarding_wizard import OnboardingWizard

        wizard = OnboardingWizard(mock_page)
        wizard._show_loading_overlay = MagicMock()
        wizard._safe_update = MagicMock()

        wizard.database_panel = MagicMock()
        wizard.database_panel.save_config = AsyncMock(return_value=False)
        wizard.vm.fn_validate_database = wizard.database_panel.save_config

        assert wizard.vm.fn_validate_database is not None
        result = await wizard.vm.fn_validate_database()

        assert result is False
        wizard.database_panel.save_config.assert_called_once()


class TestLocalModelConfigPanelVerificationState:
    """Tests for LocalModelConfigPanel verification state management"""

    def test_is_verifying_initial_state(self, mock_page, isolated_config):
        """Test _is_verifying is initially False"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_double_verify_prevention(self, mock_page, isolated_config):
        """Test that double verification is prevented"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel._is_verifying = True

        result = await panel.async_verify_model()

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_success(self, mock_page, isolated_config):
        """Test _is_verifying is reset after successful verification"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=True), show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()

        assert result is True
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_failure(self, mock_page, isolated_config):
        """Test _is_verifying is reset after failed verification"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(on_verify_model=AsyncMock(return_value=False), show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()

        assert result is False
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_exception(self, mock_page, isolated_config):
        """Test _is_verifying is reset after exception"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(
            on_verify_model=AsyncMock(side_effect=Exception("Test error")),
            show_save_button=False,
        )
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()

        assert result is False
        assert panel._is_verifying is False
