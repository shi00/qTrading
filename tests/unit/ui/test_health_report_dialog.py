import contextlib
import logging

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

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
        """验证通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "ft.use_state(I18n.get_observable_state)" in content

    def test_pure_functions_preserved(self) -> None:
        """验证模块级纯函数保留导出。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "health_report_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")
        assert "def _health_dialog_size(" in content
        assert "def _log_report_summary(" in content
        assert "def _build_health_content(" in content


class TestHealthScoreCard:
    # HealthScoreCard.__init__ is marked pragma: no cover, so we test
    # the mapping logic via class attributes instead of instantiation.
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_status_map_green_has_excellent_key(self):
        from ui.components.health_report_dialog import HealthScoreCard

        _, _, i18n_key = HealthScoreCard._STATUS_MAP["green"]
        assert i18n_key == "health_status_excellent"

    def test_status_map_yellow_has_warning_key(self):
        from ui.components.health_report_dialog import HealthScoreCard

        _, _, i18n_key = HealthScoreCard._STATUS_MAP["yellow"]
        assert i18n_key == "health_status_warning"

    def test_default_status_has_critical_key(self):
        from ui.components.health_report_dialog import HealthScoreCard

        _, _, i18n_key = HealthScoreCard._DEFAULT_STATUS
        assert i18n_key == "health_status_critical"

    def test_unknown_status_falls_to_default(self):
        from ui.components.health_report_dialog import HealthScoreCard

        result = HealthScoreCard._STATUS_MAP.get("red", HealthScoreCard._DEFAULT_STATUS)
        assert result == HealthScoreCard._DEFAULT_STATUS


