import inspect
from unittest.mock import patch, MagicMock

from utils import config_handler as cfg_mod
from utils.config_handler import ConfigHandler


class TestConfigHandlerDefaults:
    def test_default_config_has_keys(self):
        assert "auto_update_time" in ConfigHandler.DEFAULT_CONFIG
        assert "auto_update_enabled" in ConfigHandler.DEFAULT_CONFIG


class TestConfigHandlerDeepMerge:
    def test_merge_adds_missing_keys(self):
        current = {"a": 1}
        defaults = {"a": 1, "b": 2}
        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)
        assert result["b"] == 2
        assert dirty is True

    def test_merge_no_change(self):
        current = {"a": 1, "b": 2}
        defaults = {"a": 1, "b": 2}
        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)
        assert dirty is False

    def test_merge_nested(self):
        current = {"outer": {"a": 1}}
        defaults = {"outer": {"a": 1, "b": 2}}
        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)
        assert result["outer"]["b"] == 2
        assert dirty is True


class TestConfigHandlerTryDecrypt:
    @patch("utils.config_handler.SecurityManager")
    def test_decrypt_success(self, mock_sm):
        mock_sm.decrypt_data.return_value = "decrypted_value"
        result = ConfigHandler._try_decrypt("encrypted_value")
        assert result == "decrypted_value"

    @patch("utils.config_handler.SecurityManager")
    def test_decrypt_empty(self, mock_sm):
        result = ConfigHandler._try_decrypt("")
        assert result == ""

    @patch("utils.config_handler.SecurityManager")
    def test_decrypt_none(self, mock_sm):
        result = ConfigHandler._try_decrypt(None)
        assert result == ""


class TestConfigHandlerGetToken:
    @patch("utils.config_handler.ConfigHandler.load_config")
    @patch("utils.config_handler.ConfigHandler._try_decrypt")
    def test_get_token(self, mock_decrypt, mock_load):
        mock_load.return_value = {"tushare_token": "encrypted"}
        mock_decrypt.return_value = "decrypted_token"
        result = ConfigHandler.get_token()
        assert result == "decrypted_token"


class TestConfigHandlerIsAutoUpdateEnabled:
    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_enabled(self, mock_load):
        mock_load.return_value = {"auto_update_enabled": True}
        assert ConfigHandler.is_auto_update_enabled() is True

    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_disabled(self, mock_load):
        mock_load.return_value = {"auto_update_enabled": False}
        assert ConfigHandler.is_auto_update_enabled() is False


class TestConfigHandlerGetAutoUpdateTime:
    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_get_time(self, mock_load):
        mock_load.return_value = {"auto_update_time": "09:30"}
        assert ConfigHandler.get_auto_update_time() == "09:30"


class TestConfigHandlerIsDoubaoScheduleEnabled:
    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_enabled(self, mock_load):
        mock_load.return_value = {"doubao_schedule_enabled": True}
        assert ConfigHandler.is_doubao_schedule_enabled() is True


class TestConfigHandlerGetDoubaoScheduleTime:
    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_get_time(self, mock_load):
        mock_load.return_value = {"doubao_schedule_time": "10:00"}
        assert ConfigHandler.get_doubao_schedule_time() == "10:00"


class TestConfigHandlerGetLocalAiConfig:
    @patch("utils.config_handler.ConfigHandler.load_config")
    def test_get_config(self, mock_load):
        mock_load.return_value = {
            "local_model_path": "/path/to/model.gguf",
            "local_model_timeout": 90,
            "n_threads": 4,
            "n_batch": 1024,
            "n_ctx": 4096,
            "n_gpu_layers": 0,
            "flash_attn": True,
        }
        result = ConfigHandler.get_local_ai_config()
        assert result["local_model_path"] == "/path/to/model.gguf"
        assert result["n_threads"] == 4


class TestDbUrlPasswordMasking:
    """db_url 明文口令落盘防护"""

    def test_get_db_url_restores_password(self):
        source = inspect.getsource(cfg_mod.ConfigHandler.get_db_url)
        assert "****" in source
        assert "replace" in source


class TestSaveDbPasswordEncryptFallback:
    """ConfigHandler.save_db_password 加密 fallback 路径"""

    def test_save_db_password_encrypt_failure_returns_false(self):
        with (
            patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable")),
            patch.object(cfg_mod.keyring, "delete_password", MagicMock()),
            patch.object(
                cfg_mod.SecurityManager, "encrypt_data", side_effect=cfg_mod.DecryptionError("encrypt failed")
            ),
            patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True),
        ):
            result = cfg_mod.ConfigHandler.save_db_password("my_password")
            assert result is False


class TestSaveTokenEncryptFallback:
    """ConfigHandler.save_token 加密 fallback 路径"""

    def test_save_token_encrypt_failure_returns_false(self):
        with (
            patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable")),
            patch.object(
                cfg_mod.SecurityManager, "encrypt_data", side_effect=cfg_mod.DecryptionError("encrypt failed")
            ),
        ):
            result = cfg_mod.ConfigHandler.save_token("my_token")
            assert result is False
