"""ResizableSplitter — 声明式可拖动分栏组件 (Phase A.3).

从命令式容器子类 (历史 page 引用基类) 重写为
``@ft.component`` 函数组件 (CLAUDE.md §3.2 MVVM, §3.3 声明式迁移).

变更要点:
- 历史 page 引用基类与显式 update 调用全部移除, 状态变更由 ``use_state`` 驱动自动重渲染
- 拖拽即时宽度用 ``use_ref`` 缓存 (性能优化, 非 cache 命令式实例), 节流后 ``set_state`` 提交
- ConfigHandler 宽度持久化用 ``use_effect`` 初始加载 + 拖拽结束写入
- 折叠状态用 ``use_state`` 管理; 临时保留 ``set_left_collapsed`` 方法供命令式消费方
  (screener_view) 过渡使用, Phase F.3 后删除
"""

import logging
import time
from collections.abc import Callable

import flet as ft

from ui.theme import AppColors
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class _DragCache:
    """拖拽状态缓存 (use_ref 持久化, 避免 use_state 触发 re-render)。

    ``use_ref(lambda: None)`` 推断为 ``MutableRef[None]`` 无法持 int, 故用容器类。
    """

    __slots__ = ("width", "last_time")

    def __init__(self) -> None:
        self.width: int | None = None
        self.last_time: float = 0.0


def _clamp_width(width: float, min_width: int, max_width: int) -> int:
    """将宽度 clamp 到 ``[min_width, max_width]``。"""
    return max(min_width, min(max_width, int(width)))


def _load_persisted_width(config_key: str, default_width: int, min_width: int, max_width: int) -> int:
    """从 ConfigHandler 加载持久化宽度并 clamp, 失败回退 default_width。"""
    try:
        loaded = ConfigHandler.get_typed(config_key, int, default_width)
    except Exception as e:
        logger.debug("[ResizableSplitter] load width failed, use default: %s", e)
        loaded = default_width
    return _clamp_width(loaded, min_width, max_width)


def _persist_width(config_key: str, width: int) -> None:
    """持久化宽度到 ConfigHandler, 失败仅记日志不阻断。

    ``ConfigHandler.set_typed`` 对 validator 失败返回 False (不抛异常),
    但内部 ``save_config`` 可能因磁盘满/权限抛异常, 故需 try/except 兜底。
    """
    try:
        ok = ConfigHandler.set_typed(config_key, int(width))
        if not ok:
            logger.warning("[ResizableSplitter] persist width rejected by validator: %s", config_key)
    except Exception as e:
        logger.debug("[ResizableSplitter] persist width failed: %s", e)


