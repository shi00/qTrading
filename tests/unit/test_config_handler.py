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
    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch("utils.config_handler.ConfigHandler.load_config")
    @patch("utils.config_handler.ConfigHandler._try_decrypt")
    def test_get_token(self, mock_decrypt, mock_load, mock_kr):
        mock_load.return_value = {"ts_token": "encrypted"}
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
    """get_db_url() 从组件重建 URL，正确 URL-encode 密码"""

    def test_get_db_url_rebuilds_from_components(self):
        """当 db_host 已配置时，从组件重建 URL 而非读取 db_url 字段"""
        with (
            patch.object(
                cfg_mod.ConfigHandler,
                "get_typed",
                side_effect=lambda key, typ, default: {
                    "db_host": "myhost",
                    "db_port": 5433,
                    "db_user": "admin",
                    "db_name": "testdb",
                }.get(key, default),
            ),
            patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="secret123"),
        ):
            url = cfg_mod.ConfigHandler.get_db_url()
            assert url is not None
            assert "secret123" in url
            assert "myhost" in url
            assert "5433" in url
            assert "admin" in url
            assert "testdb" in url
            assert "+asyncpg" in url

    def test_get_db_url_encodes_special_chars(self):
        """密码含特殊字符时正确 URL-encode"""
        with (
            patch.object(
                cfg_mod.ConfigHandler,
                "get_typed",
                side_effect=lambda key, typ, default: {
                    "db_host": "localhost",
                    "db_port": 5432,
                    "db_user": "postgres",
                    "db_name": "astock",
                }.get(key, default),
            ),
            patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="p@ss:word/123"),
        ):
            url = cfg_mod.ConfigHandler.get_db_url()
            # quote_plus encodes @ : /
            assert url is not None
            assert "p%40ss%3Aword%2F123" in url
            assert "@localhost" in url  # @ before host, not in password

    def test_get_db_url_falls_back_to_config_when_no_host(self):
        """当 db_host 未配置时（pre-onboarding），回退到 config.DB_URL"""
        with (
            patch.object(
                cfg_mod.ConfigHandler,
                "get_typed",
                side_effect=lambda key, typ, default: {
                    "db_host": "",
                    "db_port": 5432,
                    "db_user": "postgres",
                    "db_name": "astock",
                }.get(key, default),
            ),
            patch.object(cfg_mod.config, "DB_URL", "postgresql+asyncpg://env:pass@envhost/db"),
        ):
            url = cfg_mod.ConfigHandler.get_db_url()
            assert url == "postgresql+asyncpg://env:pass@envhost/db"


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

    def test_security_error_returns_false(self):
        """SecurityManager.encrypt_data 抛 SecurityError → 返回 False"""
        from utils.security_utils import SecurityError

        with (
            patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable")),
            patch.object(cfg_mod.keyring, "delete_password", MagicMock()),
            patch.object(cfg_mod.SecurityManager, "encrypt_data", side_effect=SecurityError("security error")),
            patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True),
        ):
            result = cfg_mod.ConfigHandler.save_db_password("my_password")
            assert result is False

    def test_generic_exception_returns_false(self):
        """SecurityManager.encrypt_data 抛普通 Exception → 返回 False"""
        with (
            patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable")),
            patch.object(cfg_mod.keyring, "delete_password", MagicMock()),
            patch.object(cfg_mod.SecurityManager, "encrypt_data", side_effect=RuntimeError("unexpected")),
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

    @patch.dict(cfg_mod.os.environ, {"DB_PASSWORD": "env_pw"}, clear=False)
    @patch.object(cfg_mod.keyring, "get_password", return_value="keyring_pw")
    def test_env_variable_highest_priority(self, mock_kr):
        """DB_PASSWORD 环境变量优先级最高，覆盖 keyring"""
        result = cfg_mod.ConfigHandler.get_db_password()
        assert result == "env_pw"
        mock_kr.assert_not_called()

    @patch.dict(cfg_mod.os.environ, {"DB_PASSWORD": ""}, clear=False)
    @patch.object(cfg_mod.keyring, "get_password", return_value="keyring_pw")
    def test_empty_env_variable_skipped(self, mock_kr):
        """空字符串的环境变量不视为有效密码，回退到 keyring"""
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
            "llm_model": "deepseek-v4-flash",
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
            "llm_model": "gpt-5.4",
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
            model="deepseek-v4-flash",
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
    @patch.object(cfg_mod.ConfigHandler, "_save_json_atomically", return_value=True)
    def test_merges_and_saves(self, mock_save):
        with patch.object(cfg_mod.ConfigHandler, "_config_cache", {}):
            cfg_mod.ConfigHandler.ensure_defaults()
            mock_save.assert_called_once()

    @patch.object(cfg_mod.ConfigHandler, "_save_json_atomically", return_value=True)
    def test_no_save_when_already_complete(self, mock_save):
        with patch.object(cfg_mod.ConfigHandler, "_config_cache", cfg_mod.ConfigHandler.DEFAULT_CONFIG.copy()):
            cfg_mod.ConfigHandler.ensure_defaults()
            mock_save.assert_not_called()


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


class TestConfigHandlerNoLockReentry:
    """C-P1-7: Verify save_config does not call load_config inside write lock,
    which would cause deadlock with RWLockFair (non-reentrant)."""

    def test_save_config_does_not_call_load_config(self):
        with patch.object(cfg_mod.ConfigHandler, "load_config") as mock_load:
            mock_load.return_value = {"test_key": "test_val"}
            with patch.object(cfg_mod.ConfigHandler, "_save_json_atomically", return_value=True):
                cfg_mod.ConfigHandler._config_cache = {"test_key": "old_val"}
                cfg_mod.ConfigHandler.save_config({"test_key": "new_val"})
                mock_load.assert_not_called()

    def test_save_config_no_deadlock_on_reentry(self):
        original_cache = cfg_mod.ConfigHandler._config_cache
        try:
            cfg_mod.ConfigHandler._config_cache = {"key": "val"}
            with patch.object(cfg_mod.ConfigHandler, "_save_json_atomically", return_value=True):
                result = cfg_mod.ConfigHandler.save_config({"key": "updated"})
                assert result is True
        finally:
            cfg_mod.ConfigHandler._config_cache = original_cache


class TestGetDbConfigUsesDefaultConfig:
    """Q-P2-8: get_db_config() should use DEFAULT_CONFIG as default value source,
    not hardcoded literals, so defaults stay in sync."""

    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_host_defaults_from_default_config(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["host"] == cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_host"]

    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_port_defaults_from_default_config(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["port"] == cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_port"]

    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_user_defaults_from_default_config(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["user"] == cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_user"]

    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_database_defaults_from_default_config(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["database"] == cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_name"]

    @patch.object(cfg_mod.ConfigHandler, "get_db_password", return_value="pw")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"db_host": "10.0.0.1"})
    def test_custom_host_overrides_default(self, mock_load, mock_pw):
        result = cfg_mod.ConfigHandler.get_db_config()
        assert result["host"] == "10.0.0.1"

    def test_default_config_host_is_127_0_0_1(self):
        assert cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_host"] == "127.0.0.1"


class TestSaveTokenKeyringDeleteLogsDebug:
    """Q-P1-3: save_token with empty token should log debug when keyring
    deletion fails, not silently swallow the exception."""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "delete_password", side_effect=RuntimeError("keyring unavailable"))
    def test_empty_token_logs_debug_on_keyring_failure(self, mock_del, mock_save):
        with patch.object(cfg_mod, "logger") as mock_logger:
            cfg_mod.ConfigHandler.save_token("")
            mock_logger.debug.assert_called_once()
            assert "ts_token" in mock_logger.debug.call_args[0][0]


class TestSaveDbPasswordKeyringDeleteLogsDebug:
    """Q-P1-3: save_db_password fallback path should log debug when keyring
    deletion fails, not silently swallow the exception."""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.SecurityManager, "encrypt_data", return_value="encrypted")
    @patch.object(cfg_mod.keyring, "delete_password", side_effect=RuntimeError("keyring unavailable"))
    @patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable"))
    def test_fallback_logs_debug_on_keyring_delete_failure(self, mock_set, mock_del, mock_enc, mock_save):
        with patch.object(cfg_mod, "logger") as mock_logger:
            cfg_mod.ConfigHandler.save_db_password("my_password")
            debug_calls = [c for c in mock_logger.debug.call_args_list if "db_password" in c[0][0]]
            assert len(debug_calls) >= 1


class TestSaveLlmConfigKeyringDeleteLogsDebug:
    """Q-P1-3: save_llm_config with empty api_key should log debug when keyring
    deletion fails, not silently swallow the exception."""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "delete_password", side_effect=RuntimeError("keyring unavailable"))
    def test_empty_key_logs_debug_on_keyring_delete_failure(self, mock_del, mock_save):
        with patch.object(cfg_mod, "logger") as mock_logger:
            cfg_mod.ConfigHandler.save_llm_config(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key="",
            )
            debug_calls = [c for c in mock_logger.debug.call_args_list if "ai_api_key" in c[0][0]]
            assert len(debug_calls) >= 1


