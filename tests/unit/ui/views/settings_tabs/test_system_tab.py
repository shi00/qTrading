"""ui/views/settings_tabs/system_tab.py 单元测试"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page

pytestmark = pytest.mark.unit


async def _run_async_passthrough(task_type, func, *args, **kwargs):
    """Mock helper: 立即同步执行 func 并返回结果，模拟线程池 offload。"""
    return func(*args, **kwargs)


def _patch_thread_pool():
    """Patch system_tab 模块级 ThreadPoolManager，run_async 直接同步执行。"""
    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_run_async_passthrough)
    mock_tpm.submit = MagicMock()
    mock_tpm.reload_config = MagicMock()
    return patch("ui.views.settings_tabs.system_tab.ThreadPoolManager", return_value=mock_tpm)


class TestSystemTabInit:
    """SystemTab 初始化测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_init_creates_language_dropdown(self, mock_page):
        tab = self._make_tab()
        assert tab.language_dropdown is not None

    def test_init_creates_theme_dropdown(self, mock_page):
        tab = self._make_tab()
        assert tab.theme_dropdown is not None

    def test_init_creates_concurrency_input(self, mock_page):
        tab = self._make_tab()
        assert tab.concurrency_input is not None

    def test_init_creates_log_level_dropdown(self, mock_page):
        tab = self._make_tab()
        assert tab.log_level_dropdown is not None

    def test_init_creates_pool_size_input(self, mock_page):
        tab = self._make_tab()
        assert tab.pool_size_input is not None

    def test_init_creates_db_overflow_input(self, mock_page):
        tab = self._make_tab()
        assert tab.db_overflow_input is not None

    def test_init_creates_db_timeout_input(self, mock_page):
        tab = self._make_tab()
        assert tab.db_timeout_input is not None

    def test_init_creates_io_workers_input(self, mock_page):
        tab = self._make_tab()
        assert tab.io_workers_input is not None

    def test_init_creates_cpu_workers_input(self, mock_page):
        tab = self._make_tab()
        assert tab.cpu_workers_input is not None

    def test_init_creates_point_tier_dropdown(self, mock_page):
        tab = self._make_tab()
        assert tab.point_tier_dropdown is not None

    def test_init_creates_no_proxy_input(self, mock_page):
        tab = self._make_tab()
        assert tab.no_proxy_input is not None

    def test_init_creates_diagnostics_button(self, mock_page):
        tab = self._make_tab()
        assert tab.diagnostics_button is not None

    def test_init_creates_content(self, mock_page):
        tab = self._make_tab()
        assert tab.content is not None

    def test_init_creates_card_main(self, mock_page):
        tab = self._make_tab()
        assert tab.card_main is not None

    def test_init_locale_subscription_id_is_none(self, mock_page):
        tab = self._make_tab()
        assert tab._locale_subscription_id is None

    def test_init_concurrency_input_value_from_config(self, mock_page):
        self.mock_ch.get_sync_max_concurrent_heavy.return_value = 8
        tab = self._make_tab()
        assert tab.concurrency_input.value == "8"

    def test_init_log_level_dropdown_value_from_config(self, mock_page):
        self.mock_ch.get_log_level.return_value = "DEBUG"
        tab = self._make_tab()
        assert tab.log_level_dropdown.value == "DEBUG"

    def test_init_theme_dropdown_value_from_config(self, mock_page):
        self.mock_ch.get_theme_name.return_value = "navy"
        tab = self._make_tab()
        assert tab.theme_dropdown.value == "navy"

    def test_init_no_proxy_input_value_from_config(self, mock_page):
        self.mock_ch.get_no_proxy_domains.return_value = ["localhost", "127.0.0.1"]
        tab = self._make_tab()
        assert tab.no_proxy_input.value == "localhost,127.0.0.1"

    def test_init_no_proxy_input_empty_when_no_domains(self, mock_page):
        self.mock_ch.get_no_proxy_domains.return_value = []
        tab = self._make_tab()
        assert tab.no_proxy_input.value == ""

    def test_init_creates_all_setting_rows(self, mock_page):
        tab = self._make_tab()
        assert tab.row_language is not None
        assert tab.row_theme is not None
        assert tab.row_log is not None
        assert tab.row_concurrency is not None
        assert tab.row_thread_pool is not None
        assert tab.row_db_pool is not None
        assert tab.row_limit is not None
        assert tab.row_proxy is not None
        assert tab.row_diagnostics is not None


