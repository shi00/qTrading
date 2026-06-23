import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed
from ui.components.toast_manager import ToastCard, ToastManager

pytestmark = pytest.mark.unit


class TestToastManager:
    def test_init_appends_container_to_overlay(self, mock_page):
        manager = ToastManager(mock_page)

        assert manager.container in mock_page.overlay

    def test_show_appends_toast_card_to_stack(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("hello")

        assert len(manager.toasts_stack.controls) == 1
        assert isinstance(manager.toasts_stack.controls[0], ToastCard)

    def test_show_multiple_toasts_stacks_them(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("first")
        manager.show("second")
        manager.show("third")

        assert len(manager.toasts_stack.controls) == 3

    def test_show_respects_max_toast_count(self, mock_page):
        manager = ToastManager(mock_page)

        for i in range(ToastManager.MAX_TOAST_COUNT + 3):
            manager.show(f"toast {i}")

        assert len(manager.toasts_stack.controls) == ToastManager.MAX_TOAST_COUNT

    def test_show_removes_oldest_when_exceeding_max(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("oldest")
        manager.show("middle")
        for i in range(ToastManager.MAX_TOAST_COUNT):
            manager.show(f"toast {i}")

        first_control = manager.toasts_stack.controls[0]
        assert first_control.message != "oldest"

    def test_show_does_nothing_when_page_is_none(self):
        manager = ToastManager(None)

        manager.show("should not appear")

        assert len(manager.toasts_stack.controls) == 0

    def test_show_does_nothing_when_stopping(self, mock_page):
        manager = ToastManager(mock_page)
        manager._is_stopping = True

        manager.show("should not appear")

        assert len(manager.toasts_stack.controls) == 0

    def test_show_does_nothing_when_page_controls_empty(self, mock_page):
        mock_page.controls.clear()
        manager = ToastManager(mock_page)

        manager.show("hello")

        assert len(manager.toasts_stack.controls) == 0

    def test_show_registers_task_via_run_task(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("with task")

        assert len(mock_page._tasks) == 1

    def test_show_default_type_is_info(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("default type")

        card = manager.toasts_stack.controls[0]
        assert card.message == "default type"

    def test_show_passes_duration_to_card(self, mock_page):
        manager = ToastManager(mock_page)

        manager.show("timed", duration=5)

        card = manager.toasts_stack.controls[0]
        assert card.duration == 5

    def test_remove_toast_removes_from_stack(self, mock_page):
        manager = ToastManager(mock_page)
        manager.show("target")
        card = manager.toasts_stack.controls[0]

        manager._remove_toast(card)

        assert card not in manager.toasts_stack.controls

    def test_remove_toast_cancels_timer(self, mock_page):
        manager = ToastManager(mock_page)
        manager.show("target")
        card = manager.toasts_stack.controls[0]

        manager._remove_toast(card)

        assert card._is_cancelled is True

    def test_remove_toast_ignores_nonexistent(self, mock_page):
        manager = ToastManager(mock_page)
        fake_card = MagicMock()

        manager._remove_toast(fake_card)

        assert len(manager.toasts_stack.controls) == 0

    @pytest.mark.asyncio
    async def test_stop_all_sets_stopping_flag(self, mock_page):
        manager = ToastManager(mock_page)

        await manager.stop_all()

        assert manager._is_stopping is True

    @pytest.mark.asyncio
    async def test_stop_all_clears_toast_stack(self, mock_page):
        manager = ToastManager(mock_page)
        manager.show("a")
        manager.show("b")

        await manager.stop_all()

        assert len(manager.toasts_stack.controls) == 0

    @pytest.mark.asyncio
    async def test_stop_all_cancels_active_cards(self, mock_page):
        manager = ToastManager(mock_page)
        manager.show("a")
        card = manager.toasts_stack.controls[0]

        await manager.stop_all()

        assert card._is_cancelled is True

    @pytest.mark.asyncio
    async def test_stop_all_is_idempotent(self, mock_page):
        manager = ToastManager(mock_page)

        await manager.stop_all()
        await manager.stop_all()

        assert manager._is_stopping is True

    def test_register_task_adds_to_active_tasks(self, mock_page):
        manager = ToastManager(mock_page)
        mock_task = MagicMock(spec=asyncio.Task)
        manager._register_task(mock_task)
        assert mock_task in manager._active_tasks

    def test_register_task_ignores_non_task(self, mock_page):
        manager = ToastManager(mock_page)

        manager._register_task(None)
        manager._register_task("not a task")

        assert len(manager._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_register_task_auto_cleans_on_done(self, mock_page):
        manager = ToastManager(mock_page)
        task = asyncio.create_task(asyncio.sleep(0))
        manager._register_task(task)
        await task
        assert task not in manager._active_tasks


class TestToastCard:
    def test_init_stores_message(self):
        card = ToastCard(
            message="test msg",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        assert card.message == "test msg"

    def test_init_stores_duration(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=15,
            on_dismiss=None,
        )
        assert card.duration == 15

    def test_init_short_text_not_long(self):
        card = ToastCard(
            message="short",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        assert card.is_long_text is False

    def test_init_long_text_detected(self):
        long_msg = "a" * (ToastCard.LONG_TEXT_THRESHOLD + 1)
        card = ToastCard(
            message=long_msg,
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        assert card.is_long_text is True

    def test_init_long_text_has_expand_btn(self):
        long_msg = "a" * (ToastCard.LONG_TEXT_THRESHOLD + 1)
        card = ToastCard(
            message=long_msg,
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        assert card.expand_btn is not None

    def test_init_short_text_no_expand_btn(self):
        card = ToastCard(
            message="short",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        assert card.expand_btn is None

    def test_cancel_timer_sets_flag(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        card.cancel_timer()
        assert card._is_cancelled is True

    def test_toggle_expand_flips_state(self):
        long_msg = "a" * (ToastCard.LONG_TEXT_THRESHOLD + 1)
        card = ToastCard(
            message=long_msg,
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        card.page = MagicMock()
        card.update = MagicMock()

        initial = card.is_expanded
        card._toggle_expand(None)
        assert card.is_expanded is not initial

    def test_on_hover_sets_hovered_true(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        e = MagicMock()
        e.data = "true"

        card._on_hover(e)

        assert card.is_hovered is True

    def test_on_hover_sets_hovered_false(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        e = MagicMock()
        e.data = "false"

        card._on_hover(e)

        assert card.is_hovered is False

    @pytest.mark.asyncio
    async def test_handle_dismiss_click_calls_dismiss(self):
        dismissed_with = []
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=lambda c: dismissed_with.append(c),
        )
        card.page = MagicMock()
        card.update = MagicMock()

        await card._handle_dismiss_click(None)

        assert card._is_dismissing is True

    @pytest.mark.asyncio
    async def test_handle_dismiss_click_prevents_double_dismiss(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        card._is_dismissing = True

        await card._handle_dismiss_click(None)

        assert card._is_dismissing is True

    @pytest.mark.asyncio
    async def test_start_timer_exits_on_cancel(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        card._is_cancelled = True
        card.page = MagicMock()

        await card.start_timer()

        assert card.remaining == 10

    @pytest.mark.asyncio
    async def test_dismiss_no_page_returns_early(self):
        card = ToastCard(
            message="x",
            icon="icon",
            color="blue",
            duration=10,
            on_dismiss=None,
        )
        card.page = None

        await card.dismiss()


class TestNewsFeed:
    def test_init_shows_empty_state(self):
        feed = NewsFeed()

        assert feed.content == feed.empty_state
        assert feed._showing_list is False

    def test_set_news_with_none_clears_feed(self):
        feed = NewsFeed()
        feed._showing_list = True
        feed.content = feed.news_list

        feed.set_news(None)

        assert feed.content == feed.empty_state
        assert feed._showing_list is False

    def test_set_news_with_empty_df_clears_feed(self):
        feed = NewsFeed()
        feed._showing_list = True
        feed.content = feed.news_list

        feed.set_news(pd.DataFrame())

        assert feed.content == feed.empty_state
        assert feed._showing_list is False

    def test_set_news_with_data_switches_to_list(self):
        feed = NewsFeed()
        df = pd.DataFrame(
            [
                {
                    "content": "news1",
                    "tags": "tech",
                    "publish_time": "2024-01-01 10:00:00",
                }
            ]
        )

        feed.set_news(df)

        assert feed.content == feed.news_list
        assert feed._showing_list is True

    def test_set_news_populates_items(self):
        feed = NewsFeed()
        df = pd.DataFrame(
            [
                {
                    "content": "news1",
                    "tags": "tech",
                    "publish_time": "2024-01-01 10:00:00",
                },
                {
                    "content": "news2",
                    "tags": "finance",
                    "publish_time": "2024-01-01 11:00:00",
                },
            ]
        )

        feed.set_news(df)

        assert len(feed.news_list.controls) == 2

    def test_set_news_with_has_more_appends_button(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "n", "tags": "", "publish_time": ""}])

        feed.set_news(df, has_more=True)

        assert feed.load_more_btn in feed.news_list.controls

    def test_set_news_without_has_more_no_button(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "n", "tags": "", "publish_time": ""}])

        feed.set_news(df, has_more=False)

        assert feed.load_more_btn not in feed.news_list.controls

    def test_set_news_caches_data(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "cached", "tags": "", "publish_time": ""}])

        feed.set_news(df, has_more=True)

        assert feed._cached_news.equals(df)
        assert feed._cached_has_more is True

    def test_set_news_replaces_previous_items(self):
        feed = NewsFeed()
        df1 = pd.DataFrame([{"content": "old", "tags": "", "publish_time": ""}])
        df2 = pd.DataFrame(
            [
                {"content": "new1", "tags": "", "publish_time": ""},
                {"content": "new2", "tags": "", "publish_time": ""},
            ]
        )

        feed.set_news(df1)
        feed.set_news(df2)

        assert len(feed.news_list.controls) == 2

    def test_prepend_news_adds_at_top(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "", "publish_time": ""}])
        feed.set_news(df)

        new_df = pd.DataFrame([{"content": "breaking", "tags": "", "publish_time": ""}])
        feed.prepend_news(new_df)

        first_item = feed.news_list.controls[0]
        col = first_item.content
        content_text = col.controls[1]
        assert content_text.value == "breaking"

    def test_prepend_news_updates_cache(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "", "publish_time": ""}])
        feed.set_news(df)

        new_df = pd.DataFrame([{"content": "breaking", "tags": "", "publish_time": ""}])
        feed.prepend_news(new_df)

        assert len(feed._cached_news) == 2

    def test_prepend_news_with_empty_df_does_nothing(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "", "publish_time": ""}])
        feed.set_news(df)

        feed.prepend_news(pd.DataFrame())

        assert len(feed.news_list.controls) == 1

    def test_prepend_news_with_none_does_nothing(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "", "publish_time": ""}])
        feed.set_news(df)

        feed.prepend_news(None)

        assert len(feed.news_list.controls) == 1

    def test_prepend_news_when_not_showing_list_calls_set_news(self):
        feed = NewsFeed()
        new_df = pd.DataFrame([{"content": "first", "tags": "", "publish_time": ""}])

        feed.prepend_news(new_df)

        assert feed._showing_list is True

    def test_update_news_tag_updates_matching_item(self):
        feed = NewsFeed()
        df = pd.DataFrame(
            [
                {
                    "content": "target news",
                    "tags": "old_tag",
                    "publish_time": "2024-01-01 10:00:00",
                }
            ]
        )
        feed.set_news(df)

        feed.update_news_tag("target news", "new_tag")

        item = feed.news_list.controls[0]
        col = item.content
        row = col.controls[0]
        tag_text = row.controls[0]
        assert tag_text.value != "old_tag"

    def test_update_news_tag_skips_when_no_content_match(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "old", "publish_time": ""}])
        feed.set_news(df)

        feed.update_news_tag("nonexistent", "new")

        item = feed.news_list.controls[0]
        col = item.content
        row = col.controls[0]
        tag_text = row.controls[0]
        assert tag_text.value != "new"

    def test_update_news_tag_skips_empty_content(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "existing", "tags": "old", "publish_time": ""}])
        feed.set_news(df)

        feed.update_news_tag("", "new")

        item = feed.news_list.controls[0]
        col = item.content
        row = col.controls[0]
        tag_text = row.controls[0]
        assert tag_text.value != "new"

    def test_update_news_tag_skips_empty_controls(self):
        feed = NewsFeed()

        feed.update_news_tag("anything", "tag")

        assert feed.content == feed.empty_state

    def test_update_news_tag_updates_all_duplicate_content(self):
        """R1.9: Two news items with same content must both be updated (not just the first)."""
        feed = NewsFeed()
        df = pd.DataFrame(
            [
                {"content": "dup", "tags": "old", "publish_time": "2024-01-01 10:00:00"},
                {"content": "dup", "tags": "old", "publish_time": "2024-01-01 11:00:00"},
            ]
        )
        feed.set_news(df)

        feed.update_news_tag("dup", "new")

        # Both items should have their tag updated (not just the first match)
        for item in feed.news_list.controls:
            col = item.content
            row = col.controls[0]
            tag_text = row.controls[0]
            # _translate_tag("new") returns "new" (default fallback for unknown key)
            assert tag_text.value == "new"

    def test_append_news_adds_items(self):
        feed = NewsFeed()
        df1 = pd.DataFrame([{"content": "first", "tags": "", "publish_time": ""}])
        feed.set_news(df1)

        df2 = pd.DataFrame([{"content": "second", "tags": "", "publish_time": ""}])
        feed.append_news(df2, has_more=False)

        assert len(feed.news_list.controls) == 2

    def test_append_news_removes_load_more_before_appending(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "first", "tags": "", "publish_time": ""}])
        feed.set_news(df, has_more=True)

        df2 = pd.DataFrame([{"content": "second", "tags": "", "publish_time": ""}])
        feed.append_news(df2, has_more=False)

        assert feed.load_more_btn not in feed.news_list.controls

    def test_append_news_with_has_more_readds_button(self):
        feed = NewsFeed()
        df = pd.DataFrame([{"content": "first", "tags": "", "publish_time": ""}])
        feed.set_news(df, has_more=True)

        df2 = pd.DataFrame([{"content": "second", "tags": "", "publish_time": ""}])
        feed.append_news(df2, has_more=True)

        assert feed.news_list.controls[-1] == feed.load_more_btn

    def test_translate_tag_splits_commas(self):
        feed = NewsFeed()

        with patch(
            "ui.components.news_feed.I18n.get",
            side_effect=lambda k, default=None: default or k,
        ):
            result = feed._translate_tag("tech, finance")

        assert "," in result

    def test_translate_tag_empty_returns_empty(self):
        feed = NewsFeed()

        result = feed._translate_tag("")

        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_load_more_calls_callback(self):
        callback = MagicMock()
        callback.return_value = asyncio.sleep(0)
        feed = NewsFeed(on_load_more_click=callback)

        await feed._handle_load_more(None)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_load_more_without_callback(self):
        feed = NewsFeed(on_load_more_click=None)

        await feed._handle_load_more(None)


