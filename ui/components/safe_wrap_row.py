"""safe_wrap_row — wrap=True 安全容器 (UIX-13 C5: PR373 视口塌陷防护).

背景: Flutter ``Wrap`` 主轴无固定长度, 子控件 ``expand=True`` (flex) 在主轴空间
不足时被压缩为 0 → 控件不可见/视口塌陷 (PR373 回归根因)。flet 0.86.5 无控件级
布局后尺寸回调 (无 Container.on_resize), 无法在运行时测量实际宽高, 故在结构层
拦截: ``SafeWrapRow`` 构造时递归校验子控件树, 发现 ``expand=True`` 立即抛
``ValueError`` (显式异常而非 assert, 避免 -O 优化跳过护栏)。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import flet as ft

__all__ = ["SafeWrapRow"]


def _iter_children(control: ft.Control) -> Iterable[ft.Control]:
    """遍历 flet 控件的直接子控件 (覆盖 controls 列表与 content 单控件)。"""
    controls = getattr(control, "controls", None)
    if isinstance(controls, list):
        yield from controls
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield content


def _assert_no_expand_children(controls: list[ft.Control]) -> None:
    """递归断言控件树中无 ``expand=True`` (wrap 容器内禁 expand, PR373 防护)。

    :raises ValueError: 发现任一子控件 ``expand`` 为真值。
    """
    for ctrl in controls:
        if ctrl is None:
            continue
        if getattr(ctrl, "expand", None):
            raise ValueError(
                f"SafeWrapRow 子控件 {type(ctrl).__name__} expand=True 与 wrap=True 冲突"
                " (PR373 视口塌陷防护): 请移除 expand 或改用非 wrap 布局"
            )
        _assert_no_expand_children(list(_iter_children(ctrl)))


class SafeWrapRow(ft.Row):
    """wrap=True 安全容器: 内部禁子控件 ``expand=True``。

    用法与 ``ft.Row(wrap=True)`` 一致, 仅多一层结构护栏:
    构造时强制 ``wrap=True`` 并递归校验子控件树, 违反即抛 ``ValueError``。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        wrap = kwargs.get("wrap", True)
        if wrap is not True:
            raise ValueError(f"SafeWrapRow 强制 wrap=True, 收到 wrap={wrap!r} (PR373 视口塌陷防护)")
        kwargs["wrap"] = True
        super().__init__(*args, **kwargs)
        _assert_no_expand_children(self.controls)
