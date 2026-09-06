"""ui/components/safe_wrap_row.py 单元测试 (UIX-13 C5: PR373 视口塌陷防护).

SafeWrapRow 是普通 flet 控件包装类 (非 @ft.component 函数组件), 可直接实例化
验证结构护栏: 强制 wrap=True + 递归禁子控件 expand=True。
"""

import flet as ft
import pytest

from ui.components.safe_wrap_row import SafeWrapRow, _assert_no_expand_children, _iter_children

pytestmark = pytest.mark.unit


class TestSafeWrapRowContract:
    """SafeWrapRow 结构护栏行为。"""

    def test_forces_wrap_true(self) -> None:
        """SafeWrapRow 构造后 wrap 恒为 True (默认即 True)。"""
        row = SafeWrapRow(controls=[ft.Text("a")], spacing=10)
        assert row.wrap is True

    def test_rejects_explicit_wrap_false(self) -> None:
        """显式传 wrap=False 抛 ValueError (SafeWrapRow 语义即 wrap 容器)。"""
        with pytest.raises(ValueError, match="强制 wrap=True"):
            SafeWrapRow(controls=[ft.Text("a")], wrap=False)

    def test_accepts_leaf_children(self) -> None:
        """叶子控件 (Text/Button 等无 expand) 正常构造。"""
        row = SafeWrapRow(controls=[ft.Text("a"), ft.IconButton(icon=ft.Icons.ADD)], spacing=5)
        assert len(row.controls) == 2

    def test_rejects_direct_expand_child(self) -> None:
        """直接子控件 expand=True 抛 ValueError (PR373 塌陷根因)。"""
        with pytest.raises(ValueError, match="expand=True 与 wrap=True 冲突"):
            SafeWrapRow(controls=[ft.Text("a", expand=True)])

    def test_rejects_nested_expand_child(self) -> None:
        """嵌套子控件树 (Row > Column > Text expand) 同样拦截 (递归校验)。"""
        nested = ft.Column(controls=[ft.Text("nested", expand=True)])
        with pytest.raises(ValueError, match="expand=True 与 wrap=True 冲突"):
            SafeWrapRow(controls=[nested])

    def test_rejects_expand_in_content(self) -> None:
        """Container.content 中 expand=True 拦截 (content 递归路径)。"""
        box = ft.Container(content=ft.Text("box", expand=True))
        with pytest.raises(ValueError, match="expand=True 与 wrap=True 冲突"):
            SafeWrapRow(controls=[box])


class TestIterChildren:
    """_iter_children 遍历 controls 列表与 content 单控件。"""

    def test_iter_controls_list(self) -> None:
        col = ft.Column(controls=[ft.Text("a"), ft.Text("b")])
        children = list(_iter_children(col))
        assert len(children) == 2

    def test_iter_content_single(self) -> None:
        box = ft.Container(content=ft.Text("x"))
        children = list(_iter_children(box))
        assert len(children) == 1

    def test_leaf_has_no_children(self) -> None:
        assert list(_iter_children(ft.Text("leaf"))) == []


class TestAssertNoExpandChildren:
    """_assert_no_expand_children 纯函数护栏。"""

    def test_ok_when_no_expand(self) -> None:
        _assert_no_expand_children([ft.Text("a"), ft.Column(controls=[ft.Text("b")])])

    def test_raise_on_expand(self) -> None:
        with pytest.raises(ValueError, match="expand=True 与 wrap=True 冲突"):
            _assert_no_expand_children([ft.Text("a", expand=True)])

    def test_ignores_none(self) -> None:
        _assert_no_expand_children([None])  # safe_controls 可能产出 None 占位
