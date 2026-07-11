import contextlib
import logging

import pytest
from unittest.mock import patch

from tests.unit.ui.conftest import wrap_mock_page
from ui.components.health_report_dialog import logger as dialog_logger

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_page():
    from tests.unit.ui.mock_flet import MockFletPage

    page = MockFletPage()
    return wrap_mock_page(page)


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


# ---------------------------------------------------------------------------
# 模块级纯函数：_health_dialog_size / _log_report_summary / _build_health_content
# （由旧 HealthReportDialog 实例方法转换）
# ---------------------------------------------------------------------------
class TestHealthReportDialog:
    """HealthReportDialog 声明式组件测试（纯函数 + 契约守护）。

    声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化（参考 Phase 3.2.7 范式）。
    实例方法已转为模块级纯函数，可直接单测。
    """

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def _make_report(self, status="green"):
        return {
            "status": status,
            "market": {"lag_days": 0, "latest_local": "2025-01-01"},
            "fundamentals": {
                "gap_count": 0,
                "sanity_errors": 0,
                "tables": {
                    "daily_quotes": {
                        "ratio": 0.95,
                        "fresh_ratio": 0.90,
                        "type": "stock",
                    },
                },
            },
            "reasons": [],
        }

    def test_health_dialog_size_default_without_page(self):
        """B1: _health_dialog_size 无 page 时返回 (600, 600)。"""
        from ui.components.health_report_dialog import _health_dialog_size

        assert _health_dialog_size(None) == (600, 600)

    def test_health_dialog_size_with_page(self, mock_page):
        """_health_dialog_size 有 page 时基于窗口尺寸计算（含上限约束）。"""
        from ui.components.health_report_dialog import _health_dialog_size

        mock_page.window.width = 2000
        mock_page.window.height = 1500
        w, h = _health_dialog_size(mock_page)
        # min(max(2000-80, 480), 600) = 600; min(max(1500-80, 400), 600) = 600
        assert w == 600
        assert h == 600

    def test_health_dialog_size_small_window(self, mock_page):
        """_health_dialog_size 小窗口时使用下限约束。"""
        from ui.components.health_report_dialog import _health_dialog_size

        mock_page.window.width = 500
        mock_page.window.height = 400
        w, h = _health_dialog_size(mock_page)
        # min(max(500-80, 480), 600) = 480; min(max(400-80, 400), 600) = 400
        assert w == 480
        assert h == 400

    def test_log_report_summary_normal(self, caplog):
        """_log_report_summary 正常路径记录 INFO 日志。"""
        from ui.components.health_report_dialog import _log_report_summary

        report = self._make_report()
        with caplog.at_level(logging.INFO, logger=dialog_logger.name):
            _log_report_summary(report)
        assert any("HealthReportDialog Opened" in r.message for r in caplog.records)

    def test_log_report_summary_handles_exception(self, caplog):
        """B7: _log_report_summary 异常路径不抛出，降级为 logger.error。"""

        class WeirdMarket:
            def get(self, key, default=None):
                if default == "?":
                    raise RuntimeError("boom")
                return default

        report = {
            "status": "green",
            "market": WeirdMarket(),
            "fundamentals": {"gap_count": 0, "sanity_errors": 0, "tables": {}},
            "reasons": [],
        }
        from ui.components.health_report_dialog import _log_report_summary

        with caplog.at_level(logging.ERROR, logger=dialog_logger.name):
            _log_report_summary(report)
        assert any("Error logging report summary" in r.message for r in caplog.records)

    def test_build_health_content_green(self):
        """_build_health_content green status 返回 Container。"""
        from ui.components.health_report_dialog import _build_health_content

        content = _build_health_content(self._make_report("green"), 600, 600)
        assert content is not None
        assert content.width == 600
        assert content.height == 600

    def test_build_health_content_yellow(self):
        from ui.components.health_report_dialog import _build_health_content

        content = _build_health_content(self._make_report("yellow"), 600, 600)
        assert content is not None

    def test_build_health_content_red(self):
        from ui.components.health_report_dialog import _build_health_content

        content = _build_health_content(self._make_report("red"), 600, 600)
        assert content is not None

    def test_build_health_content_with_reasons(self):
        """B4: _build_health_content 含 reasons 时构建 issues_section。"""
        from ui.components.health_report_dialog import _build_health_content

        report = self._make_report()
        report["reasons"] = ["数据延迟", "缺失财务"]
        content = _build_health_content(report, 600, 600)
        # issues_section 是 Column 中的第 3 个控件 (index 2)
        issues_section = content.content.controls[2]
        assert issues_section.bgcolor is not None
        issues_column = issues_section.content
        assert issues_column.controls[0].value == "common_reason"
        assert len(issues_column.controls) == 3  # 1 header + 2 reasons