class TestGetLlmConfigCustomModelsIsolation:
    """R3-1: get_llm_config() must return a deep copy of custom_models,
    not a reference to DEFAULT_CONFIG, to prevent mutation of defaults."""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    def test_custom_models_is_deepcopy_not_reference(self, mock_kr, mock_load):
        result = cfg_mod.ConfigHandler.get_llm_config()
        cm = result["custom_models"]
        assert cm == cfg_mod.ConfigHandler.DEFAULT_CONFIG["llm_custom_models"]
        assert cm is not cfg_mod.ConfigHandler.DEFAULT_CONFIG["llm_custom_models"]

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    def test_mutating_custom_models_does_not_affect_default(self, mock_kr, mock_load):
        result = cfg_mod.ConfigHandler.get_llm_config()
        result["custom_models"]["test_provider"] = ["model_x"]
        assert "test_provider" not in cfg_mod.ConfigHandler.DEFAULT_CONFIG["llm_custom_models"]


class TestGetNoProxyDomainsIsolation:
    """R3-1b: get_no_proxy_domains() must return a copy of the list,
    not a reference to DEFAULT_CONFIG, to prevent mutation of defaults."""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_returns_copy_not_reference(self, mock_load):
        result = cfg_mod.ConfigHandler.get_no_proxy_domains()
        assert result == cfg_mod.ConfigHandler.DEFAULT_CONFIG["no_proxy_domains"]
        assert result is not cfg_mod.ConfigHandler.DEFAULT_CONFIG["no_proxy_domains"]

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_mutating_result_does_not_affect_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_no_proxy_domains()
        result.append("evil.example.com")
        assert "evil.example.com" not in cfg_mod.ConfigHandler.DEFAULT_CONFIG["no_proxy_domains"]


class TestGetSyncIntegrityConfigUsesDefaultConfig:
    """R3-2: get_sync_integrity_config() should use DEFAULT_CONFIG as
    default value source for nested keys, not hardcoded literals."""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_quotes_tolerance_from_default_config(self, mock_load):
        result = cfg_mod.ConfigHandler.get_sync_integrity_config()
        assert (
            result["quotes_tolerance_ratio"]
            == (cfg_mod.ConfigHandler.DEFAULT_CONFIG["sync_integrity"]["quotes_tolerance_ratio"])
        )

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_quality_weights_from_default_config(self, mock_load):
        result = cfg_mod.ConfigHandler.get_sync_integrity_config()
        assert result["quality_weights"] == cfg_mod.ConfigHandler.DEFAULT_CONFIG["sync_integrity"]["quality_weights"]

    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"sync_integrity": {"quotes_tolerance_ratio": 0.5}},
    )
    def test_partial_config_overrides_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_sync_integrity_config()
        assert result["quotes_tolerance_ratio"] == 0.5
        assert (
            result["indicators_tolerance_ratio"]
            == (cfg_mod.ConfigHandler.DEFAULT_CONFIG["sync_integrity"]["indicators_tolerance_ratio"])
        )


