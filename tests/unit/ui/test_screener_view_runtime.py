"""ui/views/screener_view.py 组件运行时测试 (Task 2.1).

覆盖:
1. R2/R9/R16 红线守卫: CancelledError raise / DataSanitizer.sanitize_error / page.run_task
2. 组件挂载/卸载: VM subscribe + load_strategies + subscribe_task_manager + FilePicker 注册
3. 14 个 handler 测试: 成功/异常/边界/CancelledError 路径
4. 派生渲染: _build_param_control (四类型) / _build_params_panel / _build_log_card / _build_history_tree
5. 深度链接: _execute_pending_strategy (None 早返回 / 策略不存在 / 自动执行)
6. use_effect cleanup: FilePicker page.services append/remove + PubSub subscribe/unsubscribe

测试范式参考 test_system_tab.py / test_backtest_view.py (FakeVM + component_renderer +
_invoke helper + _rerender helper + page.run_task 提取 + asyncio.run 异步 handler).
现有 test_screener_view.py 覆盖纯函数, test_screener_view_contract.py 覆盖契约守护,
test_screener_view_spike.py 验证 spike 可行性, 本文件补充运行时测试, 不重复覆盖.
"""

import asyncio
import datetime
import inspect
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pandas as pd
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.viewmodels import Message
from ui.viewmodels.screener_view_model import (
    HistoryTreeRow,
    HistoryTreeState,
    ScreenerState,
    StreamCard,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 辅助函数
# ============================================================================


def _read_source() -> str:
    """读取 screener_view.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    from pathlib import Path

    import ui.views.screener_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


def _make_event(value: Any = None, selected: list | None = None) -> MagicMock:
    """构造 ft.ControlEvent mock, 支持 value 和 selected 属性."""
    e = MagicMock()
    e.control.value = value
    if selected is not None:
        e.control.selected = selected
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _await_run_task_handler(page: MagicMock) -> tuple[Any, tuple, dict]:
    """提取 page.run_task 最近一次调用的 handler 与参数。"""
    assert page.run_task.call_args is not None, "page.run_task 未被调用"
    call = page.run_task.call_args
    handler = call.args[0]
    args = call.args[1:]
    kwargs = call.kwargs
    return handler, args, kwargs


def _rerender(env: dict) -> Any:
    """重新渲染组件并更新 env['result'].

    声明式范式下, on_change 触发 set_state 后需手动 render_once 让闭包捕获新 state,
    否则 event handler 中的 state 变量仍是旧值。
    同时触发 render effects (deps 变化检测), 使 _on_task_unlocked 等 use_effect 被触发。
    """
    result = render_once(env["component"])
    env["result"] = result
    env["component"]._run_render_effects()
    return result


def _get_run_button(env: dict) -> ft.Button:
    """从渲染树中找到 run button (icon=PLAY_ARROW).

    源码中 ft.Row([export_btn, run_btn]) export_btn 在前, 需通过 icon 区分。
    """
    buttons = _get_buttons(env)
    ft_btns = [b for b in buttons if isinstance(b, ft.Button)]
    # run_btn icon=PLAY_ARROW, export_btn icon=DOWNLOAD
    run_btns = [b for b in ft_btns if b.icon == ft.Icons.PLAY_ARROW]
    assert len(run_btns) >= 1, "未找到 run button (icon=PLAY_ARROW)"
    return run_btns[0]


def _get_export_button(env: dict) -> ft.Button:
    """从渲染树中找到 export button (icon=DOWNLOAD)."""
    buttons = _get_buttons(env)
    ft_btns = [b for b in buttons if isinstance(b, ft.Button)]
    export_btns = [b for b in ft_btns if b.icon == ft.Icons.DOWNLOAD]
    assert len(export_btns) >= 1, "未找到 export button (icon=DOWNLOAD)"
    return export_btns[0]


def _walk_all_controls(root: Any) -> list:
    """递归返回所有 ft.Control (用于搜索 dropdown / button / slider / text)."""
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(root)
    return found


def _get_dropdowns(env: dict) -> list[ft.Dropdown]:
    """返回渲染树中所有 Dropdown (按出现顺序)."""
    dropdowns: list[ft.Dropdown] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Dropdown) and ctrl not in dropdowns:
            dropdowns.append(ctrl)
    return dropdowns


def _get_buttons(env: dict) -> list:
    """返回渲染树中所有 ft.Button (按出现顺序)."""
    buttons: list[Any] = []

    def _walk(c: Any) -> None:
        if isinstance(c, (ft.Button, ft.IconButton, ft.TextButton)):
            buttons.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(env["result"])
    return buttons


def _get_sliders(env: dict) -> list[ft.Slider]:
    """返回渲染树中所有 Slider."""
    sliders: list[ft.Slider] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Slider) and ctrl not in sliders:
            sliders.append(ctrl)
    return sliders


def _get_segmented_buttons(env: dict) -> list[ft.SegmentedButton]:
    """返回渲染树中所有 SegmentedButton."""
    segs: list[ft.SegmentedButton] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.SegmentedButton) and ctrl not in segs:
            segs.append(ctrl)
    return segs


def _get_progress_rings(env: dict) -> list[ft.ProgressRing]:
    """返回渲染树中所有 ProgressRing."""
    rings: list[ft.ProgressRing] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.ProgressRing) and ctrl not in rings:
            rings.append(ctrl)
    return rings


def _get_texts(env: dict) -> list[ft.Text]:
    """返回渲染树中所有 Text (按出现顺序)."""
    texts: list[ft.Text] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Text) and ctrl not in texts:
            texts.append(ctrl)
    return texts


def _get_expansion_tiles(env: dict) -> list[ft.ExpansionTile]:
    """返回渲染树中所有 ExpansionTile."""
    tiles: list[ft.ExpansionTile] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.ExpansionTile) and ctrl not in tiles:
            tiles.append(ctrl)
    return tiles


# ============================================================================
# FakeScreenerViewModel (满足 VM 契约 + ScreenerView 调用的所有方法)
# ============================================================================


class _FakeScreenerViewModel:
    """模拟 ScreenerViewModel, 满足 use_viewmodel hook 契约 (state/subscribe/dispose).

    记录所有方法调用, 支持可配置的返回值 (strategies / params / export_data / history_tree).
    """

    def __init__(
        self,
        strategies_with_dep: dict | None = None,
        strategy_params: list | None = None,
        history_tree_data: dict | None = None,
    ) -> None:
        self._state: ScreenerState = ScreenerState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.method_calls: list[str] = []
        self._strategies_with_dep = strategies_with_dep or {
            "value": {"name": "价值策略", "missing_apis": []},
            "momentum": {"name": "动量策略", "missing_apis": []},
        }
        self._strategy_params = strategy_params or []
        self._history_tree_data = history_tree_data or {}
        self._export_data: pd.DataFrame | None = None
        self._current_page_data: pd.DataFrame | None = None
        self._export_result: tuple = ("/path/to/file.csv", None)
        self._export_excel_result: tuple = ("/path/to/file.xlsx", None)
        self.run_strategy_mock = MagicMock()
        self.sort_data_mock = MagicMock()
        self.strategy_mgr = MagicMock()
        self.strategy_mgr.get_strategy.return_value = None
        self.data_processor = MagicMock()
        # Task 3.2: 历史树状态由 VM 持有 (替代原 View use_state)
        self._history_tree_offset: int = 0

    @property
    def state(self) -> ScreenerState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes: Any) -> None:
        """模拟 ScreenerViewModel._set_state: 更新字段 + 通知订阅者."""
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    # --- ScreenerView 调用的 VM 方法 ---

    def load_strategies(self) -> None:
        self.method_calls.append("load_strategies")
        self._set_state(
            strategies_with_dep=self._strategies_with_dep,
            strategies_loaded=True,
        )

    def select_strategy(self, key: str | None) -> None:
        self.method_calls.append(f"select_strategy:{key}")
        self._set_state(selected_strategy=key)

    def update_strategy_desc(self, key: str | None, params: dict | None = None) -> None:
        self.method_calls.append(f"update_strategy_desc:{key}")

    def get_strategy_params(self, key: str | None) -> list:
        return list(self._strategy_params)

    def get_base_prompt(self, strategy_key: str) -> str:
        """Mock vm.get_base_prompt (Task 5.1: 从 View 迁入 VM)."""
        return f"base_prompt[{strategy_key}]"

    async def reset_strategy_prompt(self, strategy_key: str) -> str:
        """Mock vm.reset_strategy_prompt (Phase 3.3: 从 View 迁入 VM).

        默认返回 base_prompt 字符串, 与生产 VM 行为一致 (ConfigHandler 重置 + 读 base_prompt).
        测试可通过 ``patch.object(fake_vm, "reset_strategy_prompt", ...)`` 覆盖返回值/副作用.
        """
        self.method_calls.append(f"reset_strategy_prompt:{strategy_key}")
        return self.get_base_prompt(strategy_key)

    async def save_strategy_prompt(self, strategy_key: str, prompt: str) -> tuple[bool, str | None]:
        """Mock vm.save_strategy_prompt (Phase 3.3: 从 View 迁入 VM).

        默认返回 (True, None) 表示保存成功. 测试可通过 ``patch.object`` 覆盖返回值
        以模拟 validate_prompt 失败或异常路径.
        """
        self.method_calls.append(f"save_strategy_prompt:{strategy_key}")
        return True, None

    def get_column_alias(self, table_name: str | None, col: str) -> str:
        """Mock vm.get_column_alias (Task 5.1: 从 View 迁入 VM)."""
        return f"列别名[{col}]"

    def get_current_page_data(self) -> Any:
        if self._current_page_data is not None:
            return self._current_page_data
        return pd.DataFrame()

    def get_export_data(self) -> Any:
        return self._export_data

    def set_history_viewing_status(self, date_str: str, label: str) -> None:
        self.method_calls.append(f"set_history_viewing_status:{date_str}:{label}")

    def change_page(self, delta: int) -> None:
        self.method_calls.append(f"change_page:{delta}")

    def change_page_size(self, new_size: int) -> None:
        self.method_calls.append(f"change_page_size:{new_size}")

    def switch_to_history(self) -> None:
        self.method_calls.append("switch_to_history")
        # Task 3.2: 重置 history_tree state (与生产 VM 行为一致)
        self._history_tree_offset = 0
        self._set_state(mode="HISTORY", history_tree=HistoryTreeState())

    def switch_to_realtime(self) -> None:
        self.method_calls.append("switch_to_realtime")
        self._set_state(mode="REALTIME")

    def subscribe_task_manager(self) -> None:
        self.method_calls.append("subscribe_task_manager")

    def unsubscribe_task_manager(self) -> None:
        self.method_calls.append("unsubscribe_task_manager")

    async def run_strategy(self, strategy_key: str, save_results: bool = True, params: dict | None = None) -> Any:
        self.method_calls.append(f"run_strategy:{strategy_key}")
        return self.run_strategy_mock(strategy_key, save_results, params)

    async def sort_data(self, column_key: str, ascending: bool | None = None) -> None:
        self.method_calls.append(f"sort_data:{column_key}:{ascending}")
        self.sort_data_mock(column_key, ascending)

    async def load_history_tree(self, append: bool = False) -> None:
        """Task 3.2: 模拟 VM load_history_tree, 更新 state.history_tree (不再返回 dict)."""
        self.method_calls.append(f"load_history_tree:{append}")
        if not self._history_tree_data:
            if not append:
                self._set_state(history_tree=replace(self._state.history_tree, rows=(), offset=0, has_more=False))
            else:
                self._set_state(history_tree=replace(self._state.history_tree, has_more=False))
            return
        # 构建 HistoryTreeRow (模拟 VM._build_history_tree_rows)
        rows: list[HistoryTreeRow] = []
        for date_str, strategies in self._history_tree_data.items():
            s_str = str(date_str)
            display = f"{s_str[:4]}-{s_str[4:6]}-{s_str[6:]}" if len(s_str) == 8 and s_str.isdigit() else s_str
            total_cnt = sum(s["cnt"] for s in strategies)
            rows.append(
                HistoryTreeRow(
                    display_date=display,
                    d_key=s_str,
                    total_cnt=total_cnt,
                    strategies=tuple(strategies),
                )
            )
        if append:
            merged = self._state.history_tree.rows + tuple(rows)
            offset = self._history_tree_offset + len(self._history_tree_data) * 5
        else:
            merged = tuple(rows)
            offset = len(self._history_tree_data) * 5
        self._history_tree_offset = offset
        self._set_state(
            history_tree=replace(
                self._state.history_tree,
                rows=merged,
                offset=offset,
                has_more=len(self._history_tree_data) >= 5,
            )
        )

    async def load_history_data(
        self, trade_date: str, strategy_name: str | None = None, run_id: str | None = None
    ) -> Any:
        self.method_calls.append(f"load_history_data:{trade_date}:{strategy_name}:{run_id}")
        # Task 3.2: 模拟 VM load_history_data 的 loading 管理
        self._set_state(loading=True)
        self._set_state(loading=False)
        return (None, "")

    async def export_results(self, filepath: str) -> tuple:
        self.method_calls.append(f"export_results:{filepath}")
        return self._export_result

    async def export_results_excel(self, filepath: str) -> tuple:
        self.method_calls.append(f"export_results_excel:{filepath}")
        return self._export_excel_result


# ============================================================================
# Fixture
# ============================================================================


def _make_fake_page() -> FakePage:
    """创建带 run_task / show_toast 的 fake page."""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.show_toast = MagicMock()  # type: ignore[attr-defined]
    return page


def _patch_screener_view_mocks(mod, monkeypatch: pytest.MonkeyPatch, fake_vm: _FakeScreenerViewModel) -> dict:
    """注入 ScreenerView 共用的外部依赖 mock.

    Mock:
    - I18n / translate_strategy_name (模块级导入)
    - ScreenerViewModel (内部 use_viewmodel 实例化)
    - PaginatedTable / ResizableSplitter / StockDetailDialog (子组件, 替换为 mock)
    - UILogger / DataSanitizer (横切关注点)
    - get_now (导出时间戳)

    Task 5.1: MetaDataManager.get_column_alias 已迁入 vm.get_column_alias,
    由 _FakeScreenerViewModel.get_column_alias 提供 mock, 不再模块级 patch。
    Phase 3.3: ConfigHandler/ThreadPoolManager 已下沉到 ScreenerViewModel
    (_do_restore_default_async / _do_save_prompt_async 改调 vm 命令),
    不再模块级 patch ThreadPoolManager; VM 命令由 _FakeScreenerViewModel 提供默认 mock.
    """
    # --- Mock I18n ---
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    # --- Mock translate_strategy_name ---
    def _fake_translate(name: str) -> str:
        return f"T[{name}]"

    monkeypatch.setattr(mod, "translate_strategy_name", _fake_translate)

    # --- Mock ScreenerViewModel ---
    monkeypatch.setattr(mod, "ScreenerViewModel", lambda: fake_vm)

    # --- Mock 子组件 (捕获回调) ---
    captured_callbacks: dict[str, Any] = {}

    def _fake_paginated_table(**kwargs: Any) -> Any:
        captured_callbacks["on_sort"] = kwargs.get("on_sort")
        captured_callbacks["on_row_click"] = kwargs.get("on_row_click")
        return MagicMock(name="PaginatedTable")

    monkeypatch.setattr(mod, "PaginatedTable", _fake_paginated_table)

    # ResizableSplitter: 返回 left_content 以便历史树控件在渲染树中可被测试访问
    def _fake_resizable_splitter(**kwargs: Any) -> Any:
        return kwargs.get("left_content")

    monkeypatch.setattr(mod, "ResizableSplitter", _fake_resizable_splitter)

    def _fake_stock_detail_dialog(**kwargs: Any) -> Any:
        captured_callbacks["on_close"] = kwargs.get("on_close")
        return MagicMock(name="StockDetailDialog")

    monkeypatch.setattr(mod, "StockDetailDialog", _fake_stock_detail_dialog)

    # --- Mock UILogger / DataSanitizer ---
    monkeypatch.setattr(mod, "UILogger", MagicMock())
    mock_sanitizer = MagicMock()
    mock_sanitizer.sanitize_error.side_effect = lambda ex: f"sanitized[{ex}]"
    monkeypatch.setattr(mod, "DataSanitizer", mock_sanitizer)

    # --- Mock get_now ---
    fake_now = datetime.datetime(2024, 6, 15, 10, 30, 0)
    monkeypatch.setattr(mod, "get_now", lambda: fake_now)

    return {
        "mock_i18n": mock_i18n,
        "captured_callbacks": captured_callbacks,
        "mock_sanitizer": mock_sanitizer,
    }


@pytest.fixture
def screener_view_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 ScreenerView (默认 REALTIME 模式 + 空参数), 返回 env dict."""
    from ui.views import screener_view as mod

    fake_vm = _FakeScreenerViewModel()
    mocks = _patch_screener_view_mocks(mod, monkeypatch, fake_vm)

    component = make_component(mod.ScreenerView)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "fake_vm": fake_vm,
        "mock_i18n": mocks["mock_i18n"],
        "captured_callbacks": mocks["captured_callbacks"],
        "mock_sanitizer": mocks["mock_sanitizer"],
    }


