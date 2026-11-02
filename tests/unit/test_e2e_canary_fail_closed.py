"""E2E page canary fail-closed 契约测试（D15-F01）。

守护 ``tests/e2e/conftest.py::_e2e_page_with_viewport``：
- 锚点回归 / 页面未正确渲染时必须让用例失败（fail-closed）；
- 不得用 ``pytest.skip`` 把真实回归转成 skip；
- 仅环境不可用（sidecar binary 缺失）保留显式 skip。

采用静态 AST 检查，不导入 E2E conftest（避免其模块级 keyring mock 污染单测会话）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"
CANARY_LIFECYCLE = "_e2e_page_with_viewport"
SIDECAR_FIXTURE = "real_sidecar_binary_e2e"


def _tree() -> ast.Module:
    return ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))


def _function_node(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{CONFTEST_PATH.name} 缺少函数 {name}")


def _pytest_calls(node: ast.AST) -> list[str]:
    return [
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "pytest"
    ]


def test_canary_failure_fails_instead_of_skipping() -> None:
    calls = _pytest_calls(_function_node(_tree(), CANARY_LIFECYCLE))
    assert "fail" in calls, "canary 失败必须以 pytest.fail 失败退出（fail-closed）"
    assert "skip" not in calls, "canary 失败不得用 pytest.skip 掩盖（D15-F01）"


def test_environment_unavailable_skip_is_preserved() -> None:
    calls = _pytest_calls(_function_node(_tree(), SIDECAR_FIXTURE))
    assert "skip" in calls, "sidecar binary 缺失等环境不可用应保留显式 skip"
