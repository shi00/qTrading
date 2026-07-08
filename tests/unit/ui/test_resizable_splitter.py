"""ResizableSplitter 单元测试 — v4.3 响应式布局 Task 1。

覆盖：默认/持久化宽度加载、clamp、拖动计算、Python 级节流、hover 反馈
（on_enter/on_exit 高亮与恢复，含异常兜底）、边界约束、双击重置、持久化、异常兜底、折叠。
"""

import logging
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.mock_flet import MockDragUpdateEvent
from ui.components.resizable_splitter import ResizableSplitter
from ui.theme import AppColors

pytestmark = pytest.mark.unit

DEFAULT_WIDTH = 360
MIN_WIDTH = 280
MAX_WIDTH = 600
CONFIG_KEY = "test_panel_width"
LOGGER_NAME = "ui.components.resizable_splitter"


@pytest.fixture
def make_splitter():
    """工厂 fixture：可指定 get_typed 返回值与构造参数。"""

    def _make(get_typed_return=DEFAULT_WIDTH, **kwargs):
        defaults = dict(
            left_content=ft.Container(width=100),
            right_content=ft.Container(width=100),
            config_key=CONFIG_KEY,
            default_width=DEFAULT_WIDTH,
            min_width=MIN_WIDTH,
            max_width=MAX_WIDTH,
        )
        defaults.update(kwargs)
        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=get_typed_return):
            return ResizableSplitter(**defaults)

    return _make


@pytest.fixture
def splitter(make_splitter):
    """默认 splitter：无持久化值，使用 default_width。"""
    return make_splitter()


# --- 1. 默认宽度加载 ---


def test_default_width_no_persisted_value(make_splitter):
    """get_typed 返回 default_width 时，_current_width == default_width。"""
    s = make_splitter(get_typed_return=DEFAULT_WIDTH)
    assert s._current_width == DEFAULT_WIDTH


# --- 2. 持久化宽度加载与 clamp ---


def test_persisted_width_load_and_clamp_high(make_splitter):
    """持久化值超过 max_width 时 clamp 到 max_width。"""
    s = make_splitter(get_typed_return=700)
    assert s._current_width == MAX_WIDTH


def test_persisted_width_load_and_clamp_low(make_splitter):
    """持久化值低于 min_width 时 clamp 到 min_width。"""
    s = make_splitter(get_typed_return=200)
    assert s._current_width == MIN_WIDTH


def test_persisted_width_load_within_bounds(make_splitter):
    """持久化值在边界内时原样加载。"""
    s = make_splitter(get_typed_return=450)
    assert s._current_width == 450


# --- 3. _load_width 异常兜底 ---


def test_load_width_exception_fallback(make_splitter):
    """get_typed 抛异常时回退 default_width，不抛出。"""
    with patch("utils.config_handler.ConfigHandler.get_typed", side_effect=RuntimeError("config corrupted")):
        s = ResizableSplitter(
            left_content=ft.Container(width=100),
            right_content=ft.Container(width=100),
            config_key=CONFIG_KEY,
            default_width=DEFAULT_WIDTH,
            min_width=MIN_WIDTH,
            max_width=MAX_WIDTH,
        )
    assert s._current_width == DEFAULT_WIDTH


# --- 4. 拖动计算 ---


def test_drag_update_increments_width(splitter):
    """primary_delta 正向增量更新 _current_width。"""
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=50))
    assert splitter._current_width == DEFAULT_WIDTH + 50


def test_drag_update_negative_delta(splitter):
    """primary_delta 负向减量更新 _current_width。"""
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=-30))
    assert splitter._current_width == DEFAULT_WIDTH - 30


def test_drag_update_local_delta_fallback(splitter):
    """R13 回退路径：primary_delta 为 None 时使用 local_delta.x。

    覆盖 V0 mock 或边界场景：当事件仅提供 local_delta.x 而无 primary_delta 时，
    splitter 应正确回退并更新宽度（静默回归修复）。
    """
    local_delta = MagicMock(x=40)
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=None, local_delta=local_delta))
    assert splitter._current_width == DEFAULT_WIDTH + 40


# --- 5. Python 级节流 ---


def test_drag_update_throttle_ui_and_callback(make_splitter):
    """连续快速触发时 _current_width 跟随鼠标，但 UI update 与 on_resize 被节流。"""
    on_resize = MagicMock()
    s = make_splitter(on_resize=on_resize, drag_interval=16)
    # 让 _left_container.page 为真值，使 update 分支可达
    s._left_container._mock_page = MagicMock()
    s._left_container.update = MagicMock()

    # 连续 3 次快速拖动（间隔 < 16ms）
    s._on_drag_update(MockDragUpdateEvent(primary_delta=10))
    s._on_drag_update(MockDragUpdateEvent(primary_delta=10))
    s._on_drag_update(MockDragUpdateEvent(primary_delta=10))

    # 宽度跟随鼠标（3 次增量均生效）
    assert s._current_width == DEFAULT_WIDTH + 30
    # UI update 仅首次执行（后续被节流）
    assert s._left_container.update.call_count == 1
    # on_resize 回调同样仅首次执行
    assert on_resize.call_count == 1