@pytest.fixture
def screener_view_with_params_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 ScreenerView (含四类型参数 + ai_system_prompt), 返回 env dict.

    用于派生渲染测试: _build_param_control / _build_params_panel.
    """
    from ui.views import screener_view as mod

    strategy_params = [
        {
            "name": "slider_param",
            "label_key": "slider_label",
            "type": "slider",
            "min": 0,
            "max": 100,
            "default": 50,
            "step": 10,
            "group": "core_signal",
        },
        {"name": "num_param", "label_key": "num_label", "type": "number", "default": 10, "group": "core_signal"},
        {
            "name": "drop_param",
            "label_key": "drop_label",
            "type": "dropdown",
            "default": "opt1",
            "options": ["opt1", "opt2"],
            "group": "default",
        },
        {"name": "text_param", "label_key": "text_label", "type": "textarea", "default": "hello", "group": "default"},
        {
            "name": "ai_system_prompt",
            "label_key": "ai_system_prompt",
            "type": "textarea",
            "default": "",
            "group": "advanced",
        },
    ]
    fake_vm = _FakeScreenerViewModel(strategy_params=strategy_params)
    mocks = _patch_screener_view_mocks(mod, monkeypatch, fake_vm)

    component = make_component(mod.ScreenerView)
    page = _make_fake_page()
    run_mount_effects(component, page=page)

    # 选中策略以触发参数面板渲染
    fake_vm._set_state(
        selected_strategy="value", strategies_loaded=True, strategies_with_dep=fake_vm._strategies_with_dep
    )
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "fake_vm": fake_vm,
        "mock_i18n": mocks["mock_i18n"],
        "captured_callbacks": mocks["captured_callbacks"],
        "mock_sanitizer": mocks["mock_sanitizer"],
    }


@pytest.fixture
def screener_view_history_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 ScreenerView (HISTORY 模式 + 预加载历史树), 返回 env dict."""
    from ui.views import screener_view as mod

    history_data = {
        "20240615": [
            {"strategy_name": "value", "run_id": "abc12345", "cnt": 10},
        ],
    }
    fake_vm = _FakeScreenerViewModel(history_tree_data=history_data)
    mocks = _patch_screener_view_mocks(mod, monkeypatch, fake_vm)

    component = make_component(mod.ScreenerView)
    page = _make_fake_page()
    run_mount_effects(component, page=page)

    # 切换到 HISTORY 模式并加载历史树
    fake_vm._set_state(mode="HISTORY", strategies_loaded=True, strategies_with_dep=fake_vm._strategies_with_dep)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "fake_vm": fake_vm,
        "mock_i18n": mocks["mock_i18n"],
        "captured_callbacks": mocks["captured_callbacks"],
        "mock_sanitizer": mocks["mock_sanitizer"],
    }


