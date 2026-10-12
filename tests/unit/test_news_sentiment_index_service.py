# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock 模式，统一局部禁用参数类型与 mock 属性访问告警（同 test_market_dao.py 范式）。

"""N5-2 情绪指数聚合服务单测：R24 交易日归属 / 加权聚合 / 盘中衰减 / 市场级互斥。

覆盖 Plans N5-2 DoD：
- 聚合：除零（conf 全 0）→ None；无新闻 → None（R21 不画 0）；n<3 标样本不足；
  来源权重生效；score/conf 缺失行不计入；
- 归属（R24）：盘中→当日、14:59→当日、15:00→次一交易日、周末/节假日→次一
  交易日、CST 00:30（UTC 前日 16:30 naive）→ CST 当日、日历不可得→丢弃；
- 衰减：Δt=4h 权重减半、as_of=None 不衰减、Δt<0（时钟偏移）剔除、历史桶不衰减；
- 市场级：【市场】前缀单独聚合、与个股桶互斥；
- 写库链：scorer.model_tag + 落库载荷（见 test_news_subscription_service.py /
  test_market_dao.py 对应用例）。
"""

import dataclasses
import datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.news_sentiment_index_service import (
    DailySentimentPoint,
    NewsSentimentIndexService,
    aggregate_daily,
    assign_trade_day,
)
from utils.time_utils import to_utc_for_db

pytestmark = pytest.mark.unit

# 合成交易日历：10-09（周五）/ 10-12（周一）~ 10-15（周四）；10-10、10-11 为周末，
# 10-16 起不在日历内（模拟「日历不可得」窗口）。
TRADE_DAYS = [
    datetime.date(2026, 10, 9),
    datetime.date(2026, 10, 12),
    datetime.date(2026, 10, 13),
    datetime.date(2026, 10, 14),
    datetime.date(2026, 10, 15),
]


def _cst(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime.datetime:
    """CST aware 时刻。"""
    return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))


def _utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime.datetime:
    """UTC tz-naive 时刻（market_news.publish_time 口径）。"""
    return datetime.datetime(y, m, d, hh, mm)


def _row(publish_cst: datetime.datetime, score: float | None, conf: float | None, source_kind: str | None = "news"):
    return {
        "publish_time": to_utc_for_db(publish_cst),
        "sentiment_score": score,
        "sentiment_conf": conf,
        "source_kind": source_kind,
    }


# ---------------------------------------------------------------------------
# assign_trade_day（R24 归属）
# ---------------------------------------------------------------------------


class TestAssignTradeDay:
    def test_intraday_maps_same_day(self):
        assert assign_trade_day(_utc(2026, 10, 12, 2, 0), TRADE_DAYS) == datetime.date(2026, 10, 12)  # CST 10:00

    def test_1459_maps_same_day(self):
        assert assign_trade_day(_utc(2026, 10, 12, 6, 59), TRADE_DAYS) == datetime.date(2026, 10, 12)  # CST 14:59

    def test_1500_maps_next_trade_day(self):
        assert assign_trade_day(_utc(2026, 10, 12, 7, 0), TRADE_DAYS) == datetime.date(2026, 10, 13)  # CST 15:00

    def test_friday_1500_maps_next_monday(self):
        assert assign_trade_day(_utc(2026, 10, 9, 7, 0), TRADE_DAYS) == datetime.date(2026, 10, 12)

    def test_weekend_maps_next_trade_day(self):
        # 周六 10:00（CST）→ 周一
        assert assign_trade_day(_utc(2026, 10, 10, 2, 0), TRADE_DAYS) == datetime.date(2026, 10, 12)

    def test_cst_midnight_from_prev_day_utc(self):
        """跨时区：UTC 前日 16:30 naive = CST 当日 00:30 → 归属 CST 当日。"""
        assert assign_trade_day(_utc(2026, 10, 11, 16, 30), TRADE_DAYS) == datetime.date(2026, 10, 12)

    def test_beyond_calendar_returns_none(self):
        """日历不可得（10-16 起不在窗口内）→ None（丢弃不猜测）。"""
        assert assign_trade_day(_utc(2026, 10, 16, 2, 0), TRADE_DAYS) is None

    def test_none_input_returns_none(self):
        assert assign_trade_day(None, TRADE_DAYS) is None

    def test_empty_calendar_returns_none(self):
        assert assign_trade_day(_utc(2026, 10, 12, 2, 0), []) is None