class TestGetMaxIoWorkersUsesDefaultConfig:
    """R3-5: get_max_io_workers() should use DEFAULT_CONFIG as default value
    source for max_io_workers. Note: result is capped by db_capacity."""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_max_io_workers_capped_by_db_capacity(self, mock_load):
        result = cfg_mod.ConfigHandler.get_max_io_workers()
        db_capacity = (
            cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_connection_pool_size"]
            + cfg_mod.ConfigHandler.DEFAULT_CONFIG["db_max_overflow"]
        )
        assert result == min(cfg_mod.ConfigHandler.DEFAULT_CONFIG["max_io_workers"], db_capacity)

    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"max_io_workers": 5, "db_connection_pool_size": 20, "db_max_overflow": 10},
    )
    def test_custom_io_workers_within_db_capacity(self, mock_load):
        result = cfg_mod.ConfigHandler.get_max_io_workers()
        assert result == 5


class TestMultiProviderCredentials:
    """P0-8: 多供应商凭证存储与读取测试"""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password")
    def test_save_provider_credential(self, mock_set, mock_save):
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="qwen_key_123",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            models=["qwen-plus", "qwen-turbo"],
        )
        assert result is True
        mock_set.assert_called_once_with(cfg_mod.KEYRING_SERVICE_NAME, "ai_api_key_qwen", "qwen_key_123")

    @patch.object(cfg_mod.keyring, "get_password", return_value="qwen_key_123")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={
            "llm_provider_credentials": {
                "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-plus"]}
            }
        },
    )
    def test_get_provider_credential(self, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_provider_credential("qwen")
        assert result["api_key"] == "qwen_key_123"
        assert result["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert "qwen-plus" in result["models"]

    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(cfg_mod.ConfigHandler, "_try_decrypt", return_value="")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_fallback_to_global_api_key(self, mock_load, mock_decrypt, mock_kr):
        with patch.object(
            cfg_mod.keyring, "get_password", side_effect=lambda svc, key: "global_key" if key == "ai_api_key" else None
        ):
            result = cfg_mod.ConfigHandler.get_provider_credential("qwen")
            assert result["api_key"] == "global_key"

    @patch.object(cfg_mod.keyring, "get_password", return_value="qwen_key")
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"llm_provider_credentials": {"qwen": {"models": ["qwen-plus"]}}},
    )
    def test_get_llm_config_for_provider(self, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_llm_config_for_provider("qwen")
        assert result["provider"] == "qwen"
        assert result["model"] == "qwen-plus"
        assert result["api_key"] == "qwen_key"

    @patch.object(
        cfg_mod.ConfigHandler, "load_config", return_value={"llm_custom_models": {}, "llm_provider_credentials": {}}
    )
    @patch.object(cfg_mod.ConfigHandler, "save_config")
    @patch.object(cfg_mod.keyring, "set_password")
    def test_save_provider_credential_without_base_url_registers_provider(self, mock_set, mock_save, mock_load):
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="qwen_key",
            base_url="",
            models=["qwen-plus"],
        )
        assert result is True
        saved_config = mock_save.call_args[0][0]
        assert "qwen" in saved_config["llm_provider_credentials"]
        assert saved_config["llm_custom_models"]["qwen"] == ["qwen-plus"]

    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={
            "llm_custom_models": {"qwen": [f"model-{i}" for i in range(48)]},
            "llm_provider_credentials": {"qwen": {}},
        },
    )
    @patch.object(cfg_mod.ConfigHandler, "save_config")
    @patch.object(cfg_mod.keyring, "set_password")
    def test_save_provider_credential_models_limit_50(self, mock_set, mock_save, mock_load):
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="qwen_key",
            base_url="https://api.qwen.com/v1",
            models=["model-48", "model-49", "model-50"],
        )
        assert result is True
        saved_config = mock_save.call_args[0][0]
        models_list = saved_config["llm_custom_models"]["qwen"]
        assert len(models_list) == 50
        assert models_list[-3:] == ["model-48", "model-49", "model-50"]


