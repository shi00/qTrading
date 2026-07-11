"""flet_test_page fixture 契约探针（Task 2.5.3 DoD 验证）。

验证：
1. flet_test_page fixture 可用（启动 Flet app 并返回 page）
2. wait_for_render 正常路径：page.add 后 wait_for_render 返回
3. wait_for_render 超时路径：expected_controls 不可达时抛 TimeoutError

``no_db`` marker: 跳过 ``db_schema_ready`` autouse fixture 的 DB 初始化，
让 probe 测试在 DB 不可用环境也能运行（方案 §3.3.3 DoD）。

运行环境限制（双重 skip）：

1. **Windows skip**: ``ft.run_async`` 的 socket server 不兼容
   ``WindowsSelectorEventLoop``（抛 ``NotImplementedError``），而 pytest-asyncio
   在 Windows 强制 selector policy（``tests/conftest.py`` L25）。

2. **Headless Linux skip**: ``ft.run_async`` 内部调用 ``is_linux_server()``
   检测 ``DISPLAY`` 环境变量——CI ubuntu-latest headless 环境下返回 True，
   强制 ``view=AppView.WEB_BROWSER``（flet app.py L188-190），启动 web server
   等待浏览器连接。无浏览器时 main 回调永不触发，fixture 挂起 120s 超时失败。

   因此 probe 测试在 CI headless Linux 下也 skip。本地 Linux 有 X server
   或用 ``xvfb-run`` 时 ``DISPLAY`` 已设置，可正常运行。本地 Windows 验证
   请用独立 spike：``python -m tests.integration._spike_flet_run_async``。

技术债：CI 完整验证 flet_test_page 需装 ``xvfb`` + ``flet_desktop``（见
``phase-2.5-review.md`` 限制章节）。
"""

import os
import sys

import flet as ft
import pytest

# Headless Linux 判定（与 flet.utils.is_linux_server 一致：Linux + 非 WSL + DISPLAY 未设置）
# CI ubuntu-latest 符合此条件；本地 Linux 有 X server 时 DISPLAY 已设置
_IS_HEADLESS_LINUX = sys.platform == "linux" and not os.environ.get("DISPLAY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.no_db,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="ft.run_async socket server 不兼容 WindowsSelectorEventLoop（pytest-asyncio 强制 selector policy）",
    ),
    pytest.mark.skipif(
        _IS_HEADLESS_LINUX,
        reason="ft.run_async 在 headless Linux 被 is_linux_server() 强制切到 WEB_BROWSER，无浏览器则 main 不触发；需 xvfb + flet_desktop（技术债）",
    ),
]


async def test_flet_test_page_available(flet_test_page):
    """DoD 1: flet_test_page fixture 可用。"""
    assert flet_test_page is not None
    assert isinstance(flet_test_page.page, ft.Page)


async def test_wait_for_render_returns_on_control_added(flet_test_page):
    """DoD 2: wait_for_render 正常路径——page.add 后返回。"""
    page = flet_test_page.page
    initial = len(page.controls)
    page.add(ft.Text("probe"))
    flet_test_page.wait_for_render(timeout=2.0, expected_controls=initial + 1)
    assert len(page.controls) == initial + 1


async def test_wait_for_render_raises_timeout(flet_test_page):
    """DoD 3: wait_for_render 超时抛 TimeoutError。"""
    page = flet_test_page.page
    # 不 add 任何控件，期望 controls 数量永远不变
    unreachable_target = len(page.controls) + 100
    with pytest.raises(TimeoutError):
        flet_test_page.wait_for_render(timeout=0.3, expected_controls=unreachable_target)


# ============================================================================
# Phase 3.0.1 扩展：wait_for_condition / find_control
# ============================================================================


async def test_wait_for_condition_returns_when_true(flet_test_page):
    """DoD 4: wait_for_condition 正常路径——predicate 返回 True 时立即返回。"""
    page = flet_test_page.page
    page.add(ft.Text("condition-probe"))
    # predicate 检查控件内容已渲染
    flet_test_page.wait_for_condition(
        predicate=lambda: any(getattr(c, "value", None) == "condition-probe" for c in page.controls),
        timeout=2.0,
    )


async def test_wait_for_condition_raises_timeout(flet_test_page):
    """DoD 5: wait_for_condition 超时抛 TimeoutError。"""
    # predicate 永远返回 False
    with pytest.raises(TimeoutError):
        flet_test_page.wait_for_condition(predicate=lambda: False, timeout=0.3)


async def test_find_control_returns_matching_control(flet_test_page):
    """DoD 6: find_control 深度优先查找满足谓词的控件。"""
    page = flet_test_page.page
    target_text = ft.Text("find-me", key="target")
    container = ft.Container(content=ft.Column([ft.Text("other"), target_text]))
    page.add(container)
    # 深度优先：page → Container → Column → Text("other") / Text("find-me")
    found = flet_test_page.find_control(
        predicate=lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "find-me"
    )
    assert found is target_text


async def test_find_control_returns_none_when_not_found(flet_test_page):
    """DoD 7: find_control 未找到时返回 None。"""
    page = flet_test_page.page
    page.add(ft.Text("exists"))
    found = flet_test_page.find_control(
        predicate=lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "nonexistent"
    )
    assert found is None
