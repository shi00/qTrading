import logging
import time
from collections.abc import Callable

import flet as ft

from ui.theme import AppColors

logger = logging.getLogger(__name__)


class ResizableSplitter(ft.Container):
    """可拖动调整左右两栏宽度的容器（对标 VS Code 侧栏 splitter）。

    依赖：``ui.theme.AppColors``（hover 高亮配色）、``utils.config_handler.ConfigHandler``
    （宽度持久化）、``utils.logger``（异常兜底日志）。

    Args:
        left_content: 左侧内容控件
        right_content: 右侧内容控件
        config_key: 持久化键名（如 "backtest_config_panel_width"）
        default_width: 默认左侧宽度（首次启动或重置时使用）
        min_width: 左侧最小宽度（默认 280）
        max_width: 左侧最大宽度（默认 600）
        on_resize: 宽度变化回调（可选，用于触发子控件刷新，签名 () -> None）
        drag_interval: 拖拽事件节流毫秒数，默认 16ms（≈60fps），避免 Flet 通讯管道被高频事件打满
        collapsible: 是否允许折叠左侧栏，默认 False
    """

    def __init__(
        self,
        left_content: ft.Control,
        right_content: ft.Control,
        config_key: str,
        default_width: int = 360,
        min_width: int = 280,
        max_width: int = 600,
        on_resize: Callable[[], None] | None = None,
        drag_interval: int = 16,
        collapsible: bool = False,
    ):
        super().__init__(expand=True)
        self._left_content = left_content
        self._right_content = right_content
        self._config_key = config_key
        self._default_width = default_width
        self._min_width = min_width
        self._max_width = max_width
        self._on_resize = on_resize
        self._drag_interval = drag_interval
        self._collapsible = collapsible
        self._left_collapsed = False
        self._current_width = self._load_width()
        self._last_drag_time = 0.0  # Python 级节流兜底

        # 分隔条：强制使用 on_horizontal_drag_* 避免与左侧面板垂直滚动冲突
        # 使用 on_enter/on_exit 替代 on_hover：Flet HoverEvent.data 是 JSON 字符串
        # （含 timestamp/kind/global_x 等字段，见 flet/core/gesture_detector.py:847-858），
        # 不是 "true"/"false"，解析会失效；on_enter/on_exit 语义清晰无需解析 data。
        self._divider = ft.GestureDetector(
            content=ft.Container(
                width=6,
                expand=True,
                bgcolor=ft.Colors.TRANSPARENT,
            ),
            on_enter=self._on_divider_enter,
            on_exit=self._on_divider_exit,
            on_horizontal_drag_start=self._on_drag_start,
            on_horizontal_drag_update=self._on_drag_update,
            on_horizontal_drag_end=self._on_drag_end,
            on_double_tap=self._on_double_tap,
            mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
            drag_interval=drag_interval,
        )

        # 左侧容器保留命名引用，以便拖动时局部 update 而非全局刷新
        self._left_container = ft.Container(content=self._left_content, width=self._current_width)

        self.content = ft.Row(
            [
                self._left_container,
                self._divider,
                ft.Container(content=self._right_content, expand=True),
            ],
            spacing=0,
            expand=True,
        )

    def _load_width(self) -> int:
        """从 ConfigHandler 加载持久化宽度，并 clamp 到 [min_width, max_width]。

        加载失败时回退到 default_width，确保 ConfigHandler 异常不影响 splitter 可用性。
        """
        from utils.config_handler import ConfigHandler

        try:
            loaded = ConfigHandler.get_typed(self._config_key, int, self._default_width)
        except Exception as e:
            logger.debug("[ResizableSplitter] load width failed, use default: %s", e)
            loaded = self._default_width
        return max(self._min_width, min(self._max_width, loaded))

    def set_left_collapsed(self, collapsed: bool) -> None:
        """折叠/恢复左栏，用于 ScreenerView 实时/历史模式切换。"""
        if not self._collapsible or self._left_collapsed == collapsed:
            return
        self._left_collapsed = collapsed
        # 仅依赖 visible 属性控制布局占用，不冗余设置 width=0
        self._left_container.visible = not collapsed
        self._divider.visible = not collapsed
        if not collapsed:
            self._left_container.width = self._current_width
        if self.page:
            self.update()
        if self._on_resize:
            self._on_resize()

    def _on_drag_start(self, e) -> None:
        """拖动开始回调（当前无需记录起始宽度，增量累加在 _on_drag_update 完成）。"""
        return

    def _on_drag_update(self, e) -> None:
        """按拖动增量更新左栏宽度。

        宽度计算始终跟随鼠标（不丢弃 delta_x），仅节流 UI update 与回调，
        避免 Flet 通讯管道被高频事件打满的同时保证拖动跟手。
        """
        delta_x = getattr(e, "delta_x", 0) or 0
        new_width = max(self._min_width, min(self._max_width, self._current_width + delta_x))
        if new_width == self._current_width:
            return
        self._current_width = new_width
        self._left_container.width = new_width

        # Python 级节流兜底：仅节流 UI update 与回调，防止 Flet drag_interval 在某些平台失效
        current_time = time.time()
        if current_time - self._last_drag_time < (self._drag_interval / 1000.0):
            return
        self._last_drag_time = current_time

        if self._left_container.page:
            self._left_container.update()
        if self._on_resize:
            self._on_resize()

    def _on_drag_end(self, e) -> None:
        """拖动结束时持久化宽度。失败仅记日志，不阻断用户操作。

        注意：``ConfigHandler.set_typed`` 对 validator 失败返回 False（不抛异常），
        但内部 ``save_config`` 可能因磁盘满/权限抛异常，故仍需 try/except 兜底。
        返回值 False 仅记 warning，不视为致命错误。
        """
        from utils.config_handler import ConfigHandler

        try:
            ok = ConfigHandler.set_typed(self._config_key, int(self._current_width))
            if not ok:
                logger.warning("[ResizableSplitter] persist width rejected by validator: %s", self._config_key)
        except Exception as e:
            logger.debug("[ResizableSplitter] persist width failed: %s", e)

    def _on_double_tap(self, e) -> None:
        """双击恢复默认宽度并持久化。"""
        self._current_width = self._default_width
        self._left_container.width = self._current_width
        if self.page:
            self.update()
        self._on_drag_end(e)

    def _on_divider_enter(self, e) -> None:
        """鼠标进入分隔条：高亮中线（AppColors.PRIMARY with opacity）。

        使用 on_enter 而非 on_hover：Flet ``HoverEvent.data`` 是 JSON 字符串
        （含 timestamp/kind/global_x/local_x/delta_x 等字段），不是 "true"/"false"，
        旧实现字符串比较恒为 False 导致 hover 高亮失效。
        """
        line = self._divider.content
        if not line:
            return
        try:
            line.bgcolor = ft.Colors.with_opacity(0.6, AppColors.PRIMARY)
            line.update()
        except Exception as ex:
            logger.debug("[ResizableSplitter] divider enter highlight failed: %s", ex)

    def _on_divider_exit(self, e) -> None:
        """鼠标离开分隔条：恢复透明。"""
        line = self._divider.content
        if not line:
            return
        try:
            line.bgcolor = ft.Colors.TRANSPARENT
            line.update()
        except Exception as ex:
            logger.debug("[ResizableSplitter] divider exit restore failed: %s", ex)
