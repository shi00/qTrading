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
- 时间显示: 今日 → ``HH:MM``；非今日 → ``MM-DD HH:MM``；换日插入日期分隔条
  (``_build_news_controls``，``today`` 由 ``utils.time_utils.get_now()`` 注入)
"""

import datetime
import re
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.home_view_model import NewsRow
from utils.time_utils import get_now

# 英文关键词（保留原有，\b 单词边界匹配）
_POSITIVE_EN_KEYWORDS = ("surge", "rally", "up", "gain", "bullish", "beat", "exceed")
_NEGATIVE_EN_KEYWORDS = ("plunge", "crash", "fall", "down", "loss", "bearish", "miss")

# 中文关键词（双字词及以上，按长度降序排列以避免重叠计数）
_POSITIVE_CN_KEYWORDS = (
    "涨停板",
    "一字涨停",
    "大涨",
    "暴涨",
    "飙升",
    "上涨",
    "走高",
    "走强",
    "突破新高",
    "创新高",
    "利好",
    "逆势上涨",
    "强势上涨",
    "高开高走",
    "放量上涨",
    "反弹",
    "领涨",
)
_NEGATIVE_CN_KEYWORDS = (
    "跌停板",
    "一字跌停",
    "大跌",
    "暴跌",
    "下挫",
    "跳水",
    "重挫",
    "走弱",
    "破位下行",
    "利空",
    "逆势下跌",
    "弱势下跌",
    "低开低走",
    "放量下跌",
    "回调",
    "领跌",
)

# 中文否定词模式：匹配关键词前 0-2 个否定字 + 可选间隔字（是/有）
# 覆盖场景: "不上涨"/"没有上涨"/"不是上涨"/"未下跌"
_CN_NEGATION_CHAR = "[不未非没]"
_CN_NEGATION_PREFIX_RE = re.compile(rf"{_CN_NEGATION_CHAR}{{0,2}}[是有]{{0,1}}")

# 预编译中文关键词正则（正向+负向）
_CN_POSITIVE_PATTERNS = tuple(
    re.compile(rf"({_CN_NEGATION_PREFIX_RE.pattern}{re.escape(kw)})") for kw in _POSITIVE_CN_KEYWORDS
)
_CN_NEGATIVE_PATTERNS = tuple(
    re.compile(rf"({_CN_NEGATION_PREFIX_RE.pattern}{re.escape(kw)})") for kw in _NEGATIVE_CN_KEYWORDS
)

# 否定词检测正则（预编译）
_CN_IS_NEGATED_RE = re.compile(rf"{_CN_NEGATION_CHAR}{{1,2}}")

# data 层返回业务 code (如 "no_title"), View 层维护 code → i18n key 映射,
# 在渲染时按当前 locale 翻译 (CLAUDE.md §3.2 i18n 状态驱动；data 层不感知 locale).
NEWS_TITLE_CODE_TO_I18N_KEY: dict[str, str] = {"no_title": "news_no_title"}

# A 股股票代码正则: 6 位数字, 可选 SH/SZ/BJ 前缀 (如 SZ000001, 600519)
_STOCK_CODE_RE = re.compile(r"(?:SH|SZ|BJ)?(\d{6})")


def _detect_sentiment(content: str) -> str:
    """Detect sentiment using bilingual keyword matching.

    中文: 否定词窗口检测 + 区间去重（避免重叠子串重复计数）
    英文: \\b 单词边界匹配（保留原有逻辑）
    """
    if not content:
        return "neutral"
    text = content.lower()

    # --- 中文匹配 ---
    pos_cn = 0
    neg_cn = 0
    matched_spans: list[tuple[int, int]] = []

    def _has_overlap(span: tuple[int, int]) -> bool:
        """检查新区间是否与已有区间重叠。"""
        s, e = span
        return any(not (e <= ms or s >= me) for ms, me in matched_spans)

    # 中文正向关键词
    for pattern in _CN_POSITIVE_PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if _has_overlap(span):
                continue
            matched_spans.append(span)
            if _CN_IS_NEGATED_RE.match(m.group(1)):
                neg_cn += 1
            else:
                pos_cn += 1

    # 中文负向关键词
    for pattern in _CN_NEGATIVE_PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if _has_overlap(span):
                continue
            matched_spans.append(span)
            if _CN_IS_NEGATED_RE.match(m.group(1)):
                pos_cn += 1
            else:
                neg_cn += 1

    # --- 英文匹配（保留原有 \\b 单词边界逻辑）---
    pos_en = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in _POSITIVE_EN_KEYWORDS)
    neg_en = sum(len(re.findall(rf"\b{kw}\b", text)) for kw in _NEGATIVE_EN_KEYWORDS)

    # --- 汇总判定 ---
    pos_count = pos_cn + pos_en
    neg_count = neg_cn + neg_en
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


# 时间/日期缺失时的 i18n 键（R21: 不以看似合法的时分伪装缺失）
NEWS_TIME_UNKNOWN_KEY = "news_time_unknown"
NEWS_DATE_TODAY_KEY = "news_date_today"


def _parse_publish_time(time_str: str) -> datetime.datetime | None:
    """解析 publish_time 字符串为 datetime；缺失/无法解析返回 None。

    兼容 DB 返回的 ``YYYY-MM-DD HH:MM:SS``（含 pandas.Timestamp 字符串化）
    与 ISO ``T`` 分隔形态；不臆造时区（``market_news.publish_time`` 为无时区列）。
    """
    # NOTE(lazy): 直接按 publish_time 字面日期/HH:MM 展示与分组，不做 UTC→CST 换算。
    # ceiling: 写库路径把源站 CST 文本经 to_utc_for_db 转为 UTC tz-naive，故 DB 字面时间为 UTC；
    #   与 get_now() 的 CST "今日" 在 CST 00:00–07:59 窗口存在日期/时刻偏移（沿用既有展示口径，
    #   修复前 time_str[-8:] 亦为 UTC 原值，非本次引入）。
    # upgrade: 当首页快讯需与 CST 展示严格对齐，或出现跨时区分组投诉时，改用 from_utc_to_cst 换算后再格式化/分组。
    if not time_str:
        return None
    try:
        return datetime.datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return None


def _format_news_time(time_str: str, today: datetime.date) -> str:
    """格式化新闻发布时间显示。

    今日 → ``HH:MM``；非今日 → ``MM-DD HH:MM``；
    缺失/无法解析 → i18n ``news_time_unknown``（R21: 不以合法时分伪装缺失）。
    """
    dt = _parse_publish_time(time_str)
    if dt is None:
        return I18n.get(NEWS_TIME_UNKNOWN_KEY)
    if dt.date() == today:
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def _format_date_group_label(date_obj: datetime.date, today: datetime.date) -> str:
    """日期分隔条标签：今日 → i18n ``news_date_today``；同年 → ``MM-DD``；跨年 → ``YYYY-MM-DD``。

    跨年时补全年份，避免不同年份的 ``MM-DD`` 视觉歧义。
    """
    if date_obj == today:
        return I18n.get(NEWS_DATE_TODAY_KEY)
    if date_obj.year == today.year:
        return date_obj.strftime("%m-%d")
    return date_obj.strftime("%Y-%m-%d")


def _build_date_separator(date_obj: datetime.date, today: datetime.date) -> ft.Control:
    """构建日期分隔条（换日时出现）。"""
    return ft.Container(
        key=f"news-date-{date_obj.isoformat()}",
        content=ft.Row(
            [
                ft.Container(expand=True, height=1, bgcolor=AppColors.DIVIDER),
                ft.Text(
                    _format_date_group_label(date_obj, today),
                    color=AppColors.TEXT_HINT,
                    size=AppStyles.FONT_SIZE_CAPTION,
                ),
                ft.Container(expand=True, height=1, bgcolor=AppColors.DIVIDER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(vertical=2),
    )


def _build_news_item(
    row: NewsRow,
    news_id: str,
    on_view_stock: Callable[[str], None] | None = None,
    today: datetime.date | None = None,
) -> ft.Container:
    """Build a single news item container (pure function).

    Receives a NewsRow + key, no state dependency.
    ``today`` 用于判定"今日/非今日"显示格式；缺省取 ``get_now().date()``
    （测试应显式注入固定日期，与生产取日期同源）。
    """
    if today is None:
        today = get_now().date()

    raw_tag = row.tags
    translated_tag = _translate_tag(raw_tag)

    content = row.content

    sentiment = _detect_sentiment(content)
    if sentiment == "positive":
        bg_color = ft.Colors.with_opacity(0.1, AppColors.UP_RED)
    elif sentiment == "negative":
        bg_color = ft.Colors.with_opacity(0.1, AppColors.DOWN_GREEN)
    else:
        bg_color = AppColors.TRANSPARENT

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
            _format_news_time(row.publish_time, today),
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


def _build_news_controls(
    news_rows: tuple[NewsRow, ...],
    today: datetime.date,
    on_view_stock: Callable[[str], None] | None = None,
) -> list[ft.Control]:
    """构建新闻列表控件，换日时插入日期分隔条（纯函数，``today`` 注入便于测试）。

    按 ``news_rows`` 顺序遍历：某行日期与上一已渲染日期组不同时插入分隔条；
    同一日期多条目仅一条分隔条；日期缺失/无法解析的行不触发分组变化（R21）。
    """
    controls: list[ft.Control] = []
    current_date: datetime.date | None = None
    for i, row in enumerate(news_rows):
        parsed = _parse_publish_time(row.publish_time)
        row_date = parsed.date() if parsed is not None else None
        if row_date is not None and row_date != current_date:
            controls.append(_build_date_separator(row_date, today))
            current_date = row_date
        controls.append(_build_news_item(row, str(i), on_view_stock=on_view_stock, today=today))
    return controls


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
                padding=ft.Padding.symmetric(vertical=32),
            ),
            bgcolor=style.get("bgcolor"),
            border_radius=style.get("border_radius"),
            border=style.get("border"),
            padding=10,
        )

    # --- Build news items (含换日日期分隔条) ---
    today = get_now().date()
    controls: list[ft.Control] = _build_news_controls(news_rows, today, on_view_stock=on_view_stock)

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
        content=ft.Column(
            controls=controls,
            spacing=10,
        ),
        bgcolor=style.get("bgcolor"),
        border_radius=style.get("border_radius"),
        border=style.get("border"),
        padding=10,
    )
