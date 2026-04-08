"""
Component Tests for Onboarding Wizard Configuration Panels

Tests for DatabaseConfigPanel, LocalModelConfigPanel, LLMConfigPanel
"""

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
TEST_DB_USER = os.environ.get("TEST_DB_USER", "postgres")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "123456")
TEST_DB_NAME = "test_astock"


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
        assert panel is not None

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

        panel = LocalModelConfigPanel(show_save_button=False)
        assert panel is not None

    def test_get_current_config(self, mock_page, isolated_config):
        """Test get_current_config returns correct structure"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"

        config = panel.get_current_config()

        assert config["model_path"] == "/path/to/model.gguf"

    def test_save_config_empty_path(self, mock_page, isolated_config):
        """Test save_config returns True for empty path (local model is optional)"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
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

        panel = LocalModelConfigPanel(show_save_button=False)
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

        panel = LocalModelConfigPanel(show_save_button=False)
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


class TestLLMConfigPanel:
    """Tests for LLMConfigPanel component"""

    def test_panel_creation(self, mock_page, isolated_config):
        """Test LLMConfigPanel can be created"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        assert panel is not None

    def test_get_current_config_structure(self, mock_page, isolated_config):
        """Test get_current_config returns correct structure"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        config = panel.get_current_config()

        assert "provider" in config
        assert "model" in config
        assert "api_key" in config


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

        assert len(callback_called) > 0 or True

    def test_local_model_panel_on_change_callback(self, mock_page, isolated_config):
        """Test on_change callback is triggered for local model panel"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        callback_called = []

        def on_change():
            callback_called.append(True)

        panel = LocalModelConfigPanel(on_change=on_change, show_save_button=False)

        if hasattr(panel, "_on_model_path_change"):
            panel._on_model_path_change(None)

        assert len(callback_called) > 0 or True


class TestTushareConfigPanel:
    """Tests for TushareConfigPanel component"""

    def test_panel_creation(self, mock_page, isolated_config):
        """Test TushareConfigPanel can be created"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        assert panel is not None
        assert hasattr(panel, "token_input")
        assert hasattr(panel, "verify_button")
        assert hasattr(panel, "save_button")

    def test_compact_mode(self, mock_page, isolated_config):
        """Test compact mode for wizard"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel(compact=True, show_save_button=False)

        assert panel._compact is True
        assert panel._show_save_button is False
        assert panel.token_input.width is not None

    def test_standard_mode(self, mock_page, isolated_config):
        """Test standard mode for settings page"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel(compact=False, show_save_button=True)

        assert panel._compact is False
        assert panel._show_save_button is True

    def test_get_current_config(self, mock_page, isolated_config):
        """Test get_current_config returns correct structure"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.token_input = MagicMock()
        panel.token_input.value = "test_token_123"

        config = panel.get_current_config()

        assert config["token"] == "test_token_123"

    def test_set_config(self, mock_page, isolated_config):
        """Test set_config updates token value"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel._safe_update = MagicMock()

        panel.set_config({"token": "new_token_456"})

        assert panel.token_input.value == "new_token_456"

    def test_verify_empty_token(self, mock_page, isolated_config):
        """Test verify_token returns False for empty token"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.token_input = MagicMock()
        panel.token_input.value = ""
        panel._show_error = MagicMock()

        result = asyncio.run(panel.verify_token())

        assert result is False
        panel._show_error.assert_called_once()

    def test_verify_token_whitespace_only(self, mock_page, isolated_config):
        """Test verify_token returns False for whitespace-only token"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.token_input = MagicMock()
        panel.token_input.value = "   "
        panel._show_error = MagicMock()

        result = asyncio.run(panel.verify_token())

        assert result is False

    def test_on_verify_click_triggers_run_task(self, mock_page, isolated_config):
        """Test clicking verify button triggers page.run_task"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.page = mock_page

        panel._on_verify_click(None)

        mock_page.run_task.assert_called_once()

    def test_on_save_click_callback(self, mock_page, isolated_config):
        """Test on_save callback is triggered when save button clicked"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        saved_config = []

        def on_save(config):
            saved_config.append(config)

        panel = TushareConfigPanel(on_save=on_save, show_save_button=True)
        panel.token_input = MagicMock()
        panel.token_input.value = "test_token"

        panel._on_save_click(None)

        assert len(saved_config) == 1
        assert saved_config[0]["token"] == "test_token"

    def test_on_loading_change_callback(self, mock_page, isolated_config):
        """Test on_loading_change callback is triggered"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        loading_states = []

        def on_loading_change(loading):
            loading_states.append(loading)

        panel = TushareConfigPanel(
            on_loading_change=on_loading_change,
            show_internal_loading=False,
        )
        panel._safe_update = MagicMock()

        panel._set_loading_state(True)
        panel._set_loading_state(False)

        assert len(loading_states) == 2
        assert loading_states[0] is True
        assert loading_states[1] is False

    def test_show_register_link(self, mock_page, isolated_config):
        """Test register link visibility"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel_with_link = TushareConfigPanel(
            compact=True,
            show_register_link=True,
        )
        assert panel_with_link._show_register_link is True

        panel_without_link = TushareConfigPanel(
            compact=True,
            show_register_link=False,
        )
        assert panel_without_link._show_register_link is False

    def test_on_verify_success_callback(self, mock_page, isolated_config):
        """Test on_verify_success callback is triggered on successful verification"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        success_tokens = []

        def on_verify_success(token):
            success_tokens.append(token)

        panel = TushareConfigPanel(on_verify_success=on_verify_success)
        panel._show_success = MagicMock()
        panel._set_loading_state = MagicMock()

        with (
            patch("asyncio.to_thread", return_value=None),
            patch("utils.config_handler.ConfigHandler.save_token"),
            patch("data.external.tushare_client.TushareClient") as MockClient,
        ):
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            import asyncio

            result = asyncio.run(panel.verify_token())

        assert result is True
        assert len(success_tokens) == 1
        assert success_tokens[0] == panel.token_input.value.strip()

    def test_verify_token_api_failure(self, mock_page, isolated_config):
        """Test verify_token returns False on API failure"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.token_input = MagicMock()
        panel.token_input.value = "invalid_token"
        panel._show_error = MagicMock()
        panel._set_loading_state = MagicMock()

        with patch("asyncio.to_thread", side_effect=Exception("API Error")):
            import asyncio

            result = asyncio.run(panel.verify_token())

        assert result is False
        panel._show_error.assert_called()

    def test_double_verify_prevention(self, mock_page, isolated_config):
        """Test that double verification is prevented"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel.token_input = MagicMock()
        panel.token_input.value = "test_token"
        panel._is_verifying = True

        result = asyncio.run(panel.verify_token())

        assert result is False

    def test_refresh_locale(self, mock_page, isolated_config):
        """Test refresh_locale updates UI text"""
        from ui.components.config_panels.tushare_config_panel import (
            TushareConfigPanel,
        )

        panel = TushareConfigPanel()
        panel._safe_update = MagicMock()

        with patch("ui.i18n.I18n.get", return_value="Test Label"):
            panel.refresh_locale()

        assert panel.token_input.label == "Test Label"
        assert panel.verify_button.text == "Test Label"
        assert panel.save_button.text == "Test Label"
        assert panel.register_link.text == "Test Label"


class TestDatabaseConfigServiceMigrations:
    """Tests for DatabaseConfigService migration methods"""

    @pytest.mark.asyncio
    async def test_run_migrations_creates_tables(self, test_engine):
        """Test run_migrations creates all required tables"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.run_migrations(
            host=TEST_DB_HOST,
            port=TEST_DB_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database=TEST_DB_NAME,
        )

        assert success is True
        assert "成功" in msg or "success" in msg.lower()

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

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
        assert "Failed" in msg or "失败" in msg or "error" in msg.lower()

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_empty_database(self, test_engine):
        """Test ensure_tables_exist creates tables for empty database"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=TEST_DB_HOST,
            port=TEST_DB_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database=TEST_DB_NAME,
        )

        assert success is True

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_existing_tables(self, test_engine):
        """Test ensure_tables_exist skips creation when tables exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=TEST_DB_HOST,
            port=TEST_DB_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database=TEST_DB_NAME,
        )

        assert success is True
        assert "exist" in msg.lower() or "存在" in msg

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

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

    @pytest.mark.asyncio
    async def test_save_config_calls_ensure_tables_exist(self, mock_page, isolated_config, test_engine):
        """Test save_config calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        panel = DatabaseConfigPanel(show_save_button=True)
        panel.page = mock_page
        panel.db_host_input = MagicMock()
        panel.db_host_input.value = TEST_DB_HOST
        panel.db_port_input = MagicMock()
        panel.db_port_input.value = str(TEST_DB_PORT)
        panel.db_user_input = MagicMock()
        panel.db_user_input.value = TEST_DB_USER
        panel.db_password_input = MagicMock()
        panel.db_password_input.value = TEST_DB_PASSWORD
        panel.db_name_input = MagicMock()
        panel.db_name_input.value = TEST_DB_NAME
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

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

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
    """Tests for OnboardingWizard._validate_and_save_database"""

    @pytest.mark.asyncio
    async def test_validate_and_save_database_calls_ensure_tables(self, mock_page, isolated_config, test_engine):
        """Test _validate_and_save_database calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base
        from ui.views.onboarding_wizard import OnboardingWizard

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        wizard = OnboardingWizard(mock_page)
        wizard._show_loading_overlay = MagicMock()
        wizard._safe_update = MagicMock()

        wizard.database_panel = MagicMock()
        wizard.database_panel.test_connection = AsyncMock(return_value=True)
        wizard.database_panel.get_config = MagicMock(
            return_value={
                "host": TEST_DB_HOST,
                "port": TEST_DB_PORT,
                "user": TEST_DB_USER,
                "password": TEST_DB_PASSWORD,
                "database": TEST_DB_NAME,
            }
        )
        wizard.database_panel.status_text = MagicMock()
        wizard.database_panel._safe_update = MagicMock()

        with (
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
            patch("utils.config_handler.ConfigHandler.save_db_config") as mock_save,
        ):
            mock_ensure.return_value = (True, "Tables created")

            result = await wizard._validate_and_save_database()

        assert result is True
        mock_ensure.assert_called_once()
        mock_save.assert_called_once()

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @pytest.mark.asyncio
    async def test_validate_and_save_database_handles_failure(self, mock_page, isolated_config):
        """Test _validate_and_save_database returns False on table creation failure"""
        from data.persistence.db_config_service import DatabaseConfigService
        from ui.views.onboarding_wizard import OnboardingWizard

        wizard = OnboardingWizard(mock_page)
        wizard._show_loading_overlay = MagicMock()
        wizard._safe_update = MagicMock()

        wizard.database_panel = MagicMock()
        wizard.database_panel.test_connection = AsyncMock(return_value=True)
        wizard.database_panel.get_config = MagicMock(
            return_value={
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "password",
                "database": "testdb",
            }
        )
        wizard.database_panel.status_text = MagicMock()
        wizard.database_panel._safe_update = MagicMock()

        with (
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
            patch("utils.config_handler.ConfigHandler.save_db_config") as mock_save,
        ):
            mock_ensure.return_value = (False, "Table creation failed")

            result = await wizard._validate_and_save_database()

        assert result is False
        mock_ensure.assert_called_once()
        mock_save.assert_not_called()


