import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.data_processor import DataProcessor
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.sync.base import SyncResult
from ui.i18n import I18n
from utils.config_handler import ConfigHandler

pytestmark = pytest.mark.unit

_MIN_HISTORY_YEARS = 1
_MAX_HISTORY_YEARS = 5
_LEAP_DAY = datetime.date(2024, 2, 29)
_FIXED_NOW = datetime.datetime(2024, 6, 14)


@pytest.fixture(autouse=True)
def _restore_init_history_years():
    """Restore the persisted history-years preference to avoid cross-test pollution (R7)."""
    original = ConfigHandler.get_init_history_years()
    yield
    ConfigHandler.set_init_history_years(original)


def _make_trade_calendar_service(
    *,
    db_df: pd.DataFrame | None = None,
    db_error: Exception | None = None,
    offline_dates: list[str] | None = None,
) -> TradeCalendarService:
    """Build a TradeCalendarService with DB/offline data sources under test control."""
    cache = MagicMock()
    cache.stock_dao = MagicMock()
    if db_error is not None:
        cache.stock_dao.get_trade_cal = AsyncMock(side_effect=db_error)
    else:
        cache.stock_dao.get_trade_cal = AsyncMock(return_value=db_df)

    service = TradeCalendarService(cast(Any, cache), None)
    if offline_dates is not None:
        offline = MagicMock()
        offline.get_trade_dates = MagicMock(return_value=offline_dates)
        cast(Any, service)._offline = offline
    return service


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (-5, _MIN_HISTORY_YEARS),
        (0, _MIN_HISTORY_YEARS),
        (1, 1),
        (3, 3),
        (5, _MAX_HISTORY_YEARS),
        (6, _MAX_HISTORY_YEARS),
        (99, _MAX_HISTORY_YEARS),
    ],
)
def test_set_init_history_years_clamps_to_supported_bounds(requested: int, expected: int):
    assert ConfigHandler.set_init_history_years(requested) is True
    result = ConfigHandler.get_init_history_years()
    assert result == expected
    assert isinstance(result, int)
    assert _MIN_HISTORY_YEARS <= result <= _MAX_HISTORY_YEARS


def test_i18n_history_years_label_uses_selected_history_years():
    assert ConfigHandler.set_init_history_years(_MAX_HISTORY_YEARS) is True
    years = ConfigHandler.get_init_history_years()
    assert years == _MAX_HISTORY_YEARS
    assert I18n.get("wizard_sync_full").format(years=years) == "完整同步 (5年)"


@pytest.mark.asyncio
async def test_get_trade_dates_includes_leap_day_in_sorted_inclusive_range():
    db_df = pd.DataFrame({"cal_date": ["20240301", "20240228", "20240229"], "is_open": [1, 1, 1]})
    service = _make_trade_calendar_service(db_df=db_df)

    result = await service.get_trade_dates("20240228", "20240301")

    assert result == [datetime.date(2024, 2, 28), _LEAP_DAY, datetime.date(2024, 3, 1)]
    assert result == sorted(result)
    assert all(isinstance(d, datetime.date) and not isinstance(d, datetime.datetime) for d in result)


@pytest.mark.asyncio
async def test_get_trade_dates_single_leap_day_is_inclusive():
    db_df = pd.DataFrame({"cal_date": ["20240229"], "is_open": [1]})
    service = _make_trade_calendar_service(db_df=db_df)

    assert await service.get_trade_dates("20240229", "20240229") == [_LEAP_DAY]


@pytest.mark.asyncio
async def test_get_trade_dates_reverse_range_returns_empty_without_db_query():
    db_df = pd.DataFrame({"cal_date": ["20240229"], "is_open": [1]})
    service = _make_trade_calendar_service(db_df=db_df)

    assert await service.get_trade_dates("20240301", "20240228") == []
    cast(Any, service._cache).stock_dao.get_trade_cal.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_trade_dates_non_trading_weekend_returns_empty():
    service = _make_trade_calendar_service(db_df=pd.DataFrame(), offline_dates=[])

    assert await service.get_trade_dates("20240302", "20240303") == []


