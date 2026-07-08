import asyncio
import datetime
import logging
import typing

import pandas as pd

from data.constants import MAJOR_INDICES
from data.persistence.daos.macro_dao import MacroDao
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.error_classifier import classify_error, classify_severity
from utils.time_utils import get_now, parse_date

from .base import ISyncStrategy, SyncResult
from data.persistence.daos.base_dao import EngineDisposedError
from data.external.tushare_client import TushareAPIPermissionError
from data.constants import SYNC_RESULT_SKIPPED_PERMISSION

logger = logging.getLogger(__name__)

# Default lookback removed in favor of dynamic config.
# Shibor skip threshold: start from next day after latest
_SHIBOR_RESUME_OFFSET_DAYS = 1
# Fallback lookback when date parsing fails
_SHIBOR_FALLBACK_LOOKBACK_DAYS = 365


def _parse_period(p: typing.Any):
    """Parse Tushare macro period format (YYYYMM) to standard date string.

    Tushare macro APIs (cn_m, cn_cpi, cn_ppi) return period as 'YYYYMM' string.
    This function converts it to 'YYYY-MM-01' format for proper date parsing.

    Args:
        p: Period value (string, None, or NaN)

    Returns:
        str: 'YYYY-MM-01' format string, or original value if not YYYYMM format
        None: If input is NaN/None
    """
    if pd.isna(p):
        return None
    p_str = str(p).strip()
    if len(p_str) == 6 and p_str.isdigit():
        return f"{p_str[:4]}-{p_str[4:]}-01"
    return p_str


def _period_to_yyyymm(period: typing.Any) -> str | None:
    """将 period (date/Timestamp/str) 转为 Tushare 宏观接口的 YYYYMM 格式。

    Tushare 宏观接口 (cn_m, cn_cpi, cn_ppi) 的 start_m/end_m 参数
    要求 YYYYMM 格式，而非通用 API 的 YYYYMMDD 格式。
    """
    if period is None:
        return None
    if isinstance(period, str):
        # Already YYYYMM format (e.g. "202403")
        if len(period) == 6 and period.isdigit():
            return period
        # ISO date string (e.g. "2024-03-01") or YYYYMMDD — parse then format
        try:
            dt = parse_date(period)
            if hasattr(dt, "year") and hasattr(dt, "month"):
                return f"{dt.year}{dt.month:02d}"
        except (ValueError, TypeError):
            pass
        # Last resort: take first 6 digits
        return period[:6]
    if hasattr(period, "year") and hasattr(period, "month"):
        return f"{period.year}{period.month:02d}"
    return None


def _compute_publish_date(period_date: datetime.date) -> datetime.date:
    """保守估算宏观指标发布日期：报告期次月16日。

    中国宏观经济数据发布窗口：
    - CPI/PPI：次月 9-12 日发布
    - M2/M1/M0：次月 10-15 日发布
    统一使用次月 16 日作为保守估算，确保所有指标均已发布。
    """
    if period_date.month == 12:
        return datetime.date(period_date.year + 1, 1, 16)
    return datetime.date(period_date.year, period_date.month + 1, 16)


def _quarter_to_period_end(quarter: str) -> datetime.date | None:
    """将 Tushare cn_gdp 的 quarter 字符串（如 "2024Q4"）转为季度末日期。

    - Q1 → 03-31
    - Q2 → 06-30
    - Q3 → 09-30
    - Q4 → 12-31

    Returns:
        datetime.date 或 None（解析失败时）
    """
    if not isinstance(quarter, str) or len(quarter) != 6 or quarter[4] not in ("Q", "q"):
        return None
    try:
        year = int(quarter[:4])
        q = int(quarter[5])
    except ValueError:
        return None
    last_day = {1: 31, 2: 30, 3: 30, 4: 31}
    month = {1: 3, 2: 6, 3: 9, 4: 12}
    if q not in month:
        return None
    return datetime.date(year, month[q], last_day[q])