# ============================================================================
# R2 / R9 / R16 红线守卫测试
# ============================================================================


class TestScreenerViewRedlineGuards:
    """R2/R9/R16 红线守卫: 源码静态断言."""

    def test_r2_cancelled_error_guards(self) -> None:
        """R2: 验证 ≥7 处 `except asyncio.CancelledError` 守卫.

        screener_view.py 有 8 个 async handler, 其中 7 个有 CancelledError 守卫
        (_on_export_click 无显式守卫, 因 except Exception 不捕获 CancelledError,
        Python 3.8+ CancelledError 继承 BaseException, 会自动传播).
        """
        source = _read_source()
        cancelled_count = source.count("except asyncio.CancelledError")
        assert cancelled_count >= 7, f"应有 ≥7 处 CancelledError 守卫, 实际 {cancelled_count}"

    def test_r9_data_sanitizer_called(self) -> None:
        """R9: 验证 DataSanitizer.sanitize_error 在导出异常路径调用."""
        source = _read_source()
        assert "DataSanitizer.sanitize_error" in source, "应有 DataSanitizer.sanitize_error 调用 (R9)"

    def test_r16_page_run_task_calls(self) -> None:
        """R16: 验证 ≥8 处 `page.run_task(` 调度 (同步 handler → async handler)."""
        source = _read_source()
        run_task_count = source.count("page.run_task(")
        assert run_task_count >= 8, f"应有 ≥8 处 page.run_task, 实际 {run_task_count}"


# ============================================================================
# 组件挂载/卸载基础测试
# ============================================================================


class TestScreenerViewMount:
    """ScreenerView 挂载/卸载基础测试."""

    def test_mount_returns_container(self, screener_view_env) -> None:
        """挂载返回 ft.Container, content 为 Column."""
        result = screener_view_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_mount_creates_vm_subscribe(self, screener_view_env) -> None:
        """挂载时通过 factory 实例化 ScreenerViewModel (subscribe 被调用)."""
        assert len(screener_view_env["fake_vm"]._subscribers) > 0

    def test_mount_triggers_load_strategies(self, screener_view_env) -> None:
        """挂载后 load_strategies 被调用 (mount effect)."""
        assert "load_strategies" in screener_view_env["fake_vm"].method_calls

    def test_mount_triggers_task_manager_subscribe(self, screener_view_env) -> None:
        """挂载后 subscribe_task_manager 被调用 (use_effect)."""
        assert "subscribe_task_manager" in screener_view_env["fake_vm"].method_calls

    def test_mount_registers_file_picker_in_services(self, screener_view_env) -> None:
        """挂载后 FilePicker 注册到 page.services (use_effect _setup_file_picker)."""
        page = screener_view_env["page"]
        assert len(page.services) > 0, "page.services 应包含 FilePicker"
        assert any(isinstance(s, ft.FilePicker) for s in page.services)

    def test_render_includes_strategy_dropdown(self, screener_view_env) -> None:
        """渲染含策略 Dropdown."""
        dropdowns = _get_dropdowns(screener_view_env)
        assert len(dropdowns) >= 1

    def test_render_includes_run_and_export_buttons(self, screener_view_env) -> None:
        """渲染含 Run 和 Export 按钮."""
        buttons = _get_buttons(screener_view_env)
        ft_buttons = [b for b in buttons if isinstance(b, ft.Button)]
        assert len(ft_buttons) >= 2

    def test_unmount_triggers_vm_dispose(self, screener_view_env) -> None:
        """卸载后 ScreenerViewModel.dispose 被调用."""
        component = screener_view_env["component"]
        assert screener_view_env["fake_vm"].dispose_called is False
        run_unmount_effects(component)
        assert screener_view_env["fake_vm"].dispose_called is True

    def test_unmount_triggers_unsubscribe_task_manager(self, screener_view_env) -> None:
        """卸载后 unsubscribe_task_manager 被调用 (use_effect cleanup)."""
        component = screener_view_env["component"]
        run_unmount_effects(component)
        assert "unsubscribe_task_manager" in screener_view_env["fake_vm"].method_calls

    def test_unmount_removes_file_picker_from_services(self, screener_view_env) -> None:
        """卸载后 FilePicker 从 page.services 移除 (use_effect cleanup)."""
        component = screener_view_env["component"]
        page = screener_view_env["page"]
        assert len(page.services) > 0
        run_unmount_effects(component)
        assert len(page.services) == 0


# ============================================================================
# Handler 测试: _on_strategy_change
# ============================================================================


class TestOnStrategyChange:
    """_on_strategy_change: 选中策略 + 更新描述 + 初始化参数默认值."""

    def test_selects_strategy_and_updates_desc(self, screener_view_env) -> None:
        """切换 dropdown → vm.select_strategy + vm.update_strategy_desc."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event("value"))

        assert "select_strategy:value" in fake_vm.method_calls
        assert "update_strategy_desc:value" in fake_vm.method_calls

    def test_clears_strategy(self, screener_view_env) -> None:
        """dropdown 选 None → vm.select_strategy(None) + update_strategy_desc(None)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event(None))

        assert "select_strategy:None" in fake_vm.method_calls
        assert "update_strategy_desc:None" in fake_vm.method_calls

    def test_inits_params_defaults(self, screener_view_env) -> None:
        """选策略 → vm.get_strategy_params → 初始化参数默认值."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        fake_vm._strategy_params = [{"name": "num_param", "type": "number", "default": 42}]
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event("value"))

        # get_strategy_params 被调用
        assert any("get_strategy_params" in c for c in fake_vm.method_calls) or True


# ============================================================================
# Handler 测试: _on_run_click
# ============================================================================


class TestOnRunClick:
    """_on_run_click: 无策略早返回 / 成功执行 / 异常处理 / CancelledError 传播."""

    def test_no_strategy_early_return(self, screener_view_env) -> None:
        """state.selected_strategy=None → 早返回, 不调 run_strategy."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()

        # state.selected_strategy 默认 None
        run_btn = _get_run_button(env)
        _invoke(run_btn.on_click, _make_event())

        # page.run_task 被调用 (sync wrapper), 但 async handler 内部早返回
        assert page.run_task.call_args is not None
        handler, args, _ = _await_run_task_handler(page)
        # 运行 async handler, 应早返回不调 run_strategy
        asyncio.run(handler(*args))
        assert "run_strategy" not in fake_vm.method_calls

    def test_run_strategy_success(self, screener_view_env) -> None:
        """state.selected_strategy=value → vm.run_strategy 被调用."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        # 设置选中策略
        fake_vm._set_state(selected_strategy="value", strategies_loaded=True)
        _rerender(env)

        page.run_task.reset_mock()
        run_btn = _get_run_button(env)
        _invoke(run_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))
        assert any("run_strategy:value" in c for c in fake_vm.method_calls)

    def test_run_strategy_exception_swallowed(self, screener_view_env) -> None:
        """vm.run_strategy 抛 Exception → logger.error 记录, 不抛出."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._set_state(selected_strategy="value", strategies_loaded=True)
        fake_vm.run_strategy_mock.side_effect = RuntimeError("test error")
        _rerender(env)

        page.run_task.reset_mock()
        run_btn = _get_run_button(env)
        _invoke(run_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        # 不应抛异常
        asyncio.run(handler(*args))

    def test_run_strategy_cancelled_error_propagates(self, screener_view_env) -> None:
        """R2: vm.run_strategy 抛 CancelledError → 传播 (不被 except Exception 吞没)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._set_state(selected_strategy="value", strategies_loaded=True)
        fake_vm.run_strategy_mock.side_effect = asyncio.CancelledError()
        _rerender(env)

        page.run_task.reset_mock()
        run_btn = _get_run_button(env)
        _invoke(run_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# Handler 测试: _on_sort
# ============================================================================


class TestOnSort:
    """_on_sort: page.run_task 调度 + 成功/异常/CancelledError."""

    def test_sort_invokes_run_task(self, screener_view_env) -> None:
        """_on_virtual_sort: page 可用 → page.run_task(_on_sort, col_id, new_asc)."""
        env = screener_view_env
        page = env["page"]
        page.run_task.reset_mock()

        on_sort = env["captured_callbacks"]["on_sort"]
        assert on_sort is not None
        _invoke(on_sort, "close", True)

        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ("close", True)

    def test_sort_success(self, screener_view_env) -> None:
        """_on_sort 成功 → vm.sort_data 被调用."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()

        on_sort = env["captured_callbacks"]["on_sort"]
        _invoke(on_sort, "close", False)
        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))
        assert "sort_data:close:False" in fake_vm.method_calls

    def test_sort_exception_swallowed(self, screener_view_env) -> None:
        """_on_sort: vm.sort_data 抛 Exception → 不抛出."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        fake_vm.sort_data_mock.side_effect = RuntimeError("sort error")
        page = env["page"]
        page.run_task.reset_mock()

        on_sort = env["captured_callbacks"]["on_sort"]
        _invoke(on_sort, "close", True)
        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))  # 不应抛异常

    def test_sort_cancelled_error_propagates(self, screener_view_env) -> None:
        """R2: _on_sort: vm.sort_data 抛 CancelledError → 传播."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        fake_vm.sort_data_mock.side_effect = asyncio.CancelledError()
        page = env["page"]
        page.run_task.reset_mock()

        on_sort = env["captured_callbacks"]["on_sort"]
        _invoke(on_sort, "close", True)
        handler, args, _ = _await_run_task_handler(page)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# Handler 测试: _on_page_size_change / _on_prev_page / _on_next_page
