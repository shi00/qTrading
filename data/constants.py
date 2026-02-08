
"""
Centralized constants for data processing module.
"""

# Market Configuration
MARKET_CLOSE_HOUR = 16

# Financial Report Schema Columns (Unified for Income, Balance, Cashflow, Indicator)
FINANCIAL_REPORT_SCHEMA_COLS = [
    'ts_code', 'end_date', 'ann_date', 'report_type', 
    'total_revenue', 'revenue', 'n_income', 'n_income_attr_p', 
    'total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int', 
    'roe', 'roe_dt', 'grossprofit_margin', 'netprofit_margin', 
    'debt_to_assets', 'or_yoy', 'netprofit_yoy'
]

# Major Market Indices to Track
MAJOR_INDICES = [
    '000001.SH',  # Shanghai Composite
    '399001.SZ',  # Shenzhen Component
    '399006.SZ',  # ChiNext
    '000300.SH',  # CSI 300
    '000905.SH',  # CSI 500
    '000852.SH',  # CSI 1000
    '000688.SH'   # STAR 50
]

# Earnings Season Months (Q1, Q2, Q3, Q4 disclosure periods)
# Jan/Apr/Jul/Oct are the start of disclosure windows usually
EARNINGS_SEASON_MONTHS = [1, 4, 7, 10]

# --- Unified Financial Tables Configuration (Single Source of Truth) ---

# Group A: Batch Sync (O(Time))
# Sparse events suitable for daily market-wide fetching via `ann_date`
FINANCIAL_BATCH_TABLES = {
    'fina_forecast': {
        'api': 'get_forecast', 
        'date_col': 'ann_date', 
        'key': ['ts_code', 'end_date', 'ann_date'],
        'desc': '业绩预告'
    },
    'dividend': {
        'api': 'get_dividend', 
        'date_col': 'ann_date', 
        'key': ['ts_code', 'ann_date'], 
        'desc': '分红送转'
    },
    'repurchase': {
        'api': 'get_repurchase', 
        'date_col': 'ann_date', 
        'key': ['ts_code', 'ann_date'], 
        'desc': '股票回购'
    }
}

# Group B: Stock Sync (O(Stock))
# Specific reports suitable for per-stock fetching via `ts_code`
FINANCIAL_STOCK_TABLES = {
    'fina_mainbz': {
        'api': 'get_fina_mainbz', 
        'date_col': 'end_date', 
        'key': ['ts_code', 'end_date'], 
        'desc': '主营业务'
    },
    'fina_audit': {
        'api': 'get_fina_audit', 
        'date_col': 'end_date', 
        'key': ['ts_code', 'end_date'], 
        'desc': '审计意见'
    },
    'pledge_stat': {
        'api': 'get_pledge_stat', 
        'date_col': 'end_date', 
        'key': ['ts_code', 'end_date'], 
        # Note: Pledge stat usually by end_date snapshot
        'desc': '股权质押'
    }
}

# Combined Dictionary for Health Check Iteration
HEALTH_CHECK_TABLES = {**FINANCIAL_BATCH_TABLES, **FINANCIAL_STOCK_TABLES}
