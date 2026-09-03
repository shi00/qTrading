import logging

import pandas as pd

from data.persistence.quality_gate import QualityTier, require_quality
from strategies.ai_mixin import AIStrategyMixin
from strategies.utils import StrategyContext
from strategies.base_strategy import BaseStrategy, register_strategy
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation

logger = logging.getLogger(__name__)


@register_strategy("ai_active")
class AISelectionStrategy(BaseStrategy, AIStrategyMixin):
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes", "daily_indicators")

    @property
    def required_history_days(self):
        return ConfigHandler.get_init_history_years() * 250

    def __init__(self):
        super().__init__("strategy_ai_active_name", "strategy_ai_active_desc")
        self.limit = ConfigHandler.get_ai_max_candidates()

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    @require_quality(QualityTier.SILVER)
    async def filter(self, context: StrategyContext):
        if context is None:
            return pd.DataFrame()

        dep_result = self.check_dependencies(context)
        if dep_result["status"] == "unready":
            logger.warning(
                "[Strategy] %s: dependencies unready, missing_keys=%s, missing_tables=%s",
                self.name,
                dep_result["missing_keys"],
                dep_result["missing_tables"],
            )
            return pd.DataFrame()

        # Support both keys (test uses screening_data, legacy uses data)
        df = context.get("screening_data")
        if df is None:
            df = context.get("data")

        # Task 3.4: AI 不可用行为统一 — 不再 raise ValueError.
        # 改由 run_ai_analysis (ai_mixin) 统一处理 is_cloud_available() 检查,
        # 返回量化初筛结果 + on_progress 提示 "ai_not_configured".
        if df is None or df.empty:
            logger.warning("[AIStrategy] No data provided in context")
            return pd.DataFrame()

        # --- Step 1: Pre-Filter (The Sieve) ---
        # Rule: Profitable (PE>0), Active (Turnover > min)
        # DAT-01: 存活（Listed）过滤由 DAO 的 PIT 条件承担（as_of 时点判定），
        # 此处不再按当前 list_status 过滤，否则回测场景会把「已退市但 as_of 时仍存活」
        # 的股票二次滤除，重新引入生存者偏差。
        min_turnover = ConfigHandler.get_strategy_min_turnover()

        mask = (df["pe_ttm"] > 0) & (df["turnover_rate"] > min_turnover)
        candidates = df[mask].copy()

        # Sort by turnover_rate desc (Most active), cap at limit
        candidates = candidates.sort_values(by="turnover_rate", ascending=False).head(  # type: ignore[call-arg]
            self.limit,
        )

        if candidates.empty:
            return pd.DataFrame()

        # --- Step 2: AI Analysis via Mixin (with full data enrichment) ---
        return await self.run_ai_analysis(candidates, context)  # type: ignore[arg-type]

    def get_ai_context(self, row: dict) -> str:
        # NOTE: AI prompts are intentionally in Chinese (A-share analysis context).
        # Do NOT internationalize these strings — they are LLM prompts, not UI text.
        """Strategy-specific context: explain WHY this stock was selected."""
        turnover = row.get("turnover_rate", "N/A")
        pe = row.get("pe_ttm", "N/A")
        pct_chg = row.get("pct_chg", "N/A")
        return f"该股票由 AI 主动策略筛选：换手率={turnover}%（高活跃度），PE(TTM)={pe}（盈利），日涨跌幅={pct_chg}%"
