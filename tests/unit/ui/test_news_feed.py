from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ui.components.news_feed import NewsFeed


@pytest.fixture
def mock_i18n():
    with patch("ui.components.news_feed.I18n") as m:
        m.get.side_effect = lambda key, **kw: key
        return m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.news_feed.AppStyles") as m:
        m.card.return_value = {"bgcolor": "#ffffff", "border_radius": 8, "border": None}
        return m


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.news_feed.AppColors") as m:
        m.TEXT_HINT = "#999"
        m.TEXT_SECONDARY = "#666"
        m.ACCENT = "#0066cc"
        m.TEXT_PRIMARY = "#333"
        m.DIVIDER = "#eee"
        m.UP = "#00aa00"
        m.TRANSPARENT = "transparent"
        m.with_opacity = MagicMock(return_value="#rgba")
        return m


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
        item = feed._build_news_item(row)
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
        item = feed._build_news_item(row)
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
        item = feed._build_news_item(row)
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
        item = feed._build_news_item(row)
        assert item is not None

    def test_build_news_item_missing_tags(self, mock_i18n, mock_app_styles, mock_app_colors):
        feed = NewsFeed()
        row = pd.Series(
            {
                "content": "Test news",
                "publish_time": "2024-06-15 10:30:00",
            }
        )
        item = feed._build_news_item(row)
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
        item = feed._build_news_item(row)
        assert item is not None
