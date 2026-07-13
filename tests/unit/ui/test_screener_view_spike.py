"""ScreenerView spike 测试 — 验证 attach_fake_page 模式对复杂组件的可行性。

目标：验证 ``make_component(ScreenerView) + run_mount_effects(component)``
不抛异常，返回 ft.Container。若 spike 成功，则 Phase 2-4 方案可行。

mock 策略：
- ``ScreenerViewModel`` → ``FakeScreenerViewModel``（满足 VM 契约）
- ``get_observable_state`` / ``AppColors.get_observable_state`` → conftest fixture
- ``_get_page()`` → ``attach_fake_page`` 注入 FakePage
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    run_mount_effects,
    run_unmount_effects,
)
from ui.viewmodels import Message

pytestmark = pytest.mark.unit


# ============================================================================
# FakeScreenerViewModel（满足 VM 契约 + ScreenerView 调用的所有方法）
# ============================================================================


@dataclass(frozen=True)
class FakeScreenerState:
    """模拟 ScreenerState 的最小字段集。"""

    page_no: int = 1
    page_size: int = 50
    total_pages: int = 0
    total_items: int = 0
    sort_column: str | None = None
    sort_ascending: bool = True
    loading: bool = False
    status_message: Message | None = None
    status_color: str = ""
    logs: tuple = ()
    stream_cards: tuple = ()
    selected_strategy: str | None = None
    tier_hint: str | None = None
    mode: str = "REALTIME"
    task_unlocked: bool = False
    data_version: int = 0
    strategies_loaded: bool = False
    strategies_with_dep: dict = field(default_factory=dict)
    strategy_desc: str = ""
    strategy_desc_color: str = "default"


class FakeScreenerViewModel:
    """模拟 ScreenerViewModel，记录所有方法调用。

    未显式定义的方法/属性通过 ``__getattr__`` 返回 MagicMock，
    使组件渲染不因缺少 VM 方法而抛 AttributeError。
    """

    def __init__(self) -> None:
        self._state: FakeScreenerState = FakeScreenerState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.method_calls: list[str] = []

    @property
    def state(self) -> FakeScreenerState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in self._subscribers:
            cb(snapshot)

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    # --- ScreenerView 调用的 VM 方法 ---

    def load_strategies(self) -> None:
        self.method_calls.append("load_strategies")
        self._state = replace(self._state, strategies_loaded=True)

    def select_strategy(self, key: str | None) -> None:
        self.method_calls.append(f"select_strategy:{key}")
        self._state = replace(self._state, selected_strategy=key)

    def subscribe_task_manager(self) -> None:
        self.method_calls.append("subscribe_task_manager")

    def unsubscribe_task_manager(self) -> None:
        self.method_calls.append("unsubscribe_task_manager")

    def get_current_page_data(self) -> Any:
        """返回空 DataFrame（避免表格渲染失败）。"""
        import pandas as pd

        return pd.DataFrame()

    def get_strategy_params(self, key: str | None) -> list:
        return []

    def get_export_data(self) -> Any:
        import pandas as pd

        return pd.DataFrame()

    async def load_history_tree(self, offset: int = 0) -> tuple:
        return ()

    async def load_history_data(self, *args: Any, **kwargs: Any) -> tuple:
        return (None, "")

    async def export_results(self, filepath: str) -> tuple:
        return (filepath, None)

    async def run_strategy(self, key: str | None, params: dict | None = None) -> None:
        self.method_calls.append("run_strategy")

    async def sort_data(self, col: str, ascending: bool) -> None:
        self.method_calls.append("sort_data")

    def update_strategy_desc(self, key: str | None, params: dict | None = None) -> None:
        pass

    def change_page_size(self, size: int) -> None:
        pass

    def change_page(self, delta: int) -> None:
        pass

    def switch_to_history(self) -> None:
        pass

    def switch_to_realtime(self) -> None:
        pass

    def set_history_viewing_status(self, display: str, label: str | None = None) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        """未显式定义的属性/方法返回 MagicMock，避免 AttributeError。"""
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


# ============================================================================
# Spike 测试
# ============================================================================


@pytest.fixture
def mock_screener_vm(monkeypatch):
    """注入 FakeScreenerViewModel 替换 ScreenerViewModel。"""
    fake_vm = FakeScreenerViewModel()

    def _factory() -> FakeScreenerViewModel:
        return fake_vm

    # monkeypatch ScreenerViewModel 类，使 factory lambda 返回 fake_vm
    monkeypatch.setattr("ui.views.screener_view.ScreenerViewModel", lambda: fake_vm)
    return fake_vm


class TestScreenerViewSpike:
    """Spike：验证 attach_fake_page 能驱动 ScreenerView 渲染。"""

    def test_mount_produces_container(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_screener_vm,
    ) -> None:
        """挂载 ScreenerView 不抛异常，返回 ft.Container。"""
        from ui.views.screener_view import ScreenerView

        component = make_component(ScreenerView)
        run_mount_effects(component)

        # 验证返回值是 ft.Control 子类
        from tests.unit.ui.component_renderer import render_once

        result = render_once(component)
        assert isinstance(result, ft.Control)

    def test_mount_triggers_vm_subscribe_and_load_strategies(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_screener_vm,
    ) -> None:
        """挂载后 VM.subscribe 被调用 + load_strategies 被调用。"""
        from ui.views.screener_view import ScreenerView

        component = make_component(ScreenerView)
        run_mount_effects(component)

        # VM subscribe 被调用（use_viewmodel hook 注册）
        assert len(mock_screener_vm._subscribers) > 0
        # load_strategies 被调用（mount effect）
        assert "load_strategies" in mock_screener_vm.method_calls

    def test_mount_triggers_task_manager_subscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_screener_vm,
    ) -> None:
        """挂载后 subscribe_task_manager 被调用。"""
        from ui.views.screener_view import ScreenerView

        component = make_component(ScreenerView)
        run_mount_effects(component)

        assert "subscribe_task_manager" in mock_screener_vm.method_calls

    def test_unmount_triggers_dispose_and_unsubscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_screener_vm,
    ) -> None:
        """卸载后 dispose + unsubscribe_task_manager 被调用。"""
        from ui.views.screener_view import ScreenerView

        component = make_component(ScreenerView)
        run_mount_effects(component)

        assert mock_screener_vm.dispose_called is False

        run_unmount_effects(component)

        assert mock_screener_vm.dispose_called is True
        assert "unsubscribe_task_manager" in mock_screener_vm.method_calls
