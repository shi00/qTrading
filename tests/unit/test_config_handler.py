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


class TestConfigHandlerSaveConfigReplace:
    @patch.object(cfg_mod.ConfigHandler, "_save_json_atomically", return_value=True)
    def test_replace_mode(self, mock_save):
        cfg_mod.ConfigHandler._config_cache = {"old_key": "old_val"}
        result = cfg_mod.ConfigHandler.save_config({"new_key": "new_val"}, replace=True)
        assert result is True
        saved_data = mock_save.call_args[0][0]
        assert "new_key" in saved_data
        assert "old_key" not in saved_data
        cfg_mod.ConfigHandler._config_cache = None


class TestConfigHandlerGetDbPassword:
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"db_password_encrypted": "enc_val"})
    @patch.object(cfg_mod.ConfigHandler, "_try_decrypt", return_value="decrypted_pw")
    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    def test_from_encrypted_config(self, mock_kr, mock_decrypt, mock_load):
        result = cfg_mod.ConfigHandler.get_db_password()
        assert result == "decrypted_pw"

    @patch.object(cfg_mod.keyring, "get_password", return_value="keyring_pw")
    def test_from_keyring(self, mock_kr):
        result = cfg_mod.ConfigHandler.get_db_password()
        assert result == "keyring_pw"


class TestConfigHandlerSaveDbPassword:
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password")
    def test_save_to_keyring(self, mock_set, mock_save):
        result = cfg_mod.ConfigHandler.save_db_password("mypassword")
        assert result is True
        mock_set.assert_called_once()

    def test_empty_password(self):
        result = cfg_mod.ConfigHandler.save_db_password("")
        assert result is False


class TestConfigHandlerGetDbConfig:
    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw123")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"db_host": "localhost", "db_port": 5432, "db_user": "admin", "db_name": "testdb"},
    )
    def test_full_config(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["host"] == "localhost"
        assert result["password"] == "pw123"


class TestConfigHandlerGetLlmConfig:
    @patch.object(cfg_mod.keyring, "get_password", return_value="my-key")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={
            "llm_provider": "deepseek",
            "llm_model": "deepseek-chat",
            "llm_base_url": "https://api.deepseek.com",
        },
    )
    def test_deepseek_default_url(self, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_llm_config()
        assert result["provider"] == "deepseek"
        assert result["api_key"] == "my-key"

    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(cfg_mod.ConfigHandler, "_try_decrypt", return_value="")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={
            "llm_provider": "openai",
            "llm_model": "gpt-4",
            "llm_base_url": "",
        },
    )
    def test_openai_default_url(self, mock_load, mock_decrypt, mock_kr):
        result = cfg_mod.ConfigHandler.get_llm_config()
        assert result["base_url"] == "https://api.openai.com"

    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(cfg_mod.ConfigHandler, "_try_decrypt", return_value="")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={
            "llm_provider": "azure",
            "llm_model": "mydeploy",
            "llm_base_url": "https://myresource.openai.azure.com",
            "llm_provider_extras": {
                "azure": {
                    "api_version": "2024-02-01",
                    "resource_name": "myresource",
                    "deployment_name": "mydeploy",
                }
            },
        },
    )
    def test_azure_with_extras(self, mock_load, mock_decrypt, mock_kr):
        result = cfg_mod.ConfigHandler.get_llm_config()
        assert result["azure_resource_name"] == "myresource"
        assert result["azure_deployment_name"] == "mydeploy"


class TestConfigHandlerSaveLlmConfig:
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password")
    def test_save_azure_config(self, mock_kr, mock_save):
        result = cfg_mod.ConfigHandler.save_llm_config(
            provider="azure",
            model="mydeploy",
            base_url="",
            api_key="test-key",
            azure_resource_name="myresource",
            azure_deployment_name="mydeploy",
        )
        assert result is True

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "delete_password")
    def test_save_empty_key_clears(self, mock_del, mock_save):
        result = cfg_mod.ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key="",
        )
        assert result is True


class TestConfigHandlerLocalAiTimeout:
    @patch.object(cfg_mod.ConfigHandler, "get_setting", return_value=60)
    def test_get_timeout(self, mock_setting):
        result = cfg_mod.ConfigHandler.get_local_ai_timeout()
        assert result == 60

    @patch.object(cfg_mod.ConfigHandler, "get_setting", return_value=None)
    def test_get_timeout_none(self, mock_setting):
        result = cfg_mod.ConfigHandler.get_local_ai_timeout()
        assert result is None

    @patch.object(cfg_mod.ConfigHandler, "get_setting", return_value="invalid")
    def test_get_timeout_invalid(self, mock_setting):
        result = cfg_mod.ConfigHandler.get_local_ai_timeout()
        assert result is None

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_timeout(self, mock_save):
        cfg_mod.ConfigHandler.set_local_ai_timeout(120)
        mock_save.assert_called_once()


class TestConfigHandlerSaveLocalAiConfig:
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_with_kwargs(self, mock_save):
        cfg_mod.ConfigHandler.save_local_ai_config(
            "/path/to/model.gguf",
            timeout=60,
            n_threads=8,
            n_batch=2048,
            n_ctx=8192,
            flash_attn=False,
            n_gpu_layers=1,
        )
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved["local_model_path"] == "/path/to/model.gguf"
        assert saved["local_n_threads"] == 8


class TestConfigHandlerNoProxyDomains:
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"no_proxy_domains": ["localhost", "127.0.0.1"]})
    def test_get_domains(self, mock_load):
        result = cfg_mod.ConfigHandler.get_no_proxy_domains()
        assert result == ["localhost", "127.0.0.1"]

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"no_proxy_domains": "not_a_list"})
    def test_invalid_format(self, mock_load):
        result = cfg_mod.ConfigHandler.get_no_proxy_domains()
        assert result == []

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_domains(self, mock_save):
        result = cfg_mod.ConfigHandler.set_no_proxy_domains(["example.com"])
        assert result is True

    def test_set_invalid_domains(self):
        result = cfg_mod.ConfigHandler.set_no_proxy_domains("not_a_list")
        assert result is False


class TestConfigHandlerSyncIntegrityConfig:
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_defaults(self, mock_load):
        result = cfg_mod.ConfigHandler.get_sync_integrity_config()
        assert result["quotes_tolerance_ratio"] == 0.95
        assert result["quality_threshold"] == 80

    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"sync_integrity": {"quotes_tolerance_ratio": 0.90, "quality_threshold": 70}},
    )
    def test_custom(self, mock_load):
        result = cfg_mod.ConfigHandler.get_sync_integrity_config()
        assert result["quotes_tolerance_ratio"] == 0.90
        assert result["quality_threshold"] == 70


class TestConfigHandlerEnsureDefaults:
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    @patch.object(cfg_mod.ConfigHandler, "_deep_merge_defaults", return_value=({"auto_update_enabled": True}, True))
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_merges_and_saves(self, mock_save, mock_merge, mock_load):
        cfg_mod.ConfigHandler.ensure_defaults()
        mock_save.assert_called_once()


class TestConfigHandlerGetTokenKeyringMigration:
    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"ts_token": "encrypted_val"})
    @patch.object(cfg_mod.ConfigHandler, "_try_decrypt", return_value="decrypted_token")
    @patch.object(cfg_mod.keyring, "set_password")
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_migrates_to_keyring(self, mock_save, mock_set_pw, mock_decrypt, mock_load, mock_get_pw):
        result = cfg_mod.ConfigHandler.get_token()
        assert result == "decrypted_token"
        mock_set_pw.assert_called_once()
