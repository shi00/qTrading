"""E2E session loop scope marker 注入契约测试（CI 死锁 / 跨 loop 挂起回归守护）。

守护 ``tests/e2e/conftest.py`` 的双处 marker 注入：

- 模块级常量 ``_E2E_SESSION_LOOP_MARKER``（``pytest.mark.asyncio(loop_scope="session")``）
  由两个 hook 共享，保证 loop_scope 值单一来源；
- ``pytest_generate_tests``（``tryfirst=True``）把该常量写入 definition，
  使 pytest-asyncio 在参数化阶段按 ``loop_scope=session`` 参数化
  ``_asyncio_loop_factory``；
- ``pytest_collection_modifyitems`` 把同一常量补到实际 item 上，
  使 item 的 ``_loop_scope`` 为 session（pytest 9 的 item 不继承 definition marker）。

根因：pytest-asyncio 1.4 在 ``pytest_generate_tests`` 中按 marker 的
``loop_scope`` 参数化 ``_asyncio_loop_factory``。marker 晚到（收集完成后）
会让 session-scope async fixture 退化为逐用例重建，E2E 播种（``TRUNCATE ...
CASCADE``）与运行中应用并发写库形成 PostgreSQL 死锁
（``DeadlockDetectedError``）；若只有 definition marker 而 item 缺 marker，
测试会跑在 function loop 上、session fixtures 在 session loop 上，
跨 loop 访问 Playwright 对象会永久 hang。

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


def _module_assign_value(name: str) -> ast.expr:
    """返回模块顶层 ``name = <expr>`` 赋值的右侧表达式节点。"""
    tree = ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets:
            return node.value
    raise AssertionError(f"tests/e2e/conftest.py 缺少模块级赋值 {name}")


def _add_marker_uses_session_constant(node: ast.FunctionDef) -> bool:
    return any(
        call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "_E2E_SESSION_LOOP_MARKER"
        for call in _add_marker_calls(node)
    )


def test_session_loop_marker_constant_defined() -> None:
    value = _module_assign_value("_E2E_SESSION_LOOP_MARKER")
    assert isinstance(value, ast.Call)
    func = value.func
    assert isinstance(func, ast.Attribute) and func.attr == "asyncio", (
        "_E2E_SESSION_LOOP_MARKER 必须由 pytest.mark.asyncio(...) 构造"
    )
    assert any(
        kw.arg == "loop_scope" and isinstance(kw.value, ast.Constant) and kw.value.value == "session"
        for kw in value.keywords
    ), "_E2E_SESSION_LOOP_MARKER 必须带 loop_scope='session'"


def test_definition_marker_injected_in_pytest_generate_tests_with_tryfirst() -> None:
    node = _function_node("pytest_generate_tests")
    assert _hookimpl_kwargs(node).get("tryfirst") is True
    assert _add_marker_uses_session_constant(node), (
        "pytest_generate_tests 必须向 definition 注入 _E2E_SESSION_LOOP_MARKER"
    )


def test_item_marker_injected_in_pytest_collection_modifyitems() -> None:
    node = _function_node("pytest_collection_modifyitems")
    assert _add_marker_uses_session_constant(node), (
        "pytest_collection_modifyitems 必须为 item 补注入 _E2E_SESSION_LOOP_MARKER"
    )
