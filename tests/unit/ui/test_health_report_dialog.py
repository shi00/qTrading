import asyncio
import contextlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
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
        assert isinstance(content, ft.Container)

    def test_build_health_content_red(self):
        from ui.components.health_report_dialog import _build_health_content

        content = _build_health_content(self._make_report("red"), 600, 600)
        assert isinstance(content, ft.Container)

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


class TestBuildDepthBreadthItems:
    """_build_depth_breadth_items 纯函数测试（depth/breadth 可选项渲染）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_both_none_returns_empty_list(self):
        """depth_ratio 和 breadth_ratio 都为 None 时返回空列表。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({})
        assert items == []

    def test_only_depth_ratio_present(self):
        """仅有 depth_ratio 时返回 1 个 Text。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"depth_ratio": 0.6})
        assert len(items) == 1

    def test_only_breadth_ratio_present(self):
        """仅有 breadth_ratio 时返回 1 个 Text。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"breadth_ratio": 0.7})
        assert len(items) == 1

    def test_both_present_returns_two_items(self):
        """两者都有时返回 2 个 Text。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"depth_ratio": 0.6, "breadth_ratio": 0.7})
        assert len(items) == 2

    def test_depth_ratio_below_warning_threshold_uses_warning_color(self):
        """depth_ratio < HEALTH_DEPTH_WARNING_RATIO (0.5) 时用 WARNING 色。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"depth_ratio": 0.3})
        assert items[0].color == self.mock_ac.WARNING

    def test_depth_ratio_above_warning_threshold_uses_hint_color(self):
        """depth_ratio >= HEALTH_DEPTH_WARNING_RATIO (0.5) 时用 TEXT_HINT 色。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"depth_ratio": 0.6})
        assert items[0].color == self.mock_ac.TEXT_HINT

    def test_breadth_ratio_below_threshold_uses_warning_color(self):
        """breadth_ratio < HEALTH_THRESHOLD_BREADTH (0.6) 时用 WARNING 色。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"breadth_ratio": 0.4})
        assert items[0].color == self.mock_ac.WARNING

    def test_breadth_ratio_above_threshold_uses_hint_color(self):
        """breadth_ratio >= HEALTH_THRESHOLD_BREADTH (0.6) 时用 TEXT_HINT 色。"""
        from ui.components.health_report_dialog import _build_depth_breadth_items

        items = _build_depth_breadth_items({"breadth_ratio": 0.8})
        assert items[0].color == self.mock_ac.TEXT_HINT