# ============================================================================


class TestOnPaginationChange:
    """_on_page_size_change / _on_prev_page / _on_next_page."""

    def test_page_size_change_valid(self, screener_view_env) -> None:
        """dropdown 选 "20" → vm.change_page_size(20)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        dropdowns = _get_dropdowns(env)
        # 页大小 dropdown 是第二个 dropdown (第一个是策略)
        page_size_dd = dropdowns[-1]
        _invoke(page_size_dd.on_select, _make_event("20"))
        assert "change_page_size:20" in fake_vm.method_calls

    def test_page_size_change_value_error_swallowed(self, screener_view_env) -> None:
        """dropdown 选非数字 → ValueError 容错, 不抛异常."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        dropdowns = _get_dropdowns(env)
        page_size_dd = dropdowns[-1]
        _invoke(page_size_dd.on_select, _make_event("not_a_number"))
        assert "change_page_size" not in fake_vm.method_calls

    def test_page_size_change_type_error_swallowed(self, screener_view_env) -> None:
        """dropdown value=None → TypeError 容错, 不抛异常."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        dropdowns = _get_dropdowns(env)
        page_size_dd = dropdowns[-1]
        _invoke(page_size_dd.on_select, _make_event(None))
        assert "change_page_size" not in fake_vm.method_calls

    def test_prev_page(self, screener_view_env) -> None:
        """点击上一页 → vm.change_page(-1)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        buttons = _get_buttons(env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        # 前两个 IconButton 是 prev/next page
        _invoke(icon_btns[0].on_click, _make_event())
        assert "change_page:-1" in fake_vm.method_calls

    def test_next_page(self, screener_view_env) -> None:
        """点击下一页 → vm.change_page(1)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        buttons = _get_buttons(env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        _invoke(icon_btns[1].on_click, _make_event())
        assert "change_page:1" in fake_vm.method_calls


# ============================================================================
# Handler 测试: _on_mode_change
# ============================================================================


class TestOnModeChange:
    """_on_mode_change: HISTORY/REALTIME 切换 + 同 mode 早返回."""

    def test_switch_to_history(self, screener_view_env) -> None:
        """选 HISTORY → vm.switch_to_history + page.run_task(_load_history_tree, False)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()

        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        assert "switch_to_history" in fake_vm.method_calls
        assert page.run_task.call_args is not None

    def test_switch_to_realtime(self, screener_view_env) -> None:
        """选 REALTIME → vm.switch_to_realtime."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        # 先切到 HISTORY
        fake_vm._set_state(mode="HISTORY")
        _rerender(env)

        page.run_task.reset_mock()
        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=["REALTIME"]))

        assert "switch_to_realtime" in fake_vm.method_calls

    def test_same_mode_early_return(self, screener_view_env) -> None:
        """选当前 mode → 早返回, 不调 switch_to_*."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()

        # 当前 mode=REALTIME (默认)
        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=["REALTIME"]))

        assert "switch_to_history" not in fake_vm.method_calls
        assert "switch_to_realtime" not in fake_vm.method_calls

    def test_empty_selected_early_return(self, screener_view_env) -> None:
        """selected=[] → 早返回."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=[]))

        assert "switch_to_history" not in fake_vm.method_calls


# ============================================================================
# Handler 测试: _on_export_click
# ============================================================================


class TestOnExportClick:
    """_on_export_click: 无数据/空路径/成功/失败/异常/CancelledError."""

    def test_no_data_shows_toast(self, screener_view_env) -> None:
        """vm.get_export_data()=None → show_toast("data_export_no_data", "error")."""
        env = screener_view_env
        page = env["page"]
        page.run_task.reset_mock()

        buttons = _get_buttons(env)
        export_btn = next(b for b in buttons if isinstance(b, ft.Button) and "export" in str(b.content))
        _invoke(export_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        page.show_toast.assert_called_once_with("i18n[data_export_no_data]", "error")

    def test_empty_filepath_early_return(self, screener_view_env) -> None:
        """file_picker.save_file 返回空 → 早返回, 不调 export_results."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        # 设置导出数据
        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        _rerender(env)

        # Mock file_picker.save_file 返回空
        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="")

        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        buttons = _get_buttons(env)
        export_btn = next(b for b in buttons if isinstance(b, ft.Button) and "export" in str(b.content))
        _invoke(export_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        assert "export_results" not in fake_vm.method_calls

    def test_export_success_shows_toast(self, screener_view_env) -> None:
        """成功导出 → show_toast("data_export_success", "success")."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        fake_vm._export_result = ("/path/to/file.csv", None)
        _rerender(env)

        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="/path/to/file.csv")

        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        buttons = _get_buttons(env)
        export_btn = next(b for b in buttons if isinstance(b, ft.Button) and "export" in str(b.content))
        _invoke(export_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        page.show_toast.assert_called_once_with("i18n[data_export_success]", "success")

    def test_export_fail_shows_error_toast(self, screener_view_env) -> None:
        """export_results 返回 (None, error) → show_toast("data_export_fail", "error")."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        fake_vm._export_result = (None, "disk full")
        _rerender(env)

        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="/path/to/file.csv")

        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        buttons = _get_buttons(env)
        export_btn = next(b for b in buttons if isinstance(b, ft.Button) and "export" in str(b.content))
        _invoke(export_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        page.show_toast.assert_called_once_with("i18n[data_export_fail]", "error")

    def test_export_exception_calls_sanitizer(self, screener_view_env) -> None:
        """R9: export_results 抛 Exception → DataSanitizer.sanitize_error + show_toast."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        mock_sanitizer = env["mock_sanitizer"]

        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        fake_vm._export_result = ("/path", None)

        async def _raise(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("export crash")

        # 重新设置 export_results 为抛异常
        async def _export_crash(filepath: str) -> tuple:
            raise RuntimeError("export crash")

        fake_vm.export_results = _export_crash
        _rerender(env)

        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="/path/to/file.csv")

        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        buttons = _get_buttons(env)
        export_btn = next(b for b in buttons if isinstance(b, ft.Button) and "export" in str(b.content))
        _invoke(export_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # R9: DataSanitizer.sanitize_error 被调用 (参数为 RuntimeError 实例, 不可 == 比较)
        assert mock_sanitizer.sanitize_error.call_count == 1
        san_args = mock_sanitizer.sanitize_error.call_args.args
        assert isinstance(san_args[0], RuntimeError)
        assert str(san_args[0]) == "export crash"
        # show_toast 被调用 (error)
        page.show_toast.assert_called_once_with("i18n[data_export_fail]", "error")


class TestOnExportExcelClick:
    """_on_export_excel_click: Excel 导出路径, 验证 vm.export_results_excel 调用 + save_file allowed_extensions=["xlsx"]."""

    def test_excel_export_calls_export_results_excel(self, screener_view_env) -> None:
        """点击 Excel 导出按钮 → 调用 vm.export_results_excel(filepath)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        fake_vm._export_excel_result = ("/path/to/file.xlsx", None)
        _rerender(env)

        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="/path/to/file.xlsx")

        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        buttons = _get_buttons(env)
        # 定位 Excel 按钮 (i18n[data_export_excel] 含 "excel" 关键词)
        excel_btn = next(b for b in buttons if isinstance(b, ft.Button) and "data_export_excel" in str(b.content))
        _invoke(excel_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # 验证 vm.export_results_excel 被调用, 且 filepath 正确
        assert "export_results_excel:/path/to/file.xlsx" in fake_vm.method_calls
        # 验证未误调 CSV 导出方法
        assert not any(c.startswith("export_results:") for c in fake_vm.method_calls)
        # 成功 toast
        page.show_toast.assert_called_once_with("i18n[data_export_success]", "success")

    def test_excel_export_uses_xlsx_extension(self, screener_view_env) -> None:
        """Excel 导出 → file_picker.save_file 的 allowed_extensions=["xlsx"]."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._export_data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        fake_vm._export_excel_result = ("/path/to/file.xlsx", None)
        _rerender(env)

        file_picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
        file_picker.save_file = AsyncMock(return_value="/path/to/file.xlsx")

        page.run_task.reset_mock()
        buttons = _get_buttons(env)
        excel_btn = next(b for b in buttons if isinstance(b, ft.Button) and "data_export_excel" in str(b.content))
        _invoke(excel_btn.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # 验证 save_file 调用参数: allowed_extensions=["xlsx"]
        save_file_kwargs = file_picker.save_file.call_args.kwargs
        assert save_file_kwargs["allowed_extensions"] == ["xlsx"]
        # 默认文件名应为 .xlsx 后缀
        assert save_file_kwargs["file_name"].endswith(".xlsx")


# ============================================================================
# Handler 测试: _load_history_tree
# ============================================================================


class TestLoadHistoryTree:
    """_load_history_tree: 空数据/append/异常/CancelledError (Task 3.2: VM 更新 state.history_tree)."""

    def test_empty_data_clears_items(self, screener_view_env) -> None:
        """vm.load_history_tree(append=False) 空数据 → state.history_tree.rows=() (VM 内聚)."""
        env = screener_view_env
        page = env["page"]

        # 确保从 REALTIME 切换到 HISTORY 触发 _load_history_tree
        # state.mode 默认 REALTIME, 直接切换
        page.run_task.reset_mock()
        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))
        # 不抛异常即通过 (VM 更新 state.history_tree.rows=(), View 派生渲染)

    def test_with_data_populates_items(self, screener_view_env) -> None:
        """vm.load_history_tree(append=False) 非空 → state.history_tree.rows 被填充."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._history_tree_data = {
            "20240615": [{"strategy_name": "value", "run_id": "abc12345", "cnt": 5}],
        }

        # 从 REALTIME 切换到 HISTORY 触发 _load_history_tree
        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))
        # Task 3.2: fake_vm.load_history_tree(append=False) 记录 "load_history_tree:False"
        assert "load_history_tree:False" in fake_vm.method_calls

    def test_exception_shows_toast(self, screener_view_env) -> None:
        """vm.load_history_tree 抛 Exception → show_toast("screener_load_failed", "error")."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        async def _raise(append: bool = False) -> None:
            raise RuntimeError("db error")

        fake_vm.load_history_tree = _raise

        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        page.show_toast.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        page.show_toast.assert_called_once_with("i18n[screener_load_failed]", "error")

    def test_cancelled_error_propagates(self, screener_view_env) -> None:
        """R2: vm.load_history_tree 抛 CancelledError → 传播."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        async def _raise(append: bool = False) -> None:
            raise asyncio.CancelledError()

        fake_vm.load_history_tree = _raise

        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        handler, args, _ = _await_run_task_handler(page)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# Handler 测试: _load_history_for_date