class TestMigrateCustomModelsCredentials:
    """测试 _migrate_custom_models_credentials 迁移旧格式到新格式"""

    def test_no_custom_models_returns_false(self):
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials({})
        assert result is False

    def test_empty_custom_models_returns_false(self):
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials({"llm_custom_models": {}})
        assert result is False

    def test_already_new_format_list_returns_false(self):
        config = {"llm_custom_models": {"deepseek": ["deepseek-v3", "deepseek-v4-flash"]}}
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is False
        assert config["llm_custom_models"]["deepseek"] == ["deepseek-v3", "deepseek-v4-flash"]

    @patch.object(cfg_mod.keyring, "set_password")
    def test_migrate_old_dict_format_with_api_key(self, mock_keyring):
        config = {
            "llm_custom_models": {
                "qwen": {
                    "api_key": "qwen_secret_key",
                    "base_url": "https://dashscope.aliyuncs.com/v1",
                    "models": ["qwen-plus", "qwen-turbo"],
                }
            }
        }
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is True
        mock_keyring.assert_called_once_with(cfg_mod.KEYRING_SERVICE_NAME, "ai_api_key_qwen", "qwen_secret_key")
        assert config["llm_custom_models"]["qwen"] == ["qwen-plus", "qwen-turbo"]
        assert "qwen" in config["llm_provider_credentials"]
        assert config["llm_provider_credentials"]["qwen"]["base_url"] == "https://dashscope.aliyuncs.com/v1"

    @patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable"))
    @patch.object(cfg_mod.SecurityManager, "encrypt_data", return_value="ENCRYPTED_KEY")
    def test_migrate_old_dict_format_keyring_fallback(self, mock_encrypt, mock_keyring):
        config = {
            "llm_custom_models": {
                "qwen": {
                    "api_key": "qwen_secret_key",
                    "models": ["qwen-plus"],
                }
            }
        }
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is True
        mock_encrypt.assert_called_once_with("qwen_secret_key")
        assert config["llm_provider_credentials"]["qwen"]["api_key_encrypted"] == "ENCRYPTED_KEY"

    def test_migrate_old_dict_format_only_base_url(self):
        config = {
            "llm_custom_models": {
                "openai": {
                    "base_url": "https://api.openai.com/v1",
                    "models": ["gpt-4"],
                }
            }
        }
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is True
        assert config["llm_custom_models"]["openai"] == ["gpt-4"]
        assert config["llm_provider_credentials"]["openai"]["base_url"] == "https://api.openai.com/v1"

    def test_migrate_old_dict_format_no_models(self):
        config = {
            "llm_custom_models": {
                "deepseek": {
                    "api_key": "ds_key",
                }
            }
        }
        with patch.object(cfg_mod.keyring, "set_password"):
            result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is True
        assert "deepseek" not in config["llm_custom_models"]

    def test_migrate_models_from_credentials_to_custom_models(self):
        config = {
            "llm_custom_models": {"other": ["model1"]},
            "llm_provider_credentials": {"azure": {"models": ["gpt-4-azure"], "base_url": "https://azure.openai.com"}},
        }
        result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
        assert result is True
        assert config["llm_custom_models"]["azure"] == ["gpt-4-azure"]
        assert "models" not in config["llm_provider_credentials"]["azure"]

    def test_migrate_mixed_formats(self):
        config = {
            "llm_custom_models": {
                "deepseek": ["deepseek-v3"],
                "qwen": {
                    "api_key": "qwen_key",
                    "models": ["qwen-plus"],
                },
            }
        }
        with patch.object(cfg_mod.keyring, "set_password"):
            result = cfg_mod.ConfigHandler._migrate_custom_models_credentials(config)
            assert result is True
            assert config["llm_custom_models"]["deepseek"] == ["deepseek-v3"]
            assert config["llm_custom_models"]["qwen"] == ["qwen-plus"]


class TestValidateFailoverCredentials:
    """测试 validate_failover_credentials 校验 failover 配置完整性"""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": []})
    def test_empty_failover_list_returns_empty(self, mock_load):
        result = cfg_mod.ConfigHandler.validate_failover_credentials()
        assert result == []

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": ["deepseek/deepseek-v3"]})
    @patch.object(
        cfg_mod.ConfigHandler,
        "get_provider_credential",
        return_value={"api_key": "valid_key", "models": ["deepseek-v3"], "base_url": ""},
    )
    def test_valid_credentials_returns_empty(self, mock_cred, mock_load):
        result = cfg_mod.ConfigHandler.validate_failover_credentials()
        assert result == []

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": ["qwen/qwen-plus"]})
    @patch.object(
        cfg_mod.ConfigHandler, "get_provider_credential", return_value={"api_key": None, "models": [], "base_url": ""}
    )
    def test_missing_api_key(self, mock_cred, mock_load):
        result = cfg_mod.ConfigHandler.validate_failover_credentials()
        assert "qwen" in result

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": ["qwen/qwen-plus"]})
    @patch.object(
        cfg_mod.ConfigHandler,
        "get_provider_credential",
        return_value={"api_key": "valid_key", "models": ["qwen-turbo"], "base_url": ""},
    )
    def test_model_not_in_list(self, mock_cred, mock_load):
        result = cfg_mod.ConfigHandler.validate_failover_credentials()
        assert "qwen" in result

    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"llm_failover_models": ["deepseek/v3", "qwen/qwen-plus", "openai/gpt-4"]},
    )
    def test_multiple_providers_dedup(self, mock_load):
        cred_calls = {
            "deepseek": {"api_key": "ds_key", "models": ["v3"], "base_url": ""},
            "qwen": {"api_key": None, "models": [], "base_url": ""},
            "openai": {"api_key": None, "models": [], "base_url": ""},
        }
        with patch.object(
            cfg_mod.ConfigHandler,
            "get_provider_credential",
            side_effect=lambda p: cred_calls[p],
        ):
            result = cfg_mod.ConfigHandler.validate_failover_credentials()
            assert "deepseek" not in result
            assert "qwen" in result
            assert "openai" in result


