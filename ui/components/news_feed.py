import logging

import flet as ft
import pandas as pd

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


class NewsFeed(ft.Container):
    """
    News Feed Component
    Displays a scrollable list of news items.
    """

    def __init__(self, on_load_more_click=None):
        style = AppStyles.card()
        super().__init__()
        self.expand = True
        self.bgcolor = style["bgcolor"]  # type: ignore
        self.border_radius = style["border_radius"]  # type: ignore
        self.border = style.get("border")
        self.padding = 10
        self.on_load_more_click = on_load_more_click

        # Internal State
        self._cached_news = pd.DataFrame()  # Cache for theme reloading
        self._cached_has_more = False

        self.news_list = ft.ListView(
            spacing=10,
            padding=10,
            auto_scroll=False,
            expand=True,
        )

        # I18n Refs
        self.empty_text = ft.Text(
            I18n.get("home_news_empty"),
            color=AppColors.TEXT_HINT,
        )

        self.empty_state = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(
                        ft.Icons.ARTICLE_OUTLINED,
                        size=48,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    self.empty_text,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ft.alignment.center,
            expand=True,
        )

        self.load_more_btn = ft.Container(
            content=ft.ElevatedButton(
                content=ft.Text(I18n.get("news_load_more"), color=ft.Colors.WHITE),
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.DEFAULT: AppColors.PRIMARY},
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
                on_click=self._handle_load_more,
                height=40,
                width=120,
            ),
            alignment=ft.alignment.center,
            padding=ft.padding.only(top=10, bottom=10),
        )

        # Initial Content
        self.content = self.empty_state

        # Track if we are showing list or empty state
        self._showing_list = False

    def update_locale(self):
        """Update static text when locale changes"""
        self.empty_text.value = I18n.get("home_news_empty")
        self.load_more_btn.content.content.value = I18n.get("news_load_more")  # type: ignore
        self.update()

    def update_theme(self):
        """Re-render list on theme change"""
        style = AppStyles.card()
        self.bgcolor = style["bgcolor"]  # type: ignore
        self.bgcolor = style["bgcolor"]  # type: ignore
        # Update static texts (only if not using semantic tokens, but here we are)
        # self.empty_text.color = AppColors.TEXT_HINT  <-- Automatic
        # self.load_more_text.color = AppColors.PRIMARY_LIGHT <-- Automatic

        # Re-render list from cache
        if not self._cached_news.empty:
            self.set_news(self._cached_news, self._cached_has_more)

        if self.page:
            self.update()

    async def _handle_load_more(self, e):
        if self.on_load_more_click:
            await self.on_load_more_click(e)

    def set_news(self, news_data: pd.DataFrame, has_more: bool = False):
        """
        Full replace of news list (e.g. on first load or refresh).
        """
        self._cached_news = news_data if news_data is not None else pd.DataFrame()
        self._cached_has_more = has_more

        if news_data is None or news_data.empty:
            self.content = self.empty_state
            self._showing_list = False
            if self.page:
                self.update()
            return

        # Switch to list view if needed
        if not self._showing_list:
            self.content = self.news_list
            self._showing_list = True
            # Need to update container to show the list
            if self.page:
                self.update()

        # Rebuild items
        controls = []
        for _, row in news_data.iterrows():
            controls.append(self._build_news_item(row))

        if has_more:
            controls.append(self.load_more_btn)

        self.news_list.controls = controls
        if self.page:
            self.news_list.update()

    def _translate_tag(self, raw_tag: str) -> str:
        """Translate tag using I18n with fallback."""
        if not raw_tag:
            return ""
        tags = [t.strip() for t in raw_tag.split(",") if t.strip()]
        translated_parts = []
        for t in tags:
            tk = f"tag_{t.lower()}"
            tv = I18n.get(tk, default=t)
            translated_parts.append(tv)
        return ",".join(translated_parts) if translated_parts else raw_tag

    def update_news_tag(self, content: str, tags: str):
        """
        Update tag for a single news item by content match (TAG_UPDATE).
        """
        if not content or not self.news_list.controls:
            return

        translated_tag = self._translate_tag(tags)

        for item in self.news_list.controls:
            if item == self.load_more_btn:
                continue
            try:
                col = item.content  # type: ignore
                if not isinstance(col, ft.Column):
                    continue
                row = col.controls[0] if col.controls else None
                content_text = col.controls[1] if len(col.controls) > 1 else None
                if not isinstance(row, ft.Row) or not isinstance(content_text, ft.Text):
                    continue
                if content_text.value != content:
                    continue
                for row_ctrl in row.controls:
                    if (
                        isinstance(row_ctrl, ft.Text)
                        and row_ctrl.weight == ft.FontWeight.BOLD
                    ):
                        row_ctrl.value = translated_tag
                        if self.page:
                            self.news_list.update()
                        return
            except Exception as e:
                logger.warning(f"[NewsFeed] Error updating tag: {e}")
                continue

    def prepend_news(self, news_data: pd.DataFrame):
        """
        Insert new items at the top (Real-time updates).
        """
        if news_data is None or news_data.empty:
            return

        # Update cache
        if self._cached_news.empty:
            self._cached_news = news_data
        else:
            self._cached_news = pd.concat(
                [news_data, self._cached_news],
                ignore_index=True,
            )

        # Ensure we are in list mode
        if not self._showing_list:
            self.set_news(news_data, has_more=False)
            return

        new_items = []
        # Reverse iteration to keep order correct when inserting at 0
        for i in range(len(news_data) - 1, -1, -1):
            row = news_data.iloc[i]
            new_items.append(self._build_news_item(row))

        # Insert at top
        for item in new_items:
            self.news_list.controls.insert(0, item)

        if self.page:
            self.news_list.update()

    def append_news(self, news_data: pd.DataFrame, has_more: bool):
        """
        Append items at the bottom (Load More).
        """
        # Remove load more btn first if it exists
        if (
            self.news_list.controls
            and self.news_list.controls[-1] == self.load_more_btn
        ):
            self.news_list.controls.pop()

        for _, row in news_data.iterrows():
            self.news_list.controls.append(self._build_news_item(row))

        # Update Cache
        if not news_data.empty:
            if self._cached_news.empty:
                self._cached_news = news_data
            else:
                self._cached_news = pd.concat(
                    [self._cached_news, news_data],
                    ignore_index=True,
                )
        self._cached_has_more = has_more

        if has_more:
            self.news_list.controls.append(self.load_more_btn)

        if self.page:
            self.news_list.update()

    def _build_news_item(self, row):
        raw_tag = row.get("tags", "") or ""
        translated_tag = self._translate_tag(raw_tag)

        content = str(row.get("content", "") or "")
        time_str = str(row.get("publish_time", "") or "")

        is_positive = "利好" in content or "Up" in content or "Gain" in content
        bg_color = (
            ft.Colors.with_opacity(0.1, AppColors.UP)
            if is_positive
            else ft.Colors.TRANSPARENT
        )

        item = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                translated_tag,
                                color=AppColors.ACCENT,
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                            ft.Text(
                                time_str[-8:],
                                color=AppColors.TEXT_SECONDARY,
                                size=12,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(content, size=14, color=AppColors.TEXT_PRIMARY),
                ],
            ),
            padding=10,
            bgcolor=bg_color,
            border=ft.border.only(bottom=ft.BorderSide(1, AppColors.DIVIDER)),
        )
        return item