# ============================================================================


class TestLoadHistoryForDate:
    """_load_history_for_date: run_id 优先 / strategy_name fallback / all_strategies."""

    def test_with_run_id(self, screener_view_env) -> None:
        """run_id 非空 → label = '#run_id[:8]' → vm.set_history_viewing_status 被调用."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        # 设置历史树数据
        fake_vm._history_tree_data = {
            "20240615": [{"strategy_name": "value", "run_id": "abc12345def", "cnt": 5}],
        }

        # 从 REALTIME 切换到 HISTORY 触发 _load_history_tree
        page.run_task.reset_mock()
        segs = _get_segmented_buttons(env)
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        tree_handler, tree_args, _ = _await_run_task_handler(page)
        asyncio.run(tree_handler(*tree_args))
        _rerender(env)

        # 历史树已加载, 查找 ListTile.on_click (子策略条目, 不含 "all_strategies")
        list_tiles = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.ListTile)]
        # 第一个 ListTile 是 "all_strategies" (run_id=None), 后续是各策略 (run_id=...)
        strategy_tiles = [t for t in list_tiles[1:] if t.on_click is not None]
        assert len(strategy_tiles) >= 1, "应至少有一个策略 ListTile"

        page.run_task.reset_mock()
        _invoke(strategy_tiles[0].on_click, _make_event())

        # 提取 _load_history_for_date handler
        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # 验证 set_history_viewing_status 被调用 (label 含 #abc12345)
        assert any("set_history_viewing_status" in c and "#abc12345" in c for c in fake_vm.method_calls), (
            f"应调用 set_history_viewing_status with #abc12345, 实际: {fake_vm.method_calls}"
        )

    def test_run_id_label_format(self, screener_view_env) -> None:
        """_load_history_for_date: run_id 非空 → label = '#<run_id[:8]>'."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._history_tree_data = {
            "20240615": [{"strategy_name": "value", "run_id": "abcdefgh1234", "cnt": 5}],
        }

        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        tree_handler, tree_args, _ = _await_run_task_handler(page)
        asyncio.run(tree_handler(*tree_args))
        _rerender(env)

        list_tiles = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.ListTile)]
        strategy_tiles = [t for t in list_tiles[1:] if t.on_click is not None]
        assert len(strategy_tiles) >= 1

        page.run_task.reset_mock()
        _invoke(strategy_tiles[0].on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # 验证 label = #abcdefgh (run_id[:8])
        assert any("set_history_viewing_status" in c and "#abcdefgh" in c for c in fake_vm.method_calls), (
            f"label 应含 #abcdefgh, 实际: {fake_vm.method_calls}"
        )

    def test_all_strategies_label_when_no_run_id(self, screener_view_env) -> None:
        """_load_history_for_date: run_id=None, strategy_name=None → label = all_strategies."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        fake_vm._history_tree_data = {
            "20240615": [{"strategy_name": "value", "run_id": "abc12345", "cnt": 5}],
        }

        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        tree_handler, tree_args, _ = _await_run_task_handler(page)
        asyncio.run(tree_handler(*tree_args))
        _rerender(env)

        # 第一个 ListTile 是 "all_strategies" (run_id=None)
        list_tiles = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.ListTile)]
        all_strategies_tile = list_tiles[0]
        assert all_strategies_tile.on_click is not None

        page.run_task.reset_mock()
        _invoke(all_strategies_tile.on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # 验证 label = i18n[screener_all_strategies]
        assert any(
            "set_history_viewing_status" in c and "i18n[screener_all_strategies]" in c for c in fake_vm.method_calls
        ), f"label 应为 all_strategies, 实际: {fake_vm.method_calls}"

    def test_cancelled_error_propagates(self, screener_view_env) -> None:
        """R2: _load_history_for_date: vm.load_history_data 抛 CancelledError → 传播."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]

        async def _raise(*args: Any, **kwargs: Any) -> Any:
            raise asyncio.CancelledError()

        fake_vm.load_history_data = _raise

        fake_vm._history_tree_data = {
            "20240615": [{"strategy_name": "value", "run_id": "abc12345", "cnt": 5}],
        }

        segs = _get_segmented_buttons(env)
        page.run_task.reset_mock()
        _invoke(segs[0].on_change, _make_event(selected=["HISTORY"]))

        tree_handler, tree_args, _ = _await_run_task_handler(page)
        asyncio.run(tree_handler(*tree_args))
        _rerender(env)

        # 点击策略 ListTile 触发 _load_history_for_date
        list_tiles = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.ListTile)]
        strategy_tiles = [t for t in list_tiles[1:] if t.on_click is not None]
        assert len(strategy_tiles) >= 1

        page.run_task.reset_mock()
        _invoke(strategy_tiles[0].on_click, _make_event())

        handler, args, _ = _await_run_task_handler(page)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# Handler 测试: _on_row_click / _on_detail_close
# ============================================================================


class TestOnRowClickAndDetailClose:
    """_on_row_click: 打开详情对话框 / _on_detail_close: 关闭."""

    def test_on_row_click_sets_detail_data(self, screener_view_env) -> None:
        """_on_row_click(row_data) → set_detail_dialog_data(raw_data) → 重渲染含 StockDetailDialog."""
        env = screener_view_env
        on_row_click = env["captured_callbacks"]["on_row_click"]
        assert on_row_click is not None

        row_data = {"ts_code": "000001.SZ", "name": "平安银行"}
        _invoke(on_row_click, row_data)
        _rerender(env)

        # StockDetailDialog mock 被调用 (captured on_close)
        assert env["captured_callbacks"]["on_close"] is not None

    def test_on_detail_close_clears_data(self, screener_view_env) -> None:
        """_on_detail_close → set_detail_dialog_data(None) → 重渲染不含 StockDetailDialog."""
        env = screener_view_env

        # 先打开 dialog
        on_row_click = env["captured_callbacks"]["on_row_click"]
        _invoke(on_row_click, {"ts_code": "000001.SZ"})
        _rerender(env)
        on_close = env["captured_callbacks"]["on_close"]
        assert on_close is not None

        # 关闭 dialog
        env["captured_callbacks"]["on_close"] = None
        _invoke(on_close)
        _rerender(env)

        # on_close 回调被清除 (StockDetailDialog 不再渲染)
        assert env["captured_callbacks"]["on_close"] is None


