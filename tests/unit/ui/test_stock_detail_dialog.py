import math
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page


class TestStockDetailDialogFormatVal:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

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
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

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
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

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
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

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
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.components.stock_detail_dialog.I18n", self.mock_i18n),
            patch("ui.components.stock_detail_dialog.AppColors", self.mock_ac),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

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
        dlg.chart_container = MagicMock()
        await dlg.load_chart("000001.SZ")
        dlg.chart_container.update.assert_called()

    @pytest.mark.asyncio
    async def test_load_chart_with_processor(self, mock_page):
        mock_dp = MagicMock()
        mock_dp.get_stock_history = MagicMock(return_value=_async_empty_df())
        dlg = self._make_dialog(data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()
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

    fut = asyncio.Future()
    fut.set_result(pd.DataFrame())
    return fut


def _async_b64():
    import asyncio

    fut = asyncio.Future()
    fut.set_result("base64data")
    return fut
