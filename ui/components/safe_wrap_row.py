"""safe_wrap_row — wrap=True 安全容器 (UIX-13 C5: 视口防塌陷护栏).

背景: Flutter ``Wrap`` 主轴在无界约束下不给子控件提供固定主轴长度，直系子控件或无界
子树若设置 ``expand=True`` (flex) 会与 Wrap 的非 Flex 布局模型冲突或产生 ParentData 错误。
flet 0.86.5 无控件级布局后尺寸回调 (无 Container.on_resize)，故在结构层做显式护栏:
``SafeWrapRow`` 构造时强制 ``wrap=True``，并校验直系子控件树，拦截无界 ``expand=True``
(显式抛出 ``ValueError``，避免 -O 优化跳过)。对已显式指定 width/height 的有界容器内部
局部 flex 布局予以放行。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
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


def _assert_no_expand_children(controls: Sequence[ft.Control | None] | None) -> None:
    """递归断言控件树中无非法 ``expand=True`` (wrap 容器内禁无界 expand 防护)。

    若子控件为已显式声明 width 与 height 的有界 Container，其内部 flex 属局部安全排布，
    不向下递归；对未限定尺寸的容器或直接子控件中的 expand=True 抛出异常。

    :raises ValueError: 发现直接或无界子控件 ``expand`` 为真值。
    """
    if not controls:
        return
    for ctrl in controls:
        if ctrl is None:
            continue
        if getattr(ctrl, "expand", None):
            raise ValueError(
                f"SafeWrapRow 子控件 {type(ctrl).__name__} expand=True 与 wrap=True 冲突"
                " (视口塌陷防护): 请移除 expand 或改用非 wrap 布局"
            )
        # 有界容器（显式 width/height）内部 flex 约束明确，不会影响外层 Wrap
        if (
            isinstance(ctrl, ft.Container)
            and getattr(ctrl, "width", None) is not None
            and getattr(ctrl, "height", None) is not None
        ):
            continue
        _assert_no_expand_children(list(_iter_children(ctrl)))


class SafeWrapRow(ft.Row):
    """wrap=True 安全容器: 内部禁子控件 ``expand=True``。

    用法与 ``ft.Row(wrap=True)`` 一致, 仅多一层结构护栏:
    构造时强制 ``wrap=True`` 并递归校验子控件树, 违反即抛 ``ValueError``。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        wrap = kwargs.get("wrap", True)
        if wrap is not True:
            raise ValueError(f"SafeWrapRow 强制 wrap=True, 收到 wrap={wrap!r} (视口塌陷防护)")
        kwargs["wrap"] = True
        super().__init__(*args, **kwargs)
        _assert_no_expand_children(self.controls)