class TestBuildSectionHeader:
    """_build_section_header 纯函数测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_renders_i18n_key(self):
        """_build_section_header 渲染 i18n key 对应的文本。"""
        from ui.components.health_report_dialog import _build_section_header

        header = _build_section_header("health_section_global")
        # header.content 是 Row，Row.controls[1] 是 Text
        text = header.content.controls[1]
        assert text.value == "health_section_global"
        assert text.weight == ft.FontWeight.BOLD


class TestCreateCoverageRowExtended:
    """_create_coverage_row 扩展测试：global 类型/阈值边界/fresh_ratio/covered/no_data。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_global_ratio_positive_shows_count_badge(self):
        """global 类型 ratio > 0 时显示 covered 计数徽标。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("macro_economy", {"ratio": 1.0, "covered": 1000, "type": "global"})
        # global 行有 4 个 controls: name_row, count_container, spacer, check_text
        assert len(row.content.controls) == 4
        # count_container 是第二个控件
        count_container = row.content.controls[1]
        count_text = count_container.content
        # mock_i18n.get 返回 key 本身，health_global_count 含 count 格式化
        assert count_text.value == "health_global_count"
        # check 标记
        check_text = row.content.controls[3]
        assert check_text.value == "✓"

    def test_global_ratio_zero_shows_no_data(self):
        """global 类型 ratio == 0 时显示 health_global_no_data 文案。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("macro_economy", {"ratio": 0, "covered": 0, "type": "global"})
        count_container = row.content.controls[1]
        count_text = count_container.content
        assert count_text.value == "health_global_no_data"
        # check 标记为 ✗
        check_text = row.content.controls[3]
        assert check_text.value == "✗"

    def test_stock_ratio_at_excellent_threshold(self):
        """ratio == HEALTH_THRESHOLD_FINANCIAL_EXCELLENT (0.9) 时用 SUCCESS 色。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.9, "fresh_ratio": 0.85, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.SUCCESS

    def test_stock_ratio_at_coverage_threshold(self):
        """ratio == HEALTH_THRESHOLD_FINANCIAL_COVERAGE (0.7) 时用 WARNING 色。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.7, "fresh_ratio": 0.60, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.WARNING

    def test_stock_ratio_below_coverage_threshold(self):
        """ratio < HEALTH_THRESHOLD_FINANCIAL_COVERAGE (0.7) 时用 ERROR 色。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.5, "fresh_ratio": 0.40, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.ERROR

    def test_stock_renders_fresh_ratio(self):
        """stock 类型渲染 fresh_ratio 百分比。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.85, "type": "stock"})
        # stock 行 controls: name_row, progress_bar, spacer, column(ratio+freshness+depth/breadth)
        ratio_column = row.content.controls[3]
        # ratio_column.controls[0] 是 ratio 百分比, [1] 是 freshness 文本
        freshness_text = ratio_column.controls[1]
        assert freshness_text.value == "health_freshness"

    def test_stock_renders_depth_breadth_items(self):
        """stock 类型含 depth_ratio/breadth_ratio 时渲染可选项。"""
        from ui.components.health_report_dialog import _create_coverage_row

        row = _create_coverage_row(
            "daily_quotes",
            {
                "ratio": 0.95,
                "fresh_ratio": 0.85,
                "type": "stock",
                "depth_ratio": 0.4,
                "breadth_ratio": 0.5,
            },
        )
        ratio_column = row.content.controls[3]
        # controls: ratio_text, freshness_text, depth_text, breadth_text
        assert len(ratio_column.controls) == 4

    def test_uses_fallback_name_when_i18n_misses(self):
        """I18n.get 返回 key 本身（未命中）时走 HEALTH_CHECK_TABLES fallback。"""
        from ui.components.health_report_dialog import _create_coverage_row

        # mock_i18n.get 默认返回 key 本身（side_effect=lambda key: key）
        # 所以 name == key == "tab_daily_quotes"，走 fallback
        row = _create_coverage_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"})
        name_text = row.content.controls[0].controls[1]
        # HEALTH_CHECK_TABLES["daily_quotes"]["desc"] == "日K线"
        assert name_text.value == "日K线"


