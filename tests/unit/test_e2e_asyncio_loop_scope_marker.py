"""E2E session loop scope marker 注入契约测试（CI 死锁回归守护）。

守护 ``tests/e2e/conftest.py`` 的 marker 注入位置：

- marker 必须在 ``pytest_generate_tests``（参数化阶段）注入，且 hook 声明
  ``tryfirst=True``，确保早于 pytest-asyncio 的 ``pytest_generate_tests``；
- ``pytest_collection_modifyitems`` 不得再注入 asyncio marker。

根因：pytest-asyncio 1.4 在 ``pytest_generate_tests`` 中按 marker 的
``loop_scope`` 参数化 ``_asyncio_loop_factory``。marker 晚到（收集完成后）
会让 session-scope async fixture 退化为逐用例重建，E2E 播种（``TRUNCATE ...
CASCADE``）与运行中应用并发写库形成 PostgreSQL 死锁
（``DeadlockDetectedError``）。

采用静态 AST 检查，不导入 E2E conftest（其模块级 keyring mock / I18n
初始化会污染单测会话），与 ``tests/unit/test_e2e_teardown_artifact_capture.py``
同策略。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"


def _function_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"tests/e2e/conftest.py 缺少函数 {name}")


def _hookimpl_kwargs(node: ast.FunctionDef) -> dict[str, object]:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not (isinstance(func, ast.Attribute) and func.attr == "hookimpl"):
            continue
        kwargs: dict[str, object] = {}
        for kw in decorator.keywords:
            if kw.arg is not None and isinstance(kw.value, ast.Constant):
                kwargs[kw.arg] = kw.value.value
        return kwargs
    return {}


def _add_marker_calls(node: ast.FunctionDef) -> list[ast.Call]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "add_marker"
    ]


def _is_session_asyncio_marker(call: ast.Call) -> bool:
    if not call.args:
        return False
    marker = call.args[0]
    if not isinstance(marker, ast.Call):
        return False
    func = marker.func
    if not (isinstance(func, ast.Attribute) and func.attr == "asyncio"):
        return False
    return any(
        kw.arg == "loop_scope" and isinstance(kw.value, ast.Constant) and kw.value.value == "session"
        for kw in marker.keywords
    )


def test_marker_injected_in_pytest_generate_tests_with_tryfirst() -> None:
    node = _function_node("pytest_generate_tests")
    assert _hookimpl_kwargs(node).get("tryfirst") is True
    assert any(_is_session_asyncio_marker(call) for call in _add_marker_calls(node)), (
        "pytest_generate_tests 必须注入 pytest.mark.asyncio(loop_scope='session')"
    )


def test_collection_modifyitems_no_longer_injects_asyncio_marker() -> None:
    node = _function_node("pytest_collection_modifyitems")
    assert _add_marker_calls(node) == [], (
        "asyncio marker 不得在收集完成后注入：pytest-asyncio 的 loop scope 参数化已在 pytest_generate_tests 阶段完成"
    )
