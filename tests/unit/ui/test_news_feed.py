"""ui/components/news_feed.py 声明式契约守护测试 (Phase B.2).

业务逻辑（情感检测/tag 翻译）由本文件纯函数测试覆盖。
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


class TestDetectSentimentChinese:
    """中文情感检测测试 — 新增于 Issue #417 修复。"""

    # --- 中文正向 ---

    def test_chinese_positive_zhangtingban(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("该股涨停板，封板资金达10亿") == "positive"

    def test_chinese_positive_dazhang(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("贵州茅台大涨5%，创新高") == "positive"

    def test_chinese_positive_lihao(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("政策利好刺激市场反弹") == "positive"

    def test_chinese_positive_lingzhang(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("新能源板块领涨，比亚迪飙升") == "positive"

    def test_chinese_positive_yiziting(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("该股一字涨停，市场情绪高涨") == "positive"

    def test_chinese_positive_fangkgaozou(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("股价高开高走，成交量放大") == "positive"

    # --- 中文负向 ---

    def test_chinese_negative_dietingban(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("该股跌停板，封单超5000万") == "negative"

    def test_chinese_negative_baodie(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("芯片股集体暴跌，板块重挫") == "negative"

    def test_chinese_negative_lihai(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("利空消息导致股价跳水") == "negative"

    def test_chinese_negative_lingdie(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("煤炭板块领跌，个股下挫") == "negative"

    def test_chinese_negative_yizidie(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("该股一字跌停，市场恐慌") == "negative"

    def test_chinese_negative_ditou(self):
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("股价低开低走，放量下跌") == "negative"

    # --- 否定词测试 ---

    def test_negation_bu_shangzhang(self):
        """否定正向关键词应减少正向计数。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("该股未上涨，表现平平")
        # "上涨" 被 "未" 否定，不应判为正向
        assert result != "positive"

    def test_negation_wei_xiadie(self):
        """否定负向关键词应产生正向计数。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("大盘并未下跌，企稳反弹") == "positive"

    def test_negation_bingfei_lihai(self):
        """双重否定："并非利空" 应转为正向。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("并非利空，市场反弹") == "positive"

    def test_negation_with_interval_meiyou(self):
        """否定词+间隔字: "没有上涨" 应被否定。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("该股没有上涨，表现平平")
        assert result != "positive"

    def test_negation_with_interval_bushi(self):
        """否定词+间隔字: "不是上涨" 应被否定。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("该股不是上涨，是震荡")
        assert result != "positive"

    def test_negation_mei_keyword(self):
        """否定词"没"："没下跌" 应被否定。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("股价没下跌，企稳反弹")
        assert result == "positive"

    # --- 重叠去重 ---

    def test_overlap_yizi_zhangtingban(self):
        """ "一字涨停板" 仅计1次（"涨停板"不应重复计数）。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("该股一字涨停板") == "positive"

    def test_overlap_no_duplicate(self):
        """同一文本中关键词不应重复计数。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("涨停板封板")
        assert result == "positive"

    # --- 中英混合 ---

    def test_mixed_both_positive(self):
        """中英文正向同时出现。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Stock surge, 该股涨停板") == "positive"

    def test_mixed_both_negative(self):
        """中英文负向同时出现。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("Market plunge, 大盘暴跌") == "negative"

    def test_mixed_english_positive_chinese_negative(self):
        """英文正向 + 中文负向（数量决定结果）。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("Stock surge but 大盘跳水")
        # surge=1 pos(英文), 跳水=1 neg(中文) → equal → neutral
        assert result == "neutral"

    def test_mixed_chinese_negative_english_positive(self):
        """中文负向多于英文正向。"""
        from ui.components.news_feed import _detect_sentiment

        result = _detect_sentiment("该股暴跌重挫，Market surge")
        # 暴跌=1 neg, 重挫=1 neg, surge=1 pos → neg>pos
        assert result == "negative"

    # --- 中文中性 ---

    def test_chinese_neutral(self):
        """无情感关键词的中文新闻。"""
        from ui.components.news_feed import _detect_sentiment

        assert _detect_sentiment("公司发布年报，营收稳定") == "neutral"


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
