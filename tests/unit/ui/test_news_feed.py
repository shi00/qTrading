"""ui/components/news_feed.py 声明式契约守护测试 (Phase B.2).

业务逻辑（情感着色/tag 翻译）由本文件纯函数测试覆盖。
View 层测试聚焦于契约守护（grep 检查禁止的命令式模式），
参照 test_settings_widgets.py 模式。
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

from pathlib import Path
from unittest.mock import patch

import flet as ft
import pytest

from ui.theme import AppColors
from ui.viewmodels.home_view_model import NewsRow

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
# Pure function tests: _sentiment_style (MINOR-02)
# ---------------------------------------------------------------------------


class TestSentimentStyle:
    """_sentiment_style: 归一入库 market_news.sentiment 值.

    大小写不敏感（DB 混用 Positive/positive）；None/空白/未知 → neutral
    （R21：缺失不伪装、不做本地关键词猜测）。
    """

    def test_none_is_neutral(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style(None) == "neutral"

    def test_empty_is_neutral(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("") == "neutral"

    def test_whitespace_is_neutral(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("   ") == "neutral"

    def test_positive_title_case(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("Positive") == "positive"

    def test_positive_lower_case(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("positive") == "positive"

    def test_negative_title_case(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("Negative") == "negative"

    def test_negative_lower_case(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("negative") == "negative"

    def test_neutral_value_is_neutral(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("Neutral") == "neutral"

    def test_unknown_value_is_neutral(self):
        """未知取值（如 bullish）不猜测，一律中性。"""
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("bullish") == "neutral"

    def test_surrounding_whitespace_stripped(self):
        from ui.components.news_feed import _sentiment_style

        assert _sentiment_style("  Positive  ") == "positive"


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
        return NewsRow(**defaults)

    def test_build_news_item_positive_sentiment_colored(self):
        """DoD: 入库 sentiment=Positive → 正向色着色。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment="Positive")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)
        assert item.bgcolor == ft.Colors.with_opacity(0.1, AppColors.UP_RED)

    def test_build_news_item_negative_sentiment_colored(self):
        """DoD: 入库 sentiment=Negative → 负向色着色。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment="Negative")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)
        assert item.bgcolor == ft.Colors.with_opacity(0.1, AppColors.DOWN_GREEN)

    def test_build_news_item_lowercase_sentiment_colored(self):
        """DB 小写形态同样着色（大小写不敏感）。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment="positive")
        item = _build_news_item(row, "0")
        assert item.bgcolor == ft.Colors.with_opacity(0.1, AppColors.UP_RED)

    def test_build_news_item_neutral_sentiment_not_colored(self):
        """DoD: sentiment=Neutral → 中性不着色。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment="Neutral")
        item = _build_news_item(row, "0")
        assert item.bgcolor == ft.Colors.TRANSPARENT

    def test_build_news_item_missing_sentiment_not_colored(self):
        """DoD: sentiment=None 时不着色（R21 不猜测填充）。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment=None)
        item = _build_news_item(row, "0")
        assert item.bgcolor == ft.Colors.TRANSPARENT

    def test_build_news_item_empty_sentiment_not_colored(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row(sentiment="")
        item = _build_news_item(row, "0")
        assert item.bgcolor == ft.Colors.TRANSPARENT

    def test_build_news_item_ambiguous_phrase_not_colored(self):
        """DoD: "set up" / "down payment" 歧义短语不再触发着色（已移除本地关键词猜测）。"""
        from ui.components.news_feed import _build_news_item

        for text in ("Company to set up a new factory", "Buy a house with 20% down payment"):
            row = self._make_row(content=text, sentiment=None)
            item = _build_news_item(row, "0")
            assert item.bgcolor == ft.Colors.TRANSPARENT, text

    def test_build_news_item_bullish_keyword_content_not_colored(self):
        """本地关键词逻辑已移除：仅凭内容含 surge/rally 不再着色。"""
        from ui.components.news_feed import _build_news_item

        row = self._make_row(content="利好消息 surge rally", sentiment=None)
        item = _build_news_item(row, "0")
        assert item.bgcolor == ft.Colors.TRANSPARENT

    def test_build_news_item_missing_tags(self):
        from ui.components.news_feed import _build_news_item

        row = NewsRow(content="Test news", publish_time="2024-06-15 10:30:00")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)

    def test_build_news_item_missing_content(self):
        from ui.components.news_feed import _build_news_item

        row = NewsRow(content="", publish_time="2024-06-15 10:30:00", tags="公告")
        item = _build_news_item(row, "0")
        assert isinstance(item, ft.Container)

    def test_build_news_item_key_set(self):
        from ui.components.news_feed import _build_news_item

        row = self._make_row()
        item = _build_news_item(row, "42")
        assert item.key == "42"