class TestLoadConfigWithValidation:
    """测试 load_config_with_validation 返回验证详情"""

    @patch.object(cfg_mod, "CONFIG_FILE", "/nonexistent/user_settings.json")
    def test_file_not_exist_returns_defaults(self):
        with patch.object(cfg_mod.os.path, "exists", return_value=False):
            result = cfg_mod.ConfigHandler.load_config_with_validation()
            assert result.is_valid is True
            assert result.used_defaults is True
            assert result.errors == []

    @patch.object(cfg_mod, "CONFIG_FILE", "/exists/user_settings.json")
    @patch.object(cfg_mod.os.path, "exists", return_value=True)
    @patch.object(cfg_mod, "open", create=True)
    def test_valid_config(self, mock_open, mock_exists):
        mock_file = MagicMock()
        mock_file.read.return_value = '{"llm_provider": "deepseek", "llm_model": "deepseek-v3"}'
        mock_open.return_value.__enter__.return_value = mock_file
        with patch.object(cfg_mod.json, "load", return_value={"llm_provider": "deepseek", "llm_model": "deepseek-v3"}):
            result = cfg_mod.ConfigHandler.load_config_with_validation()
            assert result.is_valid is True
            assert result.used_defaults is False

    @patch.object(cfg_mod.os.path, "exists", return_value=True)
    def test_validation_error_sanitizes_sensitive_keys(self, mock_exists):
        from pydantic import ValidationError

        config_data = {"ts_token": "secret_token_value", "invalid_key": "value"}
        with patch.object(cfg_mod, "open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = "{}"
            with patch.object(cfg_mod.json, "load", return_value=config_data):
                with patch.object(
                    cfg_mod.AppConfig,
                    "model_validate",
                    side_effect=ValidationError.from_exception_data(
                        "test", [{"type": "extra_forbidden", "loc": ("ts_token",), "input": "secret_token_value"}]
                    ),
                ):
                    result = cfg_mod.ConfigHandler.load_config_with_validation()
                    assert result.is_valid is False
                    assert result.used_defaults is True

    @patch.object(cfg_mod, "CONFIG_FILE", "/exists/user_settings.json")
    @patch.object(cfg_mod.os.path, "exists", return_value=True)
    def test_general_exception(self, mock_exists):
        with patch.object(cfg_mod.json, "load", side_effect=RuntimeError("read error")):
            result = cfg_mod.ConfigHandler.load_config_with_validation()
            assert result.is_valid is False
            assert result.config == {}


class TestAiPrompts:
    """测试 AI prompt 相关方法"""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"ai_system_prompt": "custom prompt"})
    def test_get_ai_system_prompt_custom(self, mock_load):
        result = cfg_mod.ConfigHandler.get_ai_system_prompt()
        assert result == "custom prompt"

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_get_ai_system_prompt_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_ai_system_prompt()
        assert result == cfg_mod.DEFAULT_AI_PROMPT

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_ai_system_prompt_valid(self, mock_save):
        import utils.prompt_guard as pg

        with patch.object(pg, "validate_prompt", return_value=(True, None)):
            with patch.object(pg, "sanitize_prompt", return_value="sanitized"):
                result = cfg_mod.ConfigHandler.save_ai_system_prompt("test prompt")
                assert result is True
                mock_save.assert_called_once_with({"ai_system_prompt": "sanitized"})

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_ai_system_prompt_invalid(self, mock_save):
        import utils.prompt_guard as pg

        with patch.object(pg, "validate_prompt", return_value=(False, "invalid")):
            result = cfg_mod.ConfigHandler.save_ai_system_prompt("bad prompt")
            assert result is False
            mock_save.assert_not_called()

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_ai_system_prompt_empty(self, mock_save):
        result = cfg_mod.ConfigHandler.save_ai_system_prompt("")
        assert result is True
        mock_save.assert_called_once_with({"ai_system_prompt": ""})

    @patch.object(
        cfg_mod.ConfigHandler, "load_config", return_value={"ai_strategy_prompt_oversold": "custom strategy prompt"}
    )
    def test_get_strategy_prompt(self, mock_load):
        result = cfg_mod.ConfigHandler.get_strategy_prompt("oversold")
        assert result == "custom strategy prompt"

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_get_strategy_prompt_none(self, mock_load):
        result = cfg_mod.ConfigHandler.get_strategy_prompt("unknown")
        assert result is None

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_strategy_prompt_valid(self, mock_save):
        import utils.prompt_guard as pg

        with patch.object(pg, "validate_prompt", return_value=(True, None)):
            with patch.object(pg, "sanitize_prompt", return_value="sanitized"):
                result = cfg_mod.ConfigHandler.set_strategy_prompt("oversold", "test prompt")
                assert result is True
                mock_save.assert_called_once_with({"ai_strategy_prompt_oversold": "sanitized"})

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_strategy_prompt_invalid(self, mock_save):
        import utils.prompt_guard as pg

        with patch.object(pg, "validate_prompt", return_value=(False, "invalid")):
            result = cfg_mod.ConfigHandler.set_strategy_prompt("oversold", "bad prompt")
            assert result is False

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"ai_news_prompt": "custom news prompt"})
    def test_get_ai_news_prompt_custom(self, mock_load):
        result = cfg_mod.ConfigHandler.get_ai_news_prompt()
        assert result == "custom news prompt"

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_get_ai_news_prompt_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_ai_news_prompt()
        assert result == cfg_mod.DEFAULT_NEWS_PROMPT

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_ai_news_prompt(self, mock_save):
        result = cfg_mod.ConfigHandler.set_ai_news_prompt("new prompt")
        assert result is True


class TestSaveProviderCredentialEncrypt:
    """测试 save_provider_credential 加密 fallback 路径"""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable"))
    @patch.object(cfg_mod.SecurityManager, "encrypt_data", return_value="ENCRYPTED_KEY")
    def test_keyring_fallback_to_encrypt(self, mock_encrypt, mock_keyring, mock_save):
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="qwen_secret_key",
        )
        assert result is True
        mock_encrypt.assert_called_once_with("qwen_secret_key")

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password", side_effect=RuntimeError("keyring unavailable"))
    @patch.object(cfg_mod.SecurityManager, "encrypt_data", side_effect=RuntimeError("encrypt failed"))
    def test_encrypt_failure_returns_false(self, mock_encrypt, mock_keyring, mock_save):
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="qwen_secret_key",
        )
        assert result is False

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password")
    def test_with_models_limits_to_50(self, mock_keyring, mock_save):
        models = [f"model_{i}" for i in range(60)]
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            models=models,
        )
        assert result is True
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config["llm_custom_models"]["qwen"]) == 50


class TestGetProviderCredentialFallback:
    """测试 get_provider_credential keyring fallback 路径"""

    @patch.object(
        cfg_mod.keyring, "get_password", side_effect=lambda s, k: "provider_key" if k == "ai_api_key_qwen" else None
    )
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_from_provider_keyring(self, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_provider_credential("qwen")
        assert result["api_key"] == "provider_key"

    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"llm_provider_credentials": {"qwen": {"api_key_encrypted": "ENC_KEY"}}},
    )
    @patch.object(cfg_mod.SecurityManager, "decrypt_data", return_value="decrypted_key")
    def test_from_encrypted_config(self, mock_decrypt, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_provider_credential("qwen")
        assert result["api_key"] == "decrypted_key"

    @patch.object(cfg_mod.keyring, "get_password", side_effect=lambda s, k: "global_key" if k == "ai_api_key" else None)
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_fallback_to_global_keyring(self, mock_load, mock_kr):
        result = cfg_mod.ConfigHandler.get_provider_credential("unknown_provider")
        assert result["api_key"] == "global_key"

    @patch.object(cfg_mod.keyring, "get_password", return_value=None)
    @patch.object(cfg_mod.SecurityManager, "decrypt_data", return_value="decrypted_from_config")
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"ai_api_key": "ENCRYPTED_GLOBAL"})
    def test_fallback_to_global_encrypted(self, mock_load, mock_decrypt, mock_kr):
        result = cfg_mod.ConfigHandler.get_provider_credential("unknown_provider")
        assert result["api_key"] == "decrypted_from_config"


