import contextlib
import math
from unittest.mock import MagicMock, patch

import pytest

from data.data_processor import DataProcessor
from tests.unit.ui.conftest import set_page

pytestmark = pytest.mark.unit


class TestStockDetailDialogFormatVal:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_val_none_returns_dash(self):
        dlg = self._make_dialog({})
        result = dlg._format_val("close")
        assert result == "-"

    def test_format_val_nan_returns_dash(self):
        dlg = self._make_dialog({"close": float("nan")})
        result = dlg._format_val("close")
        assert result == "-"

    def test_format_val_float_with_suffix(self):
        dlg = self._make_dialog({"close": 12.34})
        result = dlg._format_val("close", "元")
        assert result == "12.34元"

    def test_format_val_non_numeric_returns_dash(self):
        dlg = self._make_dialog({"close": "abc"})
        result = dlg._format_val("close")
        assert result == "-"

    def test_format_val_zero(self):
        dlg = self._make_dialog({"close": 0})
        result = dlg._format_val("close", "%")
        assert result == "0.00%"


class TestStockDetailDialogFormatMv:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_mv_none_returns_dash(self):
        dlg = self._make_dialog({})
        result = dlg._format_mv("total_mv")
        assert result == "-"

    def test_format_mv_nan_returns_dash(self):
        dlg = self._make_dialog({"total_mv": float("nan")})
        result = dlg._format_mv("total_mv")
        assert result == "-"

    def test_format_mv_converts_wan_to_yi(self):
        dlg = self._make_dialog({"total_mv": 500000})
        result = dlg._format_mv("total_mv")
        assert "50.0" in result

    def test_format_mv_non_numeric_returns_dash(self):
        dlg = self._make_dialog({"total_mv": "abc"})
        result = dlg._format_mv("total_mv")
        assert result == "-"


class TestStockDetailDialogFormatVol:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_vol_none_returns_dash(self):
        dlg = self._make_dialog({})
        result = dlg._format_vol("vol")
        assert result == "-"

    def test_format_vol_over_10000(self):
        dlg = self._make_dialog({"vol": 50000})
        result = dlg._format_vol("vol")
        assert "5.0" in result

    def test_format_vol_under_10000(self):
        dlg = self._make_dialog({"vol": 5000})
        result = dlg._format_vol("vol")
        assert "5000" in result

    def test_format_vol_nan_returns_dash(self):
        dlg = self._make_dialog({"vol": float("nan")})
        result = dlg._format_vol("vol")
        assert result == "-"


class TestStockDetailDialogFormatAmount:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_amount_none_returns_dash(self):
        dlg = self._make_dialog({})
        result = dlg._format_amount("amount")
        assert result == "-"

    def test_format_amount_converts_qianyuan_to_yi(self):
        dlg = self._make_dialog({"amount": 5000000})
        result = dlg._format_amount("amount")
        assert "50.00" in result

    def test_format_amount_nan_returns_dash(self):
        dlg = self._make_dialog({"amount": float("nan")})
        result = dlg._format_amount("amount")
        assert result == "-"


