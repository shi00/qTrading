"""toast_manager — 声明式组件 (Phase B.4).

从命令式 class 子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- ``ToastCard`` 从 ft.Container 子类改为 ``@ft.component def ToastCard(data, on_dismiss)``
  + ``use_state`` (hover/expanded/dismissing) + ``use_effect`` (timer 生命周期)
- ``ToastManager`` 保留为普通类（命令式 API 外壳），不再继承 ft.Control，
  不再调命令式挂载/更新/生命周期钩子（改由声明式渲染 + use_effect 驱动）
- toast 队列通过 ``ToastManagerState`` (Observable) 驱动 ``ToastManagerView`` 重渲染
- asyncio 任务生命周期用 ``use_effect(setup, [], cleanup=cleanup)`` 管理
- R2: ``_run_timer`` 中 ``except asyncio.CancelledError: raise``；
  cleanup 中 ``gather_for_shutdown_cleanup`` 不重新抛出（关机清理语义）
- ``gather_for_shutdown_cleanup`` 保留（``stop_all`` 优雅停机）

消费方适配（后续 phase）:
    main.py 中 ``page.toast = ToastManager(page)`` 仍可工作（命令式 API 保留），
    但需额外将 ``ToastManagerView()`` 挂载到 page overlay 列表才能显示 toast。
    本 phase 仅重写 toast_manager.py，main.py 适配留待后续。
"""

import asyncio
import logging
import os
import platform
import threading
import typing
from collections.abc import Callable
from dataclasses import dataclass, field

import flet as ft

from ui.components.flet_type_helpers import safe_controls, safe_icon, safe_on_click, safe_on_hover
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.async_utils import gather_for_shutdown_cleanup

logger = logging.getLogger(__name__)


# ============================================================================
# 常量
# ============================================================================

LONG_TEXT_THRESHOLD = 80
COLLAPSED_MAX_LINES = 3
MAX_TOAST_COUNT = 5


# ============================================================================
# 数据模型
# ============================================================================


@dataclass
class ToastData:
    """单个 toast 的不可变数据（传递给 ToastCard 组件）。

    P2-10: 新增 action_text/on_action 支持操作按钮（如"打开文件夹"）。
    """

    id: int
    message: str
    icon: str
    color: str
    duration: int
    action_text: str | None = None
    on_action: Callable[[], None] | None = None

    def __eq__(self, other: object) -> bool:
        """仅按 id 比较 — Callable 字段不参与相等性判断（P2-10）。

        ft.observable 的 list 赋值会触发元素比较；on_action 是 Callable，
        逐字段比较有歧义，id 已足够标识唯一 toast。
        """
        if not isinstance(other, ToastData):
            return NotImplemented
        return self.id == other.id


@ft.observable
@dataclass
class ToastManagerState(ft.Observable):
    """Toast 队列 Observable 状态源。

    ``ToastManagerView`` 通过 ``ft.use_state(get_global_state)`` 订阅，
    ``show()`` 更新 ``toasts`` 触发 Observable 通知，框架自动重渲染。
    """

    toasts: list[ToastData] = field(default_factory=list)


# ============================================================================
# 全局状态（module-level singleton）
# ============================================================================

_state: ToastManagerState | None = None
_state_lock = threading.Lock()


def get_global_state() -> ToastManagerState:
    """获取全局 ToastManagerState 单例。"""
    global _state
    with _state_lock:
        if _state is None:
            _state = ToastManagerState()
        return _state


# 全局任务追踪（供 stop_all 取消所有活动任务）
_active_tasks: set[asyncio.Task | asyncio.Future] = set()
_active_tasks_lock = threading.Lock()


def _register_task(task: asyncio.Task | asyncio.Future | None) -> None:
    """注册任务到全局追踪集合（线程安全，自动清理）。"""
    if not isinstance(task, (asyncio.Task, asyncio.Future)):
        return
    with _active_tasks_lock:
        _active_tasks.add(task)

    def on_done(t: asyncio.Task | asyncio.Future) -> None:
        with _active_tasks_lock:
            _active_tasks.discard(t)

    task.add_done_callback(on_done)


