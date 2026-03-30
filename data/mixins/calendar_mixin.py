"""
CalendarMixin — Facade Proxy for TradeCalendarService.

This mixin now delegates all calendar operations to TradeCalendarService.
It exists for backward compatibility and will emit deprecation warnings.

Expected host class attributes: trade_calendar (TradeCalendarService)
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.domain_services.trade_calendar_service import TradeCalendarService

logger = logging.getLogger(__name__)


class CalendarMixin:
    """
    Facade proxy for TradeCalendarService.

    This mixin provides backward compatibility by delegating to trade_calendar.
    All methods emit DeprecationWarning to encourage direct usage.

    Expects the host class to provide:
        self.trade_calendar: TradeCalendarService
    """

    trade_calendar: TradeCalendarService

    async def get_latest_trade_date(self):
        """
        Get absolute latest trading date (today or previous trading day).

        .. deprecated::
            Use `await dp.trade_calendar.get_latest_trade_date()` instead.
        """
        warnings.warn(
            "Use dp.trade_calendar.get_latest_trade_date() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.trade_calendar.get_latest_trade_date()

    async def get_trade_dates(self, start_date, end_date):
        """
        Get list of trade dates between start and end (inclusive).

        .. deprecated::
            Use `await dp.trade_calendar.get_trade_dates()` instead.
        """
        warnings.warn(
            "Use dp.trade_calendar.get_trade_dates() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.trade_calendar.get_trade_dates(start_date, end_date)

    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].

        .. deprecated::
            This method is no longer needed. TradeCalendarService handles
            data synchronization automatically.
        """
        warnings.warn(
            "ensure_trade_cal is deprecated. TradeCalendarService handles sync automatically.",
            DeprecationWarning,
            stacklevel=2,
        )
        return True