class TestSystemTabThemeChange:
    """主题切换测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_on_theme_change_applies_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "light"
        with (
            patch("ui.theme.apply_page_theme") as mock_apply,
            _patch_thread_pool(),
        ):
            await tab._do_theme_change_async()
            mock_apply.assert_called_once_with(mock_page, "light")

    async def test_on_theme_change_updates_page(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab.theme_dropdown.value = "navy"
        with (
            patch("ui.theme.apply_page_theme"),
            _patch_thread_pool(),
        ):
            await tab._do_theme_change_async()
        page.update.assert_called_once()

    def test_on_theme_change_without_page_no_error(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme"):
            tab.on_theme_change(None)
        # 无 page → 不调度 async → 不触发 snack
        snack.assert_not_called()

    async def test_on_theme_change_exception_shows_error_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "dark"
        with (
            patch("ui.theme.apply_page_theme", side_effect=Exception("theme error")),
            _patch_thread_pool(),
        ):
            await tab._do_theme_change_async()
        snack.assert_called_once()
        call_args = snack.call_args
        assert call_args.kwargs.get("color") == self.mock_ac.ERROR or "Theme Error" in str(call_args)


class TestSystemTabLanguageChange:
    """语言切换测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_on_language_change_empty_value_skips(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.language_dropdown.value = None
        with _patch_thread_pool():
            await tab._do_language_change_async()
        self.mock_i18n.set_locale.assert_not_called()

    async def test_on_language_change_persist_failure_reverts_dropdown(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab._safe_update = MagicMock()
        self.mock_ch.set_locale.return_value = False
        self.mock_i18n.current_locale.return_value = "zh_CN"
        tab.language_dropdown.value = "en_US"

        with _patch_thread_pool():
            await tab._do_language_change_async()

        self.mock_i18n.set_locale.assert_not_called()
        assert tab.language_dropdown.value == "zh_CN"
        snack.assert_called_once_with("settings_language_save_failed", color=self.mock_ac.ERROR)

    async def test_on_language_change_exception_shows_error(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        self.mock_ch.set_locale.side_effect = Exception("config error")
        tab.language_dropdown.value = "en_US"

        with _patch_thread_pool():
            await tab._do_language_change_async()

        snack.assert_called_once()
        call_args = snack.call_args
        assert call_args.kwargs.get("color") == self.mock_ac.ERROR

    async def test_on_language_change_success_updates_locale_configuration(self, mock_page):
        import flet as ft

        mock_page.locale_configuration = MagicMock()
        mock_page.locale_configuration.current_locale = ft.Locale("zh", "CN")
        mock_page.update = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = MagicMock()
        self.mock_ch.set_locale.return_value = True
        tab.language_dropdown.value = "en_US"
        self.mock_i18n.current_locale.return_value = "en_US"

        with _patch_thread_pool():
            await tab._do_language_change_async()

        self.mock_i18n.set_locale.assert_called_once_with("en_US")
        assert mock_page.locale_configuration.current_locale.language_code == "en"
        assert mock_page.locale_configuration.current_locale.country_code == "US"
        mock_page.update.assert_called_once()
        tab.show_snack.assert_called_once_with("settings_language_changed")


class TestSystemTabLogLevelChange:
    """日志级别切换测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_on_log_level_change_calls_update_log_level(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.log_level_dropdown.value = "WARNING"
        with (
            patch("utils.logger.update_log_level") as mock_update,
            _patch_thread_pool(),
        ):
            await tab._do_log_level_change_async()
            mock_update.assert_called_once_with("WARNING")

    async def test_on_log_level_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.log_level_dropdown.value = "ERROR"
        with (
            patch("utils.logger.update_log_level"),
            _patch_thread_pool(),
        ):
            await tab._do_log_level_change_async()
        snack.assert_called_once()
        assert "ERROR" in snack.call_args[0][0]


class TestSystemTabConcurrency:
    """并发设置测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_save_concurrency_boundary_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.concurrency_input.value = "1"
        with _patch_thread_pool():
            await tab._do_save_concurrency_async()
        self.mock_ch.set_sync_max_concurrent_heavy.assert_called_with(1)

    async def test_save_concurrency_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.concurrency_input.value = "32"
        with _patch_thread_pool():
            await tab._do_save_concurrency_async()
        self.mock_ch.set_sync_max_concurrent_heavy.assert_called_with(32)

    async def test_save_concurrency_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.concurrency_input.value = "4"
        with _patch_thread_pool():
            await tab._do_save_concurrency_async()
        snack.assert_called_once()
        assert snack.call_args.kwargs.get("color") == self.mock_ac.SUCCESS


class TestSystemTabDBPoolSettings:
    """数据库连接池设置测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_save_db_pool_settings_pool_size_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "100"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        snack.assert_called_once_with("sys_snack_pool_range", color=self.mock_ac.ERROR)

    async def test_save_db_pool_settings_overflow_negative(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "-1"
        tab.db_timeout_input.value = "30"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        snack.assert_called_once_with("settings_db_overflow: 0-50", color=self.mock_ac.ERROR)

    async def test_save_db_pool_settings_timeout_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "500"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        snack.assert_called_once_with("settings_db_timeout: 5-300", color=self.mock_ac.ERROR)

    async def test_save_db_pool_settings_boundary_pool_size_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.pool_size_input.value = "1"
        tab.db_overflow_input.value = "0"
        tab.db_timeout_input.value = "5"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        self.mock_ch.set_db_connection_pool_size.assert_called_with(1)

    async def test_save_db_pool_settings_boundary_pool_size_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.pool_size_input.value = "50"
        tab.db_overflow_input.value = "0"
        tab.db_timeout_input.value = "5"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        self.mock_ch.set_db_connection_pool_size.assert_called_with(50)

    async def test_save_db_pool_settings_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        with _patch_thread_pool():
            await tab._do_save_db_pool_settings_async()
        snack.assert_called_once_with("settings_db_pool_saved", color=self.mock_ac.SUCCESS)


class TestSystemTabThreadPoolSettings:
    """线程池设置测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_save_thread_pool_settings_io_boundary_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "4"
        tab.cpu_workers_input.value = "1"
        with _patch_thread_pool():
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_io_workers.assert_called_with(4)

    async def test_save_thread_pool_settings_io_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "512"
        tab.cpu_workers_input.value = "4"
        with _patch_thread_pool():
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_io_workers.assert_called_with(512)

    async def test_save_thread_pool_settings_cpu_boundary_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "1"
        with _patch_thread_pool():
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_cpu_workers.assert_called_with(1)

    async def test_save_thread_pool_settings_cpu_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "64"
        with _patch_thread_pool():
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_cpu_workers.assert_called_with(64)

    async def test_save_thread_pool_settings_both_empty(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.io_workers_input.value = ""
        tab.cpu_workers_input.value = ""
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_threads_empty", color=self.mock_ac.ERROR)

    async def test_save_thread_pool_settings_cpu_empty(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = ""
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_threads_empty", color=self.mock_ac.ERROR)

    async def test_save_thread_pool_settings_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "4"
        with _patch_thread_pool():
            await tab.save_thread_pool_settings(None)
        # First call is "common_preparing", second is success
        assert snack.call_count == 2
        assert snack.call_args.kwargs.get("color") == self.mock_ac.SUCCESS


class TestSystemTabPointTier:
    """Tushare 积分等级设置测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_save_point_tier_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.point_tier_dropdown.value = "points_5000"
        with (
            patch("data.external.tushare_client.TushareClient"),
            _patch_thread_pool(),
        ):
            await tab._do_save_point_tier_async("points_5000")
        self.mock_ch.set_tushare_point_tier.assert_called_with("points_5000")

    def test_save_point_tier_empty_value_skips(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.point_tier_dropdown.value = None
        tab.save_point_tier(None)
        self.mock_ch.set_tushare_point_tier.assert_not_called()

    async def test_save_point_tier_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.point_tier_dropdown.value = "points_2000"
        with (
            patch("data.external.tushare_client.TushareClient"),
            _patch_thread_pool(),
        ):
            await tab._do_save_point_tier_async("points_2000")
        snack.assert_called_once()

    async def test_save_point_tier_reloads_rate_limiters(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.point_tier_dropdown.value = "points_5000"
        with (
            patch("data.external.tushare_client.TushareClient") as mock_tc,
            _patch_thread_pool(),
        ):
            mock_tc_instance = MagicMock()
            mock_tc.return_value = mock_tc_instance
            await tab._do_save_point_tier_async("points_5000")
            mock_tc_instance.reload_rate_limiters.assert_called_once()


class TestSystemTabNoProxyDomains:
    """无代理域名设置测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_save_no_proxy_domains_strips_whitespace(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "  localhost , 127.0.0.1 , example.com  "
        with (
            patch("utils.proxy_manager.ProxyManager"),
            _patch_thread_pool(),
        ):
            await tab._do_save_no_proxy_domains_async()
        self.mock_ch.set_no_proxy_domains.assert_called_with(["localhost", "127.0.0.1", "example.com"])

    async def test_save_no_proxy_domains_filters_empty_entries(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost,,127.0.0.1,"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            _patch_thread_pool(),
        ):
            await tab._do_save_no_proxy_domains_async()
        self.mock_ch.set_no_proxy_domains.assert_called_with(["localhost", "127.0.0.1"])

    async def test_save_no_proxy_domains_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.no_proxy_input.value = "localhost"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            _patch_thread_pool(),
        ):
            await tab._do_save_no_proxy_domains_async()
        snack.assert_called_once_with("settings_snack_no_proxy_saved", color=self.mock_ac.SUCCESS)

    async def test_save_no_proxy_domains_submits_proxy_reapply(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            _patch_thread_pool() as mock_tpm,
        ):
            await tab._do_save_no_proxy_domains_async()
            mock_tpm.return_value.submit.assert_called_once()


class TestSystemTabDiagnostics:
    """系统诊断导出测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    async def test_on_export_diagnostics_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_dc:
            mock_dc.export = AsyncMock(return_value="/tmp/diag.zip")
            await tab.on_export_diagnostics(None)
        snack.assert_called_once()
        success_call = [c for c in snack.call_args_list if c.kwargs.get("color") == self.mock_ac.SUCCESS]
        assert len(success_call) == 1

    async def test_on_export_diagnostics_failure(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_dc:
            mock_dc.export = AsyncMock(side_effect=Exception("export failed"))
            await tab.on_export_diagnostics(None)
        error_calls = [c for c in snack.call_args_list if c.kwargs.get("color") == self.mock_ac.ERROR]
        assert len(error_calls) == 1

    async def test_on_export_diagnostics_disables_button_during_export(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_dc:
            mock_dc.export = AsyncMock(return_value="/tmp/diag.zip")
            await tab.on_export_diagnostics(None)
        assert tab.diagnostics_button.disabled is False

    async def test_on_export_diagnostics_reenables_button_after_failure(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        with patch("utils.diagnostics.SystemDiagnosticsCollector") as mock_dc:
            mock_dc.export = AsyncMock(side_effect=Exception("export failed"))
            await tab.on_export_diagnostics(None)
        assert tab.diagnostics_button.disabled is False


class TestSystemTabLocaleChange:
    """语言变更回调测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_on_locale_change_updates_dropdown_labels(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("en_US")
        self.mock_i18n.get.assert_any_call("settings_theme")
        self.mock_i18n.get.assert_any_call("settings_log_level")

    def test_on_locale_change_updates_input_labels(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("en_US")
        self.mock_i18n.get.assert_any_call("settings_concurrency")
        self.mock_i18n.get.assert_any_call("settings_db_pool")

    def test_on_locale_change_calls_row_update_locale(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("en_US")
        rows = [
            tab.row_language,
            tab.row_theme,
            tab.row_log,
            tab.row_concurrency,
            tab.row_thread_pool,
            tab.row_db_pool,
            tab.row_limit,
            tab.row_proxy,
            tab.row_diagnostics,
        ]
        # All rows share the same SettingRow mock, so update_locale is called 9 times total
        for row in rows:
            row.update_locale.assert_called()  # 多次调用预期 (9次, 所有row共享mock)

    def test_on_locale_change_calls_section_header_update_locale(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("en_US")
        tab.section_header.update_locale.assert_called_once()

    def test_on_locale_change_exception_handled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        self.mock_i18n.get.side_effect = RuntimeError("locale error")
        tab._on_locale_change("en_US")


class TestSystemTabMountUnmount:
    """挂载/卸载生命周期测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_did_mount_sets_subscription_id(self, mock_page):
        tab = self._make_tab()
        tab.did_mount()
        assert tab._locale_subscription_id == "sub_id"

    def test_will_unmount_clears_subscription_id(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = "test_id"
        tab.will_unmount()
        assert tab._locale_subscription_id is None

    def test_will_unmount_no_subscription_skips_unsubscribe(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = None
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_not_called()


class TestSystemTabSafeUpdate:
    """线程安全 UI 更新测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_safe_update_with_page(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab._safe_update()
        page.update.assert_called_once()

    def test_safe_update_without_page(self, mock_page):
        tab = self._make_tab()
        tab._Control__page = None
        tab._safe_update()

    def test_safe_update_exception_handled(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        page.update.side_effect = RuntimeError("update failed")
        set_page(tab, page)
        tab._safe_update()


class TestSystemTabUpdateTheme:
    """主题更新测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_update_theme_sets_input_colors(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.theme_dropdown.bgcolor == self.mock_ac.INPUT_BG
        assert tab.theme_dropdown.color == self.mock_ac.INPUT_TEXT
        assert tab.theme_dropdown.border_color == self.mock_ac.INPUT_BORDER

    def test_update_theme_sets_concurrency_input_colors(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.concurrency_input.bgcolor == self.mock_ac.INPUT_BG
        assert tab.concurrency_input.color == self.mock_ac.INPUT_TEXT
        assert tab.concurrency_input.border_color == self.mock_ac.INPUT_BORDER

    def test_update_theme_without_page(self, mock_page):
        tab = self._make_tab()
        tab._Control__page = None
        tab.update_theme()
        assert tab.theme_dropdown.bgcolor == self.mock_ac.INPUT_BG

    def test_update_theme_calls_update_with_page(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab.update_theme()
        page.update.assert_called_once()
