import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pandas as pd
import pytest

from tests.unit.ui.conftest import set_page, wrap_mock_page

pytestmark = pytest.mark.unit


class TestDataExplorerView:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.DataExplorerViewModel"),
            patch("ui.views.data_view.ThreadPoolManager", MagicMock()),
            patch("ui.views.data_view.MetaDataManager", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

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
        view.vm.dispose.assert_called_once()

    def test_on_broadcast_message_cache_cleared(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._ui_built = True
        view._on_broadcast_message("cache_cleared")
        assert view.vm.tables_loaded is False

    def test_on_broadcast_message_ignores_other(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._ui_built = True
        view.vm.tables_loaded = True
        view._on_broadcast_message("other_message")
        assert view.vm.tables_loaded is True

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
        mock_page.run_task.assert_called_once()

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

    def test_refresh_locale_cascades_to_invalidate_cache(self, mock_page):
        """§5.8 规范 8：refresh_locale 通过级联调用子 tab 使 MetaDataManager 缓存失效。

        DataExplorerView 自身不直接调用 invalidate_cache，而是通过级联调用
        table_tab.refresh_locale() 与 sql_tab.refresh_locale() 触发；子 tab 的
        refresh_locale 内部各自调用一次 invalidate_cache（见 data_view.py:323/911）。
        本测试用 side_effect 模拟子 tab 的真实行为，验证级联路径生效。
        """
        import ui.views.data_view as mod

        view = self._make_view()
        set_page(view, mock_page)
        # 构造 refresh_locale 依赖的最小 UI 状态
        view._loading_text = MagicMock()
        view.tabs = MagicMock()
        view.tabs.tabs = [MagicMock(), MagicMock()]

        # 让子 tab 的 refresh_locale 真正调用 invalidate_cache（模拟真实行为）
        def _cascade_invalidate():
            mod.MetaDataManager.invalidate_cache()

        view.table_tab = MagicMock()
        view.table_tab.refresh_locale.side_effect = _cascade_invalidate
        view.sql_tab = MagicMock()
        view.sql_tab.refresh_locale.side_effect = _cascade_invalidate

        mod.MetaDataManager.invalidate_cache.reset_mock()
        view.refresh_locale()

        view.table_tab.refresh_locale.assert_called_once()
        view.sql_tab.refresh_locale.assert_called_once()
        # 子 tab 各调用一次，共 2 次
        assert mod.MetaDataManager.invalidate_cache.call_count == 2


class TestTableViewerTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.MetaDataManager"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.data_view import TableViewerTab

        mock_vm = MagicMock()
        mock_vm.current_table = "stock_basic"
        mock_vm.current_page = 1
        mock_vm.page_size = 50
        mock_vm.total_rows = 0
        mock_vm.table_columns = []
        mock_vm.numeric_cols = set()
        mock_vm.sort_col_index = None
        mock_vm.sort_asc = True
        mock_vm.is_loading = False
        mock_vm.tables_loaded = False
        mock_vm.current_data = pd.DataFrame()
        mock_vm.error_message = None
        mock_vm.filter_col = None
        mock_vm.filter_op = "="
        mock_vm.filter_val = ""
        return TableViewerTab(mock_vm)

    def test_instantiation_default_table(self):
        tab = self._make_tab()
        assert tab.vm.current_table == "stock_basic"

    def test_instantiation_default_page_size(self):
        tab = self._make_tab()
        assert tab.vm.page_size == 50

    def test_instantiation_initial_page(self):
        tab = self._make_tab()
        assert tab.vm.current_page == 1

    def test_instantiation_not_loading(self):
        tab = self._make_tab()
        assert tab.vm.is_loading is False

    def test_instantiation_tables_not_loaded(self):
        tab = self._make_tab()
        assert tab.vm.tables_loaded is False

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

        async def _mock_init_tables():
            tab.vm.tables_loaded = True
            return ["stock_basic", "daily_quotes"]

        tab.vm.init_tables = AsyncMock(side_effect=_mock_init_tables)
        tab.vm.current_table = "stock_basic"
        tab._load_schema_and_data = AsyncMock()
        await tab.did_mount_async()
        assert tab.vm.tables_loaded is True
        assert tab.table_selector.value == "stock_basic"

    @pytest.mark.asyncio
    async def test_did_mount_async_handles_error(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.vm.init_tables = AsyncMock(side_effect=Exception("DB error"))
        await tab.did_mount_async()
        assert tab.vm.tables_loaded is False

    @pytest.mark.asyncio
    async def test_did_mount_async_skips_if_already_loaded(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.vm.tables_loaded = True
        await tab.did_mount_async()

    @pytest.mark.asyncio
    async def test_on_table_changed_updates_table(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.table_selector.value = "daily_quotes"
        tab._load_schema_and_data = AsyncMock()
        await tab._on_table_changed(None)
        assert tab.vm.current_table == "daily_quotes"
        assert tab.vm.current_page == 1

    @pytest.mark.asyncio
    async def test_rebuild_table_rows_with_data(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.vm.table_columns = ["col1", "col2"]
        tab.vm.numeric_cols = {"col1"}
        tab.vm.current_data = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        tab._rebuild_table_rows()
        assert len(tab.data_table.rows) == 2

    def test_refresh_locale_preserves_dropdown_values(self, mock_page):
        """§5.8 规范 4：refresh_locale 重建 options 后 value 必须保留。"""
        import ui.views.data_view as mod

        tab = self._make_tab()
        set_page(tab, mock_page)
        # 配置 tables_list 与 get_table_alias 返回，确保 table_selector.options 能重建
        tab.vm.tables_list = ["stock_basic", "daily"]
        mod.MetaDataManager.get_table_alias.return_value = "alias"
        tab.table_selector.value = "stock_basic"
        tab.filter_op.value = "="
        original_table = tab.table_selector.value
        original_op = tab.filter_op.value
        tab.refresh_locale()
        assert tab.table_selector.value == original_table
        assert tab.filter_op.value == original_op
        assert tab.table_selector.options is not None
        assert len(tab.table_selector.options) > 0
        assert tab.filter_op.options is not None
        assert len(tab.filter_op.options) > 0

    def test_refresh_locale_preserves_filter_col_value(self, mock_page):
        """§5.8 规范 4：refresh_locale 重建 filter_col.options 后 value 必须保留。

        _populate_filter_columns 会重置 filter_col.value 为 table_columns[0]，
        因此设置 table_columns[0]="col1" 并预选 "col1" 时，refresh_locale 后应仍为 "col1"
        （data_view.py:475-477 先置 None 强制 dirty 再回填 table_columns[0]）。
        """
        import ui.views.data_view as mod

        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.vm.tables_list = ["stock_basic", "daily"]
        tab.vm.current_table = "stock_basic"
        tab.vm.table_columns = ["col1", "col2"]
        tab.vm.current_data = pd.DataFrame()
        mod.MetaDataManager.get_table_alias.return_value = "alias"
        mod.MetaDataManager.get_column_alias.return_value = "col_alias"
        tab.filter_col.value = "col1"
        tab.refresh_locale()
        assert tab.filter_col.value == "col1"
        assert tab.filter_col.options is not None
        assert len(tab.filter_col.options) == 2

    def test_news_cell_no_hardcoded_width(self, mock_page):
        """§6.3：新闻 cell 不应硬编码 width=400，应使用 expand=True 自适应宽度。"""
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.vm.current_table = "market_news"
        tab.vm.table_columns = ["content", "col2"]
        tab.vm.numeric_cols = set()
        tab.vm.current_data = pd.DataFrame({"content": ["新闻长文本内容"], "col2": ["a"]})
        tab._rebuild_table_rows()

        assert len(tab.data_table.rows) == 1
        containers = [cell.content for cell in tab.data_table.rows[0].cells if isinstance(cell.content, ft.Container)]
        assert all(c.width != 400 for c in containers), "存在 cell 硬编码 width=400"
        news_containers = [c for c in containers if c.alignment == ft.alignment.top_left]
        assert news_containers, "未找到新闻 cell（top_left 对齐的 Container）"
        assert news_containers[0].expand is True, "新闻 cell 应使用 expand=True"

    def test_toolbar_row_has_right_padding(self, mock_page):
        """§6.3：工具栏 Row（scroll=AUTO）末端应有 8px 右侧留白防止内容贴边被裁切。

        Flet 0.28.3 的 Row 不支持 padding 参数，以 Row 末端追加 width=8 的
        Container 间隔实现等价右侧留白（同时覆盖滚动与非滚动两种布局）。
        """
        tab = self._make_tab()
        set_page(tab, mock_page)
        toolbar_row = tab.content.controls[0].controls[0].content
        assert isinstance(toolbar_row, ft.Row)
        assert toolbar_row.scroll == ft.ScrollMode.AUTO
        last = toolbar_row.controls[-1]
        assert isinstance(last, ft.Container), "工具栏 Row 末端应为留白 Container"
        assert last.width == 8, f"工具栏末端右侧留白应为 8，实际 {last.width}"


class TestSQLConsoleTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.patches = [
            patch("ui.views.data_view.I18n", self.mock_i18n),
            patch("ui.views.data_view.AppColors", self.mock_ac),
            patch("ui.views.data_view.AppStyles", self.mock_as),
            patch("ui.views.data_view.MetaDataManager"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.data_view import SQLConsoleTab

        mock_vm = MagicMock()
        mock_vm.sql_result = None
        mock_vm.sql_is_executing = False
        return SQLConsoleTab(mock_vm)

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
        tab.vm.execute_sql = AsyncMock(return_value={"success": True, "data": df})
        await tab._run_query(None)
        assert tab.result_table.visible is True
        assert tab.empty_state.visible is False
        assert len(tab.result_table.rows) == 2

    @pytest.mark.asyncio
    async def test_run_query_with_error_result(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = "SELECT * FROM nonexistent"
        tab.vm.execute_sql = AsyncMock(return_value={"success": False, "error": "Table not found"})
        await tab._run_query(None)
        assert tab.result_table.visible is False
        assert tab.empty_state.visible is True

    @pytest.mark.asyncio
    async def test_run_query_with_exception(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.sql_editor.value = "INVALID SQL"
        tab.vm.execute_sql = AsyncMock(side_effect=Exception("Connection error"))
        await tab._run_query(None)
        assert tab.result_table.visible is False
        assert tab.empty_state.visible is True
