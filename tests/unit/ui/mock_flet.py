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
        self._snack_bar = None
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
        self.splash = None
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
    def snack_bar(self):
        return self._snack_bar

    @snack_bar.setter
    def snack_bar(self, value):
        self._snack_bar = value

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
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                # Intentionally not awaiting: MockFletPage has no real event loop.
                # The _coro attribute is stored for test inspection only.
                mock_task._coro = result  # noqa: RUF006
        except Exception:
            pass
        self._tasks.append(mock_task)
        return mock_task

    def show_toast(self, message, type="info"):
        pass

    def go(self, route):
        pass

    def can_pop(self):
        return True

    def pop(self):
        pass

    def open(self, control):
        if control not in self.overlay:
            self.overlay.append(control)

    def close(self, control):
        if control in self.overlay:
            self.overlay.remove(control)
