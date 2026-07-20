"""ResizableSplitter 组件体测试 — 通过 component_renderer 驱动 @ft.component 执行。

补充 test_resizable_splitter.py 仅覆盖纯函数的不足，验证组件体 (lines 94-208) 的
渲染结构 + 事件 handler 行为。配套 conftest.py 的 ``mock_app_colors_state`` /
``mock_i18n_state`` 注入 Observable state，``_v1_page_compat`` 让 ``control.page``
可注入。
"""

# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.resizable_splitter import ResizableSplitter

pytestmark = pytest.mark.unit

DEFAULT_WIDTH = 360
MIN_WIDTH = 280
MAX_WIDTH = 600
CONFIG_KEY = "test_panel_width"


def _make_splitter(
    default_width: int = DEFAULT_WIDTH,
    min_width: int = MIN_WIDTH,
    max_width: int = MAX_WIDTH,
    collapsible: bool = False,
    collapsed: bool = False,
    on_resize=None,
    on_load_width=None,
    on_persist_width=None,
    drag_interval: int = 16,
):
    """构造一个 ResizableSplitter Component 实例（含 mount effects 已运行）。

    P1-1/P2-1: ``on_load_width`` / ``on_persist_width`` 经回调上抛父 VM,
    默认 None 时 _load_effect / _persist 直接 return (不触发任何 IO)。
    """
    return make_component(
        ResizableSplitter,
        left_content=ft.Container(width=100),
        right_content=ft.Container(width=100),
        config_key=CONFIG_KEY,
        default_width=default_width,
        min_width=min_width,
        max_width=max_width,
        collapsible=collapsible,
        collapsed=collapsed,
        on_resize=on_resize,
        on_load_width=on_load_width,
        on_persist_width=on_persist_width,
        drag_interval=drag_interval,
    )


def _render(component):
    """驱动 mount effects + 渲染一次，返回 (page, result)。

    返回 page 以便测试断言 ``page.session.scheduled_updates``。
    """
    page = run_mount_effects(component)
    return page, render_once(component)


def _find_divider(container: ft.Container) -> ft.GestureDetector:
    """从渲染结果中找到 divider (GestureDetector)。"""
    row = container.content
    assert isinstance(row, ft.Row)
    # 顺序: [left_container, divider, right_container]
    divider = row.controls[1]
    assert isinstance(divider, ft.GestureDetector)
    return divider


def _trigger_callback(cb, event):
    """Safely trigger Flet optional callback in tests.

    Flet stubs declare callbacks (on_click/on_change/on_horizontal_drag_*/etc.)
    as Optional[Callable[[], None]], but runtime passes a ControlEvent.
    Centralize type narrowing + type: ignore here.
    """
    assert cb is not None
    cb(event)  # type: ignore[reportCallIssue, reason: Flet stub declares callbacks as 0-arg, but runtime passes event]


class TestSplitterRenderStructure:
    """验证渲染后的控件树结构 (lines 94-208 中 render 段)。"""

    def test_renders_container_with_row(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter())

        assert isinstance(result, ft.Container)
        row = result.content
        assert isinstance(row, ft.Row)
        assert len(row.controls) == 3

    def test_left_container_holds_left_content(self, mock_i18n_state, mock_app_colors_state):
        """default==persisted 时 set_width 不触发，left_container.width=default_width。"""
        left = ft.Container(width=100)
        component = _make_splitter(default_width=DEFAULT_WIDTH)
        component.kwargs["left_content"] = left
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(component)

        row = result.content
        left_container = row.controls[0]
        assert isinstance(left_container, ft.Container)
        assert left_container.content is left
        assert left_container.width == DEFAULT_WIDTH

    def test_left_container_width_uses_persisted(self, mock_i18n_state, mock_app_colors_state):
        """persisted != default 时 _load_effect 调用 set_width(persisted)。"""
        component = _make_splitter(default_width=DEFAULT_WIDTH, on_load_width=lambda: 450)
        _, result = _render(component)

        row = result.content
        left_container = row.controls[0]
        # 持久化值 450 在 _load_effect 中 set_width，渲染时 width=450
        assert left_container.width == 450

    def test_right_container_expands(self, mock_i18n_state, mock_app_colors_state):
        right = ft.Container(width=100)
        component = _make_splitter()
        component.kwargs["right_content"] = right
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(component)

        row = result.content
        right_container = row.controls[2]
        assert isinstance(right_container, ft.Container)
        assert right_container.expand is True

    def test_divider_is_gesture_detector(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter())

        divider = _find_divider(result)
        # 验证 mouse_cursor 与 drag_interval
        assert divider.mouse_cursor == ft.MouseCursor.RESIZE_LEFT_RIGHT
        assert divider.drag_interval == 16

    def test_divider_visible_when_not_collapsed(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter(collapsible=True, collapsed=False))

        divider = _find_divider(result)
        assert divider.visible is True

    def test_divider_hidden_when_collapsed(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter(collapsible=True, collapsed=True))

        divider = _find_divider(result)
        assert divider.visible is False
        row = result.content
        left_container = row.controls[0]
        assert left_container.visible is False

    def test_hover_color_transparent_when_not_hovered(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter())

        divider = _find_divider(result)
        inner_container = divider.content
        assert isinstance(inner_container, ft.Container)
        assert inner_container.bgcolor == ft.Colors.TRANSPARENT

    def test_outer_container_expands(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter())

        assert result.expand is True


