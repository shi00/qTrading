"""news_feed 组件纯函数单元测试 (Task 8.1).

测试 _extract_stock_code 股票代码提取逻辑 (纯函数, 无 Flet 渲染依赖).
"""

import pytest

from ui.components.news_feed import _extract_stock_code

pytestmark = pytest.mark.unit


class TestExtractStockCode:
    """Task 8.1: _extract_stock_code 从新闻内容提取 A 股股票代码."""

    def test_empty_content_returns_empty(self):
        assert _extract_stock_code("") == ""

    def test_six_digit_code_extracted(self):
        assert _extract_stock_code("平安银行 000001 涨停") == "000001"

    def test_prefixed_code_extracted(self):
        assert _extract_stock_code("SZ000001 涨停") == "000001"

    def test_multiple_codes_returns_first(self):
        assert _extract_stock_code("000001 和 600519 同时上涨") == "000001"

    def test_eight_digit_date_not_extracted(self):
        """8 位日期中的 6 位子串不应被误匹配."""
        assert _extract_stock_code("20240101 数据发布") == ""

    def test_no_code_returns_empty(self):
        assert _extract_stock_code("今日市场平稳运行") == ""