class TestSimpleGetterSetters:
    """测试简单 getter/setter 方法"""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"log_level": "DEBUG"})
    def test_get_log_level(self, mock_load):
        assert cfg_mod.ConfigHandler.get_log_level() == "DEBUG"

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_log_level(self, mock_set):
        cfg_mod.ConfigHandler.set_log_level("info")
        mock_set.assert_called_once_with("log_level", "INFO")

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"log_format": "json"})
    def test_get_log_format(self, mock_load):
        assert cfg_mod.ConfigHandler.get_log_format() == "json"

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_log_format(self, mock_set):
        cfg_mod.ConfigHandler.set_log_format("TEXT")
        mock_set.assert_called_once_with("log_format", "text")

    @patch.object(cfg_mod.ConfigHandler, "get_typed", return_value=5)
    def test_get_init_history_years(self, mock_get):
        assert cfg_mod.ConfigHandler.get_init_history_years() == 5

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_init_history_years_clamped(self, mock_set):
        cfg_mod.ConfigHandler.set_init_history_years(10)
        mock_set.assert_called_once_with("init_history_years", 5)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_doubao_schedule_enabled(self, mock_set):
        cfg_mod.ConfigHandler.set_doubao_schedule_enabled(True)
        mock_set.assert_called_once_with("doubao_schedule_enabled", True)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_doubao_schedule_time(self, mock_set):
        cfg_mod.ConfigHandler.set_doubao_schedule_time("12:00")
        mock_set.assert_called_once_with("doubao_schedule_time", "12:00")

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_db_connection_pool_size(self, mock_set):
        cfg_mod.ConfigHandler.set_db_connection_pool_size(20)
        mock_set.assert_called_once_with("db_connection_pool_size", 20)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_db_max_overflow(self, mock_set):
        cfg_mod.ConfigHandler.set_db_max_overflow(10)
        mock_set.assert_called_once_with("db_max_overflow", 10)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_db_pool_timeout(self, mock_set):
        cfg_mod.ConfigHandler.set_db_pool_timeout(60)
        mock_set.assert_called_once_with("db_pool_timeout", 60)

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_set_sync_concurrency(self, mock_save):
        cfg_mod.ConfigHandler.set_sync_concurrency(5)
        mock_save.assert_called_once_with({"sync_max_concurrent_heavy": 5})

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_max_batch_rows(self, mock_set):
        cfg_mod.ConfigHandler.set_max_batch_rows(50000)
        mock_set.assert_called_once_with("max_batch_rows", 50000)

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"locale": "en"})
    def test_get_locale(self, mock_load):
        assert cfg_mod.ConfigHandler.get_locale() == "en"

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_locale(self, mock_set):
        cfg_mod.ConfigHandler.set_locale("zh")
        mock_set.assert_called_once_with("locale", "zh")

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"theme_name": "light"})
    def test_get_theme_name(self, mock_load):
        assert cfg_mod.ConfigHandler.get_theme_name() == "light"

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_theme_name(self, mock_set):
        cfg_mod.ConfigHandler.set_theme_name("dark")
        mock_set.assert_called_once_with("theme_name", "dark")

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_max_io_workers(self, mock_set):
        cfg_mod.ConfigHandler.set_max_io_workers(8)
        mock_set.assert_called_once_with("max_io_workers", 8)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_max_cpu_workers(self, mock_set):
        cfg_mod.ConfigHandler.set_max_cpu_workers(4)
        mock_set.assert_called_once_with("max_cpu_workers", 4)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_max_concurrent_tasks(self, mock_set):
        cfg_mod.ConfigHandler.set_max_concurrent_tasks(10)
        mock_set.assert_called_once_with("max_concurrent_tasks", 10)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_sync_request_delay_heavy(self, mock_set):
        cfg_mod.ConfigHandler.set_sync_request_delay(0.5, is_heavy=True)
        mock_set.assert_called_once_with("sync_request_delay_heavy", 0.5)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_sync_request_delay_light(self, mock_set):
        cfg_mod.ConfigHandler.set_sync_request_delay(0.1, is_heavy=False)
        mock_set.assert_called_once_with("sync_request_delay_light", 0.1)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_news_poll_interval(self, mock_set):
        cfg_mod.ConfigHandler.set_news_poll_interval(30)
        mock_set.assert_called_once_with("news_poll_interval", 30)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_market_data_poll_interval(self, mock_set):
        cfg_mod.ConfigHandler.set_market_data_poll_interval(15)
        mock_set.assert_called_once_with("market_data_poll_interval", 15)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_ai_max_candidates(self, mock_set):
        cfg_mod.ConfigHandler.set_ai_max_candidates(50)
        mock_set.assert_called_once_with("ai_max_candidates", 50)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_ai_max_concurrent_analysis(self, mock_set):
        cfg_mod.ConfigHandler.set_ai_max_concurrent_analysis(10)
        mock_set.assert_called_once_with("ai_max_concurrent_analysis", 10)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_strategy_min_turnover(self, mock_set):
        cfg_mod.ConfigHandler.set_strategy_min_turnover(5.0)
        mock_set.assert_called_once_with("strategy_min_turnover", 5.0)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_tushare_timeout(self, mock_set):
        cfg_mod.ConfigHandler.set_tushare_timeout(60)
        mock_set.assert_called_once_with("tushare_timeout", 60)

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_tushare_api_limit(self, mock_set):
        cfg_mod.ConfigHandler.set_tushare_api_limit(500)
        mock_set.assert_called_once_with("tushare_api_rate_limit", 500)


