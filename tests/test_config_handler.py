import unittest
import os
import json
import tempfile
from unittest.mock import patch, mock_open
from utils.config_handler import ConfigHandler
import config

class TestConfigHandler(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary file for config
        self.test_dir = tempfile.TemporaryDirectory()
        self.config_file = os.path.join(self.test_dir.name, "user_settings.json")
        
        # Patch the CONFIG_FILE path in ConfigHandler
        self.patcher = patch('utils.config_handler.CONFIG_FILE', self.config_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.test_dir.cleanup()

    def test_load_config_empty(self):
        """Test loading when file doesn't exist"""
        if os.path.exists(self.config_file):
            os.remove(self.config_file)
            
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
        # Default should be False
        self.assertFalse(ConfigHandler.is_onboarding_complete())
        
        # Set to True
        ConfigHandler.set_onboarding_complete(True)
        self.assertTrue(ConfigHandler.is_onboarding_complete())

    def test_auto_update_settings(self):
        """Test auto update settings"""
        # Default
        self.assertEqual(ConfigHandler.get_auto_update_time(), "16:30")
        self.assertFalse(ConfigHandler.is_auto_update_enabled())
        
        # Change settings via generic save
        ConfigHandler.save_config({
            "auto_update_enabled": True,
            "auto_update_time": "18:00"
        })
        
        self.assertTrue(ConfigHandler.is_auto_update_enabled())
        self.assertEqual(ConfigHandler.get_auto_update_time(), "18:00")

    def test_log_settings_defaults(self):
        """Test log settings default values"""
        self.assertEqual(ConfigHandler.get_log_max_mb(), 5)
        self.assertEqual(ConfigHandler.get_log_backup_count(), 5)

    def test_log_settings_custom(self):
        """Test custom log settings"""
        ConfigHandler.save_config({
            "log_max_mb": 10,
            "log_backup_count": 3
        })
        self.assertEqual(ConfigHandler.get_log_max_mb(), 10)
        self.assertEqual(ConfigHandler.get_log_backup_count(), 3)

    def test_load_config_error(self):
        """Test handling of corrupt config file"""
        with open(self.config_file, 'w') as f:
            f.write("{invalid_json")
            
        # Should return empty dict on error
        config_data = ConfigHandler.load_config()
        self.assertEqual(config_data, {})

    def test_save_config_error(self):
        """Test handling of save error (e.g. permission denied)"""
        # Mock open to raise exception
        with patch('builtins.open', side_effect=PermissionError("Denied")):
            result = ConfigHandler.save_config({"a": 1})
            self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