def _reset_state_for_test() -> None:
    """测试隔离：重置全局 state 和任务集合。

    单元测试 autouse fixture 调用，避免跨测试泄漏。
    """
    global _state
    with _state_lock:
        _state = None
    with _active_tasks_lock:
        _active_tasks.clear()


# ============================================================================
# 导出引导 (P2-10)
# ============================================================================


async def open_export_folder(filepath: str) -> None:
    """打开导出文件所在文件夹 (桌面端 action toast 回调).

    跨平台守卫 (§0.5.12.2 #52): os.startfile 仅 Windows 可用, 其他平台静默跳过.
    R16: os.startfile 经 asyncio.to_thread 提交 (Python stdlib, 不阻塞事件循环).
    """
    if platform.system() != "Windows":
        return
    folder = os.path.dirname(os.path.abspath(filepath))
    if not folder or not os.path.isdir(folder):
        return
    await asyncio.to_thread(os.startfile, folder)  # type: ignore[attr-defined]  # [reason: os.startfile 仅 Windows 存在, 已有 platform 守卫, 类型存根跨平台缺失]


# ============================================================================
# 颜色/图标映射
# ============================================================================

_COLOR_MAP = {
    "success": (AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE),
    "error": (AppColors.ERROR, ft.Icons.ERROR),
    "warning": (AppColors.WARNING, ft.Icons.WARNING),
    "info": (AppColors.INFO, ft.Icons.INFO),
}


def _resolve_color_icon(toast_type: str) -> tuple[str, str]:
    return _COLOR_MAP.get(toast_type, _COLOR_MAP["info"])


# ============================================================================
# 命令式 API 外壳
# ============================================================================


class ToastManager:
    """命令式 API 外壳，操作全局 ``ToastManagerState``。

    保留 ``.show()`` / ``.stop_all()`` API 供 main.py / startup_views.py /
    shutdown.py 调用。消费方需将 ``ToastManagerView()`` 挂载到 ``page.overlay``
    才能显示 toast（声明式渲染，本 phase 不改 main.py）。

    Thread Safety:
    - ``show()`` / ``_remove_toast()`` / ``stop_all()`` 通过 ``_lock`` 保护
        ``_next_id`` 与 ``_is_stopping``
    - ``_active_tasks`` 由 module-level ``_active_tasks_lock`` 保护
    """

    MAX_TOAST_COUNT = MAX_TOAST_COUNT

    def __init__(self, page: ft.Page | None = None):
        self.page = page
        self._lock = threading.Lock()
        self._is_stopping = False
        self._next_id = 0

    def show(
        self,
        message: str,
        toast_type: str = "info",
        duration: int = 10,
        action_text: str | None = None,
        on_action: Callable[[], None] | None = None,
    ) -> None:
        """显示 toast 通知。

        Args:
            message: 显示文本
            toast_type: 'info' / 'success' / 'error' / 'warning'
            duration: 自动消失秒数
            action_text: 操作按钮文本 (P2-10); None 时不显示按钮
            on_action: 操作按钮回调 (P2-10); action_text 非空时必填
        """
        if not self.page or self._is_stopping:
            return

        # 防御：空 page.controls 时挂载动画 overlay 会崩溃 Flet Dart 渲染器
        if not self.page.controls:
            logger.warning(
                "[ToastManager] Suppressed toast notification because page has no controls: %s",
                message,
            )
            return

        color, icon = _resolve_color_icon(toast_type)

        # P2-10: action toast 用更长 duration (30s), 给用户足够时间点击操作
        if action_text is not None:
            duration = 30

        with self._lock:
            self._next_id += 1
            new_toast = ToastData(
                id=self._next_id,
                message=message,
                icon=icon,
                color=color,
                duration=duration,
                action_text=action_text,
                on_action=on_action,
            )
            state = get_global_state()
            new_list = [*state.toasts, new_toast]
            # 限制最大数量，移除最旧
            while len(new_list) > self.MAX_TOAST_COUNT:
                new_list.pop(0)
            # dataclass __setattr__ 触发 _notify → ToastManagerView 重渲染
            state.toasts = new_list

    def _remove_toast(self, toast_id: int) -> None:
        """从队列移除指定 toast（供 ToastCard dismiss 回调）。"""
        with self._lock:
            state = get_global_state()
            state.toasts = [t for t in state.toasts if t.id != toast_id]

    async def stop_all(self) -> None:
        """优雅停机：取消所有活动任务并清空队列。

        幂等，可多次调用。使用 ``gather_for_shutdown_cleanup`` 等待所有任务
        清理完成（CancelledError 视为预期结果，不重新抛出）。
        """
        self._is_stopping = True

        with _active_tasks_lock:
            tasks_snapshot = list(_active_tasks)

        valid_tasks = [t for t in tasks_snapshot if isinstance(t, (asyncio.Task, asyncio.Future))]
        for task in valid_tasks:
            if not task.done():
                task.cancel()

        if valid_tasks:
            await gather_for_shutdown_cleanup(*valid_tasks)

        with self._lock:
            state = get_global_state()
            state.toasts = []