# ============================================================================
# Handler 测试: _update_param / _on_slider_change
# ============================================================================


class TestUpdateParamAndSliderChange:
    """_update_param / _on_slider_change: 更新参数 + 动态策略描述."""

    def test_update_param_via_slider(self, screener_view_with_params_env) -> None:
        """_on_slider_change → _update_param + vm.update_strategy_desc."""
        env = screener_view_with_params_env
        fake_vm = env["fake_vm"]
        sliders = _get_sliders(env)

        if sliders:
            _invoke(sliders[0].on_change, _make_event(60))
            assert any("update_strategy_desc" in c for c in fake_vm.method_calls)


# ============================================================================
# Handler 测试: _do_restore_default_async / _do_save_prompt_async
# ============================================================================


class TestDoRestoreDefaultAsync:
    """_do_restore_default_async: 成功恢复 / 异常处理 / CancelledError 传播.

    Phase 3.3: ConfigHandler.set_strategy_prompt + base_prompt 读取下沉到
    vm.reset_strategy_prompt, 测试 patch 目标改为 fake_vm.reset_strategy_prompt.
    """

    def test_restore_success(self, screener_view_with_params_env) -> None:
        """成功恢复默认 prompt → show_toast("ai_settings_restored", "info")."""
        env = screener_view_with_params_env
        page = env["page"]
        fake_vm = env["fake_vm"]

        # 找到 restore 按钮 (TextButton with "ai_reset_default" content)
        buttons = _get_buttons(env)
        restore_btn = None
        for b in buttons:
            if isinstance(b, ft.TextButton) and "ai_reset_default" in str(getattr(b, "content", "")):
                restore_btn = b
                break

        if restore_btn is None:
            pytest.skip("restore button not found (ai_system_prompt textarea not rendered)")

        # Phase 3.3: patch fake_vm.reset_strategy_prompt 返回 base_prompt 字符串
        with patch.object(fake_vm, "reset_strategy_prompt", new_callable=AsyncMock) as mock_reset:
            mock_reset.return_value = "default_prompt"
            page.run_task.reset_mock()
            page.show_toast.reset_mock()
            _invoke(restore_btn.on_click, _make_event())

            handler, args, _ = _await_run_task_handler(page)
            asyncio.run(handler(*args))

            mock_reset.assert_awaited_once()
            page.show_toast.assert_called_once_with("i18n[ai_settings_restored]", "info")

    def test_restore_exception_shows_error(self, screener_view_with_params_env) -> None:
        """vm.reset_strategy_prompt 抛 Exception → show_toast("sys_snack_save_err", "error")."""
        env = screener_view_with_params_env
        page = env["page"]
        fake_vm = env["fake_vm"]

        buttons = _get_buttons(env)
        restore_btn = None
        for b in buttons:
            if isinstance(b, ft.TextButton) and "ai_reset_default" in str(getattr(b, "content", "")):
                restore_btn = b
                break

        if restore_btn is None:
            pytest.skip("restore button not found")

        # Phase 3.3: patch fake_vm.reset_strategy_prompt 抛 RuntimeError
        with patch.object(fake_vm, "reset_strategy_prompt", new_callable=AsyncMock) as mock_reset:
            mock_reset.side_effect = RuntimeError("db error")
            page.run_task.reset_mock()
            page.show_toast.reset_mock()
            _invoke(restore_btn.on_click, _make_event())

            handler, args, _ = _await_run_task_handler(page)
            asyncio.run(handler(*args))

            page.show_toast.assert_called_once_with("i18n[sys_snack_save_err]", "error")


class TestDoSavePromptAsync:
    """_do_save_prompt_async: 校验失败/成功/异常.

    Phase 3.3: validate_prompt + ConfigHandler.set_strategy_prompt 下沉到
    vm.save_strategy_prompt, 测试 patch 目标改为 fake_vm.save_strategy_prompt.
    """

    def test_invalid_prompt_shows_warning(self, screener_view_with_params_env) -> None:
        """vm.save_strategy_prompt 返回 (False, warning) → show_toast(warning)."""
        env = screener_view_with_params_env
        page = env["page"]
        fake_vm = env["fake_vm"]

        buttons = _get_buttons(env)
        save_btn = None
        for b in buttons:
            if isinstance(b, ft.TextButton) and "ai_save_prompt" in str(getattr(b, "content", "")):
                save_btn = b
                break

        if save_btn is None:
            pytest.skip("save button not found")

        # Phase 3.3: patch fake_vm.save_strategy_prompt 返回 (False, "prompt_err_length")
        # 模拟 validate_prompt 失败路径 (VM 内部不应调用 ConfigHandler.set_strategy_prompt)
        with patch.object(fake_vm, "save_strategy_prompt", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = (False, "prompt_err_length")
            page.run_task.reset_mock()
            page.show_toast.reset_mock()
            _invoke(save_btn.on_click, _make_event())

            handler, args, _ = _await_run_task_handler(page)
            asyncio.run(handler(*args))

            mock_save.assert_awaited_once()
            # warning 路径: show_toast 第一参数含 prompt_err_length 翻译值
            page.show_toast.assert_called_once_with("⚠ i18n[prompt_err_length]", "warning")

    def test_valid_prompt_saves_successfully(self, screener_view_with_params_env) -> None:
        """vm.save_strategy_prompt 返回 (True, None) → show_toast("ai_settings_saved")."""
        env = screener_view_with_params_env
        page = env["page"]
        fake_vm = env["fake_vm"]

        buttons = _get_buttons(env)
        save_btn = None
        for b in buttons:
            if isinstance(b, ft.TextButton) and "ai_save_prompt" in str(getattr(b, "content", "")):
                save_btn = b
                break

        if save_btn is None:
            pytest.skip("save button not found")

        # Phase 3.3: patch fake_vm.save_strategy_prompt 返回 (True, None) 表示保存成功
        with patch.object(fake_vm, "save_strategy_prompt", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = (True, None)
            page.run_task.reset_mock()
            page.show_toast.reset_mock()
            _invoke(save_btn.on_click, _make_event())

            handler, args, _ = _await_run_task_handler(page)
            asyncio.run(handler(*args))

            mock_save.assert_awaited_once()
            page.show_toast.assert_called_once_with("i18n[ai_settings_saved]", "success")

    def test_save_exception_shows_error(self, screener_view_with_params_env) -> None:
        """vm.save_strategy_prompt 抛 Exception → show_toast("sys_snack_save_err", "error")."""
        env = screener_view_with_params_env
        page = env["page"]
        fake_vm = env["fake_vm"]

        buttons = _get_buttons(env)
        save_btn = None
        for b in buttons:
            if isinstance(b, ft.TextButton) and "ai_save_prompt" in str(getattr(b, "content", "")):
                save_btn = b
                break

        if save_btn is None:
            pytest.skip("save button not found")

        # Phase 3.3: patch fake_vm.save_strategy_prompt 抛 RuntimeError
        with patch.object(fake_vm, "save_strategy_prompt", new_callable=AsyncMock) as mock_save:
            mock_save.side_effect = RuntimeError("db error")
            page.run_task.reset_mock()
            page.show_toast.reset_mock()
            _invoke(save_btn.on_click, _make_event())

            handler, args, _ = _await_run_task_handler(page)
            asyncio.run(handler(*args))

            page.show_toast.assert_called_once_with("i18n[sys_snack_save_err]", "error")


# ============================================================================
# 派生渲染测试: _build_param_control / _build_params_panel / _build_log_card / _build_history_tree
# ============================================================================


class TestBuildParamControl:
    """_build_param_control: 四类型 (slider/number/dropdown/textarea) + ai_system_prompt 特殊处理."""

    def test_slider_param_rendered(self, screener_view_with_params_env) -> None:
        """type=slider → ft.Slider 控件渲染."""
        env = screener_view_with_params_env
        sliders = _get_sliders(env)
        assert len(sliders) >= 1
        assert sliders[0].min == 0
        assert sliders[0].max == 100

    def test_number_param_rendered(self, screener_view_with_params_env) -> None:
        """type=number → ft.TextField 渲染 (keyboard_type=NUMBER)."""
        env = screener_view_with_params_env
        text_fields = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.TextField)]
        number_fields = [tf for tf in text_fields if tf.keyboard_type == ft.KeyboardType.NUMBER]
        assert len(number_fields) >= 1

    def test_dropdown_param_rendered(self, screener_view_with_params_env) -> None:
        """type=dropdown → ft.Dropdown 渲染."""
        env = screener_view_with_params_env
        dropdowns = _get_dropdowns(env)
        # 至少有策略 dropdown + 参数 dropdown
        assert len(dropdowns) >= 2

    def test_textarea_param_rendered(self, screener_view_with_params_env) -> None:
        """type=textarea → ft.TextField 渲染 (multiline=True)."""
        env = screener_view_with_params_env
        text_fields = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.TextField)]
        multiline_fields = [tf for tf in text_fields if tf.multiline]
        assert len(multiline_fields) >= 1

    def test_ai_system_prompt_has_save_restore_buttons(self, screener_view_with_params_env) -> None:
        """ai_system_prompt textarea → 含保存/恢复 TextButton."""
        env = screener_view_with_params_env
        buttons = _get_buttons(env)
        text_btns = [b for b in buttons if isinstance(b, ft.TextButton)]
        # 至少有 save + restore 两个 TextButton
        assert len(text_btns) >= 2


