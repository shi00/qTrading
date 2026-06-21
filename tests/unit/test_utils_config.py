import os
import uuid
from unittest.mock import patch

import pytest

from utils.config_handler import ConfigHandler

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_config(tmp_path):
    import utils.config_handler as config_module

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


class TestConfigHandler:
    def test_load_config_empty(self, isolated_config):
        config_data = ConfigHandler.load_config()
        assert config_data == {}

    def test_save_and_load_config(self, isolated_config):
        test_data = {"test_key": "test_value"}
        result = ConfigHandler.save_config(test_data)
        assert result is True

        loaded_data = ConfigHandler.load_config()
        assert loaded_data.get("test_key") == "test_value"

    def test_get_save_token(self, isolated_config):
        token = "test_token_123"
        with (
            patch("utils.config_handler.keyring.set_password"),
            patch("utils.config_handler.keyring.get_password", return_value=token),
        ):
            ConfigHandler.save_token(token)
            assert ConfigHandler.get_token() == token

    def test_onboarding_status(self, isolated_config):
        assert ConfigHandler.is_onboarding_complete() is False

        ConfigHandler.set_onboarding_complete(True)
        assert ConfigHandler.is_onboarding_complete() is True

    def test_auto_update_settings(self, isolated_config):
        assert ConfigHandler.get_auto_update_time() == "16:30"
        assert ConfigHandler.is_auto_update_enabled() is False

        ConfigHandler.save_config(
            {"auto_update_enabled": True, "auto_update_time": "18:00"},
        )

        assert ConfigHandler.is_auto_update_enabled() is True
        assert ConfigHandler.get_auto_update_time() == "18:00"

    def test_log_settings_defaults(self, isolated_config):
        assert ConfigHandler.get_log_max_mb() == 5
        assert ConfigHandler.get_log_backup_count() == 5

    def test_log_settings_custom(self, isolated_config):
        ConfigHandler.save_config({"log_max_mb": 10, "log_backup_count": 3})
        assert ConfigHandler.get_log_max_mb() == 10
        assert ConfigHandler.get_log_backup_count() == 3

    def test_load_config_error(self, isolated_config):
        with open(isolated_config, "w") as f:
            f.write("{invalid_json")

        ConfigHandler._config_cache = None
        config_data = ConfigHandler.load_config()
        assert config_data == {}

    def test_save_config_error(self, isolated_config):
        with patch("builtins.open", side_effect=PermissionError("Denied")):
            result = ConfigHandler.save_config({"a": 1})
            assert result is False

    def test_save_db_password_falls_back_to_encrypt_data_when_keyring_unavailable(self, isolated_config):
        with (
            patch(
                "utils.config_handler.keyring.set_password",
                side_effect=RuntimeError("keyring unavailable"),
            ),
            patch(
                "utils.config_handler.SecurityManager.encrypt_data",
                return_value="encrypted-secret",
            ) as mock_encrypt,
        ):
            result = ConfigHandler.save_db_password("secret")

        assert result is True
        mock_encrypt.assert_called_once_with("secret")
        assert ConfigHandler.load_config().get("db_password_encrypted") == "encrypted-secret"
