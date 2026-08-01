"""news_feed — 声明式组件 (Phase B.2).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式容器子类 → ``@ft.component def NewsFeed(news_rows, ...)``
- 移除所有命令式 API（批量替换/前插/后插/标签更新/locale 刷新/theme 刷新/手动刷新）
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 状态驱动渲染: news_rows/has_more 由消费方通过 props 推送触发重渲染
  (增量更新在声明式下由消费方推送完整 list，组件直接渲染)
- 情感检测 ``_detect_sentiment`` / tag 翻译 ``_translate_tag`` 保留为模块级纯函数
- content→id 映射不再必要（声明式下 tag 更新由消费方推送新 news_rows 触发重渲染）
- L771 合规: news_rows: tuple[NewsRow, ...] 替代 DataFrame
"""

import re
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.home_view_model import NewsRow

_POSITIVE_KEYWORDS = ("surge", "rally", "up", "gain", "bullish", "beat", "exceed")
_NEGATIVE_KEYWORDS = ("plunge", "crash", "fall", "down", "loss", "bearish", "miss")

# data 层返回业务 code (如 "no_title"), View 层维护 code → i18n key 映射,
# 在渲染时按当前 locale 翻译 (CLAUDE.md §3.2 i18n 状态驱动；data 层不感知 locale).
NEWS_TITLE_CODE_TO_I18N_KEY: dict[str, str] = {"no_title": "news_no_title"}

# A 股股票代码正则: 6 位数字, 可选 SH/SZ/BJ 前缀 (如 SZ000001, 600519)
_STOCK_CODE_RE = re.compile(r"(?:SH|SZ|BJ)?(\d{6})")


def _detect_sentiment(content: str) -> str:
    """Detect sentiment using word-boundary matching (case-insensitive)."""
    if not content:
        return "neutral"
    text = content.lower()
    pos_count = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in _POSITIVE_KEYWORDS)
    neg_count = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in _NEGATIVE_KEYWORDS)
    if pos_count > neg_count:
        return "positive"
    if neg_count > pos_count:
        return "negative"
    return "neutral"


def _translate_tag(raw_tag: str) -> str:
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


def _translate_title_code(code: str) -> str:
    """Translate title business code to localized string.

    data 层返回业务 code (如 "no_title"), View 层维护 code → i18n key 映射,
    在渲染时按当前 locale 翻译 (CLAUDE.md §3.2 i18n 状态驱动；data 层不感知 locale).
    """
    if not code:
        return ""
    i18n_key = NEWS_TITLE_CODE_TO_I18N_KEY.get(code)
    if i18n_key is None:
        return ""
    return I18n.get(i18n_key)


def _extract_stock_code(content: str) -> str:
    """从新闻内容中提取第一个 A 股股票代码 (6 位数字).

    返回空字符串表示未找到。仅匹配 6 位连续数字, 过滤日期/年份等误匹配:
    排除以 20 开头的年份模式 (如 20240101) 及纯 8 位日期。
    """
    if not content:
        return ""
    for m in _STOCK_CODE_RE.finditer(content):
        code = m.group(1)
        # 排除 8 位日期中的 6 位子串: 检查前后是否有连续数字
        start = m.start(1)
        end = m.end(1)
        before = content[start - 1] if start > 0 else ""
        after = content[end] if end < len(content) else ""
        if before.isdigit() or after.isdigit():
            continue
        return code
    return ""


