"""data/domain_services/offline_calendar.py 单元测试"""

import datetime
from unittest.mock import MagicMock, patch


class TestOfflineCalendarGetInstance:
    def test_get_instance_success(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        with patch("data.domain_services.offline_calendar.get_calendar") as mock_get_cal:
            mock_cal = MagicMock()
            mock_get_cal.return_value = mock_cal

            result = OfflineCalendar.get_instance()
            assert result is mock_cal
            mock_get_cal.assert_called_once_with("SSE")

    def test_get_instance_failure_returns_none(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        with patch("data.domain_services.offline_calendar.get_calendar", side_effect=Exception("load error")):
            result = OfflineCalendar.get_instance()
            assert result is None

    def test_get_instance_caches_calendar(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result1 = OfflineCalendar.get_instance()
            result2 = OfflineCalendar.get_instance()

        assert result1 is result2
        assert result1 is mock_cal


class TestOfflineCalendarIsTradingDay:
    def test_is_trading_day_with_string_date(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day("2024-01-02")
            assert result is True

    def test_is_trading_day_with_date_object(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day(datetime.date(2024, 1, 2))
            assert result is True

    def test_is_trading_day_with_datetime_object(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day(datetime.datetime(2024, 1, 2))
            assert result is True

    def test_is_trading_day_with_invalid_type(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.side_effect = Exception("invalid date type")

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day(12345)
            assert result is False

    def test_is_trading_day_no_calendar(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        with patch("data.domain_services.offline_calendar.get_calendar", side_effect=Exception("no calendar")):
            result = OfflineCalendar.is_trading_day("2024-01-02")
            assert result is False

    def test_is_trading_day_non_trading_day(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=True)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day("2024-01-01")
            assert result is False


class TestOfflineCalendarGetTradeDates:
    def test_get_trade_dates_success(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        import pandas as pd

        mock_cal = MagicMock()
        mock_cal.valid_days.return_value = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.get_trade_dates("2024-01-01", "2024-01-05")
            assert result == ["20240102", "20240103"]

    def test_get_trade_dates_no_calendar(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        with patch("data.domain_services.offline_calendar.get_calendar", side_effect=Exception("no calendar")):
            result = OfflineCalendar.get_trade_dates("2024-01-01", "2024-01-05")
            assert result == []

    def test_get_trade_dates_exception(self):
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.valid_days.side_effect = Exception("query error")

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.get_trade_dates("2024-01-01", "2024-01-05")
            assert result == []