def _collect_text_values(control, acc: list[str] | None = None) -> list[str]:
    """递归收集控件树中所有 ft.Text 的 value（测试辅助）。"""
    if acc is None:
        acc = []
    if isinstance(control, ft.Text):
        acc.append(control.value or "")
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        _collect_text_values(content, acc)
    elif isinstance(content, list):
        for c in content:
            _collect_text_values(c, acc)
    controls = getattr(control, "controls", None)
    if isinstance(controls, list):
        for c in controls:
            _collect_text_values(c, acc)
    return acc


# ---------------------------------------------------------------------------
# Pure function tests: 时间显示格式 (MINOR-01)
# ---------------------------------------------------------------------------


class TestFormatNewsTime:
    """MINOR-01: 今日 → HH:MM；非今日 → MM-DD HH:MM；缺失/无效 → 未知文案 (R21)."""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def test_today_news_shows_hhmm_only(self):
        from datetime import date

        from ui.components.news_feed import _format_news_time

        assert _format_news_time("2024-06-15 10:30:00", date(2024, 6, 15)) == "10:30"

    def test_non_today_news_shows_mm_dd_hhmm(self):
        from datetime import date

        from ui.components.news_feed import _format_news_time

        assert _format_news_time("2024-06-15 10:30:00", date(2024, 6, 16)) == "06-15 10:30"

    def test_cross_year_news_shows_mm_dd_hhmm(self):
        """跨年新闻仍按需求显示 MM-DD HH:MM（年份歧义由分隔条承担）。"""
        from datetime import date

        from ui.components.news_feed import _format_news_time

        assert _format_news_time("2024-12-31 23:59:00", date(2025, 1, 1)) == "12-31 23:59"

    def test_empty_time_returns_unknown(self):
        from datetime import date

        from ui.components.news_feed import _format_news_time

        # mock I18n.get 返回 key 本身 → 断言返回"未知"文案 key
        assert _format_news_time("", date(2024, 6, 15)) == "news_time_unknown"

    def test_invalid_time_returns_unknown(self):
        """R21: 无法解析的时间串不得伪装成合法时分。"""
        from datetime import date

        from ui.components.news_feed import _format_news_time

        assert _format_news_time("not-a-time", date(2024, 6, 15)) == "news_time_unknown"

    def test_today_rendered_in_item_text(self):
        """DoD: 今日新闻渲染文本只含时分（经 _build_news_item 整条渲染）。"""
        from datetime import date

        from ui.components.news_feed import _build_news_item

        row = NewsRow(content="Test", publish_time="2024-06-15 10:30:00")
        item = _build_news_item(row, "0", today=date(2024, 6, 15))
        texts = _collect_text_values(item)
        assert "10:30" in texts
        assert "06-15" not in texts

    def test_non_today_rendered_with_mm_dd(self):
        """DoD: 跨日新闻渲染文本含 MM-DD。"""
        from datetime import date

        from ui.components.news_feed import _build_news_item

        row = NewsRow(content="Test", publish_time="2024-06-15 10:30:00")
        item = _build_news_item(row, "0", today=date(2024, 6, 16))
        texts = _collect_text_values(item)
        assert "06-15 10:30" in texts


# ---------------------------------------------------------------------------
# Pure function tests: 日期分隔条 (MINOR-01)
# ---------------------------------------------------------------------------


