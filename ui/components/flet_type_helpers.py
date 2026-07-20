"""Flet V1 类型边界 helper 函数。

Flet V1 的 ``Event[T]`` 是不变（invariant）的，且 ``_BaseControlType`` 不暴露子类属性，
导致大量类型告警。本模块集中封装类型边界处理，调用点使用 helper 替代直接传值，
保持类型严格度的同时避免分散的 ``type: ignore``。

NOTE(lazy): Flet V1 类型系统限制. ceiling: Flet V2 升级. upgrade: 升级 Flet 后评估是否可移除.
"""

from __future__ import annotations

import typing
from collections.abc import Callable, Sequence

import flet as ft

__all__ = [
    "safe_on_click",
    "safe_on_change",
    "safe_on_select",
    "safe_on_dismiss",
    "safe_on_focus",
    "safe_on_hover",
    "safe_controls",
    "safe_icon",
    "safe_icon_str",
    "get_control_value",
    "get_control_attr",
]


def safe_on_click(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_click`` parameter.

    Flet V1 ``Event[T]`` 是不变的，``Callable[[ControlEvent], None]`` 不能直接赋给
    ``ControlEventHandler[TextButton]`` 等。使用本 helper 统一处理类型边界。
    """
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_on_change(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_change`` parameter."""
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_on_select(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_select`` parameter."""
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_on_dismiss(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_dismiss`` parameter."""
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_on_focus(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_focus`` parameter."""
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_on_hover(
    handler: Callable[[ft.ControlEvent], None] | None,
) -> ft.ControlEventHandler | None:
    """Wrap ``ControlEvent`` handler for Flet ``on_hover`` parameter."""
    if handler is None:
        return None
    return typing.cast(ft.ControlEventHandler, handler)


def safe_controls(items: Sequence[ft.Control]) -> list[ft.Control]:
    """Convert ``Sequence[Control]`` to ``list[Control]`` for Flet ``controls`` parameter.

    ``list`` 是不变的，``list[Container]`` 不能赋给 ``list[Control]``。
    使用本 helper 显式转换。本函数仅做浅拷贝（``list(items)``），开销可忽略。
    """
    return list(items)


def safe_icon(name: str | ft.IconData) -> ft.IconData:
    """Convert ``str`` or ``IconData`` to ``IconData`` for Flet ``icon`` parameter.

    Flet V1 中 ``ft.Icons.XXX`` 是 ``IconData``（``IntEnum`` 子类），但部分 API
    历史上接受 ``str``。本 helper 统一处理双向赋值。
    """
    return typing.cast(ft.IconData, name)


def safe_icon_str(name: str | ft.IconData) -> str:
    """Convert ``str`` or ``IconData`` to ``str`` for legacy str-typed icon parameters."""
    return typing.cast(str, name)


def get_control_value(control: ft.BaseControl, expected_type: type[ft.Control]) -> typing.Any:
    """Safely access ``.value`` on a Flet control with type narrowing.

    Flet ``ControlEvent.control`` 类型为 ``_BaseControlType``（即 ``BaseControl``），
    不暴露 ``value`` 属性。使用本 helper 显式 ``cast`` 后访问。

    Args:
        control: 原始 Flet 控件（通常是 ``e.control``）。
        expected_type: 期望的具体控件类型（如 ``ft.TextButton``、``ft.DatePicker``）。
            仅用于类型文档，运行时不参与检查。
    """
    # expected_type 是运行时变量，pyright 不允许在 cast 类型位置使用，
    # 改为 cast(Any, ...) 后访问 .value，由调用方负责语义正确性。
    _ = expected_type  # 仅用于 IDE 提示与文档，运行时未使用
    return typing.cast(typing.Any, control).value


def get_control_attr(control: ft.BaseControl, expected_type: type[ft.Control], attr: str) -> typing.Any:
    """Safely access any attribute on a Flet control with type narrowing.

    Args:
        control: 原始 Flet 控件（通常是 ``e.control``）。
        expected_type: 期望的具体控件类型（仅用于文档）。
        attr: 要访问的属性名（如 ``value``、``selected``、``selected_index``）。
    """
    _ = expected_type  # 仅用于 IDE 提示与文档，运行时未使用
    return getattr(typing.cast(typing.Any, control), attr)