def _compute_gdp_publish_date(period_date: datetime.date) -> datetime.date:
    """保守估算 GDP 发布日期：季度结束后次月 20 日。

    中国 GDP 数据发布窗口（国家统计局）：
    - 一季度 GDP：4 月 16-20 日发布
    - 二季度 GDP：7 月 15-20 日发布
    - 三季度 GDP：10 月 18-20 日发布
    - 四季度 GDP：次年 1 月 18-20 日发布
    统一使用季度结束后次月 20 日作为保守估算，避免回测前视偏差。
    """
    if period_date.month == 12:
        return datetime.date(period_date.year + 1, 1, 20)
    next_month = period_date.month + 1
    # 季度末月份：3, 6, 9, 12 → 次月 4, 7, 10, 1
    return datetime.date(period_date.year, next_month, 20)


def _latest_quarter_before(period: datetime.date) -> str:
    """根据 period（最新 macro_economy period）推断应同步的最近 quarter。

    若 period 是 2024-06-30（Q2 末），返回 "2024Q2"；若 period 是 2024-07-15
    （Q3 中），返回 "2024Q2"（Q3 未结束，不能拉取）。

    Returns:
        "YYYYQN" 字符串
    """
    month = period.month
    if month <= 3:
        return f"{period.year - 1}Q4"
    if month <= 6:
        return f"{period.year}Q1"
    if month <= 9:
        return f"{period.year}Q2"
    return f"{period.year}Q3"


class MacroSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Macroeconomic data (M2, CPI, PPI, GDP, Shibor).
    Runs efficiently by checking the latest available data date.
    """

    _M2_COLUMNS = ["period", "m2", "m2_yoy", "m1", "m1_yoy", "m0", "m0_yoy"]
    # Phase 2D §3.2.6：cn_gdp 返回字段（quarter 已被 _COLUMN_RENAMES 重命名为 period）
    _GDP_COLUMNS = ["period", "gdp", "gdp_yoy", "pi", "pi_yoy", "si", "si_yoy", "ti", "ti_yoy"]

    def __init__(self, context: typing.Any):
        super().__init__(context)
        self.dao = MacroDao(context.cache.engine)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_effective_trade_date(self) -> datetime.date:
        """Prefer the latest closed trade date for default sync windows."""
        try:
            trade_date = await self.context.processor.trade_calendar.get_latest_trade_date()  # type: ignore[union-attr]
            if trade_date is None:
                logger.warning("[MacroSync] get_latest_trade_date returned None, falling back to today.")
            elif isinstance(trade_date, datetime.datetime):
                return trade_date.date()
            elif isinstance(trade_date, datetime.date):
                return trade_date
            elif trade_date:
                parsed = parse_date(str(trade_date))
                return parsed.date() if hasattr(parsed, "date") else parsed
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[MacroSync] Effective trade date fallback: %s", e, exc_info=True)
        return get_now().date()

    @log_async_operation(
        operation_name="MacroSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def _run_impl(self, **kwargs: typing.Any) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            await self._sync_macro_monthly(result)
            if self._check_cancelled(result):
                return result
            await self._sync_shibor_daily(result)
            if self._check_cancelled(result):
                return result
            await self._sync_index_weights(result)
        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except EngineDisposedError:
            logger.warning("[MacroSync] Run | Engine disposed, stopping sync.")
            result.status = "failed"
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[MacroSync] SYSTEM-LEVEL failure: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning("[MacroSync] Recoverable error (%s): %s", error_info["code"], e, exc_info=True)
            else:
                logger.error("[MacroSync] Operational error: %s", e, exc_info=True)
            result.status = "failed"
            result.errors.append(error_info["message_key"])
        if self._cancelled and result.status not in ("failed", "cancelled"):
            result.status = "cancelled"
        if result.status == "cancelled":
            logger.info(
                "[MacroSync] Run | ⚠️ Cancelled. Added=%s, Errors=%s",
                result.added,
                len(result.errors),
            )
        elif result.status != "failed":
            logger.info(
                "[MacroSync] Run | ✅ Complete. Added=%s, Errors=%s",
                result.added,
                len(result.errors),
            )
        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _sync_macro_monthly(self, result: typing.Any):
        """
        Fetch M2, CPI, PPI, GDP and merge into a single DataFrame before save.
        Merging in-memory avoids INSERT OR REPLACE wiping other columns.
        """
        try:
            latest = await self.dao.get_macro_latest_date()
            start_m = _period_to_yyyymm(latest)

            df_m2 = await self.context.api.get_macro_data("cn_m", start_m=start_m)
            df_cpi = await self.context.api.get_macro_data("cn_cpi", start_m=start_m)
            df_ppi = await self.context.api.get_macro_data("cn_ppi", start_m=start_m)

            # Phase 2D §3.2.6：cn_gdp 同步分支
            # v1.10.0 P0-1：cn_gdp API 期望 quarter 参数（如 "2024Q4"），与 cn_m/cn_cpi/cn_ppi
            # 的 start_m（YYYYMM）不兼容，因此使用专用 get_cn_gdp(quarter) wrapper。
            # quarter 推断：根据 latest period 取最近已结束的季度，避免拉取未公布数据。
            df_gdp = None
            try:
                if latest is not None:
                    latest_period = parse_date(str(latest)).date() if isinstance(latest, str) else latest
                    quarter = _latest_quarter_before(latest_period)
                    df_gdp = await self.context.api.get_cn_gdp(quarter=quarter)
                else:
                    # 首次同步：拉取去年 Q4（确保已发布）
                    current_year = get_now().year
                    df_gdp = await self.context.api.get_cn_gdp(quarter=f"{current_year - 1}Q4")
            except TushareAPIPermissionError:
                logger.warning("[MacroSync] Monthly | ⛔ Permission denied for cn_gdp, skipping GDP")
                # GDP 权限不足不阻断 m2/cpi/ppi 同步，df_gdp 保持 None
            except Exception as e:
                logger.warning("[MacroSync] Monthly | ⚠️ cn_gdp fetch failed, skipping GDP: %s", e, exc_info=True)
                # GDP 失败不阻断 m2/cpi/ppi 同步，df_gdp 保持 None

            merged = self._merge_macro_data(df_m2, df_cpi, df_ppi, df_gdp)

            if merged is not None and not merged.empty:
                count = await self.dao.save_macro_economy(merged)
                result.added += count if count else 0
                logger.debug("[MacroSync] Monthly | Saved %s macro records", count)
                latest_period = merged["period"].max() if "period" in merged.columns else get_now().date()
                if isinstance(latest_period, str):
                    if len(latest_period) == 6:
                        latest_period = parse_date(latest_period, "%Y%m").date()
                    else:
                        latest_period = parse_date(latest_period).date()
                await self.context.cache.update_sync_status(
                    "macro_economy",
                    latest_period,
                    count or 0,
                )

        except EngineDisposedError:
            raise
        except TushareAPIPermissionError:
            logger.warning("[MacroSync] Monthly | ⛔ Permission denied for macro APIs")
            result.errors.append("Macro Monthly: permission denied")
            try:
                latest = await self.dao.get_macro_latest_date()
                if latest:
                    latest_period = parse_date(str(latest)).date() if isinstance(latest, str) else latest
                else:
                    latest_period = get_now().date()
                await self.context.cache.update_sync_status(
                    "macro_economy",
                    latest_period,
                    0,
                    status="skipped_permission",
                    last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                )
            except Exception as e:
                logger.debug("[MacroSync] Monthly | Failed to record skipped_permission status: %s", e, exc_info=True)
        except Exception as e:
            logger.warning("[MacroSync] Monthly | ⚠️ Error: %s", e, exc_info=True)
            result.errors.append(f"Macro Monthly: {e}")

    @classmethod
    def _merge_macro_data(
        cls,
        df_m2: typing.Any,
        df_cpi: typing.Any,
        df_ppi: typing.Any,
        df_gdp: typing.Any = None,
    ):
        """
        Merge M2/CPI/PPI/GDP DataFrames on period column.

        - M2/CPI/PPI: monthly period (YYYYMM), merged on period (月初)
        - GDP: quarterly period ("YYYYQN" → quarter-end date), independent rows

        Note: Column renaming is handled by TushareClient._COLUMN_RENAMES:
        - cn_m: month -> period
        - cn_cpi: month -> period, nt_val -> cpi
        - cn_ppi: month -> period, ppi_yoy -> ppi
        - cn_gdp: quarter -> period
        """
        merged = None

        if df_m2 is not None and not df_m2.empty:
            available = [c for c in cls._M2_COLUMNS if c in df_m2.columns]
            merged = df_m2[available].copy()

        merged = cls._merge_indicator(merged, df_cpi, "cpi")
        merged = cls._merge_indicator(merged, df_ppi, "ppi")

        # Phase 2D §3.2.6：追加 GDP 数据（季度粒度，period 为季度末日）
        # GDP 行与月度行 period 不同（2024-12-31 vs 2024-12-01），作为独立行 concat
        if df_gdp is not None and not df_gdp.empty:
            gdp_available = [c for c in cls._GDP_COLUMNS if c in df_gdp.columns]
            gdp_df = df_gdp[gdp_available].copy()
            if "period" in gdp_df.columns:
                # quarter 字符串（如 "2024Q4"）转季度末日期（如 2024-12-31）
                gdp_df["period"] = gdp_df["period"].apply(_quarter_to_period_end)
                gdp_df = gdp_df.dropna(subset=["period"])
                if not gdp_df.empty:
                    # 保守 publish_date：季度结束后次月 20 日
                    gdp_df["publish_date"] = gdp_df["period"].apply(_compute_gdp_publish_date)
                    if merged is not None and not merged.empty:
                        merged = pd.concat([merged, gdp_df], ignore_index=True)
                    else:
                        merged = gdp_df

        if merged is not None and not merged.empty:
            if "period" not in merged.columns:
                logger.warning("[MacroSync] _merge_macro_data | 'period' column missing after merge, returning None")
                return None

            # Tushare macro APIs (cn_m, cn_cpi, cn_ppi) return period as 'YYYYMM' string.
            # base_dao.py's pd.to_datetime(format='mixed') parses 'YYYYMM' as NaT.
            # Here we ensure it's either cleanly parsed or dropped if completely invalid.
            # GDP rows already have period as datetime.date, _parse_period passes through.
            merged["period"] = merged["period"].apply(_parse_period)
            merged["period"] = pd.to_datetime(merged["period"], format="mixed", errors="coerce").dt.date
            merged = merged.dropna(subset=["period"])

            # Compute publish_date
            # - GDP rows already have publish_date set (from _compute_gdp_publish_date)
            # - Monthly rows need publish_date computed (from _compute_publish_date)
            if "publish_date" not in merged.columns:
                merged["publish_date"] = merged["period"].apply(
                    lambda p: _compute_publish_date(p) if isinstance(p, datetime.date) else None
                )
            else:
                # Fill missing publish_date (monthly rows where GDP concat left NaN)
                mask = merged["publish_date"].isna()
                if mask.any():
                    merged.loc[mask, "publish_date"] = merged.loc[mask, "period"].apply(
                        lambda p: _compute_publish_date(p) if isinstance(p, datetime.date) else None
                    )

        return merged

    @staticmethod
    def _merge_indicator(merged: typing.Any, df: pd.DataFrame, target_col: typing.Any):
        """
        Merge a single indicator DataFrame into the merged result.

        Args:
            merged: Existing merged DataFrame or None
            df: Indicator DataFrame (columns already renamed by TushareClient._COLUMN_RENAMES)
            target_col: Target column name (e.g., 'cpi', 'ppi')
        """
        if df is None or df.empty:
            return merged

        if target_col not in df.columns:
            logger.warning("[MacroSync] _merge_indicator | '%s' column not found in data, skipping merge", target_col)
            return merged
        if "period" not in df.columns:
            logger.warning(
                "[MacroSync] _merge_indicator | 'period' column not found in %s data, skipping merge",
                target_col,
            )
            return merged

        indicator = df[["period", target_col]].drop_duplicates(subset=["period"])  # type: ignore[untyped]
        if merged is not None:
            return merged.merge(indicator, on="period", how="outer")
        return indicator

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _sync_shibor_daily(self, result: typing.Any):
        try:
            latest = await self.dao.get_shibor_latest_date()
            today = await self._get_effective_trade_date()

            if not latest:
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = today - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
                    start_date=rough_start_date,
                    end_date=today,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (all_dates[0] if all_dates else (today - datetime.timedelta(days=365 * years)))
                )
            else:
                try:
                    last_dt = parse_date(latest)
                    start_date = last_dt.date() + datetime.timedelta(days=_SHIBOR_RESUME_OFFSET_DAYS)
                except ValueError:
                    logger.warning(
                        "[MacroSync] Invalid latest date '%s', fallback to 1 year.",
                        latest,
                    )
                    start_date = today - datetime.timedelta(days=_SHIBOR_FALLBACK_LOOKBACK_DAYS)

            if start_date > today:
                logger.debug("[MacroSync] Shibor already up to date.")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, "strftime") else str(start_date)
            end_str = today.strftime("%Y%m%d") if hasattr(today, "strftime") else str(today)
            df = await self.context.api.get_shibor(
                start_date=start_str,
                end_date=end_str,
            )

            # Phase 3G §4.3.4：同时拉取 LPR 数据，按 date 合并到同一 df 后入库
            # LPR 权限不足时降级为仅同步 shibor（与 shibor 同档位 points_120，通常一并可用）
            lpr_df: pd.DataFrame | None = None
            try:
                lpr_df = await self.context.api.get_shibor_lpr(
                    start_date=start_str,
                    end_date=end_str,
                )
            except TushareAPIPermissionError:
                logger.warning("[MacroSync] Shibor | ⛔ Permission denied for shibor_lpr API")
            except Exception as lpr_err:
                logger.debug("[MacroSync] Shibor | LPR fetch failed, continuing with shibor only: %s", lpr_err)

            if df is not None and not df.empty:
                if lpr_df is not None and not lpr_df.empty:
                    # how="left"：以 shibor 日频为主表，避免 LPR 缺失日引入 NaN 覆盖 shibor 列；
                    # LPR 为月频数据，仅在发布日对齐 shibor 行写入，其他日 lpr_1y/lpr_5y 为 NaN
                    # （_save_upsert 会将 NaN 转 None，因 macro_dao 未标记 null_protected，已有 LPR 值会被覆盖为 NULL）
                    # 故仅当 LPR date 与 shibor date 有交集时才合并（交集非空即合并），否则跳过 LPR merge
                    # 已知限制：LPR 发布日若为非工作日（周末），因 shibor 主表无该日行，该 LPR 数据会丢失；
                    # 下次同步时 LPR API 仍返回该日数据，但 shibor 主表仍无该日行，数据持续丢失。
                    # ceiling: 月频 LPR 单次丢失最多 1 条/月. upgrade: 改用独立 upsert 按 date 主键写入.
                    common_dates = set(df["date"]).intersection(set(lpr_df["date"]))
                    if common_dates:
                        df = df.merge(lpr_df, on="date", how="left")
                    else:
                        logger.debug("[MacroSync] Shibor | LPR dates do not intersect shibor dates, skipping LPR merge")
                count = await self.dao.save_shibor_daily(df)
                result.added += count if count else 0
                logger.debug("[MacroSync] Shibor | Saved %s records", count)
                await self.context.cache.update_sync_status(
                    "shibor_daily",
                    today,
                    count or 0,
                )

        except EngineDisposedError:
            raise
        except TushareAPIPermissionError:
            logger.warning("[MacroSync] Shibor | ⛔ Permission denied for shibor API")
            result.errors.append("Shibor: permission denied")
            try:
                today = await self._get_effective_trade_date()
                await self.context.cache.update_sync_status(
                    "shibor_daily",
                    today,
                    0,
                    status="skipped_permission",
                    last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                )
            except Exception as e:
                logger.debug("[MacroSync] Shibor | Failed to record skipped_permission status: %s", e, exc_info=True)
        except Exception as e:
            logger.warning("[MacroSync] Shibor | ⚠️ Error: %s", e, exc_info=True)
            result.errors.append(f"Shibor: {e}")

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _sync_index_weights(self, result: typing.Any):
        try:
            market_dao = self.context.cache.market_dao
            latest = await market_dao.get_latest_index_weight_date()

            today_date = await self._get_effective_trade_date()
            should_update = False

            if not latest:
                should_update = True
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = today_date - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
                    start_date=rough_start_date,
                    end_date=today_date,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (all_dates[0] if all_dates else (today_date - datetime.timedelta(days=365 * years)))
                )
            else:
                last_dt = parse_date(latest)
                last_date = last_dt.date() if hasattr(last_dt, "date") else last_dt
                if (today_date - last_date).days > 30:
                    should_update = True
                    start_date = last_date + datetime.timedelta(days=1)
                else:
                    start_date = today_date

            if not should_update:
                logger.debug("[MacroSync] Index weights up to date (monthly).")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, "strftime") else str(start_date)
            end_date = today_date.strftime("%Y%m%d")
            logger.debug(
                "[MacroSync] IndexWeight | Syncing %s indices...",
                len(MAJOR_INDICES),
            )

            iw_saved = 0
            for idx_code in MAJOR_INDICES:
                if self._cancelled:
                    break

                try:
                    df = await self.context.api.get_index_weight(
                        index_code=idx_code,
                        start_date=start_str,
                        end_date=end_date,
                    )

                    if df is not None and not df.empty:
                        count = await self.context.cache.save_index_weights(df)
                        if count:
                            iw_saved += count
                            result.added += count
                except EngineDisposedError:
                    raise
                except TushareAPIPermissionError:
                    logger.warning(
                        "[MacroSync] IndexWeight | ⛔ Permission denied for %s",
                        idx_code,
                    )
                except Exception as e:
                    logger.warning("[MacroSync] IndexWeight | ⚠️ Failed %s: %s", idx_code, e, exc_info=True)

            await self.context.cache.update_sync_status(
                "index_weight",
                today_date,
                iw_saved,
            )
            logger.debug("[MacroSync] IndexWeight | Total: %s records", iw_saved)

        except EngineDisposedError:
            raise
        except TushareAPIPermissionError:
            logger.warning(
                "[MacroSync] IndexWeight | ⛔ Permission denied",
            )
            try:
                today_date = await self._get_effective_trade_date()
                await self.context.cache.update_sync_status(
                    "index_weight",
                    today_date,
                    0,
                    status="skipped_permission",
                    last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                )
            except Exception as e:
                logger.debug(
                    "[MacroSync] IndexWeight | Failed to record skipped_permission status: %s", e, exc_info=True
                )
        except Exception as e:
            logger.warning("[MacroSync] IndexWeight | ⚠️ Flow-level error: %s", e, exc_info=True)
            result.errors.append(f"IndexWeight: {e}")