# ---------------------------------------------------------------------------
# 契约守护测试：HealthReportDialog 声明式组件禁止命令式模式
# ---------------------------------------------------------------------------
class TestHealthReportDialogContract:
    """契约守护测试：HealthReportDialog 声明式组件禁止命令式模式。

    注意：HealthScanDialog 仍为命令式（Task 4.3 重写），其 did_mount/pop_dialog 等
    不在本契约范围内。本契约仅守护 HealthReportDialog 的声明式重写成果。
    """

    def test_no_page_show_dialog(self) -> None:
        """DoD: grep `page.show_dialog` in health_report_dialog.py == 0。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "page.show_dialog" not in content, "禁止 page.show_dialog（DoD）"

    def test_health_report_dialog_is_declarative_component(self) -> None:
        """验证 HealthReportDialog 是 @ft.component 声明式组件。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "@ft.component" in content
        assert "def HealthReportDialog(" in content

    def test_health_report_dialog_not_alert_dialog_subclass(self) -> None:
        """验证 HealthReportDialog 不再是 ft.AlertDialog 子类。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "class HealthReportDialog(ft.AlertDialog)" not in content

    def test_uses_use_dialog(self) -> None:
        """验证通过 ft.use_dialog 自动挂载/卸载 dialog。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "ft.use_dialog(" in content

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "ft.use_state(get_observable_state)" in content

    def test_pure_functions_preserved(self) -> None:
        """验证模块级纯函数保留导出。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "def _health_dialog_size(" in content
        assert "def _log_report_summary(" in content
        assert "def _build_health_content(" in content


class TestHealthScoreCard:
    """HealthScoreCard 状态映射测试（Phase E.3 重写后由模块级常量 _HEALTH_STATUS_MAP 替代）。

    旧 ``HealthScoreCard(ft.Container)`` class 已重写为 ``_build_health_score_card`` 纯函数，
    状态映射改为模块级常量 ``_HEALTH_STATUS_MAP`` / ``_HEALTH_DEFAULT_STATUS``。
    纯函数渲染测试见 ``test_health_report_dialog_contract.py::TestBuildHealthScoreCard``。
    """

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_status_map_green_has_excellent_key(self):
        from ui.components.health_report_dialog import _HEALTH_STATUS_MAP

        _, _, i18n_key = _HEALTH_STATUS_MAP["green"]
        assert i18n_key == "health_status_excellent"

    def test_status_map_yellow_has_warning_key(self):
        from ui.components.health_report_dialog import _HEALTH_STATUS_MAP

        _, _, i18n_key = _HEALTH_STATUS_MAP["yellow"]
        assert i18n_key == "health_status_warning"

    def test_default_status_has_critical_key(self):
        from ui.components.health_report_dialog import _HEALTH_DEFAULT_STATUS

        _, _, i18n_key = _HEALTH_DEFAULT_STATUS
        assert i18n_key == "health_status_critical"

    def test_unknown_status_falls_to_default(self):
        from ui.components.health_report_dialog import _HEALTH_DEFAULT_STATUS, _HEALTH_STATUS_MAP

        result = _HEALTH_STATUS_MAP.get("red", _HEALTH_DEFAULT_STATUS)
        assert result == _HEALTH_DEFAULT_STATUS


class TestKeyMetricsGrid:
    """KeyMetricsGrid 测试（Phase E.3 重写后由 _build_key_metrics_grid 纯函数替代）。

    旧 ``KeyMetricsGrid(ft.Column)`` class 已重写为 ``_build_key_metrics_grid`` 纯函数。
    """

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def _make_grid(self, lag_days=0, gap_count=0, sanity_errors=0, latest_local="N/A"):
        from ui.components.health_report_dialog import _build_key_metrics_grid

        market = {"lag_days": lag_days, "latest_local": latest_local}
        fundamentals = {"gap_count": gap_count, "sanity_errors": sanity_errors}
        return _build_key_metrics_grid(market, fundamentals)

    def test_creates_with_market_and_fundamentals(self):
        grid = self._make_grid(lag_days=1, gap_count=2, sanity_errors=0)
        assert len(grid.controls) >= 2

    def test_lag_days_positive_uses_error_color(self):
        grid = self._make_grid(lag_days=3)
        lag_tile = grid.controls[1].controls[0]
        value_text = lag_tile.content.controls[1]
        assert value_text.color == self.mock_ac.ERROR

    def test_lag_days_zero_uses_success_color(self):
        grid = self._make_grid(lag_days=0)
        lag_tile = grid.controls[1].controls[0]
        value_text = lag_tile.content.controls[1]
        assert value_text.color == self.mock_ac.SUCCESS


class TestCoverageDetailTable:
    """CoverageDetailTable 测试（Phase E.3 重写后由 _build_coverage_detail_table 纯函数替代）。

    旧 ``CoverageDetailTable(ft.Column)`` class 已重写为 ``_build_coverage_detail_table`` 纯函数，
    实例方法 ``_create_row`` 改为模块级 ``_create_coverage_row``。
    """

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


class TestHealthScanDialog:
    """HealthScanDialog 测试（Phase E.3 重写为 @ft.component 声明式组件）。

    旧命令式 ``HealthScanDialog(ft.AlertDialog)`` class 已重写为
    ``@ft.component def HealthScanDialog(...)`` 声明式组件。

    声明式组件的渲染逻辑由 Flet 框架保证，无法在无 renderer 下直接实例化测试
    （``use_state`` 在无 renderer 下抛 RuntimeError）。

    - 契约守护（@ft.component / 无 did_mount / 无 .update() 等）由
      ``test_health_report_dialog_contract.py::TestHealthScanDialogContract`` 承担
    - 模块级纯函数测试（_build_scan_content / _build_scan_result / _scan_dialog_size）由
      ``test_health_report_dialog_contract.py::TestBuildScanContent`` 等承担

    旧命令式 API 测试（start_scan/_update_progress/refresh_locale/_data_processor 等）
    已删除，对应行为通过 ``use_effect`` + state 驱动在声明式范式中由框架保证。
    """

    pass
