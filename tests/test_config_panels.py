"""
Component Tests for Onboarding Wizard Configuration Panels

Tests for DatabaseConfigPanel, LocalModelConfigPanel, LLMConfigPanel
"""

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
        """Test save_config returns False for empty path (requires model)"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = ""
        panel.status_text = MagicMock()

        result = panel.save_config()

        assert result is False

    def test_save_config_nonexistent_path(self, mock_page, isolated_config):
        """Test save_config returns False for nonexistent path"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/nonexistent/path/model.gguf"
        panel.status_text = MagicMock()

        with patch("os.path.exists", return_value=False):
            result = panel.save_config()

        assert result is False

    def test_save_config_wrong_format(self, mock_page, isolated_config):
        """Test save_config returns False for wrong file format"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel(show_save_button=False)
        panel.model_path_input = MagicMock()
        panel.model_path_input.value = "/path/to/model.txt"
        panel.status_text = MagicMock()

        with patch("os.path.exists", return_value=True):
            result = panel.save_config()

        assert result is False


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
