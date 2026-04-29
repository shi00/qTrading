from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
class TestConfigHandlerKeyringFallback:
    def test_save_db_password_falls_back_to_encrypt_when_keyring_fails(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod

        encrypted_value = "ENCRYPTED_SECRET_123"

        monkeypatch.setattr(cfg_mod.keyring, "set_password", MagicMock(side_effect=RuntimeError("keyring unavailable")))
        mock_encrypt = MagicMock(return_value=encrypted_value)
        monkeypatch.setattr(cfg_mod.SecurityManager, "encrypt_data", mock_encrypt)
        saved_configs = []
        monkeypatch.setattr(cfg_mod.ConfigHandler, "save_config", lambda payload: saved_configs.append(payload) or True)

        result = cfg_mod.ConfigHandler.save_db_password("my_secret_password")

        assert result is True
        mock_encrypt.assert_called_once_with("my_secret_password")
        assert len(saved_configs) == 1
        assert saved_configs[0] == {"db_password_encrypted": encrypted_value}

    def test_save_db_password_returns_false_when_both_fail(self, monkeypatch):
        import utils.config_handler as cfg_mod

        monkeypatch.setattr(cfg_mod.keyring, "set_password", MagicMock(side_effect=RuntimeError("keyring unavailable")))
        monkeypatch.setattr(
            cfg_mod.SecurityManager, "encrypt_data", MagicMock(side_effect=RuntimeError("encryption failed"))
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
        monkeypatch.setattr(cfg_mod.ConfigHandler, "save_config", lambda payload: saved_configs.append(payload) or True)

        result = cfg_mod.ConfigHandler.save_db_password("my_secret_password")

        assert result is True
        assert len(keyring_called) == 1
        assert keyring_called[0] == (cfg_mod.KEYRING_SERVICE_NAME, "db_password", "my_secret_password")
        assert saved_configs == [{"db_password_encrypted": ""}]

    def test_save_db_password_returns_false_for_empty(self, monkeypatch):
        import utils.config_handler as cfg_mod

        result = cfg_mod.ConfigHandler.save_db_password("")
        assert result is False

    def test_get_db_password_reads_encrypted_fallback(self, monkeypatch):
        import utils.config_handler as cfg_mod

        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(side_effect=RuntimeError("keyring unavailable")))
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
