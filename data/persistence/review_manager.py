import asyncio
import datetime
import json
import logging
import typing
import uuid

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.external.tushare_client import TushareClient
from data.persistence.daos.base_dao import EngineDisposedError
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date, to_date

logger = logging.getLogger(__name__)


class ReviewManager:
    """
    Manages the 'Verification' and 'Correction' phases of the AI loop.
    1. Calculates Actual Returns (T+1, T+5).
    2. Labels predictions (Win/Loss).
    3. Extracts 'Lessons' for Prompt Context.
    """

    def __init__(
        self,
        alpha_win_threshold: float = 0.5,
        alpha_loss_threshold: float = 0.5,
    ):
        self.cache = CacheManager()
        self.api = TushareClient()
        self.config = ConfigHandler()
        self.alpha_win_threshold = alpha_win_threshold
        self.alpha_loss_threshold = alpha_loss_threshold

    @log_async_operation(
        operation_name="t1_review",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run_review(self):
        """
        Main entry point: Review all pending predictions.
        Should be run daily after 16:00.
        """
        logger.info("[Review] Starting daily review...")

        pending_df = await self._get_pending_predictions()
        if pending_df.empty:
            logger.info("[Review] No pending predictions to review.")
            return

        updates: list[dict] = []

        all_codes = pending_df["ts_code"].unique().tolist()
        min_pred_date = str(pending_df["trade_date"].min())

        bulk_quotes = await self.cache.get_daily_quotes(
            ts_code_list=all_codes,
            start_date=min_pred_date,
        )
        if bulk_quotes is None or bulk_quotes.empty:
            logger.warning("[Review] Bulk quotes fetch returned empty.")
            return
        quotes_by_code = {code: group.sort_values("trade_date") for code, group in bulk_quotes.groupby("ts_code")}

        index_code = ConfigHandler.get_config("benchmark_index", "000001.SH")
        index_cache: dict[str, float | None] = {}

        try:
            max_quote_date = str(bulk_quotes["trade_date"].max())
            df_index_bulk = await self.cache.get_index_daily_range(
                ts_code_list=[index_code],
                start_date=min_pred_date,
                end_date=max_quote_date,
            )
            if df_index_bulk is not None and not df_index_bulk.empty:
                for _, i_row in df_index_bulk.iterrows():
                    dt_val = i_row["trade_date"]
                    if hasattr(dt_val, "strftime"):
                        dt_str = dt_val.strftime("%Y%m%d")
                    else:
                        dt_str = str(dt_val).replace("-", "")[:8]
                    raw_pct = i_row.get("pct_chg")
                    index_cache[dt_str] = float(raw_pct) if raw_pct is not None and pd.notna(raw_pct) is True else None
                logger.info(
                    "[Review] Bulk loaded %d days of index data for %s.",
                    len(df_index_bulk),
                    index_code,
                )
        except asyncio.CancelledError:
            logger.warning("[Review] Cancelled during index bulk pre-fetch.")
            raise
        except Exception as exc:
            error_info = classify_error(exc, context="db")
            severity = classify_severity(exc, context="db")
            if severity == "system":
                logger.critical(
                    "[Review] SYSTEM-LEVEL failure in index bulk pre-fetch (%s): %s",
                    error_info["code"],
                    exc,
                    exc_info=True,
                )
                raise
            logger.warning(
                "[Review] Failed to bulk pre-fetch index quotes (%s): %s",
                error_info["code"],
                exc,
            )

        for _, row in pending_df.iterrows():
            ts_code = row["ts_code"]
            pred_date = row["trade_date"]

            df_quotes = quotes_by_code.get(ts_code)
            if df_quotes is None or df_quotes.empty:
                continue

            try:
                t0_row = df_quotes[df_quotes["trade_date"].astype(object) == pred_date]
                if t0_row.empty:
                    continue

                t0_idx = int(df_quotes.index.get_loc(t0_row.index[0]))  # type: ignore[arg-type]
                t1_pct: float | None = None
                t1_price: float | None = None
                t5_pct: float | None = None
                t5_price: float | None = None
                t1_row = None
                if len(df_quotes) > t0_idx + 1:
                    t1_row = df_quotes.iloc[t0_idx + 1]
                    raw_pct = t1_row["pct_chg"]
                    if bool(pd.notna(raw_pct)):
                        t1_pct = float(raw_pct)
                    if "close" in t1_row.index and bool(pd.notna(t1_row["close"])):
                        t1_price = float(t1_row["close"])

                if len(df_quotes) > t0_idx + 5:
                    t5_row = df_quotes.iloc[t0_idx + 5]
                    if "close" in t5_row.index and bool(pd.notna(t5_row["close"])):
                        t5_price = float(t5_row["close"])
                        t0_close = t0_row.iloc[0].get("close")
                        if bool(pd.notna(t0_close)) and float(t0_close) != 0:
                            t5_pct = (t5_price / float(t0_close) - 1.0) * 100.0

                if t1_pct is not None and t1_row is not None:
                    t1_date_val = t1_row["trade_date"]
                    if hasattr(t1_date_val, "date") and callable(t1_date_val.date):
                        t1_date_obj: datetime.date = typing.cast(datetime.date, t1_date_val.date())
                    elif hasattr(t1_date_val, "year"):
                        t1_date_obj = t1_date_val
                    else:
                        t1_date_obj = datetime.datetime.strptime(str(t1_date_val).replace("-", "")[:8], "%Y%m%d").date()
                    trade_date_str = t1_date_obj.strftime("%Y%m%d")

                    if trade_date_str not in index_cache:
                        try:
                            df_idx = await self.cache.get_index_daily(ts_code=index_code, trade_date=t1_date_obj)
                            if df_idx is not None and not df_idx.empty:
                                raw_pct = df_idx.iloc[0]["pct_chg"]
                                index_cache[trade_date_str] = float(raw_pct) if pd.notna(raw_pct) is True else None
                            else:
                                try:
                                    df_idx_api = await self.api.get_index_daily(
                                        ts_code=index_code,
                                        start_date=trade_date_str,
                                        end_date=trade_date_str,
                                    )
                                    if df_idx_api is not None and not df_idx_api.empty:
                                        raw_pct = df_idx_api.iloc[0]["pct_chg"]
                                        index_cache[trade_date_str] = (
                                            float(raw_pct) if pd.notna(raw_pct) is True else None
                                        )
                                    else:
                                        index_cache[trade_date_str] = None
                                except (ValueError, TypeError, KeyError):
                                    index_cache[trade_date_str] = None
                        except Exception as exc:
                            error_info = classify_error(exc, context="db")
                            severity = classify_severity(exc, context="db")
                            if severity == "system":
                                logger.critical(
                                    "[Review] SYSTEM-LEVEL failure in cache index lookup for %s on %s (%s): %s",
                                    index_code,
                                    trade_date_str,
                                    error_info["code"],
                                    exc,
                                    exc_info=True,
                                )
                                raise
                            logger.warning(
                                "[Review] Cache index lookup failed for %s on %s (%s): %s",
                                index_code,
                                trade_date_str,
                                error_info["code"],
                                exc,
                            )
                            index_cache[trade_date_str] = None

                    index_pct = index_cache.get(trade_date_str)

                    if index_pct is None:
                        logger.warning(
                            "[Review] %s: Index return unavailable for %s, skipping to avoid label pollution",
                            ts_code,
                            trade_date_str,
                        )
                        continue

                    alpha = t1_pct - index_pct

                    label = "DRAW"
                    if alpha > self.alpha_win_threshold:
                        label = "WIN"
                    elif alpha < -self.alpha_loss_threshold:
                        label = "LOSS"

                    updates.append(
                        {
                            "record_id": row["id"],
                            "pct": t1_pct,
                            "label": label,
                            "index_pct": index_pct,
                            "t1_price": t1_price,
                            "t5_pct": t5_pct,
                            "t5_price": t5_price,
                            "alpha": alpha,
                        }
                    )
                    logger.info(
                        "[Review] %s: Stock %s%% vs Index %s%% = Alpha %.2f%% -> %s, T+5=%s",
                        ts_code,
                        t1_pct,
                        index_pct,
                        alpha,
                        label,
                        t5_pct if t5_pct is not None else "N/A",
                    )

            except Exception as e:
                logger.error("[Review] Error reviewing %s: %s", ts_code, e)

        if updates:
            await self._batch_update_results(updates)

        logger.info("[Review] Completed. Updated %s records.", len(updates))

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_pending_predictions(self):
        """
        Get predictions from last 10 trade days that have no result yet.
        Uses trade calendar for accurate lookback instead of natural days.
        """
        try:
            end_date = await self.cache.get_latest_trade_date()
            if not end_date:
                date_threshold = (get_now() - datetime.timedelta(days=14)).date()
            else:
                end_dt = parse_date(str(end_date))
                start_dt = end_dt - datetime.timedelta(days=30)
                trade_cal_df = await self.cache.get_trade_cal(
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=end_dt.strftime("%Y%m%d"),
                    is_open="1",
                )
                if trade_cal_df is not None and not trade_cal_df.empty and len(trade_cal_df) >= 10:
                    date_threshold = trade_cal_df.iloc[-10]["cal_date"]
                elif trade_cal_df is not None and not trade_cal_df.empty:
                    date_threshold = trade_cal_df.iloc[0]["cal_date"]
                else:
                    date_threshold = (get_now() - datetime.timedelta(days=14)).date()

            if date_threshold is not None:
                date_threshold = to_date(date_threshold)
            return await self.cache.screener_dao.get_pending_predictions(date_threshold)  # type: ignore[union-attr]

        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(
                    "[Review] SYSTEM-LEVEL error fetching pending predictions (%s): %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
                raise
            logger.error(
                "[Review] Error fetching pending predictions (%s): %s",
                error_info["code"],
                e,
            )
            return pd.DataFrame()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_learning_context(self, limit: int | None = 3, as_of: datetime.date | datetime.datetime | None = None):
        """
        Extract 'Best Wins' and 'Worst Losses' for Prompt Injection.
        Returns formatted XML string for few-shot learning.

        P0-5 fix: as_of parameter prevents look-ahead bias. When provided,
        only predictions with trade_date < as_of are included, preventing
        future data from leaking into historical replay contexts.

        Corner cases:
        - No history: Returns minimal XML
        - All wins/no losses: Handles gracefully
        - DB errors: Returns empty context (non-blocking)
        """
        if as_of is not None and isinstance(as_of, datetime.datetime):
            as_of = as_of.date()
        if as_of is None:
            logger.warning(
                "[ReviewManager] get_learning_context called without as_of; "
                "using all completed samples. This may introduce look-ahead bias in backtest scenarios."
            )
        wins = []
        losses = []

        try:
            df_wins = await self.cache.screener_dao.get_learning_context(
                limit=limit or 3,
                is_win=True,
                as_of=as_of,
            )
            if df_wins is not None and not df_wins.empty:
                for _, row in df_wins.iterrows():
                    wins.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "alpha": row["alpha"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore[union-attr]
                            else "",
                        },
                    )

            df_losses = await self.cache.screener_dao.get_learning_context(
                limit=limit or 3,
                is_win=False,
                as_of=as_of,
            )
            if df_losses is not None and not df_losses.empty:
                for _, row in df_losses.iterrows():
                    losses.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "alpha": row["alpha"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore[union-attr]
                            else "",
                        },
                    )

        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(
                    "[Review] SYSTEM-LEVEL error fetching learning context (%s): %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
                raise
            logger.warning(
                "[Review] Error fetching learning context (%s): %s",
                error_info["code"],
                e,
            )
            # Non-blocking: return empty context on error

        # Build XML
        xml = "<history_context>\n"

        if wins:
            xml += f"  [{I18n.get('review_ctx_positive')}]\n"
            for w in wins:
                alpha_str = f"{w['alpha']:+.1f}"
                pct_str = f"{w['pct']:+.1f}"
                reason = w["reason"] or I18n.get("review_ctx_no_reason")
                xml += f"  - {I18n.get('review_ctx_win_detail', code=w['code'], name=w['name'], alpha=alpha_str, pct=pct_str, reason=reason)}\n"

        if losses:
            xml += f"  [{I18n.get('review_ctx_negative')}]\n"
            for loss in losses:
                alpha_str = f"{loss['alpha']:+.1f}"
                pct_str = f"{loss['pct']:+.1f}"
                reason = loss["reason"] or I18n.get("review_ctx_no_reason")
                xml += f"  - {I18n.get('review_ctx_loss_detail', code=loss['code'], name=loss['name'], alpha=alpha_str, pct=pct_str, reason=reason)}\n"

        if not wins and not losses:
            xml += f"  {I18n.get('review_ctx_none')}\n"

        xml += "</history_context>"
        return xml

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _batch_update_results(self, updates: list[dict]):
        """Update all review results within a single transaction."""
        dao = self.cache.screener_dao
        engine = self.cache.engine
        if engine is None:
            logger.error("[Review] Engine not available for batch update.")
            return

        try:
            async with engine.begin() as conn:
                for u in updates:
                    await dao.update_prediction_result(
                        u["record_id"],
                        u["pct"],
                        u["label"],
                        t1_price=u["t1_price"],
                        t5_pct=u["t5_pct"],
                        t5_price=u["t5_price"],
                        index_pct=u["index_pct"],
                        alpha=u["alpha"],
                        conn=conn,
                    )
        except EngineDisposedError:
            # R5 一致性：disposed 引擎不可恢复，必须上抛避免被吞没（news_subscription_service 是停止后台循环策略，此处为同步调用路径需上抛）.
            raise
        except Exception as e:
            logger.error("[Review] Batch update failed, falling back to individual updates: %s", e)
            for u in updates:
                try:
                    await self._update_result(
                        u["record_id"],
                        u["pct"],
                        u["label"],
                        index_pct=u["index_pct"],
                        t1_price=u["t1_price"],
                        t5_pct=u["t5_pct"],
                        t5_price=u["t5_price"],
                        alpha=u["alpha"],
                    )
                except EngineDisposedError:
                    # R5 一致性： disposed 引擎不可恢复，fallback 路径同样必须上抛（与主路径对齐）.
                    raise
                except Exception as inner_e:
                    logger.error("[Review] Individual update also failed for record %s: %s", u["record_id"], inner_e)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _update_result(
        self,
        record_id: typing.Any,
        pct: typing.Any,
        label: typing.Any,
        index_pct: typing.Any = None,
        t1_price: typing.Any = None,
        t5_pct: typing.Any = None,
        t5_price: typing.Any = None,
        alpha: typing.Any = None,
        review_status: typing.Any = None,
    ):
        """Update DB with T+1/T+5 review metrics and review_status."""
        await self.cache.screener_dao.update_prediction_result(
            record_id,
            pct,
            label,
            t1_price=t1_price,
            t5_pct=t5_pct,
            t5_price=t5_price,
            index_pct=index_pct,
            alpha=alpha,
            review_status=review_status,
        )

    @staticmethod
    def _normalize_trade_date(value: typing.Any) -> datetime.date:
        """Normalize supported trade_date input types to datetime.date."""
        return to_date(value)

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def save_results(
        self,
        strategy_name: str | None,
        df: pd.DataFrame,
        trade_date: datetime.date | datetime.datetime | pd.Timestamp | str | None = None,
        run_id: str | None = None,
        params_snapshot: str | dict[str, typing.Any] | None = None,
    ):
        """
        Save screening results to history for future review.
        Persists the full strategy execution snapshot including financial indicators and AI thinking.

        Args:
            strategy_name: Name of the strategy that produced the results.
            df: DataFrame of screening results.
            trade_date: The trading date being analyzed (not the current natural date).
                        If omitted, a single unique df["trade_date"] value may be used.
        """
        if df is None or df.empty:
            return

        effective_date = self._normalize_trade_date(trade_date) if trade_date is not None else None

        df_trade_date = None
        if "trade_date" in df.columns:
            normalized_dates = {self._normalize_trade_date(v) for v in df["trade_date"].dropna().unique().tolist()}
            if len(normalized_dates) > 1:
                raise ValueError("save_results received multiple trade_date values in result dataframe")
            if normalized_dates:
                df_trade_date = next(iter(normalized_dates))

        if effective_date is None and df_trade_date is not None:
            effective_date = df_trade_date
        elif effective_date is not None and df_trade_date is not None and effective_date != df_trade_date:
            raise ValueError(
                f"save_results trade_date mismatch: arg={effective_date} df={df_trade_date}",
            )

        if effective_date is None:
            raise ValueError(
                "save_results requires an analysis trade_date or a single unique df['trade_date'] value",
            )

        if run_id is None:
            run_id = uuid.uuid4().hex[:16]

        if params_snapshot is None:
            params_snapshot_value = None
        elif isinstance(params_snapshot, dict):
            params_snapshot_value = params_snapshot
        else:
            try:
                params_snapshot_value = (
                    json.loads(params_snapshot) if isinstance(params_snapshot, str) else params_snapshot
                )
            except (json.JSONDecodeError, TypeError):
                params_snapshot_value = {"raw": str(params_snapshot)}

        # Helpers to safely extract fields
        def _f(row_data: typing.Any, key: typing.Any, default: typing.Any = None):
            v = row_data.get(key, default)
            if pd.isnull(v):
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _s(row_data: typing.Any, key: typing.Any, default: typing.Any = ""):
            v = row_data.get(key, default)
            if pd.isnull(v):
                return default
            return str(v)

        records = []
        for _, row in df.iterrows():
            ts_code = row.get("ts_code")
            if not ts_code:
                continue

            ai_score = row.get("ai_score", 0)
            try:
                ai_score = int(ai_score) if pd.notnull(ai_score) else 0  # type: ignore[union-attr]
            except (ValueError, TypeError):
                ai_score = 0

            ai_reason = row.get("ai_reason", "")
            if pd.isnull(ai_reason):  # type: ignore[union-attr]
                ai_reason = ""

            thinking = row.get("thinking", "")
            if pd.isnull(thinking):  # type: ignore[union-attr]
                thinking = ""

            records.append(
                {
                    "run_id": run_id,
                    "trade_date": effective_date,
                    "strategy_name": strategy_name,
                    "ts_code": ts_code,
                    "name": _s(row, "name"),
                    "close": _f(row, "close"),
                    "pct_chg": _f(row, "pct_chg"),
                    "industry": _s(row, "industry"),
                    "vol": _f(row, "vol"),
                    "amount": _f(row, "amount"),
                    "turnover_rate": _f(row, "turnover_rate"),
                    "pe_ttm": _f(row, "pe_ttm"),
                    "pb": _f(row, "pb"),
                    "ps_ttm": _f(row, "ps_ttm"),
                    "dv_ttm": _f(row, "dv_ttm"),
                    "total_mv": _f(row, "total_mv"),
                    "circ_mv": _f(row, "circ_mv"),
                    "roe": _f(row, "roe"),
                    "grossprofit_margin": _f(row, "grossprofit_margin"),
                    "debt_to_assets": _f(row, "debt_to_assets"),
                    "or_yoy": _f(row, "or_yoy"),
                    "netprofit_yoy": _f(row, "netprofit_yoy"),
                    "ai_score": ai_score,
                    "ai_reason": str(ai_reason),
                    "thinking": str(thinking),
                    "params_snapshot": params_snapshot_value,
                }
            )

        if not records:
            return

        await self.cache.screener_dao.save_screening_results(records)
        logger.info("[Review] Saved %s predictions for %s", len(records), strategy_name)
