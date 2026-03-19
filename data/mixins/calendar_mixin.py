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

import pandas as pd

from data.constants import MARKET_CLOSE_HOUR
from utils.log_decorators import PerfThreshold, log_async_operation
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
    @log_async_operation(
        operation_name="get_latest_trade_date",
        log_exceptions=True,
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def get_latest_trade_date(self):
        """Get absolute latest trading date (today or previous trading day) as native date."""
        # Initialize cache if missing (guard for edge-case hot paths)
        if not hasattr(self, "_trade_date_cache"):
            self._trade_date_cache = {"ts": 0, "val": None}

        now_ts = time.time()
        if self._trade_date_cache["val"] and (
            now_ts - self._trade_date_cache["ts"] < 300
        ):
            return self._trade_date_cache["val"]

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
                # Update cache
                self._trade_date_cache = {"ts": now_ts, "val": result}
                return result
        except Exception as e:
            logger.warning(
                f"[DataProcessor] Calendar | ⚠️ Failed to fetch latest trade date fallback: {e}",
            )

        # Fallback
        dt = end_dt
        while dt.weekday() >= 5:
            dt -= datetime.timedelta(days=1)
        return dt

    @log_async_operation(
        operation_name="get_trade_dates",
        log_exceptions=True,
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def get_trade_dates(self, start_date, end_date):
        """Get list of trade dates between start and end (inclusive) as native date objects."""
        # Coerce strings securely if passed by accident
        def to_date(d):
            if isinstance(d, str):
                return datetime.datetime.strptime(d.replace("-", ""), "%Y%m%d").date()
            if isinstance(d, datetime.datetime):
                return d.date()
            return d
            
        start_date = to_date(start_date)
        end_date = to_date(end_date)
        
        try:
            await self.ensure_trade_cal(end_date, required_start_date=start_date)
            # Use strict type check for safety with generic read
            cache_df = await self.cache.get_trade_cal(
                start_date=start_date, end_date=end_date, is_open=1,
            )

            if not cache_df.empty:
                # CacheManager returns DataFrame (Pandas)
                # Convert explicitly to a list of native Python dates
                dates = pd.to_datetime(cache_df["cal_date"]).dt.date.tolist()
                return sorted(dates)
        except Exception as e:
            logger.error(
                f"[DataProcessor] Calendar | ❌ Primary calendar sync rejected: {e}",
                exc_info=True,
            )

        # Fallback (Simple logic)
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(current)
            current += datetime.timedelta(days=1)
        return dates

    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        Includes memory caching to avoid frequent DB/API checks (and log spam).
        """
        # Optimized path for frequent checks (e.g. Home Screen polling)
        # Only cache if using default start date (required_start_date is None)
        if (
            required_start_date is None
            and self._trade_cal_cache.get("date") == end_date
        ):
            return True

        success = await self._ensure_trade_cal_impl(end_date, required_start_date)

        if success and required_start_date is None:
            self._trade_cal_cache = {"date": end_date}

        return success

    @log_async_operation(
        operation_name="ensure_trade_cal_impl",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _ensure_trade_cal_impl(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        """
        try:
            # Helper to coerce to native date
            def to_date(d):
                if d is None:
                    return None
                if isinstance(d, str):
                    return datetime.datetime.strptime(d.replace("-", ""), "%Y%m%d").date()
                if isinstance(d, datetime.datetime):
                    return d.date()
                return d
                
            end_date_obj = to_date(end_date) or datetime.date.today()
            req_start_date_obj = to_date(required_start_date)

            min_db, max_db = await self.cache.get_trade_cal_range()
            min_db_obj = to_date(min_db)
            max_db_obj = to_date(max_db)

            curr_year = end_date_obj.year
            # Default start to 4 years ago if not specified
            target_start_obj = (
                req_start_date_obj
                if req_start_date_obj
                else datetime.date(curr_year - 4, 1, 1)
            )

            async def fetch_and_save(s_obj, e_obj):
                y = e_obj.year
                real_end = datetime.date(y, 12, 31)
                e_obj = max(e_obj, real_end)

                pass  # 动作已由装饰器覆盖
                # Pass native date objects to the api
                df = await self.api.get_trade_cal(start_date=s_obj, end_date=e_obj)
                if df is not None and not df.empty:
                    await self.cache.save_trade_cal(df)
                    return True
                return False

            if not min_db_obj or not max_db_obj:
                return await fetch_and_save(target_start_obj, end_date_obj)
                
            # Check coverage and fetch missing parts
            tasks = []
            if target_start_obj < min_db_obj:
                gap = (min_db_obj - target_start_obj).days
                if gap > 10:
                    tasks.append(fetch_and_save(target_start_obj, min_db_obj))

            if max_db_obj < end_date_obj:
                tasks.append(fetch_and_save(max_db_obj, end_date_obj))

            if tasks:
                results = await asyncio.gather(*tasks)
                return all(results)

            return True  # Already covered

        except Exception as e:
            logger.error(
                f"[DataProcessor] Calendar | ❌ Deep engine failure on ensure_trade_cal limit break: {e}",
                exc_info=True,
            )
            return False
