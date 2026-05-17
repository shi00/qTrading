from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from tests.unit.ui.conftest import set_page, wrap_mock_page


class TestDataExplorerView:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.DatabaseManager", MagicMock()),
            patch("ui.views.data_view.ThreadPoolManager", MagicMock()),
            patch("ui.views.data_view.MetaDataManager", MagicMock()),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    def _make_view(self):
        from ui.views.data_view import DataExplorerView

        return DataExplorerView()

    def test_instantiation_creates_loading_view(self):
        view = self._make_view()
        assert view.loading_view is not None
        assert view._ui_built is False

    def test_did_mount_sets_mounted_flag(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.did_mount()
        assert view._mounted is True

    def test_did_mount_skips_if_already_mounted(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._mounted = True
        view.did_mount()
        mock_page.run_task.assert_not_called()

    def test_will_unmount_clears_mounted_flag(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._mounted = True
        view.will_unmount()
        assert view._mounted is False

    def test_on_broadcast_message_cache_cleared(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._ui_built = True
        view.table_tab = MagicMock()
        view._on_broadcast_message("cache_cleared")
        assert view.table_tab._tables_loaded is False

    def test_on_broadcast_message_ignores_other(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._ui_built = True
        view.table_tab = MagicMock()
        view._on_broadcast_message("other_message")
        view.table_tab._tables_loaded = True

    def test_update_theme_propagates_to_tabs(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view.table_tab = MagicMock()
        view.sql_tab = MagicMock()
        view.update_theme()
        view.table_tab.update_theme.assert_called_once()
        view.sql_tab.update_theme.assert_called_once()

    def test_on_tab_changed_triggers_table_mount(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._ui_built = True
        view.tabs = MagicMock()
        view.tabs.selected_index = 0
        view.table_tab = MagicMock()
        view._on_tab_changed(None)
        mock_page.run_task.assert_called()

    def test_on_tab_changed_skips_if_not_built(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._ui_built = False
        view._on_tab_changed(None)

    def test_will_unmount_unsubscribes_pubsub(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._pubsub_subscribed = True
        view.will_unmount()
        mock_page.pubsub.unsubscribe.assert_called_once()
        assert view._pubsub_subscribed is False

    def test_will_unmount_cancels_mount_task(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        mock_task = MagicMock()
        view._mount_task = mock_task
        view.will_unmount()
        mock_task.cancel.assert_called_once()
        assert view._mount_task is None

    @pytest.mark.asyncio
    async def test_lazy_build_ui_creates_tabs(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        await view._lazy_build_ui()
        assert hasattr(view, "table_tab")
        assert hasattr(view, "sql_tab")
        assert hasattr(view, "tabs")

    @pytest.mark.asyncio
    async def test_lazy_build_ui_without_page(self):
        view = self._make_view()
        await view._lazy_build_ui()
        assert not hasattr(view, "table_tab")
        assert view._ui_built is False


class TestTableViewerTab:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_db = MagicMock()
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.MetaDataManager"),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    def _make_tab(self):
        from ui.views.data_view import TableViewerTab

        return TableViewerTab(self.mock_db)

    def test_instantiation_default_table(self):
        tab = self._make_tab()
        assert tab.current_table == "stock_basic"

    def test_instantiation_default_page_size(self):
        tab = self._make_tab()
        assert tab.page_size == 50

    def test_instantiation_initial_page(self):
        tab = self._make_tab()
        assert tab.current_page == 1

    def test_instantiation_not_loading(self):
        tab = self._make_tab()
        assert tab._is_loading is False

    def test_instantiation_tables_not_loaded(self):
        tab = self._make_tab()
        assert tab._tables_loaded is False

    def test_did_mount_sets_flag(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.did_mount()
        assert tab._mounted is True

    def test_did_mount_skips_if_already_mounted(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._mounted = True
        tab.did_mount()
        assert tab._mounted is True

    def test_will_unmount_clears_flag(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._mounted = True
        tab.will_unmount()
        assert tab._mounted is False

    def test_update_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.table_selector.bgcolor == self.mock_ac.INPUT_BG
        assert tab.btn_query.icon_color == self.mock_ac.PRIMARY

    @pytest.mark.asyncio
    async def test_did_mount_async_loads_tables(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(return_value=["stock_basic", "daily_quotes"])
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            tab._load_schema_and_data = AsyncMock()
            await tab.did_mount_async()
        assert tab._tables_loaded is True
        assert tab.table_selector.value == "stock_basic"

    @pytest.mark.asyncio
    async def test_did_mount_async_handles_error(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(side_effect=Exception("DB error"))
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            await tab.did_mount_async()
        assert tab._tables_loaded is False

    @pytest.mark.asyncio
    async def test_did_mount_async_skips_if_already_loaded(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._tables_loaded = True
        await tab.did_mount_async()

    @pytest.mark.asyncio
    async def test_on_table_changed_updates_table(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.table_selector.value = "daily_quotes"
        tab._load_schema_and_data = AsyncMock()
        await tab._on_table_changed(None)
        assert tab.current_table == "daily_quotes"
        assert tab.current_page == 1

    @pytest.mark.asyncio
    async def test_refresh_data_rows_with_data(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.table_columns = ["col1", "col2"]
        tab.numeric_cols = {"col1"}
        tab.filter_val.value = ""
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(
            side_effect=[
                10,
                pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]}),
            ]
        )
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            await tab._refresh_data_rows()
        assert tab.total_rows == 10
        assert len(tab.data_table.rows) == 2


class TestSQLConsoleTab:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_db = MagicMock()
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.MetaDataManager"),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    def _make_tab(self):
        from ui.views.data_view import SQLConsoleTab

        return SQLConsoleTab(self.mock_db)

    def test_instantiation_creates_editor(self):
        tab = self._make_tab()
        assert tab.sql_editor is not None

    def test_instantiation_creates_run_button(self):
        tab = self._make_tab()
        assert tab.btn_run is not None

    def test_set_sql_updates_editor(self):
        tab = self._make_tab()
        tab.sql_editor.update = MagicMock()
        tab._set_sql("SELECT 1")
        assert tab.sql_editor.value == "SELECT 1"

    @pytest.mark.asyncio
    async def test_run_query_empty_sql(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = ""
        await tab._run_query(None)

    def test_update_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.sql_editor.bgcolor == self.mock_ac.INPUT_BG

    @pytest.mark.asyncio
    async def test_run_query_with_valid_sql(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = "SELECT * FROM stock_basic LIMIT 10"
        df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(return_value={"success": True, "data": df})
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            await tab._run_query(None)
        assert tab.result_table.visible is True
        assert tab.empty_state.visible is False
        assert len(tab.result_table.rows) == 2

    @pytest.mark.asyncio
    async def test_run_query_with_error_result(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = "SELECT * FROM nonexistent"
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(return_value={"success": False, "error": "Table not found"})
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            await tab._run_query(None)
        assert tab.result_table.visible is False
        assert tab.empty_state.visible is True

    @pytest.mark.asyncio
    async def test_run_query_with_exception(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = "INVALID SQL"
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(side_effect=Exception("Connection error"))
        with patch("ui.views.data_view.ThreadPoolManager", return_value=mock_tpm):
            await tab._run_query(None)
        assert tab.result_table.visible is False
        assert tab.empty_state.visible is True
