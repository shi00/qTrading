"""Spike: Dialog 声明式模式验证（Phase 3.0.2）。

验证目标：
1. ``ft.use_dialog(dialog)`` hook 是 Flet 0.85.3 官方声明式 Dialog API
2. state 驱动：``dialog = ft.AlertDialog(...) if state.show else None``
3. 无需 ``page.show_dialog()`` / ``page.pop_dialog()``（命令式 API）

本 spike 用最小 ``@ft.component`` 验证模式可行，为后续 Phase 3.2.7/3.4.3/4.3 的
Dialog 重写（StockDetailDialog/HealthReportDialog/HealthScanDialog/ProviderCredentialDialog）
确立标准范式。

注意：Windows/headless Linux 会 skip（依赖 ``ft.run_async``），但 spike 文件本身
作为活文档（living documentation）记录声明式 Dialog 模式。
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import os
import sys
from unittest.mock import MagicMock

import flet as ft
import pytest

_IS_HEADLESS_LINUX = sys.platform == "linux" and not os.environ.get("DISPLAY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32" or _IS_HEADLESS_LINUX,
        reason="Spike 需 ft.run_async 渲染声明式组件，Windows/headless Linux 不支持",
    ),
]


@ft.component
def _spike_dialog_view() -> ft.Control:
    """最小声明式 Dialog 组件：use_state 控制 dialog 显示/隐藏。

    范式：
    - ``use_state(show)`` 驱动 Dialog 显隐
    - ``ft.use_dialog(dialog)`` 自动挂载/卸载到 page overlay
    - ``dialog = ft.AlertDialog(...) if show else None``
    - 事件处理器调 ``set_show(False)`` 关闭（state 驱动，非命令式 pop API）
    """
    show, set_show = ft.use_state(False)

    def _open_dialog(_e: ft.ControlEvent) -> None:
        set_show(True)

    def _close_dialog(_e: ft.ControlEvent) -> None:
        set_show(False)

    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text("spike-title"),
            content=ft.Text("spike-content"),
            actions=[
                ft.TextButton("close", on_click=_close_dialog),
            ],
        )
        if show
        else None
    )
    ft.use_dialog(dialog)

    return ft.Column(
        [
            ft.Text("spike-host"),
            ft.ElevatedButton("open", on_click=_open_dialog),
        ]
    )


async def test_spike_dialog_renders_host_without_dialog(flet_test_page):
    """DoD 1: 初始渲染（show=False）时 host 可见，无 Dialog 挂载。"""
    page = flet_test_page.page
    page.add(_spike_dialog_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "spike-host")
            is not None
        ),
        timeout=2.0,
    )
    # 初始 show=False，dialog=None，page._dialogs.controls 应为空
    assert len(page._dialogs.controls) == 0


async def test_spike_dialog_opens_on_button_click(flet_test_page):
    """DoD 2: 点击 open 按钮后 Dialog 挂载到 page._dialogs.controls，open=True。"""
    page = flet_test_page.page
    page.add(_spike_dialog_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "open"
            )
            is not None
        ),
        timeout=2.0,
    )

    # 查找 open 按钮并触发 click（用 MagicMock 模拟事件，避免 ControlEvent 签名问题）
    open_btn = flet_test_page.find_control(
        lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "open"
    )
    assert open_btn is not None
    mock_event = MagicMock()
    open_btn.on_click(mock_event)

    # 等待 state 变更触发重渲染，Dialog 挂载
    flet_test_page.wait_for_condition(
        lambda: len(page._dialogs.controls) > 0 and page._dialogs.controls[0].open is True,
        timeout=2.0,
    )
    assert page._dialogs.controls[0].title.value == "spike-title"


async def test_spike_dialog_closes_on_button_click(flet_test_page):
    """DoD 3: 点击 close 按钮后 Dialog 从 page._dialogs.controls 移除（state 驱动，非 page.pop_dialog）。"""
    page = flet_test_page.page
    page.add(_spike_dialog_view())

    # 先打开 Dialog
    open_btn = flet_test_page.find_control(
        lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "open"
    )
    flet_test_page.wait_for_condition(lambda: open_btn is not None, timeout=2.0)
    open_btn.on_click(MagicMock())
    flet_test_page.wait_for_condition(lambda: len(page._dialogs.controls) > 0, timeout=2.0)

    # 查找 close 按钮并触发 click
    close_btn = flet_test_page.find_control(
        lambda c: isinstance(c, ft.TextButton) and getattr(c, "text", None) == "close"
    )
    assert close_btn is not None
    close_btn.on_click(MagicMock())

    # 等待 state 变更触发重渲染，Dialog 移除
    flet_test_page.wait_for_condition(lambda: len(page._dialogs.controls) == 0, timeout=2.0)
