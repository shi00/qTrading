-- Database Schema for A-Stock Screener
-- Usage: Execute using executescript() in SQLite

-- Enable Write-Ahead Logging for concurrency
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 1. Basic Stock Info
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    area TEXT,
    industry TEXT,
    market TEXT,
    list_date TEXT,
    list_status TEXT,
    updated_at TEXT
);

-- 2. Daily Quotes
CREATE TABLE IF NOT EXISTS daily_quotes (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    pre_close REAL,
    change REAL,
    pct_chg REAL,
    vol REAL,
    amount REAL,
    adj_factor REAL,
    -- Pre-Adjusted Prices (QFQ) for easier Backtesting
    qfq_open REAL,
    qfq_high REAL,
    qfq_low REAL,
    qfq_close REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 3. Daily Indicators
CREATE TABLE IF NOT EXISTS daily_indicators (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    pe REAL,
    pe_ttm REAL,
    pb REAL,
    ps REAL,
    ps_ttm REAL,
    dv_ratio REAL,
    dv_ttm REAL,
    total_mv REAL,
    circ_mv REAL,
    total_share REAL,
    float_share REAL,
    free_share REAL,
    turnover_rate REAL,
    turnover_rate_f REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 4. Money Flow
CREATE TABLE IF NOT EXISTS moneyflow_daily (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    buy_sm_vol INTEGER,
    buy_sm_amount REAL,
    sell_sm_vol INTEGER,
    sell_sm_amount REAL,
    buy_md_vol INTEGER,
    buy_md_amount REAL,
    sell_md_vol INTEGER,
    sell_md_amount REAL,
    buy_lg_vol INTEGER,
    buy_lg_amount REAL,
    sell_lg_vol INTEGER,
    sell_lg_amount REAL,
    buy_elg_vol INTEGER,
    buy_elg_amount REAL,
    sell_elg_vol INTEGER,
    sell_elg_amount REAL,
    net_mf_vol INTEGER,
    net_mf_amount REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 5. Northbound Holding
CREATE TABLE IF NOT EXISTS northbound_holding (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    name TEXT,
    vol INTEGER,
    ratio REAL,
    exchange TEXT,
    PRIMARY KEY (ts_code, trade_date)
);

-- 6. Dragon Tiger Board
CREATE TABLE IF NOT EXISTS top_list (
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT,
    close REAL,
    pct_chg REAL,
    turnover_rate REAL,
    amount REAL,
    l_sell REAL,
    l_buy REAL,
    l_amount REAL,
    net_amount REAL,
    net_rate REAL,
    amount_rate REAL,
    float_values REAL,
    reason TEXT,
    PRIMARY KEY (trade_date, ts_code)
);

-- 7. Sync Status
CREATE TABLE IF NOT EXISTS sync_status (
    table_name TEXT PRIMARY KEY,
    last_sync_date TEXT,
    last_data_date TEXT,
    record_count INTEGER,
    status TEXT,
    updated_at TEXT
);

-- 8. Screening History
CREATE TABLE IF NOT EXISTS screening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT,
    close REAL,
    pct_chg REAL,
    t1_price REAL,
    t5_price REAL,
    ai_score INTEGER,
    ai_reason TEXT,
    prediction_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, strategy_name, ts_code)
);

-- 9. Block Trades
CREATE TABLE IF NOT EXISTS block_trade (
    ts_code TEXT,
    trade_date TEXT,
    price REAL,
    volume REAL,
    amount REAL,
    buyer TEXT,
    seller TEXT,
    reason TEXT,
    updated_at TEXT,
    PRIMARY KEY (ts_code, trade_date, buyer, seller)
);

-- 10. Market News
CREATE TABLE IF NOT EXISTS market_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,
    tags TEXT,
    publish_time TEXT,
    source TEXT,
    created_at TEXT,
    UNIQUE(content, publish_time)
);

-- 11. Trade Calendar
CREATE TABLE IF NOT EXISTS trade_cal (
    cal_date TEXT PRIMARY KEY,
    exchange TEXT,
    is_open INTEGER,
    pretrade_date TEXT
);

-- 12. Financial Reports
CREATE TABLE IF NOT EXISTS financial_reports (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    ann_date TEXT,
    report_type TEXT,
    total_revenue REAL,
    revenue REAL,
    n_income REAL,
    n_income_attr_p REAL,
    total_assets REAL,
    total_liab REAL,
    total_hldr_eqy_exc_min_int REAL,
    roe REAL,
    roe_dt REAL,
    grossprofit_margin REAL,
    netprofit_margin REAL,
    debt_to_assets REAL,
    or_yoy REAL,
    netprofit_yoy REAL,
    goodwill REAL, 
    audit_result TEXT,
    PRIMARY KEY (ts_code, end_date)
);

