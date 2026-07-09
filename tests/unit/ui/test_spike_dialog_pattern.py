"""Spike: Dialog 声明式模式单元测试（Phase 3.0.2）。

纯逻辑验证（不依赖 ``ft.run_async``，Windows 可运行）：
- grep 守护：spike 组件不使用命令式 Dialog API
- API 验证：``ft.use_dialog`` 是 Flet 0.85.3 官方 hook

渲染行为验证在 ``tests/integration/test_spike_dialog_declarative.py``（Windows skip）。
"""

import inspect

import flet as ft
import pytest

from tests.integration.test_spike_dialog_declarative import _spike_dialog_view

pytestmark = pytest.mark.unit


def test_spike_dialog_no_imperative_api():
    """DoD 4: grep 守护——spike 组件不使用 page.show_dialog / page.pop_dialog。"""
    source = inspect.getsource(_spike_dialog_view)
    assert "page.show_dialog" not in source, "spike 不应使用 page.show_dialog（命令式 API）"
    assert "page.pop_dialog" not in source, "spike 不应使用 page.pop_dialog（命令式 API）"
    assert "ft.use_dialog" in source, "spike 必须用 ft.use_dialog（声明式 API）"
    assert "use_state" in source, "spike 必须用 use_state 驱动 Dialog 显隐"


def test_spike_dialog_uses_ft_use_dialog_api():
    """DoD 5: 验证 ft.use_dialog 是 Flet 0.85.3 官方 API（非自定义垫片）。"""
    assert hasattr(ft, "use_dialog"), "ft.use_dialog 必须存在（Flet 0.85.3 官方 hook）"
    sig = inspect.signature(ft.use_dialog)
    assert "dialog" in sig.parameters, "ft.use_dialog 必须接受 dialog 参数"


def test_spike_dialog_view_is_ft_component():
    """DoD 6: spike 组件必须被 @ft.component 装饰。"""
    assert hasattr(_spike_dialog_view, "__wrapped__"), "spike 必须用 @ft.component 装饰"
