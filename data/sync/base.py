import typing

"""
Base interfaces and data structures for sync strategies.
"""

import datetime
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Forward declaration for type hinting if needed,
# but usually avoid circular imports by strict typing or Protocol
# from data.external.tushare_client import TushareClient
# from data.cache.cache_manager import CacheManager

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
    _processor_ref: Any = None  # weakref.ref(DataProcessor)

    @property
    def processor(self):
        if self._processor_ref is not None:
            return self._processor_ref()
        return None

    @processor.setter
    def processor(self, value):
        if value is not None:
            import weakref

            self._processor_ref = weakref.ref(value)
        else:
            self._processor_ref = None


@dataclass
class SyncResult:
    """
    Standardized result object for synchronization operations.
    """

    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "success"  # success, partial, failed, cancelled
    message: str = ""
    quality_scores: dict[datetime.date, float] = field(default_factory=dict)
    expected_bases: dict[datetime.date, int] = field(default_factory=dict)
    table_stats: dict[str, dict] = field(default_factory=dict)

    def merge(self, other: SyncResult):
        """Merge another result into this one."""
        self.added += other.added
        self.updated += other.updated
        self.skipped += other.skipped
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

        if other.message:
            if self.message:
                self.message = self.message + " | " + other.message
            else:
                self.message = other.message
            if len(self.message) > 2000:
                self.message = self.message[:1997] + "..."

        def normalize_date_key(key):
            if isinstance(key, datetime.date):
                return key
            if isinstance(key, str):
                try:
                    return datetime.datetime.strptime(key, "%Y%m%d").date()
                except ValueError:
                    return key
            return key

        self.quality_scores = {normalize_date_key(k): v for k, v in self.quality_scores.items()}
        self.expected_bases = {normalize_date_key(k): v for k, v in self.expected_bases.items()}

        for key, value in other.quality_scores.items():
            normalized_key = normalize_date_key(key)
            self.quality_scores[normalized_key] = value

        for key, value in other.expected_bases.items():
            normalized_key = normalize_date_key(key)
            self.expected_bases[normalized_key] = value

        for table, stats in other.table_stats.items():
            if table in self.table_stats:
                existing = self.table_stats[table].get("count", 0)
                self.table_stats[table]["count"] = existing + stats.get("count", 0)
            else:
                self.table_stats[table] = stats.copy()

        if other.status == "cancelled" or self.status == "cancelled":
            self.status = "cancelled"
        elif other.status == "failed" and self.status == "failed":
            self.status = "failed"
        elif (
            other.status == "failed" or self.status == "failed" or other.status == "partial" or self.status == "partial"
        ):
            self.status = "partial"
        else:
            self.status = "success"

    def to_summary(self) -> str:
        """Generate a human-readable summary of the sync result."""
        parts = [f"status={self.status}"]
        if self.added > 0:
            parts.append(f"added={self.added}")
        if self.updated > 0:
            parts.append(f"updated={self.updated}")
        if self.skipped > 0:
            parts.append(f"skipped={self.skipped}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        if self.message:
            parts.append(f"message={self.message}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """Generate a structured dictionary representation of the sync result."""
        return {
            "status": self.status,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors.copy(),
            "warnings": self.warnings.copy(),
            "message": self.message,
            "quality_scores": self.quality_scores.copy(),
            "expected_bases": self.expected_bases.copy(),
            "table_stats": self.table_stats.copy(),
        }


class ISyncStrategy(ABC):
    """
    Interface for all synchronization strategies.
    """

    def __init__(self, context: SyncContext):
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)
        self._cancelled = False

    def cancel(self):
        """Signal the strategy to stop gracefully."""
        self._cancelled = True

    def _check_cancelled(self, result: SyncResult) -> bool:
        """Check if cancelled and update result status. Returns True if cancelled."""
        if self._cancelled:
            result.status = "cancelled"
            return True
        return False

    @staticmethod
    def _clean_null_values(df: typing.Any) -> typing.Any:
        """
        Clean NULL values in DataFrame.

        Converts empty strings, "None", "nan" to real NULL (np.nan).
        This ensures database queries work correctly.

        Args:
            df: pandas DataFrame to clean

        Returns:
            Cleaned DataFrame
        """
        import numpy as np

        if df is None or not hasattr(df, "replace"):
            return df

        df = df.replace("", np.nan)
        df = df.replace("None", np.nan)
        df = df.replace("nan", np.nan)

        return df

    @abstractmethod
    async def run(self, **kwargs: typing.Any) -> SyncResult:
        """
        Execute the synchronization logic.
        """
        pass