def _build_news_item(
    row: NewsRow,
    news_id: str,
    on_view_stock: Callable[[str], None] | None = None,
) -> ft.Container:
    """Build a single news item container (pure function).

    Receives a NewsRow + key, no state dependency.
    """
    raw_tag = row.tags
    translated_tag = _translate_tag(raw_tag)

    content = row.content
    time_str = row.publish_time

    sentiment = _detect_sentiment(content)
    if sentiment == "positive":
        bg_color = ft.Colors.with_opacity(0.1, AppColors.UP_RED)
    elif sentiment == "negative":
        bg_color = ft.Colors.with_opacity(0.1, AppColors.DOWN_GREEN)
    else:
        bg_color = ft.Colors.TRANSPARENT

    # 标签行: translated_tag + AI生成 badge (if applicable) + 时间 + 查看个股 link
    tag_row_controls: list[ft.Control] = []
    if translated_tag:
        tag_row_controls.append(
            ft.Text(
                translated_tag,
                color=AppColors.ACCENT,
                weight=ft.FontWeight.BOLD,
                size=AppStyles.FONT_SIZE_BODY_SM,
            )
        )
    if row.is_ai_tagged:
        tag_row_controls.append(
            ft.Container(
                content=ft.Text(
                    I18n.get("ai_generated_tag"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_ON_PRIMARY,
                ),
                bgcolor=AppColors.ACCENT,
                border_radius=4,
                padding=ft.Padding.symmetric(horizontal=4, vertical=1),
            )
        )
    tag_row_controls.append(ft.Container(expand=True))
    tag_row_controls.append(
        ft.Text(
            time_str[-8:],
            color=AppColors.TEXT_SECONDARY,
            size=AppStyles.FONT_SIZE_BODY_SM,
        )
    )

    # 查看个股 link (仅当内容含股票代码且提供回调时显示)
    stock_code = _extract_stock_code(content)
    content_controls: list[ft.Control] = [
        ft.Text(content, size=AppStyles.FONT_SIZE_LG, color=AppColors.TEXT_PRIMARY),
    ]
    if stock_code and on_view_stock is not None:
        content_controls.append(
            ft.TextButton(
                content=f"{I18n.get('news_view_stock')} ({stock_code})",
                on_click=safe_on_click(lambda _e, c=stock_code: on_view_stock(c)),
                style=ft.ButtonStyle(color=AppColors.PRIMARY),
                height=28,
            )
        )

    return ft.Container(
        key=news_id,
        content=ft.Column(
            [
                ft.Row(tag_row_controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                *content_controls,
            ],
        ),
        padding=10,
        bgcolor=bg_color,
        border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.DIVIDER)),
    )


@ft.component
def NewsFeed(
    news_rows: tuple[NewsRow, ...] = (),
    has_more: bool = False,
    on_load_more_click: Callable[[ft.ControlEvent], None] | None = None,
    on_view_stock: Callable[[str], None] | None = None,
) -> ft.Container:
    """News feed component (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - news_rows/has_more 由消费方通过 props 推送触发重渲染
      (替代旧批量替换/前插/后插命令式 API)
    - tag 更新由消费方推送新 news_rows props 触发重渲染
      (替代旧标签更新命令式 API)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
      (替代旧 locale/theme 刷新命令式 API)
    - L771 合规: news_rows: tuple[NewsRow, ...] 替代 DataFrame

    Args:
        news_rows: 新闻行数据 tuple (NewsRow frozen dataclass),
                   空时显示空状态
        has_more: 是否显示"加载更多"按钮
        on_load_more_click: "加载更多"按钮点击回调
        on_view_stock: "查看个股"点击回调, 接收股票代码 (Task 8.1)
    """
    # Subscribe to i18n + theme changes (triggers auto-rerender)
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    style = AppStyles.card()

    # --- Empty state ---
    if not news_rows:
        return ft.Container(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(
                            ft.Icons.ARTICLE_OUTLINED,
                            size=AppStyles.ICON_SIZE_XL,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            I18n.get("home_news_empty"),
                            color=AppColors.TEXT_HINT,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                alignment=ft.Alignment.CENTER,
                expand=True,
            ),
            expand=True,
            bgcolor=style.get("bgcolor"),
            border_radius=style.get("border_radius"),
            border=style.get("border"),
            padding=10,
        )

    # --- Build news items ---
    controls: list[ft.Control] = [
        _build_news_item(row, str(i), on_view_stock=on_view_stock) for i, row in enumerate(news_rows)
    ]

    # --- Load more button ---
    if has_more:
        controls.append(
            ft.Container(
                content=ft.Button(
                    content=ft.Text(
                        I18n.get("news_load_more"),
                        color=AppColors.TEXT_ON_PRIMARY,
                    ),
                    style=ft.ButtonStyle(
                        bgcolor={ft.ControlState.DEFAULT: AppColors.PRIMARY},
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                    on_click=safe_on_click(on_load_more_click),
                    height=40,
                    width=AppStyles.CONTROL_WIDTH_SM,
                ),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.only(top=10, bottom=10),
            )
        )

    # --- News list ---
    return ft.Container(
        content=ft.ListView(
            controls=controls,
            spacing=10,
            padding=10,
            auto_scroll=False,
            expand=True,
        ),
        expand=True,
        bgcolor=style.get("bgcolor"),
        border_radius=style.get("border_radius"),
        border=style.get("border"),
        padding=10,
    )
