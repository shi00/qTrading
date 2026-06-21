"""契约测试：确保 _create_mock_keyring 覆盖 config_handler.py 使用的所有 keyring API。"""

import ast
from pathlib import Path
import pytest


pytestmark = pytest.mark.unit


def test_mock_keyring_covers_all_used_apis():
    """扫描 config_handler.py 中所有 keyring.xxx 调用，确保 mock 覆盖。

    覆盖 utils/config_handler.py 的 17 处 keyring 调用：
    - get_password (386, 561, 840, 1047, 1061)
    - set_password (132, 398, 415, 580, 789, 850, 986)
    - delete_password (409, 586, 808, 1008)
    - errors.PasswordDeleteError (1009)
    """
    # 基于 __file__ 定位项目根目录，避免依赖 cwd
    project_root = Path(__file__).resolve().parents[2]  # tests/unit/ -> tests/ -> project_root
    config_handler_path = project_root / "utils" / "config_handler.py"
    config_handler_source = config_handler_path.read_text(encoding="utf-8")
    tree = ast.parse(config_handler_source)

    used_apis = set()
    for node in ast.walk(tree):
        # 检查 keyring.xxx 模式
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "keyring":
            used_apis.add(node.attr)
        # 检查 keyring.errors.xxx 模式
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "keyring"
            and node.value.attr == "errors"
        ):
            used_apis.add(f"errors.{node.attr}")

    # mock_keyring 必须覆盖所有使用的 API
    from tests.conftest import _create_mock_keyring

    mock_kr = _create_mock_keyring()

    for api in used_apis:
        if api.startswith("errors."):
            error_class = api.split(".", 1)[1]
            assert hasattr(mock_kr.errors, error_class), f"mock_keyring.errors 缺少 keyring.{api}"
            # 确保是异常类，不是 MagicMock
            attr = getattr(mock_kr.errors, error_class)
            assert isinstance(attr, type) and issubclass(attr, Exception), (
                f"mock_keyring.errors.{error_class} 必须是异常类，不是 MagicMock"
            )
        else:
            assert hasattr(mock_kr, api), f"mock_keyring 缺少 keyring.{api}"
