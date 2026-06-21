import datetime
from unittest.mock import patch, MagicMock

from data.domain_services.offline_calendar import OfflineCalendar
import pytest


pytestmark = pytest.mark.unit


class TestOfflineCalendarIsTradingDayFallback:
    def test_exception_returns_false(self):
        with patch.object(
            OfflineCalendar,
            "get_instance",
            side_effect=Exception("Calendar init failed"),
        ):
            result = OfflineCalendar.is_trading_day("2024-06-15")
            assert result is False

    def test_calendar_none_weekday_returns_false(self):
        with patch.object(OfflineCalendar, "get_instance", return_value=None):
            result = OfflineCalendar.is_trading_day("2024-06-14")
            assert result is False

    def test_calendar_none_weekend_returns_false(self):
        with patch.object(OfflineCalendar, "get_instance", return_value=None):
            result = OfflineCalendar.is_trading_day("2024-06-15")
            assert result is False

    def test_calendar_none_date_obj_weekday(self):
        with patch.object(OfflineCalendar, "get_instance", return_value=None):
            result = OfflineCalendar.is_trading_day(datetime.date(2024, 6, 14))
            assert result is False

    def test_calendar_none_date_obj_weekend(self):
        with patch.object(OfflineCalendar, "get_instance", return_value=None):
            result = OfflineCalendar.is_trading_day(datetime.date(2024, 6, 15))
            assert result is False

    def test_calendar_none_unparseable_returns_false(self):
        with patch.object(OfflineCalendar, "get_instance", return_value=None):
            result = OfflineCalendar.is_trading_day(12345)
            assert result is False

    def test_schedule_exception_returns_false(self):
        mock_cal = MagicMock()
        mock_cal.schedule.side_effect = Exception("Schedule error")
        with patch.object(OfflineCalendar, "get_instance", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day("2024-06-14")
            assert result is False

    def test_schedule_empty_returns_false(self):
        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=True)
        with patch.object(OfflineCalendar, "get_instance", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day("2024-06-14")
            assert result is False

    def test_schedule_non_empty_returns_true(self):
        import pandas as pd

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = pd.DataFrame({"market_open": [pd.Timestamp("2024-06-14")]})
        with patch.object(OfflineCalendar, "get_instance", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day("2024-06-14")
            assert result is True
