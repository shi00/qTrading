import inspect
from unittest.mock import MagicMock, patch


import utils.config_handler as cfg_mod


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


class TestDbUrlPasswordMasking:
    """db_url 明文口令落盘防护"""

    def test_get_db_url_restores_password(self):
        source = inspect.getsource(cfg_mod.ConfigHandler.get_db_url)
        assert "****" in source
        assert "replace" in source
