"""R9 红线测试: ConfigHandler 的 secret getter 必须注册到 DataSanitizer。

get_db_password / get_llm_config / get_provider_credential 的每个返回分支
(env/keyring/encrypted config) 都必须调用 DataSanitizer.register_secret(value)，
确保日志中不会泄露明文 secret（参考 get_token() 模式）。
"""

from unittest.mock import MagicMock

import pytest

from utils import config_handler as cfg_mod
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_known_secrets():
    """每个测试前后清空 _known_secrets，避免测试间状态污染。"""
    DataSanitizer._reset_known_secrets()
    yield
    DataSanitizer._reset_known_secrets()


# 测试用 secret 必须长度 >= 8 (DataSanitizer._MIN_SECRET_LEN)
_SECRET_PASSWORD = "test_db_password_12345"
_SECRET_API_KEY = "test_ai_api_key_67890"
_SECRET_PROVIDER_KEY = "test_provider_key_abcde"


def _patch_llm_base_config(monkeypatch, extra=None):
    """Patch load_config 返回 LLM 基础配置，避免触发 LLM_PROVIDERS 查找。"""
    base = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "llm_base_url": "https://api.deepseek.com",
    }
    if extra:
        base.update(extra)
    monkeypatch.setattr(cfg_mod.ConfigHandler, "load_config", lambda: base)


class TestGetDbPasswordRegistersSecret:
    """R9: get_db_password 的每个返回分支必须注册 secret。"""

    def test_env_password_registers_secret(self, monkeypatch):
        monkeypatch.setenv(cfg_mod.ENV_FALLBACK_MAP["db_password"], _SECRET_PASSWORD)
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        monkeypatch.setattr(cfg_mod.ConfigHandler, "load_config", lambda: {})

        result = ConfigHandler.get_db_password()

        assert result == _SECRET_PASSWORD
        assert _SECRET_PASSWORD in DataSanitizer._known_secrets

    def test_keyring_password_registers_secret(self, monkeypatch):
        monkeypatch.delenv(cfg_mod.ENV_FALLBACK_MAP["db_password"], raising=False)
        monkeypatch.setattr(
            cfg_mod.keyring,
            "get_password",
            lambda service, key: _SECRET_PASSWORD if key == "db_password" else None,
        )
        monkeypatch.setattr(cfg_mod.ConfigHandler, "load_config", lambda: {})

        result = ConfigHandler.get_db_password()

        assert result == _SECRET_PASSWORD
        assert _SECRET_PASSWORD in DataSanitizer._known_secrets

    def test_encrypted_password_registers_secret(self, monkeypatch):
        monkeypatch.delenv(cfg_mod.ENV_FALLBACK_MAP["db_password"], raising=False)
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {"db_password_encrypted": "ENCRYPTED_VALUE"},
        )
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "_try_decrypt",
            lambda v: _SECRET_PASSWORD if v else "",
        )

        result = ConfigHandler.get_db_password()

        assert result == _SECRET_PASSWORD
        assert _SECRET_PASSWORD in DataSanitizer._known_secrets


