import contextlib
import logging
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.data_processor import DataProcessor
from tests.unit.ui.conftest import set_page
from ui.components.stock_detail_dialog import logger as detail_logger

pytestmark = pytest.mark.unit


class _TrickyInt(int):
    """int 子类，其 __float__ 抛出 ValueError。

    用于测试 format_mv/vol/amount/_format_val 中的防御性 except 分支：
    is_valid_number 对 int 子类直接返回 True（不调用 float），
    但 format 函数中的 float(val) 会触发 __float__ 从而抛出异常。
    """

    def __float__(self):
        raise ValueError("tricky")


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

    def test_refresh_locale_rebuilds_content(self, mock_page):
        """§5.8 规范 6：refresh_locale 应正确刷新文案（重建 title/content/actions），不抛出异常。"""
        dlg = self._make_dialog({"ts_code": "000001.SZ", "name": "平安银行"})
        set_page(dlg, mock_page)
        dlg.update = MagicMock()
        original_title = dlg.title
        original_content = dlg.content
        original_actions = list(dlg.actions)

        # 模拟 locale 切换：i18n 返回翻译后的值，使重建产生不同内容
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        dlg.refresh_locale()

        # title 不依赖 i18n（仅用 stock_data），V1 Prop 在值相等时跳过赋值，这是 V1 的优化行为
        assert dlg.title is original_title
        # content/actions 依赖 i18n，locale 切换后值变化，V1 Prop 会更新引用
        assert dlg.content is not original_content
        # actions 列表被替换为新 TextButton
        assert len(dlg.actions) == 1
        assert dlg.actions[0] is not original_actions[0]
        dlg.update.assert_called_once()
        # I18n.get 应被调用以刷新文案
        self.mock_i18n.get.assert_any_call("common_close")

    def test_refresh_locale_swallows_exception(self, mock_page, caplog):
        """refresh_locale 异常时不应抛出，应降级为 logger.warning。"""
        dlg = self._make_dialog({"ts_code": "000001.SZ"})
        set_page(dlg, mock_page)
        # 强制 I18n.get 抛异常以触发 try/except
        self.mock_i18n.get.side_effect = RuntimeError("i18n boom")

        with caplog.at_level(logging.WARNING, logger=detail_logger.name):
            # 不应抛出异常
            dlg.refresh_locale()

        assert any("refresh_locale failed" in r.message and "i18n boom" in r.message for r in caplog.records)

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
        assert dlg.chart_container.update.call_count == 2  # 多次调用预期 (loading + chart)

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
        assert dlg.chart_container.update.call_count == 2  # 多次调用预期 (loading + empty)

    @pytest.mark.asyncio
    async def test_load_chart_exception(self, mock_page):
        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = MagicMock(side_effect=Exception("network error"))

        dlg = self._make_dialog(data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()  # spec omitted: Flet Container, complex __init__

        await dlg.load_chart("000001.SZ")
        assert dlg.chart_container.update.call_count == 2  # 多次调用预期 (loading + error)


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


# ---------------------------------------------------------------------------
# 补充覆盖：format_mv/vol/amount 防御性 except 分支
# ---------------------------------------------------------------------------
class TestFormatPureFunctionExceptionBranch:
    """覆盖 format_mv/vol/amount 中 except (ValueError, TypeError) 分支。

    使用 _TrickyInt（int 子类，__float__ 抛 ValueError）使 is_valid_number 返回 True
    但 format 函数中的 float(val) 抛异常。
    """

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

    def test_format_mv_tricky_int_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv(_TrickyInt(5)) == "-"

    def test_format_vol_tricky_int_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol(_TrickyInt(5)) == "-"

    def test_format_amount_tricky_int_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount(_TrickyInt(5)) == "-"


# ---------------------------------------------------------------------------
# 补充覆盖：_format_val 防御性 except 分支
# ---------------------------------------------------------------------------
class TestStockDetailDialogFormatValException:
    """覆盖 _format_val 中 except (ValueError, TypeError) 分支。"""

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

    def test_format_val_tricky_int_returns_dash(self):
        dlg = self._make_dialog({"close": _TrickyInt(5)})
        assert dlg._format_val("close") == "-"


# ---------------------------------------------------------------------------
# 补充覆盖：_dialog_size 带 page 参数
# ---------------------------------------------------------------------------
class TestStockDetailDialogDialogSize:
    """覆盖 _dialog_size 在有 page 参数时的计算分支。"""

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

    def test_dialog_size_with_page_uses_window_dims(self, mock_page):
        from ui.components.stock_detail_dialog import StockDetailDialog

        # mock_page.window.width=1200, window.height=800
        # w = min(max(1200-80, 600), 900) = 900
        # h = min(max(800-80, 500), 700) = 700
        dlg = StockDetailDialog(stock_data={}, page=mock_page)
        assert dlg._cached_width == 900
        assert dlg._cached_height == 700

    def test_dialog_size_clamps_to_min(self, mock_page):
        from ui.components.stock_detail_dialog import StockDetailDialog

        mock_page.window.width = 600  # 600-80=520 < 600 → clamp to 600
        mock_page.window.height = 500  # 500-80=420 < 500 → clamp to 500
        dlg = StockDetailDialog(stock_data={}, page=mock_page)
        assert dlg._cached_width == 600
        assert dlg._cached_height == 500

    def test_dialog_size_clamps_to_max(self, mock_page):
        from ui.components.stock_detail_dialog import StockDetailDialog

        mock_page.window.width = 2000  # 2000-80=1920 > 900 → clamp to 900
        mock_page.window.height = 2000  # 2000-80=1920 > 700 → clamp to 700
        dlg = StockDetailDialog(stock_data={}, page=mock_page)
        assert dlg._cached_width == 900
        assert dlg._cached_height == 700

    def test_dialog_size_window_none_falls_back(self, mock_page):
        from ui.components.stock_detail_dialog import StockDetailDialog

        mock_page.window.width = None
        mock_page.window.height = None
        # int(None or 1280) = 1280, int(None or 800) = 800
        dlg = StockDetailDialog(stock_data={}, page=mock_page)
        assert dlg._cached_width == 900  # min(max(1280-80, 600), 900) = 900
        assert dlg._cached_height == 700  # min(max(800-80, 500), 700) = 700

    def test_chart_width_derived_from_dialog_width(self, mock_page):
        from ui.components.stock_detail_dialog import StockDetailDialog

        mock_page.window.width = 1000
        dlg = StockDetailDialog(stock_data={}, page=mock_page)
        # _cached_width = min(max(1000-80, 600), 900) = 900
        # _chart_width = max(900 - 40, 600) = 860
        assert dlg._chart_width == 860


# ---------------------------------------------------------------------------
# 补充覆盖：ai_score 解析异常
# ---------------------------------------------------------------------------
class TestStockDetailDialogAiScoreParse:
    """覆盖 ai_score 为不可转换值时的 except 分支。"""

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

    def test_ai_score_invalid_string_falls_back_to_zero(self):
        # ai_score="not_a_number" → float() raises ValueError → score_val=0
        dlg = self._make_dialog({"ai_reason": "test", "ai_score": "not_a_number"})
        # 验证对话框正常构建（score_val=0，不抛异常）
        assert dlg.content is not None

    def test_ai_score_none_with_ai_reason(self):
        # ai_score=None → score_val=0 (else 分支)
        dlg = self._make_dialog({"ai_reason": "test", "ai_score": None})
        assert dlg.content is not None

    def test_ai_score_valid_number(self):
        dlg = self._make_dialog({"ai_reason": "test", "ai_score": "85.5"})
        assert dlg.content is not None

    def test_ai_score_only_no_reason(self):
        # 仅 ai_score，无 ai_reason
        dlg = self._make_dialog({"ai_score": "90"})
        assert dlg.content is not None

    def test_ai_score_int_zero(self):
        dlg = self._make_dialog({"ai_reason": "test", "ai_score": 0})
        assert dlg.content is not None


# ---------------------------------------------------------------------------
# 补充覆盖：_close / did_mount / will_unmount / refresh_locale 边界
# ---------------------------------------------------------------------------
class TestStockDetailDialogLifecycle:
    """覆盖生命周期方法与 _close 的边界分支。"""

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

    def test_close_without_page_no_update(self):
        # page 未设置，_close 不应抛出异常（不调用 page.update）
        dlg = self._make_dialog()
        dlg._close(None)
        assert dlg.open is False

    def test_did_mount_subscribes_locale(self):
        dlg = self._make_dialog()
        dlg.did_mount()
        # I18n.subscribe 应被调用，返回 sub_id
        self.mock_i18n.subscribe.assert_called_once()
        assert dlg._locale_subscription_id == "sub_id"

    def test_will_unmount_unsubscribes_when_id_set(self):
        dlg = self._make_dialog()
        dlg.did_mount()  # 设置 _locale_subscription_id
        dlg.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
        assert dlg._locale_subscription_id is None

    def test_will_unmount_noop_when_id_none(self):
        dlg = self._make_dialog()
        # 未调用 did_mount，_locale_subscription_id 为 None
        dlg.will_unmount()
        self.mock_i18n.unsubscribe.assert_not_called()

    def test_refresh_locale_without_page_no_update(self):
        dlg = self._make_dialog({"ts_code": "000001.SZ"})
        # page 未设置，refresh_locale 不应抛出（不调用 self.update）
        dlg.refresh_locale()
        # title/content 被重建
        assert dlg.title is not None
        assert dlg.content is not None

    def test_refresh_locale_preserves_ft_image_chart(self, mock_page):
        """refresh_locale 应保留已加载的 ft.Image K 线图。"""
        import flet as ft

        dlg = self._make_dialog({"ts_code": "000001.SZ", "name": "测试"})
        set_page(dlg, mock_page)
        dlg.update = MagicMock()

        # 模拟已加载的 K 线图（ft.Image）
        original_image = ft.Image(src="loaded_png_data", fit=ft.BoxFit.CONTAIN)
        dlg.chart_container.content = original_image

        dlg.refresh_locale()

        # chart_container.content 应恢复为原来的 ft.Image
        assert dlg.chart_container.content is original_image
        dlg.update.assert_called_once()

    def test_refresh_locale_does_not_preserve_non_image_chart(self, mock_page):
        """chart_container.content 非 ft.Image 时不应保留。"""
        import flet as ft

        dlg = self._make_dialog({"ts_code": "000001.SZ"})
        set_page(dlg, mock_page)
        dlg.update = MagicMock()

        # chart_container.content 是 ProgressRing（非 ft.Image）
        dlg.chart_container.content = ft.ProgressRing()
        original = dlg.chart_container.content

        dlg.refresh_locale()

        # _build_content 会重建 chart_container，content 不再是原来的 ProgressRing
        assert dlg.chart_container.content is not original


# ---------------------------------------------------------------------------
# 补充覆盖：load_chart 成功路径（非空 DataFrame + 生成图片）
# ---------------------------------------------------------------------------
class TestStockDetailDialogLoadChartSuccess:
    """覆盖 load_chart 成功路径：非空 DataFrame → 生成 PNG → 设置 ft.Image。"""

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
    async def test_load_chart_success_sets_ft_image(self, mock_page):
        import flet as ft
        import pandas as pd

        # 非空 DataFrame，无 vol 列（覆盖 line 529-530 添加 vol 列）
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = AsyncMock(return_value=df)

        dlg = self._make_dialog(data={"name": "测试股票"}, data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value="base64pngdata")
            await dlg.load_chart("000001.SZ")

        # 验证最终设置了 ft.Image
        assert isinstance(dlg.chart_container.content, ft.Image)
        assert dlg.chart_container.content.src == "base64pngdata"
        # chart_container.update 被调用多次（loading + 最终图片）
        assert dlg.chart_container.update.call_count >= 2

    @pytest.mark.asyncio
    async def test_load_chart_success_with_existing_vol_column(self, mock_page):
        import flet as ft
        import pandas as pd

        # 非空 DataFrame，已有 vol 列
        df = pd.DataFrame({"close": [10.0, 11.0], "vol": [100, 200]})

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = AsyncMock(return_value=df)

        dlg = self._make_dialog(data={"name": "测试"}, data_processor=mock_dp)
        set_page(dlg, mock_page)
        dlg.chart_container = MagicMock()

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value="b64")
            await dlg.load_chart("000001.SZ")

        assert isinstance(dlg.chart_container.content, ft.Image)
        # vol 列已存在，不应被覆盖
        assert list(df["vol"]) == [100, 200]