class TestKeyMetricsGrid:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def _make_grid(self, lag_days=0, gap_count=0, sanity_errors=0, latest_local="N/A"):
        from ui.components.health_report_dialog import KeyMetricsGrid

        market = {"lag_days": lag_days, "latest_local": latest_local}
        fundamentals = {"gap_count": gap_count, "sanity_errors": sanity_errors}
        return KeyMetricsGrid(market, fundamentals)

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
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def test_creates_with_stock_tables(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        tables = {
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = CoverageDetailTable(tables)
        assert len(table.controls) == 2

    def test_creates_with_global_tables(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        tables = {
            "macro_economy": {"ratio": 1.0, "covered": 100, "type": "global"},
        }
        table = CoverageDetailTable(tables)
        assert len(table.controls) == 2

    def test_creates_with_mixed_tables(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        tables = {
            "macro_economy": {"ratio": 1.0, "covered": 100, "type": "global"},
            "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
        }
        table = CoverageDetailTable(tables)
        assert len(table.controls) == 5

    def test_create_row_high_ratio_uses_success_color(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        table = CoverageDetailTable({})
        row = table._create_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.SUCCESS

    def test_create_row_medium_ratio_uses_warning_color(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        table = CoverageDetailTable({})
        row = table._create_row("daily_quotes", {"ratio": 0.8, "fresh_ratio": 0.70, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.WARNING

    def test_create_row_low_ratio_uses_error_color(self):
        from ui.components.health_report_dialog import CoverageDetailTable

        table = CoverageDetailTable({})
        row = table._create_row("daily_quotes", {"ratio": 0.3, "fresh_ratio": 0.20, "type": "stock"})
        progress_bar = row.content.controls[1]
        assert progress_bar.color == self.mock_ac.ERROR

    def test_create_row_uses_i18n_name_when_available(self):
        """覆盖 263->266 分支：I18n.get 返回非 key 值时不走 fallback。"""
        from ui.components.health_report_dialog import CoverageDetailTable

        # 让 I18n.get 对 tab_ 前缀的 key 返回翻译值
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}" if key.startswith("tab_") else key

        table = CoverageDetailTable({})
        row = table._create_row("daily_quotes", {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"})

        # 验证使用了 i18n 翻译名而非 fallback
        name_text = row.content.controls[0].controls[1]
        assert name_text.value == "translated_tab_daily_quotes"


class TestHealthScanDialog:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        with contextlib.ExitStack() as stack:
            for p in _apply_patches(mock_i18n, mock_app_colors):
                stack.enter_context(p)
            yield

    def _make_scan_dialog(self, mock_page, data_processor=None):
        from ui.components.health_report_dialog import HealthScanDialog

        return HealthScanDialog(
            page=mock_page,
            data_processor=data_processor or MagicMock(),
        )

    def test_constructor_stores_data_processor(self, mock_page):
        # UI-C2: DataProcessor is injected via constructor, not hardcoded
        mock_dp = MagicMock()
        dlg = self._make_scan_dialog(mock_page, mock_dp)
        assert dlg._data_processor is mock_dp

    @pytest.mark.asyncio
    async def test_start_scan_uses_injected_data_processor(self, mock_page):
        # UI-C2: start_scan uses the injected DataProcessor instance
        mock_dp = MagicMock()
        mock_dp.run_quality_scan = AsyncMock(return_value={"score": 90, "tier": 3})
        dlg = self._make_scan_dialog(mock_page, mock_dp)
        dlg.show_results = MagicMock()
        await dlg.start_scan()
        mock_dp.run_quality_scan.assert_awaited_once()
        dlg.show_results.assert_called_once_with({"score": 90, "tier": 3})

    @pytest.mark.asyncio
    async def test_start_scan_does_not_instantiate_data_processor(self, mock_page):
        # UI-C2: start_scan must NOT instantiate DataProcessor internally
        with patch("data.data_processor.DataProcessor") as mock_dp_cls:
            mock_dp = MagicMock()
            mock_dp.run_quality_scan = AsyncMock(return_value={"score": 90, "tier": 3})
            dlg = self._make_scan_dialog(mock_page, mock_dp)
            dlg.show_results = MagicMock()
            await dlg.start_scan()
            mock_dp_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_progress_schedules_via_run_coroutine_threadsafe(self, mock_page):
        # UI-C1: on_progress must schedule UI updates via asyncio.run_coroutine_threadsafe
        # instead of directly calling page_ref.update() from a worker thread
        import asyncio

        mock_dp = MagicMock()
        mock_dp.run_quality_scan = AsyncMock(return_value={"score": 90, "tier": 3})
        dlg = self._make_scan_dialog(mock_page, mock_dp)
        dlg.show_results = MagicMock()

        # Capture the progress_callback to invoke it manually
        captured = {}

        async def fake_scan(sample_size=50, progress_callback=None):
            captured["cb"] = progress_callback
            if progress_callback:
                progress_callback(10, 100, "step 1")
                progress_callback(50, 100, "step 2")
            return {"score": 90, "tier": 3}

        mock_dp.run_quality_scan = fake_scan

        def schedule_side_effect(coro, loop):
            coro.close()  # Avoid "coroutine never awaited" warning
            return MagicMock()

        with patch("asyncio.run_coroutine_threadsafe", side_effect=schedule_side_effect) as mock_schedule:
            await dlg.start_scan()

        # Verify run_coroutine_threadsafe was called for each progress update
        assert mock_schedule.call_count == 2
        # Verify the loop (second positional arg) is the running event loop
        expected_loop = asyncio.get_event_loop()
        for call in mock_schedule.call_args_list:
            assert call.args[1] is expected_loop

    @pytest.mark.asyncio
    async def test_on_progress_does_not_call_page_update_directly(self, mock_page):
        # UI-C1: on_progress must NOT directly call page_ref.update()
        mock_page.update = MagicMock()
        mock_dp = MagicMock()

        async def fake_scan(sample_size=50, progress_callback=None):
            if progress_callback:
                progress_callback(10, 100, "step 1")
            return {"score": 90, "tier": 3}

        mock_dp.run_quality_scan = fake_scan
        dlg = self._make_scan_dialog(mock_page, mock_dp)
        dlg.show_results = MagicMock()

        def schedule_side_effect(coro, loop):
            coro.close()  # Avoid "coroutine never awaited" warning
            return MagicMock()

        with patch("asyncio.run_coroutine_threadsafe", side_effect=schedule_side_effect):
            await dlg.start_scan()

        # page_ref.update should NOT be called during progress updates
        # (only show_results or error handling may call it)
        mock_page.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_progress_updates_ui(self, mock_page):
        # UI-C1: _update_progress coroutine updates the progress bar and status text
        mock_page.update = MagicMock()
        dlg = self._make_scan_dialog(mock_page)
        await dlg._update_progress(25, 100, "quarter done")
        assert dlg.progress_bar.value == 0.25
        assert dlg.status_text.value == "quarter done"
        mock_page.update.assert_called_once()

    def test_dialog_size_default_without_page(self):
        """B8: _dialog_size 无 page_ref 时返回 (450, 300)。"""
        from ui.components.health_report_dialog import HealthScanDialog

        dlg = HealthScanDialog(page=None, data_processor=MagicMock())
        assert dlg._cached_width == 450
        assert dlg._cached_height == 300

    def test_refresh_locale_updates_texts(self, mock_page):
        """B9: refresh_locale 正常路径。"""
        dlg = self._make_scan_dialog(mock_page)
        dlg.page = mock_page
        dlg.update = MagicMock()

        dlg.refresh_locale()

        assert dlg._title_text.value == "scan_title"
        assert dlg._close_btn.content == "common_close"
        dlg.update.assert_called_once()

    def test_refresh_locale_calls_show_results_when_visible(self, mock_page):
        """B10: refresh_locale 结果区域可见时调用 show_results。"""
        dlg = self._make_scan_dialog(mock_page)
        dlg.page = mock_page
        dlg.result_content.visible = True
        dlg._last_result = {"score": 90, "tier": 3}
        dlg.show_results = MagicMock()
        dlg.update = MagicMock()

        dlg.refresh_locale()

        dlg.show_results.assert_called_once_with({"score": 90, "tier": 3})

    @pytest.mark.asyncio
    async def test_start_scan_handles_error(self, mock_page):
        """B11: start_scan 异常路径。"""
        mock_dp = MagicMock()
        mock_dp.run_quality_scan = AsyncMock(side_effect=RuntimeError("scan failed"))
        dlg = self._make_scan_dialog(mock_page, mock_dp)
        mock_page.update = MagicMock()

        await dlg.start_scan()

        assert dlg.status_text.value == "db_err_format"
        mock_page.update.assert_called_once()
