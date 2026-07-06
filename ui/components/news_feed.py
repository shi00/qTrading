import logging
import re

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

    _news_id_counter: int = 0  # 类属性，递增计数器，为每条新闻分配唯一 ID

    def __init__(self, on_load_more_click=None):
        style = AppStyles.card()
        super().__init__()
        self.expand = True
        self.bgcolor = style["bgcolor"]  # type: ignore[untyped]
        self.border_radius = style["border_radius"]  # type: ignore[untyped]
        self.border = style.get("border")
        self.padding = 10
        self.on_load_more_click = on_load_more_click

        # Internal State
        self._cached_news = pd.DataFrame()  # Cache for theme reloading
        self._cached_has_more = False
        self._content_to_ids: dict[str, list[int]] = {}  # content → news_id 列表映射

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
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

        self.load_more_text = ft.Text(I18n.get("news_load_more"), color=ft.Colors.WHITE)

        self.load_more_btn = ft.Container(
            content=ft.Button(
                content=self.load_more_text,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.DEFAULT: AppColors.PRIMARY},
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
                on_click=self._handle_load_more,
                height=40,
                width=120,
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.only(top=10, bottom=10),
        )

        # Initial Content
        self.content = self.empty_state

        # Track if we are showing list or empty state
        self._showing_list = False

    def update_locale(self):
        """Update static text when locale changes"""
        try:
            self.empty_text.value = I18n.get("home_news_empty")
            self.load_more_text.value = I18n.get("news_load_more")
            # 重建已渲染新闻项，刷新 tag 翻译（_translate_tag 在渲染时固化，与 update_theme 一致）
            if not self._cached_news.empty:
                self.set_news(self._cached_news, self._cached_has_more)
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[NewsFeed] update_locale failed: %s", e, exc_info=True)

    def update_theme(self):
        """Re-render list on theme change"""
        # bgcolor 使用 ft.Colors.SURFACE 语义 token（__init__ 中设置），自动随主题切换，无需重新赋值。
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
        self._content_to_ids = {}
        controls = []
        for _, row in news_data.iterrows():
            self._news_id_counter += 1
            news_id = self._news_id_counter
            controls.append(self._build_news_item(row, news_id))
            content = str(row.get("content", "") or "")
            self._content_to_ids.setdefault(content, []).append(news_id)

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
        Update tag for news items matching the given content (TAG_UPDATE).

        Uses ``_content_to_ids`` to locate all news_ids for the content, then
        precisely targets each control via its ``key`` attribute. This ensures
        all duplicate-content items are updated (not just the first match).
        """
        if not content or not self.news_list.controls:
            return

        translated_tag = self._translate_tag(tags)
        news_ids = self._content_to_ids.get(content, [])
        if not news_ids:
            return

        # Build a lookup from key → control for O(1) precise targeting
        key_to_item: dict[str, ft.Control] = {}
        for item in self.news_list.controls:
            if item == self.load_more_btn:
                continue
            key_val = getattr(item, "key", None)
            if key_val is not None:
                key_to_item[str(key_val)] = item

        for news_id in news_ids:
            key_str = str(news_id)
            item = key_to_item.get(key_str)
            if item is None:
                continue
            try:
                col = item.content  # type: ignore[untyped]
                if not isinstance(col, ft.Column):
                    continue
                row = col.controls[0] if col.controls else None
                if not isinstance(row, ft.Row):
                    continue
                for row_ctrl in row.controls:
                    if isinstance(row_ctrl, ft.Text) and row_ctrl.weight == ft.FontWeight.BOLD:
                        row_ctrl.value = translated_tag
                        break
            except Exception as e:
                logger.warning("[NewsFeed] Error updating tag: %s", e, exc_info=True)
                continue

        if self.page:
            self.news_list.update()

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
            self._news_id_counter += 1
            news_id = self._news_id_counter
            new_items.append(self._build_news_item(row, news_id))
            content = str(row.get("content", "") or "")
            self._content_to_ids.setdefault(content, []).append(news_id)

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
        if self.news_list.controls and self.news_list.controls[-1] == self.load_more_btn:
            self.news_list.controls.pop()

        for _, row in news_data.iterrows():
            self._news_id_counter += 1
            news_id = self._news_id_counter
            self.news_list.controls.append(self._build_news_item(row, news_id))
            content = str(row.get("content", "") or "")
            self._content_to_ids.setdefault(content, []).append(news_id)

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

    _POSITIVE_KEYWORDS = ("surge", "rally", "up", "gain", "bullish", "beat", "exceed")
    _NEGATIVE_KEYWORDS = ("plunge", "crash", "fall", "down", "loss", "bearish", "miss")

    @classmethod
    def _detect_sentiment(cls, content: str) -> str:
        """Detect sentiment using word-boundary matching (case-insensitive)."""
        if not content:
            return "neutral"
        text = content.lower()
        pos_count = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in cls._POSITIVE_KEYWORDS)
        neg_count = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in cls._NEGATIVE_KEYWORDS)
        if pos_count > neg_count:
            return "positive"
        if neg_count > pos_count:
            return "negative"
        return "neutral"

    def _build_news_item(self, row, news_id: int):
        raw_tag = row.get("tags", "") or ""
        translated_tag = self._translate_tag(raw_tag)

        content = str(row.get("content", "") or "")
        time_str = str(row.get("publish_time", "") or "")

        sentiment = self._detect_sentiment(content)
        if sentiment == "positive":
            bg_color = ft.Colors.with_opacity(0.1, AppColors.UP)
        elif sentiment == "negative":
            bg_color = ft.Colors.with_opacity(0.1, AppColors.DOWN)
        else:
            bg_color = ft.Colors.TRANSPARENT

        item = ft.Container(
            key=str(news_id),
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
            border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.DIVIDER)),
        )
        return item
