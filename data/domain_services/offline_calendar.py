import datetime
import logging
import typing

import pandas as pd
from pandas_market_calendars import get_calendar

from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


class OfflineCalendar:
    """
    Offline Calendar Wrapper using pandas_market_calendars (exchange_calendars).

    单例豁免说明（P3-21）：本类通过类属性 ``_calendar`` + ``get_instance`` 构成事实单例，
    但未纳入 ``@register_singleton``：
    ① 缓存对象为 ``pandas_market_calendars`` 的不可变日历（SSE），无状态变更方法；
    ② ``_calendar`` 仅在首次 ``get_instance`` 时赋值，后续只读访问，无并发写竞态；
    ③ 测试间残留同一日历实例无业务行为差异（日历是全局真理而非测试可变状态），
       无 R7 测试状态污染实际危害；
    ④ 不持有外部资源（连接池/线程池/文件句柄），无需 ``_atexit_cleanup``。
    若未来缓存语义变为可变状态或引入外部资源，需重新评估纳入 ``register_singleton``。
    """

    _calendar = None

    @classmethod
    def get_instance(cls):
        # P3-21: 单例豁免——返回不可变 SSE 日历缓存，无状态变更，详见类 docstring。
        if cls._calendar is None:
            try:
                # SSE includes holidays for Shanghai Stock Exchange (A-Share)
                cls._calendar = get_calendar("SSE")
            except Exception as e:
                error_info = classify_error(e, context="general")
                severity = classify_severity(e)
                if severity == "system":
                    _log = logger.critical
                elif severity == "recoverable":
                    _log = logger.warning
                else:
                    _log = logger.error
                _log(
                    "[OfflineCalendar] Failed to load SSE calendar (%s): %s",
                    error_info["code"],
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
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
                logger.error(
                    "[OfflineCalendar] Calendar unavailable, treating %s as non-trading day (conservative fallback)",
                    date_obj,
                )
                return False

            if isinstance(date_obj, (str, datetime.date, datetime.datetime)):
                ts = pd.Timestamp(date_obj)
            else:
                ts = date_obj

            schedule = cal.schedule(start_date=ts, end_date=ts)
            return not schedule.empty

        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e)
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[OfflineCalendar] is_trading_day check failed for %s (%s): %s",
                date_obj,
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
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
            error_info = classify_error(e, context="general")
            severity = classify_severity(e)
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[OfflineCalendar] Range check failed (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
            return []
