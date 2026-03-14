"""
Base interfaces and data structures for sync strategies.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List

# Forward declaration for type hinting if needed,
# but usually avoid circular imports by strict typing or Protocol
# from data.tushare_client import TushareClient
# from data.cache_manager import CacheManager

logger = logging.getLogger(__name__)


@dataclass
class SyncContext:
    """
    Dependency Injection Container for Strategies.
    Decouples strategies from the main DataProcessor.
    """

    api: Any  # TushareClient
    cache: Any  # CacheManager
    config: Any = None  # ConfigHandler (Optional)


@dataclass
class SyncResult:
    """
    Standardized result object for synchronization operations.
    """

    added: int = 0
    updated: int = 0
    errors: List[str] = field(default_factory=list)
    status: str = "success"  # success, partial, failed, cancelled
    message: str = ""

    def merge(self, other: "SyncResult"):
        """Merge another result into this one."""
        self.added += other.added
        self.updated += other.updated
        self.errors.extend(other.errors)
        # Status logic: if either is failed, result is failed.
        if other.status == "failed" or self.status == "failed":
            self.status = "failed"
        elif other.status == "cancelled" or self.status == "cancelled":
            self.status = "cancelled"
        elif other.status == "partial" or self.status == "partial":
            self.status = "partial"


class ISyncStrategy(ABC):
    """
    Interface for all synchronization strategies.
    """

    def __init__(self, context: SyncContext):
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self, **kwargs) -> SyncResult:
        """
        Execute the synchronization logic.
        """
        pass

    async def cancel(self):
        """
        Handle cancellation requests.
        Default implementation just logs, overrides should set internal flags.
        """
        self.logger.debug("Cancellation requested.")
