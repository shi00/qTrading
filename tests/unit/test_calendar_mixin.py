import pytest
import warnings
from unittest.mock import AsyncMock, MagicMock

from data.mixins.calendar_mixin import CalendarMixin


class Host(CalendarMixin):
    def __init__(self):
        self.trade_calendar = MagicMock()


class TestCalendarMixin:
    @pytest.mark.asyncio
    async def test_get_latest_trade_date_delegates(self):
        host = Host()
        host.trade_calendar.get_latest_trade_date = AsyncMock(return_value="20240614")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = await host.get_latest_trade_date()
            assert result == "20240614"
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        host.trade_calendar.get_latest_trade_date.assert_called_once_with(allow_fallback=False)

    @pytest.mark.asyncio
    async def test_get_latest_trade_date_with_fallback(self):
        host = Host()
        host.trade_calendar.get_latest_trade_date = AsyncMock(return_value="20240613")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = await host.get_latest_trade_date(allow_fallback=True)
            assert result == "20240613"
        host.trade_calendar.get_latest_trade_date.assert_called_once_with(allow_fallback=True)

    @pytest.mark.asyncio
    async def test_get_trade_dates_delegates(self):
        host = Host()
        host.trade_calendar.get_trade_dates = AsyncMock(return_value=["20240610", "20240611"])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = await host.get_trade_dates("20240610", "20240614")
            assert result == ["20240610", "20240611"]
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        host.trade_calendar.get_trade_dates.assert_called_once_with("20240610", "20240614")

    @pytest.mark.asyncio
    async def test_ensure_trade_cal_with_required_start(self):
        host = Host()
        host.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        await host.ensure_trade_cal("20240614", required_start_date="20240601")
        host.trade_calendar.ensure_calendar_range.assert_called_once_with("20240601", "20240614")

    @pytest.mark.asyncio
    async def test_ensure_trade_cal_without_required_start(self):
        host = Host()
        host.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        await host.ensure_trade_cal("20240614")
        host.trade_calendar.ensure_calendar_range.assert_called_once_with("20240614", "20240614")
