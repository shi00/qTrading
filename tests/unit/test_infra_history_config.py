import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from data.domain_services.trade_calendar_service import TradeCalendarService
from ui.i18n import I18n
from utils.config_handler import ConfigHandler

pytestmark = pytest.mark.unit

_MIN_HISTORY_YEARS = 1
_MAX_HISTORY_YEARS = 5
_LEAP_DAY = datetime.date(2024, 2, 29)


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