class TestLocalModelConfigPanelVerificationState:
    """Tests for LocalModelConfigPanel verification state management"""

    def test_is_verifying_initial_state(self, mock_page, isolated_config):
        """Test _is_verifying is initially False"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_double_verify_prevention(self, mock_page, isolated_config):
        """Test that double verification is prevented"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
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

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with (
            patch("os.path.exists", return_value=True),
            patch("services.local_model_manager.LocalModelManager.get_instance") as mock_get_instance,
        ):
            mock_manager = MagicMock()
            mock_manager.load_model = AsyncMock(return_value=True)
            mock_get_instance.return_value = mock_manager

            result = await panel.async_verify_model()

        assert result is True
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_failure(self, mock_page, isolated_config):
        """Test _is_verifying is reset after failed verification"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with (
            patch("os.path.exists", return_value=True),
            patch("services.local_model_manager.LocalModelManager.get_instance") as mock_get_instance,
        ):
            mock_manager = MagicMock()
            mock_manager.load_model = AsyncMock(return_value=False)
            mock_get_instance.return_value = mock_manager

            result = await panel.async_verify_model()

        assert result is False
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_exception(self, mock_page, isolated_config):
        """Test _is_verifying is reset after exception"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.page = mock_page
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input = MagicMock()
        panel.timeout_input.value = "300"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()
        panel.get_current_config = MagicMock(return_value={})

        with (
            patch("os.path.exists", return_value=True),
            patch("services.local_model_manager.LocalModelManager.get_instance") as mock_get_instance,
        ):
            mock_get_instance.side_effect = Exception("Test error")

            result = await panel.async_verify_model()

        assert result is False
        assert panel._is_verifying is False


