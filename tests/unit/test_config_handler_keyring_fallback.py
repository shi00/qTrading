from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.unit
class TestConfigHandlerKeyringFallback:
    def test_save_db_password_falls_back_to_encrypt_when_keyring_fails(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod

        encrypted_value = "ENCRYPTED_SECRET_123"

        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            MagicMock(side_effect=RuntimeError("keyring unavailable")),
        )
        mock_encrypt = MagicMock(return_value=encrypted_value)
        monkeypatch.setattr(cfg_mod.SecurityManager, "encrypt_data", mock_encrypt)
        saved_configs = []
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "save_config",
            lambda payload: saved_configs.append(payload) or True,
        )

        result = cfg_mod.ConfigHandler.save_db_password("my_secret_password")

        assert result is True
        mock_encrypt.assert_called_once_with("my_secret_password")
        assert len(saved_configs) == 1
        assert saved_configs[0] == {"db_password_encrypted": encrypted_value}

    def test_save_db_password_returns_false_when_both_fail(self, monkeypatch):
        import utils.config_handler as cfg_mod

        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            MagicMock(side_effect=RuntimeError("keyring unavailable")),
        )
        monkeypatch.setattr(
            cfg_mod.SecurityManager,
            "encrypt_data",
            MagicMock(side_effect=RuntimeError("encryption failed")),
        )

        result = cfg_mod.ConfigHandler.save_db_password("my_secret_password")

        assert result is False

    def test_save_db_password_prefers_keyring(self, monkeypatch):
        import utils.config_handler as cfg_mod

        keyring_called = []
        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            lambda service, key, pw: keyring_called.append((service, key, pw)),
        )
        saved_configs = []
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "save_config",
            lambda payload: saved_configs.append(payload) or True,
        )

        result = cfg_mod.ConfigHandler.save_db_password("my_secret_password")

        assert result is True
        assert len(keyring_called) == 1
        assert keyring_called[0] == (
            cfg_mod.KEYRING_SERVICE_NAME,
            "db_password",
            "my_secret_password",
        )
        assert saved_configs == [{"db_password_encrypted": ""}]

    def test_save_db_password_returns_false_for_empty(self, monkeypatch):
        import utils.config_handler as cfg_mod

        result = cfg_mod.ConfigHandler.save_db_password("")
        assert result is False

    def test_get_db_password_reads_encrypted_fallback(self, monkeypatch):
        import utils.config_handler as cfg_mod

        monkeypatch.setattr(
            cfg_mod.keyring,
            "get_password",
            MagicMock(side_effect=RuntimeError("keyring unavailable")),
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {"db_password_encrypted": "ENCRYPTED_VALUE"},
        )
        mock_decrypt = MagicMock(return_value="decrypted_password")
        monkeypatch.setattr(cfg_mod.SecurityManager, "decrypt_data", mock_decrypt)

        result = cfg_mod.ConfigHandler.get_db_password()

        assert result == "decrypted_password"
        mock_decrypt.assert_called_once_with("ENCRYPTED_VALUE")


@pytest.mark.unit
class TestKeyringFallbackMigratesLegacy:
    """H-3: get_token / get_ai_api_key must migrate legacy config values to keyring."""

    def test_get_token_migrates_legacy_to_keyring(self, monkeypatch):
        import utils.config_handler as cfg_mod

        keyring_reads = []
        keyring_writes = []
        config_saves = []

        def mock_get_pw(service, key):
            keyring_reads.append(key)
            if key == "ts_token":
                return None
            return None

        monkeypatch.setattr(cfg_mod.keyring, "get_password", mock_get_pw)
        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            lambda s, k, v: keyring_writes.append((k, v)),
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {"ts_token": "legacy_encrypted_token"},
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "_try_decrypt",
            lambda v: "decrypted_token" if v else "",
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "save_config",
            lambda payload: config_saves.append(payload) or True,
        )

        result = cfg_mod.ConfigHandler.get_token()

        assert result == "decrypted_token"
        assert ("ts_token", "decrypted_token") in keyring_writes
        assert {"ts_token": ""} in config_saves

    def test_get_token_skips_migration_when_keyring_has_value(self, monkeypatch):
        import utils.config_handler as cfg_mod

        keyring_writes = []

        monkeypatch.setattr(
            cfg_mod.keyring,
            "get_password",
            lambda s, k: "existing_keyring_token" if k == "ts_token" else None,
        )
        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            lambda s, k, v: keyring_writes.append((k, v)),
        )

        result = cfg_mod.ConfigHandler.get_token()

        assert result == "existing_keyring_token"
        assert len(keyring_writes) == 0

    def test_get_ai_config_migrates_legacy_api_key(self, monkeypatch):
        import utils.config_handler as cfg_mod

        keyring_writes = []
        config_saves = []

        monkeypatch.setattr(cfg_mod.keyring, "get_password", lambda s, k: None)
        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            lambda s, k, v: keyring_writes.append((k, v)),
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {
                "ai_api_key": "legacy_encrypted_key",
                "llm_provider": "deepseek",
                "llm_model": "deepseek-v4-flash",
            },
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "_try_decrypt",
            lambda v: "decrypted_api_key" if v else "",
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "save_config",
            lambda payload: config_saves.append(payload) or True,
        )

        result = cfg_mod.ConfigHandler.get_llm_config()

        assert result["api_key"] == "decrypted_api_key"
        assert ("ai_api_key", "decrypted_api_key") in keyring_writes
        assert {"ai_api_key": ""} in config_saves


@pytest.mark.unit
class TestKeyringFallbackClearsStale:
    """H-3: save_db_password fallback must clear stale keyring entry."""

    def test_keyring_fallback_clears_stale_keyring(self, monkeypatch):
        import utils.config_handler as cfg_mod

        delete_calls = []
        monkeypatch.setattr(
            cfg_mod.keyring,
            "set_password",
            MagicMock(side_effect=RuntimeError("keyring unavailable")),
        )
        monkeypatch.setattr(cfg_mod.keyring, "delete_password", lambda s, k: delete_calls.append((s, k)))
        monkeypatch.setattr(cfg_mod.SecurityManager, "encrypt_data", lambda x: "ENC_NEW")
        monkeypatch.setattr(cfg_mod.ConfigHandler, "save_config", lambda _: True)

        result = cfg_mod.ConfigHandler.save_db_password("new_password_v2")
        assert result is True
        assert delete_calls == [(cfg_mod.KEYRING_SERVICE_NAME, "db_password")], (
            "H-3: fallback path must wipe stale keyring entry to prevent old password winning"
        )

    def test_keyring_success_path_does_not_delete(self, monkeypatch):
        import utils.config_handler as cfg_mod

        delete_calls = []
        monkeypatch.setattr(cfg_mod.keyring, "set_password", MagicMock())
        monkeypatch.setattr(cfg_mod.keyring, "delete_password", lambda s, k: delete_calls.append((s, k)))
        monkeypatch.setattr(cfg_mod.ConfigHandler, "save_config", lambda _: True)

        result = cfg_mod.ConfigHandler.save_db_password("pw")
        assert result is True
        assert len(delete_calls) == 0, "H-3: success path must NOT call delete_password"
