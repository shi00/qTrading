import asyncio

from unittest.mock import MagicMock


def _install_v1_compat_control_page_mock() -> None:
    """V1 兼容桩：让 ``ft.Control.page`` 可读写、``update()`` 容忍未挂载情况。

    V1 中 ``ft.Control.page`` 改为只读 property（通过 ``parent`` 链查找），
    ``Control.update()`` 要求控件已挂载到 page，否则抛 ``RuntimeError``。
    本项目测试代码与 4 处源码（``app_layout``/``task_center_view``/
    ``toast_manager``/``failover_config_panel``）依赖 V0 行为：直接
    ``self.page = page`` 赋值、未挂载控件调用 ``update()`` 静默返回、
    ``if self.page:`` 在未挂载时返回 falsy 而非抛异常。

    本函数在测试环境下 monkey-patch ``ft.Control``，保持 V0 兼容行为：

    - ``page`` property：getter 优先返回 ``__dict__['_mock_page']``，
      未设过则走 V1 原生 ``parent`` 链查找；若原生查找抛 ``RuntimeError``
      （控件未挂载），返回 ``None``（兼容 ``if self.page:`` 用法，V0 行为）；
      setter 写入 ``__dict__``。
    - ``update()``：若 ``_mock_page`` 与原生 ``parent`` 链均无 page，
      静默返回（不抛 ``RuntimeError``），与 V0 行为兼容。

    幂等：多次调用安全（仅 patch 一次，由 ``_mock_page_patched`` 标志守护）。
    """
    import flet as ft

    if getattr(ft.Control, "_mock_page_patched", False):
        return

    original_page_get = ft.Control.page.fget
    original_update = ft.Control.update

    @property
    def page(self):  # type: ignore[no-untyped-def]
        mock_page = self.__dict__.get("_mock_page", None)
        if mock_page is not None:
            return mock_page
        # V0 兼容：未挂载时返回 None（不抛 RuntimeError），支持 `if self.page:` 用法
        try:
            return original_page_get(self)
        except RuntimeError:
            return None

    @page.setter
    def page(self, value):
        self.__dict__["_mock_page"] = value

    def update(self):  # type: ignore[no-untyped-def]
        # V0 兼容：未挂载到 page 时静默返回（不抛 RuntimeError）
        if self.__dict__.get("_mock_page", None) is None:
            try:
                original_page_get(self)
            except RuntimeError:
                return
        original_update(self)

    ft.Control.page = page  # type: ignore[method-assign]
    ft.Control.update = update  # type: ignore[method-assign]
    ft.Control._mock_page_patched = True  # type: ignore[attr-defined]


# 模块导入时一次性应用 V1 兼容桩，确保 UI 测试目录下所有测试均生效。
_install_v1_compat_control_page_mock()


class MockClientStorage:
    def __init__(self):
        self._data = {}

    def set(self, key, value):
        self._data[key] = value

    def get(self, key):
        return self._data.get(key)

    def contains_key(self, key):
        return key in self._data

    def remove(self, key):
        self._data.pop(key, None)


class MockSession:
    def __init__(self):
        self._data = {}

    def set(self, key, value):
        self._data[key] = value

    def get(self, key):
        return self._data.get(key)

    def contains_key(self, key):
        return key in self._data

    def remove(self, key):
        self._data.pop(key, None)


