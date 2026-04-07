import logging
import typing

import pandas as pd

from data.persistence.quality_gate import QualityTier, require_quality
from services.ai_service import AIService
from strategies.ai_mixin import AIStrategyMixin
from strategies.base_strategy import BaseStrategy, register_strategy
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


@register_strategy("ai_active")
class AISelectionStrategy(BaseStrategy, AIStrategyMixin):
    @property
    def required_history_days(self):
        from utils.config_handler import ConfigHandler

        return ConfigHandler.get_init_history_years() * 250

    def __init__(self):
        super().__init__("strategy_ai_active_name", "strategy_ai_active_desc")
        self.limit = ConfigHandler.get_ai_max_candidates()

    @require_quality(QualityTier.SILVER)
    async def filter(self, context: typing.Any):
        if context is None:
            return pd.DataFrame()

        # Support both keys (test uses screening_data, legacy uses data)
        df = context.get("screening_data")
        if df is None:
            df = context.get("data")

        # Fail fast if API not configured (test_ai_core compliance)
        ai_client = AIService()
        if not ai_client.is_cloud_available():
            raise ValueError("API Key missing or client not initialized")

        if df is None or df.empty:
            logger.warning("[AIStrategy] No data provided in context")
            return pd.DataFrame()

        # --- Step 1: Pre-Filter (The Sieve) ---
        # Rule: Listed, Profitable (PE>0), Active (Turnover > min)
        min_turnover = ConfigHandler.get_strategy_min_turnover()

        mask = (df["pe_ttm"] > 0) & (df["turnover_rate"] > min_turnover) & (df["list_status"] == "L")
        candidates = df[mask].copy()

        # Sort by turnover_rate desc (Most active), cap at limit
        candidates = candidates.sort_values("turnover_rate", ascending=False).head(
            self.limit,
        )

        if candidates.empty:
            return pd.DataFrame()

        # --- Step 2: AI Analysis via Mixin (with full data enrichment) ---
        return await self.run_ai_analysis(candidates, context)

    def get_ai_context(self, row: dict) -> str:
        """Strategy-specific context: explain WHY this stock was selected."""
        turnover = row.get("turnover_rate", "N/A")
        pe = row.get("pe_ttm", "N/A")
        pct_chg = row.get("pct_chg", "N/A")
        return f"该股票由 AI 主动策略筛选：换手率={turnover}%（高活跃度），PE(TTM)={pe}（盈利），日涨跌幅={pct_chg}%"
