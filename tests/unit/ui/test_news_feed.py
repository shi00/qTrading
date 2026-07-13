"""ui/components/news_feed.py 声明式契约守护测试 (Phase B.2).

业务逻辑（情感检测/tag 翻译）由本文件纯函数测试覆盖。
View 层测试聚焦于契约守护（grep 检查禁止的命令式模式），
参照 test_settings_widgets.py 模式。
"""

from pathlib import Path
from unittest.mock import patch

import flet as ft
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node):
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.components.news_feed as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.news_feed as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Contract guard tests
# ---------------------------------------------------------------------------


class TestNewsFeedContract:
    """声明式组件契约守护测试。"""

    def test_component_is_ft_component(self):
        """DoD: NewsFeed 必须被 @ft.component 装饰。"""
        from ui.components.news_feed import NewsFeed

        assert hasattr(NewsFeed, "__wrapped__"), "NewsFeed 必须用 @ft.component 装饰"

    def test_no_class_inheritance(self):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert "class NewsFeed(" not in _code_source()

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_set_news(self):
        """DoD: 禁止命令式 set_news（改用 props 推送）。"""
        assert "set_news" not in _code_source()

    def test_no_prepend_news(self):
        """DoD: 禁止命令式 prepend_news（改用 props 推送）。"""
        assert "prepend_news" not in _code_source()

    def test_no_append_news(self):
        """DoD: 禁止命令式 append_news（改用 props 推送）。"""
        assert "append_news" not in _code_source()

    def test_no_update_news_tag(self):
        """DoD: 禁止命令式 update_news_tag（改用 props 推送）。"""
        assert "update_news_tag" not in _code_source()

    def test_no_update_locale(self):
        """DoD: 禁止命令式 update_locale（声明式通过 Observable state 自动重渲染）。"""
        assert "update_locale" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_no_content_to_ids(self):
        """DoD: 禁止 _content_to_ids 映射（声明式下 tag 更新由 props 推送）。"""
        assert "_content_to_ids" not in _code_source()

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state（locale 自动重渲染）。"""
        assert "get_observable_state" in _raw_source()

    def test_subscribes_app_colors(self):
        """DoD: 必须订阅 AppColors.get_observable_state（sentiment 涨跌色自动重渲染）。"""
        assert "AppColors.get_observable_state" in _raw_source()


# ---------------------------------------------------------------------------
# Pure function tests: _detect_sentiment
# ---------------------------------------------------------------------------


class TestDetectSentiment:
    """Tests for sentiment detection via word-boundary matching (UI-M2)."""

    def test_detect_sentiment_positive(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Stock surge on rally") == "positive"

    def test_detect_sentiment_negative(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Market plunge and crash") == "negative"

    def test_detect_sentiment_neutral(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Regular market update") == "neutral"

    def test_detect_sentiment_update_does_not_match_up(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Update on quarterly results") == "neutral"

    def test_detect_sentiment_case_insensitive(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("STOCK UP ON GAIN") == "positive"

    def test_detect_sentiment_mixed_more_positive(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Stock surge and rally but crash") == "positive"

    def test_detect_sentiment_mixed_more_negative(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Stock fall and plunge but gain") == "negative"

    def test_detect_sentiment_empty_content(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("") == "neutral"

    def test_detect_sentiment_equal_counts_is_neutral(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Stock up but also down") == "neutral"

    def test_detect_sentiment_up_word_boundary(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Prices went up today") == "positive"

    def test_detect_sentiment_bullish_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Market is bullish today") == "positive"

    def test_detect_sentiment_beat_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Company beat earnings estimates") == "positive"

    def test_detect_sentiment_exceed_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Revenue exceed expectations") == "positive"

    def test_detect_sentiment_loss_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Company reports loss this quarter") == "negative"

    def test_detect_sentiment_bearish_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Outlook is bearish") == "negative"

    def test_detect_sentiment_miss_keyword(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Earnings miss forecasts") == "negative"


# ---------------------------------------------------------------------------
# Pure function tests: _translate_tag
# ---------------------------------------------------------------------------


class TestTranslateTag:
    """Tests for tag translation pure function."""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def test_translate_tag_empty(self):
        from ui.components.news_feed import _translate_tag

        assert _translate_tag("") == ""

    def test_translate_tag_none(self):
        from ui.components.news_feed import _translate_tag

        assert _translate_tag(None) == ""

    def test_translate_tag_single(self):
        from ui.components.news_feed import _translate_tag

        result = _translate_tag("公告")
        assert "公告" in result

    def test_translate_tag_multiple(self):
        from ui.components.news_feed import _translate_tag

        result = _translate_tag("公告, 利好")
        assert "公告" in result
        assert "利好" in result


# ---------------------------------------------------------------------------
# Pure function tests: _build_news_item
# ---------------------------------------------------------------------------


class TestBuildNewsItem:
    """Tests for _build_news_item pure rendering function."""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def _make_row(self, **kwargs):
        defaults = {
            "content": "Test news",
            "publish_time": "2024-06-15 10:30:00",
            "tags": "公告",
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_build_news_item_positive_content(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row(content="利好消息 surge rally")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)
        assert item.bgcolor != ft.Colors.TRANSPARENT

    def test_build_news_item_negative_content(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row(content="Market crash and plunge")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)
        assert item.bgcolor != ft.Colors.TRANSPARENT

    def test_build_news_item_neutral_content(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row(content="普通市场新闻")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)
        assert item.bgcolor == ft.Colors.TRANSPARENT

    def test_build_news_item_missing_tags(self):
        from ui.components.news_feed import _build_news_item

        row = pd.Series({"content": "Test news", "publish_time": "2024-06-15 10:30:00"})
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)

    def test_build_news_item_missing_content(self):
        from ui.components.news_feed import _build_news_item

        row = pd.Series({"content": None, "publish_time": "2024-06-15 10:30:00", "tags": "公告"})
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)

    def test_build_news_item_key_set(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row()
        item = _build_news_item(row, "42")
        assert item.key == "42"
