import pytest
from unittest.mock import patch
from utils.config_handler import ConfigHandler

@pytest.fixture(autouse=True)
def isolate_config_file(tmp_path):
    """
    全局测试隔离夹具（Global Autouse Fixture）。
    
    确保每一个测试函数运行时，ConfigHandler 操作的是一个由 pytest 全权管理的
    临时文件，而非工程根目录下的真实 user_settings.json。
    
    - 作用域：function（每个测试用例独立隔离）
    - 自动注入：autouse=True（无需任何测试文件显式引用）
    - 无文件锁：使用 tmp_path 而非 mkstemp，规避 Windows 文件锁定问题
    """
    tmp_file = str(tmp_path / "test_settings.json")

    # 清空内存缓存，防止读取到上一个测例的残留状态
    ConfigHandler._config_cache = None

    with patch('utils.config_handler.CONFIG_FILE', tmp_file):
        yield

    # 测试结束后再次清空，确保下一个测例从零开始
    ConfigHandler._config_cache = None
