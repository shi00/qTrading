import datetime
import logging
import typing

import pandas as pd
from pandas_market_calendars import get_calendar

from utils.time_utils import parse_date

logger = logging.getLogger(__name__)


class OfflineCalendar:
    """
    Offline Calendar Wrapper using pandas_market_calendars (exchange_calendars).
    """

    _calendar = None

    @classmethod
    def get_instance(cls):
        if cls._calendar is None:
            try:
                # SSE includes holidays for Shanghai Stock Exchange (A-Share)
                cls._calendar = get_calendar("SSE")
            except Exception as e:
                logger.error(f"[OfflineCalendar] Failed to load SSE calendar: {e}")
                return None
        return cls._calendar

    @staticmethod
    def is_trading_day(date_obj: typing.Any):
        """
        Check if a date is a trading day.
        """
        try:
            cal = OfflineCalendar.get_instance()
            if cal is None:
                if isinstance(date_obj, str):
                    date_obj = parse_date(date_obj)
                if hasattr(date_obj, "weekday"):
                    is_weekday = date_obj.weekday() < 5
                    if is_weekday:
                        logger.warning(
                            f"[OfflineCalendar] Calendar unavailable, treating {date_obj} as trading day (weekday fallback)",
                        )
                    return is_weekday
                logger.error(f"[OfflineCalendar] Cannot determine date type for {date_obj}, assuming non-trading day")
                return False

            if isinstance(date_obj, (str, datetime.date, datetime.datetime)):
                ts = pd.Timestamp(date_obj)
            else:
                ts = date_obj

            schedule = cal.schedule(start_date=ts, end_date=ts)
            return not schedule.empty

        except Exception as e:
            logger.error(f"[OfflineCalendar] is_trading_day check failed for {date_obj}: {e}")
            return False

    @staticmethod
    def get_trade_dates(start_date: str | None, end_date: str | None):
        """
        Get list of trading dates between start and end (inclusive).
        Returns list of strings in YYYYMMDD format.
        """
        try:
            cal = OfflineCalendar.get_instance()
            if cal is None:
                return []

            # Convert to Timestamps
            # valid_days returns a DatetimeIndex
            valid = cal.valid_days(start_date=start_date, end_date=end_date)

            # Format to list of strings
            return [d.strftime("%Y%m%d") for d in valid]

        except Exception as e:
            logger.error(f"[OfflineCalendar] Range check failed: {e}")
            return []
