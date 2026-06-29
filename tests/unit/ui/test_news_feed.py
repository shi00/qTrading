from unittest.mock import MagicMock, patch

import flet as ft
import pandas as pd
import pytest

from ui.components.news_feed import NewsFeed

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_i18n():
    with patch("ui.components.news_feed.I18n") as m:
        # 模拟真实 I18n.get：key 不在 locale 中时返回 default（若提供），否则返回 key 本身
        m.get.side_effect = lambda key, default=None, **kw: default if default is not None else key
        yield m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.news_feed.AppStyles") as m:
        m.card.return_value = {"bgcolor": "#ffffff", "border_radius": 8, "border": None}
        yield m


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.news_feed.AppColors") as m:
        m.TEXT_HINT = "#999"
        m.TEXT_SECONDARY = "#666"
        m.ACCENT = "#0066cc"
        m.TEXT_PRIMARY = "#333"
        m.DIVIDER = "#eee"
        m.UP = "#00aa00"
        m.DOWN = "#aa0000"
        m.TRANSPARENT = "transparent"
        m.with_opacity = MagicMock(return_value="#rgba")
        yield m


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.overlay = []
    page.run_task = MagicMock()
    return page


class TestNewsFeed:
    def test_init_creates_empty_state(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed.content is not None

    def test_set_news_empty_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.set_news(pd.DataFrame(), has_more=False)
        assert feed.content == feed.empty_state

    def test_set_news_none_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.set_news(None, has_more=False)
        assert feed.content == feed.empty_state

    def test_set_news_with_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news 1", "Test news 2"],
                "publish_time": ["2024-06-15 10:30:00", "2024-06-15 11:00:00"],
                "tags": ["利好", "公告"],
            }
        )
        feed.set_news(news_data, has_more=True)
        assert feed._showing_list is True
        assert len(feed.news_list.controls) == 3

    def test_set_news_without_has_more(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        assert len(feed.news_list.controls) == 1

    def test_append_news_with_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        initial_data = pd.DataFrame(
            {
                "content": ["Initial news"],
                "publish_time": ["2024-06-15 10:00:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(initial_data, has_more=True)
        append_data = pd.DataFrame(
            {
                "content": ["Appended news"],
                "publish_time": ["2024-06-15 11:00:00"],
                "tags": ["利好"],
            }
        )
        feed.append_news(append_data, has_more=False)
        assert len(feed.news_list.controls) == 2

    def test_append_news_empty_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        initial_data = pd.DataFrame(
            {
                "content": ["Initial news"],
                "publish_time": ["2024-06-15 10:00:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(initial_data, has_more=True)
        feed.append_news(pd.DataFrame(), has_more=False)
        assert len(feed.news_list.controls) == 1

    def test_prepend_news_with_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        initial_data = pd.DataFrame(
            {
                "content": ["Initial news"],
                "publish_time": ["2024-06-15 10:00:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(initial_data, has_more=False)
        prepend_data = pd.DataFrame(
            {
                "content": ["Prepended news"],
                "publish_time": ["2024-06-15 09:00:00"],
                "tags": ["利好"],
            }
        )
        feed.prepend_news(prepend_data)
        assert feed.news_list.controls[0] != feed.load_more_btn

    def test_prepend_news_empty_data(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        initial_data = pd.DataFrame(
            {
                "content": ["Initial news"],
                "publish_time": ["2024-06-15 10:00:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(initial_data, has_more=False)
        initial_count = len(feed.news_list.controls)
        feed.prepend_news(pd.DataFrame())
        assert len(feed.news_list.controls) == initial_count

    def test_prepend_news_to_empty_feed(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        prepend_data = pd.DataFrame(
            {
                "content": ["First news"],
                "publish_time": ["2024-06-15 09:00:00"],
                "tags": ["公告"],
            }
        )
        feed.prepend_news(prepend_data)
        assert feed._showing_list is True

    def test_update_theme(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        feed.update_theme()

    def test_update_theme_empty_cache(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.update_theme()

    def test_update_locale(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.update_locale()
        # 验证 load_more_text 实例属性引用一致性（消除链式 content 访问脆弱，§5.8 规范5）
        assert feed.load_more_text.value == "news_load_more"
        assert feed.load_more_btn.content.content is feed.load_more_text
        assert feed.empty_text.value == "home_news_empty"

    def test_update_locale_with_cached_news_preserves_load_more_text(
        self, mock_i18n, mock_app_styles, mock_app_colors, mock_page
    ):
        """update_locale 在 _cached_news 非空时调用 set_news 重建控件，load_more_text 引用必须保持一致。"""
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=True)
        feed.update_locale()
        # 重建后引用一致性保持（核心修复目标）
        assert feed.load_more_btn.content.content is feed.load_more_text
        assert feed.load_more_text.value == "news_load_more"
        # has_more=True 时按钮仍在控件列表末尾
        assert feed.news_list.controls[-1] is feed.load_more_btn

    def test_translate_tag_empty(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        result = feed._translate_tag("")
        assert result == ""

    def test_translate_tag_none(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        result = feed._translate_tag(None)
        assert result == ""

    def test_translate_tag_single(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        result = feed._translate_tag("公告")
        assert result == "公告"

    def test_translate_tag_multiple(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        result = feed._translate_tag("公告, 利好")
        assert "公告" in result

    def test_update_news_tag_found(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news content"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        feed.update_news_tag("Test news content", "利好")
        assert feed.news_list.controls[0] != feed.load_more_btn

    def test_update_news_tag_not_found(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news content"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        feed.update_news_tag("Non-existent content", "利好")

    def test_update_news_tag_empty_content(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Test news content"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        feed.update_news_tag("", "利好")

    def test_update_news_tag_empty_controls(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        feed = NewsFeed()
        feed.page = mock_page
        feed.update_news_tag("Any content", "利好")

    def test_update_news_tag_updates_all_duplicate_content(
        self, mock_i18n, mock_app_styles, mock_app_colors, mock_page
    ):
        """R1.9: Two news items with same content must both be updated (not just the first)."""
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Same content", "Same content"],
                "publish_time": ["2024-06-15 10:30:00", "2024-06-15 11:00:00"],
                "tags": ["公告", "公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        feed.update_news_tag("Same content", "利好")

        # Both items should have their tag updated (not just the first match)
        # _translate_tag("利好") returns "利好" (default fallback for unknown key "tag_利好")
        tag_values = []
        for item in feed.news_list.controls:
            col = item.content
            row = col.controls[0]
            tag_text = row.controls[0]
            tag_values.append(tag_text.value)
        # Both should be updated to the same translated value, not the original "公告"
        assert len(tag_values) == 2
        assert tag_values[0] == tag_values[1]
        assert tag_values[0] != "公告"

    def test_update_news_tag_no_matching_content_returns_silently(
        self, mock_i18n, mock_app_styles, mock_app_colors, mock_page
    ):
        """R1.9: When content has no matching news_id, update silently returns."""
        feed = NewsFeed()
        feed.page = mock_page
        feed.news_list.page = mock_page
        news_data = pd.DataFrame(
            {
                "content": ["Existing content"],
                "publish_time": ["2024-06-15 10:30:00"],
                "tags": ["公告"],
            }
        )
        feed.set_news(news_data, has_more=False)
        # Capture original tag value
        item = feed.news_list.controls[0]
        original_tag = item.content.controls[0].controls[0].value
        feed.update_news_tag("Non-existent content", "利好")
        # Tag should remain unchanged
        assert item.content.controls[0].controls[0].value == original_tag

    def test_update_news_tag_empty_list_returns_directly(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        """R1.9: Empty news list (no controls) returns directly without error."""
        feed = NewsFeed()
        feed.page = mock_page
        # No set_news called, news_list.controls is empty
        feed.update_news_tag("Any content", "利好")
        assert feed.news_list.controls == []

    @pytest.mark.asyncio
    async def test_handle_load_more(self, mock_i18n, mock_app_styles, mock_app_colors, mock_page):
        load_more_callback = MagicMock()
        feed = NewsFeed(on_load_more_click=load_more_callback)
        feed.page = mock_page

        async def async_callback(e):
            pass

        feed.on_load_more_click = async_callback
        await feed._handle_load_more(MagicMock())

    def test_build_news_item_positive_content(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "利好消息公布",
                "publish_time": "2024-06-15 10:30:00",
                "tags": "利好",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None

    def test_build_news_item_english_positive(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "Stock shows Up trend",
                "publish_time": "2024-06-15 10:30:00",
                "tags": "analysis",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None

    def test_build_news_item_gain_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "Company reports Gain in revenue",
                "publish_time": "2024-06-15 10:30:00",
                "tags": "financial",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None

    def test_build_news_item_negative_content(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "普通市场新闻",
                "publish_time": "2024-06-15 10:30:00",
                "tags": "公告",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None

    def test_build_news_item_missing_tags(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "Test news",
                "publish_time": "2024-06-15 10:30:00",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None

    def test_build_news_item_missing_content(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": None,
                "publish_time": "2024-06-15 10:30:00",
                "tags": "公告",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None


class TestNewsFeedSentiment:
    """Tests for sentiment detection via word-boundary matching (UI-M2)."""

    def test_detect_sentiment_positive(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Stock surge on rally") == "positive"

    def test_detect_sentiment_negative(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Market plunge and crash") == "negative"

    def test_detect_sentiment_neutral(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Regular market update") == "neutral"

    def test_detect_sentiment_update_does_not_match_up(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        # "Update" contains "Up" as substring but \b word boundary must NOT match
        assert feed._detect_sentiment("Update on quarterly results") == "neutral"

    def test_detect_sentiment_case_insensitive(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("STOCK UP ON GAIN") == "positive"

    def test_detect_sentiment_mixed_more_positive(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Stock surge and rally but crash") == "positive"

    def test_detect_sentiment_mixed_more_negative(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Stock fall and plunge but gain") == "negative"

    def test_detect_sentiment_empty_content(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("") == "neutral"

    def test_detect_sentiment_equal_counts_is_neutral(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Stock up but also down") == "neutral"

    def test_detect_sentiment_up_word_boundary(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        # "up" as a standalone word matches
        assert feed._detect_sentiment("Prices went up today") == "positive"

    def test_detect_sentiment_bullish_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Market is bullish today") == "positive"

    def test_detect_sentiment_beat_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Company beat earnings estimates") == "positive"

    def test_detect_sentiment_exceed_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Revenue exceed expectations") == "positive"

    def test_detect_sentiment_loss_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Company reports loss this quarter") == "negative"

    def test_detect_sentiment_bearish_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Outlook is bearish") == "negative"

    def test_detect_sentiment_miss_keyword(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        assert feed._detect_sentiment("Earnings miss forecasts") == "negative"

    def test_build_news_item_negative_uses_down_color(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "Market crash and plunge",
                "publish_time": "2024-06-15 10:30:00",
                "tags": "公告",
            }
        )
        item = feed._build_news_item(row, 1)
        assert item is not None
        # Negative sentiment should use DOWN color with opacity (not transparent)
        assert item.bgcolor != ft.Colors.TRANSPARENT
