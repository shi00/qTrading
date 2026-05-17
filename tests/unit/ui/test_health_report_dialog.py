import contextlib

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.unit.ui.conftest import set_page, wrap_mock_page


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
        patch("ui.components.health_report_dialog.HEALTH_THRESHOLD_FINANCIAL_EXCELLENT", 0.9),
        patch("ui.components.health_report_dialog.HEALTH_THRESHOLD_FINANCIAL_COVERAGE", 0.7),
        patch("ui.components.health_report_dialog.HEALTH_DEPTH_WARNING_RATIO", 0.5),
        patch("ui.components.health_report_dialog.HEALTH_THRESHOLD_BREADTH", 0.6),
    ]


class TestHealthReportDialog:
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
                    "daily_quotes": {"ratio": 0.95, "fresh_ratio": 0.90, "type": "stock"},
                },
            },
            "reasons": [],
        }

    def _make_dialog(self, mock_page, report=None, on_dismiss=None):
        from ui.components.health_report_dialog import HealthReportDialog

        return HealthReportDialog(
            page=mock_page,
            report=report or self._make_report(),
            on_dismiss=on_dismiss,
        )

    def test_dialog_creates_with_report_data(self, mock_page):
        report = self._make_report()
        callback = MagicMock()
        dlg = self._make_dialog(mock_page, report, callback)
        assert dlg.page_ref is mock_page
        assert dlg.report is report
        assert dlg.on_dismiss_callback is callback

    def test_dialog_has_two_actions(self, mock_page):
        dlg = self._make_dialog(mock_page)
        assert len(dlg.actions) == 2

    @pytest.mark.asyncio
    async def test_run_deep_scan_closes_and_opens_scan_dialog(self, mock_page):
        dlg = self._make_dialog(mock_page)
        set_page(dlg, mock_page)
        dlg.close_dialog = MagicMock()
        mock_scan = MagicMock()
        mock_scan.start_scan = AsyncMock()
        with patch("ui.components.health_report_dialog.HealthScanDialog", return_value=mock_scan):
            await dlg.run_deep_scan(None)
        dlg.close_dialog.assert_called_once_with(None)
        assert mock_scan in mock_page.overlay
        mock_scan.start_scan.assert_awaited_once()

    def test_build_content_green_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("green"))
        assert dlg.content is not None

    def test_build_content_yellow_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("yellow"))
        assert dlg.content is not None

    def test_build_content_red_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("red"))
        assert dlg.content is not None


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
