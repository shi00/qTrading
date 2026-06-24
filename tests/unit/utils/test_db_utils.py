# tests/unit/utils/test_db_utils.py
from unittest.mock import patch
from utils.db_utils import get_db_pool_config


def test_get_db_pool_config_returns_configured_values():
    """get_db_pool_config 应正确组装 ConfigHandler 返回的连接池参数。"""
    with (
        patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value=20),
        patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value=10),
        patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value=60),
        patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value=3600),
        patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", return_value=False),
    ):
        config = get_db_pool_config()
        assert config == {
            "pool_size": 20,
            "max_overflow": 10,
            "pool_timeout": 60,
            "pool_recycle": 3600,
            "pool_pre_ping": False,
        }


def test_get_db_pool_config_uses_confighandler_defaults():
    """ConfigHandler.get_typed 已内置默认值回退，get_db_pool_config 应透传这些默认值。

    默认值由 ConfigHandler.get_typed 的 default 参数定义：
    pool_size=10, max_overflow=5, pool_timeout=30, pool_recycle=1800, pool_pre_ping=True
    """
    with (
        patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value=10),
        patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value=5),
        patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value=30),
        patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value=1800),
        patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", return_value=True),
    ):
        config = get_db_pool_config()
        assert config == {
            "pool_size": 10,
            "max_overflow": 5,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
