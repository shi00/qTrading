"""
Domain services module.

Provides unified service interfaces for data access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.domain_services.market_data_service import MarketDataService
    from data.domain_services.offline_calendar import OfflineCalendar
    from data.domain_services.review_stats_service import (
        MetricStat,
        SampleGrade,
        StrategyStatRow,
        compute_strategy_review_stats,
        grade_for,
    )
    from data.domain_services.trade_calendar_service import TradeCalendarService
    from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel


def __getattr__(name):
    if name == "MarketDataService":
        from data.domain_services.market_data_service import MarketDataService

        return MarketDataService
    if name == "OfflineCalendar":
        from data.domain_services.offline_calendar import OfflineCalendar

        return OfflineCalendar
    if name == "TradeCalendarService":
        from data.domain_services.trade_calendar_service import TradeCalendarService

        return TradeCalendarService
    if name == "TransactionCostModel":
        from data.domain_services.transaction_cost import TransactionCostModel

        return TransactionCostModel
    if name == "TransactionCostConfig":
        from data.domain_services.transaction_cost import TransactionCostConfig

        return TransactionCostConfig
    if name == "MetricStat":
        from data.domain_services.review_stats_service import MetricStat

        return MetricStat
    if name == "SampleGrade":
        from data.domain_services.review_stats_service import SampleGrade

        return SampleGrade
    if name == "StrategyStatRow":
        from data.domain_services.review_stats_service import StrategyStatRow

        return StrategyStatRow
    if name == "compute_strategy_review_stats":
        from data.domain_services.review_stats_service import compute_strategy_review_stats

        return compute_strategy_review_stats
    if name == "grade_for":
        from data.domain_services.review_stats_service import grade_for

        return grade_for
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TradeCalendarService",
    "MarketDataService",
    "OfflineCalendar",
    "TransactionCostModel",
    "TransactionCostConfig",
    "MetricStat",
    "SampleGrade",
    "StrategyStatRow",
    "compute_strategy_review_stats",
    "grade_for",
]
