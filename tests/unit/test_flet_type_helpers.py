"""ui/components/flet_type_helpers.py 单元测试.

覆盖所有 helper 函数的 None 路径与非 None 路径, 满足 per-file 80% 覆盖率门禁.
helper 函数本身是 Flet V1 类型边界的 typing.cast 封装 (详见模块 NOTE(lazy)),
测试重点验证: ① None 输入透传 ② 非 None 输入原样返回 (cast 仅类型层效果, 运行时无副作用).
"""

from collections.abc import Callable

import flet as ft
import pytest

from ui.components.flet_type_helpers import (
    get_control_attr,
    get_control_value,
    safe_controls,
    safe_icon,
    safe_icon_str,
    safe_on_change,
    safe_on_click,
    safe_on_dismiss,
    safe_on_focus,
    safe_on_hover,
    safe_on_select,
)

pytestmark = pytest.mark.unit


def _handler(_: ft.ControlEvent) -> None:
    """测试用的 ControlEvent handler."""
    return None


# safe_on_* 系列函数共享同一行为模式, 用 parametrize 一次覆盖 6 个函数.
_SAFE_WRAPPERS: list[tuple[str, Callable]] = [
    ("safe_on_click", safe_on_click),
    ("safe_on_change", safe_on_change),
    ("safe_on_select", safe_on_select),
    ("safe_on_dismiss", safe_on_dismiss),
    ("safe_on_focus", safe_on_focus),
    ("safe_on_hover", safe_on_hover),
]


@pytest.mark.parametrize("name,wrapper", _SAFE_WRAPPERS, ids=[n for n, _ in _SAFE_WRAPPERS])
class TestSafeOnEventWrappers:
    """safe_on_* 系列: None 透传 + 非 None 原样返回."""

    def test_none_returns_none(self, name: str, wrapper: Callable) -> None:
        """None handler → 返回 None (避免 None 被 cast 为非 None 假象)."""
        assert wrapper(None) is None

    def test_handler_returns_same_callable(self, name: str, wrapper: Callable) -> None:
        """非 None handler → 返回值与输入是同一对象 (cast 仅类型层, 运行时无变化)."""
        result = wrapper(_handler)
        assert result is _handler


class TestSafeControls:
    """safe_controls: Sequence → list 转换."""

    def test_converts_tuple_to_list(self) -> None:
        """tuple[Control, ...] → list[Control] (Flet controls 参数要求 list)."""
        ctrl = ft.Container()
        result = safe_controls((ctrl,))
        assert isinstance(result, list)
        assert result == [ctrl]

    def test_empty_sequence_returns_empty_list(self) -> None:
        """空序列 → 空 list (非 None)."""
        result = safe_controls([])
        assert result == []
        assert isinstance(result, list)

    def test_does_not_mutate_input(self) -> None:
        """输入序列不被修改 (浅拷贝语义)."""
        ctrl = ft.Container()
        src = [ctrl]
        result = safe_controls(src)
        assert result is not src
        assert result == src


class TestSafeIcon:
    """safe_icon: str | IconData → IconData."""

    def test_icon_data_passthrough(self) -> None:
        """ft.Icons.XXX (IconData 枚举) → 原样返回."""
        result = safe_icon(ft.Icons.PLAY_ARROW)
        assert result == ft.Icons.PLAY_ARROW

    def test_str_passthrough(self) -> None:
        """str → cast 为 IconData (运行时原样返回 str)."""
        result = safe_icon("play_arrow")
        assert result == "play_arrow"


class TestSafeIconStr:
    """safe_icon_str: str | IconData → str."""

    def test_str_passthrough(self) -> None:
        """str → 原样返回."""
        result = safe_icon_str("play_arrow")
        assert result == "play_arrow"

    def test_icon_data_cast_to_str(self) -> None:
        """IconData → cast 为 str (运行时原样返回 IconData 枚举值)."""
        result = safe_icon_str(ft.Icons.PLAY_ARROW)
        assert result == ft.Icons.PLAY_ARROW


class TestGetControlValue:
    """get_control_value: 安全访问 control.value (直接 .value, 不带默认)."""

    def test_returns_value_attribute(self) -> None:
        """访问 TextField.value (expected_type 仅文档用, 运行时不检查)."""
        tf = ft.TextField(value="hello")
        result = get_control_value(tf, ft.TextField)
        assert result == "hello"

    def test_raises_attribute_error_when_no_value(self) -> None:
        """控件无 value 属性 → 抛 AttributeError (直接 .value 访问, 非 getattr 安全访问).

        语义说明: helper 不做容错, 调用方需确保控件类型有 value 属性
        (符合 Flet ControlEvent.control.value 的典型用法约定).
        """
        container = ft.Container()
        with pytest.raises(AttributeError):
            get_control_value(container, ft.Container)


class TestGetControlAttr:
    """get_control_attr: 安全访问任意属性 (getattr 不带默认)."""

    def test_returns_selected_attribute(self) -> None:
        """访问 SegmentedButton.selected."""
        seg = ft.SegmentedButton(segments=[], selected=["a"])
        result = get_control_attr(seg, ft.SegmentedButton, "selected")
        assert result == ["a"]

    def test_raises_attribute_error_when_attr_missing(self) -> None:
        """属性不存在 → 抛 AttributeError (getattr 不带默认值).

        语义说明: helper 不做容错, 调用方需确保属性存在
        (符合 Flet ControlEvent.control.<attr> 的典型用法约定).
        """
        container = ft.Container()
        with pytest.raises(AttributeError):
            get_control_attr(container, ft.Container, "nonexistent_attr")
