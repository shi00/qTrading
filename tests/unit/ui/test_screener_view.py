"""ui/views/screener_view.py 纯函数单元测试 (Phase F.3).

声明式重写后 View 层测试聚焦:
- 纯函数辅助 (_format_cell_value / _build_table_data / _parse_num /
  _build_strategy_options / _build_page_size_options / _resolve_group_title /
  _format_history_date / _build_strategy_desc) 覆盖
- _compute_tier_hint 已迁入 VM (R.2.1), 测试见 test_screener_view_model.py
- 声明式契约守护见 test_screener_view_contract.py
- VM 交互 / 流式渲染 / 深度链接 / 模式切换由集成测试 (flet_test_page fixture) 承担,
  声明式组件含 use_state 在无 renderer 下抛 RuntimeError
"""

import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ui.views.screener_view import (
    _COLUMN_WIDTHS,
    _build_page_size_options,
    _build_strategy_options,
    _build_table_data,
    _format_cell_value,
    _format_history_date,
    _parse_num,
    _render_status_message,
    _resolve_group_title,
)

pytestmark = pytest.mark.unit


class TestFormatCellValue:
    def test_nan_returns_dash(self):
        result = _format_cell_value("close", float("nan"))
        assert result == "-"

    def test_strategy_name_translates(self):
        with patch("ui.views.screener_view.translate_strategy_name", return_value="策略A"):
            with patch("ui.views.screener_view.I18n"):
                result = _format_cell_value("strategy_name", "strategy_a")
                assert result == "策略A"

    def test_date_col_with_datetime(self):
        dt = datetime.date(2024, 1, 15)
        result = _format_cell_value("trade_date", dt)
        assert result == "2024-01-15"

    def test_date_col_with_8digit_string(self):
        result = _format_cell_value("trade_date", "20240115")
        assert result == "2024-01-15"

    def test_date_col_with_non_date_string(self):
        result = _format_cell_value("trade_date", "notadate")
        assert result == "notadate"

    def test_volume_col_over_yi(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: "亿" if key == "unit_yi" else key
            result = _format_cell_value("vol", 2_000_000_000)
            assert "亿" in result

    def test_volume_col_over_wan(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: "万" if key == "unit_wan" else key
            result = _format_cell_value("vol", 50_000)
            assert "万" in result

    def test_volume_col_small(self):
        result = _format_cell_value("vol", 9999)
        assert "9,999" in result

    def test_float_format_two_decimals(self):
        result = _format_cell_value("close", 12.3456)
        assert result == "12.35"

    def test_ts_code_not_formatted(self):
        result = _format_cell_value("ts_code", "000001.SZ")
        assert result == "000001.SZ"

    def test_string_value_returns_str(self):
        result = _format_cell_value("name", "平安银行")
        assert result == "平安银行"

    def test_int_non_volume_returns_str(self):
        # int 值且非 volume 列时跳过 float 格式化，走 str(val)
        result = _format_cell_value("ai_score", 85)
        assert result == "85"


class TestBuildTableData:
    def _make_vm(self) -> MagicMock:
        """Task 5.1: _build_table_data 需要 vm.get_column_alias 参数。"""
        vm = MagicMock()
        vm.get_column_alias.side_effect = lambda table, col: col
        return vm

    def test_hides_hidden_columns(self):
        df = pd.DataFrame({"symbol": ["s1"], "ts_code": ["000001.SZ"], "name": ["test"]})
        cols, rows = _build_table_data(df, self._make_vm())
        col_ids = [c["id"] for c in cols]
        assert "symbol" not in col_ids
        assert "ts_code" in col_ids
        assert "name" in col_ids

    def test_uses_custom_width(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        cols, _ = _build_table_data(df, self._make_vm())
        assert cols[0]["width"] == _COLUMN_WIDTHS["ts_code"]

    def test_default_width_for_unknown_col(self):
        df = pd.DataFrame({"unknown_col": ["val"]})
        cols, _ = _build_table_data(df, self._make_vm())
        assert cols[0]["width"] == 80

    def test_formats_rows(self):
        df = pd.DataFrame({"name": ["test"], "close": [12.34]})
        _, rows = _build_table_data(df, self._make_vm())
        assert len(rows) == 1
        assert rows[0]["name"] == "test"
        assert rows[0]["close"] == "12.34"


class TestParseNum:
    """_parse_num 纯函数测试: 尝试解析数值, 失败时返回原字符串。"""

    def test_valid_int_string(self):
        assert _parse_num("42") == 42.0

    def test_valid_float_string(self):
        assert _parse_num("3.14") == 3.14

    def test_negative_number(self):
        assert _parse_num("-5.5") == -5.5

    def test_int_value_passthrough(self):
        assert _parse_num(10) == 10.0

    def test_float_value_passthrough(self):
        assert _parse_num(2.71) == 2.71

    def test_invalid_string_returns_original(self):
        assert _parse_num("not_a_number") == "not_a_number"

    def test_empty_string_returns_original(self):
        assert _parse_num("") == ""

    def test_none_returns_none(self):
        # None 触发 TypeError → 返回原值 None
        assert _parse_num(None) is None


class TestBuildStrategyOptions:
    """_build_strategy_options 纯函数测试。"""

    def test_with_name_key(self):
        """策略有 name_key 时用 I18n.get(name_key) 翻译。"""
        mock_mgr = MagicMock()
        mock_strategy = MagicMock()
        mock_strategy.name_key = "strategy_value_name"
        mock_mgr.get_strategy.return_value = mock_strategy
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "价值策略"
            result = _build_strategy_options(
                {"value": {"name": "旧名称", "missing_apis": []}},
                mock_mgr,
            )
        assert len(result) == 1
        assert result[0].key == "value"
        assert result[0].text == "价值策略"

    def test_without_name_key_falls_back_to_info_name(self):
        """策略无 name_key (或 get_strategy 返回 None) 时用 info['name']。"""
        mock_mgr = MagicMock()
        mock_mgr.get_strategy.return_value = None
        result = _build_strategy_options(
            {"momentum": {"name": "动量策略", "missing_apis": []}},
            mock_mgr,
        )
        assert len(result) == 1
        assert result[0].text == "动量策略"

    def test_missing_apis_adds_warning_suffix(self):
        """missing_apis 非空时追加 (!) 标记 (P2-7: ⚠️ → 文本符号)。"""
        mock_mgr = MagicMock()
        mock_mgr.get_strategy.return_value = None
        result = _build_strategy_options(
            {"northbound": {"name": "北向资金", "missing_apis": ["api1", "api2"]}},
            mock_mgr,
        )
        assert result[0].text is not None
        assert "(!)" in result[0].text

    def test_empty_strategies(self):
        """空策略字典返回空列表。"""
        mock_mgr = MagicMock()
        assert _build_strategy_options({}, mock_mgr) == []


class TestBuildPageSizeOptions:
    """_build_page_size_options 纯函数测试。"""

    def test_returns_four_options(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "条/页"
            result = _build_page_size_options()
        assert len(result) == 4
        keys = [opt.key for opt in result]
        assert keys == ["10", "20", "50", "100"]


class TestResolveGroupTitle:
    """_resolve_group_title 纯函数测试。"""

    def test_label_key_takes_priority(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "自定义标签"
            result = _resolve_group_title("some_group", "label_key_xyz")
        assert result == "自定义标签"
        mock_i18n.get.assert_called_with("label_key_xyz")

    def test_default_group_label(self):
        # DEFAULT_GROUP_LABELS 为 group_name→i18n_key 映射，应通过 I18n.get 渲染
        with (
            patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "param_group_default"}),
            patch("ui.views.screener_view.I18n") as mock_i18n,
        ):
            mock_i18n.get.return_value = "基础设置"
            result = _resolve_group_title("default", None)
        assert result == "基础设置"
        mock_i18n.get.assert_called_with("param_group_default")

    def test_fallback_to_group_name(self):
        # group_name 不在 DEFAULT_GROUP_LABELS 中时，直接返回 group_name
        with patch("ui.theme.DEFAULT_GROUP_LABELS", {}):
            result = _resolve_group_title("unknown_group", None)
        assert result == "unknown_group"


class TestFormatHistoryDate:
    """_format_history_date 纯函数测试。"""

    def test_date_object(self):
        dt = datetime.date(2024, 3, 15)
        display, key = _format_history_date(dt)
        assert display == "2024-03-15"
        assert key == "2024-03-15"

    def test_datetime_object(self):
        dt = datetime.datetime(2024, 3, 15, 10, 30)
        display, key = _format_history_date(dt)
        assert display == "2024-03-15"
        assert key == "2024-03-15"

    def test_8digit_string(self):
        display, key = _format_history_date("20240315")
        assert display == "2024-03-15"
        assert key == "20240315"

    def test_non_date_string(self):
        display, key = _format_history_date("notadate")
        assert display == "notadate"
        assert key == "notadate"


# ============================================================================
# R.2.3: _render_status_message helper 单元测试
# 验证 VM 传 i18n key (如 name_key), View 渲染时翻译为当前 locale
# ============================================================================


class TestRenderStatusMessage:
    """R.2.3: _render_status_message 翻译 ``*_key`` 后缀 params 为当前 locale.

    覆盖 None 边界 / 单 *_key 翻译 / 多 *_key / 非 str 跳过 / 非 *_key 保留 / 空 params.
    """

    def test_none_returns_empty(self):
        """msg=None 时返回空字符串."""
        assert _render_status_message(None) == ""

    @patch("ui.views.screener_view.I18n")
    def test_single_key_param_translated(self, mock_i18n):
        """单个 *_key 后缀 param 翻译并替换字段名 (name_key → name)."""
        from ui.viewmodels import Message

        mock_i18n.get.side_effect = lambda key, **kw: f"[T]{key}" if not kw else f"[T]{key}/{kw}"
        msg = Message("screener_running_strategy", {"name_key": "strategy_value_name"})

        _render_status_message(msg)

        # name_key 被翻译为 [T]strategy_value_name, 字段名替换为 name
        mock_i18n.get.assert_any_call("strategy_value_name")
        # 最终 I18n.get 调用应传 name=翻译值 (非 name_key=raw key)
        final_call = mock_i18n.get.call_args_list[-1]
        assert final_call.args == ("screener_running_strategy",)
        assert final_call.kwargs == {"name": "[T]strategy_value_name"}

    @patch("ui.views.screener_view.I18n")
    def test_non_key_params_preserved(self, mock_i18n):
        """非 *_key 后缀 params 保留原样, 不翻译."""
        from ui.viewmodels import Message

        mock_i18n.get.side_effect = lambda key, **kw: f"[T]{key}" if not kw else f"[T]{key}/{kw}"
        msg = Message("screener_done_saved", {"count": 42, "tables": "northbound_data"})

        _render_status_message(msg)

        # 最终调用应保留 count + tables 原值
        final_call = mock_i18n.get.call_args_list[-1]
        assert final_call.args == ("screener_done_saved",)
        assert final_call.kwargs == {"count": 42, "tables": "northbound_data"}

    @patch("ui.views.screener_view.I18n")
    def test_multiple_key_params_translated(self, mock_i18n):
        """多个 *_key 后缀 params 同时翻译 (name_key + provider_key)."""
        from ui.viewmodels import Message

        mock_i18n.get.side_effect = lambda key, **kw: f"[T]{key}" if not kw else f"[T]{key}/{kw}"
        msg = Message(
            "test_multi_key",
            {"name_key": "strategy_value_name", "provider_key": "provider_qwen", "count": 5},
        )

        _render_status_message(msg)

        # 应分别翻译 name_key 和 provider_key, 保留 count
        mock_i18n.get.assert_any_call("strategy_value_name")
        mock_i18n.get.assert_any_call("provider_qwen")
        final_call = mock_i18n.get.call_args_list[-1]
        assert final_call.args == ("test_multi_key",)
        assert final_call.kwargs == {
            "name": "[T]strategy_value_name",
            "provider": "[T]provider_qwen",
            "count": 5,
        }

    @patch("ui.views.screener_view.I18n")
    def test_non_str_key_param_skipped(self, mock_i18n):
        """*_key 后缀但值非 str 时跳过翻译 (isinstance 守卫)."""
        from ui.viewmodels import Message

        mock_i18n.get.side_effect = lambda key, **kw: f"[T]{key}" if not kw else f"[T]{key}/{kw}"
        # name_key 是 int (异常场景), 不应被翻译
        msg = Message("test_key", {"name_key": 123, "count": 5})

        _render_status_message(msg)

        # name_key 应保留原字段名和值 (因非 str)
        final_call = mock_i18n.get.call_args_list[-1]
        assert final_call.args == ("test_key",)
        assert final_call.kwargs == {"name_key": 123, "count": 5}

    @patch("ui.views.screener_view.I18n")
    def test_empty_params(self, mock_i18n):
        """空 params 的 Message 正常处理."""
        from ui.viewmodels import Message

        mock_i18n.get.return_value = "[T]empty"
        msg = Message("test_empty", {})

        result = _render_status_message(msg)

        assert result == "[T]empty"
        mock_i18n.get.assert_called_once_with("test_empty")

    @patch("ui.views.screener_view.I18n")
    def test_locale_switch_retranslates(self, mock_i18n):
        """R.2.3 核心目标: locale 切换后, 同一 Message 重渲染时用新 locale 翻译 name_key.

        验证: helper 每次调用都重新调 I18n.get(name_key), 不缓存翻译结果.
        """
        from ui.viewmodels import Message

        # 模拟 locale 切换: 第一次 I18n.get(name_key) 返回中文, 第二次返回英文
        translations = iter(["价值策略", "Value Strategy"])
        mock_i18n.get.side_effect = lambda key, **kw: (
            next(translations) if not kw and key == "strategy_value_name" else f"[T]{key}/{kw}"
        )
        msg = Message("screener_running_strategy", {"name_key": "strategy_value_name"})

        # 第一次渲染 (zh_CN locale)
        _render_status_message(msg)
        first_call = mock_i18n.get.call_args_list[-1]
        assert first_call.kwargs == {"name": "价值策略"}

        # 第二次渲染 (en_US locale, 同一 msg)
        _render_status_message(msg)
        second_call = mock_i18n.get.call_args_list[-1]
        assert second_call.kwargs == {"name": "Value Strategy"}, (
            "locale 切换后 helper 必须用新 locale 重新翻译 name_key (R.2.3 核心目标)"
        )
