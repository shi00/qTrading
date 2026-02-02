from pandas_market_calendars import get_calendar
import pandas as pd
import datetime
import logging

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
                cls._calendar = get_calendar('SSE')
            except Exception as e:
                logger.error(f"[OfflineCalendar] Failed to load SSE calendar: {e}")
                return None
        return cls._calendar

    @staticmethod
    def is_trading_day(date_obj):
        """
        Check if a date is a trading day.
        """
        try:
            cal = OfflineCalendar.get_instance()
            if cal is None:
                # Fallback if library fails
                if isinstance(date_obj, str):
                    date_obj = datetime.datetime.strptime(date_obj, '%Y%m%d')
                if hasattr(date_obj, 'weekday'):
                    return date_obj.weekday() < 5
                return True

            # Convert to Timestamp
            if isinstance(date_obj, str):
                ts = pd.Timestamp(date_obj)
            elif isinstance(date_obj, (datetime.date, datetime.datetime)):
                ts = pd.Timestamp(date_obj)
            else:
                ts = date_obj # Hope it's compatible

            # Check schedule
            # Efficient way: valid_days(start, end)
            schedule = cal.schedule(start_date=ts, end_date=ts)
            return not schedule.empty
            
        except Exception as e:
            logger.warning(f"[OfflineCalendar] Check failed: {e}")
            # Fallback
            return True

    @staticmethod
    def get_trade_dates(start_date, end_date):
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
            return [d.strftime('%Y%m%d') for d in valid]
            
        except Exception as e:
            logger.error(f"[OfflineCalendar] Range check failed: {e}")
            return []
