import inspect


from ui.components.virtual_table import PaginatedTable
from ui.viewmodels.screener_view_model import ScreenerViewModel


class TestSortDirectionConsistency:
    """View/VM 排序方向一致性"""

    def test_vm_sort_data_accepts_ascending_param(self):
        sig = inspect.signature(ScreenerViewModel.sort_data)
        params = list(sig.parameters.keys())
        assert "ascending" in params

    def test_vm_sort_data_ascending_default_none(self):
        sig = inspect.signature(ScreenerViewModel.sort_data)
        assert sig.parameters["ascending"].default is None

    def test_vm_new_column_defaults_ascending(self):
        source = inspect.getsource(ScreenerViewModel.sort_data)
        assert "self.sort_ascending = True" in source, (
            "VM new column should default to ascending (True) to match PaginatedTable"
        )

    def test_paginated_table_new_column_defaults_ascending(self):
        source = inspect.getsource(PaginatedTable._handle_sort_click)
        assert "self.sort_asc = True" in source, "PaginatedTable new column should default to ascending"