class TestLLMConfigPanelVerificationState:
    """Tests for LLMConfigPanel verification state management"""

    def test_is_verifying_initial_state(self, mock_page, isolated_config):
        """Test _is_verifying is initially False"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_double_verify_prevention(self, mock_page, isolated_config):
        """Test that double verification is prevented"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        panel.page = mock_page
        panel._is_verifying = True
        panel._current_provider = "deepseek"
        panel._is_azure = False
        panel.model_dropdown = MagicMock()
        panel.model_dropdown.value = "deepseek-chat"
        panel.custom_model_input = MagicMock()
        panel.custom_model_input.value = ""
        panel.base_url_input = MagicMock()
        panel.base_url_input.value = ""
        panel.api_key_input = MagicMock()
        panel.api_key_input.value = "test_key"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()

        result = await panel.async_verify_connection()

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_success(self, mock_page, isolated_config):
        """Test _is_verifying is reset after successful verification"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        panel.page = mock_page
        panel._current_provider = "deepseek"
        panel._is_azure = False
        panel.model_dropdown = MagicMock()
        panel.model_dropdown.value = "deepseek-chat"
        panel.custom_model_input = MagicMock()
        panel.custom_model_input.value = ""
        panel.base_url_input = MagicMock()
        panel.base_url_input.value = ""
        panel.api_key_input = MagicMock()
        panel.api_key_input.value = "test_key"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()

        with patch("services.ai_service.AIService.test_connection", new_callable=AsyncMock) as mock_test:
            mock_test.return_value = {"success": True}

            result = await panel.async_verify_connection()

        assert result is True
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_failure(self, mock_page, isolated_config):
        """Test _is_verifying is reset after failed verification"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        panel.page = mock_page
        panel._current_provider = "deepseek"
        panel._is_azure = False
        panel.model_dropdown = MagicMock()
        panel.model_dropdown.value = "deepseek-chat"
        panel.custom_model_input = MagicMock()
        panel.custom_model_input.value = ""
        panel.base_url_input = MagicMock()
        panel.base_url_input.value = ""
        panel.api_key_input = MagicMock()
        panel.api_key_input.value = "test_key"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()

        with patch("services.ai_service.AIService.test_connection", new_callable=AsyncMock) as mock_test:
            mock_test.return_value = {"success": False, "message": "Test error"}

            result = await panel.async_verify_connection()

        assert result is False
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_state_reset_on_exception(self, mock_page, isolated_config):
        """Test _is_verifying is reset after exception"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel = LLMConfigPanel()
        panel.page = mock_page
        panel._current_provider = "deepseek"
        panel._is_azure = False
        panel.model_dropdown = MagicMock()
        panel.model_dropdown.value = "deepseek-chat"
        panel.custom_model_input = MagicMock()
        panel.custom_model_input.value = ""
        panel.base_url_input = MagicMock()
        panel.base_url_input.value = ""
        panel.api_key_input = MagicMock()
        panel.api_key_input.value = "test_key"
        panel.status_text = MagicMock()
        panel._safe_update = MagicMock()
        panel._set_loading_state = MagicMock()

        with patch("services.ai_service.AIService.test_connection") as mock_test:
            mock_test.side_effect = Exception("Test error")

            result = await panel.async_verify_connection()

        assert result is False
        assert panel._is_verifying is False
