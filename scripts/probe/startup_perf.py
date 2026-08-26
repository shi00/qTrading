"""UX-06 (P1-04) 冷启动性能探针 — 生产模式全页构造 proxy 测量。

用途：量化 ``app_layout._build_pages_stack`` 生产模式（非 E2E）全页构造的冷启动成本，
为"是否推广 visited_tabs 懒构造"提供可复现证据（UX-06 判定：不实施，证据基准）。

测量维度：
1. 7 个 View 模块 import 总耗时 + litellm 是否被冲入 sys.modules（验证双层惰性有效）
2. 关键单例构造（StrategyManager/ReviewManager）与 ScreenerViewModel 冷/热构造
3. 7 个 View 组件真渲染耗时（active=False，控件树构建）——复用 tests/unit/ui/
   component_renderer 无头渲染基础设施（FakePage），等价生产模式预构造行为。

注意：
- 探针在独立进程运行，import 为冷缓存；数值为本机基准，不同机器有差异
- 探针不在 pytest testpaths 内，不会进入测试采集（无 R7 污染）
- UX-06 结论：litellm 已由 ai_service._ensure_litellm_loaded 双层惰性移除 import
  阻塞；生产模式主导成本为依赖库 import（懒构造无法改善），visited_tabs 懒构造
  最大可省 ~1-1.4s（约 15-20%），收益/风险比不成立，不实施。

运行（Windows PowerShell）：
    $env:PYTHONPATH = "<repo-root>"; uv run python scripts/probe/startup_perf.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

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

_RENDER_VIEWS = [
    ("HomeView", "ui.views.home_view", "HomeView"),
    ("ScreenerView", "ui.views.screener_view", "ScreenerView"),
    ("BacktestView", "ui.views.backtest_view", "BacktestView"),
    ("DataExplorerView", "ui.views.data_view", "DataExplorerView"),
    ("TaskCenterView", "ui.views.task_center_view", "TaskCenterView"),
    ("SettingsView", "ui.views.settings_view", "SettingsView"),
    ("WatchlistView", "ui.views.watchlist_view", "WatchlistView"),
]


def _step_import_views() -> None:
    """1. 7 个 View 模块 import 耗时 + litellm 审计。"""
    t0 = T()
    for mod in _VIEW_MODULES:
        __import__(mod)
    dt = (T() - t0) * 1000
    print(f"1. import 7 view modules: {dt:.2f} ms | litellm in sys.modules: {'litellm' in sys.modules}")


def _step_singletons() -> None:
    """2. 关键单例与 ScreenerViewModel 冷/热构造（含 1081ms 分解依据）。"""
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
    print(
        f"2. StrategyManager {(t1 - t0) * 1000:.2f} ms | ReviewManager {(t2 - t1) * 1000:.2f} ms | "
        f"ScreenerViewModel cold {(t3 - t2) * 1000:.2f} ms / hot {(t4 - t3) * 1000:.2f} ms"
    )


def _step_render_views() -> None:
    """3. 7 个 View 真渲染耗时（active=False，控件树构建）。"""
    from core.i18n import DEFAULT_LOCALE
    from tests.unit.ui.component_renderer import FakePage, make_component, render_once, run_mount_effects
    from ui.i18n import I18nState
    from ui.theme import AppColorsState, ThemeName

    import ui.i18n as ui_i18n
    import ui.theme as ui_theme

    ui_i18n._i18n_state = I18nState(locale=DEFAULT_LOCALE)
    ui_theme.AppColors._state = AppColorsState(theme_name=ThemeName.LIGHT)

    total = 0.0
    for label, mod_path, comp_name in _RENDER_VIEWS:
        mod = __import__(mod_path, fromlist=[comp_name])
        t0 = T()
        component = make_component(getattr(mod, comp_name), active=False)
        run_mount_effects(component, page=FakePage())
        render_once(component)
        dt = (T() - t0) * 1000
        total += dt
        print(f"   {label:<22} {dt:8.2f} ms")
    print(f"3. TOTAL(7 views render) {total:.2f} ms")


def main() -> None:
    print("== UX-06 startup probe: production-mode page construction (proxy) ==")
    _step_import_views()
    _step_singletons()
    _step_render_views()


if __name__ == "__main__":
    main()