# ---------------------------------------------------------------------------
# aggregate_daily（聚合 / 衰减 / 输出序列）
# ---------------------------------------------------------------------------


class TestAggregateDailyBasics:
    def test_weighted_mean_with_source_weights(self):
        """巨潮(1.5) +1 vs 快讯(0.8) -1（conf 均 1）→ S = 0.7/2.3。

        as_of 取次日（10-13）：10-12 为历史桶不衰减，纯验证加权公式
        （盘中衰减语义由 TestAggregateDailyDecay 单独覆盖）。
        """
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), 1.0, 1.0, "announcement"),
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 1.0, "telegraph"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 13, 10, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index == pytest.approx(0.7 / 2.3)
        assert target.sample_count == 2
        assert target.sample_insufficient is True  # n=2 < 3（正/负覆盖见 sample_insufficient 专项用例）

    def test_all_zero_confidence_division_guard(self):
        """除零：conf 全 0 → 分母 0 → index=None（不做除零）。"""
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), 1.0, 0.0, "news"),
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 0.0, "news"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index is None
        assert target.sample_count == 2

    def test_no_news_day_is_none_not_zero(self):
        """R21：无新闻交易日 index=None（不画成 0），n=0。"""
        points = aggregate_daily([], TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index is None
        assert target.sample_count == 0
        assert target.sample_insufficient is False

    def test_sample_insufficient_flag_below_three(self):
        # as_of 取次日：10-12 为历史桶不衰减，纯验证加权公式
        rows = [_row(_cst(2026, 10, 12, 9, 0), 0.5, 0.8, "news"), _row(_cst(2026, 10, 12, 10, 0), 0.3, 0.9, "news")]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 13, 10, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 2
        assert target.sample_insufficient is True
        assert target.index == pytest.approx((0.5 * 0.8 + 0.3 * 0.9) / (0.8 + 0.9))

    def test_three_samples_not_insufficient(self):
        rows = [_row(_cst(2026, 10, 12, h, 0), 0.5, 0.8, "news") for h in (9, 10, 11)]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_insufficient is False

    def test_missing_score_row_not_counted(self):
        """R21：score/conf 缺失（None）行不计入聚合与条数。"""
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), None, 0.8, "news"),
            _row(_cst(2026, 10, 12, 10, 0), 0.5, None, "news"),
            _row(_cst(2026, 10, 12, 11, 0), 0.4, 0.7, "news"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 1
        assert target.index == pytest.approx(0.4)
        assert target.sample_insufficient is True

    def test_unknown_source_kind_uses_default_weight(self):
        # as_of 取次日：10-12 为历史桶不衰减，纯验证等权反向 → 0
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), 1.0, 1.0, "unknown_kind"),
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 1.0, "unknown_kind"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 13, 10, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index == pytest.approx(0.0)  # 等权反向 → 0

    def test_unassignable_rows_dropped(self):
        """日历不可得的行丢弃（不计入任何桶）。"""
        rows = [_row(_cst(2026, 10, 16, 9, 0), 1.0, 1.0, "news")]  # 10-16 起不在日历
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        assert all(p.sample_count == 0 for p in points)

    def test_empty_calendar_returns_empty(self):
        assert aggregate_daily([], [], as_of=_cst(2026, 10, 12, 14, 0)) == []


class TestAggregateDailyDecay:
    def test_half_life_decay_weights(self):
        """Δt=4h 权重减半：+1(新鲜) 与 -1(4h 前) → S = (1 - 0.5)/1.5 = 1/3。"""
        rows = [
            _row(_cst(2026, 10, 12, 14, 0), 1.0, 1.0, "news"),  # Δt=0
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 1.0, "news"),  # Δt=4h → 0.5
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index == pytest.approx(1.0 / 3.0)

    def test_no_decay_without_as_of(self):
        """as_of=None（历史模式）→ 不衰减：等权反向 → 0（若衰减则为 ≈0.0864）。"""
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), 1.0, 1.0, "news"),
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 1.0, "news"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=None, days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index == pytest.approx(0.0)

    def test_future_publish_dropped(self):
        """Δt<0（发布时刻晚于 as_of，时钟偏移）→ 剔除。"""
        rows = [_row(_cst(2026, 10, 12, 15, 30), 1.0, 1.0, "news")]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 10, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 0
        assert target.index is None

    def test_historical_bucket_no_decay(self):
        """历史桶不衰减：as_of=10-13，10-12 桶内等权反向两行 → 0（不随查询时刻漂移）。"""
        rows = [
            _row(_cst(2026, 10, 12, 9, 0), 1.0, 1.0, "news"),
            _row(_cst(2026, 10, 12, 10, 0), -1.0, 1.0, "news"),
        ]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 13, 10, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.index == pytest.approx(0.0)


