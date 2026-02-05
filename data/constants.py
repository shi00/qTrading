
"""
Centralized constants for data processing module.
"""

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