-- 13. Index Daily
CREATE TABLE IF NOT EXISTS index_daily (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL,
    open REAL,
    high REAL,
    low REAL,
    pre_close REAL,
    change REAL,
    pct_chg REAL,
    vol REAL,
    amount REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 13b. Index Indicators
CREATE TABLE IF NOT EXISTS index_dailybasic (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    total_mv REAL,
    float_mv REAL,
    total_share REAL,
    float_share REAL,
    free_share REAL,
    turnover_rate REAL,
    turnover_rate_f REAL,
    pe REAL,
    pe_ttm REAL,
    pb REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 14. Margin Data
CREATE TABLE IF NOT EXISTS margin_daily (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    rzye REAL, 
    rqye REAL, 
    rzmre REAL, 
    rqyl REAL, 
    rzrqye REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 15. Suspension Data
CREATE TABLE IF NOT EXISTS suspend_d (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    suspend_timing TEXT,
    suspend_type_name TEXT,
    PRIMARY KEY (ts_code, trade_date)
);

-- 16. Limit List
CREATE TABLE IF NOT EXISTS limit_list (
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT,
    close REAL,
    pct_chg REAL,
    amp REAL,
    fc_ratio REAL, 
    fl_ratio REAL, 
    fd_amount REAL, 
    first_time TEXT, 
    last_time TEXT, 
    open_times INTEGER, 
    strth REAL, 
    limit_type TEXT, 
    PRIMARY KEY (trade_date, ts_code)
);

-- 17. Performance Forecast
CREATE TABLE IF NOT EXISTS fina_forecast (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    ann_date TEXT,
    type TEXT,
    p_change_min REAL, 
    p_change_max REAL, 
    net_profit_min REAL, 
    net_profit_max REAL, 
    PRIMARY KEY (ts_code, end_date, ann_date)
);

-- 18. Main Business Composition
CREATE TABLE IF NOT EXISTS fina_mainbz (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    bz_item TEXT, 
    bz_sales REAL, 
    bz_profit REAL, 
    bz_cost REAL, 
    curr_type TEXT, 
    update_flag TEXT,
    PRIMARY KEY (ts_code, end_date, bz_item)
);

-- 19. Stock Pledge Statistics
CREATE TABLE IF NOT EXISTS pledge_stat (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    pledge_count INTEGER,
    unrest_pledge REAL,
    rest_pledge REAL,
    total_share REAL,
    pledge_ratio REAL,
    PRIMARY KEY (ts_code, end_date)
);

-- 20. Stock Repurchase
CREATE TABLE IF NOT EXISTS repurchase (
    ts_code TEXT NOT NULL,
    ann_date TEXT NOT NULL,
    end_date TEXT,
    proc TEXT,
    exp_date TEXT,
    vol REAL,
    amount REAL,
    high_limit REAL,
    low_limit REAL,
    PRIMARY KEY (ts_code, ann_date) 
);

-- 21. Dividend History
CREATE TABLE IF NOT EXISTS dividend (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    ann_date TEXT NOT NULL,
    div_proc TEXT, 
    stk_div REAL, 
    stk_bo_rate REAL, 
    stk_co_rate REAL, 
    cash_div_tax REAL, 
    cash_div_tax_rate REAL, 
    record_date TEXT, 
    ex_date TEXT, 
    PRIMARY KEY (ts_code, ann_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_quotes_date ON daily_quotes(trade_date);
CREATE INDEX IF NOT EXISTS idx_quotes_code ON daily_quotes(ts_code);
CREATE INDEX IF NOT EXISTS idx_indicators_date ON daily_indicators(trade_date);
CREATE INDEX IF NOT EXISTS idx_fina_enddate ON financial_reports(end_date);
CREATE INDEX IF NOT EXISTS idx_fina_code_date ON financial_reports(ts_code, end_date);
CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_north_date ON northbound_holding(trade_date);
CREATE INDEX IF NOT EXISTS idx_history_date ON screening_history(trade_date);
CREATE INDEX IF NOT EXISTS idx_cal_date ON trade_cal(cal_date);
CREATE INDEX IF NOT EXISTS idx_stock_list_date ON stock_basic(list_date);
CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_margin_date ON margin_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_suspend_date ON suspend_d(trade_date);
CREATE INDEX IF NOT EXISTS idx_limit_date ON limit_list(trade_date);
CREATE INDEX IF NOT EXISTS idx_forecast_code ON fina_forecast(ts_code);
CREATE INDEX IF NOT EXISTS idx_mainbz_code ON fina_mainbz(ts_code);
CREATE INDEX IF NOT EXISTS idx_pledge_code ON pledge_stat(ts_code);
CREATE INDEX IF NOT EXISTS idx_repurchase_code ON repurchase(ts_code);
CREATE INDEX IF NOT EXISTS idx_dividend_code ON dividend(ts_code);
