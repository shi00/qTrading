"""data/domain_services/offline_calendar.py 单元测试"""

import datetime
from unittest.mock import MagicMock, patch
import pytest


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_offline_calendar_cache():
    """每个测试结束后重置 OfflineCalendar 的类级日历缓存（R7 状态隔离）。

    get_instance() 会把 patched 的 mock 写入类属性 _calendar；with patch() 退出仅还原
    get_calendar，不还原 _calendar。若 mock 泄漏到同进程后续测试，其未配置的 valid_days
    会在 get_trade_dates 中被 except 吞掉并返回空，导致依赖离线兜底的测试（如回测
    test_empty_db_falls_back_to_offline）间歇性失败。故在 teardown 阶段统一清空。
    """
    yield
    from data.domain_services.offline_calendar import OfflineCalendar

    OfflineCalendar._calendar = None


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

        with patch(
            "data.domain_services.offline_calendar.get_calendar",
            side_effect=Exception("load error"),
        ):
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

        with patch(
            "data.domain_services.offline_calendar.get_calendar",
            side_effect=Exception("no calendar"),
        ):
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

    def test_is_trading_day_beyond_trusted_interval_returns_none(self):
        """超出可信区间返回 None（未知），绝不猜测（D2-7）。"""
        from data.domain_services.offline_calendar import OfflineCalendar, _OFFLINE_TRUSTED_UNTIL

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.is_trading_day(_OFFLINE_TRUSTED_UNTIL + datetime.timedelta(days=1))
            assert result is None
            mock_cal.schedule.assert_not_called()

    def test_is_trading_day_at_trusted_boundary_still_evaluates(self):
        """恰好等于可信区间上界的日期仍是可信区间内，进入规则判定（D2-7 边界语义）。"""
        from data.domain_services.offline_calendar import OfflineCalendar, _OFFLINE_TRUSTED_UNTIL

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        mock_cal.schedule.return_value = MagicMock(empty=False)

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            assert OfflineCalendar.is_trading_day(_OFFLINE_TRUSTED_UNTIL) is True

    def test_trusted_until_is_in_future(self):
        """可信区间守卫：若常量过期（today > 边界），说明维护遗漏，调度器将持续跳过交易日（D2-7 失效）。"""
        from data.domain_services.offline_calendar import _OFFLINE_TRUSTED_UNTIL

        assert datetime.date.today() < _OFFLINE_TRUSTED_UNTIL


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

        with patch(
            "data.domain_services.offline_calendar.get_calendar",
            side_effect=Exception("no calendar"),
        ):
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

    def test_get_trade_dates_accepts_date_objects(self):
        """M13-F2: get_trade_dates 接受 date 对象（TradeCalendarService 内部传 date，
        此前调用点依赖 # type: ignore 压制；签名放宽后 date 为一等合法输入）。"""
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        import pandas as pd

        mock_cal = MagicMock()
        mock_cal.valid_days.return_value = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])

        with patch("data.domain_services.offline_calendar.get_calendar", return_value=mock_cal):
            result = OfflineCalendar.get_trade_dates(datetime.date(2024, 1, 1), datetime.date(2024, 1, 5))
            assert result == ["20240102", "20240103"]

    def test_get_trade_dates_start_beyond_trusted_returns_empty(self):
        """D2-7: start 超出可信区间 → 返回空，绝不外推猜测（与 is_trading_day 三态语义对齐）。"""
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        mock_cal = MagicMock()
        with (
            patch(
                "data.domain_services.offline_calendar.get_calendar",
                return_value=mock_cal,
            ),
            patch(
                "data.domain_services.offline_calendar._OFFLINE_TRUSTED_UNTIL",
                datetime.date(2024, 6, 30),
            ),
        ):
            result = OfflineCalendar.get_trade_dates(datetime.date(2024, 7, 1), datetime.date(2024, 7, 10))

        assert result == []
        mock_cal.valid_days.assert_not_called()

    def test_get_trade_dates_end_beyond_trusted_truncated(self):
        """D2-7: end 超出可信区间 → 截断到可信区间上界，离线库不预测未来调休/节假日。"""
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        import pandas as pd

        mock_cal = MagicMock()
        mock_cal.valid_days.return_value = pd.DatetimeIndex(["2024-06-28"])

        with (
            patch(
                "data.domain_services.offline_calendar.get_calendar",
                return_value=mock_cal,
            ),
            patch(
                "data.domain_services.offline_calendar._OFFLINE_TRUSTED_UNTIL",
                datetime.date(2024, 6, 30),
            ),
        ):
            result = OfflineCalendar.get_trade_dates(datetime.date(2024, 6, 1), datetime.date(2024, 7, 15))

        assert result == ["20240628"]
        _, kwargs = mock_cal.valid_days.call_args
        assert pd.Timestamp(kwargs["end_date"]).date() == datetime.date(2024, 6, 30)

    def test_get_trade_dates_end_none_tightened_to_trusted(self):
        """D2-7: end 缺省 → 收紧到可信区间上界，不外推到日历终点。"""
        from data.domain_services.offline_calendar import OfflineCalendar

        OfflineCalendar._calendar = None

        import pandas as pd

        mock_cal = MagicMock()
        mock_cal.valid_days.return_value = pd.DatetimeIndex([])

        with (
            patch(
                "data.domain_services.offline_calendar.get_calendar",
                return_value=mock_cal,
            ),
            patch(
                "data.domain_services.offline_calendar._OFFLINE_TRUSTED_UNTIL",
                datetime.date(2024, 6, 30),
            ),
        ):
            OfflineCalendar.get_trade_dates(datetime.date(2024, 6, 1), None)

        _, kwargs = mock_cal.valid_days.call_args
        assert pd.Timestamp(kwargs["end_date"]).date() == datetime.date(2024, 6, 30)
