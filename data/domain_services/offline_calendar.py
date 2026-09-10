import datetime
import logging
import typing

import pandas as pd
from pandas_market_calendars import get_calendar

from utils.error_classifier import log_classified

logger = logging.getLogger(__name__)

# 中国 A 股法定节假日与调休由国务院每年公告，pandas_market_calendars 无法对未来完全预测。
# 离线日历仅对 <= 此日期的历史/近期交易日负责；超出返回 None（未知），由调用方显式决策，绝不猜测。
# 维护：每年国务院新年放假通知发布后，前移为次年 12-31（相关守卫生效时间同为该常量，勿只更新不核对）。
_OFFLINE_TRUSTED_UNTIL = datetime.date(2027, 12, 31)


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
                log_classified(
                    logger,
                    e,
                    "general",
                    "[OfflineCalendar] Failed to load SSE calendar (%s): %s",
                    exc_info=True,
                )
                return None
        return cls._calendar

    @staticmethod
    def is_trading_day(date_obj: typing.Any) -> bool | None:
        """离线判定交易日。返回三态（D2-7）：
        True  确定是交易日
        False 确定非交易日（历史节假日/周末/日历不可用时的保守判定）
        None  日期超出可信区间（> _OFFLINE_TRUSTED_UNTIL），无法确定——绝不猜测
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

            # 可信区间守卫：超出 _OFFLINE_TRUSTED_UNTIL 的日期，离线库对未来调休/节假日不预测，
            # 返回 None（未知）交由调用方显式决策，避免把规则外推当权威判定。
            try:
                check_date = ts.date() if isinstance(ts, pd.Timestamp) else ts
                if isinstance(check_date, datetime.date) and check_date > _OFFLINE_TRUSTED_UNTIL:
                    logger.info(
                        "[OfflineCalendar] Date %s beyond trusted interval (%s), returning None (unknown)",
                        date_obj,
                        _OFFLINE_TRUSTED_UNTIL,
                    )
                    return None
            except (AttributeError, TypeError):
                # 非日期输入不进入可信区间比较，交由下方 schedule 判定（异常由 except 兜底为 False）
                pass

            schedule = cal.schedule(start_date=ts, end_date=ts)
            return not schedule.empty

        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[OfflineCalendar] is_trading_day check failed for %s (%s): %s",
                date_obj,
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
            log_classified(
                logger,
                e,
                "general",
                "[OfflineCalendar] Range check failed (%s): %s",
                exc_info=True,
            )
            return []
