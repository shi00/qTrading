"""Function scope loop 隔离守护测试。

替换旧的 test_infra_loop_isolation.py（验证 reset_loop_local_cache fixture 有效）。
新测试验证 function scope 下 loop-local 隔离机制。

背景：pyproject.toml 配置 asyncio_default_test_loop_scope = "function"，
每个测试有独立的事件循环。loop-local 缓存（asyncio.Event/Lock/Semaphore）
通过 WeakKeyDictionary 绑定到当前循环，循环关闭后自动 GC，不跨测试残留。
_fallback_store（同步上下文 fallback）仍由 _reset_loop_local_fallback fixture 清理。

与旧测试的区别：
- 旧测试（test_infra_loop_isolation.py）：验证 reset_loop_local_cache fixture
  在 session scope 下有效清理 loop-local 状态（_stores + _fallback_store）
- 新测试（本文件）：验证 function scope 下 _stores 自动隔离，
  _fallback_store 由精准化的 _reset_loop_local_fallback fixture 清理
"""

import asyncio
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent


def test_unit_test_loop_scope_is_function():
    """守护测试：unit test 的 loop scope 必须为 function。

    function scope 下每个测试有独立的事件循环，loop-local 缓存
    通过 WeakKeyDictionary 自动隔离，无需 reset_loop_local_cache fixture。

    如果此测试失败（loop scope 不是 function），说明：
    1. pyproject.toml 的 asyncio_default_test_loop_scope 被改回 session，或
    2. reset_loop_local_cache fixture 被重新引入但 loop scope 未同步
    """
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        config = tomllib.load(f)
    loop_scope = config["tool"]["pytest"]["ini_options"].get("asyncio_default_test_loop_scope")
    assert loop_scope == "function", (
        f"unit test loop scope 应为 function（当前: {loop_scope}）。"
        f"function scope 下每个测试有独立事件循环，loop-local 缓存自动隔离。"
        f"若需改回 session，请重新引入 reset_loop_local_cache fixture。"
    )


async def test_loop_local_binds_to_current_loop():
    """验证 loop-local 对象绑定到当前事件循环，不使用全局 fallback。

    function scope 下每个测试有独立循环，get_loop_local() 应将对象
    绑定到当前循环（store[loop]），而非模块级 fallback_store。
    如果对象进入 fallback_store，说明在同步上下文中被调用，可能导致跨测试残留。
    """
    from utils.loop_local import _fallback_store, _stores, get_loop_local

    # 创建一个 loop-local Event
    event = get_loop_local("test_func_scope_guard", asyncio.Event)

    # 验证对象绑定到当前循环（在 _stores 中），而非 fallback_store
    loop = asyncio.get_running_loop()
    store = _stores.get("test_func_scope_guard")
    assert store is not None, "loop-local store not created"
    assert loop in store, "current loop not in store — object may have gone to fallback"
    assert "test_func_scope_guard" not in _fallback_store, (
        "loop-local 对象错误地存入了 fallback_store — 这表明在同步上下文中调用了 get_loop_local(strict=False)"
    )
    assert store[loop] is event, "store object mismatch"


async def test_loop_local_factory_not_called_twice_for_same_loop():
    """验证同一事件循环内 get_loop_local 只调用一次 factory。

    function scope 下每个测试有独立循环，factory 应只在首次访问时调用。
    如果 factory 被多次调用，说明 loop-local 缓存未正确绑定到循环。
    """
    from utils.loop_local import get_loop_local

    call_count = 0

    def counting_factory() -> asyncio.Event:
        nonlocal call_count
        call_count += 1
        return asyncio.Event()

    # 第一次获取：应调用 factory
    event1 = get_loop_local("test_factory_guard", counting_factory)
    assert call_count == 1, f"factory should be called once, got {call_count}"

    # 第二次获取同一 key：不应调用 factory，应返回同一对象
    event2 = get_loop_local("test_factory_guard", counting_factory)
    assert call_count == 1, f"factory should not be called twice, got {call_count}"
    assert event1 is event2, "same key should return same object within one loop"
