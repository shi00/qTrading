"""StockDetailDialog 测试（声明式 V1）。

测试策略：
1. 模块级纯函数单测（is_valid_number/format_mv/format_vol/format_amount/
   _format_val/_format_mv/_format_vol/_format_amount/_dialog_size/_build_title/
   _build_content/_load_chart_async）
2. 契约守护测试（grep 命令式禁止模式 = 0 + 验证声明式 API）

声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化（参考 3.2.1-3.2.7 范式）。
实例方法已转为模块级纯函数，可直接单测。
"""

import contextlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from data.data_processor import DataProcessor

pytestmark = pytest.mark.unit


class _TrickyInt(int):
    """int 子类，其 __float__ 抛出 ValueError。

    用于测试 format_mv/vol/amount/_format_val 中的防御性 except 分支：
    is_valid_number 对 int 子类直接返回 True（不调用 float），
    但 format 函数中的 float(val) 会触发 __float__ 从而抛出异常。
    """

    def __float__(self):
        raise ValueError("tricky")


# ---------------------------------------------------------------------------
# 模块级常量
# ---------------------------------------------------------------------------
class TestTushareUnitConstants:
    """Verify Tushare unit conversion constants are exported and correct."""

    def test_mv_unit_is_10000(self):
        from ui.components.stock_detail_dialog import TUSHARE_MV_UNIT

        assert TUSHARE_MV_UNIT == 10000

    def test_amount_unit_is_100000(self):
        from ui.components.stock_detail_dialog import TUSHARE_AMOUNT_UNIT

        assert TUSHARE_AMOUNT_UNIT == 100000