# --- 6. hover 反馈（on_enter/on_exit）---


def test_enter_highlights_divider(splitter):
    """on_enter 时分隔条高亮为 AppColors.PRIMARY with opacity。"""
    splitter._on_divider_enter(MagicMock())
    assert splitter._divider.content.bgcolor == ft.Colors.with_opacity(0.6, AppColors.PRIMARY)


def test_exit_restores_transparent(splitter):
    """on_exit 时恢复透明。"""
    # 先高亮
    splitter._on_divider_enter(MagicMock())
    # 再移出
    splitter._on_divider_exit(MagicMock())
    assert splitter._divider.content.bgcolor == ft.Colors.TRANSPARENT


def test_enter_exception_no_raise(splitter, caplog):
    """on_enter 内部 update 抛异常时不传播，记 debug 日志。"""
    splitter._divider.content.update = MagicMock(side_effect=RuntimeError("update failed"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # 不应抛出
        splitter._on_divider_enter(MagicMock())
    assert any("divider enter highlight failed" in r.message for r in caplog.records)


def test_exit_exception_no_raise(splitter, caplog):
    """on_exit 内部 update 抛异常时不传播，记 debug 日志。"""
    splitter._divider.content.update = MagicMock(side_effect=RuntimeError("update failed"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # 不应抛出
        splitter._on_divider_exit(MagicMock())
    assert any("divider exit restore failed" in r.message for r in caplog.records)


# --- 7. 边界约束 ---


def test_drag_update_clamp_to_max(splitter):
    """primary_delta 使宽度超过 max_width 时 clamp 到 max_width。"""
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=1000))
    assert splitter._current_width == MAX_WIDTH


def test_drag_update_clamp_to_min(splitter):
    """primary_delta 使宽度低于 min_width 时 clamp 到 min_width。"""
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=-1000))
    assert splitter._current_width == MIN_WIDTH


# --- 8. 双击重置 ---


def test_double_tap_resets_to_default(splitter):
    """双击恢复 default_width 并持久化。"""
    # 先拖动改变宽度
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=50))
    assert splitter._current_width == DEFAULT_WIDTH + 50
    # 双击重置
    with patch("utils.config_handler.ConfigHandler.set_typed", return_value=True) as mock_set:
        splitter._on_double_tap(MockDragUpdateEvent())
    assert splitter._current_width == DEFAULT_WIDTH
    assert splitter._left_container.width == DEFAULT_WIDTH
    mock_set.assert_called_once_with(CONFIG_KEY, DEFAULT_WIDTH)


# --- 9. 持久化调用 ---


def test_drag_end_persists_width(splitter):
    """_on_drag_end 调用 ConfigHandler.set_typed 持久化当前宽度。"""
    splitter._on_drag_update(MockDragUpdateEvent(primary_delta=40))
    with patch("utils.config_handler.ConfigHandler.set_typed", return_value=True) as mock_set:
        splitter._on_drag_end(MockDragUpdateEvent())
    mock_set.assert_called_once_with(CONFIG_KEY, DEFAULT_WIDTH + 40)


# --- 10. set_typed 返回 False 记 warning ---


def test_drag_end_set_typed_false_warns(splitter, caplog):
    """set_typed 返回 False 时记 warning 日志。"""
    with patch("utils.config_handler.ConfigHandler.set_typed", return_value=False):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            splitter._on_drag_end(MockDragUpdateEvent())
    assert any("rejected by validator" in r.message for r in caplog.records)


# --- 11. _on_drag_end 磁盘异常兜底 ---


def test_drag_end_exception_no_raise(splitter, caplog):
    """set_typed 抛异常时不传播，记 debug 日志。"""
    with patch("utils.config_handler.ConfigHandler.set_typed", side_effect=OSError("disk full")):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            # 不应抛出
            splitter._on_drag_end(MockDragUpdateEvent())
    assert any("persist width failed" in r.message for r in caplog.records)


# --- 12. set_left_collapsed 折叠/恢复 ---


def test_set_left_collapsed_true(make_splitter):
    """折叠左栏：left_container.visible == False, divider.visible == False。"""
    s = make_splitter(collapsible=True)
    s.set_left_collapsed(True)
    assert s._left_container.visible is False
    assert s._divider.visible is False


def test_set_left_collapsed_false_restores(make_splitter):
    """恢复左栏：visible == True 且 _left_container.width == _current_width。"""
    s = make_splitter(collapsible=True)
    # 先折叠
    s.set_left_collapsed(True)
    assert s._left_container.visible is False
    # 再恢复
    s.set_left_collapsed(False)
    assert s._left_container.visible is True
    assert s._divider.visible is True
    assert s._left_container.width == s._current_width


def test_set_left_collapsed_noop_when_not_collapsible(make_splitter):
    """collapsible=False 时折叠操作无效。"""
    s = make_splitter(collapsible=False)
    s.set_left_collapsed(True)
    assert s._left_container.visible is True


# --- 13. _on_drag_start 不抛异常 ---


def test_on_drag_start_no_raise(splitter):
    """_on_drag_start 当前为空实现预留，不应抛异常。"""
    splitter._on_drag_start(MockDragUpdateEvent())
