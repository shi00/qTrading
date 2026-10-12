"""新闻情绪指数聚合服务（N5-2，提案 §10；输出仅展示，ADR-0008）。

单股/市场日度情绪指数：

    S = Σ(score_i × conf_i × w_src) / Σ(conf_i × w_src)

- 来源权重：巨潮公告 1.5 / 新浪个股 1.0 / 快讯 0.8（未知源 1.0）；
- 只统计 ``sentiment_score`` 非空（且 conf 有限）的新闻；当日无可用新闻指数为
  ``None``（R21，不画成 0）；分母 Σ(conf×w)=0（conf 全 0）→ ``None``；
- 交易日归属（R24 时点正确性）：先按发布时间 → 所属交易日（收盘后 / 非交易日
  归次一交易日，CST 15:00 收盘线），再按交易日聚合；禁止用自然日历日直接归组；
- 盘中实时：对「as_of 所属交易日」桶内行按 0.5^(Δt/4h) 衰减（Δt = as_of − 发布
  时刻，CST）；历史交易日桶不衰减（历史指数不随查询时间漂移）；
- 市场级：``【市场】`` 前缀新闻单独聚合，与个股桶互斥。

分层：services → data（CacheManager / TradeCalendarService / news_match）合法依赖；
输出仅用于展示（K 线情绪柱 / 洞察面板），选股/策略模块不得读取（N3-7 守护）。
"""

from __future__ import annotations

import datetime
import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.news_match import match_news_to_stock
from utils.singleton_registry import register_singleton
from utils.time_utils import CST_TZ, from_utc_to_cst, get_now, to_utc_for_db

logger = logging.getLogger(__name__)

# 收盘线（CST）：15:00 及以后发布的新闻归次一交易日（R24，提案 §10）。
NEWS_DAY_CUTOFF = datetime.time(15, 0)

# 来源权重（提案 §10）：巨潮公告 1.5 / 新浪个股 1.0 / 快讯 0.8；
# 未知源（含存量 source_kind IS NULL 行）按 1.0 计。
SOURCE_WEIGHTS: dict[str, float] = {"announcement": 1.5, "news": 1.0, "telegraph": 0.8}
_UNKNOWN_SOURCE_WEIGHT = 1.0

# 盘中实时衰减半衰期（小时，提案 §10「约 4 小时」）。
HALF_LIFE_HOURS = 4.0

# 「样本不足」阈值：当日参与聚合条数 < 3 时标记（提案 §10）。
MIN_SAMPLE_COUNT = 3

# 市场级（宏观/大盘）新闻前缀：单独聚合，与个股桶互斥（提案 §10）。
MARKET_SCOPE_PREFIX = "【市场】"

# 个股快讯匹配预筛上限 / 市场级快讯窗口读取上限。
_TELEGRAPH_LIMIT = 500
_MARKET_LIMIT = 1000

# 交易日历查询缓冲：向前多取 10 天（收盘后新闻归属次一交易日需未来日历），
# 向后按 days×2+15 自然日取（覆盖长假 + 保证 days 个历史交易日）。
_FORWARD_CALENDAR_DAYS = 10


@dataclass(frozen=True)
class DailySentimentPoint:
    """单交易日情绪指数点（R21：index=None 表示当日无可用新闻，不是 0）。"""

    trade_date: datetime.date
    index: float | None
    sample_count: int
    sample_insufficient: bool


def _is_finite_number(value: object) -> bool:
    """有限数值判定（None/bool/NaN/Inf/非数值 → False，R21 缺失不计入）。"""
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]  # 外层 try 已兜 TypeError，object→float 运行时安全
    except (TypeError, ValueError):
        return False