class TestGetFailoverConfig:
    """测试 get_failover_config 获取 failover 配置"""

    @patch.object(
        cfg_mod.ConfigHandler,
        "get_llm_config",
        return_value={
            "provider": "deepseek",
            "model": "deepseek-v3",
            "api_key": "key",
            "base_url": "",
            "api_version": "",
            "azure_resource_name": "",
            "azure_deployment_name": "",
            "custom_models": {},
        },
    )
    @patch.object(
        cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": ["qwen/qwen-plus", "openai/gpt-4"]}
    )
    def test_with_fallbacks(self, mock_load, mock_llm):
        result = cfg_mod.ConfigHandler.get_failover_config()
        assert result["primary"] == "deepseek/deepseek-v3"
        assert result["fallbacks"] == ["qwen/qwen-plus", "openai/gpt-4"]

    @patch.object(
        cfg_mod.ConfigHandler,
        "get_llm_config",
        return_value={
            "provider": "deepseek",
            "model": "deepseek-v3",
            "api_key": "key",
            "base_url": "",
            "api_version": "",
            "azure_resource_name": "",
            "azure_deployment_name": "",
            "custom_models": {},
        },
    )
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_failover_models": "not_a_list"})
    def test_invalid_fallbacks_returns_empty(self, mock_load, mock_llm):
        result = cfg_mod.ConfigHandler.get_failover_config()
        assert result["fallbacks"] == []

    @patch.object(
        cfg_mod.ConfigHandler,
        "get_llm_config",
        return_value={
            "provider": "",
            "model": "",
            "api_key": "key",
            "base_url": "",
            "api_version": "",
            "azure_resource_name": "",
            "azure_deployment_name": "",
            "custom_models": {},
        },
    )
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_empty_provider(self, mock_load, mock_llm):
        result = cfg_mod.ConfigHandler.get_failover_config()
        assert result["primary"] == ""


class TestGetTypedExceptions:
    """测试 get_typed 异常路径"""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"test_bool": "true"})
    def test_bool_from_string_true(self, mock_load):
        result = cfg_mod.ConfigHandler.get_typed("test_bool", bool, False)
        assert result is True

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"test_bool": "FALSE"})
    def test_bool_from_string_false(self, mock_load):
        result = cfg_mod.ConfigHandler.get_typed("test_bool", bool, True)
        assert result is False

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"test_int": "not_a_number"})
    def test_int_invalid_returns_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_typed("test_int", int, 42)
        assert result == 42


class TestSetTypedValidation:
    """测试 set_typed 验证路径"""

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_with_validator_pass(self, mock_save):
        result = cfg_mod.ConfigHandler.set_typed("test_key", "valid_value", validator=lambda x: len(x) > 3)
        assert result is True

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_with_validator_fail(self, mock_save):
        result = cfg_mod.ConfigHandler.set_typed("test_key", "x", validator=lambda x: len(x) > 3)
        assert result is False
        mock_save.assert_not_called()

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_sensitive_key_sanitized_in_log(self, mock_save):
        with patch.object(cfg_mod, "logger"):
            result = cfg_mod.ConfigHandler.set_typed("ts_token", "short", validator=lambda x: len(x) > 10)
            assert result is False


