import datetime
from unittest.mock import patch, MagicMock

from data.domain_services.offline_calendar import OfflineCalendar, _OFFLINE_TRUSTED_UNTIL
from utils.time_utils import get_now
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


class TestOfflineCalendarTrustedUntil:
    def test_trusted_until_within_six_months(self):
        # review08-C2：可信区间常量由人工每年维护（国务院新年放假通知发布后前移至次年 12-31）。
        # 距今不足 6 个月（183 天近似半年，略保守）即失败告警，防止忘记更新
        # 导致 is_trading_day 对超出区间日期静默返回 None（全链路降级）。
        # 测试日期须与生产同源（CST），避免非东八区 CI 机器误判（review08-C2）
        today = get_now().date()
        assert today + datetime.timedelta(days=183) <= _OFFLINE_TRUSTED_UNTIL, (
            "可信区间不足 6 个月：请更新 _OFFLINE_TRUSTED_UNTIL 至次年 12-31，并核对相关守卫生效时间"
        )