def assign_trade_day(
    publish_time_utc_naive: datetime.datetime | None,
    trade_days: list[datetime.date] | tuple[datetime.date, ...] | set[datetime.date],
) -> datetime.date | None:
    """新闻发布时刻 → 所属交易日（R24 时点正确性，提案 §10；纯函数）。

    规则（CST 视角）：
    - < 15:00 且当日为交易日 → 当日；
    - ≥ 15:00 或当日非交易日（周末/节假日）→ 次一交易日；
    - 日历不可得（trade_days 未覆盖该日期及其后续）→ ``None``（调用方丢弃，不猜测）。

    Args:
        publish_time_utc_naive: 入库时点（UTC tz-naive，与 market_news.publish_time
            一致）；aware datetime 亦接受（按其自身时区换算）。
        trade_days: 升序交易日列表（含去重即可，函数内部排序去重），由调用方
            一次性取自 TradeCalendarService（DB → API → 离线兜底链）。
    """
    if publish_time_utc_naive is None:
        return None
    publish_cst = from_utc_to_cst(publish_time_utc_naive)
    if publish_cst is None:
        return None
    day = publish_cst.date()
    ordered = sorted(set(trade_days))
    if publish_cst.time() < NEWS_DAY_CUTOFF and day in set(ordered):
        return day
    return next((d for d in ordered if d > day), None)


def _decay_weight(publish_cst: datetime.datetime, as_of_cst: datetime.datetime) -> float:
    """盘中实时衰减权重：0.5^(Δt / 半衰期)，Δt = as_of − 发布（CST，小时）。"""
    delta_hours = (as_of_cst - publish_cst).total_seconds() / 3600.0
    return 0.5 ** (delta_hours / HALF_LIFE_HOURS)


def aggregate_daily(
    rows: list[dict],
    trade_days: list[datetime.date] | tuple[datetime.date, ...] | set[datetime.date],
    *,
    as_of: datetime.datetime | None = None,
    days: int = 7,
) -> list[DailySentimentPoint]:
    """按交易日聚合日度情绪指数（提案 §10；纯函数，无 IO）。

    Args:
        rows: 每项含 ``publish_time``（UTC tz-naive datetime）、``sentiment_score``、
            ``sentiment_conf``、``source_kind``（可空）。
        trade_days: 覆盖查询窗口（含未来缓冲）的交易日列表。
        as_of: 实时锚点（CST aware datetime；naive 按 CST 解释）。None = 历史模式
            （不衰减、不剔除未来行，输出取 trade_days 尾部 days 个）。
        days: 输出的历史交易日个数。

    聚合口径：
    - 只统计 score 与 conf 均为有限数值的行（R21：缺失/NaN 不计入、不伪装）；
    - S = Σ(score×conf×w)/Σ(conf×w)；n=0 或分母 ≤ 0（conf 全 0）→ index=None；
    - n < 3 标 ``sample_insufficient``（index 仍给出，展示层标注样本不足）；
    - 归属（R24）：:func:`assign_trade_day`；日历不可得的行丢弃（debug 记数）；
    - 衰减：仅 as_of 提供时，对 as_of 所属交易日桶内行乘 0.5^(Δt/4h)；发布时刻
      晚于 as_of 的行（时钟偏移）剔除；其余（历史）桶不衰减；
    - 输出 = 最后 days 个 ≤ as_of 日的交易日（无数据日填充 index=None）+
      归属到未来交易日且有数据的尾部桶（收盘后新闻，R24）。
    """
    ordered = sorted(set(trade_days))
    if not ordered:
        return []

    as_of_cst: datetime.datetime | None = None
    anchor_day: datetime.date | None = None
    if as_of is not None:
        as_of_cst = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=CST_TZ)
        anchor_day = assign_trade_day(to_utc_for_db(as_of_cst), ordered)

    # 桶：trade_date -> [(score, conf, 有效权重)]
    buckets: dict[datetime.date, list[tuple[float, float, float]]] = defaultdict(list)
    dropped_unassigned = 0
    for row in rows:
        score = row.get("sentiment_score")
        conf = row.get("sentiment_conf")
        if not _is_finite_number(score) or not _is_finite_number(conf):
            continue  # R21：缺失/非有限不计入、不伪装
        publish_utc = row.get("publish_time")
        publish_cst = from_utc_to_cst(publish_utc) if publish_utc is not None else None
        if publish_cst is None:
            dropped_unassigned += 1
            continue
        if as_of_cst is not None and publish_cst > as_of_cst:
            continue  # 发布时刻晚于 as_of（时钟偏移）→ 剔除，防未来信息泄漏
        assigned = assign_trade_day(publish_utc, ordered)
        if assigned is None:
            dropped_unassigned += 1
            continue
        weight = SOURCE_WEIGHTS.get(row.get("source_kind") or "", _UNKNOWN_SOURCE_WEIGHT)
        if anchor_day is not None and as_of_cst is not None and assigned == anchor_day:
            weight *= _decay_weight(publish_cst, as_of_cst)
        buckets[assigned].append((float(score), float(conf), weight))  # type: ignore[arg-type]  # _is_finite_number 已确认 score/conf 为有限数值

    if dropped_unassigned:
        logger.debug(
            "[NewsSentimentIndex] %s rows dropped: trade day unassignable (calendar gap).",
            dropped_unassigned,
        )

    # 输出日期序列：历史（≤ as_of 日，含无数据日填 None）+ 未来尾部有数据桶
    as_of_date = as_of_cst.date() if as_of_cst is not None else ordered[-1]
    history = [d for d in ordered if d <= as_of_date][-days:] if days > 0 else []
    future = sorted(d for d in buckets if d > as_of_date)
    output_dates = list(dict.fromkeys(history + future))

    points: list[DailySentimentPoint] = []
    for day in output_dates:
        samples = buckets.get(day, [])
        n = len(samples)
        denom = sum(conf * w for _, conf, w in samples)
        index: float | None = None
        if n > 0 and denom > 0.0 and math.isfinite(denom):
            raw = sum(score * conf * w for score, conf, w in samples) / denom
            if math.isfinite(raw):
                index = raw
        points.append(
            DailySentimentPoint(
                trade_date=day,
                index=index,
                sample_count=n,
                sample_insufficient=index is not None and n < MIN_SAMPLE_COUNT,
            )
        )
    return points


