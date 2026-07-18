import pytest
from unittest.mock import AsyncMock, MagicMock

from data.mixins.calendar_mixin import CalendarMixin

pytestmark = pytest.mark.unit


class Host(CalendarMixin):
    def __init__(self):
        self.trade_calendar = MagicMock()


class TestCalendarMixin:
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