class TestSaveDbConfig:
    """测试 save_db_config 保存数据库配置"""

    @patch.object(cfg_mod.ConfigHandler, "save_db_password", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_full_config(self, mock_save, mock_pw):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch.object(
            DatabaseConfigService, "build_url", return_value="postgresql+asyncpg://user:pass@host:5432/db"
        ):
            result = cfg_mod.ConfigHandler.save_db_config("localhost", 5432, "user", "password", "mydb")
        assert result is True

    @patch.object(cfg_mod.ConfigHandler, "save_db_password", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_save_without_password(self, mock_save, mock_pw):
        from data.persistence.db_config_service import DatabaseConfigService

        with patch.object(DatabaseConfigService, "build_url", return_value="postgresql://user:@host:5432/db"):
            result = cfg_mod.ConfigHandler.save_db_config("localhost", 5432, "user", "", "mydb")
        assert result is True
        mock_pw.assert_not_called()

    @patch.object(cfg_mod.ConfigHandler, "save_db_password", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_syncs_config_db_url(self, mock_save, mock_pw):
        """save_db_config 必须同步更新 config.DB_URL / DB_URL_SYNC"""
        from data.persistence.db_config_service import DatabaseConfigService

        built_url = "postgresql+asyncpg://admin:secret@dbhost:5433/testdb"
        with patch.object(DatabaseConfigService, "build_url", return_value=built_url):
            cfg_mod.ConfigHandler.save_db_config("dbhost", 5433, "admin", "secret", "testdb")
        # save_db_config() assigns config.DB_URL directly; verify it was set
        assert built_url == cfg_mod.config.DB_URL
        assert cfg_mod.config.DB_URL_SYNC == "postgresql://admin:secret@dbhost:5433/testdb"

    @patch.object(cfg_mod.ConfigHandler, "save_db_password", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_password_masked_in_saved_url(self, mock_save, mock_pw):
        """保存到 user_settings.json 的 db_url 中密码必须被掩码"""
        from data.persistence.db_config_service import DatabaseConfigService

        built_url = "postgresql+asyncpg://admin:secret@dbhost:5433/testdb"
        with patch.object(DatabaseConfigService, "build_url", return_value=built_url):
            cfg_mod.ConfigHandler.save_db_config("dbhost", 5433, "admin", "secret", "testdb")
        saved_data = mock_save.call_args[0][0]
        assert "secret" not in saved_data["db_url"]
        assert "****" in saved_data["db_url"]

    @patch.object(cfg_mod.ConfigHandler, "save_db_password", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    def test_password_with_at_sign_masked_correctly(self, mock_save, mock_pw):
        """密码含 @ 时掩码正则仍能正确匹配"""
        from data.persistence.db_config_service import DatabaseConfigService

        built_url = "postgresql+asyncpg://admin:p%40ssword@dbhost:5432/db"
        with patch.object(DatabaseConfigService, "build_url", return_value=built_url):
            cfg_mod.ConfigHandler.save_db_config("dbhost", 5432, "admin", "p@ssword", "db")
        saved_data = mock_save.call_args[0][0]
        assert "p%40ssword" not in saved_data["db_url"]
        assert "****" in saved_data["db_url"]


class TestGetLlmConfigForProviderWarning:
    """测试 get_llm_config_for_provider 无模型警告"""

    @patch.object(
        cfg_mod.ConfigHandler, "get_provider_credential", return_value={"api_key": "key", "base_url": "", "models": []}
    )
    def test_no_models_warning(self, mock_cred):
        with patch.object(cfg_mod, "logger") as mock_logger:
            result = cfg_mod.ConfigHandler.get_llm_config_for_provider("unknown")
            assert result["model"] == ""
            mock_logger.warning.assert_called_once()


class TestGetMaxConcurrentTasks:
    """测试 get_max_concurrent_tasks 默认值逻辑"""

    @patch.object(cfg_mod.ConfigHandler, "get_typed", return_value=0)
    @patch.object(cfg_mod.ConfigHandler, "get_max_cpu_workers", return_value=8)
    def test_fallback_to_cpu_workers(self, mock_cpu, mock_get):
        result = cfg_mod.ConfigHandler.get_max_concurrent_tasks()
        assert result == 8

    @patch.object(cfg_mod.ConfigHandler, "get_typed", return_value=0)
    @patch.object(cfg_mod.ConfigHandler, "get_max_cpu_workers", return_value=0)
    def test_fallback_to_default_5(self, mock_cpu, mock_get):
        result = cfg_mod.ConfigHandler.get_max_concurrent_tasks()
        assert result == 5

    @patch.object(cfg_mod.ConfigHandler, "get_typed", return_value=10)
    def test_configured_value(self, mock_get):
        result = cfg_mod.ConfigHandler.get_max_concurrent_tasks()
        assert result == 10


class TestTusharePointTier:
    """测试 get_tushare_point_tier / set_tushare_point_tier"""

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"tushare_point_tier": "pro"})
    def test_get_tushare_point_tier_custom(self, mock_load):
        result = cfg_mod.ConfigHandler.get_tushare_point_tier()
        assert result == "pro"

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_get_tushare_point_tier_default(self, mock_load):
        result = cfg_mod.ConfigHandler.get_tushare_point_tier()
        assert result == "custom"

    @patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True)
    def test_set_tushare_point_tier(self, mock_set):
        result = cfg_mod.ConfigHandler.set_tushare_point_tier("pro")
        assert result is True
        mock_set.assert_called_once_with("tushare_point_tier", "pro")

    def test_point_tier_roundtrip(self):
        with patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True):
            cfg_mod.ConfigHandler._config_cache = {"tushare_point_tier": "pro"}
            result = cfg_mod.ConfigHandler.get_tushare_point_tier()
            assert result == "pro"
            cfg_mod.ConfigHandler._config_cache = None

    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={})
    def test_point_tier_default_when_unset(self, mock_load):
        result = cfg_mod.ConfigHandler.get_tushare_point_tier()
        assert result == "custom"

    def test_set_tushare_point_tier_rejects_invalid(self):
        with patch.object(cfg_mod.ConfigHandler, "set_typed", return_value=True) as mock_set:
            result = cfg_mod.ConfigHandler.set_tushare_point_tier("platinum")
            assert result is False
            mock_set.assert_not_called()


class TestSaveProviderCredentialClearSemantics:
    """测试 save_provider_credential 的清空语义 (Fix 7)

    api_key/base_url 参数语义：
    - None 表示不修改该字段
    - "" 表示清除该字段
    - 其他值表示更新该字段
    """

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "delete_password")
    def test_empty_api_key_deletes_keyring(self, mock_del, mock_save):
        """空字符串 api_key 应删除 keyring 条目"""
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert result is True
        mock_del.assert_called_once_with(cfg_mod.KEYRING_SERVICE_NAME, "ai_api_key_qwen")

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.ConfigHandler, "load_config", return_value={"llm_provider_credentials": {"qwen": {}}})
    def test_empty_base_url_clears_config(self, mock_load, mock_save):
        """空字符串 base_url 应清除配置中的 base_url 字段"""
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="test_key",
            base_url="",
        )
        assert result is True
        saved_config = mock_save.call_args[0][0]
        qwen_cred = saved_config["llm_provider_credentials"]["qwen"]
        assert "base_url" not in qwen_cred

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(cfg_mod.keyring, "set_password")
    def test_none_api_key_does_not_modify_keyring(self, mock_set, mock_save):
        """None api_key 不应修改 keyring"""
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key=None,
            base_url="https://api.qwen.com/v1",
        )
        assert result is True
        mock_set.assert_not_called()

    @patch.object(cfg_mod.ConfigHandler, "save_config", return_value=True)
    @patch.object(
        cfg_mod.ConfigHandler,
        "load_config",
        return_value={"llm_provider_credentials": {"qwen": {"base_url": "https://old.url"}}},
    )
    def test_none_base_url_preserves_existing(self, mock_load, mock_save):
        """None base_url 应保留配置中的已有值"""
        result = cfg_mod.ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="test_key",
            base_url=None,
        )
        assert result is True
        saved_config = mock_save.call_args[0][0]
        qwen_cred = saved_config["llm_provider_credentials"]["qwen"]
        assert qwen_cred.get("base_url") == "https://old.url"