def _is_market_scope_row(row: dict) -> bool:
    """``【市场】`` 前缀判定（提案 §10 市场级口径；content 去首部空白后前缀匹配）。"""
    content = row.get("content") or ""
    return content.lstrip().startswith(MARKET_SCOPE_PREFIX)


def _finite_or_none(value: object) -> float | None:
    """DataFrame 数值 → 有限 float 或 None（NaN/Inf/非数值 → None，R21）。"""
    if not _is_finite_number(value):
        return None
    return float(value)  # type: ignore[arg-type]  # _is_finite_number 已确认 float(value) 可行，pyright 无法跨函数收窄


def _df_to_scored_rows(df: pd.DataFrame | None) -> list[dict]:
    """DAO 返回的 market_news DataFrame → 聚合输入行（统一类型，NaT/NaN → None）。"""
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        publish_time = r.get("publish_time")
        if publish_time is not None and pd.isna(publish_time):
            publish_time = None
        title = r.get("title")
        content = r.get("content")
        if title is not None and pd.isna(title):
            title = None  # NaN 是 truthy，混入匹配文本会成为字面 "nan"
        if content is not None and pd.isna(content):
            content = None
        out.append(
            {
                "publish_time": publish_time,
                "sentiment_score": _finite_or_none(r.get("sentiment_score")),
                "sentiment_conf": _finite_or_none(r.get("sentiment_conf")),
                "source_kind": r.get("source_kind"),
                "title": title,
                "content": content,
            }
        )
    return out


