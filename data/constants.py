"""
Centralized constants for data processing module.
"""

# Market Configuration
MARKET_CLOSE_HOUR = 16

# Health Check Thresholds
HEALTH_THRESHOLD_FINANCIAL_COVERAGE = 0.90  # Minimum acceptable (Yellow/Green boundary)
HEALTH_THRESHOLD_FINANCIAL_EXCELLENT = 0.98  # Excellent quality (Green)
HEALTH_THRESHOLD_MARKET_LAG_DAYS = 3
HEALTH_CHECK_DEFAULT_TIMEOUT = 30  # Seconds


# Depth & Breadth Health Check Constants
# Full-score baseline: dynamic based on configured history years
def get_health_depth_full_trade_days() -> int:
    """动态获取满分生命周期的交易日天数基线 (250天/年)"""
    from utils.config_handler import ConfigHandler

    return ConfigHandler.get_init_history_years() * 250


# Breadth: expected_rows already excludes IPO gaps; residual gap is suspension (~0.3%) + API blind spots (~1-3%)
HEALTH_THRESHOLD_BREADTH = 0.90
# UI color warning threshold for depth display (below this, show orange)
HEALTH_DEPTH_WARNING_RATIO = 0.30

# Quality Tier Assignment Thresholds (used by _assign_basic_tier fast-path)
TIER_QUOTE_FRESHNESS_DAYS = 5  # Max lag days for daily_quotes to qualify as SILVER
TIER_FINANCIAL_FRESHNESS_DAYS = 100  # Max lag days for financial_reports to qualify as GOLD
TIER_FUNDAMENTAL_LOW_THRESHOLD = (
    0.3  # Below this, fundamental fields insufficient for fundamental strategies (SILVER cap)
)
TIER_FUNDAMENTAL_HIGH_THRESHOLD = 0.7  # Above this, fundamental fields sufficient for GOLD qualification
TIER_FIN_FRESH_RATIO_GOLD = 0.9  # Financial fresh ratio for GOLD (without fin_lag_days)
TIER_FIN_FRESH_RATIO_NEUTRAL = 0.5  # Neutral financial fresh ratio (fast-path default)
TIER_FIN_FRESH_RATIO_MIN = 0.1  # Minimum financial fresh ratio for SILVER

# Financial Report Schema Columns (Unified for Income, Balance, Cashflow, Indicator)
FINANCIAL_REPORT_SCHEMA_COLS = [
    "ts_code",
    "end_date",
    "ann_date",
    "report_type",
    "total_revenue",
    "revenue",
    "n_income",
    "n_income_attr_p",
    "total_assets",
    "total_liab",
    "total_hldr_eqy_exc_min_int",
    "roe",
    "roe_dt",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "or_yoy",
    "netprofit_yoy",
    "goodwill",
    "audit_result",
    "n_cashflow_act",  # 经营活动产生的现金流量净额
]

# Major Market Indices to Track
MAJOR_INDICES = [
    "000001.SH",  # Shanghai Composite
    "399001.SZ",  # Shenzhen Component
    "399006.SZ",  # ChiNext
    "000300.SH",  # CSI 300
    "000905.SH",  # CSI 500
    "000852.SH",  # CSI 1000
    "000688.SH",  # STAR 50
]

# DataFrame column unit metadata
DATAFRAME_ATTR_COLUMN_UNITS = "column_units"
DATAFRAME_ATTR_COLUMN_UNIT_SOURCES = "column_unit_sources"

SYNC_RESULT_HAS_DATA = "HAS_DATA"
SYNC_RESULT_EMPTY = "EMPTY"
SYNC_RESULT_FETCH_FAILED = "FETCH_FAILED"
SYNC_RESULT_SAVE_FAILED = "SAVE_FAILED"
SYNC_RESULT_SKIPPED_PERMISSION = "SKIPPED_PERMISSION"

CRITICAL_EMPTY_TABLES = {"daily_quotes", "daily_indicators"}

REVIEW_STATUS_PENDING = "PENDING"
REVIEW_STATUS_T1_DONE = "T1_DONE"
REVIEW_STATUS_COMPLETED = "COMPLETED"
TOP_LIST_NET_AMOUNT_UNIT = "yuan"
TOP_LIST_NET_AMOUNT_UNIT_SOURCE = {
    "provider": "tushare.top_list",
    "doc_url": "https://tushare.pro/document/2?doc_id=106",
    "doc_field": "net_amount",
    "doc_description": "龙虎榜净买入额（元）",
}
TOP_LIST_COLUMN_UNITS = {
    "net_amount": TOP_LIST_NET_AMOUNT_UNIT,
}
TOP_LIST_COLUMN_UNIT_SOURCES = {
    "net_amount": TOP_LIST_NET_AMOUNT_UNIT_SOURCE,
}


def attach_column_units(df, column_units: dict[str, str]):
    """Attach non-schema unit metadata to a DataFrame."""
    if df is None:
        return df

    existing = dict(df.attrs.get(DATAFRAME_ATTR_COLUMN_UNITS, {}))
    existing.update(column_units)
    df.attrs[DATAFRAME_ATTR_COLUMN_UNITS] = existing
    return df


