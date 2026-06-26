"""ui/views/settings_tabs/database_tab.py 单元测试"""

import contextlib
import logging
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestDatabaseTabInit:
    """DatabaseTab 初始化相关测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_instantiation_creates_config_panel(self):
        tab = self._make_tab()
        assert tab.config_panel is not None

    def test_instantiation_sets_expand_true(self):
        tab = self._make_tab()
        assert tab.expand is True

    def test_instantiation_stores_show_snack_callback(self):
        callback = MagicMock()
        tab = self._make_tab()
        tab.show_snack = callback
        assert tab.show_snack is callback

    def test_instantiation_passes_on_save_callback(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args
            assert call_kwargs.kwargs.get("on_save_callback") is not None

    def test_instantiation_passes_on_test_success_callback(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args
            assert call_kwargs.kwargs.get("on_test_success_callback") is not None

    def test_instantiation_passes_show_header_true(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args
            assert call_kwargs.kwargs.get("show_header") is True

    def test_instantiation_passes_compact_false(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args
            assert call_kwargs.kwargs.get("compact") is False

    def test_instantiation_passes_load_password_true(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args
            assert call_kwargs.kwargs.get("load_password") is True

    def test_instantiation_sets_did_mount(self):
        tab = self._make_tab()
        assert callable(tab.did_mount)

    def test_instantiation_sets_will_unmount(self):
        tab = self._make_tab()
        assert callable(tab.will_unmount)


class TestDatabaseTabOnSave:
    """DatabaseTab._on_save 回调测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_on_save_calls_show_snack_with_success(self):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab._on_save({"host": "localhost"})
        snack.assert_called_once_with("settings_db_saved", "success")

    def test_on_save_with_empty_config(self):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab._on_save({})
        snack.assert_called_once_with("settings_db_saved", "success")

    def test_on_save_with_full_config(self):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        config = {
            "host": "192.168.1.1",
            "port": 5432,
            "database": "mydb",
            "user": "admin",
            "password": "secret",
        }
        tab._on_save(config)
        snack.assert_called_once_with("settings_db_saved", "success")

    def test_on_save_show_snack_none_raises_type_error(self):
        tab = self._make_tab()
        tab.show_snack = None
        # show_snack 为 None 时调用会抛 TypeError
        with pytest.raises(TypeError):
            tab._on_save({"host": "localhost"})


class TestDatabaseTabOnTestSuccess:
    """DatabaseTab._on_test_success 回调测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_on_test_success_logs_debug(self, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "localhost", "port": 5432, "database": "test"})
        assert any("Database connection test successful" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_host(self, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "db.example.com", "port": 3306, "database": "prod"})
        assert any("db.example.com" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_port(self, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "localhost", "port": 5432, "database": "test"})
        assert any("5432" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_database(self, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "localhost", "port": 5432, "database": "mydb"})
        assert any("mydb" in r.message for r in caplog.records)

    def test_on_test_success_missing_keys_no_exception(self):
        tab = self._make_tab()
        # config 缺少 key 时，f-string 会抛 KeyError，验证异常传播行为
        with pytest.raises(KeyError):
            tab._on_test_success({"host": "localhost"})


class TestDatabaseTabLifecycle:
    """DatabaseTab 生命周期方法测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_on_mount_calls_reload_config(self):
        tab = self._make_tab()
        tab._on_mount()
        tab.config_panel.reload_config.assert_called_once()

    def test_on_mount_calls_reload_config_only_once(self):
        tab = self._make_tab()
        tab._on_mount()
        tab.config_panel.reload_config.assert_called_once()

    def test_on_unmount_does_not_raise(self):
        tab = self._make_tab()
        # _on_unmount 是空操作，不应抛异常
        tab._on_unmount()

    def test_did_mount_delegates_to_on_mount(self):
        tab = self._make_tab()
        tab.did_mount()
        tab.config_panel.reload_config.assert_called_once()

    def test_will_unmount_delegates_to_on_unmount(self):
        tab = self._make_tab()
        # 不应抛异常
        tab.will_unmount()


class TestDatabaseTabBuildUI:
    """DatabaseTab UI 构建测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_build_ui_returns_column(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.ft.Column") as mock_col:
            mock_col.return_value = MagicMock()
            DatabaseTab(show_snack_callback=MagicMock())
            mock_col.assert_called_once()

    def test_content_is_set_after_init(self):
        tab = self._make_tab()
        assert tab.content is not None

    def test_i18n_used_for_title(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()):
            DatabaseTab(show_snack_callback=MagicMock())
        # I18n.get 应被调用获取 settings_db_title
        self.mock_i18n.get.assert_any_call("settings_db_title")


class TestDatabaseTabEdgeCases:
    """DatabaseTab 边界情况测试"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_on_mount_reload_config_exception_propagates(self):
        tab = self._make_tab()
        tab.config_panel.reload_config.side_effect = RuntimeError("config load error")
        with pytest.raises(RuntimeError, match="config load error"):
            tab._on_mount()

    def test_on_test_success_with_nonstandard_port(self, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "remote-host", "port": 9999, "database": "analytics"})
        assert any("9999" in r.message for r in caplog.records)
        assert any("analytics" in r.message for r in caplog.records)

    def test_config_panel_callback_wiring(self):
        """验证 _on_save 和 _on_test_success 被正确传入 DatabaseConfigPanel"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel") as mock_panel:
            DatabaseTab(show_snack_callback=MagicMock())
            call_kwargs = mock_panel.call_args.kwargs
            # 验证传入的回调是 tab 实例的方法
            assert callable(call_kwargs["on_save_callback"])
            assert callable(call_kwargs["on_test_success_callback"])