@register_singleton
class NewsSentimentIndexService:
    """新闻情绪指数服务（N5-2）：个股/市场日度情绪指数（仅展示，ADR-0008）。

    无共享可变状态（每次查询独立取数 + 纯函数聚合），单例仅为复用依赖句柄。
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(
        self,
        cache: CacheManager | None = None,
        trade_calendar: TradeCalendarService | None = None,
    ):
        # CON-01: double-checked locking（同其他注册单例，初始化整体移入锁内）。
        if getattr(self, "_initialized", False):
            return
        with self._lock:
            if getattr(self, "_initialized", False):
                return
            self._cache = cache if cache is not None else CacheManager()
            self._calendar = trade_calendar
            self._initialized = True

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def _get_calendar(self) -> TradeCalendarService:
        """交易日历（DB → Tushare → 离线兜底链）；惰性构造，支持测试注入。"""
        if self._calendar is None:
            self._calendar = TradeCalendarService(self._cache)
        return self._calendar

    async def _load_window(
        self,
        now_cst: datetime.datetime,
        days: int,
    ) -> tuple[list[datetime.date], datetime.datetime | None, datetime.datetime | None]:
        """查询窗口：交易日列表（含未来缓冲）+ 新闻取数窗口（UTC naive 边界）。"""
        calendar = self._get_calendar()
        cal_end = now_cst.date() + datetime.timedelta(days=_FORWARD_CALENDAR_DAYS)
        cal_start = now_cst.date() - datetime.timedelta(days=days * 2 + 15)
        trade_days = list(await calendar.get_trade_dates(cal_start, cal_end))
        if not trade_days:
            logger.warning("[NewsSentimentIndex] Trade calendar unavailable; sentiment index skipped.")
            return [], None, None
        start_utc = to_utc_for_db(datetime.datetime.combine(cal_start, datetime.time.min))
        end_utc = to_utc_for_db(now_cst)
        return trade_days, start_utc, end_utc

    async def get_stock_daily_index(
        self,
        ts_code: str,
        stock_name: str | None = None,
        days: int = 7,
        as_of: datetime.datetime | None = None,
    ) -> list[DailySentimentPoint]:
        """个股日度情绪指数（提案 §10；ts_code 直达公告/个股新闻 + 快讯精确匹配并入）。

        - 公告（announcement）/ 个股新闻（news）：``get_scored_stock_news`` 按
          ts_code 直达；
        - 快讯（telegraph）：``get_telegraph_news_for_stocks`` DB 侧预筛 +
          :func:`match_news_to_stock` 精确关联后并入；
        - ``【市场】`` 前缀新闻与个股桶互斥（单独进市场级，见
          :meth:`get_market_daily_index`）。
        """
        if not ts_code or days <= 0:
            return []
        now_cst = as_of if as_of is not None else get_now()
        trade_days, start_utc, end_utc = await self._load_window(now_cst, days)
        if not trade_days:
            return []
        rows = await self._collect_stock_rows(str(ts_code), stock_name, start_utc, end_utc)
        return aggregate_daily(rows, trade_days, as_of=now_cst, days=days)

    async def _collect_stock_rows(
        self,
        ts_code: str,
        stock_name: str | None,
        start_utc: datetime.datetime | None,
        end_utc: datetime.datetime | None,
    ) -> list[dict]:
        mdao = self._cache.market_dao
        rows: list[dict] = []
        direct = await mdao.get_scored_stock_news(ts_code, start_time=start_utc, end_time=end_utc)
        for row in _df_to_scored_rows(direct):
            if _is_market_scope_row(row):
                continue  # 【市场】前缀与个股桶互斥（提案 §10）
            rows.append(row)
        tele = await mdao.get_telegraph_news_for_stocks(
            [ts_code],
            [stock_name] if stock_name else [],
            start_time=start_utc,
            end_time=end_utc,
            limit=_TELEGRAPH_LIMIT,
        )
        candidates = [{"ts_code": ts_code, "name": (stock_name or "").strip()}]
        for row in _df_to_scored_rows(tele):
            if _is_market_scope_row(row):
                continue
            text = " ".join(part for part in (row.get("title"), row.get("content")) if part)
            if match_news_to_stock(text, candidates):
                rows.append(row)
        return rows

    async def get_market_daily_index(
        self,
        days: int = 7,
        as_of: datetime.datetime | None = None,
    ) -> list[DailySentimentPoint]:
        """市场级（大盘）日度情绪指数：``【市场】`` 前缀快讯单独聚合（提案 §10）。"""
        if days <= 0:
            return []
        now_cst = as_of if as_of is not None else get_now()
        trade_days, start_utc, end_utc = await self._load_window(now_cst, days)
        if not trade_days:
            return []
        tele = await self._cache.market_dao.get_telegraph_news_in_window(
            start_utc,
            end_utc,
            limit=_MARKET_LIMIT,
        )
        rows = [row for row in _df_to_scored_rows(tele) if _is_market_scope_row(row)]
        return aggregate_daily(rows, trade_days, as_of=now_cst, days=days)