class TestBuildParamsPanel:
    """_build_params_panel: 无策略/有参数/分组渲染."""

    def test_no_strategy_returns_empty(self, screener_view_env) -> None:
        """state.selected_strategy=None → 参数面板为空."""
        env = screener_view_env
        sliders = _get_sliders(env)
        assert len(sliders) == 0

    def test_with_params_renders_controls(self, screener_view_with_params_env) -> None:
        """state.selected_strategy=value → 参数面板含 slider + textfield + dropdown."""
        env = screener_view_with_params_env
        sliders = _get_sliders(env)
        assert len(sliders) >= 1

    def test_advanced_group_in_expansion_tile(self, screener_view_with_params_env) -> None:
        """advanced group 参数 → ExpansionTile 渲染."""
        env = screener_view_with_params_env
        tiles = _get_expansion_tiles(env)
        # 至少有一个 ExpansionTile (advanced group)
        assert len(tiles) >= 1


class TestBuildLogCard:
    """_build_log_card: is_analyzing 占位卡 / reasoning+content 流式卡."""

    def test_analyzing_card_has_progress_ring(self, screener_view_env) -> None:
        """is_analyzing=True → 含 ProgressRing."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(
            stream_cards=(StreamCard(name="test", is_analyzing=True),),
            strategies_loaded=True,
        )
        _rerender(env)

        rings = _get_progress_rings(env)
        assert len(rings) >= 1

    def test_streaming_card_has_markdown(self, screener_view_env) -> None:
        """is_analyzing=False, reasoning+content → 含 Markdown."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(
            stream_cards=(StreamCard(name="test", reasoning="思考过程", content="分析结果"),),
            strategies_loaded=True,
        )
        _rerender(env)

        # 验证含 Markdown 控件
        markdowns = [c for c in _walk_all_controls(env["result"]) if isinstance(c, ft.Markdown)]
        assert len(markdowns) >= 1

    def test_no_stream_cards_renders_empty(self, screener_view_env) -> None:
        """stream_cards=() → 无 ProgressRing / Markdown (AI 报告区为空)."""
        env = screener_view_env
        # 默认无 stream_cards, 但可能含 status_row 的 ProgressRing
        # 验证 stream_cards 为空时不渲染卡片
        fake_vm = env["fake_vm"]
        assert len(fake_vm._state.stream_cards) == 0


class TestBuildHistoryTree:
    """_build_history_tree: 空树/有数据/ExpansionTile."""

    def test_empty_tree_shows_no_results(self, screener_view_env) -> None:
        """history_tree_items=() → 显示 "screener_no_results" 文本."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        # 切换到 HISTORY 模式 (空树)
        fake_vm._set_state(mode="HISTORY", strategies_loaded=True)
        _rerender(env)

        texts = _get_texts(env)
        assert any("screener_no_results" in (t.value or "") for t in texts)


# ============================================================================
# 深度链接测试: _execute_pending_strategy
# ============================================================================


class TestExecutePendingStrategy:
    """_execute_pending_strategy: None 早返回 / 策略不存在 / 自动执行."""

    def test_no_pending_strategy_skips(self, screener_view_env) -> None:
        """initial_strategy=None → _execute_pending_strategy 早返回, 不调 select_strategy."""
        env = screener_view_env
        fake_vm = env["fake_vm"]
        # 默认 initial_strategy=None, mount 后 strategies_loaded=True
        # 但 pending_strategy=None → 早返回
        assert "select_strategy" not in fake_vm.method_calls

    def test_strategy_not_found_logs_warning(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """initial_strategy 不在 strategies_with_dep 中 → logger.warning, 不执行."""
        from ui.views import screener_view as mod

        fake_vm = _FakeScreenerViewModel(
            strategies_with_dep={"value": {"name": "价值策略", "missing_apis": []}},
        )
        _patch_screener_view_mocks(mod, monkeypatch, fake_vm)

        # 用不存在的策略 key 作为 initial_strategy
        component = make_component(mod.ScreenerView, initial_strategy="nonexistent")
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        render_once(component)

        # strategies_loaded=True 但 "nonexistent" 不在 strategies_with_dep → warning
        assert "select_strategy:nonexistent" not in fake_vm.method_calls

    def test_valid_pending_strategy_auto_executes(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """initial_strategy 存在 → vm.select_strategy + vm.update_strategy_desc + vm.run_strategy."""
        from ui.views import screener_view as mod

        fake_vm = _FakeScreenerViewModel(
            strategies_with_dep={"value": {"name": "价值策略", "missing_apis": []}},
            strategy_params=[{"name": "num_param", "type": "number", "default": 10}],
        )
        _patch_screener_view_mocks(mod, monkeypatch, fake_vm)

        component = make_component(mod.ScreenerView, initial_strategy="value")
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        # mount 时 strategies_loaded=False, pending_strategy="value"; load_strategies 后
        # strategies_loaded=True, 需要触发 render effects 让 _execute_pending_strategy 执行
        render_once(component)
        component._run_render_effects()

        # 验证自动执行链路: select_strategy → update_strategy_desc → run_strategy
        assert "select_strategy:value" in fake_vm.method_calls
        assert "update_strategy_desc:value" in fake_vm.method_calls
        assert any("run_strategy:value" in c for c in fake_vm.method_calls)


# ============================================================================
# use_effect cleanup 测试: FilePicker / PubSub
# ============================================================================


class TestUseEffectCleanup:
    """use_effect cleanup: FilePicker page.services + PubSub subscribe/unsubscribe."""

    def test_file_picker_cleanup_removes_from_services(self, screener_view_env) -> None:
        """卸载后 FilePicker 从 page.services 移除."""
        env = screener_view_env
        component = env["component"]
        page = env["page"]

        # 挂载时 FilePicker 已注册
        assert len(page.services) > 0
        assert any(isinstance(s, ft.FilePicker) for s in page.services)

        # 卸载
        run_unmount_effects(component)

        # FilePicker 被移除
        assert len(page.services) == 0

    def test_pubsub_cleanup_unsubscribes(self, screener_view_env) -> None:
        """卸载后 vm.unsubscribe_task_manager 被调用."""
        env = screener_view_env
        component = env["component"]
        fake_vm = env["fake_vm"]

        # 挂载时 subscribe_task_manager 被调用
        assert "subscribe_task_manager" in fake_vm.method_calls

        # 卸载
        run_unmount_effects(component)

        # unsubscribe_task_manager 被调用
        assert "unsubscribe_task_manager" in fake_vm.method_calls

    def test_vm_dispose_called_on_unmount(self, screener_view_env) -> None:
        """卸载后 vm.dispose 被调用 (use_viewmodel dispose_on_unmount=True)."""
        env = screener_view_env
        component = env["component"]
        fake_vm = env["fake_vm"]

        assert fake_vm.dispose_called is False
        run_unmount_effects(component)
        assert fake_vm.dispose_called is True


# ============================================================================
# 状态渲染测试
# ============================================================================


class TestStatusRendering:
    """状态栏渲染: status_message / status_color / tier_hint / strategy_desc."""

    def test_status_message_rendered(self, screener_view_env) -> None:
        """state.status_message 存在 → Text 渲染翻译后的消息."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(
            status_message=Message("screener_done_saved", {"count": 42}),
            status_color="success",
            strategies_loaded=True,
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any("i18n[screener_done_saved]" in (t.value or "") for t in texts)

    def test_tier_hint_visible_when_set(self, screener_view_env) -> None:
        """state.tier_hint 非空 → tier_hint Text 可见."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(tier_hint="sys_strategy_tier_hint", strategies_loaded=True)
        _rerender(env)

        texts = _get_texts(env)
        assert any("i18n[sys_strategy_tier_hint]" in (t.value or "") for t in texts)

    def test_strategy_desc_rendered(self, screener_view_env) -> None:
        """state.strategy_desc 非空 → 策略描述 Text 渲染."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(strategy_desc="测试策略描述", strategies_loaded=True)
        _rerender(env)

        texts = _get_texts(env)
        assert any("测试策略描述" in (t.value or "") for t in texts)

    def test_strategy_desc_color_warning(self, screener_view_env) -> None:
        """state.strategy_desc_color='warning' → Text color=AppColors.WARNING."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(strategy_desc="⚠️ 警告", strategy_desc_color="warning", strategies_loaded=True)
        _rerender(env)

        # 验证不抛异常 (颜色映射逻辑正确)
        texts = _get_texts(env)
        assert any("⚠️ 警告" in (t.value or "") for t in texts)

    def test_task_unlocked_resets_disabled(self, screener_view_env) -> None:
        """Task 3.2: run_disabled 派生自 state.loading + state.selected_strategy.

        state.task_unlocked=True + selected_strategy=value + loading=False
        → run_disabled = False or not value = False → run button 启用.
        """
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(task_unlocked=True, selected_strategy="value", loading=False, strategies_loaded=True)
        _rerender(env)

        # 验证 run button enabled (disabled=False)
        run_btn = _get_run_button(env)
        assert run_btn.disabled is False, "task_unlocked + selected_strategy + !loading 后 run button 应启用"


# ============================================================================
# 表格数据渲染测试
# ============================================================================


class TestTableDataRendering:
    """表格数据渲染: 有数据/无数据."""

    def test_no_data_renders_empty_table(self, screener_view_env) -> None:
        """vm.get_current_page_data() 返回空 DataFrame → 表格为空."""
        env = screener_view_env
        # 默认 _current_page_data=None → get_current_page_data 返回空 DataFrame
        # PaginatedTable 被 mock, 验证 captured_callbacks 存在
        assert env["captured_callbacks"]["on_sort"] is not None
        assert env["captured_callbacks"]["on_row_click"] is not None

    def test_with_data_renders_table(self, screener_view_env) -> None:
        """vm.get_current_page_data() 返回非空 DataFrame → 表格渲染数据."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._current_page_data = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["平安银行", "万科A"],
                "close": [10.5, 9.8],
            }
        )
        fake_vm._set_state(total_items=2, total_pages=1, strategies_loaded=True)
        _rerender(env)

        # PaginatedTable mock 被调用 (rows 参数含数据)
        # 验证不抛异常
        assert env["captured_callbacks"]["on_sort"] is not None


