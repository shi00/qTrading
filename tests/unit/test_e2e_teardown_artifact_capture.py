"""E2E teardown 工件独立捕获契约测试（D15-F04）。

守护 ``tests/e2e/conftest.py::_teardown_page``：

- 失败分支的 ``page.screenshot`` 与 ``context.tracing.stop(path=...)`` 必须各自
  位于独立的 ``try`` 块，截图失败不得阻断 trace 归档；
- 两个工件捕获失败都必须被记录而不抛出（两者都失败时 teardown 仍可继续）；
- ``asyncio.CancelledError`` 必须显式 ``raise``（R2 取消传播）；
- ``context.close()`` 必须在 ``finally`` 中执行。

采用静态 AST 检查，不导入 E2E conftest，避免其模块级 keyring mock / I18n
初始化污染单测会话（与 ``tests/unit/test_e2e_canary_fail_closed.py`` 同策略）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"
TEARDOWN_NAME = "_teardown_page"


def _tree() -> ast.Module:
    return ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))


def _function_node(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{CONFTEST_PATH.name} 缺少函数 {name}")


def _find_calls(
    node: ast.AST,
    *,
    attr: str,
    owner: str,
    parent_attr: str | None = None,
) -> list[ast.Call]:
    """按 ``owner[.parent_attr].attr(...)`` 形态定位调用节点。"""
    calls: list[ast.Call] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        func = child.func
        if func.attr != attr:
            continue
        target = func.value
        if parent_attr is not None:
            if not isinstance(target, ast.Attribute) or target.attr != parent_attr:
                continue
            target = target.value
        if not isinstance(target, ast.Name) or target.id != owner:
            continue
        calls.append(child)
    return calls


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(child is target for child in ast.walk(root))


def _innermost_try_containing(func: ast.AST, call: ast.Call) -> ast.Try:
    """返回直接包裹 ``call`` 的最内层 ``try`` 语句。"""
    candidates = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Try) and any(_contains(stmt, call) for stmt in node.body)
    ]
    assert candidates, f"未找到包裹 {ast.unparse(call)} 的 try 块"
    return min(candidates, key=lambda node: sum(1 for _ in ast.walk(node)))


def _has_path_kw(call: ast.Call) -> bool:
    return any(kw.arg == "path" for kw in call.keywords)


def _assert_cancelled_error_reraised(try_node: ast.Try) -> None:
    cancelled_handlers = [
        handler
        for handler in try_node.handlers
        if handler.type is not None and "CancelledError" in ast.unparse(handler.type)
    ]
    assert len(cancelled_handlers) >= 1, "捕获块必须显式处理 asyncio.CancelledError（R2）"
    for handler in cancelled_handlers:
        assert handler.body and isinstance(handler.body[-1], ast.Raise), (
            "asyncio.CancelledError 必须 raise 以配合优雅停机（R2）"
        )


def test_screenshot_and_trace_capture_are_independent() -> None:
    func = _function_node(_tree(), TEARDOWN_NAME)
    screenshots = _find_calls(func, attr="screenshot", owner="page")
    trace_stops = [
        call for call in _find_calls(func, attr="stop", owner="context", parent_attr="tracing") if _has_path_kw(call)
    ]
    assert len(screenshots) == 1, "失败分支应有且仅有一次 screenshot 调用"
    assert len(trace_stops) == 1, "失败分支应有且仅有一次带 path 的 tracing.stop 调用"

    screenshot_try = _innermost_try_containing(func, screenshots[0])
    trace_try = _innermost_try_containing(func, trace_stops[0])
    assert not _contains(screenshot_try, trace_stops[0]), (
        "tracing.stop 不得与 screenshot 共用同一 try 块：截图失败会阻断 trace 归档（D15-F04）"
    )
    assert not _contains(trace_try, screenshots[0]), "screenshot 不得与 tracing.stop 共用同一 try 块（D15-F04）"


def test_capture_failures_are_contained_and_cancellation_propagates() -> None:
    func = _function_node(_tree(), TEARDOWN_NAME)
    screenshots = _find_calls(func, attr="screenshot", owner="page")
    trace_stops = [
        call for call in _find_calls(func, attr="stop", owner="context", parent_attr="tracing") if _has_path_kw(call)
    ]
    for call in (*screenshots, *trace_stops):
        try_node = _innermost_try_containing(func, call)
        _assert_cancelled_error_reraised(try_node)
        exception_handlers = [
            handler
            for handler in try_node.handlers
            if handler.type is not None and "Exception" in ast.unparse(handler.type)
        ]
        assert len(exception_handlers) >= 1, f"{ast.unparse(call)} 的失败必须被捕获并记录，不得中断 teardown"


def test_context_closed_in_finally() -> None:
    func = _function_node(_tree(), TEARDOWN_NAME)
    close_calls = _find_calls(func, attr="close", owner="context")
    assert len(close_calls) == 1, "teardown 必须关闭浏览器 context（且仅关闭一次）"
    closed_in_finally = any(
        isinstance(node, ast.Try)
        and node.finalbody
        and any(_contains(stmt, call) for stmt in node.finalbody for call in close_calls)
        for node in ast.walk(func)
    )
    assert closed_in_finally, "context.close() 必须在 finally 中执行，确保失败/取消路径都释放资源"


def test_normal_path_still_stops_tracing_without_path() -> None:
    func = _function_node(_tree(), TEARDOWN_NAME)
    trace_stops = _find_calls(func, attr="stop", owner="context", parent_attr="tracing")
    assert any(not _has_path_kw(call) for call in trace_stops), "非失败路径必须保留不带 path 的 tracing.stop()"