@ft.component
def ResizableSplitter(
    left_content: ft.Control,
    right_content: ft.Control,
    config_key: str,
    default_width: int = 360,
    min_width: int = 280,
    max_width: int = 600,
    on_resize: Callable[[], None] | None = None,
    drag_interval: int = 16,
    collapsible: bool = False,
    collapsed: bool = False,
) -> ft.Container:
    """可拖动调整左右两栏宽度的声明式容器 (对标 VS Code 侧栏 splitter)。

    Args:
        left_content: 左侧内容控件
        right_content: 右侧内容控件
        config_key: 持久化键名 (如 "backtest_config_panel_width")
        default_width: 默认左侧宽度 (首次启动或重置时使用)
        min_width: 左侧最小宽度
        max_width: 左侧最大宽度
        on_resize: 宽度变化回调 (可选, 用于触发子控件刷新, 签名 () -> None)
        drag_interval: 拖拽事件节流毫秒数, 默认 16ms (~60fps)
        collapsible: 是否允许折叠左侧栏
        collapsed: 初始是否折叠左侧栏
    """
    width, set_width = ft.use_state(default_width)
    hovered, set_hovered = ft.use_state(False)
    is_collapsed, set_is_collapsed = ft.use_state(collapsed)
    # use_ref 缓存拖拽中的即时宽度 + 节流时间戳 (缓存数值非命令式实例, 符合声明式红线)
    drag = ft.use_ref(_DragCache)
    # use_ref.current 类型为 T | None (MutableRef 构造允许 None), 但 factory 在
    # 首次渲染时已执行, current 保证非 None。assert 收窄类型供后续属性访问。
    cache = drag.current
    assert cache is not None

    def _load_effect():
        persisted = _load_persisted_width(config_key, default_width, min_width, max_width)
        if persisted != default_width:
            set_width(persisted)

    ft.use_effect(_load_effect, dependencies=[config_key])

    # --- Drag handlers ---

    def _on_drag_start(e) -> None:
        """拖动开始 (当前无需记录起始宽度, 增量累加在 _on_drag_update 完成)。"""
        return

    def _on_drag_update(e) -> None:
        """按拖动增量更新左栏宽度。

        宽度计算始终跟随鼠标 (不丢弃 primary_delta), 仅节流 set_state 与回调,
        避免 reconcile 过频的同时保证拖动跟手。
        """
        # R13: V1 DragUpdateEvent 用 primary_delta (水平拖拽 x 增量);
        # local_delta.x 作为回退 (兼容 V0 mock 或边界场景)
        delta_x = getattr(e, "primary_delta", None)
        if delta_x is None:
            local_delta = getattr(e, "local_delta", None)
            delta_x = getattr(local_delta, "x", 0) if local_delta else 0
        current = cache.width if cache.width is not None else width
        new_width = _clamp_width(current + delta_x, min_width, max_width)
        if new_width == current:
            return
        cache.width = new_width

        # Python 级节流兜底: 仅节流 set_state 与回调, 防止 reconcile 过频
        current_time = time.time()
        if current_time - cache.last_time < (drag_interval / 1000.0):
            return
        cache.last_time = current_time

        set_width(new_width)
        if on_resize:
            on_resize()

    def _on_drag_end(e) -> None:
        """拖动结束时提交最终宽度并持久化。"""
        final_width = cache.width
        if final_width is not None:
            set_width(final_width)
            cache.width = None
            _persist_width(config_key, final_width)
        else:
            _persist_width(config_key, width)

    def _on_double_tap(e) -> None:
        """双击恢复默认宽度并持久化。"""
        cache.width = None
        set_width(default_width)
        _persist_width(config_key, default_width)

    def _on_divider_enter(e) -> None:
        """鼠标进入分隔条: 高亮中线。"""
        set_hovered(True)

    def _on_divider_exit(e) -> None:
        """鼠标离开分隔条: 恢复透明。"""
        set_hovered(False)

    def _set_left_collapsed(collapsed_val: bool) -> None:
        """折叠/恢复左栏 (临时兼容层, 供命令式消费方过渡使用)。"""
        if not collapsible:
            return
        set_is_collapsed(collapsed_val)
        if on_resize:
            on_resize()

    # --- Render ---
    hover_color = ft.Colors.with_opacity(0.6, AppColors.PRIMARY) if hovered else ft.Colors.TRANSPARENT

    left_container = ft.Container(
        content=left_content,
        width=width,
        visible=not is_collapsed,
    )
    # 分隔条: 强制使用 on_horizontal_drag_* 避免与左侧面板垂直滚动冲突;
    # on_enter/on_exit 替代 on_hover (V1 HoverEvent 已强类型化, 语义清晰)
    divider = ft.GestureDetector(
        content=ft.Container(
            width=6,
            expand=True,
            bgcolor=hover_color,
        ),
        on_enter=_on_divider_enter,
        on_exit=_on_divider_exit,
        on_horizontal_drag_start=_on_drag_start,
        on_horizontal_drag_update=_on_drag_update,
        on_horizontal_drag_end=_on_drag_end,
        on_double_tap=_on_double_tap,
        mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
        drag_interval=drag_interval,
        visible=not is_collapsed,
    )

    container = ft.Container(
        content=ft.Row(
            [
                left_container,
                divider,
                ft.Container(content=right_content, expand=True),
            ],
            spacing=0,
            expand=True,
        ),
        expand=True,
    )
    # NOTE(lazy): 临时兼容层, 给命令式消费方 (screener_view) 提供 set_left_collapsed 方法.
    # ceiling: screener_view 在 Phase F.3 声明式重写完成.
    # upgrade: Phase F.3 screener_view 声明式重写完成后删除此赋值.
    container.set_left_collapsed = _set_left_collapsed  # type: ignore[method-assign]  # [reason: 声明式组件无方法定义, 动态挂兼容方法供命令式消费方过渡]
    return container