class TestAggregateDailyOutput:
    def test_history_window_and_fill(self):
        """days=2 → 输出最后 2 个 ≤ as_of 日的交易日（无数据日填 None）。"""
        points = aggregate_daily([], TRADE_DAYS, as_of=_cst(2026, 10, 13, 10, 0), days=2)
        assert [p.trade_date for p in points] == [datetime.date(2026, 10, 12), datetime.date(2026, 10, 13)]
        assert all(p.index is None for p in points)

    def test_future_bucket_appended(self):
        """收盘后新闻归属次一交易日：as_of=10-13 16:00 → 10-14 尾部桶有数据。"""
        rows = [_row(_cst(2026, 10, 13, 16, 0), 0.8, 0.9, "news")]  # ≥15:00 → 10-14
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 13, 16, 0), days=7)
        assert points[-1].trade_date == datetime.date(2026, 10, 14)
        assert points[-1].sample_count == 1
        assert points[-1].index == pytest.approx(0.8)

    def test_post_close_news_not_in_publish_day_bucket(self):
        """R24：15:00 后发布的新闻不计入发布当日桶。"""
        rows = [_row(_cst(2026, 10, 12, 16, 0), 0.8, 0.9, "news")]  # → 10-13
        points = aggregate_daily(rows, TRADE_DAYS, as_of=_cst(2026, 10, 12, 16, 0), days=7)
        day12 = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert day12.sample_count == 0
        future = [p for p in points if p.trade_date == datetime.date(2026, 10, 13)]
        assert len(future) == 1 and future[0].sample_count == 1

    def test_naive_as_of_interpreted_as_cst(self):
        rows = [_row(_cst(2026, 10, 12, 14, 0), 1.0, 1.0, "news")]
        points = aggregate_daily(rows, TRADE_DAYS, as_of=datetime.datetime(2026, 10, 12, 14, 0), days=7)
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 1  # naive as_of 按 CST 解释 → 无未来剔除


# ---------------------------------------------------------------------------
# NewsSentimentIndexService（mock DAO / 日历）
# ---------------------------------------------------------------------------


def _mk_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _db_row(publish_cst: datetime.datetime, score: float | None, conf: float | None, **extra) -> dict:
    row = {
        "publish_time": to_utc_for_db(publish_cst),
        "sentiment_score": score,
        "sentiment_conf": conf,
        "source_kind": "news",
        "title": None,
        "content": "正文",
    }
    row.update(extra)
    return row


@pytest.fixture
def service():
    cache = MagicMock()
    calendar = MagicMock()
    calendar.get_trade_dates = AsyncMock(return_value=list(TRADE_DAYS))
    svc = NewsSentimentIndexService(cache=cache, trade_calendar=calendar)
    return svc, cache.market_dao, calendar


