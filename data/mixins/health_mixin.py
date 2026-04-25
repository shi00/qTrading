"""
HealthCheckMixin — Extracted from DataProcessor (P2-M1).

Provides data health diagnostics and quality tier evaluation.
Expected host class attributes: cache, is_cancelled(), clear_cancel(),
                                 get_latest_trade_date(), get_trade_dates()
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import TYPE_CHECKING

from data.constants import (
    HEALTH_THRESHOLD_BREADTH,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_MARKET_LAG_DAYS,
    TIER_FINANCIAL_FRESHNESS_DAYS,
    TIER_QUOTE_FRESHNESS_DAYS,
)
from data.data_dictionary import TABLE_DEFINITIONS
from data.persistence.data_quality import DataQualityService
from ui.i18n import I18n
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager

logger = logging.getLogger(__name__)


def _compute_tier(
    lag_days: int,
    fin_fresh_ratio: float,
    missing_critical: bool = False,
    fin_lag_days: int | None = None,
) -> int:
    """
    Shared tier computation logic used by both fast-path and deep-path.

    Rules (applied in order):
      1. CRITICAL (0): Missing critical tables
      2. BRONZE  (1): Quotes lag > TIER_QUOTE_FRESHNESS_DAYS or fin_fresh_ratio too low
      3. GOLD    (3): All fresh AND (fin_fresh_ratio > 0.9 OR fin_lag < TIER_FINANCIAL_FRESHNESS_DAYS)
      4. SILVER  (2): Default for fresh quotes but not meeting GOLD criteria

    Note: The CRITICAL tier for extreme lag (> HEALTH_THRESHOLD_MARKET_LAG_DAYS) is
    handled by check_data_health's status aggregation before calling this function,
    not within _compute_tier itself. This avoids over-penalizing in the fast-path
    where deep health data is unavailable.

    Args:
        lag_days: Calendar days since latest quote data
        fin_fresh_ratio: Financial data coverage ratio (0.0-1.0), 0.5 used as neutral when unknown
        missing_critical: Whether any critical table has < 10% coverage
        fin_lag_days: Calendar days since latest financial data (optional, used by fast-path)
    """
    if missing_critical:
        return 0

    if lag_days > TIER_QUOTE_FRESHNESS_DAYS:
        return 1

    fin_ok_for_gold = False
    if fin_lag_days is not None:
        fin_ok_for_gold = fin_lag_days < TIER_FINANCIAL_FRESHNESS_DAYS and fin_fresh_ratio >= 0.5
    else:
        fin_ok_for_gold = fin_fresh_ratio > 0.9

    if fin_ok_for_gold:
        return 3

    if fin_fresh_ratio > 0.5:
        return 2

    if lag_days <= 5 and fin_fresh_ratio >= 0.1:
        return 2

    return 1


class HealthCheckMixin:
    """
    Mixin providing data health check and quality scanning capabilities.

    Expects the host class to provide:
        self.cache: CacheManager
        self.is_cancelled() -> bool
        self.clear_cancel() -> None
        self.get_latest_trade_date() -> str
        self.get_trade_dates(start, end) -> list
    """

    # Type hints for IDE support (resolved at runtime via DataProcessor)
    cache: CacheManager
    _quality_tier: int | None
    _health_cache: dict

    async def _assign_basic_tier(self):
        """
        Fast-path to assign a basic quality tier (Bronze/Silver/Gold) without
        scanning actual table counts. It relies solely on the `sync_status` table.
        Used primarily during silent startup.

        Tier Logic:
          - CRITICAL (0): No sync_status records at all, or daily_quotes never synced.
          - BRONZE  (1): daily_quotes exists but is stale (> TIER_QUOTE_FRESHNESS_DAYS lag).
          - SILVER  (2): All critical tables are fresh. Sufficient for MA/RSI strategies.
          - GOLD    (3): All critical tables fresh AND financial_reports recent (< TIER_FINANCIAL_FRESHNESS_DAYS).
        """
        try:
            sync_records = await self.cache.get_sync_status()

            # _read_db returns a pandas DataFrame
            if sync_records is None or (hasattr(sync_records, "empty") and sync_records.empty):
                self._quality_tier = 0
                logger.warning(
                    "[DataProcessor] FastCheck | ⚠️ No sync records. Degrading Tier to CRITICAL (0)",
                )
                return

            # Convert to dictionary for easy lookup: {table_name: row_dict}
            sync_dict = sync_records.set_index("table_name").to_dict("index")
            logger.debug("[DataProcessor] FastCheck | Sync records retrieved.")

            # Get all critical tables from data dictionary
            critical_tables = [
                name for name, meta in TABLE_DEFINITIONS.items() if meta.get("quality_config", {}).get("critical")
            ]

            # Check daily_quotes first (primary gate)
            latest_quote_date = sync_dict.get("daily_quotes", {}).get(
                "last_data_date",
                "",
            )

            # Fast verification: if sync_status is missing or stale, double check actual table MAX(date)
            try:
                if not latest_quote_date:
                    db_max_date = await self.cache.get_latest_trade_date()
                    if db_max_date:
                        latest_quote_date = str(db_max_date)
            except Exception as e:
                logger.error(
                    f"[DataProcessor] FastCheck | ❌ Deep DB fallback totally failed: {e}",
                    exc_info=True,
                )

            if not latest_quote_date:
                self._quality_tier = 1
                logger.debug(
                    "[DataProcessor] FastCheck | No last quote explicitly set in stats. Attempting verify...",
                )
                return

            try:
                latest_dt = parse_date(str(latest_quote_date), "%Y%m%d")
                days_lag = (get_now() - latest_dt).days
                logger.debug(
                    f"[DataProcessor] FastCheck | Quote Lag measured as {days_lag}d",
                )

                # Double check actual table if sync_status claims it's stale (sync_status could be out of sync with DB)
                if days_lag > TIER_QUOTE_FRESHNESS_DAYS:
                    logger.debug(
                        "[DataProcessor] FastCheck | Metadata points to stale, fallback to deep sweep...",
                    )
                    try:
                        db_max_date = await self.cache.get_latest_trade_date()
                        if db_max_date:
                            latest_dt = parse_date(str(db_max_date), "%Y%m%d")
                            days_lag = (get_now() - latest_dt).days
                            logger.debug(
                                f"[DataProcessor] FastCheck | DB MAX swept. Lag settled as {days_lag}d",
                            )
                    except Exception as e:
                        logger.warning(
                            f"[DataProcessor] FastCheck | ⚠️ Fallback DB query aborted: {e}",
                        )

            except (ValueError, TypeError):
                self._quality_tier = 1
                logger.warning(
                    f"[DataProcessor] FastCheck | ⚠️ Malformed date '{latest_quote_date}'. Degrading to BRONZE.",
                )
                return

            if days_lag <= TIER_QUOTE_FRESHNESS_DAYS:
                stale_critical = []
                for table in critical_tables:
                    if table == "daily_quotes":
                        continue

                    info = sync_dict.get(table, {})
                    last_date = info.get("last_data_date", "") if info else ""
                    if last_date:
                        try:
                            table_lag = (get_now() - parse_date(str(last_date), "%Y%m%d")).days
                            if table_lag > TIER_QUOTE_FRESHNESS_DAYS:
                                stale_critical.append(table)
                        except (ValueError, TypeError):
                            stale_critical.append(table)

                missing_critical = bool(stale_critical)

                fin_lag_days = None
                fin_info = sync_dict.get("financial_reports", {})
                fin_date = fin_info.get("last_data_date", "") if fin_info else ""
                if fin_date:
                    try:
                        fin_lag_days = (get_now() - parse_date(str(fin_date), "%Y%m%d")).days
                    except (ValueError, TypeError):
                        pass

                self._quality_tier = _compute_tier(
                    lag_days=days_lag,
                    fin_fresh_ratio=0.5,
                    missing_critical=missing_critical,
                    fin_lag_days=fin_lag_days,
                )
            else:
                self._quality_tier = _compute_tier(
                    lag_days=days_lag,
                    fin_fresh_ratio=0.5,
                    missing_critical=False,
                )

            logger.debug(
                f"[DataProcessor] FastCheck | Derived fast Tier parameter = {self._quality_tier}",
            )
        except Exception as e:
            logger.error(
                f"[DataProcessor] FastCheck | ❌ Critical crash during evaluate: {e}",
                exc_info=True,
            )
            # If we can't even read metadata, be conservative but don't block everything
            self._quality_tier = 1

    @log_async_operation(
        operation_name="check_data_health",
        log_result=True,
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def check_data_health(self):
        """Check data health status. Read-only diagnostic — immune to sync cancellation."""
        now = time.time()
        # 10s cache to prevent double-tap on startup
        if self._health_cache.get("data") and (now - self._health_cache.get("time", 0) < 10):
            return self._health_cache["data"]

        try:
            end_date = await self.get_latest_trade_date()  # type: ignore
            from utils.time_utils import parse_date

            end_date_obj = parse_date(end_date)
            from utils.config_handler import ConfigHandler

            years = ConfigHandler.get_init_history_years()
            # Use a safe 2.0 multiplier for trade-days to natural-days conversion
            rough_start = (end_date_obj - datetime.timedelta(days=int(250 * years * 2.0))).date()
            all_dates = await self.get_trade_dates(  # type: ignore
                start_date=rough_start,
                end_date=end_date,
            )
            if all_dates and len(all_dates) >= (years * 250):
                start_date = all_dates[-(years * 250)]
            else:
                start_date = all_dates[0] if all_dates else (end_date_obj - datetime.timedelta(days=365 * years)).date()

            official_dates = await self.get_trade_dates(start_date, end_date)  # type: ignore

            if not official_dates:
                return {"status": "red", "msg": I18n.get("health_err_calendar")}

            local_dates = await self.cache.get_cached_trade_dates()

            # 1. Market Health
            last_local = sorted(list(local_dates))[-1] if local_dates else None

            lag_days = 0
            # If latest official date is not in local cache, calculate lag
            if official_dates and (not local_dates or official_dates[-1] > last_local):
                if local_dates and last_local:
                    # Count business days lag
                    lag_days = len([d for d in official_dates if d > last_local])
                else:
                    # No local data, lag is total days
                    lag_days = len(official_dates)

            # 1.5 Concept Health
            try:
                concept_count = await self.cache.get_concept_count()
            except Exception as e:
                logger.error(
                    f"[DataProcessor] QualityScan | ❌ Concept sweep crash: {e}",
                    exc_info=True,
                )
                concept_count = 0

            # 2. Financial Health
            deep_health = await self.cache.check_comprehensive_health()

            # Scorecard construction
            status = "green"
            reasons = []

            if lag_days > 0:
                status = "yellow"
                reasons.append(I18n.get("health_market_lag").format(days=lag_days))
            if lag_days > HEALTH_THRESHOLD_MARKET_LAG_DAYS:
                status = "red"

            # 2.2 Comprehensive Data Coverage Check
            tables = deep_health.get("tables", {})
            fin_fresh_ratio = tables.get("financial_reports", {}).get("ratio", 0)

            # Identify missing critical tables dynamically from data dictionary
            critical_tables = [
                name for name, meta in TABLE_DEFINITIONS.items() if meta.get("quality_config", {}).get("critical")
            ]
            missing_critical = [t for t in critical_tables if tables.get(t, {}).get("ratio", 0) < 0.1]

            # Count all missing stock tables (exclude sparse tables — low coverage is expected)
            all_missing = [
                t
                for t, v in tables.items()
                if v.get("type") != "global" and v.get("ratio", 0) < 0.1 and not v.get("sparse", False)
            ]

            # Determine Data Status
            data_status = "green"
            if missing_critical:
                data_status = "red"
                reasons.append(f"{len(missing_critical)} Critical Tables Missing")
            elif len(all_missing) > 3:
                data_status = "yellow"
                reasons.append(f"{len(all_missing)} Tables Missing Data")
            elif fin_fresh_ratio < HEALTH_THRESHOLD_FINANCIAL_COVERAGE:
                data_status = "yellow"
                reasons.append(
                    I18n.get("health_financial_missing").format(
                        ratio=f"{fin_fresh_ratio:.0%}",
                    ),
                )

            # --- Depth & Breadth: Config-driven evaluation ---
            config_years = ConfigHandler.get_init_history_years()
            max_required = config_years * 250

            missing_depth = []
            actual_trade_days = deep_health.get("global_trade_days", 0)
            if max_required > 0 and actual_trade_days < max_required * 0.95:
                missing_depth = [t for t in critical_tables if tables.get(t, {}).get("depth_ratio") is not None]
                if missing_depth:
                    if data_status == "green":
                        data_status = "yellow"
                    reasons.append(
                        I18n.get("health_depth_warning").format(
                            count=len(missing_depth),
                            required=max_required,
                            actual=actual_trade_days,
                        ),
                    )

            missing_breadth = [
                t
                for t in critical_tables
                if tables.get(t, {}).get("breadth_ratio") is not None
                and tables.get(t, {}).get("breadth_ratio", 1.0) < HEALTH_THRESHOLD_BREADTH
            ]
            if missing_breadth:
                if data_status == "green":
                    data_status = "yellow"
                reasons.append(
                    I18n.get("health_breadth_warning").format(
                        count=len(missing_breadth),
                    ),
                )

            # Log Metrics
            logger.debug(
                f"[DataProcessor] Health | Metrics snapshot: Lag={lag_days}d, FinCoverage={fin_fresh_ratio:.1%}, Missing={len(all_missing)}, "
                f"MissDepth={len(missing_depth)}, MissBreadth={len(missing_breadth)}",
            )

            # Final Status Aggregation
            if status == "red" or data_status == "red":
                status = "red"
            elif status == "yellow" or data_status == "yellow":
                status = "yellow"

            if status != "green":
                logger.warning(
                    f"[DataProcessor] QualityScan | ⚠️ Evaluation abnormal. Status={status}, Reasons={reasons}",
                )

            # Update Tier State
            if status == "red":
                self._quality_tier = 0
            elif status == "yellow":
                self._quality_tier = 1
            else:
                self._quality_tier = _compute_tier(
                    lag_days=lag_days,
                    fin_fresh_ratio=fin_fresh_ratio,
                    missing_critical=bool(missing_critical),
                )

            # Calculate overall system coverage (using financial as main proxy)
            sys_coverage = fin_fresh_ratio * 100

            if lag_days == 0:
                status_desc = I18n.get("health_status_ok_short")
            else:
                status_desc = I18n.get("health_status_lag_short", days=lag_days)

            status_msg = I18n.get("init_complete").format(
                status=status_desc,
                coverage=f"{sys_coverage:.1f}%",
            )
            # Append concept info
            status_msg += f" | {I18n.get('health_concepts_count', count=concept_count)}"

            # Construction of Market Info with None safety
            latest_official = official_dates[-1] if official_dates else "N/A"
            market_info = {
                "latest_local": last_local if last_local else "N/A",
                "latest_official": latest_official,
                "lag_days": lag_days,
            }

            result_dict = {
                "status": status,
                "msg": status_msg,
                "reasons": reasons,
                "market": market_info,
                "fundamentals": deep_health,
                "details": {
                    "lag": lag_days,
                    "financial_coverage": sys_coverage,
                    "concept_count": concept_count,
                    "missing_critical": len(missing_critical),
                    "missing_depth": len(missing_depth),
                    "missing_breadth": len(missing_breadth),
                    "missing_all": len(all_missing),
                },
            }
            self._health_cache = {"time": now, "data": result_dict}
            return result_dict
        except Exception as e:
            logger.error(
                f"[DataProcessor] QualityScan | ❌ Deep engine health sweep crashed: {e}",
                exc_info=True,
            )
            return {"status": "red", "msg": f"Check failed: {e!s}"}

    @log_async_operation(
        operation_name="run_quality_scan",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run_quality_scan(self, sample_size=50, progress_callback=None):
        """
        Tier 2/Tier 3 Deep Health Scan.
        Samples stocks and runs DataQualityService checks.

        Args:
            sample_size: Number of stocks to sample (default 50).
            progress_callback: Callback(current, total, msg).
        """
        import random

        # Reset cancel event (prevents immediate skipped scan if previous op was cancelled)
        self.clear_cancel()  # type: ignore

        if progress_callback:
            progress_callback(0, 100, I18n.get("scan_step_init"))

        try:
            # 1. Select Sample
            basics = await self.cache.get_stock_basic()
            if basics is None or basics.empty:
                return {"score": 0, "tier": 0, "details": {}}

            active_stocks = basics[basics["list_status"] == "L"]["ts_code"].tolist()
            sample = random.sample(active_stocks, min(sample_size, len(active_stocks)))

            logger.debug(
                f"[DataProcessor] QualityScan | Commencing deep sweep on {len(sample)} random targets.",
            )

            # 2. Prepare Context
            scan_results = {"continuity": [], "recency": [], "nulls": []}

            # --- Architecture Optimization: One-Pass Batch Fetch ---
            # Fetch 1 year of data for all sampled stocks at once to avoid N+1 queries
            # and over-fetching entire 20-year history for single stocks.
            start_date_obj = (get_now() - datetime.timedelta(days=365)).date()

            trade_cal_df = await self.trade_calendar.get_trade_cal_df(  # type: ignore
                start_date=start_date_obj,
                is_open=1,
            )
            if trade_cal_df is None or trade_cal_df.empty:
                logger.warning(
                    "[DataProcessor] QualityScan | ⚠️ Trade calendar void, continuity skipped.",
                )

            batch_df = await self.cache.get_daily_quotes(
                ts_code_list=sample,
                start_date=start_date_obj,
            )

            # 3. Iterate Sample (DataFrame Slicing in Memory)
            # We use a simplified loop. In production, could be parallelized.
            total_steps = len(sample)

            for idx, ts_code in enumerate(sample):
                if self.is_cancelled():  # type: ignore
                    break

                # Update Progress
                pct = int((idx / total_steps) * 100)
                if progress_callback:
                    progress_callback(pct, 100, I18n.get("scan_scanning", code=ts_code))

                # Fetch Data via Batch Slice (No DB hit)
                if batch_df is not None and not batch_df.empty:
                    df_daily = batch_df[batch_df["ts_code"] == ts_code]
                else:
                    df_daily = None

                if df_daily is not None and not df_daily.empty:
                    # Sort explicitly to guarantee recency check safety
                    df_daily = df_daily.sort_values("trade_date", ascending=False)  # type: ignore

                    # Check Continuity (only if trade_cal is available)
                    if trade_cal_df is not None and not trade_cal_df.empty:
                        cont_res = DataQualityService.check_continuity(
                            df_daily,
                            "trade_date",
                            trade_cal_df,
                        )
                        scan_results["continuity"].append(cont_res["coverage_ratio"])

                    # Check Recency (vs today)
                    rec_res = DataQualityService.check_recency(
                        df_daily,
                        "trade_date",
                        get_now().date(),
                    )
                    scan_results["recency"].append(rec_res["lag_days"])

                    # Check Nulls (Close price)
                    null_res = DataQualityService.check_nulls(
                        df_daily,
                        ["close", "vol"],
                    )
                    scan_results["nulls"].append(null_res.get("close", 0.0))

            # 4. Aggregate
            avg_continuity = (
                sum(scan_results["continuity"]) / len(scan_results["continuity"]) if scan_results["continuity"] else 0
            )
            avg_recency = sum(scan_results["recency"]) / len(scan_results["recency"]) if scan_results["recency"] else 99

            tier = 1
            if avg_continuity > 0.95 and avg_recency < 5:
                tier = 2
            if avg_continuity > 0.99 and avg_recency < 3:
                tier = 3  # Placeholder logic for Tier 3

            self._quality_tier = tier
            logger.info(
                f"[DataProcessor] QualityScan | ✅ Thorough evaluation complete. Validated Deep Tier is {tier}",
            )

            result = {
                "score": int(avg_continuity * 100),
                "tier": tier,
                "sample_size": len(sample),
                "avg_continuity": avg_continuity,
                "avg_lag": avg_recency,
            }

            if progress_callback:
                progress_callback(100, 100, I18n.get("scan_complete"))
            return result

        except Exception as e:
            logger.error(
                f"[DataProcessor] QualityScan | ❌ Batch sampling crashed: {e}",
                exc_info=True,
            )
            return {"score": 0, "tier": 0, "error": str(e)}
        finally:
            # Ensure cancel state doesn't leak into subsequent operations
            self.clear_cancel()  # type: ignore
