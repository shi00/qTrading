"""
Domain services module.

Provides unified service interfaces for data access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.domain_services.market_data_service import MarketDataService
    from data.domain_services.offline_calendar import OfflineCalendar
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TradeCalendarService",
    "MarketDataService",
    "OfflineCalendar",
    "TransactionCostModel",
    "TransactionCostConfig",
]
