"""FletTestPage helper 单元测试（Phase 3.0.1）。

集成测试（test_flet_test_page_probe.py）依赖 ``ft.run_async``，Windows/headless Linux
会 skip。本文件用 mock page 单元测试 ``wait_for_condition`` / ``trigger_state_change``
/ ``find_control`` 的纯逻辑，确保 helper 在所有平台可验证。

策略：
- 构造 ``MagicMock(spec=ft.Page)`` 模拟 page（``page.controls`` 为可变 list）
- 直接实例化 ``FletTestPage(page=mock)`` 测试 helper 方法
- ``find_control`` 用真实 ``ft.Control`` 子类（不依赖 Renderer）验证深度优先遍历
"""

from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.integration.conftest import FletTestPage, _find_control_recursive

pytestmark = pytest.mark.unit


def _make_mock_page() -> MagicMock:
    """构造 ``page.controls`` 为空 list 的 mock page。"""
    page = MagicMock(spec=ft.Page)
    page.controls = []
    return page


class TestWaitForCondition:
    """wait_for_condition 单元测试。"""

    def test_returns_immediately_when_predicate_true(self):
        page = _make_mock_page()
        ftp = FletTestPage(page=page)
        # predicate 立即返回 True，应不阻塞
        ftp.wait_for_condition(predicate=lambda: True, timeout=1.0)
        # 无异常即通过

    def test_returns_after_predicate_becomes_true(self):
        page = _make_mock_page()
        ftp = FletTestPage(page=page)
        call_count = [0]

        def predicate() -> bool:
            call_count[0] += 1
            # 第 3 次调用后返回 True（模拟异步 state 变更完成）
            return call_count[0] >= 3

        ftp.wait_for_condition(predicate=predicate, timeout=2.0, interval=0.01)
        assert call_count[0] >= 3

    def test_raises_timeout_when_predicate_never_true(self):
        page = _make_mock_page()
        ftp = FletTestPage(page=page)
        with pytest.raises(TimeoutError):
            ftp.wait_for_condition(predicate=lambda: False, timeout=0.2, interval=0.05)

    def test_swallows_predicate_exception_and_continues(self):
        """predicate 内部抛异常时继续轮询（模拟渲染未完成时访问属性）。"""
        page = _make_mock_page()
        ftp = FletTestPage(page=page)
        call_count = [0]

        def predicate() -> bool:
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("rendering not complete")
            return True

        ftp.wait_for_condition(predicate=predicate, timeout=2.0, interval=0.01)
        assert call_count[0] >= 2


class TestFindControl:
    """find_control 单元测试。"""

    def test_returns_matching_top_level_control(self):
        page = _make_mock_page()
        target = ft.Text("target")
        page.controls = [ft.Text("other"), target, ft.Container()]
        ftp = FletTestPage(page=page)
        found = ftp.find_control(predicate=lambda c: isinstance(c, ft.Text) and c.value == "target")
        assert found is target

    def test_returns_nested_control_via_content(self):
        page = _make_mock_page()
        target = ft.Text("nested")
        container = ft.Container(content=ft.Column([ft.Text("sibling"), target]))
        page.controls = [container]
        ftp = FletTestPage(page=page)
        found = ftp.find_control(predicate=lambda c: isinstance(c, ft.Text) and c.value == "nested")
        assert found is target

    def test_returns_nested_control_via_controls_list(self):
        page = _make_mock_page()
        target = ft.Text("in-list")
        column = ft.Column(controls=[ft.Text("first"), target])
        page.controls = [column]
        ftp = FletTestPage(page=page)
        found = ftp.find_control(predicate=lambda c: isinstance(c, ft.Text) and c.value == "in-list")
        assert found is target

    def test_returns_none_when_no_match(self):
        page = _make_mock_page()
        page.controls = [ft.Text("exists")]
        ftp = FletTestPage(page=page)
        found = ftp.find_control(predicate=lambda c: isinstance(c, ft.Text) and c.value == "nonexistent")
        assert found is None

    def test_returns_first_match_in_dfs_order(self):
        page = _make_mock_page()
        first = ft.Text("dup")
        second = ft.Text("dup")
        page.controls = [first, second]
        ftp = FletTestPage(page=page)
        found = ftp.find_control(predicate=lambda c: isinstance(c, ft.Text) and c.value == "dup")
        assert found is first  # 深度优先返回第一个匹配

    def test_predicate_exception_does_not_abort_search(self):
        """predicate 抛异常时跳过该控件继续搜索。"""
        page = _make_mock_page()
        target = ft.Text("safe")
        bad_control = ft.Text("bad")
        page.controls = [bad_control, target]
        ftp = FletTestPage(page=page)

        def predicate(c: ft.BaseControl) -> bool:
            if c is bad_control:
                raise RuntimeError("bad control")
            return isinstance(c, ft.Text) and c.value == "safe"

        found = ftp.find_control(predicate=predicate)
        assert found is target


class TestFindControlRecursive:
    """_find_control_recursive 模块级函数单元测试。"""

    def test_empty_list_returns_none(self):
        assert _find_control_recursive([], lambda _: True) is None

    def test_handles_control_without_content_or_controls(self):
        """无 content/controls 属性的控件不应抛异常。"""
        control = ft.Text("leaf")
        result = _find_control_recursive([control], lambda c: isinstance(c, ft.Text) and c.value == "leaf")
        assert result is control

    def test_handles_none_content_gracefully(self):
        """content 为 None 时不应抛异常。"""
        container = ft.Container(content=None)
        result = _find_control_recursive([container], lambda _: False)
        assert result is None