class TestNewsSentimentIndexService:
    @pytest.mark.asyncio
    async def test_stock_index_direct_and_telegraph_merge(self, service):
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(
            return_value=_mk_df([_db_row(_cst(2026, 10, 12, 9, 0), 1.0, 1.0, source_kind="announcement")])
        )
        mdao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_mk_df(
                [
                    _db_row(
                        _cst(2026, 10, 12, 10, 0), -1.0, 1.0, source_kind="telegraph", content="贵州茅台获大额回购"
                    ),
                ]
            )
        )
        points = await svc.get_stock_daily_index("600519.SH", "贵州茅台", days=7, as_of=_cst(2026, 10, 13, 10, 0))
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 2
        # 巨潮 1.5 vs 快讯 0.8（conf 均 1）
        assert target.index == pytest.approx((1.5 - 0.8) / 2.3)
        mdao.get_scored_stock_news.assert_awaited_once()
        mdao.get_telegraph_news_for_stocks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_market_prefix_excluded_from_stock_bucket(self, service):
        """【市场】前缀快讯即使命中个股名也不进个股桶（互斥）。"""
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_mk_df([_db_row(_cst(2026, 10, 12, 10, 0), 0.5, 1.0, content="【市场】贵州茅台领涨大盘")])
        )
        points = await svc.get_stock_daily_index("600519.SH", "贵州茅台", days=7, as_of=_cst(2026, 10, 13, 10, 0))
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 0
        assert target.index is None

    @pytest.mark.asyncio
    async def test_unmatched_telegraph_excluded(self, service):
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_mk_df([_db_row(_cst(2026, 10, 12, 10, 0), 0.5, 1.0, content="某 unrelated 内容")])
        )
        points = await svc.get_stock_daily_index("600519.SH", "贵州茅台", days=7, as_of=_cst(2026, 10, 13, 10, 0))
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 0

    @pytest.mark.asyncio
    async def test_market_index_only_market_prefix(self, service):
        """市场级：只聚合【市场】前缀快讯。"""
        svc, mdao, _ = service
        mdao.get_telegraph_news_in_window = AsyncMock(
            return_value=_mk_df(
                [
                    _db_row(_cst(2026, 10, 12, 10, 0), 0.6, 1.0, content="【市场】央行降准 0.5pct"),
                    _db_row(_cst(2026, 10, 12, 11, 0), 0.5, 1.0, content="个股利好新闻"),
                ]
            )
        )
        points = await svc.get_market_daily_index(days=7, as_of=_cst(2026, 10, 13, 10, 0))
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 1
        assert target.index == pytest.approx(0.6)
        mdao.get_telegraph_news_in_window.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calendar_unavailable_returns_empty(self, service):
        svc, mdao, calendar = service
        calendar.get_trade_dates = AsyncMock(return_value=[])
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_in_window = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(return_value=_mk_df([]))
        assert await svc.get_stock_daily_index("600519.SH", None, days=7, as_of=_cst(2026, 10, 13, 10, 0)) == []
        assert await svc.get_market_daily_index(days=7, as_of=_cst(2026, 10, 13, 10, 0)) == []
        mdao.get_scored_stock_news.assert_not_called()
        mdao.get_telegraph_news_in_window.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_args_return_empty(self, service):
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_in_window = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(return_value=_mk_df([]))
        assert await svc.get_stock_daily_index("", None, days=7) == []
        assert await svc.get_stock_daily_index("600519.SH", None, days=0) == []
        assert await svc.get_market_daily_index(days=0) == []
        mdao.get_scored_stock_news.assert_not_called()
        mdao.get_telegraph_news_in_window.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_sentiment_columns_treated_missing(self, service):
        """DB 三打分列为 NULL（未打分历史）→ 行不计入（R21）。"""
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(
            return_value=_mk_df([_db_row(_cst(2026, 10, 12, 9, 0), None, None, source_kind="announcement")])
        )
        mdao.get_telegraph_news_for_stocks = AsyncMock(return_value=_mk_df([]))
        points = await svc.get_stock_daily_index("600519.SH", None, days=7, as_of=_cst(2026, 10, 13, 10, 0))
        target = next(p for p in points if p.trade_date == datetime.date(2026, 10, 12))
        assert target.sample_count == 0
        assert target.index is None


# ---------------------------------------------------------------------------
# get_stock_day_news（N5-3：点击情绪柱展开当日新闻明细）
# ---------------------------------------------------------------------------


