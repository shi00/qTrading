import asyncio

from unittest.mock import MagicMock


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
        self.controls = []
        self.overlay = []
        self._client_storage = MockClientStorage()
        self._session = MockSession()
        self._dialog = None
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
    def client_storage(self):
        return self._client_storage

    @property
    def session(self):
        return self._session

    @property
    def dialog(self):
        return self._dialog

    @dialog.setter
    def dialog(self, value):
        self._dialog = value

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

    def open(self, control):
        if control not in self.overlay:
            self.overlay.append(control)

    def close(self, control):
        if control in self.overlay:
            self.overlay.remove(control)
