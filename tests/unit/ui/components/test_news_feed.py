"""news_feed 组件纯函数单元测试 (Task 8.1 / UX-09 MINOR-03 / M27-08).

测试 _extract_stock_codes 股票引用提取逻辑 (纯函数, 无 Flet 渲染依赖).
UX-09 MINOR-03: 仅匹配强标记 (SH/SZ/BJ 前缀或被括号包裹) 且落在有效代码集合内的代码,
裸 6 位数字 (金额/数量) 一律不生成链接。
M27-08: 带交易所前缀的候选必须命中 ``CODE.EXCHANGE`` 完整股票身份集合, 避免
「上证指数 SH000001」被错误链接为平安银行 (000001.SZ)。
"""

from datetime import date
from unittest.mock import patch

import flet as ft
import pytest

from ui.components.news_feed import _build_news_item, _extract_stock_codes
from ui.viewmodels.home_view_model import NewsRow

pytestmark = pytest.mark.unit

# 有效代码集合 (模拟 stock_basic): 刻意包含 600000 / 300750,
# 以证明「即使裸数字恰好是合法代码」也不得生成链接 (强标记优先)。
_VALID = frozenset({"000001", "600519", "600000", "300750", "430047"})

# 完整股票身份集合 (ts_code, 模拟 stock_basic): 000001.SZ 为平安银行;
# 000001.SH 是指数, 不在股票身份集合内 (M27-08)。
_VALID_TS = frozenset({"000001.SZ", "600519.SH", "600000.SH", "300750.SZ", "430047.BJ"})


