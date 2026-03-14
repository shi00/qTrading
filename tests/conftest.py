from unittest.mock import patch

import pytest

from utils.config_handler import ConfigHandler
from utils.security_utils import SecurityManager

# ---------------------------------------------------------------------------
# Keyring 内存替身（Mock）
# ---------------------------------------------------------------------------
_MOCK_KEYRING = {}


def _mock_set(service, username, password):
    _MOCK_KEYRING[(service, username)] = password


def _mock_get(service, username):
    return _MOCK_KEYRING.get((service, username))


def _mock_del(service, username):
    _MOCK_KEYRING.pop((service, username), None)


# ---------------------------------------------------------------------------
# 全局测试隔离夹具
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolate_environment(tmp_path):
    """
    全局测试隔离夹具（Global Autouse Fixture）。

    将三类"可穿透到真实环境"的外部资源统一收进 tmp_path 沙盒：
      1. user_settings.json  —— 防止测试覆写用户配置
      2. keyring (OS Vault)  —— 防止测试覆写 Tushare Token / AI API Key
      3. .secret.key         —— 防止测试重写加密母钥导致历史密文不可逆解密
    """
    # --- 重置内存缓存 ---
    ConfigHandler._config_cache = None
    SecurityManager._key = None
    _MOCK_KEYRING.clear()

    with (
        patch("utils.config_handler.CONFIG_FILE", str(tmp_path / "test_settings.json")),
        patch.object(SecurityManager, "KEY_FILE", str(tmp_path / ".secret.key")),
        patch.object(
            SecurityManager, "KEY_FILE_BAK", str(tmp_path / ".secret.key.bak"),
        ),
        patch("keyring.set_password", side_effect=_mock_set),
        patch("keyring.get_password", side_effect=_mock_get),
        patch("keyring.delete_password", side_effect=_mock_del),
    ):
        yield

    # --- 清扫 ---
    ConfigHandler._config_cache = None
    SecurityManager._key = None
    _MOCK_KEYRING.clear()
