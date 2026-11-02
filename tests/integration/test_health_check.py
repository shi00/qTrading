# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 本文件以 MagicMock 替身注入 HealthCheckMixin 宿主属性（cache / trade_calendar / api），
# pyright 无法验证替身与生产类型的兼容性；测试行为由断言验证。

"""
Tests for health check and data sync interactions.

P1-5: Health cache invalidated after sync.
P1-6: quality_scan includes delisted stocks (list_status D).
P1-7: API trade_cal used as gold standard for lag calculation.

回归契约（TP02-F04）：以上场景必须调用生产实现（``HealthCheckMixin.run_quality_scan`` /
``HealthCheckMixin.check_data_health``）并经外部输入断言输出，测试内不得复制等价算法。
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.mixins.health_mixin import HealthCheckMixin, _compute_tier


def _deep_health_payload() -> dict:
    """``check_comprehensive_health`` 的最小健康快照（覆盖全部 critical 表）。"""
    return {
        "global_trade_days": 250,
        "tables": {
            "daily_quotes": {"ratio": 1.0, "type": "stock"},
            "daily_indicators": {"ratio": 1.0, "type": "stock"},
            "financial_reports": {"ratio": 0.95, "type": "stock"},
            "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
            "stock_basic": {"ratio": 1.0, "type": "global"},
        },
    }


class _StubHealthHost(HealthCheckMixin):
    """HealthCheckMixin 宿主替身：注入 cache / trade_calendar / api，算法仍走生产实现。"""

    def __init__(self, cache, trade_calendar, api=None):
        self.cache = cache
        self.trade_calendar = trade_calendar
        self.api = api
        self._health_cache = {"time": 0, "data": None}
        self._quality_tier = None
        self._quality_tier_by_table = {}
        self._scan_missing_dates = frozenset()

    def is_cancelled(self) -> bool:
        return False

    def clear_cancel(self) -> None:
        return None

    def _get_cancel_event(self) -> asyncio.Event:
        return asyncio.Event()


@pytest.fixture(autouse=True)
def _reset_write_quality():
    """R7：WriteQuality 为进程级单例，隔离用例间可能残留的 coerce 降级状态。"""
    from data.persistence.write_quality import WriteQuality

    WriteQuality._reset_singleton()
    yield
    WriteQuality._reset_singleton()


class TestHealthCacheInvalidation:
    """P1-5: Health cache must be cleared after sync operations"""

    @pytest.fixture(autouse=True)
    def _reset_dp(self):
        from data.data_processor import DataProcessor

        DataProcessor._instance = None
        DataProcessor._initialized = False
        yield
        DataProcessor._instance = None
        DataProcessor._initialized = False

    @pytest.mark.asyncio
    async def test_sync_historical_clears_health_cache(self):
        from data.data_processor import DataProcessor

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["historical"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=10)
                await processor.sync_historical_data()

            assert processor._health_cache == {"time": 0, "data": None}

    @pytest.mark.asyncio
    async def test_sync_financial_clears_health_cache(self):
        from data.data_processor import DataProcessor

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["financial"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=5)
                await processor.sync_financial_reports()

            assert processor._health_cache == {"time": 0, "data": None}

    @pytest.mark.asyncio
    async def test_sync_comprehensive_clears_health_cache(self):
        from data.data_processor import DataProcessor

        with (
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_sync_max_concurrent_heavy.return_value = 5
            processor = DataProcessor()
            processor._health_cache = {"time": 100, "data": {"status": "green"}}

            with patch.object(processor.strategies["financial"], "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = MagicMock(added=5)
                await processor.sync_comprehensive_fundamentals()

            assert processor._health_cache == {"time": 0, "data": None}


def _make_quality_scan_host(basics_df: pd.DataFrame) -> _StubHealthHost:
    """构造只替换数据源的 run_quality_scan 宿主，采样/过滤算法由生产代码执行。"""
    quote_dao = MagicMock()
    quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    quote_dao.get_latest_trade_date = AsyncMock(return_value=None)

    stock_dao = MagicMock()
    stock_dao.get_stock_basic = AsyncMock(return_value=basics_df)

    sync_dao = MagicMock()
    sync_dao.get_sync_status = AsyncMock(return_value=pd.DataFrame())

    cache = MagicMock()
    cache.quote_dao = quote_dao
    cache.stock_dao = stock_dao
    cache.sync_dao = sync_dao
    cache.check_comprehensive_health = AsyncMock(return_value=_deep_health_payload())
    cache.get_field_completeness = AsyncMock(return_value=None)

    trade_calendar = MagicMock()
    trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 1, 3))
    trade_calendar.get_trade_cal_df = AsyncMock(return_value=pd.DataFrame())

    return _StubHealthHost(cache=cache, trade_calendar=trade_calendar)


async def _sampled_ts_codes(host: _StubHealthHost, sample_size: int) -> list[str]:
    """执行生产 run_quality_scan，返回其实际请求日线的采样股票列表。"""
    await host.run_quality_scan(sample_size=sample_size)
    call = host.cache.quote_dao.get_daily_quotes.await_args
    assert call is not None, "生产实现未调用 quote_dao.get_daily_quotes，采样路径未执行"
    return call.kwargs["ts_code_list"]


class TestQualityScanDelisted:
    """P1-6: quality_scan should include delisted stocks (list_status D)"""

    @pytest.mark.asyncio
    async def test_scan_filter_includes_delisted_status(self):
        basics_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                "list_status": ["L", "D", "L", "P"],
            }
        )
        host = _make_quality_scan_host(basics_df)
        sample = await _sampled_ts_codes(host, sample_size=3)

        assert set(sample) == {"000001.SZ", "000002.SZ", "000003.SZ"}
        assert "000004.SZ" not in sample

    @pytest.mark.asyncio
    async def test_delisted_stocks_counted_in_sample(self):
        basics_df = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, 101)] + ["900001.SZ"],
                "list_status": ["L"] * 100 + ["D"],
            }
        )
        host = _make_quality_scan_host(basics_df)
        sample = await _sampled_ts_codes(host, sample_size=101)

        assert "900001.SZ" in sample
        assert len(sample) == 101


def _make_health_check_host(
    *,
    official_dates: list[datetime.date],
    cached_trade_dates: set[datetime.date],
    api_cal_df: pd.DataFrame,
) -> _StubHealthHost:
    """构造只替换数据源的 check_data_health 宿主，滞后计算由生产代码执行。"""
    quote_dao = MagicMock()
    quote_dao.get_cached_trade_dates = AsyncMock(return_value=set(cached_trade_dates))
    quote_dao.get_latest_trade_date = AsyncMock(return_value=official_dates[-1])
    quote_dao.get_index_daily_coverage_summary = AsyncMock(return_value=pd.DataFrame())
    for check_name in (
        "count_orphan_ts_codes",
        "count_price_range_violations",
        "count_moneyflow_net_mismatch",
        "count_adj_factor_monotonic_violations",
    ):
        setattr(quote_dao, check_name, AsyncMock(return_value=0))

    financial_dao = MagicMock()
    financial_dao.has_ann_date_nulls = AsyncMock(return_value=False)

    stock_dao = MagicMock()
    stock_dao.get_stock_basic_health_summary = AsyncMock(return_value=pd.DataFrame())
    stock_dao.get_latest_open_cal_date = AsyncMock(
        return_value=official_dates[-1] + datetime.timedelta(days=60),
    )

    sync_dao = MagicMock()
    sync_dao.get_sync_status = AsyncMock(return_value=pd.DataFrame())

    name_history_dao = MagicMock()
    name_history_dao.get_name_history_coverage_summary = AsyncMock(
        return_value={"total_rows": 5, "st_rows": 1},
    )

    cache = MagicMock()
    cache.quote_dao = quote_dao
    cache.financial_dao = financial_dao
    cache.stock_dao = stock_dao
    cache.sync_dao = sync_dao
    cache.stock_name_history_dao = name_history_dao
    cache.check_comprehensive_health = AsyncMock(return_value=_deep_health_payload())
    cache.get_concept_count = AsyncMock(return_value=0)
    cache.get_field_completeness = AsyncMock(return_value=None)

    trade_calendar = MagicMock()
    trade_calendar.get_latest_trade_date = AsyncMock(return_value=official_dates[-1])
    trade_calendar.get_trade_dates = AsyncMock(side_effect=[[], list(official_dates)])

    api = MagicMock()
    api.trade_cal = AsyncMock(return_value=api_cal_df)

    return _StubHealthHost(cache=cache, trade_calendar=trade_calendar, api=api)


class TestLagDaysGoldStandard:
    """P1-7: API trade_cal used as gold standard for lag calculation"""

    @pytest.mark.asyncio
    async def test_api_extends_official_dates(self):
        official_dates = [
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 2),
            datetime.date(2024, 1, 3),
        ]
        host = _make_health_check_host(
            official_dates=official_dates,
            cached_trade_dates={datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)},
            api_cal_df=pd.DataFrame({"cal_date": ["20240105"]}),
        )

        with patch("data.mixins.health_mixin.ConfigHandler.get_init_history_years", return_value=1):
            result = await host.check_data_health()

        # 官方日历末 01-03，API 最新 01-05，本地行情末 01-02 → 缺 01-03、01-05 两个交易日
        assert result["market"]["lag_days"] == 2
        assert result["details"]["lag"] == 2
        assert result["market"]["latest_local"] == datetime.date(2024, 1, 2)

    @pytest.mark.asyncio
    async def test_no_api_falls_back_to_official(self):
        official_dates = [
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 2),
            datetime.date(2024, 1, 3),
        ]
        host = _make_health_check_host(
            official_dates=official_dates,
            cached_trade_dates={datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)},
            api_cal_df=pd.DataFrame(),
        )

        with patch("data.mixins.health_mixin.ConfigHandler.get_init_history_years", return_value=1):
            result = await host.check_data_health()

        # 无 API 时以官方日历为 gold standard → 仅缺 01-03 一个交易日
        assert result["market"]["lag_days"] == 1
        assert result["details"]["lag"] == 1

    def test_compute_tier_uses_lag_days(self):
        result_no_lag = _compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=False)
        result_with_lag = _compute_tier(lag_days=10, fin_fresh_ratio=0.9, missing_critical=False)
        assert result_no_lag >= result_with_lag


import pytest

pytestmark = pytest.mark.integration
