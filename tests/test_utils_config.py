import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.config_handler import CONFIG_FILE, ConfigHandler


class TestConfigHandler(unittest.TestCase):
    def setUp(self):
        ConfigHandler._config_cache = None
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

    def test_load_config_empty(self):
        """Test loading when file doesn't exist"""
        config_data = ConfigHandler.load_config()
        self.assertEqual(config_data, {})

    def test_save_and_load_config(self):
        """Test saving and then loading config"""
        test_data = {"test_key": "test_value"}
        result = ConfigHandler.save_config(test_data)
        self.assertTrue(result)

        loaded_data = ConfigHandler.load_config()
        self.assertEqual(loaded_data.get("test_key"), "test_value")

    def test_get_save_token(self):
        """Test getting and saving token"""
        token = "test_token_123"
        ConfigHandler.save_token(token)
        self.assertEqual(ConfigHandler.get_token(), token)

    def test_onboarding_status(self):
        """Test onboarding status"""
        self.assertFalse(ConfigHandler.is_onboarding_complete())

        ConfigHandler.set_onboarding_complete(True)
        self.assertTrue(ConfigHandler.is_onboarding_complete())

    def test_auto_update_settings(self):
        """Test auto update settings"""
        self.assertEqual(ConfigHandler.get_auto_update_time(), "16:30")
        self.assertFalse(ConfigHandler.is_auto_update_enabled())

        ConfigHandler.save_config(
            {"auto_update_enabled": True, "auto_update_time": "18:00"},
        )

        self.assertTrue(ConfigHandler.is_auto_update_enabled())
        self.assertEqual(ConfigHandler.get_auto_update_time(), "18:00")

    def test_log_settings_defaults(self):
        """Test log settings default values"""
        self.assertEqual(ConfigHandler.get_log_max_mb(), 5)
        self.assertEqual(ConfigHandler.get_log_backup_count(), 5)

    def test_log_settings_custom(self):
        """Test custom log settings"""
        ConfigHandler.save_config({"log_max_mb": 10, "log_backup_count": 3})
        self.assertEqual(ConfigHandler.get_log_max_mb(), 10)
        self.assertEqual(ConfigHandler.get_log_backup_count(), 3)

    def test_load_config_error(self):
        """Test handling of corrupt config file"""
        with open(CONFIG_FILE, "w") as f:
            f.write("{invalid_json")

        ConfigHandler._config_cache = None
        config_data = ConfigHandler.load_config()
        self.assertEqual(config_data, {})

    def test_save_config_error(self):
        """Test handling of save error (e.g. permission denied)"""
        from unittest.mock import patch

        with patch("builtins.open", side_effect=PermissionError("Denied")):
            result = ConfigHandler.save_config({"a": 1})
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
