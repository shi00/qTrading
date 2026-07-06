import contextlib
import logging

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.unit.ui.conftest import set_page, wrap_mock_page
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
                    "daily_quotes": {
                        "ratio": 0.95,
                        "fresh_ratio": 0.90,
                        "type": "stock",
                    },
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
        with (
            patch("data.data_processor.DataProcessor") as mock_dp_cls,
            patch(
                "ui.components.health_report_dialog.HealthScanDialog",
                return_value=mock_scan,
            ) as mock_scan_cls,
        ):
            await dlg.run_deep_scan(None)
        dlg.close_dialog.assert_called_once_with(None)
        assert mock_scan in mock_page.overlay
        mock_scan.start_scan.assert_awaited_once()
        # UI-C2: DataProcessor is instantiated by caller and injected into HealthScanDialog
        mock_dp_cls.assert_called_once()
        mock_scan_cls.assert_called_once_with(mock_page, mock_dp_cls.return_value)

    def test_build_content_green_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("green"))
        assert dlg.content is not None

    def test_build_content_yellow_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("yellow"))
        assert dlg.content is not None

    def test_build_content_red_status(self, mock_page):
        dlg = self._make_dialog(mock_page, self._make_report("red"))
        assert dlg.content is not None

    def test_refresh_locale_rebuilds_actions(self, mock_page):
        """§5.8 规范 6：refresh_locale 应正确刷新文案（重建 actions），不抛出异常。"""
        dlg = self._make_dialog(mock_page, self._make_report("green"))
        set_page(dlg, mock_page)
        dlg.update = MagicMock()
        original_content = dlg.content

        dlg.refresh_locale()

        # content 被重建为新对象（_build_content 返回新树）
        assert dlg.content is not original_content
        # 仍然有 2 个 action（深度扫描 + 关闭）
        assert len(dlg.actions) == 2
        dlg.update.assert_called_once()
        # I18n.get 应被调用以刷新文案
        self.mock_i18n.get.assert_any_call("health_btn_deep_scan")
        self.mock_i18n.get.assert_any_call("common_close")

    def test_refresh_locale_swallows_exception(self, mock_page, caplog):
        """refresh_locale 异常时不应抛出，应降级为 logger.warning。"""
        dlg = self._make_dialog(mock_page, self._make_report("green"))
        set_page(dlg, mock_page)
        # 强制 I18n.get 抛异常以触发 try/except
        self.mock_i18n.get.side_effect = RuntimeError("i18n boom")

        with caplog.at_level(logging.WARNING, logger=dialog_logger.name):
            # 不应抛出异常
            dlg.refresh_locale()

        assert any("refresh_locale failed" in r.message and "i18n boom" in r.message for r in caplog.records)

    def test_dialog_size_default_without_page(self):
        """B1: _dialog_size 无 page_ref 时返回 (600, 600)。"""
        from ui.components.health_report_dialog import HealthReportDialog

        report = {
            "status": "green",
            "market": {"lag_days": 0, "latest_local": "2025-01-01"},
            "fundamentals": {"gap_count": 0, "sanity_errors": 0, "tables": {}},
            "reasons": [],
        }
        dlg = HealthReportDialog(page=None, report=report)
        assert dlg._cached_width == 600
        assert dlg._cached_height == 600

    def test_did_mount_subscribes_i18n(self, mock_page):
        """B2: did_mount 订阅 I18n。"""
        dlg = self._make_dialog(mock_page)
        dlg.did_mount()
        self.mock_i18n.subscribe.assert_called_once_with(dlg.refresh_locale)
        assert dlg._locale_subscription_id == "sub_id"

    def test_will_unmount_unsubscribes(self, mock_page):
        """B3: will_unmount 取消订阅并清理 id。"""
        dlg = self._make_dialog(mock_page)
        dlg.did_mount()
        dlg.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
        assert dlg._locale_subscription_id is None

    def test_build_content_with_reasons(self, mock_page):
        """B4: _build_content 含 reasons 时构建 issues_section。"""
        report = self._make_report()
        report["reasons"] = ["数据延迟", "缺失财务"]
        dlg = self._make_dialog(mock_page, report)

        # issues_section 是 Column 中的第 3 个控件 (index 2)
        issues_section = dlg.content.content.controls[2]
        # 有 reasons 时 issues_section 有 bgcolor 和 content
        assert issues_section.bgcolor is not None
        issues_column = issues_section.content
        # 第一个控件是标题 "common_reason"
        assert issues_column.controls[0].value == "common_reason"
        # 后续是 2 个 reason 行
        assert len(issues_column.controls) == 3  # 1 header + 2 reasons

    def test_refresh_locale_no_page_skips_update(self, mock_page):
        """B5: refresh_locale 无 page 时不调用 update。"""
        dlg = self._make_dialog(mock_page, self._make_report("green"))
        set_page(dlg, mock_page)
        # 模拟无 page 场景
        dlg._Control__page = None
        dlg.update = MagicMock()

        dlg.refresh_locale()

        dlg.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_deep_scan_fallback_no_open(self, mock_page):
        """B6: V1 删除双路径回退，page_ref.show_dialog 为唯一路径。

        原 B6 测试验证无 open 方法时的回退，V1 升级后双路径已删除，
        此测试改为验证 show_dialog 直接调用（无回退）。
        """
        dlg = self._make_dialog(mock_page)
        set_page(dlg, mock_page)
        dlg.close_dialog = MagicMock()

        mock_scan = MagicMock()
        mock_scan.start_scan = AsyncMock()
        with (
            patch("data.data_processor.DataProcessor"),
            patch(
                "ui.components.health_report_dialog.HealthScanDialog",
                return_value=mock_scan,
            ),
        ):
            await dlg.run_deep_scan(None)

        # V1: show_dialog 是唯一路径，无回退
        assert mock_scan in mock_page.overlay
        mock_scan.start_scan.assert_awaited_once()

    def test_init_logs_error_when_summary_fails(self, mock_page, caplog):
        """B7: __init__ 摘要日志异常路径。"""

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

        with caplog.at_level(logging.ERROR, logger=dialog_logger.name):
            dlg = self._make_dialog(mock_page, report)

        assert any("Error logging report summary" in r.message for r in caplog.records)
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
        set_page(dlg, mock_page)
        dlg.update = MagicMock()

        dlg.refresh_locale()

        assert dlg._title_text.value == "scan_title"
        assert dlg._close_btn.content == "common_close"
        dlg.update.assert_called_once()

    def test_refresh_locale_calls_show_results_when_visible(self, mock_page):
        """B10: refresh_locale 结果区域可见时调用 show_results。"""
        dlg = self._make_scan_dialog(mock_page)
        set_page(dlg, mock_page)
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