class TestDateSeparator:
    """MINOR-01: 换日时插入日期分隔条。"""

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def _row(self, publish_time: str, content: str = "Test") -> NewsRow:
        return NewsRow(content=content, publish_time=publish_time)

    def _separator_keys(self, controls) -> list[str]:
        return [c.key for c in controls if c.key and str(c.key).startswith("news-date-")]

    def test_same_day_rows_single_separator(self):
        from datetime import date

        from ui.components.news_feed import _build_news_controls

        rows = (
            self._row("2024-06-15 10:30:00"),
            self._row("2024-06-15 09:00:00"),
        )
        controls = _build_news_controls(rows, date(2024, 6, 16))
        assert self._separator_keys(controls) == ["news-date-2024-06-15"]

    def test_day_change_inserts_separator(self):
        """DoD: 跨日时出现新分隔条。"""
        from datetime import date

        from ui.components.news_feed import _build_news_controls

        rows = (
            self._row("2024-06-16 10:30:00"),
            self._row("2024-06-15 23:59:00"),
        )
        controls = _build_news_controls(rows, date(2024, 6, 16))
        assert self._separator_keys(controls) == ["news-date-2024-06-16", "news-date-2024-06-15"]

    def test_today_separator_label(self):
        from datetime import date

        from ui.components.news_feed import _format_date_group_label

        assert _format_date_group_label(date(2024, 6, 15), date(2024, 6, 15)) == "news_date_today"

    def test_same_year_label_mm_dd(self):
        from datetime import date

        from ui.components.news_feed import _format_date_group_label

        assert _format_date_group_label(date(2024, 6, 14), date(2024, 6, 15)) == "06-14"

    def test_cross_year_label_full_date(self):
        """跨年边界：分隔条补全年份避免 MM-DD 歧义。"""
        from datetime import date

        from ui.components.news_feed import _format_date_group_label

        assert _format_date_group_label(date(2024, 12, 31), date(2025, 1, 1)) == "2024-12-31"

    def test_missing_time_no_separator(self):
        """R21: 时间缺失的行不产生日期分隔条，不伪装日期。"""
        from datetime import date

        from ui.components.news_feed import _build_news_controls

        rows = (
            self._row("2024-06-16 10:30:00"),
            self._row(""),
            self._row("2024-06-15 09:00:00"),
        )
        controls = _build_news_controls(rows, date(2024, 6, 16))
        # 缺失时间的行不触发分组变化（归入上一组），仅两个有效日期各一条分隔条
        assert self._separator_keys(controls) == ["news-date-2024-06-16", "news-date-2024-06-15"]

    def test_empty_rows_no_separator(self):
        from datetime import date

        from ui.components.news_feed import _build_news_controls

        assert _build_news_controls((), date(2024, 6, 16)) == []


# ---------------------------------------------------------------------------
# Pure function tests: _translate_title_code (code → i18n key mapping)
# ---------------------------------------------------------------------------


class TestTranslateTitleCode:
    """Tests for title business code → i18n key mapping and translation.

    data 层返回业务 code (如 "no_title"), View 层维护 code → i18n key 映射表,
    在渲染时按当前 locale 翻译 (CLAUDE.md §3.2 i18n 状态驱动；data 层不感知 locale).
    """

    def test_mapping_table_contains_no_title(self):
        """映射表必须包含 no_title → news_no_title 映射。"""
        from ui.components.news_feed import NEWS_TITLE_CODE_TO_I18N_KEY

        assert NEWS_TITLE_CODE_TO_I18N_KEY.get("no_title") == "news_no_title"

    @pytest.fixture(autouse=True)
    def _mock_i18n(self):
        with patch("ui.components.news_feed.I18n") as m:
            m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
            yield m

    def test_translate_title_code_empty(self):
        from ui.components.news_feed import _translate_title_code

        assert _translate_title_code("") == ""

    def test_translate_title_code_none(self):
        from ui.components.news_feed import _translate_title_code

        assert _translate_title_code(None) == ""

    def test_translate_title_code_no_title(self):
        from ui.components.news_feed import _translate_title_code

        # mocked I18n.get returns the key when no default
        assert _translate_title_code("no_title") == "news_no_title"

    def test_translate_title_code_unknown_code(self):
        from ui.components.news_feed import _translate_title_code

        assert _translate_title_code("unknown_code") == ""

    def test_locale_switch_refreshes_translation(self):
        """locale 切换时 _translate_title_code 应返回对应 locale 的翻译。

        验证: 同一业务 code "no_title" 在不同 locale 下返回不同翻译字符串,
        证明函数依赖 I18n 当前 locale (locale 切换触发组件重渲染时翻译自动刷新)。
        """
        from ui.components.news_feed import _translate_title_code

        # 模拟中文 locale: news_no_title → "无标题"
        with patch("ui.components.news_feed.I18n") as mock_zh:
            mock_zh.get.side_effect = lambda key, default=None, **kw: {"news_no_title": "无标题"}.get(key, key)
            assert _translate_title_code("no_title") == "无标题"

        # 模拟英文 locale: news_no_title → "No Title"
        with patch("ui.components.news_feed.I18n") as mock_en:
            mock_en.get.side_effect = lambda key, default=None, **kw: {"news_no_title": "No Title"}.get(key, key)
            assert _translate_title_code("no_title") == "No Title"