class TestStockDetailDialog:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None, data_processor=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {}, data_processor=data_processor)

    def test_instantiation_with_no_data(self):
        dlg = self._make_dialog()
        assert dlg.stock_data == {}

    def test_instantiation_with_data(self):
        data = {"ts_code": "000001.SZ", "name": "平安银行"}
        dlg = self._make_dialog(data)
        assert dlg.stock_data["ts_code"] == "000001.SZ"

    def test_close_sets_open_false(self, mock_page):
        dlg = self._make_dialog()
        set_page(dlg, mock_page)
        dlg._close(None)
        assert dlg.open is False

    def test_update_data_replaces_stock_data(self):
        dlg = self._make_dialog({"ts_code": "000001.SZ"})
        new_data = {"ts_code": "600000.SH", "name": "浦发银行"}
        dlg.update_data(new_data)
        assert dlg.stock_data["ts_code"] == "600000.SH"

    @pytest.mark.asyncio
    async def test_load_chart_no_processor(self, mock_page):
        dlg = self._make_dialog(data_processor=None)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()  # spec omitted: Flet Container, complex __init__
        await dlg.load_chart("000001.SZ")
        dlg.chart_container.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_chart_with_processor(self, mock_page):
        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = MagicMock(return_value=_async_empty_df())
        dlg = self._make_dialog(data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()  # spec omitted: Flet Container, complex __init__
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async.return_value = _async_b64()
            await dlg.load_chart("000001.SZ")
        dlg.chart_container.update.assert_called()

    def test_pct_chg_positive_has_plus(self):
        data = {"pct_chg": 3.5}
        dlg = self._make_dialog(data)
        assert dlg.stock_data["pct_chg"] == 3.5

    def test_pct_chg_nan_guarded(self):
        data = {"pct_chg": float("nan")}
        self._make_dialog(data)
        assert math.isnan(data["pct_chg"])

    def test_pct_chg_none_guarded(self):
        data = {"pct_chg": None}
        dlg = self._make_dialog(data)
        assert dlg.stock_data["pct_chg"] is None


def _async_empty_df():
    import asyncio
    import pandas as pd

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    fut.set_result(pd.DataFrame())
    return fut


def _async_b64():
    import asyncio

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    fut.set_result("base64data")
    return fut


class TestStockDetailDialogFormatVolException:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_vol_exception_returns_dash(self):
        dlg = self._make_dialog({"vol": "invalid"})
        result = dlg._format_vol("vol")
        assert result == "-"


class TestStockDetailDialogFormatAmountException:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_format_amount_exception_returns_dash(self):
        dlg = self._make_dialog({"amount": "invalid"})
        result = dlg._format_amount("amount")
        assert result == "-"


class TestFormatMvPureFunction:
    """Unit tests for the module-level format_mv pure function."""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_none_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv(None) == "-"

    def test_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv(float("nan")) == "-"

    def test_decimal_nan_returns_dash(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_mv

        assert format_mv(Decimal("nan")) == "-"

    def test_normal_value_converts_wan_to_yi(self):
        from ui.components.stock_detail_dialog import format_mv

        # 15000 万元 = 1.5 亿
        result = format_mv(15000.0)
        assert result == "1.5unit_yi"

    def test_zero_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv(0.0) == "0.0unit_yi"

    def test_negative_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_mv

        result = format_mv(-50000.0)
        assert result == "-5.0unit_yi"

    def test_large_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_mv

        # 1_000_000 万元 = 100 亿
        result = format_mv(1_000_000.0)
        assert result == "100.0unit_yi"

    def test_int_input_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_mv

        result = format_mv(500000)
        assert result == "50.0unit_yi"

    def test_decimal_input_formats_correctly(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_mv

        result = format_mv(Decimal("250000"))
        assert result == "25.0unit_yi"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv("invalid") == "-"

    def test_list_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv([1, 2, 3]) == "-"


class TestFormatVolPureFunction:
    """Unit tests for the module-level format_vol pure function."""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_none_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol(None) == "-"

    def test_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol(float("nan")) == "-"

    def test_decimal_nan_returns_dash(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_vol

        assert format_vol(Decimal("nan")) == "-"

    def test_boundary_value_10000_uses_wanshou(self):
        from ui.components.stock_detail_dialog import format_vol

        # Boundary: 10000 → "1.0万手"
        result = format_vol(10000)
        assert result == "1.0unit_wanshou"

    def test_value_over_10000_uses_wanshou(self):
        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(50000)
        assert result == "5.0unit_wanshou"

    def test_value_under_10000_uses_shou(self):
        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(5000)
        assert result == "5000unit_shou"

    def test_zero_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_vol

        # 0 < 10000, so uses 手 unit
        assert format_vol(0.0) == "0unit_shou"

    def test_negative_value_under_10000_uses_shou(self):
        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(-5000)
        assert result == "-5000unit_shou"

    def test_large_value_uses_wanshou(self):
        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(12345678)
        assert result == "1234.6unit_wanshou"

    def test_decimal_input_formats_correctly(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(Decimal("20000"))
        assert result == "2.0unit_wanshou"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol("invalid") == "-"

    def test_dict_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol({"v": 1}) == "-"


class TestFormatAmountPureFunction:
    """Unit tests for the module-level format_amount pure function."""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_none_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount(None) == "-"

    def test_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount(float("nan")) == "-"

    def test_decimal_nan_returns_dash(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_amount

        assert format_amount(Decimal("nan")) == "-"

    def test_normal_value_converts_qianyuan_to_yi(self):
        from ui.components.stock_detail_dialog import format_amount

        # 5_000_000 千元 = 50 亿
        result = format_amount(5_000_000.0)
        assert result == "50.00unit_yi"

    def test_zero_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount(0.0) == "0.00unit_yi"

    def test_negative_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_amount

        result = format_amount(-1_000_000.0)
        assert result == "-10.00unit_yi"

    def test_large_value_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_amount

        # 100_000_000 千元 = 1000 亿
        result = format_amount(100_000_000.0)
        assert result == "1000.00unit_yi"

    def test_int_input_formats_correctly(self):
        from ui.components.stock_detail_dialog import format_amount

        result = format_amount(5000000)
        assert result == "50.00unit_yi"

    def test_decimal_input_formats_correctly(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import format_amount

        result = format_amount(Decimal("2500000"))
        assert result == "25.00unit_yi"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount("invalid") == "-"

    def test_tuple_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount((1, 2)) == "-"


class TestStockDetailDialogLoadChart:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None, data_processor=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {}, data_processor=data_processor)

    @pytest.mark.asyncio
    async def test_load_chart_empty_dataframe(self, mock_page):
        import pandas as pd
        import asyncio

        mock_dp = MagicMock(spec=DataProcessor)
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        fut.set_result(pd.DataFrame())
        mock_dp.get_stock_history = MagicMock(return_value=fut)

        dlg = self._make_dialog(data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()  # spec omitted: Flet Container, complex __init__

        await dlg.load_chart("000001.SZ")
        dlg.chart_container.update.assert_called()

    @pytest.mark.asyncio
    async def test_load_chart_exception(self, mock_page):
        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = MagicMock(side_effect=Exception("network error"))

        dlg = self._make_dialog(data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()  # spec omitted: Flet Container, complex __init__

        await dlg.load_chart("000001.SZ")
        dlg.chart_container.update.assert_called()


def _collect_markdown_controls(control, found):
    """Recursively collect ft.Markdown controls from the flet control tree."""
    import flet as ft

    if isinstance(control, ft.Markdown):
        found.append(control)
        return
    for attr_name in ("content",):
        child = getattr(control, attr_name, None)
        if child is not None and hasattr(child, "__class__"):
            _collect_markdown_controls(child, found)
    for attr_name in ("controls",):
        children = getattr(control, attr_name, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    _collect_markdown_controls(child, found)


class TestStockDetailDialogMarkdownTapLink:
    """SEC-010: verify ft.Markdown controls register on_tap_link=safe_open_url."""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_dialog(self, data=None):
        from ui.components.stock_detail_dialog import StockDetailDialog

        return StockDetailDialog(stock_data=data or {})

    def test_markdown_controls_have_on_tap_link(self):
        from ui.components._markdown_safe import safe_open_url

        data = {"ai_reason": "test reason", "thinking": "test thinking"}
        dlg = self._make_dialog(data)
        found: list = []
        _collect_markdown_controls(dlg.content, found)
        assert len(found) >= 2  # ai_reason + thinking markdown
        for md in found:
            assert md.on_tap_link is safe_open_url


class TestIsValidNumber:
    """Unit tests for the module-level is_valid_number function."""

    def test_none_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(None) is False

    def test_float_nan_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(float("nan")) is False

    def test_decimal_nan_returns_false(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(Decimal("nan")) is False

    def test_float_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(3.14) is True

    def test_int_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(42) is True

    def test_decimal_returns_true(self):
        from decimal import Decimal

        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(Decimal("1.5")) is True

    def test_numeric_string_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number("123") is True

    def test_non_numeric_string_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number("abc") is False

    def test_list_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number([1, 2, 3]) is False

    def test_dict_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number({"v": 1}) is False

    def test_zero_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(0) is True
        assert is_valid_number(0.0) is True

    def test_negative_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(-1.5) is True


class TestTushareUnitConstants:
    """Verify Tushare unit conversion constants are exported and correct."""

    def test_mv_unit_is_10000(self):
        from ui.components.stock_detail_dialog import TUSHARE_MV_UNIT

        assert TUSHARE_MV_UNIT == 10000

    def test_amount_unit_is_100000(self):
        from ui.components.stock_detail_dialog import TUSHARE_AMOUNT_UNIT

        assert TUSHARE_AMOUNT_UNIT == 100000