@pytest.mark.asyncio
async def test_get_trade_dates_none_start_returns_empty():
    service = _make_trade_calendar_service()

    assert await service.get_trade_dates(None, "20240229") == []


@pytest.mark.asyncio
async def test_get_trade_dates_invalid_start_raises_value_error():
    service = _make_trade_calendar_service()

    with pytest.raises(ValueError) as exc_info:
        await service.get_trade_dates(20240302, "20240303")

    assert "int" in str(exc_info.value)
    assert "date" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_trade_dates_db_failure_falls_back_to_offline_calendar():
    service = _make_trade_calendar_service(
        db_error=RuntimeError("db unavailable"),
        offline_dates=["20240229"],
    )

    assert await service.get_trade_dates("20240229", "20240229") == [_LEAP_DAY]


def _make_data_processor() -> DataProcessor:
    """构造 DataProcessor（外部依赖打桩），供 initialize_system 参数契约断言。"""
    DataProcessor._reset_singleton()
    with (
        patch("data.data_processor.CacheManager"),
        patch("data.data_processor.TushareClient"),
        patch("data.data_processor.TradeCalendarService"),
        patch("data.data_processor.ConfigHandler") as mock_config_handler,
    ):
        mock_config_handler.get_token.return_value = "test_token"
        dp = DataProcessor()

    dp_any = cast(Any, dp)
    dp_any.cache.init_db = AsyncMock()
    dp_any.cache.stock_dao.TS_CONCEPT_PREFIX = "TS_"
    dp_any.sync_stock_basic = AsyncMock(return_value=5)
    dp_any.sync_concepts = AsyncMock(return_value=0)
    dp_any.sync_historical_data = AsyncMock(return_value=SyncResult())
    dp_any.sync_comprehensive_fundamentals = AsyncMock(return_value=SyncResult())
    dp_any.check_data_health = AsyncMock(return_value={"tier": 3})
    dp_any.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
    for dimension_key in ("sw_industry", "name_change"):
        dp_any.strategies[dimension_key].run = AsyncMock(return_value=SyncResult())
    for ai_key in ("macro", "holder"):
        dp_any.strategies[ai_key].run = AsyncMock(return_value=MagicMock(status="completed"))
    dp_any.clear_cancel()
    return dp


@pytest.mark.parametrize(
    ("history_years", "expected_trade_days", "expected_calendar_start"),
    [
        # 生产默认 3 年（AppConfig.init_history_years=3）→ 750 交易日
        (3, 750, datetime.date(2021, 5, 16)),
        # 配置上限 5 年（app_prefs.set_init_history_years clamp 到 5）→ 1250 交易日
        (_MAX_HISTORY_YEARS, 1250, datetime.date(2019, 5, 17)),
    ],
)
@pytest.mark.asyncio
async def test_initialize_system_derives_history_and_calendar_span_from_config(
    history_years: int,
    expected_trade_days: int,
    expected_calendar_start: datetime.date,
    monkeypatch: pytest.MonkeyPatch,
):
    """initialize_system 契约（data/data_processor.py::DataProcessor.initialize_system）：

    - Step 3 历史同步交易日数 = 250 × 配置年数（5 年 → 1250，非硬编码常量）；
    - Step 2 交易日历起点 = 今天 - (365 × 配置年数 + 30) 天，终点为今天。
    断言使用固定时钟（get_now → 2024-06-14），避免依赖运行当天日期。
    """
    monkeypatch.setattr(ConfigHandler, "get_init_history_years", lambda: history_years)
    dp = _make_data_processor()

    with (
        patch("data.data_dictionary.validate_schema_definitions"),
        patch("data.data_processor.get_now", return_value=_FIXED_NOW),
    ):
        await dp.initialize_system()

    dp_any = cast(Any, dp)
    assert dp_any.sync_historical_data.await_args.kwargs["days"] == expected_trade_days
    calendar_call = dp_any.trade_calendar.ensure_calendar_range.await_args
    assert calendar_call.args == (expected_calendar_start, _FIXED_NOW.date())
