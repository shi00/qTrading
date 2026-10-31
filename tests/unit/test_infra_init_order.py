"""启动序列静态契约：代理策略必须先于会话/业务服务初始化。

历史实现只在测试内手工调用 mock 的 ``apply_smart_proxy_policy`` 与 ``TushareClient()``，
未触及生产代码，断言顺序完全由测试自身写入（自证循环）。现改为解析真实启动入口
``app/application.py::run``，校验 ``ProxyManager.apply_smart_proxy_policy()``
（NO_PROXY 就绪）出现在 ``ApplicationSession`` / ``_run_session`` 之前，
避免网络客户端在代理策略生效前建立连接。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_PATH = PROJECT_ROOT / "app" / "application.py"
_ENTRY_FUNCTION = "run"
_POLICY_CALL = "ProxyManager.apply_smart_proxy_policy"
_SESSION_CALLS = {"ApplicationSession", "_run_session"}


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ""


def _call_lines(tree: ast.AST, names: set[str]) -> list[int]:
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and _dotted_name(node.func) in names]


def _entry_function(tree: ast.Module) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == _ENTRY_FUNCTION:
            return node
    raise AssertionError(f"app/application.py 缺少启动入口 {_ENTRY_FUNCTION}()")


def test_proxy_policy_applied_before_session_initialization():
    tree = ast.parse(APPLICATION_PATH.read_text(encoding="utf-8"), filename=str(APPLICATION_PATH))
    entry = _entry_function(tree)

    policy_lines = _call_lines(entry, {_POLICY_CALL})
    session_lines = _call_lines(entry, _SESSION_CALLS)

    assert policy_lines, f"{_ENTRY_FUNCTION}() 未调用 {_POLICY_CALL}()"
    assert session_lines, f"{_ENTRY_FUNCTION}() 未进入 ApplicationSession/_run_session 初始化"
    assert min(policy_lines) < min(session_lines), (
        "代理策略必须在会话/业务服务初始化之前应用，否则网络客户端可能早于 NO_PROXY 建立连接"
    )
