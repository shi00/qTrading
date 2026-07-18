"""
CalendarMixin — Facade Proxy for TradeCalendarService.

This mixin now delegates all calendar operations to TradeCalendarService.
Only ``ensure_trade_cal`` is retained as a facade; other calendar operations
should be invoked directly on ``self.trade_calendar``.

Expected host class attributes: trade_calendar (TradeCalendarService)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.domain_services.trade_calendar_service import TradeCalendarService

logger = logging.getLogger(__name__)


class CalendarMixin:
    """
    Facade proxy for TradeCalendarService.

    Expects the host class to provide:
        self.trade_calendar: TradeCalendarService
    """

    trade_calendar: TradeCalendarService

    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        """
        start = required_start_date if required_start_date else end_date
        return await self.trade_calendar.ensure_calendar_range(start, end_date)