# ============================================================================
# 声明式渲染组件
# ============================================================================


@ft.component
def ToastManagerView() -> ft.Container:
    """声明式 Toast 渲染组件，订阅全局 ``ToastManagerState``。

    消费方将本组件实例加入 page 的 overlay 列表即可显示 toast
    （具体挂载方式由消费方负责，本 phase 不改 main.py）。

    自动重渲染：``ToastManager.show()`` 更新 state.toasts 触发 Observable 通知，
    本组件通过 ``ft.use_state(get_global_state)`` 订阅，框架自动重渲染。
    """
    ft.use_state(get_global_state)  # 订阅状态变化
    state = get_global_state()

    def _on_dismiss(toast_id: int) -> None:
        """ToastCard dismiss 回调：从队列移除。"""
        get_global_state().toasts = [t for t in get_global_state().toasts if t.id != toast_id]

    return ft.Container(
        content=ft.Column(
            controls=[ToastCard(data=td, on_dismiss=_on_dismiss) for td in state.toasts],
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        ),
        right=20,
        bottom=20,
        width=360,
        bgcolor=ft.Colors.TRANSPARENT,
    )


@ft.component
def ToastCard(data: ToastData, on_dismiss: Callable[[int], None]) -> ft.Container:
    """声明式单个 Toast 卡片。

    状态驱动:
    - ``is_hovered``: hover 暂停倒计时
    - ``is_expanded``: 长文本展开/折叠
    - ``is_dismissing``: dismiss 动画进行中

    任务生命周期:
    - ``use_effect(setup, [], cleanup=cleanup)`` 启动计时器
    - cleanup 中 ``gather_for_shutdown_cleanup`` 等待 task 取消完成
        （R2: CancelledError 视为预期结果，不重新抛出）
    - ``_run_timer`` 中 ``except asyncio.CancelledError: raise`` 确保
        CancelledError 传播（R2 红线）
    """
    is_hovered, set_is_hovered = ft.use_state(False)
    is_expanded, set_is_expanded = ft.use_state(False)
    is_dismissing, set_is_dismissing = ft.use_state(False)

    is_long_text = len(data.message) > LONG_TEXT_THRESHOLD

    # use_ref 持久化最新 hover/expand 状态（供 timer 闭包读取最新值）
    # 注意：这不是 cache 命令式实例，是 cache 状态快照（符合声明式范式）
    hovered_ref = ft.use_ref(lambda: False)
    expanded_ref = ft.use_ref(lambda: False)
    hovered_ref.current = is_hovered
    expanded_ref.current = is_expanded

    task_ref = ft.use_ref(lambda: None)

    def setup() -> None:
        """挂载时启动计时器任务。"""
        try:
            page = ft.context.page
        except RuntimeError:
            page = None
        if page is None:
            return

        async def _run_timer() -> None:
            try:
                await asyncio.sleep(0.3)
                remaining = data.duration
                while remaining > 0:
                    if not hovered_ref.current and not expanded_ref.current:
                        remaining -= 0.1
                    await asyncio.sleep(0.1)
                set_is_dismissing(True)
                await asyncio.sleep(0.3)  # 等待 dismiss 动画
                on_dismiss(data.id)
            except asyncio.CancelledError:
                raise  # R2: CancelledError 必须传播以配合优雅停机
            except Exception as exc:
                logger.debug("[ToastManager] Auto-dismiss failed: %s", exc, exc_info=True)

        task = page.run_task(_run_timer)
        task_ref.current = typing.cast(typing.Any, task)
        _register_task(typing.cast(typing.Any, task))

    async def cleanup() -> None:
        """卸载时取消任务并等待清理完成（R2 兼容）。

        使用 ``gather_for_shutdown_cleanup`` 等待 task 取消完成，
        CancelledError 视为预期结果（关机清理语义），不重新抛出。
        """
        task = task_ref.current
        if task is None:
            return
        if not task.done():
            task.cancel()
        # gather_for_shutdown_cleanup 内部 return_exceptions=True，
        # CancelledError 不重新抛出（关机清理场景专用）
        await gather_for_shutdown_cleanup(task)

    ft.use_effect(setup, dependencies=[], cleanup=cleanup)

    # --- 渲染参数 ---
    opacity = 0 if is_dismissing else 1
    offset_x = 1.1 if is_dismissing else 0

    max_lines = None if is_expanded else COLLAPSED_MAX_LINES
    expand_icon = ft.Icons.KEYBOARD_ARROW_UP if is_expanded else ft.Icons.KEYBOARD_ARROW_DOWN
    expand_tooltip = I18n.get("common_collapse") if is_expanded else I18n.get("common_expand")

    text_control = ft.Text(
        data.message,
        size=AppStyles.FONT_SIZE_LG,
        color=ft.Colors.ON_SURFACE,
        width=270,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS,
        tooltip=I18n.get("toast_expand_hint") if is_long_text else None,
    )

    content_col_controls: list[ft.Control] = [text_control]
    if is_long_text:
        content_col_controls.append(
            ft.Row(
                [
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=expand_icon,
                        icon_size=AppStyles.FONT_SIZE_TITLE,
                        icon_color=ft.Colors.PRIMARY,
                        tooltip=expand_tooltip,
                        on_click=lambda e: set_is_expanded(not is_expanded),
                        style=ft.ButtonStyle(padding=0),
                    ),
                ],
                alignment=ft.MainAxisAlignment.END,
                height=20,
            ),
        )

    def _on_hover(e: ft.ControlEvent) -> None:
        set_is_hovered(e.data == "true")

    def _on_dismiss_click(e: ft.ControlEvent) -> None:
        if is_dismissing:
            return
        set_is_dismissing(True)
        on_dismiss(data.id)

    def _on_action_click(e: ft.ControlEvent) -> None:
        """P2-10: 执行 action 回调并 dismiss toast。"""
        if is_dismissing:
            return
        if data.on_action is not None:
            try:
                data.on_action()
            except Exception as exc:
                logger.warning("[ToastManager] Action callback failed: %s", exc, exc_info=True)
        set_is_dismissing(True)
        on_dismiss(data.id)

    # P2-10: action 按钮 (Material Snackbar 范式, 文本下方右侧)
    if data.action_text is not None:
        content_col_controls.append(
            ft.Row(
                [
                    ft.Container(expand=True),
                    ft.TextButton(
                        data.action_text,
                        on_click=safe_on_click(_on_action_click),
                        style=ft.ButtonStyle(
                            color=data.color,
                            padding=0,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.END,
                height=28,
            ),
        )

    return ft.Container(
        content=ft.Row(
            safe_controls(
                [
                    ft.Icon(safe_icon(data.icon), color=data.color, size=AppStyles.FONT_SIZE_XL),
                    ft.Column(
                        content_col_controls,
                        spacing=2,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    ft.IconButton(
                        ft.Icons.CLOSE,
                        icon_size=AppStyles.FONT_SIZE_TITLE,
                        icon_color=ft.Colors.ON_SURFACE_VARIANT,
                        on_click=safe_on_click(_on_dismiss_click),
                        tooltip=I18n.get("common_close"),
                    ),
                ],
            ),
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=12,
        bgcolor=ft.Colors.SURFACE,
        border=ft.Border.only(left=ft.BorderSide(4, data.color)),  # type: ignore[untyped]
        border_radius=8,
        shadow=ft.BoxShadow(
            spread_radius=1,
            blur_radius=10,
            color=ft.Colors.with_opacity(0.1, ft.Colors.SHADOW),
            offset=ft.Offset(0, 4),
        ),
        offset=ft.Offset(offset_x, 0),
        animate_offset=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_IN),
        opacity=opacity,
        on_hover=safe_on_hover(_on_hover),
    )
