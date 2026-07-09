"""flet_test_page fixture 契约探针（Task 2.5.3 DoD 验证）。

验证：
1. flet_test_page fixture 可用（启动 Flet app 并返回 page）
2. wait_for_render 正常路径：page.add 后 wait_for_render 返回
3. wait_for_render 超时路径：expected_controls 不可达时抛 TimeoutError

``no_db`` marker: 跳过 ``db_schema_ready`` autouse fixture 的 DB 初始化，
让 probe 测试在 DB 不可用环境也能运行（方案 §3.3.3 DoD）。

Windows skip: ``ft.run_async`` 的 socket server 不兼容 ``WindowsSelectorEventLoop``
（抛 ``NotImplementedError``），而 pytest-asyncio 在 Windows 强制 selector policy
（``tests/conftest.py`` L25）。CI 集成测试在 Linux（``ubuntu-latest``）运行，
用 ``DefaultEventLoopPolicy`` 不受影响。本地 Windows 验证请用独立 spike：
``python -m tests.integration._spike_flet_run_async``。
"""

import sys

import flet as ft
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.no_db,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="ft.run_async socket server 不兼容 WindowsSelectorEventLoop；CI Linux 验证",
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
