"""PreFetchedContext 数据容器（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class PreFetchedContext:
    """
    Container for pre-fetched data shared across all stock analyses in a batch.

    This dataclass encapsulates all pre-fetched data to avoid parameter bloat
    in method signatures and enable clean extension for future enhancements.
    """

    capital: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    concepts_map: dict = field(default_factory=dict)
    news_tasks: dict = field(default_factory=dict)
    history_context: str = ""
    global_context: str = ""
    trade_date: str | None = None

    indicators: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_stats: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)
    market_context_str: str = ""
    macro_context: str = ""
    auxiliary_data: dict = field(default_factory=dict)
    news_as_of: date | None = None
    is_backtest: bool = False


ContextBuilder = Callable[[dict, PreFetchedContext], tuple[str, bool]]
