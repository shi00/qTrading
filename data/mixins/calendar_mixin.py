"""
CalendarMixin — Extracted from DataProcessor (P2-M1).

Provides trading calendar management: latest trade date lookup,
trade date range queries, and calendar sync with Tushare API.

Expected host class attributes: cache (CacheManager), api (TushareClient)
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING

from data.constants import MARKET_CLOSE_HOUR
from utils.log_decorators import log_async_operation, PerfThreshold
from utils.time_utils import get_now

if TYPE_CHECKING:
    from data.cache_manager import CacheManager
    from data.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class CalendarMixin:
    """
    Mixin providing trading calendar management.

    Expects the host class to provide:
        self.cache: CacheManager
        self.api: TushareClient
        self._trade_cal_cache: dict
    """

    # Type hints for IDE support (resolved at runtime via DataProcessor)
    cache: "CacheManager"
    api: "TushareClient"
    _trade_cal_cache: dict

    # CR-02: Use manual TTL cache (5 min) instead of infinite alru_cache
    # @alru_cache(maxsize=1) 
    @log_async_operation(operation_name="get_latest_trade_date", log_exceptions=True, threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_latest_trade_date(self):
        """Get absolute latest trading date (today or previous trading day)."""
        # Initialize cache if missing (guard for edge-case hot paths)
        if not hasattr(self, '_trade_date_cache'):
            self._trade_date_cache = {'ts': 0, 'val': None}

        now_ts = time.time()
        if self._trade_date_cache['val'] and (now_ts - self._trade_date_cache['ts'] < 300):
            return self._trade_date_cache['val']

        now = get_now()
        if now.hour < MARKET_CLOSE_HOUR:
            end_dt = now - datetime.timedelta(days=1)
        else:
            end_dt = now

        end_str = end_dt.strftime('%Y%m%d')
        start_str = (end_dt - datetime.timedelta(days=20)).strftime('%Y%m%d')

        try:
            dates = await self.get_trade_dates(start_str, end_str)
            if dates:
                result = dates[-1]
                # Update cache
                self._trade_date_cache = {'ts': now_ts, 'val': result}
                return result
        except Exception as e:
            logger.warning(f"[DataProcessor] Calendar | ⚠️ Failed to fetch latest trade date fallback: {e}")

        # Fallback
        dt = end_dt
        while dt.weekday() >= 5:
            dt -= datetime.timedelta(days=1)
        fallback_res = dt.strftime('%Y%m%d')
        return fallback_res

    @log_async_operation(operation_name="get_trade_dates", log_exceptions=True, threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_trade_dates(self, start_date, end_date):
        """Get list of trade dates between start and end."""
        try:
            await self.ensure_trade_cal(end_date, required_start_date=start_date)
            # Use strict type check for safety with generic read
            cache_df = await self.cache.get_trade_cal(start_date=start_date, end_date=end_date, is_open=1)

            if not cache_df.empty:
                # Polars or Pandas? CacheManager returns DataFrame (Pandas)
                # Ensure it's list of strings
                return sorted(cache_df['cal_date'].astype(str).tolist())
        except Exception as e:
            logger.error(f"[DataProcessor] Calendar | ❌ Primary calendar sync rejected: {e}", exc_info=True)

        # Fallback (Simple logic)
        dates = []
        current = datetime.datetime.strptime(start_date, '%Y%m%d')
        end = datetime.datetime.strptime(end_date, '%Y%m%d')
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime('%Y%m%d'))
            current += datetime.timedelta(days=1)
        return dates

    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        Includes memory caching to avoid frequent DB/API checks (and log spam).
        """
        # Optimized path for frequent checks (e.g. Home Screen polling)
        # Only cache if using default start date (required_start_date is None)
        if required_start_date is None and self._trade_cal_cache.get('date') == end_date:
            return True

        success = await self._ensure_trade_cal_impl(end_date, required_start_date)

        if success and required_start_date is None:
            self._trade_cal_cache = {'date': end_date}

        return success

    @log_async_operation(operation_name="ensure_trade_cal_impl", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _ensure_trade_cal_impl(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        """
        try:
            min_db, max_db = await self.cache.get_trade_cal_range()

            curr_year = int(end_date[:4])
            # Default start to 4 years ago if not specified
            target_start = required_start_date if required_start_date else datetime.date(curr_year - 4, 1, 1).strftime(
                '%Y%m%d')

            async def fetch_and_save(s, e):
                y = int(e[:4])
                real_end = datetime.date(y, 12, 31).strftime('%Y%m%d')
                if e < real_end: e = real_end

                pass # 动作已由装饰器覆盖
                df = await self.api.get_trade_cal(start_date=s, end_date=e)
                if df is not None and not df.empty:
                    await self.cache.save_trade_cal(df)
                    return True
                return False

            if not min_db or not max_db:
                return await fetch_and_save(target_start, end_date)
            else:
                # Check coverage and fetch missing parts
                tasks = []
                if target_start < min_db:
                    gap = (datetime.datetime.strptime(min_db, '%Y%m%d') - datetime.datetime.strptime(target_start,
                                                                                                     '%Y%m%d')).days
                    if gap > 10:
                        tasks.append(fetch_and_save(target_start, min_db))

                if max_db < end_date:
                    tasks.append(fetch_and_save(max_db, end_date))

                if tasks:
                    results = await asyncio.gather(*tasks)
                    return all(results)

            return True  # Already covered

        except Exception as e:
            logger.error(f"[DataProcessor] Calendar | ❌ Deep engine failure on ensure_trade_cal limit break: {e}", exc_info=True)
            return False