# ---------------------------------------------------------------------------
# 模块级纯函数：is_valid_number
# ---------------------------------------------------------------------------
class TestIsValidNumber:
    """Unit tests for the module-level is_valid_number function."""

    def test_none_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(None) is False

    def test_float_nan_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(float("nan")) is False

    def test_decimal_nan_returns_false(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(Decimal("nan")) is False

    def test_float_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(3.14) is True

    def test_int_returns_true(self):
        from ui.components.stock_detail_dialog import is_valid_number

        assert is_valid_number(42) is True

    def test_decimal_returns_true(self):
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


# ---------------------------------------------------------------------------
# 模块级纯函数：format_mv
# ---------------------------------------------------------------------------
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
        from ui.components.stock_detail_dialog import format_mv

        result = format_mv(Decimal("250000"))
        assert result == "25.0unit_yi"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv("invalid") == "-"

    def test_list_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_mv

        assert format_mv([1, 2, 3]) == "-"


# ---------------------------------------------------------------------------
# 模块级纯函数：format_vol
# ---------------------------------------------------------------------------
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
        from ui.components.stock_detail_dialog import format_vol

        result = format_vol(Decimal("20000"))
        assert result == "2.0unit_wanshou"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol("invalid") == "-"

    def test_dict_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_vol

        assert format_vol({"v": 1}) == "-"


# ---------------------------------------------------------------------------
# 模块级纯函数：format_amount
# ---------------------------------------------------------------------------
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
        from ui.components.stock_detail_dialog import format_amount

        result = format_amount(Decimal("2500000"))
        assert result == "25.00unit_yi"

    def test_string_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount("invalid") == "-"

    def test_tuple_invalid_type_returns_dash(self):
        from ui.components.stock_detail_dialog import format_amount

        assert format_amount((1, 2)) == "-"


# ---------------------------------------------------------------------------
# 模块级纯函数：_format_val / _format_mv / _format_vol / _format_amount
# （由旧实例方法转换，接收 stock_data dict）
# ---------------------------------------------------------------------------
class TestFormatValModuleFunction:
    """模块级 _format_val(stock_data, key, suffix) 纯函数测试。"""

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

    def test_format_val_none_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({}, "close") == "-"

    def test_format_val_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({"close": float("nan")}, "close") == "-"

    def test_format_val_float_with_suffix(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({"close": 12.34}, "close", "元") == "12.34元"

    def test_format_val_non_numeric_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({"close": "abc"}, "close") == "-"

    def test_format_val_zero(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({"close": 0}, "close", "%") == "0.00%"

    def test_format_val_tricky_int_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_val

        assert _format_val({"close": _TrickyInt(5)}, "close") == "-"


class TestFormatMvModuleFunction:
    """模块级 _format_mv(stock_data, key) 纯函数测试。"""

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

    def test_format_mv_none_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_mv

        assert _format_mv({}, "total_mv") == "-"

    def test_format_mv_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_mv

        assert _format_mv({"total_mv": float("nan")}, "total_mv") == "-"

    def test_format_mv_converts_wan_to_yi(self):
        from ui.components.stock_detail_dialog import _format_mv

        result = _format_mv({"total_mv": 500000}, "total_mv")
        assert "50.0" in result

    def test_format_mv_non_numeric_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_mv

        assert _format_mv({"total_mv": "abc"}, "total_mv") == "-"


class TestFormatVolModuleFunction:
    """模块级 _format_vol(stock_data, key) 纯函数测试。"""

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

    def test_format_vol_none_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_vol

        assert _format_vol({}, "vol") == "-"

    def test_format_vol_over_10000(self):
        from ui.components.stock_detail_dialog import _format_vol

        result = _format_vol({"vol": 50000}, "vol")
        assert "5.0" in result

    def test_format_vol_under_10000(self):
        from ui.components.stock_detail_dialog import _format_vol

        result = _format_vol({"vol": 5000}, "vol")
        assert "5000" in result

    def test_format_vol_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_vol

        assert _format_vol({"vol": float("nan")}, "vol") == "-"

    def test_format_vol_exception_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_vol

        assert _format_vol({"vol": "invalid"}, "vol") == "-"


class TestFormatAmountModuleFunction:
    """模块级 _format_amount(stock_data, key) 纯函数测试。"""

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

    def test_format_amount_none_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_amount

        assert _format_amount({}, "amount") == "-"

    def test_format_amount_converts_qianyuan_to_yi(self):
        from ui.components.stock_detail_dialog import _format_amount

        result = _format_amount({"amount": 5000000}, "amount")
        assert "50.00" in result

    def test_format_amount_nan_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_amount

        assert _format_amount({"amount": float("nan")}, "amount") == "-"

    def test_format_amount_exception_returns_dash(self):
        from ui.components.stock_detail_dialog import _format_amount

        assert _format_amount({"amount": "invalid"}, "amount") == "-"


# ---------------------------------------------------------------------------
# 模块级纯函数：format_mv/vol/amount 防御性 except 分支
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
# 模块级纯函数：_dialog_size
# ---------------------------------------------------------------------------
class TestDialogSizeFunction:
    """模块级 _dialog_size(page) 纯函数测试。"""

    def test_no_page_returns_default(self):
        from ui.components.stock_detail_dialog import _dialog_size

        w, h = _dialog_size(None)
        assert w == 900
        assert h == 700

    def test_with_page_uses_window_dims(self, mock_page):
        from ui.components.stock_detail_dialog import _dialog_size

        # mock_page.window.width=1200, window.height=800
        # w = min(max(1200-80, 600), 900) = 900
        # h = min(max(800-80, 500), 700) = 700
        w, h = _dialog_size(mock_page)
        assert w == 900
        assert h == 700

    def test_clamps_to_min(self, mock_page):
        from ui.components.stock_detail_dialog import _dialog_size

        mock_page.window.width = 600  # 600-80=520 < 600 → clamp to 600
        mock_page.window.height = 500  # 500-80=420 < 500 → clamp to 500
        w, h = _dialog_size(mock_page)
        assert w == 600
        assert h == 500

    def test_clamps_to_max(self, mock_page):
        from ui.components.stock_detail_dialog import _dialog_size

        mock_page.window.width = 2000  # 2000-80=1920 > 900 → clamp to 900
        mock_page.window.height = 2000  # 2000-80=1920 > 700 → clamp to 700
        w, h = _dialog_size(mock_page)
        assert w == 900
        assert h == 700

    def test_window_none_falls_back(self, mock_page):
        from ui.components.stock_detail_dialog import _dialog_size

        mock_page.window.width = None
        mock_page.window.height = None
        # int(None or 1280) = 1280, int(None or 800) = 800
        w, h = _dialog_size(mock_page)
        assert w == 900  # min(max(1280-80, 600), 900) = 900
        assert h == 700  # min(max(800-80, 500), 700) = 700


# ---------------------------------------------------------------------------
# 模块级纯函数：_build_title
# ---------------------------------------------------------------------------
class TestBuildTitleFunction:
    """模块级 _build_title(stock_data) 纯函数测试。"""

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

    def test_build_title_with_name_and_code(self):
        from ui.components.stock_detail_dialog import _build_title

        title = _build_title({"ts_code": "000001.SZ", "name": "平安银行"})
        assert isinstance(title, ft.Row)
        # 标题应包含名称和代码
        texts = [c for c in title.controls if isinstance(c, ft.Text)]
        assert any("平安银行" in (t.value or "") for t in texts)
        assert any("000001.SZ" in (t.value or "") for t in texts)

    def test_build_title_with_empty_data(self):
        from ui.components.stock_detail_dialog import _build_title

        title = _build_title({})
        assert isinstance(title, ft.Row)


# ---------------------------------------------------------------------------
# 模块级纯函数：_initial_chart_content
# ---------------------------------------------------------------------------
class TestInitialChartContent:
    """模块级 _initial_chart_content() 纯函数测试。"""

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

    def test_initial_chart_content_has_progress_ring(self):
        from ui.components.stock_detail_dialog import _initial_chart_content

        content = _initial_chart_content()
        assert isinstance(content, ft.Column)
        # 应包含 ProgressRing 和 Text
        controls = content.controls
        assert any(isinstance(c, ft.ProgressRing) for c in controls)
        assert any(isinstance(c, ft.Text) for c in controls)


# ---------------------------------------------------------------------------
# 模块级纯函数：_build_content（ai_score 解析 + markdown on_tap_link）
# ---------------------------------------------------------------------------
class TestBuildContentFunction:
    """模块级 _build_content(stock_data, chart_content, width, height) 纯函数测试。"""

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

    def _build(self, data=None):
        from ui.components.stock_detail_dialog import _build_content, _initial_chart_content

        return _build_content(data or {}, _initial_chart_content(), 900, 700)

    def test_build_content_returns_container(self):
        container = self._build({"ts_code": "000001.SZ"})
        assert isinstance(container, ft.Container)

    def test_ai_score_invalid_string_falls_back_to_zero(self):
        # ai_score="not_a_number" → float() raises ValueError → score_val=0
        container = self._build({"ai_reason": "test", "ai_score": "not_a_number"})
        assert container is not None

    def test_ai_score_none_with_ai_reason(self):
        # ai_score=None → score_val=0 (else 分支)
        container = self._build({"ai_reason": "test", "ai_score": None})
        assert container is not None

    def test_ai_score_valid_number(self):
        container = self._build({"ai_reason": "test", "ai_score": "85.5"})
        assert container is not None

    def test_ai_score_only_no_reason(self):
        # 仅 ai_score，无 ai_reason
        container = self._build({"ai_score": "90"})
        assert container is not None

    def test_ai_score_int_zero(self):
        container = self._build({"ai_reason": "test", "ai_score": 0})
        assert container is not None

    def test_markdown_controls_have_on_tap_link(self):
        """SEC-010: verify ft.Markdown controls register on_tap_link=safe_open_url."""
        from ui.components._markdown_safe import safe_open_url

        container = self._build({"ai_reason": "test reason", "thinking": "test thinking"})
        found: list = []
        _collect_markdown_controls(container, found)
        assert len(found) >= 2  # ai_reason + thinking markdown
        for md in found:
            assert md.on_tap_link is safe_open_url


def _collect_markdown_controls(control, found):
    """Recursively collect ft.Markdown controls from the flet control tree."""
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


# ---------------------------------------------------------------------------
# 模块级纯函数：_load_chart_async
# ---------------------------------------------------------------------------
class TestLoadChartAsyncFunction:
    """模块级 _load_chart_async(...) 纯函数测试。

    接收 set_chart_content 回调，可独立单测（无需实例化 StockDetailDialog）。
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

    @pytest.mark.asyncio
    async def test_no_processor_sets_error_text(self):
        from ui.components.stock_detail_dialog import _load_chart_async

        calls: list = []
        await _load_chart_async(None, {}, "000001.SZ", calls.append, 860, 340)
        assert len(calls) == 1
        assert isinstance(calls[0], ft.Text)

    @pytest.mark.asyncio
    async def test_empty_dataframe_sets_no_history_text(self):
        import pandas as pd

        from ui.components.stock_detail_dialog import _load_chart_async

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())

        calls: list = []
        await _load_chart_async(mock_dp, {"name": "测试"}, "000001.SZ", calls.append, 860, 340)
        # 第一次：loading（ProgressRing），第二次：no_history（Text）
        assert len(calls) == 2
        assert isinstance(calls[1], ft.Text)

    @pytest.mark.asyncio
    async def test_exception_sets_error_text(self):
        from ui.components.stock_detail_dialog import _load_chart_async

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = MagicMock(side_effect=Exception("network error"))

        calls: list = []
        await _load_chart_async(mock_dp, {"name": "测试"}, "000001.SZ", calls.append, 860, 340)
        # 第一次：loading，第二次：error
        assert len(calls) == 2
        assert isinstance(calls[1], ft.Text)

    @pytest.mark.asyncio
    async def test_success_sets_ft_image(self):
        import pandas as pd

        from ui.components.stock_detail_dialog import _load_chart_async

        # 非空 DataFrame，无 vol 列（覆盖添加 vol 列分支）
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = AsyncMock(return_value=df)

        calls: list = []
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value="base64pngdata")
            await _load_chart_async(mock_dp, {"name": "测试股票"}, "000001.SZ", calls.append, 860, 340)

        # 最终设置 ft.Image
        assert isinstance(calls[-1], ft.Image)
        assert calls[-1].src == "base64pngdata"

    @pytest.mark.asyncio
    async def test_success_with_existing_vol_column(self):
        import pandas as pd

        from ui.components.stock_detail_dialog import _load_chart_async

        # 非空 DataFrame，已有 vol 列
        df = pd.DataFrame({"close": [10.0, 11.0], "vol": [100, 200]})

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.get_stock_history = AsyncMock(return_value=df)

        calls: list = []
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value="b64")
            await _load_chart_async(mock_dp, {"name": "测试"}, "000001.SZ", calls.append, 860, 340)

        assert isinstance(calls[-1], ft.Image)
        # vol 列已存在，不应被覆盖
        assert list(df["vol"]) == [100, 200]


# ---------------------------------------------------------------------------
# 契约守护测试：声明式组件禁止命令式模式
# ---------------------------------------------------------------------------
class TestStockDetailDialogContract:
    """契约守护测试：声明式组件禁止命令式模式。"""

    def test_no_imperative_patterns(self) -> None:
        """grep 命令式禁止模式 = 0（did_mount/will_unmount/refresh_locale/.update()/class X(ft.AlertDialog)/PageRefMixin/update_data/load_chart）。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        forbidden_patterns = [
            "def did_mount",
            "def will_unmount",
            "def refresh_locale",
            "self.update()",
            "class StockDetailDialog(ft.AlertDialog)",
            "class StockDetailDialog(ft.Container)",
            "class StockDetailDialog(ft.UserControl)",
            "PageRefMixin",
            "def update_data",
            "def load_chart",
            "page.show_dialog",
            "page.pop_dialog",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"禁止命令式模式: {pattern}"

    def test_is_declarative_component(self) -> None:
        """验证是 @ft.component 声明式组件。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "@ft.component" in content
        assert "def StockDetailDialog(" in content

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "ft.use_state(get_observable_state)" in content

    def test_uses_use_dialog(self) -> None:
        """验证通过 ft.use_dialog 自动挂载/卸载 dialog。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "ft.use_dialog(" in content

    def test_uses_use_effect_for_chart_loading(self) -> None:
        """验证通过 ft.use_effect 异步加载 K 线图。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "ft.use_effect(" in content

    def test_pure_functions_preserved(self) -> None:
        """验证模块级纯函数保留导出。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        # 纯函数
        assert "def is_valid_number(" in content
        assert "def format_mv(" in content
        assert "def format_vol(" in content
        assert "def format_amount(" in content
        # 常量
        assert "TUSHARE_MV_UNIT" in content
        assert "TUSHARE_AMOUNT_UNIT" in content

    def test_alert_dialog_modal_is_false(self) -> None:
        """契约：AlertDialog.modal=False，允许外部点击关闭（P3-UI-Source-Bugs-5）。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "modal=False" in content
        assert "modal=True" not in content

    def test_alert_dialog_on_dismiss_registered(self) -> None:
        """契约：AlertDialog 注册 on_dismiss=_close，外部点击关闭时同步状态（P3-UI-Source-Bugs-5）。"""
        from pathlib import Path

        dialog_path = Path(__file__).parent.parent.parent.parent / "ui" / "components" / "stock_detail_dialog.py"
        content = dialog_path.read_text(encoding="utf-8")

        assert "on_dismiss=_close" in content


# ---------------------------------------------------------------------------
# 组件运行时测试：AlertDialog modal=False + on_dismiss 回调
# ---------------------------------------------------------------------------
class TestStockDetailDialogComponent:
    """StockDetailDialog 组件运行时测试（声明式 V1，P3-UI-Source-Bugs-5）。

    验证 AlertDialog 的 modal 属性和 on_dismiss 回调正确注册：
    - ``dialog.modal is False``
    - 调用 ``dialog.on_dismiss(...)`` 触发 ``set_open(False)`` + ``on_close()``
    """

    def test_dialog_modal_is_false_at_runtime(self, mock_i18n_state, mock_app_colors_state, monkeypatch):
        """挂载后 dialog.modal is False。"""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )
        from ui.components import stock_detail_dialog as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: key
        monkeypatch.setattr(mod, "I18n", mock_i18n)

        component = make_component(
            mod.StockDetailDialog,
            stock_data={"ts_code": "000001.SZ", "name": "测试"},
            data_processor=None,
            page=None,
            open_state=True,
            on_close=MagicMock(),
        )
        page = FakePage()
        run_mount_effects(component, page=page)
        render_once(component)

        # 从 page._dialogs 找到 AlertDialog
        dialog = page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        assert dialog.modal is False

    def test_on_dismiss_invokes_on_close(self, mock_i18n_state, mock_app_colors_state, monkeypatch):
        """调用 dialog.on_dismiss 触发 on_close 回调（同步关闭状态）。"""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )
        from ui.components import stock_detail_dialog as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: key
        monkeypatch.setattr(mod, "I18n", mock_i18n)

        on_close = MagicMock()
        component = make_component(
            mod.StockDetailDialog,
            stock_data={"ts_code": "000001.SZ", "name": "测试"},
            data_processor=None,
            page=None,
            open_state=True,
            on_close=on_close,
        )
        page = FakePage()
        run_mount_effects(component, page=page)
        render_once(component)

        dialog = page._dialogs.controls[-1]
        # on_dismiss 类型为 Optional[ControlEventHandler]，测试上下文保证非 None
        assert dialog.on_dismiss is not None
        dialog.on_dismiss(None)
        on_close.assert_called_once_with()