class TestExtractStockCodes:
    """Task 8.1 / UX-09 MINOR-03 / M27-08: _extract_stock_codes 提取有效股票引用."""

    def test_empty_content_returns_empty(self):
        assert _extract_stock_codes("", _VALID) == []

    def test_no_code_returns_empty(self):
        assert _extract_stock_codes("今日市场平稳运行", _VALID) == []

    def test_bare_amount_not_linked_even_if_valid_code(self):
        """DoD: "募资 600000 万元" 不生成链接——600000 虽是合法代码, 但无强标记故不匹配。"""
        assert _extract_stock_codes("募资 600000 万元", _VALID) == []
        assert _extract_stock_codes("成交 300750 手", _VALID) == []

    def test_bracketed_code_linked(self):
        """DoD: "（600519）" 生成正确链接。"""
        assert _extract_stock_codes("贵州茅台（600519）发布公告", _VALID) == ["600519"]

    def test_half_width_bracket_linked(self):
        assert _extract_stock_codes("贵州茅台(600519)发布公告", _VALID) == ["600519"]

    def test_prefixed_code_linked(self):
        assert _extract_stock_codes("SZ000001 涨停", _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001"]

    def test_bracket_with_prefix_linked(self):
        assert _extract_stock_codes("（SH600519）公告", _VALID, valid_stock_ts_codes=_VALID_TS) == ["600519"]

    def test_multiple_codes_return_multiple(self):
        """DoD: 正文含多只有效代码时返回多个 (按出现顺序)。"""
        content = "贵州茅台（600519）与平安银行（000001）同时上涨"
        assert _extract_stock_codes(content, _VALID) == ["600519", "000001"]

    def test_multiple_codes_deduped(self):
        content = "（600519）再次提及（600519）"
        assert _extract_stock_codes(content, _VALID) == ["600519"]

    def test_code_not_in_valid_set_excluded(self):
        assert _extract_stock_codes("（123456）", _VALID) == []

    def test_eight_digit_date_in_bracket_excluded(self):
        """8 位日期不会被括号分支截取为 6 位代码。"""
        assert _extract_stock_codes("（20240101）数据发布", _VALID) == []

    def test_empty_valid_codes_returns_empty(self):
        """R21 精神: 无有效代码集合时降级为不链接 (不生成未校验链接)。"""
        assert _extract_stock_codes("贵州茅台（600519）", frozenset()) == []
        assert _extract_stock_codes("贵州茅台（600519）", None) == []


class TestExtractStockCodesExchangeIdentity:
    """M27-08: 保留交易所身份, 带前缀候选须命中完整股票 ts_code 集合。"""

    def test_sh000001_index_not_linked_as_stock(self):
        """上证指数 SH000001 的 000001.SH 不是股票, 不得借用裸 000001 生成链接。"""
        assert _extract_stock_codes("上证指数 SH000001 收涨", _VALID, valid_stock_ts_codes=_VALID_TS) == []

    def test_sz000001_stock_linked(self):
        assert _extract_stock_codes("平安银行 SZ000001 上涨", _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001"]

    def test_exchange_mismatch_excluded(self):
        """SH600519 命中完整身份, 但 SH000001 不命中; 不得按 symbol 放行错配交易所。"""
        content = "贵州茅台 SH600519 与上证指数 SH000001"
        assert _extract_stock_codes(content, _VALID, valid_stock_ts_codes=_VALID_TS) == ["600519"]

    def test_prefixed_code_requires_ts_identity_set(self):
        """未提供完整股票身份集合时, 带前缀候选一律不展示 (R21 精神: 宁少不伪造)。"""
        assert _extract_stock_codes("SZ000001 涨停", _VALID) == []
        assert _extract_stock_codes("SZ000001 涨停", _VALID, valid_stock_ts_codes=frozenset()) == []

    def test_bracket_with_prefix_requires_ts_identity_set(self):
        assert _extract_stock_codes("（SH600519）公告", _VALID) == []
        assert _extract_stock_codes("（SH000001）指数公告", _VALID, valid_stock_ts_codes=_VALID_TS) == []

    def test_bracket_without_exchange_keeps_bare_validation(self):
        """括号内无交易所维持既有 bare symbol 集合校验, 与完整身份集合无关。"""
        assert _extract_stock_codes("（600519）公告", _VALID) == ["600519"]
        assert _extract_stock_codes("（600519）公告", frozenset(), valid_stock_ts_codes=_VALID_TS) == []

    def test_lowercase_prefix_normalized(self):
        assert _extract_stock_codes("sz000001 涨停", _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001"]
        assert _extract_stock_codes("（sh600519）公告", _VALID, valid_stock_ts_codes=_VALID_TS) == ["600519"]

    def test_dedupe_and_order_across_forms(self):
        """同一 symbol 的裸括号与带前缀形式只保留首次出现, 且顺序不变。"""
        content = "平安银行（000001）与 SZ000001 同一只股票"
        assert _extract_stock_codes(content, _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001"]
        content = "SZ000001 与 贵州茅台（600519）"
        assert _extract_stock_codes(content, _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001", "600519"]

    def test_index_and_stock_same_symbol_only_stock_linked(self):
        """SH000001(指数) 与 SZ000001(股票) 同文出现时只链接股票一次。"""
        content = "上证指数 SH000001 与平安银行 SZ000001 分化"
        assert _extract_stock_codes(content, _VALID, valid_stock_ts_codes=_VALID_TS) == ["000001"]


def _collect_link_labels(control) -> list[str]:
    """递归收集控件树中所有 ft.TextButton 的 content (「查看个股」链接标签, 测试辅助)。"""
    labels: list[str] = []
    if isinstance(control, ft.TextButton):
        labels.append(control.content if isinstance(control.content, str) else "")
    for attr in ("content", "controls"):
        value = getattr(control, attr, None)
        if isinstance(value, ft.Control):
            labels.extend(_collect_link_labels(value))
        elif isinstance(value, list):
            for child in value:
                labels.extend(_collect_link_labels(child))
    return labels


class TestBuildNewsItemStockLinks:
    """UX-09 MINOR-03 / M27-08: 「查看个股」链接渲染 (整条新闻渲染级验证 DoD)。"""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def _links(self, content: str, *, with_ts: bool = True) -> list[str]:
        row = NewsRow(content=content, publish_time="2024-06-15 10:30:00")
        item = _build_news_item(
            row,
            "0",
            on_view_stock=lambda _c: None,
            valid_stock_codes=_VALID,
            valid_stock_ts_codes=_VALID_TS if with_ts else None,
        )
        return _collect_link_labels(item)

    def test_bare_amount_renders_no_link(self):
        """DoD: "募资 600000 万元" 不生成「查看个股」链接。"""
        assert self._links("募资 600000 万元") == []

    def test_bracketed_code_renders_link(self):
        """DoD: "（600519）" 生成正确链接。"""
        labels = self._links("贵州茅台（600519）发布公告")
        assert len(labels) == 1
        assert "600519" in labels[0]

    def test_multiple_codes_render_multiple_links(self):
        """DoD: 正文含多只有效代码时生成多个链接。"""
        labels = self._links("贵州茅台（600519）与平安银行（000001）同时上涨")
        assert len(labels) == 2
        assert "600519" in labels[0]
        assert "000001" in labels[1]

    def test_index_prefix_renders_no_link(self):
        """M27-08 DoD: 上证指数 SH000001 不渲染「查看个股」。"""
        assert self._links("上证指数 SH000001 收涨") == []

    def test_prefixed_stock_renders_link(self):
        labels = self._links("平安银行 SZ000001 上涨")
        assert len(labels) == 1
        assert "000001" in labels[0]

    def test_prefixed_without_ts_identity_renders_no_link(self):
        """宿主未传入完整身份集合时, 带前缀链接收紧为不展示。"""
        assert self._links("平安银行 SZ000001 上涨", with_ts=False) == []

    def test_legacy_positional_arguments_still_supported(self):
        """旧位置实参调用保持可用 (M27-08 向后兼容)。"""
        row = NewsRow(content="贵州茅台（600519）发布公告", publish_time="2024-06-15 10:30:00")
        item = _build_news_item(
            row,
            "0",
            lambda _c: None,
            date(2024, 6, 15),
            _VALID,
        )
        labels = _collect_link_labels(item)
        assert len(labels) == 1
        assert "600519" in labels[0]