class TestLoadEffect:
    """验证 _load_effect (use_effect) 行为：从持久化加载宽度。"""

    def test_persisted_differs_from_default_sets_width(self, mock_i18n_state, mock_app_colors_state):
        """持久化宽度 != default_width 时 set_width 触发 schedule_update。"""
        component = _make_splitter(default_width=DEFAULT_WIDTH, on_load_width=lambda: 450)
        page, _ = _render(component)
        # set_width 调用后应调度 update
        assert component in page.session.scheduled_updates

    def test_persisted_equals_default_no_set_width(self, mock_i18n_state, mock_app_colors_state):
        """持久化宽度 == default_width 时不调用 set_width (无 schedule_update)。"""
        component = _make_splitter(default_width=DEFAULT_WIDTH, on_load_width=lambda: DEFAULT_WIDTH)
        page, _ = _render(component)
        # _load_effect 不调用 set_width，scheduled_updates 不应包含此 component
        assert component not in page.session.scheduled_updates


class TestDragHandlers:
    """验证拖拽 handler 行为 (lines 113-159)。"""

    def test_on_drag_start_no_op(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter())
        divider = _find_divider(result)
        # on_drag_start 应为可调用且不抛异常
        assert callable(divider.on_horizontal_drag_start)
        _trigger_callback(divider.on_horizontal_drag_start, MagicMock())

    def test_on_drag_update_with_primary_delta(self, mock_i18n_state, mock_app_colors_state):
        """primary_delta 路径：增量更新宽度 + 节流后 set_width。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter(drag_interval=0))
        divider = _find_divider(result)

        # 构造 DragUpdateEvent 桩
        e = MagicMock()
        e.primary_delta = 50  # +50px
        _trigger_callback(divider.on_horizontal_drag_update, e)

        # set_width 被调用 → schedule_update 被调用
        assert component_in_updates(page, divider)

    def test_on_drag_update_local_delta_fallback(self, mock_i18n_state, mock_app_colors_state):
        """primary_delta=None 时回退到 local_delta.x。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter(drag_interval=0))
        divider = _find_divider(result)

        e = MagicMock()
        e.primary_delta = None
        local_delta = MagicMock()
        local_delta.x = 30
        e.local_delta = local_delta
        # 不应抛异常
        _trigger_callback(divider.on_horizontal_drag_update, e)
        assert component_in_updates(page, divider)

    def test_on_drag_update_local_delta_none(self, mock_i18n_state, mock_app_colors_state):
        """primary_delta=None 且 local_delta=None 时 delta_x=0 → new_width==current → return。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter())
        divider = _find_divider(result)

        e = MagicMock()
        e.primary_delta = None
        e.local_delta = None
        updates_before = len(page.session.scheduled_updates)
        # 不应抛异常；delta_x=0 → new_width=current → 提前 return
        _trigger_callback(divider.on_horizontal_drag_update, e)
        assert len(page.session.scheduled_updates) == updates_before

    def test_on_drag_update_clamps_to_max(self, mock_i18n_state, mock_app_colors_state):
        """增量超过 max_width 时 clamp 到 max_width。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter(drag_interval=0))
        divider = _find_divider(result)

        e = MagicMock()
        e.primary_delta = 1000  # 远超 max_width
        # 不应抛异常
        _trigger_callback(divider.on_horizontal_drag_update, e)
        assert component_in_updates(page, divider)

    def test_on_drag_update_clamps_to_min(self, mock_i18n_state, mock_app_colors_state):
        """负增量低于 min_width 时 clamp 到 min_width。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter(drag_interval=0))
        divider = _find_divider(result)

        e = MagicMock()
        e.primary_delta = -1000  # 远低于 min_width
        _trigger_callback(divider.on_horizontal_drag_update, e)
        assert component_in_updates(page, divider)

    def test_on_drag_update_throttled(self, mock_i18n_state, mock_app_colors_state):
        """节流窗口内不重复 set_width。"""
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter(drag_interval=1000))
        divider = _find_divider(result)

        e1 = MagicMock()
        e1.primary_delta = 10
        _trigger_callback(divider.on_horizontal_drag_update, e1)
        updates_after_first = len(page.session.scheduled_updates)

        e2 = MagicMock()
        e2.primary_delta = 20
        _trigger_callback(divider.on_horizontal_drag_update, e2)
        updates_after_second = len(page.session.scheduled_updates)

        # 节流窗口内第二次不应新增调度
        assert updates_after_second == updates_after_first

    def test_on_drag_update_invokes_on_resize_callback(self, mock_i18n_state, mock_app_colors_state):
        """节流窗口外 set_width 后应调用 on_resize。"""
        on_resize = MagicMock()
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, result = _render(_make_splitter(on_resize=on_resize, drag_interval=0))
        divider = _find_divider(result)

        e = MagicMock()
        e.primary_delta = 50
        _trigger_callback(divider.on_horizontal_drag_update, e)
        on_resize.assert_called_once()

    def test_on_drag_end_with_cache_persists(self, mock_i18n_state, mock_app_colors_state):
        """拖拽结束 + cache 有值时持久化最终宽度。"""
        mock_persist = MagicMock()
        _, result = _render(_make_splitter(on_persist_width=mock_persist))
        divider = _find_divider(result)

        # 先 drag_update 设置 cache
        e_update = MagicMock()
        e_update.primary_delta = 50
        _trigger_callback(divider.on_horizontal_drag_update, e_update)

        # 再 drag_end (P1-1: 经 on_persist_width 回调上抛父 VM)
        _trigger_callback(divider.on_horizontal_drag_end, MagicMock())
        mock_persist.assert_called_once()
        # 持久化的宽度应为 410 (default 360 + 50)
        assert mock_persist.call_args.args[0] == 410

    def test_on_drag_end_without_cache_persists_current_width(self, mock_i18n_state, mock_app_colors_state):
        """拖拽结束但 cache 为 None 时持久化当前 width state。"""
        mock_persist = MagicMock()
        _, result = _render(_make_splitter(on_persist_width=mock_persist))
        divider = _find_divider(result)

        _trigger_callback(divider.on_horizontal_drag_end, MagicMock())
        mock_persist.assert_called_once_with(DEFAULT_WIDTH)

    def test_on_double_tap_resets_to_default(self, mock_i18n_state, mock_app_colors_state):
        """双击恢复 default_width 并持久化。"""
        mock_persist = MagicMock()
        _, result = _render(_make_splitter(default_width=400, on_persist_width=mock_persist))
        divider = _find_divider(result)

        _trigger_callback(divider.on_double_tap, MagicMock())
        mock_persist.assert_called_once_with(400)


class TestHoverHandlers:
    """验证鼠标 hover handler (lines 161-167)。"""

    def test_on_divider_enter_sets_hovered(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter())
        divider = _find_divider(result)

        # hovered=False 初始，hover_color = TRANSPARENT
        inner_container = divider.content
        assert inner_container.bgcolor == ft.Colors.TRANSPARENT

        # 触发 on_enter，应调度 set_hovered(True)
        _trigger_callback(divider.on_enter, MagicMock())
        # schedule_update 被调用
        assert component_in_updates(page, divider)

    def test_on_divider_exit_clears_hovered(self, mock_i18n_state, mock_app_colors_state):
        """on_exit 应触发 set_hovered(False) 重渲染。

        需先触发 on_enter 使 hovered=True，否则 set_hovered(False) 与
        初值相等，框架不会调度 schedule_update（声明式等值优化）。
        """
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            page, result = _render(_make_splitter())
        divider = _find_divider(result)

        # 先 enter 使 hovered=True
        _trigger_callback(divider.on_enter, MagicMock())
        # 清空 mount 期间累积的 schedule_update，便于隔离 exit 的影响
        page.session.scheduled_updates.clear()
        # 再 exit 使 hovered=False，状态变化触发 schedule_update
        _trigger_callback(divider.on_exit, MagicMock())
        assert component_in_updates(page, divider)


class TestUnmount:
    """验证组件 unmount 不抛异常。"""

    def test_unmount_safe(self, mock_i18n_state, mock_app_colors_state):
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=DEFAULT_WIDTH):
            _, _ = _render(_make_splitter())
        # 不应抛异常
        run_unmount_effects(_make_splitter())


def component_in_updates(page, _divider=None) -> bool:
    """检查 page.session.scheduled_updates 是否非空（即 set_state 被调用）。"""
    return len(page.session.scheduled_updates) > 0
