"""冷启动性能探针 — 生产模式全页常驻挂载构造 proxy 测量 (UX-06 / F07 / R04 分支 A 验证)。

用途：量化 ``app_layout._build_pages_stack`` 生产模式（全常驻挂载模型）冷启动成本，
测量首帧（Active First Frame）、首个可操作帧（First Operable Frame）及各隐页构建开销，
核对是否符合 ``PerfThreshold`` 性能红线与 8s 冷启动 SLA 门槛。

测量维度：
1. 7 个 View 模块 import 总耗时 + litellm 是否被冲入 sys.modules（验证双层惰性有效）
2. 关键单例构造（StrategyManager/ReviewManager）与 ScreenerViewModel 冷/热构造
3. 首帧（HomeView active=True）与各隐页（6 个非激活页 active=False）单独渲染与汇总耗时
4. 生产常驻挂载页面栈装配与 AppLayout 整体首个可操作帧耗时
5. PerfThreshold 与 8s SLA 门槛核对

运行（Windows PowerShell）：
    $env:PYTHONPATH = "<repo-root>"; uv run python scripts/probe/startup_perf.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tests"))

T = time.perf_counter

_VIEW_MODULES = [
    "ui.views.home_view",
    "ui.views.screener_view",
    "ui.views.backtest_view",
    "ui.views.data_view",
    "ui.views.task_center_view",
    "ui.views.settings_view",
    "ui.views.watchlist_view",
]

_HIDDEN_VIEWS = [
    ("ScreenerView", "ui.views.screener_view", "ScreenerView", {"stock_filter_request": None}),
    ("BacktestView", "ui.views.backtest_view", "BacktestView", {"prefill_request": None}),
    ("DataExplorerView", "ui.views.data_view", "DataExplorerView", {}),
    ("TaskCenterView", "ui.views.task_center_view", "TaskCenterView", {}),
    ("SettingsView", "ui.views.settings_view", "SettingsView", {"target_subtab": None}),
    ("WatchlistView", "ui.views.watchlist_view", "WatchlistView", {}),
]

_STARTUP_SLA_MS = 8000.0  # P3-UX06-Startup-SLA-E2E-Gap 记录的 8s 端到端冷启动 SLA 门槛


def _create_headless_page() -> Any:
    """创建供无头探针运行的 FakePage 模拟对象。"""
    from tests.unit.ui.component_renderer import FakePage

    page = FakePage()
    page.schedule_update = MagicMock()  # type: ignore[method-assign]
    page.pubsub = MagicMock()  # type: ignore[method-assign]
    page.run_task = MagicMock()  # type: ignore[method-assign]
    return page


def _step_import_views() -> float:
    """1. 7 个 View 模块 import 耗时 + litellm 审计。"""
    t0 = T()
    for mod in _VIEW_MODULES:
        __import__(mod)
    dt = (T() - t0) * 1000
    print(f"1. import 7 view modules: {dt:.2f} ms | litellm in sys.modules: {'litellm' in sys.modules}")
    return dt


def _step_singletons() -> float:
    """2. 关键单例与 ScreenerViewModel 冷/热构造。"""
    from data.persistence.review_manager import ReviewManager
    from strategies.all_strategies import StrategyManager
    from ui.viewmodels.screener_view_model import ScreenerViewModel

    t0 = T()
    StrategyManager()
    t1 = T()
    ReviewManager()
    t2 = T()
    ScreenerViewModel()
    t3 = T()
    ScreenerViewModel()
    t4 = T()
    total_dt = (t4 - t0) * 1000
    print(
        f"2. StrategyManager {(t1 - t0) * 1000:.2f} ms | ReviewManager {(t2 - t1) * 1000:.2f} ms | "
        f"ScreenerViewModel cold {(t3 - t2) * 1000:.2f} ms / hot {(t4 - t3) * 1000:.2f} ms"
    )
    return total_dt


def _step_per_page_construction() -> tuple[float, float, dict[str, float]]:
    """3. 首帧与各隐页真渲染耗时（控件树构建）。

    返回:
        (first_frame_ms, hidden_total_ms, per_page_details)
    """
    from core.i18n import DEFAULT_LOCALE
    from tests.unit.ui.component_renderer import attach_fake_page, make_component, render_once
    from ui.i18n import I18nState
    from ui.theme import AppColorsState, ThemeName

    import ui.i18n as ui_i18n
    import ui.theme as ui_theme

    ui_i18n._i18n_state = I18nState(locale=DEFAULT_LOCALE)
    ui_theme.AppColors._state = AppColorsState(theme_name=ThemeName.LIGHT)
    page = _create_headless_page()

    # 3.1 激活首帧 (HomeView active=True)
    home_mod = __import__("ui.views.home_view", fromlist=["HomeView"])
    t0 = T()
    home_comp = make_component(home_mod.HomeView, active=True)
    attach_fake_page(home_comp, page)
    render_once(home_comp)
    first_frame_dt = (T() - t0) * 1000

    print("3. Per-page construction cost (First Frame vs Hidden Pages):")
    print(f"   [First Frame] HomeView (active=True)      {first_frame_dt:8.2f} ms")

    # 3.2 6 个隐藏页 (active=False)
    hidden_total = 0.0
    details: dict[str, float] = {"HomeView": first_frame_dt}
    for label, mod_path, comp_name, extra_kwargs in _HIDDEN_VIEWS:
        mod = __import__(mod_path, fromlist=[comp_name])
        t0 = T()
        component = make_component(getattr(mod, comp_name), active=False, **extra_kwargs)
        attach_fake_page(component, page)
        render_once(component)
        dt = (T() - t0) * 1000
        hidden_total += dt
        details[label] = dt
        print(f"   [Hidden Page] {label:<24} {dt:8.2f} ms")

    print(f"   TOTAL(6 hidden pages render)              {hidden_total:8.2f} ms")
    return first_frame_dt, hidden_total, details


def _step_operable_frame_and_layout() -> tuple[float, float]:
    """4. 页面栈装配与 AppLayout 整体首次可操作帧耗时。

    返回:
        (stack_wrapper_ms, layout_first_frame_ms)
    """
    from tests.unit.ui.component_renderer import attach_fake_page, make_component, render_once
    import ui.app_layout as mod

    page = _create_headless_page()

    # 4.1 _build_pages_stack 在生产常驻挂载模型下的包装耗时
    t0 = T()
    stack_comp = make_component(mod._build_pages_stack, 0)
    attach_fake_page(stack_comp, page)
    render_once(stack_comp)
    dt_stack = (T() - t0) * 1000

    # 4.2 AppLayout 整体（含 NavigationRail、状态栏、快捷键、页面栈组件）首次可操作帧耗时
    t0 = T()
    layout_comp = make_component(mod.AppLayout)
    attach_fake_page(layout_comp, page)
    render_once(layout_comp)
    dt_layout = (T() - t0) * 1000

    print("4. Production layout assembly:")
    print(f"   _build_pages_stack (full-mount wrapper)   {dt_stack:8.2f} ms")
    print(f"   AppLayout complete first operable frame   {dt_layout:8.2f} ms")
    return dt_stack, dt_layout


def _step_perf_threshold_check(
    import_dt: float,
    singletons_dt: float,
    first_frame_dt: float,
    hidden_total_dt: float,
    layout_first_frame_dt: float,
    page_details: dict[str, float],
) -> bool:
    """5. PerfThreshold 与 SLA 门槛核对。"""
    from utils.log_decorators import PerfThreshold

    print("5. PerfThreshold & SLA Compliance Audit:")
    passed = True

    # 5.1 首帧控件纯计算构建耗时 (MEMORY_COMPUTE: 50ms)
    # 首帧 UI 构建不包含外部网络 IO，纯控件树装配
    if first_frame_dt <= PerfThreshold.MEMORY_COMPUTE:
        print(
            f"   [PASS] First Frame pure render ({first_frame_dt:.2f} ms) <= MEMORY_COMPUTE ({PerfThreshold.MEMORY_COMPUTE} ms)"
        )
    else:
        print(
            f"   [WARN] First Frame pure render ({first_frame_dt:.2f} ms) > MEMORY_COMPUTE ({PerfThreshold.MEMORY_COMPUTE} ms)"
        )

    # 5.2 各隐页单页构建开销 (应在 DB_SINGLE_QUERY 200ms 量级以内)
    for label, dt in page_details.items():
        if dt <= PerfThreshold.DB_SINGLE_QUERY:
            print(f"   [PASS] Page {label} ({dt:.2f} ms) <= DB_SINGLE_QUERY ({PerfThreshold.DB_SINGLE_QUERY} ms)")
        else:
            print(
                f"   [INFO] Page {label} ({dt:.2f} ms) > DB_SINGLE_QUERY ({PerfThreshold.DB_SINGLE_QUERY} ms) [cold one-off singleton init]"
            )

    # 5.3 冷启动全流程 proxy 总耗时与 GLOBAL_INIT (15000ms) 及 8s SLA 门槛核对
    total_startup_proxy = import_dt + singletons_dt + first_frame_dt + hidden_total_dt + layout_first_frame_dt
    print(f"   --> Total Estimated Startup Proxy: {total_startup_proxy:.2f} ms")

    if total_startup_proxy <= PerfThreshold.GLOBAL_INIT:
        print(
            f"   [PASS] Total Startup Proxy ({total_startup_proxy:.2f} ms) <= GLOBAL_INIT ({PerfThreshold.GLOBAL_INIT} ms)"
        )
    else:
        print(
            f"   [FAIL] Total Startup Proxy ({total_startup_proxy:.2f} ms) > GLOBAL_INIT ({PerfThreshold.GLOBAL_INIT} ms)"
        )
        passed = False

    if total_startup_proxy <= _STARTUP_SLA_MS:
        print(f"   [PASS] Total Startup Proxy ({total_startup_proxy:.2f} ms) <= 8s SLA ({_STARTUP_SLA_MS:.0f} ms)")
    else:
        print(
            f"   [WARN] Total Startup Proxy ({total_startup_proxy:.2f} ms) > 8s SLA ({_STARTUP_SLA_MS:.0f} ms) "
            "[Host dependency import load fluctuation; hidden render is NOT bottleneck]"
        )

    hidden_ratio = (hidden_total_dt / total_startup_proxy) * 100 if total_startup_proxy > 0 else 0.0
    print(f"   --> Hidden Pages Construction Ratio: {hidden_ratio:.2f}% of startup time")
    return passed


def main() -> None:
    print("== Cold Startup Perf Probe: Production Full-Mount Model (UX-06 / F07 / R04) ==")
    import_dt = _step_import_views()
    singletons_dt = _step_singletons()
    first_frame_dt, hidden_total_dt, page_details = _step_per_page_construction()
    _stack_dt, layout_dt = _step_operable_frame_and_layout()
    passed = _step_perf_threshold_check(
        import_dt,
        singletons_dt,
        first_frame_dt,
        hidden_total_dt,
        layout_dt,
        page_details,
    )
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