class TestBuildCoverageDetailTableExtended:
    """_build_coverage_detail_table 扩展测试：不在 ORDER 中的表/排序。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_table_not_in_order_appended_after(self):
        """不在 HEALTH_REPORT_ORDER 中的表追加到末尾。"""
        from ui.components.health_report_dialog import _build_coverage_detail_table

        tables = {
            "custom_table": {"ratio": 0.5, "fresh_ratio": 0.40, "type": "stock"},
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = _build_coverage_detail_table(tables)
        # 1 section header + 2 rows = 3 controls
        assert len(table.controls) == 3
        # daily_quotes 在 ORDER 中，排前面；custom_table 追加后面
        # table.controls[1] 是 daily_quotes 的 row, [2] 是 custom_table 的 row
        daily_quotes_row_name = table.controls[1].content.controls[0].controls[1].value
        custom_row_name = table.controls[2].content.controls[0].controls[1].value
        # daily_quotes 走 fallback desc "日K线"，custom_table 走 fallback 用 key 本身
        assert daily_quotes_row_name == "日K线"
        assert custom_row_name == "custom_table"

    def test_sort_follows_health_report_order(self):
        """多个在 ORDER 中的表按 ORDER 顺序排序。"""
        from ui.components.health_report_dialog import _build_coverage_detail_table

        # HEALTH_REPORT_ORDER 中 financial_reports 在 daily_quotes 之后
        tables = {
            "financial_reports": {"ratio": 0.8, "fresh_ratio": 0.70, "type": "stock"},
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = _build_coverage_detail_table(tables)
        # 1 section header + 2 rows = 3 controls
        # 第一行应为 daily_quotes（ORDER 中靠前），第二行为 financial_reports
        first_row_name = table.controls[1].content.controls[0].controls[1].value
        second_row_name = table.controls[2].content.controls[0].controls[1].value
        assert first_row_name == "日K线"
        assert second_row_name == "财务报表"

    def test_global_before_stock(self):
        """global 类型 section 在 stock 类型 section 之前。"""
        from ui.components.health_report_dialog import _build_coverage_detail_table

        tables = {
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
            "macro_economy": {"ratio": 1.0, "covered": 100, "type": "global"},
        }
        table = _build_coverage_detail_table(tables)
        # 1 global header + 1 global row + 1 divider + 1 stock header + 1 stock row = 5
        assert len(table.controls) == 5
        # 第一个 section header 是 global
        global_header_text = table.controls[0].content.controls[1].value
        assert global_header_text == "health_section_global"


class TestBuildScanResultExtended:
    """_build_scan_result 扩展测试：avg_fundamental/fin_recency_ok/tier。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_high_avg_fundamental_uses_success_color(self):
        """avg_fundamental > 0.7 时用 SUCCESS 色。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95, "avg_fundamental": 0.8}
        column = _build_scan_result(result)
        # 最后一个 Row 是 fundamental/fin_recency 行
        # Row.controls[0] 是 fundamental Column, Column.controls[1] 是 value Text
        fundamental_row = column.controls[-1]
        fundamental_value = fundamental_row.controls[0].controls[1]
        assert fundamental_value.color == self.mock_ac.SUCCESS

    def test_medium_avg_fundamental_uses_warning_color(self):
        """0.5 < avg_fundamental <= 0.7 时用 WARNING 色。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 60, "tier": 2, "avg_lag": 5, "avg_continuity": 0.8, "avg_fundamental": 0.6}
        column = _build_scan_result(result)
        fundamental_row = column.controls[-1]
        fundamental_value = fundamental_row.controls[0].controls[1]
        assert fundamental_value.color == self.mock_ac.WARNING

    def test_low_avg_fundamental_uses_error_color(self):
        """avg_fundamental <= 0.5 时用 ERROR 色。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 30, "tier": 1, "avg_lag": 30, "avg_continuity": 0.5, "avg_fundamental": 0.3}
        column = _build_scan_result(result)
        fundamental_row = column.controls[-1]
        fundamental_value = fundamental_row.controls[0].controls[1]
        assert fundamental_value.color == self.mock_ac.ERROR

    def test_fin_recency_ok_true_shows_check(self):
        """fin_recency_ok=True 时显示 ✓（SUCCESS 色）。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {
            "score": 90,
            "tier": 3,
            "avg_lag": 1,
            "avg_continuity": 0.95,
            "avg_fundamental": 0.8,
            "fin_recency_ok": True,
        }
        column = _build_scan_result(result)
        fundamental_row = column.controls[-1]
        # Row.controls[1] 是 fin_recency Column, Column.controls[1] 是 value Text
        fin_recency_value = fundamental_row.controls[1].controls[1]
        assert fin_recency_value.value == "✓"
        assert fin_recency_value.color == self.mock_ac.SUCCESS

    def test_fin_recency_ok_false_shows_cross(self):
        """fin_recency_ok=False 时显示 ✗（ERROR 色）。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {
            "score": 30,
            "tier": 1,
            "avg_lag": 30,
            "avg_continuity": 0.5,
            "avg_fundamental": 0.3,
            "fin_recency_ok": False,
        }
        column = _build_scan_result(result)
        fundamental_row = column.controls[-1]
        fin_recency_value = fundamental_row.controls[1].controls[1]
        assert fin_recency_value.value == "✗"
        assert fin_recency_value.color == self.mock_ac.ERROR

    def test_tier_rendered_in_score_row(self):
        """tier 渲染为 quality_tier_N 文本。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 60, "tier": 2, "avg_lag": 5, "avg_continuity": 0.8, "avg_fundamental": 0.6}
        column = _build_scan_result(result)
        # score_row 是 column.controls[1]
        # score_row.controls[1] 是 Column, Column.controls[1] 是 tier Text
        score_row = column.controls[1]
        tier_text = score_row.controls[1].controls[1]
        assert tier_text.value == "quality_tier_2"

    def test_score_boundary_80_uses_warning(self):
        """score == 80 不 > 80，用 WARNING 色（边界测试）。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 80, "tier": 2, "avg_lag": 5, "avg_continuity": 0.8, "avg_fundamental": 0.6}
        column = _build_scan_result(result)
        score_row = column.controls[1]
        icon = score_row.controls[0]
        assert icon.color == self.mock_ac.WARNING

    def test_score_boundary_50_uses_error(self):
        """score == 50 不 > 50，用 ERROR 色（边界测试）。"""
        from ui.components.health_report_dialog import _build_scan_result

        result = {"score": 50, "tier": 1, "avg_lag": 30, "avg_continuity": 0.5, "avg_fundamental": 0.3}
        column = _build_scan_result(result)
        score_row = column.controls[1]
        icon = score_row.controls[0]
        assert icon.color == self.mock_ac.ERROR


# ---------------------------------------------------------------------------
# 组件运行时测试：HealthReportDialog（声明式 V1）
# ---------------------------------------------------------------------------


def _make_report_dict() -> dict:
    """构造测试用健康报告字典。"""
    return {
        "status": "green",
        "market": {"lag_days": 0, "latest_local": "2025-01-01"},
        "fundamentals": {
            "gap_count": 0,
            "sanity_errors": 0,
            "tables": {
                "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
            },
        },
        "reasons": [],
    }


@pytest.fixture
def health_report_dialog_env(mock_i18n_state, monkeypatch):
    """挂载 HealthReportDialog 组件，返回包含 component/page/mocks 的 dict。"""
    from ui.components import health_report_dialog as mod

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    on_close = MagicMock()
    on_deep_scan = MagicMock()
    report = _make_report_dict()
    component = make_component(
        mod.HealthReportDialog,
        report=report,
        page=None,
        open_state=True,
        on_close=on_close,
        on_deep_scan=on_deep_scan,
    )
    page = FakePage()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "on_close": on_close,
        "on_deep_scan": on_deep_scan,
        "report": report,
    }


class TestHealthReportDialogComponent:
    """HealthReportDialog 组件运行时测试（声明式 V1）。"""

    def test_mount_returns_container(self, health_report_dialog_env):
        """挂载返回 ft.Container（宿主容器）。"""
        result = health_report_dialog_env["result"]
        assert isinstance(result, ft.Container)

    def test_mount_with_open_state_logs_summary(self, health_report_dialog_env, caplog):
        """open_state=True 时 _log_effect 调用 _log_report_summary 记录 INFO 日志。"""
        with caplog.at_level(logging.INFO, logger=dialog_logger.name):
            # 重新挂载以触发 log effect
            env = health_report_dialog_env
            component = make_component(
                env["mod"].HealthReportDialog,
                report=env["report"],
                page=None,
                open_state=True,
                on_close=env["on_close"],
                on_deep_scan=env["on_deep_scan"],
            )
            page = FakePage()
            run_mount_effects(component, page=page)
        assert any("HealthReportDialog Opened" in r.message for r in caplog.records)

    def test_close_handler_invokes_on_close(self, health_report_dialog_env):
        """_close handler 调用 on_close 回调。"""
        env = health_report_dialog_env
        # 从 page._dialogs 找到 AlertDialog
        dialog = env["page"]._dialogs.controls[-1]
        # actions 中最后一个按钮是 close
        close_btn = dialog.actions[-1]
        # on_click 类型为 Optional[Callable]，直接调用（测试上下文保证非 None）
        close_btn.on_click(None)
        env["on_close"].assert_called_once()

    def test_deep_scan_handler_invokes_on_deep_scan(self, health_report_dialog_env):
        """_deep_scan handler 调用 on_close + on_deep_scan。"""
        env = health_report_dialog_env
        dialog = env["page"]._dialogs.controls[-1]
        # actions 中第一个按钮是 deep_scan
        deep_scan_btn = dialog.actions[0]
        deep_scan_btn.on_click(None)
        env["on_close"].assert_called_once()
        env["on_deep_scan"].assert_called_once()

    def test_unmount_triggers_cleanup(self, health_report_dialog_env):
        """卸载组件不抛异常（log effect 无 cleanup，仅验证不崩溃）。"""
        component = health_report_dialog_env["component"]
        run_unmount_effects(component)  # 不抛异常即可


# ---------------------------------------------------------------------------
# 组件运行时测试：HealthScanDialog（声明式 V1，R2/R11 守卫）
# ---------------------------------------------------------------------------


@pytest.fixture
def health_scan_dialog_factory(mock_i18n_state, monkeypatch):
    """HealthScanDialog 组件挂载工厂。

    返回工厂函数，调用时挂载组件并返回 (component, page, result)。
    工厂模式使测试可在 ``with patch(...)`` 上下文中创建组件。
    """
    from ui.components import health_report_dialog as mod

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    def _factory(
        data_processor: Any = None,
        open_state: bool = True,
        on_close: Any = None,
    ) -> tuple[Any, FakePage, Any]:
        component = make_component(
            mod.HealthScanDialog,
            data_processor=data_processor,
            page=None,
            open_state=open_state,
            on_close=on_close,
        )
        page = FakePage()
        run_mount_effects(component, page=page)
        result = render_once(component)
        return component, page, result

    return _factory


class TestHealthScanDialogComponent:
    """HealthScanDialog 组件运行时测试：_start_scan_effect / on_progress / _cleanup_scan。"""

    def test_data_processor_none_sets_error_state(self, health_scan_dialog_factory):
        """_start_scan_effect: data_processor=None → set_scan_state("error")。"""
        component, page, _ = health_scan_dialog_factory(data_processor=None, open_state=True)
        # 重新渲染以反映 state 变化
        render_once(component)
        # _build_scan_content 在 error 状态下渲染 db_err_format 文本
        dialog = page._dialogs.controls[-1]
        content = dialog.content
        # content.content 是 Column, controls[1] 是 status Text
        status_text = content.content.controls[1]
        assert status_text.value == "i18n[db_err_format]"

    def test_scan_success_sets_done_state(self, health_scan_dialog_factory):
        """_start_scan_effect: run_quality_scan 成功 → set_scan_state("done")。"""
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(
            return_value={
                "score": 90,
                "tier": 3,
                "avg_lag": 1,
                "avg_continuity": 0.95,
                "avg_fundamental": 0.8,
                "fin_recency_ok": True,
                "sample_size": 50,
            }
        )
        component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=True)
        # 重新渲染以反映 state 变化
        render_once(component)
        # done 状态下 dialog content 是 _build_scan_result 返回的 Column
        dialog = page._dialogs.controls[-1]
        content = dialog.content
        # _build_scan_result 的第一个控件是 Container(height=20)
        assert content.content.controls[0].height == 20

    def test_scan_exception_sets_error_state(self, health_scan_dialog_factory):
        """_start_scan_effect: run_quality_scan 抛 Exception → set_scan_state("error")。"""
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(side_effect=RuntimeError("scan failed"))
        component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=True)
        # 重新渲染以反映 state 变化
        render_once(component)
        dialog = page._dialogs.controls[-1]
        content = dialog.content
        status_text = content.content.controls[1]
        assert status_text.value == "i18n[db_err_format]"

    def test_cancelled_error_propagates(self, health_scan_dialog_factory):
        """R2: _start_scan_effect 中 CancelledError 必须 raise（不被 except Exception 吞没）。"""
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            health_scan_dialog_factory(data_processor=data_processor, open_state=True)

    def test_on_progress_uses_run_coroutine_threadsafe(self, health_scan_dialog_factory):
        """R11: on_progress 跨线程回调用 run_coroutine_threadsafe 调度回主 loop。"""
        captured_cb: list[Any] = []

        async def fake_scan(*args: Any, **kwargs: Any) -> dict:
            cb = kwargs.get("progress_callback")
            if cb:
                captured_cb.append(cb)
                cb(5, 10, "scanning...")
            return {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}

        data_processor = MagicMock()
        data_processor.run_quality_scan = fake_scan

        with patch("asyncio.run_coroutine_threadsafe") as mock_rct:
            mock_rct.return_value = MagicMock()
            component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=True)
            if mock_rct.called:
                coro = mock_rct.call_args.args[0]
                if hasattr(coro, "close"):
                    coro.close()

        # R11 守卫：验证 run_coroutine_threadsafe 被调用
        assert mock_rct.called, "on_progress 必须通过 run_coroutine_threadsafe 调度回主 loop"
        # 验证传入的 loop 是当前事件循环（loop-local 守卫，不跨循环复用同步原语）
        call_args = mock_rct.call_args
        loop_arg = call_args.args[1]
        assert isinstance(loop_arg, asyncio.AbstractEventLoop), "loop 参数必须是 AbstractEventLoop 实例"

    def test_close_handler_invokes_on_close(self, health_scan_dialog_factory):
        """_close handler 调用 on_close 回调。"""
        on_close = MagicMock()
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(
            return_value={"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}
        )
        component, page, _ = health_scan_dialog_factory(
            data_processor=data_processor, open_state=True, on_close=on_close
        )
        dialog = page._dialogs.controls[-1]
        close_btn = dialog.actions[-1]
        close_btn.on_click(None)
        on_close.assert_called_once()

    def test_cleanup_cancels_pending_futures(self, health_scan_dialog_factory):
        """_cleanup_scan: 卸载时取消 pending futures（R2 兼容不重新抛出）。"""
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_future.cancel = MagicMock()

        async def fake_scan(*args: Any, **kwargs: Any) -> dict:
            cb = kwargs.get("progress_callback")
            if cb:
                cb(5, 10, "scanning...")
            return {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}

        data_processor = MagicMock()
        data_processor.run_quality_scan = fake_scan

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future) as mock_rct:
            component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=True)
            # future 已通过 on_progress 添加到 futures_ref
            # 卸载触发 _cleanup_scan
            run_unmount_effects(component)
            # R2 兼容：cancel 被调用，但不重新抛出 CancelledError
            mock_future.cancel.assert_called_once()
            if mock_rct.called:
                coro = mock_rct.call_args.args[0]
                if hasattr(coro, "close"):
                    coro.close()

    def test_cleanup_with_no_futures_no_error(self, health_scan_dialog_factory):
        """_cleanup_scan: 无 pending futures 时不抛异常。"""
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(
            return_value={"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}
        )
        component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=True)
        # 卸载不抛异常
        run_unmount_effects(component)

    def test_open_state_false_skips_scan(self, health_scan_dialog_factory):
        """_start_scan_effect: open_=False 时早返回，不调 run_quality_scan。"""
        data_processor = MagicMock()
        data_processor.run_quality_scan = AsyncMock(return_value={"score": 90})
        component, page, _ = health_scan_dialog_factory(data_processor=data_processor, open_state=False)
        # open_=False 时 effect 早返回，run_quality_scan 未被调用
        data_processor.run_quality_scan.assert_not_called()
