"""契约测试：确保 _create_mock_keyring 覆盖配置域所有 keyring API。

review05-E11 后 ``ConfigHandler`` 已拆为 ``utils/config_handler.py``（薄 facade）+
``utils/config/*.py`` 各领域模块，keyring 调用实际落在 ``utils/config/secrets.py`` /
``storage.py`` / ``llm.py``。旧实现只扫描 facade，使用集合恒为空 → 契约测试空转，
mock 缺 API 也不会失败。本用例同时扫描 facade 与领域模块，并要求使用集合非空。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PACKAGE = _PROJECT_ROOT / "utils" / "config"
_KEYRING_SOURCE_PATHS = [
    _PROJECT_ROOT / "utils" / "config_handler.py",
    *sorted(_CONFIG_PACKAGE.glob("*.py")),
]

# _create_mock_keyring 显式支持的 API（MagicMock 对任意属性 hasattr 恒为 True，
# 不能用 hasattr 校验，故以显式集合为准）。
_SUPPORTED_KEYRING_APIS = frozenset(
    {
        "get_password",
        "set_password",
        "delete_password",
        "clear",
        "errors.NoKeyringError",
        "errors.PasswordDeleteError",
    }
)


def _keyring_owner(node: ast.AST) -> bool:
    """node 是否为 ``keyring`` 命名空间的引用（``keyring`` 或 ``cfg.keyring``）。"""
    if isinstance(node, ast.Name):
        return node.id == "keyring"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "keyring"
        and isinstance(node.value, ast.Name)
        and node.value.id == "cfg"
    )


def _iter_keyring_usages(tree: ast.AST):
    """产出源码中使用的 keyring API 名（支持 ``keyring.x`` / ``cfg.keyring.x`` 及 errors 子命名空间）。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if _keyring_owner(value):
            # keyring.<api> / cfg.keyring.<api>；中间节点 "errors" 不是 API 本身，跳过
            if node.attr != "errors":
                yield node.attr
        elif isinstance(value, ast.Attribute) and value.attr == "errors" and _keyring_owner(value.value):
            yield f"errors.{node.attr}"


def _collect_used_apis() -> set[str]:
    used: set[str] = set()
    for path in _KEYRING_SOURCE_PATHS:
        assert path.is_file(), f"keyring 扫描目标缺失: {path}"
        used.update(_iter_keyring_usages(ast.parse(path.read_text(encoding="utf-8"))))
    return used


def test_keyring_mock_covers_all_used_apis():
    """扫描配置域源码中所有 keyring API 调用，确保 mock 覆盖。"""
    used_apis = _collect_used_apis()
    # 防空转：拆分后 keyring 调用位于领域模块，使用集合必须非空
    assert used_apis, f"未在 {_KEYRING_SOURCE_PATHS} 中发现任何 keyring 调用（扫描路径失效）"
    missing = used_apis - _SUPPORTED_KEYRING_APIS
    assert not missing, f"_create_mock_keyring 未声明支持的 keyring API: {sorted(missing)}"

    from tests.conftest import _create_mock_keyring

    mock_kr = _create_mock_keyring()
    for api in used_apis:
        if api.startswith("errors."):
            error_name = api.split(".", 1)[1]
            assert error_name in mock_kr.errors.__dict__, f"mock_keyring.errors 缺少 keyring.{api}"
            error_class = mock_kr.errors.__dict__[error_name]
            assert isinstance(error_class, type) and issubclass(error_class, Exception), (
                f"mock_keyring.errors.{error_name} 必须是异常类，不是 MagicMock"
            )
        else:
            assert api in mock_kr.__dict__, f"mock_keyring 缺少 keyring.{api}"
