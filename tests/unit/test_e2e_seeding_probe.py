"""seeding 页面探测的失败传播契约测试（D21-OTHER-004）。

守护 ``tests/e2e/helpers/seeding_probe.py`` 与 ``tests/e2e/conftest.py``
``_trigger_sidecar_startup_via_browser`` 的调用点：

- 错误 UI 已出现属于确定性失败，必须立即抛 ``SeedingProbeFailure``，
  不得返回「未就绪」让调用方继续 sleep 到 600s 通用超时；
- 仅调用方显式声明的可重试暂态异常被降级为重试；
- 其它异常（含 ``asyncio.CancelledError``）原样传播（R2）；
- 调用点不得用 ``except Exception`` 包裹探测，``context.close()`` 只能在
  ``finally`` 中恰好执行一次。

采用伪 page 行为断言 + 静态 AST 检查调用点，不导入 E2E conftest
（其模块级 keyring 替身会污染单测会话）。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from tests.e2e.helpers.seeding_probe import SeedingProbeFailure, require_seeding_page_ready

pytestmark = pytest.mark.unit

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"
TRIGGER_NAME = "_trigger_sidecar_startup_via_browser"
PROBE_NAME = "require_seeding_page_ready"
ERROR_SELECTOR = "text=DB init failed"
READY_SELECTOR = "text=Screener"


class _FakeLocator:
    def __init__(self, result: int | BaseException) -> None:
        self._result = result

    async def count(self) -> int:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _FakePage:
    """按选择器返回预设结果，并记录查询顺序。"""

    def __init__(self, results: dict[str, int | BaseException] | None = None) -> None:
        self._results = results or {}
        self.queried: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        self.queried.append(selector)
        return _FakeLocator(self._results.get(selector, 0))


async def _probe(page: _FakePage, retryable_errors: tuple[type[BaseException], ...] = (TimeoutError,)) -> bool:
    return await require_seeding_page_ready(
        page,
        error_text="DB init failed",
        ready_text="Screener",
        retryable_errors=retryable_errors,
    )


async def test_error_ui_raises_immediately() -> None:
    """错误 UI 已出现时必须抛原诊断，而不是继续轮询到总超时。"""
    page = _FakePage({ERROR_SELECTOR: 1, READY_SELECTOR: 1})

    with pytest.raises(SeedingProbeFailure, match="DB initialization error UI"):
        await _probe(page)

    assert page.queried == [ERROR_SELECTOR], "错误 UI 命中后不得继续探测就绪文本，必须立即失败"


async def test_ready_page_reports_ready() -> None:
    page = _FakePage({ERROR_SELECTOR: 0, READY_SELECTOR: 1})

    assert await _probe(page) is True


async def test_pending_page_reports_not_ready() -> None:
    page = _FakePage({ERROR_SELECTOR: 0, READY_SELECTOR: 0})

    assert await _probe(page) is False


async def test_retryable_probe_error_is_retried() -> None:
    """等待/定位暂态失败只降级为重试，不得让调用方失败。"""
    transient = TimeoutError("page not ready")
    page = _FakePage({ERROR_SELECTOR: transient, READY_SELECTOR: transient})

    assert await _probe(page) is False


async def test_unexpected_probe_error_propagates() -> None:
    """未被声明为可重试的异常必须原样传播。"""
    page = _FakePage({ERROR_SELECTOR: RuntimeError("target detached")})

    with pytest.raises(RuntimeError, match="target detached"):
        await _probe(page)


async def test_cancellation_propagates() -> None:
    """R2：取消必须传播，不能被探测重试吞掉。"""
    page = _FakePage({ERROR_SELECTOR: asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await _probe(page)


def _tree() -> ast.Module:
    return ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))


def _function_node(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{CONFTEST_PATH.name} 缺少函数 {name}")


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(child is target for child in ast.walk(root))


def _probe_call(func: ast.AST) -> ast.Call:
    calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == PROBE_NAME
    ]
    assert len(calls) == 1, f"{TRIGGER_NAME} 应有且仅有一次 {PROBE_NAME} 调用"
    return calls[0]


def test_probe_failure_is_not_wrapped_by_broad_except() -> None:
    """探测调用不得被 try/except 包裹：否则自诊断会退化为通用超时。"""
    func = _function_node(_tree(), TRIGGER_NAME)
    probe_call = _probe_call(func)

    for try_node in (node for node in ast.walk(func) if isinstance(node, ast.Try)):
        if not any(_contains(stmt, probe_call) for stmt in try_node.body):
            continue
        for handler in try_node.handlers:
            caught = "" if handler.type is None else ast.unparse(handler.type)
            assert "Exception" not in caught and "BaseException" not in caught, (
                f"{PROBE_NAME} 的异常不得被 except {caught or '裸 except'} 捕获（D21-OTHER-004）"
            )


def test_context_closed_once_in_finally() -> None:
    """失败/取消路径也必须回收 context，且只能关闭一次。"""
    func = _function_node(_tree(), TRIGGER_NAME)
    close_calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "context"
    ]
    assert len(close_calls) == 1, f"{TRIGGER_NAME} 必须恰好关闭一次 context"
    closed_in_finally = any(
        isinstance(node, ast.Try) and node.finalbody and any(_contains(stmt, close_calls[0]) for stmt in node.finalbody)
        for node in ast.walk(func)
    )
    assert closed_in_finally, "context.close() 必须在 finally 中执行，确保失败/取消路径都释放资源"