class TestGetStockDayNews:
    @pytest.mark.asyncio
    async def test_day_window_and_assignment_filter(self, service):
        """归属过滤：仅保留归属 trade_date 的行（含前一交易日 15:00 后），倒序返回。

        - 10-9 15:30（CST）→ 归 10-12 ✓；
        - 10-9 15:00 整（≥15:00 归次一交易日 = 10-12）✓；
        - 10-9 14:59 → 归 10-9 ✗（窗口外，即便 DAO 返回也被归属防线剔除）；
        - 10-12 盘中/14:59 → 10-12 ✓；
        - 10-12 15:00 整 → 归 10-13 ✗。
        """
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(
            return_value=_mk_df(
                [
                    _db_row(_cst(2026, 10, 9, 15, 30), 0.8, 0.9),
                    _db_row(_cst(2026, 10, 9, 15, 0), 0.6, 0.9),
                    _db_row(_cst(2026, 10, 9, 14, 59), 0.1, 0.9),
                    _db_row(_cst(2026, 10, 12, 9, 0), 0.5, 0.8),
                    _db_row(_cst(2026, 10, 12, 14, 59), 0.3, 0.7),
                    _db_row(_cst(2026, 10, 12, 15, 0), 0.9, 0.9),
                ]
            )
        )
        mdao.get_telegraph_news_for_stocks = AsyncMock(return_value=_mk_df([]))
        rows = await svc.get_stock_day_news("600519.SH", "贵州茅台", datetime.date(2026, 10, 12))
        assert [r["sentiment_score"] for r in rows] == [0.3, 0.5, 0.8, 0.6]  # publish_time 倒序
        # 取数窗口：[前一交易日 15:00, trade_date 15:00)（CST → UTC naive）
        kwargs = mdao.get_scored_stock_news.await_args.kwargs
        assert kwargs["start_time"] == to_utc_for_db(_cst(2026, 10, 9, 15, 0))
        assert kwargs["end_time"] == to_utc_for_db(_cst(2026, 10, 12, 15, 0))

    @pytest.mark.asyncio
    async def test_unscored_rows_kept_for_detail(self, service):
        """明细保留未打分行（R21：展示「-」，不在此层丢弃/填值）。"""
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([_db_row(_cst(2026, 10, 12, 9, 0), None, None)]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(return_value=_mk_df([]))
        rows = await svc.get_stock_day_news("600519.SH", None, datetime.date(2026, 10, 12))
        assert len(rows) == 1
        assert rows[0]["sentiment_score"] is None
        assert rows[0]["sentiment_conf"] is None

    @pytest.mark.asyncio
    async def test_market_prefix_excluded(self, service):
        """【市场】前缀快讯即使命中个股名也不进个股日明细（与个股桶互斥）。"""
        svc, mdao, _ = service
        mdao.get_scored_stock_news = AsyncMock(return_value=_mk_df([]))
        mdao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_mk_df([_db_row(_cst(2026, 10, 12, 10, 0), 0.5, 1.0, content="【市场】贵州茅台领涨")])
        )
        assert await svc.get_stock_day_news("600519.SH", "贵州茅台", datetime.date(2026, 10, 12)) == []

    @pytest.mark.asyncio
    async def test_invalid_args_return_empty(self, service):
        svc, mdao, _ = service
        assert await svc.get_stock_day_news("", None, datetime.date(2026, 10, 12)) == []
        assert await svc.get_stock_day_news("600519.SH", None, None) == []
        mdao.get_scored_stock_news.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_prev_trade_day_returns_empty(self, service):
        """trade_date 为日历首个交易日（无前一交易日）→ 空明细。"""
        svc, mdao, calendar = service
        calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2026, 10, 12)])
        assert await svc.get_stock_day_news("600519.SH", None, datetime.date(2026, 10, 12)) == []
        mdao.get_scored_stock_news.assert_not_called()

    @pytest.mark.asyncio
    async def test_calendar_unavailable_returns_empty(self, service):
        svc, mdao, calendar = service
        calendar.get_trade_dates = AsyncMock(return_value=[])
        assert await svc.get_stock_day_news("600519.SH", None, datetime.date(2026, 10, 12)) == []
        mdao.get_scored_stock_news.assert_not_called()


# ---------------------------------------------------------------------------
# DailySentimentPoint 契约
# ---------------------------------------------------------------------------


def test_daily_point_is_frozen():
    point = DailySentimentPoint(
        trade_date=datetime.date(2026, 10, 12), index=None, sample_count=0, sample_insufficient=False
    )
    with pytest.raises(dataclasses.FrozenInstanceError) as exc_info:
        point.index = 0.5  # type: ignore[misc]  # [reason: 故意验证 frozen 契约]
    assert "index" in str(exc_info.value)  # 弱断言修复：验证异常指向被改字段


def test_model_tag_none_when_not_loaded():
    """scorer.model_tag：未加载/未配置 → None（R21 缺失不伪装；截断逻辑见
    test_news_subscription_service 的载荷用例）。"""
    from services.news_sentiment_scorer import NewsSentimentScorer

    assert NewsSentimentScorer().model_tag is None
