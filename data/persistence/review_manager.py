import datetime
import hashlib
import json
import logging
import typing

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.external.tushare_client import TushareClient
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class ReviewManager:
    """
    Manages the 'Verification' and 'Correction' phases of the AI loop.
    1. Calculates Actual Returns (T+1, T+5).
    2. Labels predictions (Win/Loss).
    3. Extracts 'Lessons' for Prompt Context.
    """

    def __init__(self):
        self.cache = CacheManager()
        self.api = TushareClient()
        self.config = ConfigHandler()

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

        # 1. Get all recent predictions without results
        pending_df = await self._get_pending_predictions()
        if pending_df.empty:
            logger.info("[Review] No pending predictions to review.")
            return

        updated_count = 0

        # 2. Check each prediction
        for _, row in pending_df.iterrows():
            ts_code = row["ts_code"]
            pred_date = row["trade_date"]  # The date the prediction was made (Close)

            # We need prices for T+1, T+2...
            # Get next trading days from Tushare
            # Since we assume 'pred_date' is the date of analysis (after close),
            # T+1 is the NEXT trading day.

            # Fetch prices since pred_date
            df_quotes = await self.cache.get_daily_quotes(
                start_date=pred_date,
                ts_code=ts_code,
            )
            if df_quotes.empty:
                # Try fetching from API if not in cache (e.g. today's close)
                # In a real system, we assume sync_daily_market_snapshot has run.
                continue

            df_quotes = df_quotes.sort_values("trade_date")

            # Identify T+0 (Analysis Day), T+1, T+2...
            # Note: df_quotes includes pred_date (T+0)

            try:
                # Find the index of prediction date
                t0_row = df_quotes[df_quotes["trade_date"].astype(object) == pred_date]
                if t0_row.empty:
                    continue

                t0_idx = df_quotes.index.get_loc(t0_row.index[0])

                # Check T+1
                t1_pct: float | None = None
                if len(df_quotes) > t0_idx + 1:  # type: ignore
                    t1_row = df_quotes.iloc[t0_idx + 1]  # type: ignore
                    t1_pct = float(t1_row["pct_chg"])

                # Check T+5 (optional, simpler logic here just for T+1 focus first)

                if t1_pct is not None:
                    # Determine Result (Relative Return)
                    # We need Index Return for this date to calculate Alpha.
                    # Default benchmark: 000300.SH (CSI 300) or 000001.SH (Shanghai Composite)
                    index_code = ConfigHandler.get_config(
                        "benchmark_index",
                        "000001.SH",
                    )

                    # Fetch Index Quote for T+1
                    # Since we don't cache index daily quotes in the same efficient way yet (or handled by quotes table?),
                    # We might need to fetch it dynamically or ensure we sync benchmarks.
                    # For now, let's fetch on demand via API if missing.

                    index_pct = 0.0
                    try:
                        t1_date_val = t1_row["trade_date"]
                        if hasattr(t1_date_val, "strftime"):
                            trade_date = t1_date_val.strftime("%Y%m%d")
                        else:
                            trade_date = str(t1_date_val).replace("-", "")
                        df_idx = await self.api.get_index_daily(
                            ts_code=index_code,
                            start_date=trade_date,
                            end_date=trade_date,
                        )
                        if df_idx is not None and not df_idx.empty:
                            index_pct = float(df_idx.iloc[0]["pct_chg"])
                    except Exception:
                        pass  # Network fail, assume 0 benchmark

                    # Alpha Calculation
                    alpha = t1_pct - index_pct

                    label = "DRAW"
                    # Win Condition: Alpha > 0 (Outperform Marker) AND Absolute > -2% (Avoid disaster)
                    # Strict: Must make money OR outperform significantly

                    if alpha > 0.5:
                        label = "WIN"
                    elif alpha < -0.5:
                        label = "LOSS"

                    # Log it
                    await self._update_result(row["id"], t1_pct, label, index_pct)
                    updated_count += 1
                    logger.info(
                        f"[Review] {ts_code}: Stock {t1_pct}% vs Index {index_pct}% = Alpha {alpha:.2f}% -> {label}",
                    )

            except Exception as e:
                logger.error(f"[Review] Error reviewing {ts_code}: {e}")

        logger.info(f"[Review] Completed. Updated {updated_count} records.")

    async def _get_pending_predictions(self):
        """
        Get predictions from last 10 days that have no result yet.
        Corner cases handled:
        - Empty DB: Returns empty DataFrame
        - Missing columns: Uses safe column access
        - Date edge cases: Uses 10-day lookback window
        """
        date_threshold = (get_now() - datetime.timedelta(days=10)).date()

        try:
            return await self.cache.screener_dao.get_pending_predictions(date_threshold)  # type: ignore

        except Exception as e:
            logger.error(f"[Review] Error fetching pending predictions: {e}")
            return pd.DataFrame()

    async def get_learning_context(self, limit: int | None = 3):
        """
        Extract 'Best Wins' and 'Worst Losses' for Prompt Injection.
        Returns formatted XML string for few-shot learning.

        Corner cases:
        - No history: Returns minimal XML
        - All wins/no losses: Handles gracefully
        - DB errors: Returns empty context (non-blocking)
        """
        wins = []
        losses = []

        try:
            df_wins = await self.cache.screener_dao.get_learning_context(
                limit=limit,
                is_win=True,
            )
            if df_wins is not None and not df_wins.empty:
                for _, row in df_wins.iterrows():
                    wins.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore
                            else "",
                        },
                    )

            df_losses = await self.cache.screener_dao.get_learning_context(
                limit=limit,
                is_win=False,
            )
            if df_losses is not None and not df_losses.empty:
                for _, row in df_losses.iterrows():
                    losses.append(
                        {
                            "code": row["ts_code"],
                            "name": row["name"],
                            "pct": row["t1_pct"],
                            "score": row["ai_score"],
                            "reason": str(row["ai_reason"])[:50]
                            if row["ai_reason"]  # type: ignore
                            else "",
                        },
                    )

        except Exception as e:
            logger.warning(f"[Review] Error fetching learning context: {e}")
            # Non-blocking: return empty context on error

        # Build XML
        xml = "<history_context>\n"

        if wins:
            xml += "  [复盘参考 - 正向样本]\n"
            for w in wins:
                xml += f"  - {w['code']} ({w['name']}): 次日 {w['pct']:+.1f}%，当时归因摘要: {w['reason'] or '无'}\n"

        if losses:
            xml += "  [复盘参考 - 负向样本]\n"
            for loss in losses:
                xml += f"  - {loss['code']} ({loss['name']}): 次日 {loss['pct']:+.1f}%，当时归因摘要: {loss['reason'] or '无'}\n"

        if not wins and not losses:
            xml += "  暂无可用历史复盘样本。\n"

        xml += "</history_context>"
        return xml

    async def _update_result(
        self,
        record_id: typing.Any,
        pct: typing.Any,
        label: typing.Any,
        index_pct: typing.Any = 0.0,
    ):
        """Update DB with result. index_pct reserved for future alpha storage."""
        await self.cache.screener_dao.update_prediction_result(record_id, pct, label)

    @staticmethod
    def _normalize_trade_date(value: typing.Any) -> datetime.date:
        """Normalize supported trade_date input types to datetime.date."""
        if isinstance(value, pd.Timestamp):
            return value.date()
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        if isinstance(value, str):
            raw = value.strip()
            for fmt in ("%Y%m%d", "%Y-%m-%d"):
                try:
                    return datetime.datetime.strptime(raw, fmt).date()
                except ValueError:
                    continue
        raise ValueError(f"Unsupported trade_date value: {value!r}")

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
            seed = f"{effective_date.isoformat()}|{strategy_name or ''}"
            run_id = hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]

        if params_snapshot is None:
            params_snapshot_str = None
        elif isinstance(params_snapshot, str):
            params_snapshot_str = params_snapshot
        else:
            params_snapshot_str = json.dumps(params_snapshot, ensure_ascii=False, sort_keys=True)

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

            # Extract AI fields with NaN safety
            ai_score = row.get("ai_score", 0)
            try:
                ai_score = int(ai_score) if pd.notnull(ai_score) else 0  # type: ignore
            except (ValueError, TypeError):
                ai_score = 0

            ai_reason = row.get("ai_reason", "")
            if pd.isnull(ai_reason):  # type: ignore
                ai_reason = ""

            thinking = row.get("thinking", "")
            if pd.isnull(thinking):  # type: ignore
                thinking = ""

            records.append(
                (
                    run_id,
                    effective_date,
                    strategy_name,
                    ts_code,
                    _s(row, "name"),
                    _f(row, "close"),
                    _f(row, "pct_chg"),
                    # Market data snapshot
                    _s(row, "industry"),
                    _f(row, "vol"),
                    _f(row, "amount"),
                    _f(row, "turnover_rate"),
                    # Valuation snapshot
                    _f(row, "pe_ttm"),
                    _f(row, "pb"),
                    _f(row, "ps_ttm"),
                    _f(row, "dv_ttm"),
                    _f(row, "total_mv"),
                    _f(row, "circ_mv"),
                    # Financial snapshot
                    _f(row, "roe"),
                    _f(row, "grossprofit_margin"),
                    _f(row, "debt_to_assets"),
                    _f(row, "or_yoy"),
                    _f(row, "netprofit_yoy"),
                    # AI analysis
                    ai_score,
                    str(ai_reason),
                    str(thinking),
                    params_snapshot_str,
                ),
            )

        if not records:
            return

        await self.cache.screener_dao.save_screening_results(records)
        logger.info(f"[Review] Saved {len(records)} predictions for {strategy_name}")