def attach_column_unit_sources(df, column_unit_sources: dict[str, dict[str, str]]):
    """Attach upstream evidence for non-schema unit metadata to a DataFrame."""
    if df is None:
        return df

    existing = {
        column_name: dict(source)
        for column_name, source in df.attrs.get(DATAFRAME_ATTR_COLUMN_UNIT_SOURCES, {}).items()
    }
    for column_name, source in column_unit_sources.items():
        existing[column_name] = dict(source)
    df.attrs[DATAFRAME_ATTR_COLUMN_UNIT_SOURCES] = existing
    return df


def attach_top_list_column_units(df):
    """Declare known top_list units and their upstream documentation without changing schema."""
    df = attach_column_units(df, TOP_LIST_COLUMN_UNITS)
    return attach_column_unit_sources(df, TOP_LIST_COLUMN_UNIT_SOURCES)


def get_column_unit(df, column_name: str, default: str | None = None) -> str | None:
    """Read unit metadata from a DataFrame."""
    if df is None:
        return default

    column_units = df.attrs.get(DATAFRAME_ATTR_COLUMN_UNITS, {})
    return column_units.get(column_name, default)


def get_column_unit_source(df, column_name: str, default: dict | None = None) -> dict | None:
    """Read upstream evidence for a column unit from a DataFrame."""
    if df is None:
        return default

    column_unit_sources = df.attrs.get(DATAFRAME_ATTR_COLUMN_UNIT_SOURCES, {})
    source = column_unit_sources.get(column_name)
    return dict(source) if isinstance(source, dict) else default


# Earnings Season Months (Q1, Q2, Q3, Q4 disclosure periods)
# Jan/Apr/Jul/Oct are the start of disclosure windows usually
EARNINGS_SEASON_MONTHS = [1, 4, 7, 10]

# Learning Context Safety Margins (prevents look-ahead bias)
SAFE_LIVE_LEARNING_OFFSET_DAYS = 10
SAFE_BACKTEST_LEARNING_OFFSET_DAYS = 15

# --- Unified Financial Tables Configuration (Single Source of Truth) ---

# Group A: Batch Sync (O(Time))
# Sparse events suitable for daily market-wide fetching via `ann_date`
FINANCIAL_BATCH_TABLES = {
    "fina_forecast": {
        "api": "get_forecast",
        "date_col": "ann_date",
        "key": ["ts_code", "end_date", "ann_date"],
        "desc": "业绩预告",
    },
    "dividend": {
        "api": "get_dividend",
        "date_col": "ann_date",
        "key": ["ts_code", "ann_date"],
        "desc": "分红送转",
    },
    "repurchase": {
        "api": "get_repurchase",
        "date_col": "ann_date",
        "key": ["ts_code", "ann_date"],
        "desc": "股票回购",
    },
}

# Group B: Stock Sync (O(Stock))
# Specific reports suitable for per-stock fetching via `ts_code`
FINANCIAL_STOCK_TABLES = {
    "fina_mainbz": {
        "api": "get_fina_mainbz",
        "date_col": "end_date",
        "key": ["ts_code", "end_date"],
        "desc": "主营业务",
    },
    "fina_audit": {
        "api": "get_fina_audit",
        "date_col": "end_date",
        "key": ["ts_code", "end_date"],
        "desc": "审计意见",
    },
    "pledge_stat": {
        "api": "get_pledge_stat",
        "date_col": "end_date",
        "key": ["ts_code", "end_date"],
        "desc": "股权质押",
    },
}

# Group C: Core Data Tables (Essential for Analysis)
CORE_DATA_TABLES = {
    "financial_reports": {"desc": "财务报表(主表)"},
    "daily_indicators": {"desc": "每日指标(PE/PB)"},
    "moneyflow_daily": {"desc": "日资金流"},
    "margin_daily": {"desc": "融资融券"},
    "suspend_d": {"desc": "停复牌信息"},
}

# Group D: Step 5 AI Alpha Data (New Check)
AI_DATA_TABLES = {
    "stk_holdernumber": {"desc": "股东户数"},
    "top10_holders": {"desc": "前十大股东"},
    "macro_economy": {"desc": "宏观经济", "type": "global"},
    "shibor_daily": {"desc": "Shibor利率", "type": "global"},
    "moneyflow_hsgt": {"desc": "北向资金流", "type": "global"},
}

# Combined Dictionary for Health Check Iteration
HEALTH_CHECK_TABLES = {
    **FINANCIAL_BATCH_TABLES,
    **FINANCIAL_STOCK_TABLES,
    **CORE_DATA_TABLES,
    **AI_DATA_TABLES,
}

# UI Display Order (must cover ALL keys in HEALTH_CHECK_TABLES)
HEALTH_REPORT_ORDER = [
    # Core
    "daily_quotes",
    "financial_reports",
    "fina_forecast",
    "daily_indicators",
    # Global (AI)
    "macro_economy",
    "shibor_daily",
    "moneyflow_hsgt",
    # Stock (AI)
    "stk_holdernumber",
    "top10_holders",
    # Market
    "moneyflow_daily",
    "margin_daily",
    "pledge_stat",
    "suspend_d",
    "dividend",
    "repurchase",
    # Financial Stock
    "fina_mainbz",
    "fina_audit",
]
