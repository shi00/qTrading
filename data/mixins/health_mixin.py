"""
HealthCheckMixin — Extracted from DataProcessor (P2-M1).

Provides data health diagnostics and quality tier evaluation.
Expected host class attributes: cache, trade_calendar, is_cancelled(), clear_cancel()
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import pandas as pd

from data.constants import (
    CRITICAL_EMPTY_TABLES,
    HEALTH_THRESHOLD_BREADTH,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_MARKET_LAG_DAYS,
    MAJOR_INDICES,
    SYNC_RESULT_EMPTY,
    TIER_FINANCIAL_FRESHNESS_DAYS,
    TIER_FIN_FRESH_RATIO_GOLD,
    TIER_FIN_FRESH_RATIO_MIN,
    TIER_FIN_FRESH_RATIO_NEUTRAL,
    TIER_FUNDAMENTAL_HIGH_THRESHOLD,
    TIER_FUNDAMENTAL_LOW_THRESHOLD,
    TIER_QUOTE_FRESHNESS_DAYS,
)
from data.data_dictionary import TABLE_DEFINITIONS
from data.persistence.data_quality import DataQualityService
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.write_quality import WriteQuality
from core.i18n import I18n, Message
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now, parse_date, to_yyyymmdd_str

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.domain_services.trade_calendar_service import TradeCalendarService

logger = logging.getLogger(__name__)

# DAT-02: adj_factor 缺失率告警阈值（QualityScan 采样中缺失率超过该比例则告警）
_ADJ_FACTOR_NULL_RATIO_WARN = 0.1

# DAT-13: stock_basic active 数合理区间（A 股上市公司约 5000~5600）
_STOCK_BASIC_ACTIVE_MIN = 5000
_STOCK_BASIC_ACTIVE_MAX = 5600
# DAT-13: stock_basic updated_at 超过该天数视为陈旧
_STOCK_BASIC_STALE_DAYS = 30
# DAT-13: trade_cal 未来至少覆盖该天数（自然日），否则回测/调度日期轴提前截断
_TRADE_CAL_FUTURE_DAYS = 30
# DAT-13: index_daily 最新交易日滞后该天数视为陈旧
_INDEX_STALE_DAYS = 7


def _compute_tier(
    lag_days: int,
    fin_fresh_ratio: float | None,
    missing_critical: bool = False,
    fin_lag_days: int | None = None,
    avg_fundamental: float | None = None,
) -> int:
    """
    Shared tier computation logic used by both fast-path and deep-path.

    Rules (applied in order):
      1. CRITICAL (0): Missing critical tables
      2. BRONZE  (1): Quotes lag > TIER_QUOTE_FRESHNESS_DAYS
      3. SILVER  (2): Quotes fresh but field-level fundamental completeness < TIER_FUNDAMENTAL_LOW_THRESHOLD (insufficient for fundamental strategies)
      4. GOLD    (3): All fresh AND (fin_fresh_ratio > 0.9 OR fin_lag < TIER_FINANCIAL_FRESHNESS_DAYS)
                      AND field-level fundamental completeness > TIER_FUNDAMENTAL_HIGH_THRESHOLD (must be available)
                      When avg_fundamental is None (fast-path), GOLD is conservatively unreachable.
      5. SILVER  (2): Default for fresh quotes but not meeting GOLD criteria

    Note: The CRITICAL tier for extreme lag (> HEALTH_THRESHOLD_MARKET_LAG_DAYS) is
    handled by check_data_health's status aggregation before calling this function,
    not within _compute_tier itself. This avoids over-penalizing in the fast-path
    where deep health data is unavailable.

    Args:
        lag_days: Calendar days since latest quote data
        fin_fresh_ratio: Financial data coverage ratio (0.0-1.0), None when unknown (fast-path)
        missing_critical: Whether any critical table has < 10% coverage
        fin_lag_days: Calendar days since latest financial data (optional, used by fast-path)
        avg_fundamental: Field-level fundamental completeness (0.0-1.0), None when unavailable
    """
    if missing_critical:
        return 0

    if lag_days > TIER_QUOTE_FRESHNESS_DAYS:
        return 1

    if avg_fundamental is not None and avg_fundamental < TIER_FUNDAMENTAL_LOW_THRESHOLD:
        return 2

    if fin_fresh_ratio is None:
        if lag_days <= TIER_QUOTE_FRESHNESS_DAYS:
            return 2
        return 1

    fin_ok_for_gold = False
    if fin_lag_days is not None:
        fin_ok_for_gold = (
            fin_lag_days < TIER_FINANCIAL_FRESHNESS_DAYS and fin_fresh_ratio >= TIER_FIN_FRESH_RATIO_NEUTRAL
        )
    else:
        fin_ok_for_gold = fin_fresh_ratio > TIER_FIN_FRESH_RATIO_GOLD

    if fin_ok_for_gold:
        if avg_fundamental is not None and avg_fundamental > TIER_FUNDAMENTAL_HIGH_THRESHOLD:
            return 3

    if fin_fresh_ratio > TIER_FIN_FRESH_RATIO_NEUTRAL:
        return 2

    if lag_days <= TIER_QUOTE_FRESHNESS_DAYS and fin_fresh_ratio >= TIER_FIN_FRESH_RATIO_MIN:
        return 2

    return 1


class HealthCheckMixin:
    """
    Mixin providing data health check and quality scanning capabilities.

    Expects the host class to provide:
        self.cache: CacheManager
        self.trade_calendar: TradeCalendarService
        self.is_cancelled() -> bool
        self.clear_cancel() -> None
    """

    # Type hints for IDE support (resolved at runtime via DataProcessor)
    trade_calendar: TradeCalendarService
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
                         (GOLD is unreachable in fast-path because field-level fundamental
                          completeness is unavailable; use check_data_health for GOLD.)
        """
        try:
            sync_records = await self.cache.sync_dao.get_sync_status()

            # _read_db returns a pandas DataFrame
            if sync_records is None or not isinstance(sync_records, pd.DataFrame) or sync_records.empty:
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
                    db_max_date = await self.cache.quote_dao.get_latest_trade_date()
                    if db_max_date:
                        latest_quote_date = str(db_max_date)
            except Exception as e:
                logger.error(
                    "[DataProcessor] FastCheck | ❌ Deep DB fallback totally failed: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )

            if not latest_quote_date:
                self._quality_tier = 1
                logger.debug(
                    "[DataProcessor] FastCheck | No last quote explicitly set in stats. Attempting verify...",
                )
                return

            try:
                normalized = to_yyyymmdd_str(latest_quote_date)
                if not normalized:
                    self._quality_tier = 1
                    logger.warning(
                        "[DataProcessor] FastCheck | ⚠️ Invalid quote date '%s'. Degrading to BRONZE.",
                        latest_quote_date,
                    )
                    return
                latest_dt = parse_date(normalized, "%Y%m%d")
                days_lag = (get_now() - latest_dt).days
                logger.debug(
                    "[DataProcessor] FastCheck | Quote Lag measured as %sd",
                    days_lag,
                )

                # Double check actual table if sync_status claims it's stale (sync_status could be out of sync with DB)
                if days_lag > TIER_QUOTE_FRESHNESS_DAYS:
                    logger.debug(
                        "[DataProcessor] FastCheck | Metadata points to stale, fallback to deep sweep...",
                    )
                    try:
                        db_max_date = await self.cache.quote_dao.get_latest_trade_date()
                        if db_max_date:
                            latest_dt = parse_date(str(db_max_date), "%Y%m%d")
                            days_lag = (get_now() - latest_dt).days
                            logger.debug(
                                "[DataProcessor] FastCheck | DB MAX swept. Lag settled as %sd",
                                days_lag,
                            )
                    except Exception as e:
                        logger.warning(
                            "[DataProcessor] FastCheck | ⚠️ Fallback DB query aborted: %s",
                            DataSanitizer.sanitize_error(e),
                        )

            except (ValueError, TypeError):
                self._quality_tier = 1
                logger.warning(
                    "[DataProcessor] FastCheck | ⚠️ Malformed date '%s'. Degrading to BRONZE.",
                    latest_quote_date,
                )
                return

            if days_lag <= TIER_QUOTE_FRESHNESS_DAYS:
                stale_critical = []
                empty_critical = []
                for table in critical_tables:
                    info = sync_dict.get(table, {})
                    if not info:
                        stale_critical.append(table)
                        continue

                    last_date = info.get("last_data_date", "")
                    result_status = info.get("last_result_status", "")
                    record_count = info.get("record_count", 0)

                    if table in CRITICAL_EMPTY_TABLES:
                        if (
                            result_status == SYNC_RESULT_EMPTY
                            or info.get("status") == "empty"
                            or (record_count is not None and record_count == 0)
                        ):
                            empty_critical.append(table)
                            continue

                    normalized = to_yyyymmdd_str(last_date)
                    if normalized:
                        try:
                            table_lag = (get_now() - parse_date(normalized, "%Y%m%d")).days
                            if table_lag > TIER_QUOTE_FRESHNESS_DAYS:
                                stale_critical.append(table)
                        except (ValueError, TypeError):
                            stale_critical.append(table)
                    else:
                        stale_critical.append(table)

                if empty_critical:
                    self._quality_tier = 0
                    logger.warning(
                        "[DataProcessor] FastCheck | ⚠️ Critical tables with EMPTY data: %s. "
                        "Tier forced to CRITICAL (0)",
                        empty_critical,
                    )
                    return

                missing_critical = bool(stale_critical)

                fin_lag_days = None
                fin_info = sync_dict.get("financial_reports", {})
                fin_date = fin_info.get("last_data_date", "") if fin_info else ""
                normalized = to_yyyymmdd_str(fin_date)
                if normalized:
                    try:
                        fin_lag_days = (get_now() - parse_date(normalized, "%Y%m%d")).days
                    except (ValueError, TypeError):
                        pass

                self._quality_tier = _compute_tier(
                    lag_days=days_lag,
                    fin_fresh_ratio=None,
                    missing_critical=missing_critical,
                    fin_lag_days=fin_lag_days,
                )
            else:
                self._quality_tier = _compute_tier(
                    lag_days=days_lag,
                    fin_fresh_ratio=None,
                    missing_critical=False,
                )

            logger.debug(
                "[DataProcessor] FastCheck | Derived fast Tier parameter = %s",
                self._quality_tier,
            )
        except Exception as e:
            logger.error(
                "[DataProcessor] FastCheck | ❌ Critical crash during evaluate: %s",
                DataSanitizer.sanitize_error(e),
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
        """Check data health status. Read-only diagnostic — immune to sync cancellation.

        Note: "immune to sync cancellation" 指**数据同步**的取消信号（``context.cancel_event``），
        本方法不响应数据同步取消。但会将**关停/用户取消**信号（``self._get_cancel_event()``，
        即 ``asyncio.Event``）传入 ``check_comprehensive_health``，使构造深度健康检查时能及时中止，
        二者是不同信号源，语义互不影响。
        """
        now = time.time()
        # 10s cache to prevent double-tap on startup
        if self._health_cache.get("data") and (now - self._health_cache.get("time", 0) < 10):
            return self._health_cache["data"]

        try:
            end_date = await self.trade_calendar.get_latest_trade_date()
            if end_date is None:
                logger.warning("[DataProcessor] HealthCheck | No trade date available, using today.")
                end_date = get_now().date()
            end_date_obj = parse_date(end_date)

            years = ConfigHandler.get_init_history_years()
            # Use a safe 2.0 multiplier for trade-days to natural-days conversion
            rough_start = (end_date_obj - datetime.timedelta(days=int(250 * years * 2.0))).date()
            all_dates = await self.trade_calendar.get_trade_dates(
                start_date=rough_start,
                end_date=end_date,
            )
            if all_dates and len(all_dates) >= (years * 250):
                start_date = all_dates[-(years * 250)]
            else:
                start_date = all_dates[0] if all_dates else (end_date_obj - datetime.timedelta(days=365 * years)).date()

            official_dates = await self.trade_calendar.get_trade_dates(start_date, end_date)

            if not official_dates:
                return {"status": "red", "msg": I18n.get("health_err_calendar")}

            api_latest_official = None
            try:
                tc = getattr(self, "api", None)
                if tc is None:
                    from data.external.tushare_client import TushareClient

                    tc = TushareClient()
                api_cal_df = await tc.trade_cal(  # type: ignore[attr-defined]
                    start_date=end_date_obj.strftime("%Y%m%d"),
                    end_date=end_date_obj.strftime("%Y%m%d"),
                    is_open="1",
                )
                if api_cal_df is not None and not api_cal_df.empty:
                    api_latest = sorted(api_cal_df["cal_date"].tolist())[-1]
                    if isinstance(api_latest, str):
                        api_latest_official = api_latest
                        if api_latest > str(official_dates[-1]):
                            logger.warning(
                                "[DataProcessor] Health | Local trade_cal latest=%s, "
                                "API latest=%s. Local calendar may be stale. Using API as gold standard.",
                                official_dates[-1],
                                api_latest,
                            )
            except Exception as e:
                logger.debug("[DataProcessor] Health | API trade_cal cross-check skipped: %s", e)

            local_dates = await self.cache.quote_dao.get_cached_trade_dates()

            # 1. Market Health
            last_local = sorted(list(local_dates))[-1] if local_dates else None

            # P1-7 fix: Use API trade_cal as gold standard for lag calculation
            # when available, to avoid both trade_cal and daily_quotes being
            # stale simultaneously and falsely reporting "no lag".
            gold_standard_dates = official_dates
            if api_latest_official and isinstance(api_latest_official, str):
                try:
                    api_latest_date = parse_date(api_latest_official).date()
                    local_latest_str = str(official_dates[-1]) if official_dates else ""
                    if local_latest_str and api_latest_official > local_latest_str:
                        gold_standard_dates = official_dates + [api_latest_date]
                        logger.info(
                            "[DataProcessor] Health | P1-7: API extends official dates from %s to %s",
                            local_latest_str,
                            api_latest_official,
                        )
                except (ValueError, TypeError):
                    pass

            lag_days = 0
            if gold_standard_dates and (last_local is None or gold_standard_dates[-1] > last_local):
                if local_dates and last_local:
                    lag_days = len([d for d in gold_standard_dates if d > last_local])
                else:
                    lag_days = len(gold_standard_dates)

            # 1.5 Concept Health
            try:
                concept_count = await self.cache.get_concept_count()
            except Exception as e:
                logger.error(
                    "[DataProcessor] QualityScan | ❌ Concept sweep crash: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                concept_count = 0

            # 2. Financial Health
            deep_health = await self.cache.check_comprehensive_health(cancel_event=self._get_cancel_event())  # type: ignore[attr-defined]  # mixin 依赖宿主 DataProcessor 提供 _get_cancel_event

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

            # DAT-03: 关键表最近一次写入发生脏日期 coerce 超阈值 → 该表数据疑为错/缺，
            # 也视为质量降级（进 missing_critical 驱动 red status 与 tier=CRITICAL）。
            write_quality = WriteQuality()
            for t in critical_tables:
                if t not in missing_critical and write_quality.is_degraded(t):
                    missing_critical.append(t)
                    logger.warning(
                        "[DataProcessor] Health | ⚠️ 关键表 %s 最近一次写入日期字段 coerce 率超阈值，视为质量降级。",
                        t,
                    )

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

            # DAT-06: financial_reports 存在 ann_date IS NULL 的行 → 回测/实盘口径分叉。
            # 仅告警 + reasons 可见（健康面板），不硬降级 tier/status（review「非零即告警」）。
            try:
                if await self.cache.financial_dao.has_ann_date_nulls():
                    logger.warning(
                        "[DataProcessor] Health | ⚠️ financial_reports 存在 ann_date 为 NULL 的行（DAT-06），"
                        "回测/实盘口径将分叉",
                    )
                    reasons.append("financial_reports has rows with NULL ann_date")
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.debug(
                    "[DataProcessor] Health | ann_date NULL check skipped: %s",
                    DataSanitizer.sanitize_error(e),
                )

            # DAT-12: 跨表/跨字段一致性校验（外键替代，review03 建议的 SQL 检查）。
            # 仅告警 + reasons 可见，不硬降级 tier/status。
            await self._run_cross_validation_checks(reasons)
            # DAT-13: 维度表质量监控（stock_basic / trade_cal / index_daily）。
            await self._run_dimension_checks(reasons)

            # Log Metrics
            logger.debug(
                "[DataProcessor] Health | Metrics snapshot: Lag=%sd, FinCoverage=%.1f%%, Missing=%s, "
                "MissDepth=%s, MissBreadth=%s",
                lag_days,
                fin_fresh_ratio * 100,
                len(all_missing),
                len(missing_depth),
                len(missing_breadth),
            )

            # Final Status Aggregation
            if status == "red" or data_status == "red":
                status = "red"
            elif status == "yellow" or data_status == "yellow":
                status = "yellow"

            if status != "green":
                logger.warning(
                    "[DataProcessor] QualityScan | ⚠️ Evaluation abnormal. Status=%s, Reasons=%s",
                    status,
                    reasons,
                )

            # Tier uses the shared computation path even when status is yellow/red,
            # so fast/deep/scan stay aligned on the same grading inputs.
            avg_fund = None
            fin_lag_days = None
            try:
                latest_td = await self.cache.quote_dao.get_latest_trade_date()
                if latest_td:
                    fc = await self.cache.get_field_completeness(latest_td)
                    if fc:
                        valid_values = [v for v in fc.values() if v is not None]
                        avg_fund = sum(valid_values) / len(valid_values) if valid_values else None
            except Exception as e:
                logger.debug("[DataProcessor] HealthCheck | Field completeness check skipped: %s", e)
            try:
                sync_records = await self.cache.sync_dao.get_sync_status()
                if isinstance(sync_records, pd.DataFrame) and not sync_records.empty:
                    fin_info = sync_records[sync_records["table_name"] == "financial_reports"]
                    if not fin_info.empty:
                        fin_date_str = fin_info.iloc[0].get("last_data_date", "")
                        if fin_date_str:
                            fin_lag_days = (get_now() - parse_date(str(fin_date_str), "%Y%m%d")).days
            except (ValueError, TypeError, KeyError) as exc:
                logger.debug("[HealthMixin] Financial freshness calc skipped: %s", exc)
                pass

            self._quality_tier = _compute_tier(
                lag_days=lag_days,
                fin_fresh_ratio=fin_fresh_ratio,
                missing_critical=bool(missing_critical),
                fin_lag_days=fin_lag_days,
                avg_fundamental=avg_fund,
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
                "tier": self._quality_tier,
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
            # R9: 用 DataSanitizer.sanitize_error 脱敏，避免异常对象泄露 DB 密码/API token
            safe = DataSanitizer.sanitize_error(e)
            logger.error(
                "[DataProcessor] QualityScan | ❌ Deep engine health sweep crashed: %s",
                safe,
                exc_info=True,
            )
            return {"status": "red", "msg": f"Check failed: {safe}"}

    async def _run_cross_validation_checks(self, reasons: list[str]) -> None:
        """DAT-12: 跨表/跨字段一致性检查（生产原无任何跨表防线，外键替代）。

        review03 DAT-12 建议的 5 条规则中，financial_reports.ann_date IS NULL 已由
        DAT-06 单独实现；其余 4 条（孤儿 ts_code / 价格范围 / moneyflow 净流入 /
        adj_factor 单调）在此实现。全部只告警 + reasons 可见（健康面板），不硬降级
        tier/status（「非零即告警」口径）。每条检查独立容错，单表异常不影响其余检查。
        """
        q = self.cache.quote_dao
        checks: list[tuple[str, Callable[[], Awaitable[int]]]] = [
            ("orphan ts_codes in daily_quotes not in stock_basic", q.count_orphan_ts_codes),
            ("price range violations (high<low or close outside)", q.count_price_range_violations),
            ("moneyflow net_mf_amount != sum(buy) - sum(sell)", q.count_moneyflow_net_mismatch),
            ("adj_factor decreased vs previous trade day", q.count_adj_factor_monotonic_violations),
        ]
        for label, check in checks:
            try:
                cnt = await check()
                if cnt:
                    logger.warning("[DataProcessor] Health | ⚠️ DAT-12 %s: %d 行", label, cnt)
                    reasons.append(f"{label}: {cnt} rows")
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.debug(
                    "[DataProcessor] Health | DAT-12 check '%s' skipped: %s",
                    label,
                    DataSanitizer.sanitize_error(e),
                )

    async def _run_dimension_checks(self, reasons: list[str]) -> None:
        """DAT-13: 维度表质量监控——stock_basic / trade_cal / index_daily。

        与日线表不同，维度表的检查是「覆盖度」而非「连续性」：
        - stock_basic: active 数合理区间 + ts_code 格式合法 + updated_at 新鲜度
        - trade_cal: 未来至少覆盖 _TRADE_CAL_FUTURE_DAYS 天（否则回测/调度日期轴提前截断）
        - index_daily: 每只 MAJOR_INDICES 都有近期数据（screening_history 的
          index_pct / alpha 计算基准）。同样只告警，不硬降级。
        """
        # --- stock_basic ---
        try:
            sb = await self.cache.stock_dao.get_stock_basic_health_summary()
            if sb is not None and not sb.empty:
                row = sb.iloc[0]
                active = row.get("active_count")
                if active is not None and not (_STOCK_BASIC_ACTIVE_MIN <= active <= _STOCK_BASIC_ACTIVE_MAX):
                    logger.warning(
                        "[DataProcessor] Health | ⚠️ DAT-13 stock_basic active=%s 超出合理区间 [%d, %d]",
                        active,
                        _STOCK_BASIC_ACTIVE_MIN,
                        _STOCK_BASIC_ACTIVE_MAX,
                    )
                    reasons.append(f"stock_basic active count {active} outside expected range")
                invalid = row.get("invalid_ts_code_count")
                if invalid:
                    reasons.append(f"stock_basic has {invalid} invalid ts_code rows")
                latest_upd = row.get("latest_updated_at")
                if latest_upd is not None:
                    age_days = (get_now() - parse_date(latest_upd)).days
                    if age_days > _STOCK_BASIC_STALE_DAYS:
                        logger.warning(
                            "[DataProcessor] Health | ⚠️ DAT-13 stock_basic updated_at 陈旧 (%dd)",
                            age_days,
                        )
                        reasons.append(f"stock_basic updated_at stale ({age_days}d)")
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug(
                "[DataProcessor] Health | DAT-13 stock_basic check skipped: %s",
                DataSanitizer.sanitize_error(e),
            )

        # --- trade_cal ---
        try:
            latest_cal = await self.cache.stock_dao.get_latest_open_cal_date()
            if latest_cal is None:
                reasons.append("trade_cal has no open-day records")
            else:
                cal_d = latest_cal.date() if isinstance(latest_cal, datetime.datetime) else latest_cal
                if cal_d < get_now().date() + datetime.timedelta(days=_TRADE_CAL_FUTURE_DAYS):
                    logger.warning(
                        "[DataProcessor] Health | ⚠️ DAT-13 trade_cal 最新 open 日=%s 未来覆盖不足",
                        cal_d,
                    )
                    reasons.append(f"trade_cal future coverage insufficient (latest open day {cal_d})")
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug(
                "[DataProcessor] Health | DAT-13 trade_cal check skipped: %s",
                DataSanitizer.sanitize_error(e),
            )

        # --- index_daily ---
        try:
            idx = await self.cache.quote_dao.get_index_daily_coverage_summary()
            covered = set(idx["ts_code"].tolist()) if idx is not None and not idx.empty else set()
            missing = [c for c in MAJOR_INDICES if c not in covered]
            if missing:
                logger.warning(
                    "[DataProcessor] Health | ⚠️ DAT-13 index_daily 缺失 %d 只 MAJOR_INDICES: %s",
                    len(missing),
                    missing,
                )
                reasons.append(f"index_daily missing data for {len(missing)} MAJOR_INDICES")
            if idx is not None and not idx.empty:
                cutoff = get_now().date() - datetime.timedelta(days=_INDEX_STALE_DAYS)
                stale = []
                for _, r in idx.iterrows():
                    latest = r.get("latest_trade_date")
                    if latest is None:
                        continue
                    d = latest.date() if isinstance(latest, datetime.datetime) else latest
                    if d < cutoff:
                        stale.append(str(r.get("ts_code")))
                if stale:
                    logger.warning(
                        "[DataProcessor] Health | ⚠️ DAT-13 index_daily 陈旧 %d 只: %s",
                        len(stale),
                        stale,
                    )
                    reasons.append(f"index_daily stale for {len(stale)} MAJOR_INDICES")
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug(
                "[DataProcessor] Health | DAT-13 index_daily check skipped: %s",
                DataSanitizer.sanitize_error(e),
            )

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
        self.clear_cancel()  # type: ignore[attr-defined]

        if progress_callback:
            progress_callback(0, 100, Message("scan_step_init"))

        try:
            # 1. Select Sample
            basics = await self.cache.stock_dao.get_stock_basic()
            if basics is None or basics.empty:
                return {"score": 0, "tier": 0, "details": {}}

            active_stocks = basics[basics["list_status"].isin(["L", "D"])]["ts_code"].tolist()
            sample = random.sample(active_stocks, min(sample_size, len(active_stocks)))

            logger.debug(
                "[DataProcessor] QualityScan | Commencing deep sweep on %s random targets.",
                len(sample),
            )

            deep_health = {}
            fin_fresh_ratio = 0.0
            missing_critical = False
            try:
                deep_health = await self.cache.check_comprehensive_health(cancel_event=self._get_cancel_event())  # type: ignore[attr-defined]  # mixin 依赖宿主 DataProcessor 提供 _get_cancel_event
                tables = deep_health.get("tables", {}) if isinstance(deep_health, dict) else {}
                fin_fresh_ratio = tables.get("financial_reports", {}).get("ratio", 0.0)
                critical_tables = [
                    name for name, meta in TABLE_DEFINITIONS.items() if meta.get("quality_config", {}).get("critical")
                ]
                missing_critical = any(tables.get(t, {}).get("ratio", 0.0) < 0.1 for t in critical_tables)
            except Exception as e:
                logger.debug("[DataProcessor] QualityScan | Coverage snapshot skipped: %s", e)

            # 2. Prepare Context
            scan_results = {"continuity": [], "recency": [], "nulls": []}

            # Align deep-scan recency to the latest closed trade date to avoid
            # intraday false negatives before the market close snapshot exists.
            try:
                latest_closed_trade_date = await self.trade_calendar.get_latest_trade_date()
                if isinstance(latest_closed_trade_date, datetime.datetime):
                    end_date_obj = latest_closed_trade_date.date()
                elif isinstance(latest_closed_trade_date, datetime.date):
                    end_date_obj = latest_closed_trade_date
                elif latest_closed_trade_date:
                    end_date_obj = parse_date(str(latest_closed_trade_date))
                else:
                    end_date_obj = get_now().date()
            except Exception as e:
                logger.debug("[DataProcessor] QualityScan | Latest trade date fallback: %s", e)
                end_date_obj = get_now().date()

            # --- Architecture Optimization: One-Pass Batch Fetch ---
            # Fetch 1 year of data for all sampled stocks at once to avoid N+1 queries
            # and over-fetching entire 20-year history for single stocks.
            start_date_obj = end_date_obj - datetime.timedelta(days=365)

            trade_cal_df = await self.trade_calendar.get_trade_cal_df(  # type: ignore[attr-defined]
                start_date=start_date_obj,
                end_date=end_date_obj,
                is_open=1,
            )
            if trade_cal_df is None or trade_cal_df.empty:
                logger.warning(
                    "[DataProcessor] QualityScan | ⚠️ Trade calendar void, continuity skipped.",
                )

            batch_df = await self.cache.quote_dao.get_daily_quotes(
                ts_code_list=sample,
                start_date=start_date_obj,
                end_date=end_date_obj,
            )

            # 3. Iterate Sample (DataFrame Slicing in Memory)
            # We use a simplified loop. In production, could be parallelized.
            total_steps = len(sample)

            for idx, ts_code in enumerate(sample):
                if self.is_cancelled():  # type: ignore[attr-defined]
                    break

                # Update Progress
                pct = int((idx / total_steps) * 100)
                if progress_callback:
                    progress_callback(pct, 100, Message("scan_scanning", {"code": ts_code}))

                # Fetch Data via Batch Slice (No DB hit)
                if batch_df is not None and not batch_df.empty:
                    df_daily = batch_df[batch_df["ts_code"] == ts_code]
                else:
                    df_daily = None

                if df_daily is not None and not df_daily.empty:
                    # Sort explicitly to guarantee recency check safety
                    df_daily = df_daily.sort_values("trade_date", ascending=False)  # type: ignore[union-attr]

                    # Check Continuity (only if trade_cal is available)
                    if trade_cal_df is not None and not trade_cal_df.empty:
                        cont_res = DataQualityService.check_continuity(
                            df_daily,
                            "trade_date",
                            trade_cal_df,
                        )
                        scan_results["continuity"].append(cont_res["coverage_ratio"])

                    # Check Recency against the latest closed trade date, not wall-clock today.
                    rec_res = DataQualityService.check_recency(
                        df_daily,
                        "trade_date",
                        end_date_obj,
                    )
                    scan_results["recency"].append(rec_res["lag_days"])

                    # Check Nulls (Close price)
                    null_res = DataQualityService.check_nulls(
                        df_daily,
                        ["close", "vol"],
                    )
                    scan_results["nulls"].append(null_res.get("close", 0.0))

                    # Check adj_factor monotonicity + null ratio (DAT-02)
                    adj_health = DataQualityService.check_adj_factor_monotonic(df_daily)
                    if adj_health["violations"]:
                        logger.warning(
                            "[DataProcessor] QualityScan | ⚠️ adj_factor 非单调: %s",
                            adj_health["violations"],
                        )
                    if adj_health["null_ratio"] > _ADJ_FACTOR_NULL_RATIO_WARN:
                        logger.warning(
                            "[DataProcessor] QualityScan | ⚠️ adj_factor 缺失率 %.0f%%: %s",
                            adj_health["null_ratio"] * 100,
                            ts_code,
                        )

            # 4. Aggregate Quote Metrics
            avg_continuity = (
                sum(scan_results["continuity"]) / len(scan_results["continuity"]) if scan_results["continuity"] else 0
            )
            avg_recency = sum(scan_results["recency"]) / len(scan_results["recency"]) if scan_results["recency"] else 99

            # 5. Fundamental Field Completeness
            fundamental_completeness = {}
            try:
                latest_td = await self.cache.quote_dao.get_latest_trade_date()
                if latest_td:
                    fundamental_completeness = await self.cache.get_field_completeness(latest_td)
            except Exception as e:
                logger.debug("[DataProcessor] QualityScan | Fundamental completeness check skipped: %s", e)

            avg_fundamental = (
                sum(v for v in fundamental_completeness.values() if v is not None)
                / len([v for v in fundamental_completeness.values() if v is not None])
                if fundamental_completeness and any(v is not None for v in fundamental_completeness.values())
                else None
            )

            # 6. Multi-table Coverage (financial_reports recency)
            fin_recency_ok = False
            fin_lag_days = None
            try:
                sync_records = await self.cache.sync_dao.get_sync_status()
                if isinstance(sync_records, pd.DataFrame) and not sync_records.empty:
                    fin_info = sync_records[sync_records["table_name"] == "financial_reports"]
                    if not fin_info.empty:
                        fin_date_str = fin_info.iloc[0].get("last_data_date", "")
                        if fin_date_str:
                            fin_dt = parse_date(str(fin_date_str), "%Y%m%d")
                            fin_dt_date = fin_dt.date() if isinstance(fin_dt, datetime.datetime) else fin_dt
                            end_as_date = (
                                end_date_obj.date() if isinstance(end_date_obj, datetime.datetime) else end_date_obj
                            )
                            fin_lag_days = (end_as_date - fin_dt_date).days
                            fin_recency_ok = fin_lag_days < TIER_FINANCIAL_FRESHNESS_DAYS
            except (ValueError, TypeError, KeyError) as exc:
                logger.debug("[HealthMixin] Composite tier calc skipped: %s", exc)
                pass

            # 7. Composite Score & Tier
            quote_score = avg_continuity * 100
            if avg_fundamental is not None:
                fundamental_score = avg_fundamental * 100
            else:
                fundamental_score = None
            if fundamental_score is not None:
                composite_score = quote_score * 0.5 + fundamental_score * 0.3 + (100.0 if fin_recency_ok else 0.0) * 0.2
            else:
                composite_score = quote_score * 0.7 + (100.0 if fin_recency_ok else 0.0) * 0.3

            tier = _compute_tier(
                lag_days=int(avg_recency),
                fin_fresh_ratio=fin_fresh_ratio,
                missing_critical=missing_critical,
                fin_lag_days=fin_lag_days,
                avg_fundamental=avg_fundamental if fundamental_completeness else None,
            )

            self._quality_tier = tier
            fin_score_str = f"{fundamental_score:.0f}" if fundamental_score is not None else "N/A"
            logger.info(
                "[DataProcessor] QualityScan | ✅ Thorough evaluation complete. "
                "Composite=%.0f (Quote=%.0f, Fin=%s, "
                "FinRecency=%s). Tier=%s",
                composite_score,
                quote_score,
                fin_score_str,
                "OK" if fin_recency_ok else "STALE",
                tier,
            )

            result = {
                "score": int(composite_score),
                "tier": tier,
                "sample_size": len(sample),
                "avg_continuity": avg_continuity,
                "avg_lag": avg_recency,
                "fundamental_completeness": fundamental_completeness,
                "avg_fundamental": avg_fundamental,
                "fin_recency_ok": fin_recency_ok,
            }

            if progress_callback:
                progress_callback(100, 100, Message("scan_complete"))
            return result

        except Exception as e:
            # R9: 用 DataSanitizer.sanitize_error 脱敏，避免异常对象泄露 DB 密码/API token
            safe = DataSanitizer.sanitize_error(e)
            logger.error(
                "[DataProcessor] QualityScan | ❌ Batch sampling crashed: %s",
                safe,
                exc_info=True,
            )
            return {"score": 0, "tier": 0, "error": safe}
        finally:
            # Ensure cancel state doesn't leak into subsequent operations
            self.clear_cancel()  # type: ignore[attr-defined]
