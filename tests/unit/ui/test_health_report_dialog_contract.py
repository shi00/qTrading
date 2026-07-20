"""ui/components/health_report_dialog.py 声明式契约守护测试 (Phase E.3).

声明式重写后契约聚焦:
1. HealthScanDialog 是 ``@ft.component`` 函数（非 class）
2. 4 个命令式子组件 class 已移除（HealthScoreCard/MetricTile/KeyMetricsGrid/CoverageDetailTable）
3. 无命令式 API（did_mount/will_unmount/.update()/PageRefMixin/_page_ref/weakref）
4. HealthReportDialog 仍是 ``@ft.component``（保留验证，Phase 3.2.7 完成）
5. 模块级纯函数保留（_build_health_score_card/_build_metric_tile/_build_key_metrics_grid/
   _build_coverage_detail_table/_build_scan_content/_build_scan_result）

业务逻辑覆盖（颜色判断/状态映射/扫描结果构建）由 ``test_health_report_dialog.py`` 单测承担。
"""

# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.components.health_report_dialog as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.health_report_dialog as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：HealthScanDialog 声明式范式
# ============================================================================


class TestHealthScanDialogContract:
    """HealthScanDialog 声明式契约守护测试 (Phase E.3)。"""

    def test_health_scan_dialog_is_ft_component(self):
        """DoD: HealthScanDialog 必须被 @ft.component 装饰。"""
        from ui.components.health_report_dialog import HealthScanDialog

        assert hasattr(HealthScanDialog, "__wrapped__"), "HealthScanDialog 必须用 @ft.component 装饰"

    def test_health_scan_dialog_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "HealthScanDialog 必须用 @ft.component 装饰"
        assert "def HealthScanDialog(" in _code_source(), "必须是函数定义"

    def test_no_class_health_scan_dialog(self):
        """DoD: 禁止命令式 class HealthScanDialog。"""
        assert "class HealthScanDialog(" not in _code_source(), "HealthScanDialog 不应是 class (命令式)"

    def test_no_class_health_score_card(self):
        """DoD: 禁止命令式 class HealthScoreCard。"""
        assert "class HealthScoreCard(" not in _code_source(), "HealthScoreCard 不应是 class (命令式)"

    def test_no_class_metric_tile(self):
        """DoD: 禁止命令式 class MetricTile。"""
        assert "class MetricTile(" not in _code_source(), "MetricTile 不应是 class (命令式)"

    def test_no_class_key_metrics_grid(self):
        """DoD: 禁止命令式 class KeyMetricsGrid。"""
        assert "class KeyMetricsGrid(" not in _code_source(), "KeyMetricsGrid 不应是 class (命令式)"

    def test_no_class_coverage_detail_table(self):
        """DoD: 禁止命令式 class CoverageDetailTable。"""
        assert "class CoverageDetailTable(" not in _code_source(), "CoverageDetailTable 不应是 class (命令式)"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_self_update(self):
        """DoD: 禁止命令式 self.update() / .update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"

    def test_no_page_ref_mixin(self):
        """DoD: 禁止 PageRefMixin。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"

    def test_no_page_ref(self):
        """DoD: 禁止 _page_ref / page_ref 属性。"""
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "page_ref" not in _code_source(), "不应使用 page_ref"

    def test_no_weakref(self):
        """DoD: 禁止 weakref。"""
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_pop_dialog(self):
        """DoD: 禁止 page.pop_dialog (声明式用 ft.use_dialog 自动卸载)。"""
        assert "pop_dialog" not in _code_source(), "不应使用 pop_dialog (声明式用 use_dialog)"

    def test_no_show_dialog(self):
        """DoD: 禁止 page.show_dialog (声明式用 ft.use_dialog 自动挂载)。"""
        assert "show_dialog" not in _code_source(), "不应使用 show_dialog (声明式用 use_dialog)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式用 ft.use_state 自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_start_scan_method(self):
        """DoD: 禁止旧命令式 start_scan 实例方法 (声明式用 use_effect 自动触发)。"""
        # 注意：_start_scan_effect 是 use_effect 内部闭包，不是实例方法
        assert "def start_scan(" not in _code_source(), "不应有 start_scan 实例方法 (声明式用 use_effect)"

    def test_uses_use_dialog(self):
        """DoD: 必须通过 ft.use_dialog 自动挂载/卸载 dialog。"""
        assert "ft.use_dialog(" in _raw_source(), "必须使用 ft.use_dialog"

    def test_uses_i18n_observable_state(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "ft.use_state(get_observable_state)" in _raw_source(), "必须订阅 get_observable_state"

    def test_uses_use_effect_for_scan(self):
        """DoD: 扫描任务必须通过 use_effect 启动 (R2 CancelledError 传播)。"""
        assert "ft.use_effect(" in _raw_source(), "必须使用 use_effect 启动扫描"

    def test_uses_use_viewmodel_for_business_state(self):
        """DoD: 业务状态必须经 use_viewmodel 消费 HealthScanViewModel (CLAUDE.md §3.2 MVVM)。

        R2/R11 守卫（CancelledError raise / run_coroutine_threadsafe）已随业务逻辑
        下沉到 HealthScanViewModel，由 ``test_health_scan_view_model.py`` 守护。
        """
        assert "use_viewmodel(" in _raw_source(), "必须经 use_viewmodel 消费 VM"
        assert "HealthScanViewModel(" in _raw_source(), "必须实例化 HealthScanViewModel"

    def test_no_direct_data_processor_call(self):
        """DoD: View 不应直驱 DataProcessor.run_quality_scan (已下沉到 VM)。"""
        code = _code_source()
        assert "data_processor.run_quality_scan" not in code, "禁止 View 直驱 DataProcessor.run_quality_scan"

    def test_no_self_held_business_state(self):
        """DoD: View 不应自持业务状态 (scan_state/progress/result 由 VM 持有)。

        use_state 仅用于 dialog 显隐开关 (open_/set_open)，业务字段必须从 VM state 读取。
        """
        code = _code_source()
        # 禁止 use_state 持有业务状态字段（scan_state/progress/status_text/result/error_key）
        forbidden_patterns = [
            'use_state("scanning"',
            'use_state("done"',
            'use_state("error"',
            "use_state(0.0)",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in code, f"View 不应自持业务状态: {pattern}"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "get_observable_state" in _raw_source(), "必须订阅 get_observable_state"

    def test_pure_functions_preserved(self):
        """DoD: 模块级纯函数保留导出 (由旧 class 转换)。"""
        code = _raw_source()
        assert "def _build_health_score_card(" in code, "必须保留 _build_health_score_card 纯函数"
        assert "def _build_metric_tile(" in code, "必须保留 _build_metric_tile 纯函数"
        assert "def _build_key_metrics_grid(" in code, "必须保留 _build_key_metrics_grid 纯函数"
        assert "def _build_coverage_detail_table(" in code, "必须保留 _build_coverage_detail_table 纯函数"
        assert "def _build_scan_content(" in code, "必须保留 _build_scan_content 纯函数"
        assert "def _build_scan_result(" in code, "必须保留 _build_scan_result 纯函数"
        assert "def _scan_dialog_size(" in code, "必须保留 _scan_dialog_size 纯函数"

    def test_no_class_alert_dialog_subclass(self):
        """DoD: 禁止任何 ft.AlertDialog 子类 (声明式用条件渲染)。"""
        code = _code_source()
        assert "class HealthScanDialog(ft.AlertDialog)" not in code, "HealthScanDialog 不应继承 ft.AlertDialog"
        assert "class HealthScoreCard(ft.Container)" not in code, "HealthScoreCard 不应继承 ft.Container"
        assert "class MetricTile(ft.Container)" not in code, "MetricTile 不应继承 ft.Container"
        assert "class KeyMetricsGrid(ft.Column)" not in code, "KeyMetricsGrid 不应继承 ft.Column"
        assert "class CoverageDetailTable(ft.Column)" not in code, "CoverageDetailTable 不应继承 ft.Column"


# ============================================================================
# 契约守护：HealthReportDialog 仍是声明式（保留验证，Phase 3.2.7 完成）
# ============================================================================


class TestHealthReportDialogContractPreserved:
    """HealthReportDialog 声明式契约守护测试 (Phase 3.2.7 完成，保留验证)。"""

    def test_health_report_dialog_is_ft_component(self):
        """DoD: HealthReportDialog 必须被 @ft.component 装饰。"""
        from ui.components.health_report_dialog import HealthReportDialog

        assert hasattr(HealthReportDialog, "__wrapped__"), "HealthReportDialog 必须用 @ft.component 装饰"

    def test_health_report_dialog_uses_ft_component(self):
        """DoD: HealthReportDialog 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source()
        assert "def HealthReportDialog(" in _code_source()

    def test_no_class_health_report_dialog(self):
        """DoD: 禁止命令式 class HealthReportDialog。"""
        assert "class HealthReportDialog(" not in _code_source()

    def test_no_page_show_dialog(self):
        """DoD: grep `page.show_dialog` in health_report_dialog.py == 0。"""
        assert "page.show_dialog" not in _raw_source(), "禁止 page.show_dialog（DoD）"

    def test_uses_use_dialog(self):
        """DoD: 必须通过 ft.use_dialog 自动挂载/卸载 dialog。"""
        assert "ft.use_dialog(" in _raw_source()

    def test_uses_i18n_observable_state(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "ft.use_state(get_observable_state)" in _raw_source()

    def test_pure_functions_preserved(self):
        """DoD: 模块级纯函数保留导出。"""
        code = _raw_source()
        assert "def _health_dialog_size(" in code
        assert "def _log_report_summary(" in code
        assert "def _build_health_content(" in code


# ============================================================================
# 模块级纯函数测试（由旧 class 实例方法转换）
# ============================================================================


def _apply_patches(mock_i18n, mock_ac):
    return [
        patch("ui.components.health_report_dialog.I18n", mock_i18n),
        patch("ui.components.health_report_dialog.AppColors", mock_ac),
        patch(
            "ui.components.health_report_dialog.HEALTH_CHECK_TABLES",
            {
                "daily_quotes": {"desc": "日K线"},
                "financial_reports": {"desc": "财务报表"},
                "macro_economy": {"desc": "宏观经济", "type": "global"},
            },
        ),
        patch(
            "ui.components.health_report_dialog.HEALTH_REPORT_ORDER",
            [
                "daily_quotes",
                "financial_reports",
                "macro_economy",
            ],
        ),
        patch(
            "ui.components.health_report_dialog.HEALTH_THRESHOLD_FINANCIAL_EXCELLENT",
            0.9,
        ),
        patch(
            "ui.components.health_report_dialog.HEALTH_THRESHOLD_FINANCIAL_COVERAGE",
            0.7,
        ),
        patch("ui.components.health_report_dialog.HEALTH_DEPTH_WARNING_RATIO", 0.5),
        patch("ui.components.health_report_dialog.HEALTH_THRESHOLD_BREADTH", 0.6),
    ]


class TestBuildHealthScoreCard:
    """_build_health_score_card 模块级纯函数测试（由 HealthScoreCard class 转换）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_green_status_uses_success_color(self):
        from ui.components.health_report_dialog import _build_health_score_card

        card = _build_health_score_card("green", 5)
        assert isinstance(card, ft.Container)

    def test_yellow_status_uses_warning_color(self):
        from ui.components.health_report_dialog import _build_health_score_card

        card = _build_health_score_card("yellow", 5)
        assert isinstance(card, ft.Container)

    def test_red_status_uses_error_color(self):
        from ui.components.health_report_dialog import _build_health_score_card

        card = _build_health_score_card("red", 5)
        assert isinstance(card, ft.Container)

    def test_unknown_status_falls_to_default(self):
        from ui.components.health_report_dialog import _build_health_score_card

        # 未知 status 走 _HEALTH_DEFAULT_STATUS (error/critical)
        card = _build_health_score_card("unknown_status", 0)
        assert isinstance(card, ft.Container)


class TestBuildMetricTile:
    """_build_metric_tile 模块级纯函数测试（由 MetricTile class 转换）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_creates_tile_with_label_and_value(self):
        from ui.components.health_report_dialog import _build_metric_tile

        tile = _build_metric_tile("label", "100")
        assert tile is not None
        # content 是 Column，应有 2 个 controls (label + value)
        assert len(tile.content.controls) == 2

    def test_creates_tile_with_sub_text(self):
        from ui.components.health_report_dialog import _build_metric_tile

        tile = _build_metric_tile("label", "100", sub_text="hint")
        assert tile is not None
        # content 是 Column，应有 3 个 controls (label + value + sub_text)
        assert len(tile.content.controls) == 3

    def test_creates_tile_without_sub_text(self):
        from ui.components.health_report_dialog import _build_metric_tile

        tile = _build_metric_tile("label", "100", sub_text=None)
        assert tile is not None
        assert len(tile.content.controls) == 2


class TestBuildKeyMetricsGrid:
    """_build_key_metrics_grid 模块级纯函数测试（由 KeyMetricsGrid class 转换）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_creates_with_market_and_fundamentals(self):
        from ui.components.health_report_dialog import _build_key_metrics_grid

        market = {"lag_days": 1, "latest_local": "2025-01-01"}
        fundamentals = {"gap_count": 2, "sanity_errors": 0}
        grid = _build_key_metrics_grid(market, fundamentals)
        assert len(grid.controls) >= 2

    def test_lag_days_positive_uses_error_color(self):
        from ui.components.health_report_dialog import _build_key_metrics_grid

        market = {"lag_days": 3, "latest_local": "N/A"}
        fundamentals = {"gap_count": 0, "sanity_errors": 0}
        grid = _build_key_metrics_grid(market, fundamentals)
        # grid.controls[1] 是第一个 Row，Row.controls[0] 是第一个 metric tile
        lag_tile = grid.controls[1].controls[0]
        value_text = lag_tile.content.controls[1]
        assert value_text.color == self.mock_ac.ERROR

    def test_lag_days_zero_uses_success_color(self):
        from ui.components.health_report_dialog import _build_key_metrics_grid

        market = {"lag_days": 0, "latest_local": "N/A"}
        fundamentals = {"gap_count": 0, "sanity_errors": 0}
        grid = _build_key_metrics_grid(market, fundamentals)
        lag_tile = grid.controls[1].controls[0]
        value_text = lag_tile.content.controls[1]
        assert value_text.color == self.mock_ac.SUCCESS


class TestBuildCoverageDetailTable:
    """_build_coverage_detail_table 模块级纯函数测试（由 CoverageDetailTable class 转换）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_creates_with_stock_tables(self):
        from ui.components.health_report_dialog import _build_coverage_detail_table

        tables = {
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = _build_coverage_detail_table(tables)
        # 1 section header + 1 row = 2 controls
        assert len(table.controls) == 2

    def test_creates_with_global_tables(self):
        from ui.components.health_report_dialog import _build_coverage_detail_table

        tables = {
            "macro_economy": {"ratio": 1.0, "covered": 100, "type": "global"},
        }
        table = _build_coverage_detail_table(tables)
        assert len(table.controls) == 2

    def test_creates_with_mixed_tables(self):
        from ui.components.health_report_dialog import _build_coverage_detail_table

        tables = {
            "macro_economy": {"ratio": 1.0, "covered": 100, "type": "global"},
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = _build_coverage_detail_table(tables)
        # 1 global header + 1 global row + 1 transparent divider + 1 stock header + 1 stock row = 5
        assert len(table.controls) == 5

    def test_create_row_high_ratio_uses_success_color(self):
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.SUCCESS

    def test_create_row_medium_ratio_uses_warning_color(self):
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.8, "fresh_ratio": 0.70, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.WARNING

    def test_create_row_low_ratio_uses_error_color(self):
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.3, "fresh_ratio": 0.20, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.ERROR

    def test_create_row_uses_i18n_name_when_available(self):
        """覆盖 I18n.get 返回非 key 值时不走 fallback。"""
        from ui.components.health_report_dialog import _create_coverage_row

        # 让 I18n.get 对 tab_ 前缀的 key 返回翻译值
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}" if key.startswith("tab_") else key

        row = _create_coverage_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"})

        # 验证使用了 i18n 翻译名而非 fallback
        name_text = row.content.controls[0].controls[1]
        assert name_text.value == "translated_tab_daily_quotes"

    def test_create_row_global_uses_count_badge(self):
        """global type 行显示 count 徽标而非进度条。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("macro_economy", {"ratio": 1.0, "covered": 100, "type": "global"})
        # global 行的 Row 有 4 个 controls: name_row, count_container, spacer, check_text
        assert len(row.content.controls) == 4
        # 第二个控件是 count_container (ft.Container)
        count_container = row.content.controls[1]
        assert count_container.bgcolor is not None


class TestBuildScanContent:
    """_build_scan_content 模块级纯函数测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_done_state_returns_result_container(self):
        from ui.components.health_report_dialog import _build_scan_content

        result = {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}
        content = _build_scan_content("done", 1.0, "done", result, 450, 300)
        assert content.width == 450
        assert content.height == 300

    def test_scanning_state_shows_progress_bar(self):
        from ui.components.health_report_dialog import _build_scan_content

        content = _build_scan_content("scanning", 0.5, "scanning", None, 450, 300)
        # content.content 是 Column，3 个 controls: spacer, text, progressbar
        progress_bar = content.content.controls[2]
        assert progress_bar.value == 0.5

    def test_error_state_shows_error_text(self):
        from ui.components.health_report_dialog import _build_scan_content

        content = _build_scan_content("error", 0.0, "scanning", None, 450, 300)
        status_text = content.content.controls[1]
        # error 状态用 I18n.get("db_err_format")
        assert status_text.value == "db_err_format"

    def test_idle_state_progress_value_none(self):
        from ui.components.health_report_dialog import _build_scan_content

        content = _build_scan_content("idle", 0.0, "init", None, 450, 300)
        progress_bar = content.content.controls[2]
        assert progress_bar.value is None


class TestBuildScanResult:
    """_build_scan_result 模块级纯函数测试（由 HealthScanDialog.show_results 转换）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_high_score_uses_success_color(self):
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}
        column = _build_scan_result(result)
        # column.controls[1] 是 ft.Row (score row)
        # Row.controls[0] 是 ft.Icon, Row.controls[1] 是 ft.Column (score text)
        score_row = column.controls[1]
        icon = score_row.controls[0]
        assert icon.color == self.mock_ac.SUCCESS

    def test_medium_score_uses_warning_color(self):
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 60, "tier": 2, "avg_lag": 5, "avg_continuity": 0.8}
        column = _build_scan_result(result)
        score_row = column.controls[1]
        icon = score_row.controls[0]
        assert icon.color == self.mock_ac.WARNING

    def test_low_score_uses_error_color(self):
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 30, "tier": 1, "avg_lag": 30, "avg_continuity": 0.5}
        column = _build_scan_result(result)
        score_row = column.controls[1]
        icon = score_row.controls[0]
        assert icon.color == self.mock_ac.ERROR


class TestScanDialogSize:
    """_scan_dialog_size 模块级纯函数测试。"""

    def test_default_without_page(self):
        """无 page 时返回 (450, 300)。"""
        from ui.components.health_report_dialog import _scan_dialog_size

        assert _scan_dialog_size(None) == (450, 300)

    def test_with_large_page(self):
        """大窗口时受上限约束 (450, 300)。"""
        from ui.components.health_report_dialog import _scan_dialog_size

        mock_page = MagicMock()
        mock_page.window.width = 2000
        mock_page.window.height = 1500
        w, h = _scan_dialog_size(mock_page)
        # min(max(2000-80, 360), 450) = 450; min(max(1500-80, 240), 300) = 300
        assert w == 450
        assert h == 300

    def test_with_small_page(self):
        """小窗口时受下限约束 (360, 240)。"""
        from ui.components.health_report_dialog import _scan_dialog_size

        mock_page = MagicMock()
        mock_page.window.width = 400
        mock_page.window.height = 300
        w, h = _scan_dialog_size(mock_page)
        # min(max(400-80, 360), 450) = 360; min(max(300-80, 240), 300) = 240
        assert w == 360
        assert h == 240
