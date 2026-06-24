# utils/db_utils.py
from utils.config_handler import ConfigHandler


def get_db_pool_config() -> dict:
    """提取数据库连接池配置。

    ConfigHandler.get_typed 已内置类型转换与默认值回退，
    此函数仅负责组装为 dict 供 create_engine / create_async_engine 使用。
    """
    return {
        "pool_size": ConfigHandler.get_db_connection_pool_size(),
        "max_overflow": ConfigHandler.get_db_max_overflow(),
        "pool_timeout": ConfigHandler.get_db_pool_timeout(),
        "pool_recycle": ConfigHandler.get_db_pool_recycle(),
        "pool_pre_ping": ConfigHandler.get_db_pool_pre_ping(),
    }
