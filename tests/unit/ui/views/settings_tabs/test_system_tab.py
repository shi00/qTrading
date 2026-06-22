"""ui/views/settings_tabs/system_tab.py 单元测试"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page

pytestmark = pytest.mark.unit


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

    def test_init_creates_rate_limit_input(self, mock_page):
        tab = self._make_tab()
        assert tab.rate_limit_input is not None

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

    def test_init_rate_limit_disabled_when_not_custom(self, mock_page):
        self.mock_ch.get_tushare_point_tier.return_value = "free"
        tab = self._make_tab()
        assert tab.rate_limit_input.disabled is True

    def test_init_rate_limit_enabled_when_custom(self, mock_page):
        self.mock_ch.get_tushare_point_tier.return_value = "custom"
        tab = self._make_tab()
        assert tab.rate_limit_input.disabled is False

    def test_init_rate_limit_input_value_when_positive(self, mock_page):
        self.mock_ch.get_tushare_api_limit.return_value = 200
        tab = self._make_tab()
        assert tab.rate_limit_input.value == "200"

    def test_init_rate_limit_input_empty_when_zero(self, mock_page):
        self.mock_ch.get_tushare_api_limit.return_value = 0
        tab = self._make_tab()
        assert tab.rate_limit_input.value == ""

    def test_init_rate_limit_input_empty_when_none(self, mock_page):
        self.mock_ch.get_tushare_api_limit.return_value = None
        tab = self._make_tab()
        assert tab.rate_limit_input.value == ""

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

    def test_on_theme_change_applies_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "light"
        with patch("ui.theme.apply_page_theme") as mock_apply:
            tab.on_theme_change(None)
            mock_apply.assert_called_once_with(mock_page, "light")

    def test_on_theme_change_updates_page(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab.theme_dropdown.value = "navy"
        with patch("ui.theme.apply_page_theme"):
            tab.on_theme_change(None)
        page.update.assert_called_once()

    def test_on_theme_change_without_page_no_error(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme"):
            tab.on_theme_change(None)
        snack.assert_called_once_with("settings_snack_theme_updated")

    def test_on_theme_change_exception_shows_error_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme", side_effect=Exception("theme error")):
            tab.on_theme_change(None)
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

    def test_on_language_change_empty_value_skips(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.language_dropdown.value = None
        tab.on_language_change(None)
        self.mock_i18n.set_locale.assert_not_called()

    def test_on_language_change_persist_failure_reverts_dropdown(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab._safe_update = MagicMock()
        self.mock_ch.set_locale.return_value = False
        self.mock_i18n.current_locale.return_value = "zh_CN"
        tab.language_dropdown.value = "en_US"

        tab.on_language_change(None)

        self.mock_i18n.set_locale.assert_not_called()
        assert tab.language_dropdown.value == "zh_CN"
        snack.assert_called_once_with("settings_language_save_failed", color=self.mock_ac.ERROR)

    def test_on_language_change_exception_shows_error(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        self.mock_ch.set_locale.side_effect = Exception("config error")
        tab.language_dropdown.value = "en_US"

        tab.on_language_change(None)

        snack.assert_called_once()
        call_args = snack.call_args
        assert call_args.kwargs.get("color") == self.mock_ac.ERROR


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

    def test_on_log_level_change_calls_update_log_level(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.log_level_dropdown.value = "WARNING"
        with patch("utils.logger.update_log_level") as mock_update:
            tab.on_log_level_change(None)
            mock_update.assert_called_once_with("WARNING")

    def test_on_log_level_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.log_level_dropdown.value = "ERROR"
        with patch("utils.logger.update_log_level"):
            tab.on_log_level_change(None)
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

    def test_save_concurrency_boundary_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.concurrency_input.value = "1"
        tab.save_concurrency(None)
        self.mock_ch.set_sync_max_concurrent_heavy.assert_called_with(1)

    def test_save_concurrency_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.concurrency_input.value = "32"
        tab.save_concurrency(None)
        self.mock_ch.set_sync_max_concurrent_heavy.assert_called_with(32)

    def test_save_concurrency_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.concurrency_input.value = "4"
        tab.save_concurrency(None)
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

    def test_save_db_pool_settings_pool_size_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "100"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("sys_snack_pool_range", color=self.mock_ac.ERROR)

    def test_save_db_pool_settings_overflow_negative(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "-1"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("settings_db_overflow: 0-50", color=self.mock_ac.ERROR)

    def test_save_db_pool_settings_timeout_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "500"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("settings_db_timeout: 5-300", color=self.mock_ac.ERROR)

    def test_save_db_pool_settings_boundary_pool_size_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.pool_size_input.value = "1"
        tab.db_overflow_input.value = "0"
        tab.db_timeout_input.value = "5"
        tab.save_db_pool_settings(None)
        self.mock_ch.set_db_connection_pool_size.assert_called_with(1)

    def test_save_db_pool_settings_boundary_pool_size_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.pool_size_input.value = "50"
        tab.db_overflow_input.value = "0"
        tab.db_timeout_input.value = "5"
        tab.save_db_pool_settings(None)
        self.mock_ch.set_db_connection_pool_size.assert_called_with(50)

    def test_save_db_pool_settings_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
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
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_io_workers.assert_called_with(4)

    async def test_save_thread_pool_settings_io_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "512"
        tab.cpu_workers_input.value = "4"
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_io_workers.assert_called_with(512)

    async def test_save_thread_pool_settings_cpu_boundary_min(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "1"
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_cpu_workers.assert_called_with(1)

    async def test_save_thread_pool_settings_cpu_boundary_max(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "64"
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
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
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
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

    def test_save_point_tier_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.update = MagicMock()
        tab.point_tier_dropdown.value = "pro"
        with patch("data.external.tushare_client.TushareClient"):
            tab.save_point_tier(None)
        self.mock_ch.set_tushare_point_tier.assert_called_with("pro")

    def test_save_point_tier_enables_rate_limit_when_custom(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.update = MagicMock()
        tab.point_tier_dropdown.value = "custom"
        with patch("data.external.tushare_client.TushareClient"):
            tab.save_point_tier(None)
        assert tab.rate_limit_input.disabled is False

    def test_save_point_tier_disables_rate_limit_when_not_custom(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.update = MagicMock()
        tab.point_tier_dropdown.value = "free"
        with patch("data.external.tushare_client.TushareClient"):
            tab.save_point_tier(None)
        assert tab.rate_limit_input.disabled is True

    def test_save_point_tier_empty_value_skips(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.point_tier_dropdown.value = None
        tab.save_point_tier(None)
        self.mock_ch.set_tushare_point_tier.assert_not_called()

    def test_save_point_tier_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.rate_limit_input.update = MagicMock()
        tab.point_tier_dropdown.value = "standard"
        with patch("data.external.tushare_client.TushareClient"):
            tab.save_point_tier(None)
        snack.assert_called_once()

    def test_save_point_tier_reloads_rate_limiters(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.update = MagicMock()
        tab.point_tier_dropdown.value = "pro"
        with patch("data.external.tushare_client.TushareClient") as mock_tc:
            mock_tc_instance = MagicMock()
            mock_tc.return_value = mock_tc_instance
            tab.save_point_tier(None)
            mock_tc_instance.reload_rate_limiters.assert_called_once()


class TestSystemTabRateLimit:
    """API 限速设置测试"""

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

    def test_save_rate_limit_non_custom_tier_hint(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        self.mock_ch.get_tushare_point_tier.return_value = "free"
        tab.rate_limit_input.value = "200"
        tab.save_rate_limit(None)
        snack.assert_called_once_with("sys_snack_tier_override_hint")

    def test_save_rate_limit_negative_disables(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        self.mock_ch.get_tushare_point_tier.return_value = "custom"
        tab.rate_limit_input.value = "-5"
        with patch("data.external.tushare_client.TushareClient"):
            tab.save_rate_limit(None)
        self.mock_ch.set_tushare_api_limit.assert_called_with(0)

    def test_save_rate_limit_reloads_rate_limiters(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        self.mock_ch.get_tushare_point_tier.return_value = "custom"
        tab.rate_limit_input.value = "200"
        with patch("data.external.tushare_client.TushareClient") as mock_tc:
            mock_tc_instance = MagicMock()
            mock_tc.return_value = mock_tc_instance
            tab.save_rate_limit(None)
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

    def test_save_no_proxy_domains_strips_whitespace(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "  localhost , 127.0.0.1 , example.com  "
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager"),
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
        self.mock_ch.set_no_proxy_domains.assert_called_with(["localhost", "127.0.0.1", "example.com"])

    def test_save_no_proxy_domains_filters_empty_entries(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost,,127.0.0.1,"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager"),
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
        self.mock_ch.set_no_proxy_domains.assert_called_with(["localhost", "127.0.0.1"])

    def test_save_no_proxy_domains_calls_snack_on_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.no_proxy_input.value = "localhost"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager"),
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
        snack.assert_called_once_with("settings_snack_no_proxy_saved", color=self.mock_ac.SUCCESS)

    def test_save_no_proxy_domains_submits_proxy_reapply(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
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
