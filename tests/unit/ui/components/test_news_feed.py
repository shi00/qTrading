"""news_feed 组件纯函数单元测试 (Task 8.1 / UX-09 MINOR-03).

测试 _extract_stock_codes 股票引用提取逻辑 (纯函数, 无 Flet 渲染依赖).
UX-09 MINOR-03: 仅匹配强标记 (SH/SZ/BJ 前缀或被括号包裹) 且落在有效代码集合内的代码,
裸 6 位数字 (金额/数量) 一律不生成链接。
"""

from unittest.mock import patch

import flet as ft
import pytest

from ui.components.news_feed import _extract_stock_codes
from ui.viewmodels.home_view_model import NewsRow

pytestmark = pytest.mark.unit

# 有效代码集合 (模拟 stock_basic): 刻意包含 600000 / 300750,
# 以证明「即使裸数字恰好是合法代码」也不得生成链接 (强标记优先)。
_VALID = frozenset({"000001", "600519", "600000", "300750", "430047"})


class TestExtractStockCodes:
    """Task 8.1 / UX-09 MINOR-03: _extract_stock_codes 提取有效股票引用."""

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
        assert _extract_stock_codes("SZ000001 涨停", _VALID) == ["000001"]

    def test_bracket_with_prefix_linked(self):
        assert _extract_stock_codes("（SH600519）公告", _VALID) == ["600519"]

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
    """UX-09 MINOR-03: 「查看个股」链接渲染 (整条新闻渲染级验证 DoD)。"""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def _links(self, content: str) -> list[str]:
        from ui.components.news_feed import _build_news_item

        row = NewsRow(content=content, publish_time="2024-06-15 10:30:00")
        item = _build_news_item(row, "0", on_view_stock=lambda _c: None, valid_stock_codes=_VALID)
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