class TestMarketDashboard:
    def test_init_shows_default_values(self):
        dashboard = MarketDashboard()

        assert dashboard.sh_val.value == "--"
        assert dashboard.sz_val.value == "--"
        assert dashboard.cyb_val.value == "--"
        assert dashboard.hsgt_val.value == "--"

    def test_init_shows_concepts_placeholder(self):
        dashboard = MarketDashboard()

        assert dashboard.concepts_placeholder in dashboard.concepts_row.controls

    def test_update_data_with_empty_dict_does_nothing(self):
        dashboard = MarketDashboard()

        dashboard.update_data({})

        assert dashboard.sh_val.value == "--"

    def test_update_data_with_none_does_nothing(self):
        dashboard = MarketDashboard()

        dashboard.update_data(None)

        assert dashboard.sh_val.value == "--"

    def test_update_data_updates_indices(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200.50", "change": "+1.2%", "color": "RED"},
                {"value": "10500.30", "change": "-0.5%", "color": "GREEN"},
                {"value": "2100.00", "change": "+0.3%", "color": "RED"},
            ]
        }

        dashboard.update_data(data)

        assert dashboard.sh_val.value == "3200.50"
        assert dashboard.sh_chg.value == "+1.2%"
        assert dashboard.sz_val.value == "10500.30"
        assert dashboard.sz_chg.value == "-0.5%"
        assert dashboard.cyb_val.value == "2100.00"

    def test_update_data_applies_red_color_to_up_index(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200", "change": "+1%", "color": "RED"},
                {"value": "10500", "change": "-1%", "color": "GREEN"},
                {"value": "2100", "change": "0%", "color": "GREY"},
            ]
        }

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.sh_chg.color == AppColors.UP

    def test_update_data_applies_green_color_to_down_index(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200", "change": "+1%", "color": "RED"},
                {"value": "10500", "change": "-1%", "color": "GREEN"},
                {"value": "2100", "change": "0%", "color": "GREY"},
            ]
        }

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.sz_chg.color == AppColors.DOWN

    def test_update_data_applies_grey_color_to_neutral_index(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200", "change": "+1%", "color": "RED"},
                {"value": "10500", "change": "-1%", "color": "GREEN"},
                {"value": "2100", "change": "0%", "color": "GREY"},
            ]
        }

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.cyb_chg.color == AppColors.TEXT_SECONDARY

    def test_update_data_updates_hsgt(self):
        dashboard = MarketDashboard()
        data = {"hsgt": {"value": "50.3亿", "sub": "沪股通:30亿", "color": "RED"}}

        dashboard.update_data(data)

        assert dashboard.hsgt_val.value == "50.3亿"
        assert dashboard.hsgt_sub.value == "沪股通:30亿"

    def test_update_data_hsgt_red_color(self):
        dashboard = MarketDashboard()
        data = {"hsgt": {"value": "50亿", "sub": "", "color": "RED"}}

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.hsgt_val.color == AppColors.UP

    def test_update_data_hsgt_green_color(self):
        dashboard = MarketDashboard()
        data = {"hsgt": {"value": "50亿", "sub": "", "color": "GREEN"}}

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.hsgt_val.color == AppColors.DOWN

    def test_update_data_hsgt_grey_color(self):
        dashboard = MarketDashboard()
        data = {"hsgt": {"value": "50亿", "sub": "", "color": "GREY"}}

        dashboard.update_data(data)

        from ui.theme import AppColors

        assert dashboard.hsgt_val.color == AppColors.TEXT_SECONDARY

    def test_update_data_with_hot_concepts_replaces_placeholder(self):
        dashboard = MarketDashboard()
        data = {
            "hot_concepts": [
                {"name": "AI", "change": "+5%", "color": "red"},
                {"name": "新能源", "change": "-2%", "color": "green"},
            ]
        }

        dashboard.update_data(data)

        assert dashboard.concepts_placeholder not in dashboard.concepts_row.controls
        assert len(dashboard.concepts_row.controls) == 2

    def test_update_data_hot_concepts_sets_name(self):
        dashboard = MarketDashboard()
        data = {
            "hot_concepts": [
                {"name": "AI", "change": "+5%", "color": "red"},
            ]
        }

        dashboard.update_data(data)

        card = dashboard.concepts_row.controls[0]
        assert card.data["name"].value == "AI"

    def test_update_data_hot_concepts_sets_change(self):
        dashboard = MarketDashboard()
        data = {
            "hot_concepts": [
                {"name": "AI", "change": "+5.2%", "color": "red"},
            ]
        }

        dashboard.update_data(data)

        card = dashboard.concepts_row.controls[0]
        assert card.data["change"].value == "+5.2%"

    def test_update_data_hot_concepts_up_icon(self):
        dashboard = MarketDashboard()
        data = {
            "hot_concepts": [
                {"name": "AI", "change": "+5%", "color": "red"},
            ]
        }

        dashboard.update_data(data)

        card = dashboard.concepts_row.controls[0]
        from ui.theme import AppColors

        assert card.data["icon"].name == "trending_up"
        assert card.data["change"].color == AppColors.UP

    def test_update_data_hot_concepts_down_icon(self):
        dashboard = MarketDashboard()
        data = {
            "hot_concepts": [
                {"name": "新能源", "change": "-2%", "color": "green"},
            ]
        }

        dashboard.update_data(data)

        card = dashboard.concepts_row.controls[0]
        from ui.theme import AppColors

        assert card.data["icon"].name == "trending_down"
        assert card.data["change"].color == AppColors.DOWN

    def test_update_data_empty_hot_concepts_shows_placeholder(self):
        dashboard = MarketDashboard()
        data_with = {"hot_concepts": [{"name": "AI", "change": "+5%", "color": "red"}]}
        dashboard.update_data(data_with)

        data_without = {"hot_concepts": []}
        dashboard.update_data(data_without)

        assert dashboard.concepts_placeholder in dashboard.concepts_row.controls
        assert len(dashboard.concepts_row.controls) == 1

    def test_update_data_caches_last_data(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200", "change": "+1%", "color": "RED"},
                {"value": "10500", "change": "-1%", "color": "GREEN"},
                {"value": "2100", "change": "0%", "color": "GREY"},
            ]
        }

        dashboard.update_data(data)

        assert dashboard._last_data == data

    def test_update_data_handles_missing_index_gracefully(self):
        dashboard = MarketDashboard()
        data = {
            "indices": [
                {"value": "3200", "change": "+1%", "color": "RED"},
            ]
        }

        dashboard.update_data(data)

        assert dashboard.sz_val.value == "--"
        assert dashboard.cyb_val.value == "--"

    def test_update_data_handles_non_dict_index_gracefully(self):
        dashboard = MarketDashboard()
        data = {"indices": [None, None, None]}

        dashboard.update_data(data)

        assert dashboard.sh_val.value == "--"
        assert dashboard.sh_chg.value == "--"

    def test_update_data_removes_excess_concept_cards(self):
        dashboard = MarketDashboard()
        data_more = {
            "hot_concepts": [
                {"name": "A", "change": "+1%", "color": "red"},
                {"name": "B", "change": "+2%", "color": "red"},
                {"name": "C", "change": "+3%", "color": "red"},
            ]
        }
        dashboard.update_data(data_more)

        data_fewer = {
            "hot_concepts": [
                {"name": "A", "change": "+1%", "color": "red"},
            ]
        }
        dashboard.update_data(data_fewer)

        assert len(dashboard.concepts_row.controls) == 1

    def test_update_data_adds_missing_concept_cards(self):
        dashboard = MarketDashboard()
        data_one = {
            "hot_concepts": [
                {"name": "A", "change": "+1%", "color": "red"},
            ]
        }
        dashboard.update_data(data_one)

        data_three = {
            "hot_concepts": [
                {"name": "A", "change": "+1%", "color": "red"},
                {"name": "B", "change": "+2%", "color": "green"},
                {"name": "C", "change": "+3%", "color": "red"},
            ]
        }
        dashboard.update_data(data_three)

        assert len(dashboard.concepts_row.controls) == 3