class MockFletPage:
    def __init__(self):
        self.controls = [MagicMock()]
        self.overlay = []
        self._open_dialogs = []  # R11: 独立 dialog 栈，与 overlay 解耦（overlay 可能混入 ToastManager 等非 dialog 元素）
        self.services = []
        self._shared_preferences = MockClientStorage()
        self._session = MockSession()
        self._theme_mode = None
        self._theme = None
        self._dark_theme = None
        self._scroll = None
        self.title = ""
        self.window = MagicMock()
        self.window.width = 1200
        self.window.height = 800
        self.padding = 0
        self.spacing = 0
        self.bgcolor = None
        self.data = None
        self.horizontal_alignment = None
        self.vertical_alignment = None
        self.fonts = None
        self.pubsub = MagicMock()
        self.pubsub.subscribe_all = MagicMock()
        self.pubsub.unsubscribe_all = MagicMock()
        self.pubsub.send_all = MagicMock()
        self._tasks = []

    @property
    def shared_preferences(self):
        return self._shared_preferences

    @property
    def session(self):
        return self._session

    @property
    def theme_mode(self):
        return self._theme_mode

    @theme_mode.setter
    def theme_mode(self, value):
        self._theme_mode = value

    @property
    def theme(self):
        return self._theme

    @theme.setter
    def theme(self, value):
        self._theme = value

    @property
    def dark_theme(self):
        return self._dark_theme

    @dark_theme.setter
    def dark_theme(self, value):
        self._dark_theme = value

    @property
    def scroll(self):
        return self._scroll

    @scroll.setter
    def scroll(self, value):
        self._scroll = value

    def add(self, *controls):
        self.controls.extend(controls)

    def insert(self, index, control):
        self.controls.insert(index, control)

    def remove(self, control):
        if control in self.controls:
            self.controls.remove(control)

    def clean(self):
        self.controls.clear()

    def update(self, *args, **kwargs):
        pass

    def run_task(self, func, *args, **kwargs):
        """测试桩语义：同步调用 func，若是协程则关闭它。

        P2-2c: 此实现不模拟真实 Flet 的协程调度语义（真实 run_task 会在
        页面事件循环中异步执行协程并返回 Task）。测试中如需验证协程
        执行结果，应直接 await 协程而非依赖 run_task。
        保持此简化语义的原因（§1.4 不重构没坏的代码）：
        1. 改为真实调度会绑定事件循环，破坏 session 级循环隔离策略
        2. 现有测试依赖"run_task 不真正执行协程"的行为来验证接线而非结果
        3. 若需验证协程执行，测试应直接 await，而非通过 run_task 间接调度
        """
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                mock_task._coro = result
                result.close()
        except (ValueError, TypeError, AttributeError):
            pass
        self._tasks.append(mock_task)
        return mock_task

    def show_toast(self, message, type="info"):
        pass

    def go(self, route):
        pass

    def show_dialog(self, control):
        """V1 dialog 管理：维护独立 dialog 栈并同步 overlay 以支持测试断言。

        R11: 使用独立 ``_open_dialogs`` 栈而非复用 ``overlay``，因为 overlay
        可能混入非 dialog 元素（如 ToastManager 的自定义 Container），直接
        复用会导致栈语义错误。
        """
        self._open_dialogs.append(control)
        if control not in self.overlay:
            self.overlay.append(control)

    def pop_dialog(self):
        """V1 dialog 管理：弹出栈顶 dialog 并从 overlay 移除。

        R11: 从独立 ``_open_dialogs`` 栈弹出，避免误删 overlay 中的非 dialog
        元素。返回栈顶 dialog（栈空时返回 None）。
        """
        if self._open_dialogs:
            top = self._open_dialogs.pop()
            if top in self.overlay:
                self.overlay.remove(top)
            return top
        return None


class MockDragUpdateEvent:
    """DragUpdateEvent 测试桩 (V1 强类型，R13)。

    V1 ``ft.DragUpdateEvent`` 已强类型化，dataclass 字段为
    ``name/data/control/local_position/global_position/local_delta/
    global_delta/primary_delta/timestamp``——不再有 V0 的 ``delta_x/delta_y``。
    本桩提供 R13 主路径 ``primary_delta``（水平拖拽为 x 增量）与回退字段
    ``local_delta.x``（兼容边界场景），覆盖 ``resizable_splitter._on_drag_update``
    的两条路径。绕过真实 flet 需 ControlEvent + JSON 解析的构造，故提供此轻量桩。
    """

    def __init__(self, primary_delta=0, local_delta=None):
        self.primary_delta = primary_delta
        self.local_delta = local_delta


class MockHoverEvent:
    """HoverEvent 测试桩 (V1 强类型，R13)。

    V1 ``ft.HoverEvent`` 已强类型化（继承 ``PointerEvent``），dataclass 字段含
    ``kind/local_position/global_position/timestamp/local_delta/global_delta/...``
    ——不再需要 V0 的 ``json.loads(e.data)`` 解析时间戳/坐标。本桩提供
    ``local_position``/``local_delta`` 等 V1 字段，同时保留 ``data`` 字段以
    兼容可能存在的 V0 风格引用。绕过真实 flet 需 ControlEvent 构造，故用此桩。
    """

    def __init__(self, data="", local_position=None, local_delta=None, timestamp=0.0):
        self.data = data
        self.local_position = local_position
        self.local_delta = local_delta
        self.timestamp = timestamp
