import pandas as pd
import polars as pl
import logging
import datetime
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.ai_mixin import AIStrategyMixin
from utils.technical_analysis import TechnicalAnalysis
from utils.config_handler import ConfigHandler
from data.quality_gate import require_quality, QualityTier, QualityGateError
from ui.i18n import I18n

logger = logging.getLogger(__name__)


@register_strategy("oversold")
class OversoldStrategy(BaseStrategy, AIStrategyMixin):
    """
    RSI Oversold Rebound Strategy (AI-Enhanced)
    
    Level 1 (Math):  Filter stocks with RSI(N) < threshold (both configurable via UI).
    Level 2 (AI):    Parallel AI analysis on top candidates to distinguish
                     "golden pit" rebounds from "falling knife" traps.
    """
    @property
    def required_history_days(self):
        from utils.config_handler import ConfigHandler
        return ConfigHandler.get_init_history_years() * 250

    def __init__(self):
        super().__init__("strategy_oversold_name", "strategy_oversold_desc")

    # ============================================================
    # Dynamic Parameters — exposed to UI as a slider
    # ============================================================
    def get_parameters(self):
        return [
            {
                "name": "rsi_period",
                "label_key": "param_rsi_period",
                "type": "slider",
                "min": 2,
                "max": 30,
                "default": 14,
                "step": 1,
            },
            {
                "name": "rsi_threshold",
                "label_key": "param_rsi_threshold_oversold",
                "type": "slider",
                "min": 0,
                "max": 100,
                "default": 30,
                "step": 1,
            }
        ]

    def get_dynamic_description(self, current_params: dict) -> str:
        """Return description matching current slider params."""
        period = current_params.get('rsi_period', 14)
        threshold = current_params.get('rsi_threshold', 30)
        return I18n.get(
            "strategy_oversold_dynamic_desc",
            f"RSI({period}) < {threshold}"
        ).format(period=period, threshold=threshold)

    # ============================================================
    # AI Context Hook — tells the LLM WHY this stock was selected
    # ============================================================
    def get_ai_context(self, row: dict) -> str:
        period = row.get('_rsi_period', 14)
        rsi = row.get(f'rsi_{period}', 'N/A')
        threshold = row.get('_rsi_threshold', 30)
        return (
            f"This stock was selected by the RSI Oversold Rebound strategy. "
            f"Its current RSI({period}) = {rsi} (threshold < {threshold}), "
            f"indicating extreme oversold conditions. "
            f"Please evaluate: is this a 'golden pit' rebound opportunity "
            f"or a fundamental deterioration causing a bottomless decline?"
        )

    # ============================================================
    # Main Filter Logic
    # ============================================================
    @require_quality(QualityTier.SILVER)
    async def filter(self, context):
        """
        Two-phase filtering:
        Phase 1: RSI math filter (Polars) → top N oversold candidates
        Phase 2: AI analysis (via Mixin) → scored and ranked results
        """
        # --- Read dynamic params from UI (with fallback) ---
        params = context.get('params', {})
        rsi_period = params.get('rsi_period', 14)
        rsi_threshold = params.get('rsi_threshold', 30)
        
        # --- Phase 1: Math Filter ---
        candidates = await self._math_filter(context, rsi_period, rsi_threshold)
        
        if candidates is None or candidates.empty:
            return pd.DataFrame()

        # Inject thresholds into each row so get_ai_context() can use them
        candidates['_rsi_period'] = rsi_period
        candidates['_rsi_threshold'] = rsi_threshold

        logger.info(f"[OversoldStrategy] Phase 1 complete: {len(candidates)} candidates (RSI < {rsi_threshold})")

        # --- Phase 2: AI Analysis (via Mixin) ---
        return await self.run_ai_analysis(candidates, context)

    async def _math_filter(self, context, rsi_period, rsi_threshold):
        """
        Phase 1: Pure mathematical RSI filtering using Polars.
        Returns a Pandas DataFrame of candidates sorted by RSI ascending.
        """
        snapshot_df = context.get('screening_data')
        dp = context.get('data_processor')

        if snapshot_df is None or snapshot_df.empty:
            logger.warning("[OversoldStrategy] No snapshot data available.")
            return pd.DataFrame()

        if dp is None:
            logger.error("[OversoldStrategy] DataProcessor not found in context.")
            return pd.DataFrame()

        # Prepare Date Range
        # RSI needs at least N+1 days; EMA initialization needs even more for smooth stability.
        # Fetching 120 days ensures even a 30-day RSI is fully stabilized.
        end_date = await dp.cache.get_latest_trade_date()
        if not end_date:
            logger.warning("[OversoldStrategy] No trade date found in database cache. Is the DB initialized?")
            return pd.DataFrame()
            
        start_date = (datetime.datetime.strptime(end_date, "%Y%m%d") - datetime.timedelta(days=120)).strftime("%Y%m%d")

        logger.info(f"[OversoldStrategy] Fetching history {start_date} → {end_date} for RSI calculation...")

        try:
            valid_codes = snapshot_df['ts_code'].tolist()
            history_pdf = await dp.cache.get_daily_quotes(
                start_date=start_date, end_date=end_date, ts_code_list=valid_codes
            )

            if history_pdf is None or history_pdf.empty:
                logger.warning("[OversoldStrategy] No historical data found.")
                return pd.DataFrame()

            # Fix: Polars requires pyarrow for pandas 'Int64' (nullable int).
            # Cast to float64 to bypass pyarrow dependency.
            for col in history_pdf.select_dtypes(include=['Int64']).columns:
                history_pdf[col] = history_pdf[col].astype('float64')

            df = pl.from_pandas(history_pdf)

            # CRITICAL: Must sort chronologically BEFORE any window operations like .last()
            # because the underlying SQL query (get_daily_quotes) does not guarantee ORDER BY.
            df_lazy = df.lazy().sort(["ts_code", "trade_date"])

            # Calculate QFQ Close (前复权收盘价)
            if 'adj_factor' in df.columns:
                qfq_expr = (
                    pl.col('close') *
                    (pl.col('adj_factor') / pl.col('adj_factor').last().over('ts_code'))
                ).alias('qfq_close')
                df_lazy = df_lazy.with_columns(qfq_expr)
            else:
                df_lazy = df_lazy.with_columns(pl.col('close').alias('qfq_close'))

            # Calculate Dynamic RSI
            rsi_col_name = f'rsi_{rsi_period}'
            rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name='qfq_close', period=rsi_period, alias=rsi_col_name)

            result_df = (
                df_lazy
                .with_columns([
                    rsi_expr.over("ts_code"),
                    pl.col("close").count().over("ts_code").alias("day_count")
                ])
                .filter(pl.col("trade_date") == end_date)           # Latest day only
                .filter(pl.col("day_count") >= rsi_period * 2)      # Stability guard
                .filter(pl.col(rsi_col_name) < rsi_threshold)       # Dynamic threshold
                .collect()
            )

            if result_df.height == 0:
                logger.info("[OversoldStrategy] No stocks found matching RSI criteria.")
                return pd.DataFrame()

            # Join with snapshot
            rsi_pdf = result_df.select(['ts_code', rsi_col_name]).to_pandas()
            final_df = pd.merge(snapshot_df, rsi_pdf, on='ts_code', how='inner')

            # Sort by RSI ascending (most oversold first)
            return final_df.sort_values(rsi_col_name, ascending=True)

        except QualityGateError:
            raise
        except Exception as e:
            logger.error(f"[OversoldStrategy] Error during execution: {e}", exc_info=True)
            raise RuntimeError(f"Strategy internal error: {e}")
