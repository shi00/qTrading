"""
TradeCalendarService — 统一交易日历服务。

提供单一入口的交易日历操作，整合 Database、Tushare API 和 Offline Calendar 三种数据源。

设计原则:
1. 单一入口: 所有交易日历相关操作统一通过此类
2. 优雅降级: 数据库不可用时自动切换到离线模式
3. 智能缓存: 热点数据内存缓存，减少数据库压力
4. 自动补齐: 数据缺失时自动从 Tushare 拉取并入库
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING

import pandas as pd

from data.constants import MARKET_CLOSE_HOUR
from data.domain_services.offline_calendar import OfflineCalendar
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import get_loop_local
from utils.time_utils import get_now, parse_date

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.external.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class TradeCalendarService:
    """
    统一交易日历服务。

    数据源优先级: Database > Tushare API > Offline Calendar

    使用示例:
        >>> from data.cache.cache_manager import CacheManager
        >>> from data.external.tushare_client import TushareClient
        >>> cache = CacheManager()
        >>> api = TushareClient()
        >>> calendar = TradeCalendarService(cache, api)
        >>>
        >>> # 判断交易日
        >>> is_trade = await calendar.is_trading_day("2024-03-21")
        >>>
        >>> # 获取最近交易日
        >>> latest = await calendar.get_latest_trade_date()
        >>>
        >>> # 根据交易日计算起始日期
        >>> start = await calendar.get_start_date_by_trade_days(latest, 120)
    """

    def __init__(self, cache_manager: CacheManager, tushare_client: TushareClient):
        """
        初始化交易日历服务。

        Args:
            cache_manager: 缓存管理器 (提供数据库访问)
            tushare_client: Tushare 客户端 (提供 API 访问)

        Note:
            初始化仅进行依赖注入，不执行任何 I/O 操作。
            符合 Python 同步 __init__ 的约束。
        """
        self._cache = cache_manager
        self._api = tushare_client
        self._offline = OfflineCalendar

        self._mem_cache: dict = {}
        self._cache_ttl: int = 300

        self._latest_trade_date_cache: dict = {"ts": 0, "val": None}

    def _to_date(self, d) -> datetime.date | None:
        """
        统一日期类型转换。

        服务入口统一转换，内部流转全部使用 datetime.date。

        Args:
            d: 日期 (date/datetime/str/None)

        Returns:
            date 或 None
        """
        if d is None:
            return None
        if isinstance(d, datetime.date) and not isinstance(d, datetime.datetime):
            return d
        if isinstance(d, datetime.datetime):
            return d.date()
        if isinstance(d, str):
            return parse_date(d.replace("-", "")).date()
        raise ValueError(f"无法将 {type(d)} 转换为 date")

    def _to_str(self, d) -> str | None:
        """将日期转换为 YYYYMMDD 字符串格式。"""
        date_obj = self._to_date(d)
        if date_obj is None:
            return None
        return date_obj.strftime("%Y%m%d")

    async def _ensure_data_persisted(self, df: pd.DataFrame) -> bool:
        """
        确保数据持久化到数据库。

        Args:
            df: 从 API 获取的交易日历数据

        Returns:
            是否成功持久化
        """
        if df is None or df.empty:
            return False

        try:
            await self._cache.save_trade_cal(df)
            logger.debug("[TradeCalendarService] Persisted %s calendar records to DB", len(df))
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[TradeCalendarService] Failed to persist calendar data: %s", e)
            return False

    async def _fetch_from_api_and_persist(self, start_date, end_date) -> pd.DataFrame | None:
        """
        从 Tushare API 获取数据并持久化。

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame 或 None
        """
        import os

        if os.environ.get("E2E_TESTING") == "true":
            logger.debug(
                "[TradeCalendarService] E2E mode: skipping Tushare API fetch for %s - %s",
                start_date,
                end_date,
            )
            return None

        try:
            df = await self._api.get_trade_cal(start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                await self._ensure_data_persisted(df)
            return df
        except Exception as e:
            logger.warning(
                "[TradeCalendarService] API fetch failed for %s - %s: %s",
                start_date,
                end_date,
                e,
            )
            return None

    async def ensure_calendar_range(self, start_date, end_date) -> bool:
        """
        确保 trade_cal 表覆盖 [start_date, end_date] 的完整日历。

        此方法用于初始化阶段的批量同步，不同于按需懒加载。
        - 检查 DB 已有覆盖范围
        - 对缺失范围调 API 批量拉取（完整日历，含非交易日）
        - 幂等设计：重复调用不会重复写入（upsert）

        Args:
            start_date: 需要覆盖的起始日期
            end_date: 需要覆盖的截止日期

        Returns:
            bool: 是否成功确保覆盖
        """
        start_obj = self._to_date(start_date)
        end_obj = self._to_date(end_date)
        if start_obj is None or end_obj is None:
            return False

        try:
            # 1. 检查 DB 已有覆盖范围
            existing_range = await self._cache.get_trade_cal_range()
            db_min, db_max = existing_range

            if db_min and db_max:
                db_min = self._to_date(db_min)
                db_max = self._to_date(db_max)
                if db_min is not None and db_max is not None:
                    if db_min <= start_obj and db_max >= end_obj:
                        # 简单的范围检查：如果边界覆盖了就认为完整
                        # 更精确的方式是 count 日期数，但作为初始化快速路径这已足够
                        logger.debug(
                            "[TradeCalendarService] Calendar range already covers %s to %s (DB: %s to %s)",
                            start_obj,
                            end_obj,
                            db_min,
                            db_max,
                        )
                        return True

            # 2. 调 API 批量拉取完整日历（不带 is_open 过滤）
            df = await self._api.get_trade_cal(start_date=start_obj, end_date=end_obj)  # type: ignore[arg-type]
            if df is not None and not df.empty:
                await self._ensure_data_persisted(df)
                logger.info(
                    "[TradeCalendarService] Bulk synced %s calendar records (%s to %s)",
                    len(df),
                    start_obj,
                    end_obj,
                )
                return True

            logger.warning(
                "[TradeCalendarService] API returned empty for %s to %s",
                start_obj,
                end_obj,
            )
            return False

        except Exception as e:
            logger.error(
                "[TradeCalendarService] ensure_calendar_range failed: %s",
                e,
                exc_info=True,
            )
            return False

    async def is_trading_day(self, date) -> bool:
        """
        判断是否为交易日。

        Args:
            date: 日期 (date/datetime/str)

        Returns:
            bool: 是否为交易日

        示例:
            >>> await service.is_trading_day("2024-03-21")
            True
            >>> await service.is_trading_day("2024-03-23")  # 周六
            False
        """
        date_obj = self._to_date(date)
        if date_obj is None:
            return False

        try:
            df = await self._cache.get_trade_cal(start_date=date_obj, end_date=date_obj, is_open="1")
            if df is not None and not df.empty:
                return True

            if df is not None and len(df) == 0:
                df = await self._cache.get_trade_cal(start_date=date_obj, end_date=date_obj)
                if df is not None and not df.empty:
                    return df["is_open"].iloc[0] == 1

            df = await self._fetch_from_api_and_persist(date_obj, date_obj)
            if df is not None and not df.empty:
                row = df[df["cal_date"] == self._to_str(date_obj)]
                if not row.empty:
                    return row["is_open"].iloc[0] == 1  # type: ignore[index]

            return self._offline.is_trading_day(date_obj)

        except Exception as e:
            logger.warning(
                "[TradeCalendarService] is_trading_day check failed, using offline: %s",
                e,
            )
            return self._offline.is_trading_day(date_obj)

    @log_async_operation(
        operation_name="get_trade_dates",
        log_exceptions=True,
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def get_trade_dates(self, start_date, end_date) -> list[datetime.date]:
        """
        获取日期范围内的所有交易日。

        数据源优先级: Database -> Tushare API -> Offline

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List[date]: 交易日列表 (升序)

        示例:
            >>> await service.get_trade_dates("2024-03-18", "2024-03-22")
            [date(2024, 3, 18), date(2024, 3, 19), date(2024, 3, 20),
             date(2024, 3, 21), date(2024, 3, 22)]
        """
        start_obj = self._to_date(start_date)
        end_obj = self._to_date(end_date)

        if start_obj is None or end_obj is None:
            return []

        if start_obj > end_obj:
            return []

        try:
            df = await self._cache.get_trade_cal(start_date=start_obj, end_date=end_obj, is_open="1")
            if df is not None and not df.empty:
                # 数据完整性快速校验：日期跨度 vs 记录数
                # 3年约730个交易日，如果记录数远低于预期跨度的交易日密度，说明数据不完整
                span_days = (end_obj - start_obj).days
                expected_min = max(1, int(span_days * 0.6))  # 保守估计：60% 是交易日
                import os

                is_e2e = os.environ.get("E2E_TESTING") == "true"
                if not is_e2e and span_days > 30 and len(df) < expected_min * 0.5:
                    # 数据明显不完整，回退到 API 补充
                    logger.warning(
                        "[TradeCalendarService] DB data incomplete: %s records "
                        "for %s day span (expected >= %s), "
                        "falling back to API",
                        len(df),
                        span_days,
                        expected_min,
                    )
                else:
                    dates = pd.to_datetime(df["cal_date"]).dt.date.tolist()
                    return sorted(dates)

            df = await self._fetch_from_api_and_persist(start_obj, end_obj)
            if df is not None and not df.empty:
                trade_df = df[df["is_open"] == 1] if "is_open" in df.columns else df
                if not trade_df.empty:
                    dates = pd.to_datetime(trade_df["cal_date"]).dt.date.tolist()  # type: ignore[union-attr]
                    return sorted(dates)

            offline_dates = self._offline.get_trade_dates(start_obj, end_obj)  # type: ignore[arg-type]
            if offline_dates:
                return [parse_date(d).date() for d in offline_dates]

            return []

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[TradeCalendarService] get_trade_dates failed: %s",
                e,
                exc_info=True,
            )
            offline_dates = self._offline.get_trade_dates(start_obj, end_obj)  # type: ignore[arg-type]
            if offline_dates:
                return [parse_date(d).date() for d in offline_dates]
            return []

    async def count_trade_days(self, start_date, end_date) -> int:
        """
        计算日期范围内的交易日数量。

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            int: 交易日数量

        示例:
            >>> await service.count_trade_days("2024-03-18", "2024-03-22")
            5
        """
        start_obj = self._to_date(start_date)
        end_obj = self._to_date(end_date)

        if start_obj is None or end_obj is None:
            return 0

        try:
            return await self._cache.stock_dao.count_trade_days(start_obj, end_obj)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[TradeCalendarService] count_trade_days failed, using list: %s", e)
            dates = await self.get_trade_dates(start_obj, end_obj)
            return len(dates)

    async def get_start_date_by_trade_days(self, end_date, trade_days: int) -> datetime.date | None:
        """
        根据交易日数量计算起始日期。

        Args:
            end_date: 结束日期
            trade_days: 交易日数量

        Returns:
            date: 起始日期 (N 个交易日前的日期)

        示例:
            >>> await service.get_start_date_by_trade_days("2024-03-21", 120)
            date(2023, 9, 15)  # 120个交易日前
        """
        end_obj = self._to_date(end_date)
        if end_obj is None or trade_days <= 0:
            return None

        try:
            result = await self._cache.get_start_date_by_trade_days(end_obj, trade_days)
            if result is not None:
                return result

            rough_start = end_obj - datetime.timedelta(days=int(trade_days * 1.5) + 30)
            dates = await self.get_trade_dates(rough_start, end_obj)

            if len(dates) >= trade_days:
                return dates[-trade_days]

            return rough_start

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[TradeCalendarService] get_start_date_by_trade_days failed: %s", e)
            rough_start = end_obj - datetime.timedelta(days=int(trade_days * 1.5) + 30)
            return rough_start

    async def get_prev_trade_date(self, date) -> datetime.date | None:
        """
        获取指定日期的上一个交易日。

        Args:
            date: 参考日期

        Returns:
            date: 上一个交易日

        示例:
            >>> await service.get_prev_trade_date("2024-03-21")
            date(2024, 3, 20)
            >>> await service.get_prev_trade_date("2024-03-18")  # 周一
            date(2024, 3, 15)  # 上周五
        """
        date_obj = self._to_date(date)
        if date_obj is None:
            return None

        lookback_start = date_obj - datetime.timedelta(days=10)
        dates = await self.get_trade_dates(lookback_start, date_obj)

        if not dates:
            return None

        dates_before = [d for d in dates if d < date_obj]
        if dates_before:
            return dates_before[-1]

        offline_dates = self._offline.get_trade_dates(lookback_start, date_obj)  # type: ignore[arg-type]
        if offline_dates:
            offline_before = [parse_date(d).date() for d in offline_dates if parse_date(d).date() < date_obj]
            if offline_before:
                return offline_before[-1]

        return None

    async def get_next_trade_date(self, date) -> datetime.date | None:
        """
        获取指定日期的下一个交易日。

        Args:
            date: 参考日期

        Returns:
            date: 下一个交易日

        示例:
            >>> await service.get_next_trade_date("2024-03-21")
            date(2024, 3, 22)
            >>> await service.get_next_trade_date("2024-03-22")  # 周五
            date(2024, 3, 25)  # 下周一
        """
        date_obj = self._to_date(date)
        if date_obj is None:
            return None

        lookforward_end = date_obj + datetime.timedelta(days=10)
        dates = await self.get_trade_dates(date_obj, lookforward_end)

        if not dates:
            return None

        dates_after = [d for d in dates if d > date_obj]
        if dates_after:
            return dates_after[0]

        offline_dates = self._offline.get_trade_dates(date_obj, lookforward_end)  # type: ignore[arg-type]
        if offline_dates:
            offline_after = [parse_date(d).date() for d in offline_dates if parse_date(d).date() > date_obj]
            if offline_after:
                return offline_after[0]

        return None

    @log_async_operation(
        operation_name="get_latest_trade_date",
        log_exceptions=True,
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def get_latest_trade_date(self, *, allow_fallback: bool = False) -> datetime.date | None:
        """
        获取最近的交易日。

        规则:
        - 当前时间 < 15:00 → 返回上一个交易日
        - 当前时间 >= 15:00 → 返回今天 (如果是交易日) 或上一个交易日

        Args:
            allow_fallback: If True, falls back to weekday heuristic when
                calendar data is unavailable (may be incorrect on Chinese holidays).
                If False (default), returns None instead of guessing,
                which prevents incorrect dates on Chinese holidays.

        Returns:
            date: 最近交易日, or None if allow_fallback=False and no calendar data

        示例:
            # 假设今天是 2024-03-21 (周四) 14:00
            >>> await service.get_latest_trade_date()
            date(2024, 3, 20)  # 昨天的数据已完整

            # 假设今天是 2024-03-21 (周四) 16:00
            >>> await service.get_latest_trade_date()
            date(2024, 3, 21)  # 今天的数据已完整
        """
        now_ts = time.time()
        if (
            self._latest_trade_date_cache["val"] is not None
            and now_ts - self._latest_trade_date_cache["ts"] < self._cache_ttl
        ):
            return self._latest_trade_date_cache["val"]

        # Use loop-local Lock to avoid cross-loop reuse issues
        def _lock_factory():
            return asyncio.Lock()

        async with get_loop_local("trade_calendar_cache_lock", _lock_factory):
            now_ts = time.time()
            if (
                self._latest_trade_date_cache["val"] is not None
                and now_ts - self._latest_trade_date_cache["ts"] < self._cache_ttl
            ):
                return self._latest_trade_date_cache["val"]

            now = get_now()
            if now.hour < MARKET_CLOSE_HOUR:
                end_dt = (now - datetime.timedelta(days=1)).date()
            else:
                end_dt = now.date()

            start_dt = end_dt - datetime.timedelta(days=20)

            try:
                dates = await self.get_trade_dates(start_dt, end_dt)
                if dates:
                    result = dates[-1]
                    self._latest_trade_date_cache = {"ts": now_ts, "val": result}
                    return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[TradeCalendarService] get_latest_trade_date failed: %s", e)

            logger.error(
                "[TradeCalendarService] get_latest_trade_date: no trade calendar data available.%s",
                " Trying OfflineCalendar as last resort."
                if allow_fallback
                else " allow_fallback=False, returning None.",
            )
            if not allow_fallback:
                return None

            dt = end_dt
            for _ in range(20):
                if self._offline.is_trading_day(dt):
                    return dt
                dt -= datetime.timedelta(days=1)

            logger.error(
                "[TradeCalendarService] get_latest_trade_date: OfflineCalendar found no trading day in 20-day window. Returning None.",
            )
            return None

    async def get_trade_dates_batch(self, ranges: list[tuple[datetime.date, datetime.date]]) -> dict:
        """
        批量获取多个日期范围的交易日。

        优化: 合并为单次数据库查询，减少 IO 次数。

        Args:
            ranges: 日期范围列表 [(start1, end1), (start2, end2), ...]

        Returns:
            Dict: {范围元组: 交易日列表}

        示例:
            >>> ranges = [(date(2024, 3, 1), date(2024, 3, 5)),
            ...           (date(2024, 3, 10), date(2024, 3, 15))]
            >>> await service.get_trade_dates_batch(ranges)
            {(date(2024, 3, 1), date(2024, 3, 5)): [...],
             (date(2024, 3, 10), date(2024, 3, 15)): [...]}
        """
        result = {}

        if not ranges:
            return result

        all_dates = set()
        for start, end in ranges:
            all_dates.add(start)
            all_dates.add(end)

        min_date = min(all_dates)
        max_date = max(all_dates)

        all_trade_dates = await self.get_trade_dates(min_date, max_date)

        for start, end in ranges:
            range_dates = [d for d in all_trade_dates if start <= d <= end]
            result[(start, end)] = range_dates

        return result

    def clear_cache(self):
        """清除内存缓存。"""
        self._mem_cache.clear()
        self._latest_trade_date_cache = {"ts": 0, "val": None}
        logger.debug("[TradeCalendarService] Memory cache cleared")

    async def get_trade_cal_df(self, start_date=None, end_date=None, is_open=None) -> pd.DataFrame:
        """
        获取原始交易日历 DataFrame。

        用于数据连续性检查等需要原始数据的场景。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            is_open: 是否交易日 (1/0/None)

        Returns:
            DataFrame: 包含 cal_date, is_open, exchange 列

        示例:
            >>> df = await service.get_trade_cal_df(
            ...     datetime.date(2024, 3, 1), datetime.date(2024, 3, 31), is_open="1"
            ... )
        """
        start_obj = self._to_date(start_date)
        end_obj = self._to_date(end_date)

        try:
            df = await self._cache.get_trade_cal(start_date=start_obj, end_date=end_obj, is_open=is_open)
            if df is not None and not df.empty:
                return df

            df = await self._fetch_from_api_and_persist(start_obj, end_obj)
            if df is not None and not df.empty:
                if is_open is not None and "is_open" in df.columns:
                    df = df[df["is_open"] == int(is_open)]
                return df  # type: ignore[return-value]

            return pd.DataFrame()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[TradeCalendarService] get_trade_cal_df failed: %s",
                e,
                exc_info=True,
            )
            return pd.DataFrame()