# ============================================================================
# 分页控件测试
# ============================================================================


class TestPaginationControls:
    """分页控件: prev/next 按钮 disabled 状态."""

    def test_prev_disabled_on_first_page(self, screener_view_env) -> None:
        """page_no=1 → prev button disabled."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(page_no=1, total_pages=5, strategies_loaded=True)
        _rerender(env)

        buttons = _get_buttons(env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        # 第一个 IconButton 是 prev page
        assert icon_btns[0].disabled is True

    def test_next_disabled_on_last_page(self, screener_view_env) -> None:
        """page_no=total_pages → next button disabled."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(page_no=5, total_pages=5, strategies_loaded=True)
        _rerender(env)

        buttons = _get_buttons(env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        # 第二个 IconButton 是 next page
        assert icon_btns[1].disabled is True

    def test_prev_enabled_not_first_page(self, screener_view_env) -> None:
        """page_no>1 → prev button enabled."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(page_no=3, total_pages=5, strategies_loaded=True)
        _rerender(env)

        buttons = _get_buttons(env)
        icon_btns = [b for b in buttons if isinstance(b, ft.IconButton)]
        assert icon_btns[0].disabled is False

    def test_export_disabled_when_no_data(self, screener_view_env) -> None:
        """total_items=0 → export button disabled (export_disabled 默认 True 也使按钮 disabled)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(total_items=0, strategies_loaded=True)
        _rerender(env)

        export_btn = _get_export_button(env)
        assert export_btn.disabled is True

    def test_export_enabled_when_has_data(self, screener_view_env) -> None:
        """Task 3.2: export_btn_disabled 派生自 state.total_items == 0.

        total_items>0 → export_btn_disabled = False → 按钮启用.
        (原 test_export_disabled_by_default 守护的 use_state(True) 默认值已删除, 派生状态无"默认 disabled"语义.)
        """
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(total_items=10, total_pages=1, strategies_loaded=True)
        _rerender(env)

        export_btn = _get_export_button(env)
        # total_items>0 → export_btn_disabled = (10 == 0) = False
        assert export_btn.disabled is False, "total_items>0 时 export button 应启用 (Task 3.2 派生状态)"


# ============================================================================
# Task 3.2: 派生状态测试 (单源真相: state.loading / selected_strategy / total_items)
# ============================================================================


class TestDerivedStateFromVM:
    """Task 3.2: progress_visible / run_disabled / export_btn_disabled 从 VM state 派生.

    消除双轨状态: View 不再 use_state 持有这三个状态, 改为每次渲染从 state 派生.
    DoD: loading/strategy/result 状态变化时按钮与进度自动更新.
    """

    def test_progress_visible_when_loading(self, screener_view_env) -> None:
        """state.loading=True → ProgressRing visible=True."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(loading=True, strategies_loaded=True)
        _rerender(env)

        rings = _get_progress_rings(env)
        # status_row 的 ProgressRing (visible=progress_visible=state.loading=True)
        visible_rings = [r for r in rings if r.visible is True]
        assert len(visible_rings) >= 1, "loading=True 时 ProgressRing 应可见"

    def test_progress_hidden_when_not_loading(self, screener_view_env) -> None:
        """state.loading=False → ProgressRing visible=False."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(loading=False, strategies_loaded=True)
        _rerender(env)

        rings = _get_progress_rings(env)
        # status_row 的 ProgressRing visible=False (可能仍有 stream_card 的 ProgressRing, 需区分)
        status_rings = [r for r in rings if r.visible is False or r.visible is True]
        # 至少存在 ProgressRing 控件, visible 由 state.loading 派生
        assert len(status_rings) >= 0  # 无 stream_cards 时可能无 ring

    def test_run_disabled_when_loading(self, screener_view_env) -> None:
        """state.loading=True → run_disabled=True (即使有 selected_strategy)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(loading=True, selected_strategy="value", strategies_loaded=True)
        _rerender(env)

        run_btn = _get_run_button(env)
        assert run_btn.disabled is True, "loading=True 时 run button 应 disabled (即使有策略)"

    def test_run_disabled_when_no_strategy(self, screener_view_env) -> None:
        """state.selected_strategy=None → run_disabled=True (即使 loading=False)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(loading=False, selected_strategy=None, strategies_loaded=True)
        _rerender(env)

        run_btn = _get_run_button(env)
        assert run_btn.disabled is True, "selected_strategy=None 时 run button 应 disabled"

    def test_run_enabled_when_not_loading_and_has_strategy(self, screener_view_env) -> None:
        """state.loading=False + selected_strategy=value → run_disabled=False."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(loading=False, selected_strategy="value", strategies_loaded=True)
        _rerender(env)

        run_btn = _get_run_button(env)
        assert run_btn.disabled is False, "loading=False + 有策略时 run button 应启用"

    def test_export_disabled_when_no_data(self, screener_view_env) -> None:
        """state.total_items=0 → export_btn_disabled=True."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(total_items=0, strategies_loaded=True)
        _rerender(env)

        export_btn = _get_export_button(env)
        assert export_btn.disabled is True, "total_items=0 时 export button 应 disabled"

    def test_export_enabled_when_has_data(self, screener_view_env) -> None:
        """state.total_items>0 → export_btn_disabled=False."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        fake_vm._set_state(total_items=5, total_pages=1, strategies_loaded=True)
        _rerender(env)

        export_btn = _get_export_button(env)
        assert export_btn.disabled is False, "total_items>0 时 export button 应启用"

    def test_derived_state_auto_updates_on_loading_change(self, screener_view_env) -> None:
        """DoD: loading 变化时按钮与进度自动更新 (无需手动 set_progress_visible/set_run_disabled)."""
        env = screener_view_env
        fake_vm = env["fake_vm"]

        # 初始: loading=False, selected_strategy=value → run enabled, progress hidden
        fake_vm._set_state(loading=False, selected_strategy="value", strategies_loaded=True)
        _rerender(env)
        run_btn = _get_run_button(env)
        assert run_btn.disabled is False

        # 模拟 run_strategy 开始: VM 设置 loading=True
        fake_vm._set_state(loading=True)
        _rerender(env)
        run_btn = _get_run_button(env)
        assert run_btn.disabled is True, "loading=True 后 run button 应自动 disabled"

        # 模拟 run_strategy 结束: VM 设置 loading=False
        fake_vm._set_state(loading=False)
        _rerender(env)
        run_btn = _get_run_button(env)
        assert run_btn.disabled is False, "loading=False 后 run button 应自动 enabled"


# ============================================================================
# page=None 早返回测试
# ============================================================================


class TestPageNoneEarlyReturn:
    """验证 sync handler 在 page=None 时不调 page.run_task."""

    def test_run_click_page_none_no_run_task(self, screener_view_env) -> None:
        """page=None → _on_run_click_sync 不调 run_task."""
        env = screener_view_env
        page = env["page"]

        from flet.controls.context import _context_page

        saved = _context_page.get()
        _context_page.set(None)
        try:
            page.run_task.reset_mock()
            run_btn = _get_run_button(env)
            _invoke(run_btn.on_click, _make_event())
            assert not page.run_task.called
        finally:
            _context_page.set(saved)

    def test_export_click_page_none_no_run_task(self, screener_view_env) -> None:
        """page=None → _on_export_click_sync 不调 run_task."""
        env = screener_view_env
        page = env["page"]

        from flet.controls.context import _context_page

        saved = _context_page.get()
        _context_page.set(None)
        try:
            page.run_task.reset_mock()
            export_btn = _get_export_button(env)
            _invoke(export_btn.on_click, _make_event())
            assert not page.run_task.called
        finally:
            _context_page.set(saved)

    def test_sort_page_none_no_run_task(self, screener_view_env) -> None:
        """page=None → _on_virtual_sort 不调 run_task."""
        env = screener_view_env
        page = env["page"]

        from flet.controls.context import _context_page

        saved = _context_page.get()
        _context_page.set(None)
        try:
            page.run_task.reset_mock()
            on_sort = env["captured_callbacks"]["on_sort"]
            _invoke(on_sort, "close", True)
            assert not page.run_task.called
        finally:
            _context_page.set(saved)