class TestGetLlmConfigRegistersSecret:
    """R9: get_llm_config 的每个 api_key 返回分支必须注册 secret。"""

    def test_env_api_key_registers_secret(self, monkeypatch):
        monkeypatch.setenv(cfg_mod.ENV_FALLBACK_MAP["ai_api_key"], _SECRET_API_KEY)
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        _patch_llm_base_config(monkeypatch)

        result = ConfigHandler.get_llm_config()

        assert result["api_key"] == _SECRET_API_KEY
        assert _SECRET_API_KEY in DataSanitizer._known_secrets

    def test_keyring_api_key_registers_secret(self, monkeypatch):
        monkeypatch.delenv(cfg_mod.ENV_FALLBACK_MAP["ai_api_key"], raising=False)
        monkeypatch.setattr(
            cfg_mod.keyring,
            "get_password",
            lambda service, key: _SECRET_API_KEY if key == "ai_api_key" else None,
        )
        _patch_llm_base_config(monkeypatch)

        result = ConfigHandler.get_llm_config()

        assert result["api_key"] == _SECRET_API_KEY
        assert _SECRET_API_KEY in DataSanitizer._known_secrets

    def test_encrypted_api_key_registers_secret(self, monkeypatch):
        monkeypatch.delenv(cfg_mod.ENV_FALLBACK_MAP["ai_api_key"], raising=False)
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        _patch_llm_base_config(monkeypatch, extra={"ai_api_key": "ENCRYPTED_VALUE"})
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "_try_decrypt",
            lambda v: _SECRET_API_KEY if v else "",
        )
        # 避免 keyring migration 写入干扰
        monkeypatch.setattr(cfg_mod.keyring, "set_password", MagicMock())
        monkeypatch.setattr(cfg_mod.ConfigHandler, "save_config", lambda payload: True)

        result = ConfigHandler.get_llm_config()

        assert result["api_key"] == _SECRET_API_KEY
        assert _SECRET_API_KEY in DataSanitizer._known_secrets


class TestGetProviderCredentialRegistersSecret:
    """R9: get_provider_credential 的每个 api_key 返回分支必须注册 secret。"""

    def test_provider_specific_keyring_registers_secret(self, monkeypatch):
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {"llm_provider_credentials": {}, "llm_custom_models": {}},
        )

        def mock_get_pw(service, key):
            if key == "ai_api_key_qwen":
                return _SECRET_PROVIDER_KEY
            return None

        monkeypatch.setattr(cfg_mod.keyring, "get_password", mock_get_pw)

        result = ConfigHandler.get_provider_credential("qwen")

        assert result["api_key"] == _SECRET_PROVIDER_KEY
        assert _SECRET_PROVIDER_KEY in DataSanitizer._known_secrets

    def test_provider_specific_encrypted_registers_secret(self, monkeypatch):
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {
                "llm_provider_credentials": {"qwen": {"api_key_encrypted": "ENC_VALUE"}},
                "llm_custom_models": {},
            },
        )
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        monkeypatch.setattr(
            cfg_mod.SecurityManager,
            "decrypt_data",
            lambda v: _SECRET_PROVIDER_KEY if v else None,
        )

        result = ConfigHandler.get_provider_credential("qwen")

        assert result["api_key"] == _SECRET_PROVIDER_KEY
        assert _SECRET_PROVIDER_KEY in DataSanitizer._known_secrets

    def test_global_keyring_fallback_registers_secret(self, monkeypatch):
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {"llm_provider_credentials": {}, "llm_custom_models": {}},
        )

        def mock_get_pw(service, key):
            if key == "ai_api_key":
                return _SECRET_API_KEY
            return None

        monkeypatch.setattr(cfg_mod.keyring, "get_password", mock_get_pw)

        result = ConfigHandler.get_provider_credential("qwen", fallback_to_global=True)

        assert result["api_key"] == _SECRET_API_KEY
        assert _SECRET_API_KEY in DataSanitizer._known_secrets

    def test_global_encrypted_fallback_registers_secret(self, monkeypatch):
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "load_config",
            lambda: {
                "llm_provider_credentials": {},
                "llm_custom_models": {},
                "ai_api_key": "ENC_GLOBAL",
            },
        )
        monkeypatch.setattr(cfg_mod.keyring, "get_password", MagicMock(return_value=None))
        monkeypatch.setattr(
            cfg_mod.SecurityManager,
            "decrypt_data",
            lambda v: _SECRET_API_KEY if v else None,
        )

        result = ConfigHandler.get_provider_credential("qwen", fallback_to_global=True)

        assert result["api_key"] == _SECRET_API_KEY
        assert _SECRET_API_KEY in DataSanitizer._known_secrets
