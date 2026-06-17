"""P1-2 污染探测测试：验证 reset_loop_local_cache fixture 确实清理 loop-local 状态。

背景：pyproject.toml 配置 asyncio_default_test_loop_scope = "session"，
所有测试共享一个事件循环。loop-local 缓存（asyncio.Event/Lock/Semaphore）
会跨测试泄漏，由 tests/conftest.py 的 reset_loop_local_cache autouse fixture
负责清理。本测试验证该 fixture 的清理有效性。

技术债说明（§7.2）：
session 级事件循环是已知技术债，中期应降为 function 作用域以从根因消除泄漏。
降级前，本测试作为"创可贴"有效性的守护者。
"""

import asyncio
import tomllib
from pathlib import Path

import pytest

from utils.loop_local import get_loop_local, clear_all_loop_locals, _fallback_store


def test_clear_all_loop_locals_clears_fallback_store():
    """clear_all_loop_locals 应清理 fallback_store（同步调用路径）。

    同步调用 get_loop_local(strict=False) 时走 fallback_store 分支，
    清理后再次获取应创建新对象。
    """
    event1 = get_loop_local("test_fallback_probe", lambda: asyncio.Event(), strict=False)

    clear_all_loop_locals()

    event2 = get_loop_local("test_fallback_probe", lambda: asyncio.Event(), strict=False)
    assert event2 is not event1, "clear_all_loop_locals 未清理 fallback_store——旧对象仍被复用"


@pytest.mark.asyncio
async def test_clear_all_loop_locals_clears_loop_bound_store():
    """clear_all_loop_locals 应清理 loop-bound store（async 调用路径，核心场景）。

    session 级事件循环泄漏的核心场景：多个 async 测试共享同一 loop，
    _stores[key][loop] 会累积缓存。本测试验证清理后再次获取创建新对象。
    """
    event1 = get_loop_local("test_loop_bound_probe", lambda: asyncio.Event(), strict=True)

    clear_all_loop_locals()

    event2 = get_loop_local("test_loop_bound_probe", lambda: asyncio.Event(), strict=True)
    assert event2 is not event1, "clear_all_loop_locals 未清理 loop-bound store——旧对象仍被复用"


def test_reset_loop_local_cache_fixture_clears_between_tests():
    """reset_loop_local_cache autouse fixture 应在测试间清理 fallback_store。

    本测试验证 fixture 已执行（测试开始时 fallback_store 应为空）。
    若 fixture 失效，上一个测试遗留的缓存会残留。
    """
    # fixture 应在测试开始前已清理，fallback_store 应为空
    assert "test_fallback_probe" not in _fallback_store, (
        "reset_loop_local_cache fixture 未清理 fallback_store——上一个测试的缓存残留"
    )
    assert "test_loop_bound_probe" not in _fallback_store, (
        "reset_loop_local_cache fixture 未清理 fallback_store——上一个测试的缓存残留"
    )


def test_session_loop_scope_is_known_tech_debt():
    """文档性测试：session 级事件循环是已知技术债。

    此测试不验证行为，只作为活文档，提醒后续开发者：
    1. session 作用域导致 loop-local 泄漏，由 autouse fixture 维持隔离
    2. 中期应降为 function 作用域以从根因消除泄漏
    3. 降级后可删除 reset_loop_local_cache fixture 和本测试文件
    """
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        config = tomllib.load(f)
    loop_scope = config["tool"]["pytest"]["ini_options"].get("asyncio_default_test_loop_scope")
    assert loop_scope == "session", (
        f"事件循环作用域已变更（当前: {loop_scope}）。"
        f"若已降为 function，请删除 reset_loop_local_cache fixture 和本测试文件"
    )
